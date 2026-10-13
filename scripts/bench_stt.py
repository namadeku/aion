"""Voice-input benchmark: WAV phrases -> real VAD -> segmenter -> STT -> wake name -> router.

The phrase set lives in ``tests/data/voice/phrases.yaml``; audio is synthesized with Piper on the
first run into the gitignored ``tests/data/voice/wav/`` (deterministic, ~5 MB). Nothing touches
the microphone or the speakers: WAVs are fed to the real :class:`VoicePipeline` through a fake mic.

Usage (from the repo root)::

    uv run python scripts/bench_stt.py --device cpu --label baseline-cpu
    uv run python scripts/bench_stt.py --device cuda --pace 1 --label after-cuda

Reported: WER/CER of the command text, wake precision/recall, false triggers, router accuracy
and latency "end of phrase -> text ready" (``proc`` = after the utterance was cut, ``e2e`` = from
the last spoken sample; ``e2e`` is only meaningful with ``--pace 1``, i.e. real-time feeding).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import statistics
import sys
import tempfile
import time
import wave
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import soxr
import yaml
from loguru import logger
from rapidfuzz.distance import Levenshtein

from aion.app import Aion
from aion.audio.pipeline import VoicePipeline
from aion.audio.vad import FRAME, SAMPLE_RATE
from aion.config import Config, ConfigStore
from aion.config.schema import PathsConfig, default_data_dir
from aion.core import NullOutput
from aion.core.events import TtsFinished, TtsStarted, WakeDetected
from aion.stt import create_stt
from aion.text import normalize
from aion.wakeword.base import strip_name

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data" / "voice"
WAV_DIR = DATA / "wav"
RESULTS_DIR = DATA / "results"
NOISE_FLOOR_DB = -62.0  # a quiet room: constant for every clip
LEAD_S = 0.5
TAIL_S = 1.6

Samples = npt.NDArray[np.float32]


# -- phrase set ---------------------------------------------------------------------------


@dataclass
class Clip:
    id: str
    kind: str  # command | name_only | chatter | distractor | echo_only | silence
    variant: str
    path: Path
    say: str = ""
    ref: str = ""
    command: str | None = None
    speaking: bool = False  # the assistant is "talking" while this clip plays
    echo_text: str = ""

    @property
    def wake_expected(self) -> bool:
        return self.kind in ("command", "name_only")


def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(key.encode()))


def plan_clips(spec: dict[str, Any]) -> list[Clip]:
    clips: list[Clip] = []
    echo_texts: list[str] = spec["echo_texts"]
    defaults: dict[str, list[str]] = spec["defaults"]
    n_echo = 0
    for case in spec["cases"]:
        for variant in case.get("variants", defaults[case["kind"]]):
            echo_text = ""
            if variant == "echo":
                echo_text = echo_texts[n_echo % len(echo_texts)]
                n_echo += 1
            clips.append(
                Clip(
                    id=f"{case['id']}__{variant}",
                    kind=case["kind"],
                    variant=variant,
                    path=WAV_DIR / f"{case['id']}__{variant}.wav",
                    say=case["say"],
                    ref=case.get("ref", ""),
                    command=case.get("command"),
                    speaking=variant == "echo",
                    echo_text=echo_text,
                )
            )
    for case in spec["echo_only"]:
        clips.append(
            Clip(
                id=case["id"],
                kind="echo_only",
                variant="echo_only",
                path=WAV_DIR / f"{case['id']}.wav",
                say=case["say"],
                speaking=True,
                echo_text=case["say"],
            )
        )
    for case in spec["silence_clips"]:
        clips.append(
            Clip(
                id=case["id"],
                kind="silence",
                variant="silence",
                path=WAV_DIR / f"{case['id']}.wav",
            )
        )
    return clips


# -- synthesis ----------------------------------------------------------------------------


def _db(value: float) -> float:
    return 10 ** (value / 20)


def _rms(x: Samples) -> float:
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _pink(n: int, rng: np.random.Generator) -> Samples:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    spectrum /= np.sqrt(np.maximum(np.arange(len(spectrum)), 1))
    noise = np.fft.irfft(spectrum, n).astype(np.float32)
    return noise / max(_rms(noise), 1e-9)


def _room(x: Samples, rng: np.random.Generator) -> Samples:
    """Speaker -> room -> mic: a short decaying impulse response and a duller sound."""
    n = int(0.06 * SAMPLE_RATE)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-np.linspace(0, 7, n)).astype(
        np.float32
    )
    ir[0] = 1.0
    y = np.convolve(x, ir)[: len(x)].astype(np.float32)
    return np.convolve(y, np.ones(3, dtype=np.float32) / 3)[: len(x)].astype(np.float32)


async def _tts(engine: Any, text: str, voice: str, rate: float = 1.0) -> Samples:
    audio = await engine.synthesize(text, voice=voice, rate=rate)
    samples = audio.samples
    if audio.sample_rate != SAMPLE_RATE:
        samples = soxr.resample(samples, audio.sample_rate, SAMPLE_RATE)
    return np.asarray(samples, dtype=np.float32)


def _mix(parts: list[tuple[Samples, int]], total: int) -> Samples:
    out = np.zeros(total, dtype=np.float32)
    for samples, offset in parts:
        end = min(total, offset + len(samples))
        out[offset:end] += samples[: end - offset]
    return out


def write_wav(path: Path, samples: Samples) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm.tobytes())


def read_wav(path: Path) -> npt.NDArray[np.int16]:
    with wave.open(str(path), "rb") as f:
        assert f.getframerate() == SAMPLE_RATE
        assert f.getnchannels() == 1
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).copy()


async def synthesize_all(spec: dict[str, Any], clips: list[Clip], models_dir: Path) -> None:
    missing = [c for c in clips if not c.path.exists()]
    if not missing:
        return
    from aion.tts.piper_engine import PiperEngine

    engine = PiperEngine(models_dir)
    voices: list[str] = spec["voices"]
    cases = {c["id"]: c for c in [*spec["cases"], *spec["echo_only"]]}
    variants: dict[str, dict[str, Any]] = spec["variants"]
    other_voice = {voices[0]: voices[1], voices[1]: voices[0]}
    print(f"Synthesizing {len(missing)} clips with Piper into {WAV_DIR} ...")
    for index, clip in enumerate(missing):
        rng = _rng(clip.id)
        case_id = clip.id.split("__")[0]
        case = cases.get(case_id, {})
        voice = case.get("voice") or voices[zlib.crc32(case_id.encode()) % len(voices)]
        if clip.kind == "silence":
            samples = np.zeros(int(3.0 * SAMPLE_RATE), dtype=np.float32)
            for sc in spec["silence_clips"]:
                if sc["id"] == clip.id and sc["kind"] != "floor":
                    noise = _pink(len(samples), rng) if sc["kind"] == "pink" else None
                    if noise is None:
                        noise = rng.standard_normal(len(samples)).astype(np.float32)
                    samples = noise * _db(sc["level_db"])
        elif clip.kind == "echo_only":
            echo = await _tts(engine, clip.say, voice)
            echo = _room(echo / max(float(np.max(np.abs(echo))), 1e-9) * 0.6, rng)
            samples = echo * _db(case.get("gain_db", -6))
        else:
            params = variants[clip.variant]
            speech = await _tts(engine, clip.say, voice, params.get("rate", 1.0))
            peak = params.get("peak", 0.6)
            speech = speech / max(float(np.max(np.abs(speech))), 1e-9) * peak
            total = len(speech)
            parts = [(speech, 0)]
            if clip.variant == "echo":
                echo = await _tts(engine, clip.echo_text, other_voice[voice])
                echo = _room(echo / max(float(np.max(np.abs(echo))), 1e-9) * 0.6, rng)
                total = max(total, len(echo))
                parts.append((echo * _db(params["echo_db"]) * peak / 0.6, 0))
            samples = _mix(parts, total)
            if "snr_db" in params:
                samples = samples + _pink(total, rng) * _rms(speech) * _db(-params["snr_db"])
        samples = np.concatenate([np.zeros(0, dtype=np.float32), samples]).astype(np.float32)
        floor = rng.standard_normal(len(samples)).astype(np.float32) * _db(NOISE_FLOOR_DB)
        write_wav(clip.path, (samples + floor).astype(np.float32))
        if index % 20 == 19:
            print(f"  {index + 1}/{len(missing)}")


# -- metrics ------------------------------------------------------------------------------


def words(text: str) -> list[str]:
    return normalize(text).split()


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """Word edit distance and reference length."""
    r, h = words(ref), words(hyp)
    return Levenshtein.distance(r, h), len(r)


def cer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = " ".join(words(ref)), " ".join(words(hyp))
    return Levenshtein.distance(r, h), len(r)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


@dataclass
class Result:
    id: str
    kind: str
    variant: str
    duration_s: float
    ref: str
    stt_text: str = ""  # direct STT over the whole clip, name stripped
    stt_ms: float = 0.0
    woke: bool = False
    submitted: str | None = None
    matched: str | None = None
    proc_ms: float | None = None
    e2e_ms: float | None = None
    utterances: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# -- running ------------------------------------------------------------------------------


class FakeMic:
    def __init__(self) -> None:
        self.frames: queue.Queue[npt.NDArray[np.int16]] = queue.Queue()

    def start(self) -> None: ...
    def stop(self) -> None: ...


class BenchPipeline(VoicePipeline):
    """The real pipeline with timing probes around the utterance handler."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.in_flight = 0
        self.utterances = 0
        self.t_utterance: float | None = None

    async def _on_utterance(self, segment: int, audio: Any, *rest: Any) -> None:  # type: ignore[override]
        self.in_flight += 1
        if audio.size:
            self.utterances += 1
            self.t_utterance = time.perf_counter()
        try:
            await super()._on_utterance(segment, audio, *rest)  # type: ignore[arg-type]
        finally:
            self.in_flight -= 1


class BenchPaths(PathsConfig):
    models_override: ClassVar[Path | None] = None

    @property
    def models(self) -> Path:
        return self.models_override or super().models


async def run_clips(  # noqa: PLR0915
    args: argparse.Namespace, clips: list[Clip]
) -> tuple[list[Result], dict[str, Any]]:
    models_dir = Path(args.models_dir) if args.models_dir else default_data_dir() / "models"
    BenchPaths.models_override = models_dir
    tmp = Path(tempfile.mkdtemp(prefix="aion-bench-"))
    config = Config(paths=BenchPaths(data_dir=tmp))
    config.stt.device = args.device
    config.stt.whisper_model = args.model
    config.stt.backend = args.backend
    config.assistant.follow_up_seconds = 0
    for dotted in args.set or []:
        _apply_override(config, dotted)
    store = ConfigStore(config, tmp / "config.yaml")
    app = Aion(store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    await app.start()

    captured: list[tuple[float, str]] = []

    async def capture(turn: Any) -> bool:
        captured.append((time.perf_counter(), turn.text))
        return True

    app.dialog.interceptors.insert(0, capture)
    wakes: list[float] = []

    async def on_wake(_event: WakeDetected) -> None:
        wakes.append(time.perf_counter())

    app.bus.subscribe(WakeDetected, on_wake)

    from aion.voice import create_vad, create_wake

    vad = await create_vad(config)
    wake = await create_wake(config)
    stt = create_stt(config)
    mic = FakeMic()
    pipeline = BenchPipeline(app, mic, vad, stt, wake)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    await pipeline.start()  # loads (and warms up) the STT
    load_s = time.perf_counter() - t0
    device = getattr(stt, "active_device", "n/a")
    model = getattr(stt, "model_name", args.model)
    print(f"STT {stt.name} model={model} device={device}: loaded in {load_s:.1f} s")

    # warm-up outside the measurements: the first inference compiles kernels
    warm = read_wav(clips[0].path)
    await stt.transcribe(warm)

    results: list[Result] = []
    for index, clip in enumerate(clips):
        audio = read_wav(clip.path)
        result = Result(clip.id, clip.kind, clip.variant, len(audio) / SAMPLE_RATE, clip.ref)
        # 1) direct STT over the whole clip (WER independent of VAD/wake)
        t = time.perf_counter()
        raw = await pipeline.recognize(audio)
        result.stt_ms = (time.perf_counter() - t) * 1000
        result.stt_text = strip_name(raw, app.config.profile.wake_names()) if raw else ""
        result.raw["stt_raw"] = raw
        # 2) the real pipeline
        if not args.stt_only and clip.kind != "silence":
            await _run_pipeline(
                app, pipeline, mic, vad, clip, audio, result, args.pace, captured, wakes
            )
        if clip.kind == "silence":
            result.woke = False
        if result.submitted is not None:
            m = app.router.match(result.submitted)
            result.matched = f"{m.command.plugin}.{m.command.name}" if m else None
        results.append(result)
        print(
            f"[{index + 1}/{len(clips)}] {clip.id:28} woke={int(result.woke)} "
            f"stt={result.stt_text!r} sub={result.submitted!r}",
            flush=True,
        )
    meta = {
        "stt": stt.name,
        "model": model,
        "device": device,
        "load_s": round(load_s, 2),
        "pace": args.pace,
        "config": {
            "min_silence_ms": config.audio.vad.min_silence_ms,
            "vad_threshold": config.audio.vad.threshold,
            "wake_sensitivity": config.wakeword.sensitivity,
            "wake_backend": config.wakeword.backend,
        },
    }
    await pipeline.stop()
    await app.stop()
    return results, meta


def _apply_override(config: Config, dotted: str) -> None:
    """``section.field=value``; fields the schema does not know yet are injected for experiments
    (the voice code reads optional tuning with ``getattr(section, name, default)``)."""
    key, _, raw = dotted.partition("=")
    *path, leaf = key.split(".")
    target: Any = config
    for part in path:
        target = getattr(target, part)
    current = getattr(target, leaf, None)
    value: Any = raw
    if isinstance(current, bool) or raw.lower() in ("true", "false"):
        value = raw.lower() in ("1", "true", "yes")
    elif isinstance(current, int) or (current is None and raw.lstrip("-").isdigit()):
        value = int(raw)
    elif isinstance(current, float) or (current is None and raw.replace(".", "", 1).isdigit()):
        value = float(raw)
    if hasattr(type(target), "model_fields") and leaf not in type(target).model_fields:
        target.__dict__[leaf] = value
    else:
        setattr(target, leaf, value)


async def _run_pipeline(  # noqa: PLR0912
    app: Aion,
    pipeline: BenchPipeline,
    mic: FakeMic,
    vad: Any,
    clip: Clip,
    audio: npt.NDArray[np.int16],
    result: Result,
    pace: float,
    captured: list[tuple[float, str]],
    wakes: list[float],
) -> None:
    rng = _rng("floor" + clip.id)
    floor = lambda s: (  # noqa: E731
        rng.standard_normal(int(s * SAMPLE_RATE)) * _db(NOISE_FLOOR_DB) * 32768
    ).astype(np.int16)
    stream = np.concatenate([floor(LEAD_S), audio, floor(TAIL_S)])
    n_frames = len(stream) // FRAME
    vad.reset()
    pipeline.segmenter.reset()
    pipeline._deactivate()  # pyright: ignore[reportPrivateUsage]
    captured.clear()
    wakes.clear()
    pipeline.utterances = 0
    pipeline.t_utterance = None
    if clip.speaking:
        app.state.raise_flag("speaking")
        await app.bus.publish(TtsStarted(text=clip.echo_text))
    start = time.perf_counter()
    for i in range(n_frames):
        mic.frames.put(stream[i * FRAME : (i + 1) * FRAME])
        if pace > 0:
            delay = start + (i + 1) * FRAME / SAMPLE_RATE / pace - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0)
        elif i % 8 == 0:
            await asyncio.sleep(0)
    speech_end = start + (LEAD_S + len(audio) / SAMPLE_RATE) / max(pace, 1e-9)
    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        await asyncio.sleep(0.05)
        if mic.frames.empty() and pipeline.in_flight == 0 and not pipeline._prefix_checks:  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0.15)  # let a just-posted handler start
            if pipeline.in_flight == 0:
                break
    if clip.speaking:
        await app.bus.publish(TtsFinished(text=clip.echo_text))
        app.state.lower_flag("speaking")
    await app.bus.drain()
    result.utterances = pipeline.utterances
    result.woke = bool(wakes)
    if captured:
        t_ready, text = captured[-1]
        result.submitted = text
        if pipeline.t_utterance is not None:
            result.proc_ms = (t_ready - pipeline.t_utterance) * 1000
        if pace > 0:
            result.e2e_ms = (t_ready - speech_end) * 1000


# -- reporting ----------------------------------------------------------------------------


def summarize(
    results: list[Result], clips: dict[str, Clip], *, stt_only: bool = False
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    speech = [r for r in results if r.kind in ("command", "chatter", "distractor", "name_only")]

    def wer_over(rows: list[tuple[str, str]]) -> dict[str, float]:
        errs = total = cerr = ctotal = 0
        for ref, hyp in rows:
            e, n = wer(ref, hyp)
            errs, total = errs + e, total + n
            e, n = cer(ref, hyp)
            cerr, ctotal = cerr + e, ctotal + n
        return {
            "wer": round(100 * errs / max(total, 1), 1),
            "cer": round(100 * cerr / max(ctotal, 1), 1),
            "n": len(rows),
        }

    out["stt_wer"] = wer_over([(r.ref, r.stt_text) for r in speech if r.kind != "name_only"])
    by_variant: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in speech:
        if r.kind in ("command", "chatter", "distractor"):
            by_variant[r.variant].append((r.ref, r.stt_text))
    out["stt_wer_by_variant"] = {v: wer_over(rows) for v, rows in sorted(by_variant.items())}
    out["stt_ms_p50"] = round(statistics.median([r.stt_ms for r in results]), 0)

    sil = [r for r in results if r.kind == "silence"]
    out["silence_hallucinations"] = {
        "n": len(sil),
        "nonempty": sum(1 for r in sil if r.raw["stt_raw"].strip()),
        "texts": [r.raw["stt_raw"] for r in sil if r.raw["stt_raw"].strip()],
    }
    out["stt_wer_no_echo"] = wer_over(
        [(r.ref, r.stt_text) for r in speech if r.kind != "name_only" and r.variant != "echo"]
    )
    if stt_only:
        return out

    pipe = [r for r in results if r.kind != "silence"]
    tp = sum(1 for r in pipe if clips[r.id].wake_expected and r.woke)
    fn = sum(1 for r in pipe if clips[r.id].wake_expected and not r.woke)
    fp = sum(1 for r in pipe if not clips[r.id].wake_expected and r.woke)
    out["wake"] = {
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "precision": round(tp / max(tp + fp, 1), 3),
        "recall": round(tp / max(tp + fn, 1), 3),
        "false_triggers": [r.id for r in pipe if not clips[r.id].wake_expected and r.woke],
        "missed": [r.id for r in pipe if clips[r.id].wake_expected and not r.woke],
    }
    commands = [r for r in pipe if r.kind == "command"]
    out["pipeline_wer"] = wer_over([(r.ref, r.submitted or "") for r in commands])
    ok = sum(1 for r in commands if r.matched == clips[r.id].command)
    out["router_accuracy"] = round(100 * ok / max(len(commands), 1), 1)
    out["router_by_variant"] = {
        v: f"{sum(1 for r in commands if r.variant == v and r.matched == clips[r.id].command)}"
        f"/{sum(1 for r in commands if r.variant == v)}"
        for v in sorted({r.variant for r in commands})
    }
    proc = [r.proc_ms for r in commands if r.proc_ms is not None]
    e2e = [r.e2e_ms for r in commands if r.e2e_ms is not None]
    out["latency_ms"] = {
        "proc_p50": round(percentile(proc, 0.5)),
        "proc_p95": round(percentile(proc, 0.95)),
        "e2e_p50": round(percentile(e2e, 0.5)) if e2e else None,
        "e2e_p95": round(percentile(e2e, 0.95)) if e2e else None,
    }
    return out


def print_report(summary: dict[str, Any], meta: dict[str, Any]) -> None:
    print("\n=== Summary ===")
    print(f"STT {meta['stt']} / {meta['model']} / {meta['device']} (load {meta['load_s']} s)")
    s = summary
    print(
        f"Direct STT  WER no-echo {s['stt_wer_no_echo']['wer']}%  all {s['stt_wer']['wer']}%  "
        f"CER {s['stt_wer']['cer']}%  "
        f"(n={s['stt_wer']['n']}, median {s['stt_ms_p50']:.0f} ms/clip)"
    )
    print("  by variant:", {k: v["wer"] for k, v in s["stt_wer_by_variant"].items()})
    print("Silence    ", s["silence_hallucinations"])
    if "wake" not in s:
        return
    w = s["wake"]
    print(
        f"Wake        precision {w['precision']}  recall {w['recall']}  "
        f"TP {w['tp']} FN {w['fn']} FP {w['fp']}"
    )
    print("  false triggers:", w["false_triggers"])
    print("  missed:        ", w["missed"])
    print(
        f"Pipeline    command WER {s['pipeline_wer']['wer']}%  router accuracy "
        f"{s['router_accuracy']}%  {s['router_by_variant']}"
    )
    print("Latency ms ", s["latency_ms"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--model", default="auto", help="Whisper model (auto/tiny/base/small/...)")
    parser.add_argument("--backend", choices=["whisper", "vosk"], default="whisper")
    parser.add_argument(
        "--pace", type=float, default=0.0, help="0 = as fast as possible, 1 = real time"
    )
    parser.add_argument("--models-dir", help="default: <user data dir>/models")
    parser.add_argument("--only", help="comma-separated substrings of clip ids to run")
    parser.add_argument("--variants", help="comma-separated variants to run")
    parser.add_argument("--stt-only", action="store_true", help="skip the pipeline run")
    parser.add_argument("--regen", action="store_true", help="re-synthesize the WAV files")
    parser.add_argument("--label", default="run", help="name of the results JSON")
    parser.add_argument(
        "--set", action="append", help="config override, e.g. audio.vad.threshold=0.4"
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    spec = yaml.safe_load((DATA / "phrases.yaml").read_text(encoding="utf-8"))
    clips = plan_clips(spec)
    if args.regen:
        for c in clips:
            c.path.unlink(missing_ok=True)
    models_dir = Path(args.models_dir) if args.models_dir else default_data_dir() / "models"
    asyncio.run(synthesize_all(spec, clips, models_dir))
    if args.only:
        needles = args.only.split(",")
        clips = [c for c in clips if any(n in c.id for n in needles)]
    if args.variants:
        wanted = args.variants.split(",")
        clips = [c for c in clips if c.variant in wanted or c.kind in ("echo_only", "silence")]
    results, meta = asyncio.run(run_clips(args, clips))
    summary = summarize(results, {c.id: c for c in clips}, stt_only=args.stt_only)
    print_report(summary, meta)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.label}.json"
    out.write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "clips": [asdict(r) for r in results]},
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
