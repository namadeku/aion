"""On-demand CUDA libraries for Whisper on NVIDIA GPUs (the installer ships CPU only).

cuBLAS/cuDNN come from the official ``nvidia-*-cu12`` wheels on PyPI (same versions as
``uv.lock``); only their DLLs are kept, in ``data_dir/cuda/bin``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from aion.models import Progress, download_file


@dataclass(frozen=True)
class Wheel:
    name: str
    url: str
    sha256: str
    size: int


_PYPI = "https://files.pythonhosted.org/packages"
WHEELS = (
    Wheel(
        "cuBLAS",
        f"{_PYPI}/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
        "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
        553_162_896,
    ),
    Wheel(
        "NVRTC",
        f"{_PYPI}/52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
        "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
        "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349",
        76_408_187,
    ),
    Wheel(
        "cuDNN",
        f"{_PYPI}/aa/38/f856579877f7c1c5066e61182e7de7bc27bf35a78c8d1b0fa592e6985bc4/"
        "nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl",
        "06e9b0026f3bad97d2b58666330fabec04fe1672f776661ecb0ce0029c27f142",
        743_068_852,
    ),
)
DOWNLOAD_SIZE = sum(w.size for w in WHEELS)
MARKER = "installed.json"
SKIP_DLLS = {"nvblas64_12.dll"}  # BLAS shim, not used by CTranslate2


def cuda_dir(data_dir: Path) -> Path:
    return data_dir / "cuda"


def has_nvidia_gpu() -> bool:
    """The NVIDIA driver installs nvidia-smi into System32."""
    return sys.platform == "win32" and shutil.which("nvidia-smi") is not None


def bundled() -> bool:
    """CUDA libraries come from pip packages (source install with ``--extra cuda``)."""
    try:
        import nvidia  # pyright: ignore[reportMissingImports]
    except ImportError:
        return False
    return any(Path(p, "cublas", "bin").is_dir() for p in getattr(nvidia, "__path__", []))


def installed(data_dir: Path) -> bool:
    try:
        data = json.loads((cuda_dir(data_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("wheels") == [w.url for w in WHEELS]


def available(data_dir: Path) -> bool:
    return bundled() or installed(data_dir)


def add_dll_dirs(data_dir: Path | None) -> None:
    """Make cuBLAS/cuDNN visible to CTranslate2: pip packages and the downloaded copy."""
    if sys.platform != "win32":
        return
    dirs: list[Path] = []
    try:
        import nvidia  # pyright: ignore[reportMissingImports]

        for root in getattr(nvidia, "__path__", []):
            dirs.extend(Path(root).glob("*/bin"))
    except ImportError:
        pass
    if data_dir is not None and installed(data_dir):
        dirs.append(cuda_dir(data_dir) / "bin")
    for bin_dir in dirs:
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


async def download(data_dir: Path, progress: Progress | None = None) -> Path:
    """Download the wheels, verify their hashes and keep only the DLLs."""
    root = cuda_dir(data_dir)
    bin_dir = root / "bin"
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).unlink(missing_ok=True)
    for wheel in WHEELS:
        archive = root / Path(wheel.url).name
        if not archive.exists():
            logger.info("Скачиваю {} ({:.0f} МБ)", wheel.name, wheel.size / 1e6)
            await download_file(wheel.url, archive, wheel.name, progress)
        try:
            await asyncio.to_thread(_verify, archive, wheel.sha256)
            await asyncio.to_thread(_extract_dlls, archive, bin_dir)
        finally:
            archive.unlink(missing_ok=True)
    marker = {"wheels": [w.url for w in WHEELS]}
    (root / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    logger.info("Библиотеки CUDA установлены в {}", bin_dir)
    return bin_dir


def remove(data_dir: Path) -> None:
    shutil.rmtree(cuda_dir(data_dir), ignore_errors=True)


def _verify(path: Path, sha256: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise ValueError(f"Контрольная сумма {path.name} не совпала — файл повреждён")


def _extract_dlls(archive: Path, bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name.lower().endswith(".dll") or name in SKIP_DLLS:
                continue
            with zf.open(info) as src, (bin_dir / name).open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
