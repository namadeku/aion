"""Downloadable models (Vosk, Piper voices, Silero VAD) with progress reporting."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

Progress = Callable[[str, int, int], None]  # (label, downloaded, total)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOSK_BASE = "https://alphacephei.com/vosk/models"
SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
)


@dataclass(frozen=True)
class ModelFile:
    url: str
    dest: Path  # relative to the models dir
    unzip: bool = False  # extract the archive into ``dest`` (a directory)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str
    files: tuple[ModelFile, ...]

    def installed(self, models_dir: Path) -> bool:
        return all((models_dir / f.dest).exists() for f in self.files)


def piper_voice(name: str) -> ModelSpec:
    """``ru_RU-denis-medium`` -> files on the rhasspy/piper-voices Hugging Face repo."""
    try:
        lang_code, speaker, quality = name.split("-", 2)
    except ValueError as e:
        raise ValueError(f"Имя голоса Piper вида ru_RU-denis-medium, а не {name!r}") from e
    family = lang_code.split("_")[0]
    base = f"{PIPER_BASE}/{family}/{lang_code}/{speaker}/{quality}/{name}"
    return ModelSpec(
        key=f"piper:{name}",
        title=f"Голос Piper {name}",
        files=(
            ModelFile(f"{base}.onnx", Path("piper") / f"{name}.onnx"),
            ModelFile(f"{base}.onnx.json", Path("piper") / f"{name}.onnx.json"),
        ),
    )


def vosk_model(name: str) -> ModelSpec:
    return ModelSpec(
        key=f"vosk:{name}",
        title=f"Vosk {name}",
        files=(ModelFile(f"{VOSK_BASE}/{name}.zip", Path("vosk") / name, unzip=True),),
    )


def silero_vad() -> ModelSpec:
    return ModelSpec(
        key="silero-vad",
        title="Silero VAD",
        files=(ModelFile(SILERO_VAD_URL, Path("silero_vad.onnx")),),
    )


async def ensure(spec: ModelSpec, models_dir: Path, progress: Progress | None = None) -> Path:
    """Download missing files of ``spec``; returns the models dir."""
    for file in spec.files:
        target = models_dir / file.dest
        if target.exists():
            continue
        logger.info("Скачиваю {} ({})", spec.title, file.url)
        await _download(file, target, spec.title, progress)
    return models_dir


HF_BASE = "https://huggingface.co"
# what faster-whisper needs from a model repo (faster_whisper.utils.download_model)
WHISPER_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json")


def whisper_repo(name: str) -> str:
    """``small`` -> ``Systran/faster-whisper-small``; a repo id is returned as is."""
    if "/" in name:
        return name
    from faster_whisper.utils import _MODELS  # pyright: ignore[reportPrivateUsage]

    try:
        return _MODELS[name]
    except KeyError as e:
        raise ValueError(f"Неизвестная модель Whisper: {name}") from e


def whisper_dir(models_dir: Path, name: str) -> Path:
    return models_dir / "whisper" / name.replace("/", "--")


def find_whisper(models_dir: Path, name: str) -> Path | None:
    """A complete local copy of the model: our own download or an older huggingface_hub cache."""
    own = whisper_dir(models_dir, name)
    if (own / "model.bin").exists():  # downloaded last, see ensure_whisper
        return own
    cache = models_dir / "whisper" / f"models--{whisper_repo(name).replace('/', '--')}"
    for snapshot in sorted((cache / "snapshots").glob("*")):
        if (snapshot / "model.bin").exists():
            return snapshot
    return None


async def whisper_files(repo: str) -> list[tuple[str, int]]:
    """Files of a faster-whisper repo as (name, size), ``model.bin`` last."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(f"{HF_BASE}/api/models/{repo}/tree/main")
        response.raise_for_status()
    files = [
        (str(f["path"]), int(f.get("size", 0)))
        for f in response.json()
        if f.get("type") == "file"
        and (f["path"] in WHISPER_FILES or str(f["path"]).startswith("vocabulary."))
    ]
    if not any(name == "model.bin" for name, _ in files):
        raise RuntimeError(f"В {repo} нет model.bin")
    return sorted(files, key=lambda f: f[0] == "model.bin")


async def ensure_whisper(name: str, models_dir: Path, progress: Progress | None = None) -> Path:
    """Download a faster-whisper model with byte progress; returns its directory.

    huggingface_hub (what faster-whisper uses itself) reports no progress, so the biggest
    download of the first start looked like a hang.
    """
    if found := find_whisper(models_dir, name):
        return found
    repo = whisper_repo(name)
    files = await whisper_files(repo)
    target = whisper_dir(models_dir, name)
    label = f"Распознавание речи: Whisper {name}"
    total = sum(size for _, size in files)
    logger.info("Скачиваю Whisper {} ({}, {:.0f} МБ)", name, repo, total / 1e6)
    finished = 0
    for file, size in files:

        def report(_label: str, done: int, _total: int, base: int = finished) -> None:
            if progress:
                progress(label, base + done, total)

        url = f"{HF_BASE}/{repo}/resolve/main/{file}"
        await _download(ModelFile(url, target / file), target / file, label, report)
        finished += size
    return target


MAX_ATTEMPTS = 8


async def download_file(
    url: str, target: Path, label: str, progress: Progress | None = None
) -> None:
    """Download one file with resume and retries (the target is written atomically)."""
    await _download(ModelFile(url, target), target, label, progress)


async def _download(file: ModelFile, target: Path, label: str, progress: Progress | None) -> None:
    """Download with resume: a ``.part`` file survives dropped connections and restarts."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await _fetch_into(file.url, part, label, progress)
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if attempt == MAX_ATTEMPTS or (
                isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500
            ):
                raise
            delay = min(30.0, 2.0**attempt)
            logger.warning(
                "Загрузка {} прервалась ({}), попытка {}/{} через {:.0f} с",
                label,
                type(e).__name__,
                attempt + 1,
                MAX_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
    if file.unzip:
        await asyncio.to_thread(_extract_single_dir, part, target)
        part.unlink(missing_ok=True)
    else:
        part.replace(target)


async def _fetch_into(url: str, part: Path, label: str, progress: Progress | None) -> None:
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=60)) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code == 416:  # already complete
            return
        response.raise_for_status()
        if done and response.status_code != 206:  # server ignored Range: start over
            done = 0
        total = done + int(response.headers.get("content-length", 0))
        with part.open("ab" if done else "wb") as fh:
            async for chunk in response.aiter_bytes(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(label, done, total)
    if progress:  # also ends downloads whose size the server did not send
        progress(label, done, done)


def _extract_single_dir(archive: Path, target: Path) -> None:
    """Extract a zip whose content is one top-level folder into ``target``."""
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".extract-") as tmp_str:
        tmp = Path(tmp_str)
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not (tmp / member).resolve().is_relative_to(tmp.resolve()):
                    raise ValueError(f"Небезопасный путь в архиве: {member}")
            zf.extractall(tmp)
        entries = list(tmp.iterdir())
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
        shutil.move(str(source), str(target))
