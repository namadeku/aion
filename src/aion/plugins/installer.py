"""Installing plugins (folder / zip / git) and their pip dependencies."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from loguru import logger

from aion.plugins.manifest import MANIFEST_FILE, PluginManifest, load_manifest

_DEPS_MARKER = ".aion-deps"

SourceKind = Literal["dir", "zip", "git"]


class InstallError(Exception):
    pass


def detect_source(source: str) -> SourceKind:
    lowered = source.lower()
    if lowered.endswith(".zip"):
        return "zip"
    if lowered.startswith(("git@", "git+", "ssh://")) or lowered.endswith(".git"):
        return "git"
    if lowered.startswith(("http://", "https://")):
        return "git"
    return "dir"


def find_plugin_root(path: Path) -> Path:
    """The folder containing plugin.yaml: ``path`` itself or its single subfolder."""
    if (path / MANIFEST_FILE).is_file():
        return path
    candidates = [p.parent for p in path.glob(f"*/{MANIFEST_FILE}")]
    if len(candidates) == 1:
        return candidates[0]
    raise InstallError(f"В {path} не найден единственный {MANIFEST_FILE}")


def _safe_extract(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            dest = (target / member).resolve()
            if not dest.is_relative_to(target.resolve()):
                raise InstallError(f"Небезопасный путь в архиве: {member}")
        zf.extractall(target)


async def _run(*cmd: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise InstallError(f"{' '.join(cmd[:3])}…: {out.decode(errors='replace')[-2000:]}")


async def _download(url: str, dest: Path) -> None:
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)


async def install_plugin(source: str, target_root: Path, *, force: bool = False) -> PluginManifest:
    """Copy a plugin into ``target_root/<name>`` and validate its manifest."""
    kind = detect_source(source)
    with tempfile.TemporaryDirectory(prefix="aion-plugin-") as tmp_str:
        tmp = Path(tmp_str)
        if kind == "git":
            if shutil.which("git") is None:
                raise InstallError("Для установки из git нужен установленный git")
            await _run(
                "git", "clone", "--depth", "1", source.removeprefix("git+"), str(tmp / "src")
            )
            root = find_plugin_root(tmp / "src")
        elif kind == "zip":
            archive = Path(source)
            if source.lower().startswith(("http://", "https://")):
                archive = tmp / "plugin.zip"
                await _download(source, archive)
            if not archive.is_file():
                raise InstallError(f"Файл {archive} не найден")
            _safe_extract(archive, tmp / "src")
            root = find_plugin_root(tmp / "src")
        else:
            src = Path(source)
            if not src.is_dir():
                raise InstallError(f"Папка {src} не найдена")
            root = find_plugin_root(src)

        manifest = load_manifest(root)
        dest = target_root / manifest.name
        if dest.exists():
            if not force:
                raise InstallError(f"Плагин {manifest.name} уже установлен (--force — заменить)")
            shutil.rmtree(dest)
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    logger.info("Плагин {} {} установлен в {}", manifest.name, manifest.version, dest)
    return manifest


def deps_ready(manifest: PluginManifest, target: Path) -> bool:
    marker = target / _DEPS_MARKER
    return marker.is_file() and marker.read_text(encoding="utf-8") == _deps_key(manifest)


def _deps_key(manifest: PluginManifest) -> str:
    return "\n".join(sorted(manifest.dependencies))


async def install_dependencies(manifest: PluginManifest, target: Path) -> None:
    """``pip install --target`` into a per-plugin folder (uv if available, else pip)."""
    if not manifest.dependencies:
        return
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if uv := shutil.which("uv"):
        cmd = [uv, "pip", "install", "--python", sys.executable, "--target", str(target)]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        cmd += ["--target", str(target)]
    await _run(*cmd, *manifest.dependencies)
    (target / _DEPS_MARKER).write_text(_deps_key(manifest), encoding="utf-8")


def remove_plugin(name: str, roots: list[Path], deps_root: Path) -> Path:
    for root in roots:
        path = root / name
        if (path / MANIFEST_FILE).is_file():
            shutil.rmtree(path)
            shutil.rmtree(deps_root / name, ignore_errors=True)
            return path
    raise InstallError(f"Плагин {name} не найден в {', '.join(map(str, roots))}")
