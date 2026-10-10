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
