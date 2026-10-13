"""Installing plugins (folder / zip / git) and their pip dependencies.

Plugins run with the full rights of the user, so installation is defensive about what it
fetches: https only, size limits, no path escapes or links in archives, no option injection
into git/pip. It cannot make untrusted code safe — see :func:`preview_plugin` for showing
the declared permissions before installing.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from loguru import logger

from aion.plugins.manifest import MANIFEST_FILE, PluginManifest, load_manifest

_DEPS_MARKER = ".aion-deps"
MAX_ARCHIVE_FILES = 5000
MAX_UNPACKED_BYTES = 200 << 20  # a plugin is code, not a data set
MAX_DOWNLOAD_BYTES = 50 << 20
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")  # same rule as the manifest

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
    """Unpack a plugin zip: no escaping paths, no links, bounded size (zip bombs)."""
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise InstallError("Архив повреждён или это не zip-файл") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise InstallError("В архиве слишком много файлов для плагина")
        if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise InstallError("Архив слишком большой после распаковки")
        root = target.resolve()
        for info in infos:
            name = info.filename
            absolute = name.startswith(("/", "\\")) or ":" in name
            if absolute or not (target / name).resolve().is_relative_to(root):
                raise InstallError(f"Небезопасный путь в архиве: {name}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise InstallError(f"Символические ссылки в архиве запрещены: {name}")
        zf.extractall(target)


async def _run(*cmd: str, timeout: float = 600, env: dict[str, str] | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        async with asyncio.timeout(timeout):
            out, _ = await proc.communicate()
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise InstallError(f"{' '.join(cmd[:2])}: не завершилось за {timeout:.0f} с") from e
    if proc.returncode != 0:
        raise InstallError(f"{' '.join(cmd[:3])}…: {out.decode(errors='replace')[-2000:]}")


def _check_url(url: str) -> None:
    """Plain http would let anybody on the network swap the code we are about to run."""
    import httpx

    parsed = httpx.URL(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.host in LOOPBACK):
        return
    raise InstallError("Плагин можно скачать только по https (http — лишь с этого компьютера)")


async def _download(url: str, dest: Path) -> None:
    import httpx

    from aion.core.errors import user_message

    _check_url(url)
    try:
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=60, max_redirects=5) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            _check_url(str(response.url))  # a redirect must not downgrade to http
            size = 0
            with dest.open("wb") as fh:
                async for chunk in response.aiter_bytes(1 << 16):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise InstallError("Файл плагина слишком большой")
                    fh.write(chunk)
    except httpx.HTTPError as e:
        raise InstallError(f"Не удалось скачать плагин: {user_message(e)}") from e


def _check_git_source(source: str) -> str:
    url = source.removeprefix("git+")
    if url.startswith("-"):
        raise InstallError("Адрес git не может начинаться с «-»")
    if "::" in url.split("/", 1)[0]:  # git remote helpers such as ext::sh -c ...
        raise InstallError("Этот тип адреса git не поддерживается")
    if url.lower().startswith("http://"):
        _check_url(url)
    return url


async def _fetch(source: str, tmp: Path) -> Path:
    """Get the plugin folder for ``source`` (dir / zip / git) into or under ``tmp``."""
    kind = detect_source(source)
    if kind == "git":
        url = _check_git_source(source)
        if shutil.which("git") is None:
            raise InstallError("Для установки из git нужен установленный git")
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",  # no hanging on a credentials prompt
            "GIT_ALLOW_PROTOCOL": "https:ssh:git",  # no file:// or ext:: transports
        }
        await _run(
            "git", "clone", "--depth", "1", "--", url, str(tmp / "src"), env=env, timeout=300
        )
        return find_plugin_root(tmp / "src")
    if kind == "zip":
        archive = Path(source)
        if source.lower().startswith(("http://", "https://")):
            archive = tmp / "plugin.zip"
            await _download(source, archive)
        if not archive.is_file():
            raise InstallError(f"Файл {archive} не найден")
        await asyncio.to_thread(_safe_extract, archive, tmp / "src")
        return find_plugin_root(tmp / "src")
    src = Path(source)
    if not src.is_dir():
        raise InstallError(f"Папка {src} не найдена")
    return find_plugin_root(src)


async def preview_plugin(source: str) -> PluginManifest:
    """Read the manifest of ``source`` without installing: name, permissions, dependencies.

    Interfaces use it to show the permissions and ask before :func:`install_plugin`.
    """
    with tempfile.TemporaryDirectory(prefix="aion-plugin-") as tmp:
        return load_manifest(await _fetch(source, Path(tmp)))


def _skip_unsafe(directory: str, names: list[str]) -> set[str]:
    skipped = set(shutil.ignore_patterns(".git", "__pycache__")(directory, names))
    # a link could point at any file of the user and be copied into the plugin as its content
    skipped |= {n for n in names if (Path(directory) / n).is_symlink()}
    return skipped


def _replace_dir(root: Path, dest: Path, *, force: bool) -> None:
    """Copy ``root`` to ``dest``; an existing plugin survives a failed copy."""
    staging = dest.with_name(f".{dest.name}.new")
    backup = dest.with_name(f".{dest.name}.old")
    for leftover in (staging, backup):
        shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(root, staging, ignore=_skip_unsafe)
    try:
        if dest.exists():
            if not force:
                raise InstallError(f"Плагин {dest.name} уже установлен (--force — заменить)")
            dest.rename(backup)
        staging.rename(dest)
    except BaseException:
        if backup.exists() and not dest.exists():
            backup.rename(dest)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


async def install_plugin(
    source: str,
    target_root: Path,
    *,
    force: bool = False,
    confirm: Callable[[PluginManifest], Awaitable[bool]] | None = None,
) -> PluginManifest:
    """Copy a plugin into ``target_root/<name>`` and validate its manifest.

    ``confirm`` is called with the manifest before anything is installed (to show the
    permissions); returning False cancels with :class:`InstallError`.
    """
    with tempfile.TemporaryDirectory(prefix="aion-plugin-") as tmp_str:
        root = await _fetch(source, Path(tmp_str))
        manifest = load_manifest(root)
        if confirm is not None and not await confirm(manifest):
            raise InstallError("Установка отменена")
        dest = target_root / manifest.name
        if dest.exists() and not force:
            raise InstallError(f"Плагин {manifest.name} уже установлен (--force — заменить)")
        target_root.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_replace_dir, root, dest, force=force)
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
    for requirement in manifest.dependencies:
        # "--index-url ..." or "-r file" would be taken by pip/uv as options, not packages
        if not requirement.strip() or requirement.strip().startswith("-"):
            raise InstallError(f"Недопустимая зависимость плагина: {requirement!r}")
    await asyncio.to_thread(shutil.rmtree, target, True)
    target.mkdir(parents=True)
    if uv := shutil.which("uv"):
        cmd = [uv, "pip", "install", "--python", sys.executable, "--target", str(target)]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        cmd += ["--target", str(target)]
    await _run(*cmd, *manifest.dependencies)
    (target / _DEPS_MARKER).write_text(_deps_key(manifest), encoding="utf-8")


def remove_plugin(name: str, roots: list[Path], deps_root: Path) -> Path:
    if not _NAME.fullmatch(name):  # "..", "a/b": never delete outside the plugin folders
        raise InstallError(f"Недопустимое имя плагина: {name!r}")
    for root in roots:
        path = root / name
        if (path / MANIFEST_FILE).is_file():
            shutil.rmtree(path)
            shutil.rmtree(deps_root / name, ignore_errors=True)
            return path
    raise InstallError(f"Плагин {name} не найден в {', '.join(map(str, roots))}")
