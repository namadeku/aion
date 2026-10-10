"""Loguru setup: console + rotating file in the data directory."""

from __future__ import annotations

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
