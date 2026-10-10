from __future__ import annotations

import asyncio
import importlib.util
import sys
import textwrap
import types
from pathlib import Path

from aion.app import Aion
from aion.core import NullOutput
from aion.plugins.manager import BUILTIN_DIR


async def settle(aion: Aion, ticks: int = 50) -> None:
    for _ in range(ticks):
        await asyncio.sleep(0)
    await aion.dialog.wait()
    await aion.speech.flush()


async def say(aion: Aion, text: str) -> list[str]:
    """Submit a phrase, wait for the turn and return what was said during it."""
    assert isinstance(aion.speech, NullOutput)
    before = len(aion.speech.spoken)
    await aion.dialog.submit(text)
    await settle(aion)
    return aion.speech.spoken[before:]


def write_plugin(root: Path, name: str, code: str, manifest: str = "") -> Path:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n" + textwrap.dedent(manifest), encoding="utf-8"
    )
    (path / "main.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return path


def load_builtin_module(plugin: str, module: str) -> types.ModuleType:
    path = BUILTIN_DIR / plugin / f"{module}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{plugin}_{module}", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
