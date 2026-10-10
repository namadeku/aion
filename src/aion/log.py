"""Loguru setup: console + rotating file in the data directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


def setup_logging(
    level: str = "INFO", log_dir: Path | None = None, *, console: bool = True
) -> None:
    logger.remove()
    if console:
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> "
            "<cyan>{name}</cyan> {message}",
        )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "aion.log",
            level="DEBUG",
            rotation="5 MB",
            retention=5,
            encoding="utf-8",
            enqueue=True,
        )


def report_fatal(message: str, details: str = "") -> Path | None:
    """Make a fatal error visible: ``crash.log`` in the default data directory and, without a
    console, a message box.

    The windowed exe and pythonw have no console, so without this a failed start looks like
    "nothing happened". Returns the path of the crash log (``None`` if it could not be written).
    """
    from datetime import datetime

    from aion.config.schema import default_data_dir

    logger.error("{}\n{}", message, details)
    path: Path | None = default_data_dir() / "logs" / "crash.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{message}\n{details}\n")
    except OSError:
        path = None
    # cli.main() points a missing stderr to os.devnull
    if getattr(sys.stderr, "name", None) == os.devnull and sys.platform == "win32":
        import ctypes

        text = message if path is None else f"{message}\n\nПодробности: {path}"
        ctypes.windll.user32.MessageBoxW(None, text, "Aion не запустился", 0x10)  # MB_ICONERROR
    return path
