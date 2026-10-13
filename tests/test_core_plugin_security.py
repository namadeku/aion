"""Plugin installation and loading: hostile archives, sources and manifests."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.plugins import installer
from aion.plugins.installer import (
    InstallError,
    install_dependencies,
    install_plugin,
    preview_plugin,
    remove_plugin,
)
from aion.plugins.manifest import PluginManifest
from tests.helpers import write_plugin

CODE = "from aion.sdk import Plugin\n\n\nclass P(Plugin):\n    pass\n"


def make_zip(path: Path, entries: dict[str, str], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "C:/Windows/win.ini")
    return path


# -- archives --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["/etc/evil.txt", "\\evil.txt", "C:/evil.txt", "../../evil.txt"])
async def test_zip_with_escaping_paths_is_rejected(tmp_path: Path, name: str) -> None:
    archive = make_zip(tmp_path / "a.zip", {name: "x", "plugin.yaml": "name: evil\n"})
    with pytest.raises(InstallError, match="Небезопасный"):
        await install_plugin(str(archive), tmp_path / "installed")
    assert not (tmp_path / "evil.txt").exists()


async def test_zip_with_symlink_is_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {"plugin.yaml": "name: evil\n"}, symlink="link")
    with pytest.raises(InstallError, match="ссылки"):
        await install_plugin(str(archive), tmp_path / "installed")


async def test_zip_bomb_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = make_zip(tmp_path / "a.zip", {"plugin.yaml": "name: p\n", "big.bin": "0" * 5000})
    monkeypatch.setattr(installer, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(InstallError, match="слишком большой"):
        await install_plugin(str(archive), tmp_path / "installed")
    monkeypatch.setattr(installer, "MAX_UNPACKED_BYTES", 10**9)
    monkeypatch.setattr(installer, "MAX_ARCHIVE_FILES", 1)
    with pytest.raises(InstallError, match="слишком много"):
        await install_plugin(str(archive), tmp_path / "installed")


async def test_not_a_zip_is_a_readable_error(tmp_path: Path) -> None:
    fake = tmp_path / "a.zip"
    fake.write_text("hello", encoding="utf-8")
    with pytest.raises(InstallError, match="повреждён"):
        await install_plugin(str(fake), tmp_path / "installed")


# -- sources ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "--upload-pack=calc.exe.git",
        "ext::sh -c touch% /tmp/pwned.git",
        "http://example.com/plugin.git",
        "http://example.com/plugin.zip",
    ],
)
async def test_hostile_sources_are_rejected_before_any_network_or_process(
    tmp_path: Path, source: str
) -> None:
    with pytest.raises(InstallError):
        await install_plugin(source, tmp_path / "installed")


def _mock_download(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_download_is_size_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "MAX_DOWNLOAD_BYTES", 100)
    _mock_download(monkeypatch, lambda _r: httpx.Response(200, content=b"x" * 1000))
    with pytest.raises(InstallError, match="слишком большой"):
        await install_plugin("https://example.com/p.zip", tmp_path / "installed")


async def test_download_without_internet_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _mock_download(monkeypatch, offline)
    with pytest.raises(InstallError, match="подключения к интернету"):
        await install_plugin("https://example.com/p.zip", tmp_path / "installed")


async def test_redirect_to_plain_http_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://evil.example/p.zip"})
        return httpx.Response(200, content=b"x")

    _mock_download(monkeypatch, redirect)
    with pytest.raises(InstallError, match="https"):
        await install_plugin("https://example.com/p.zip", tmp_path / "installed")


# -- dependencies and removal ----------------------------------------------------------


@pytest.mark.parametrize("requirement", ["--index-url=http://evil/simple", "-r /etc/passwd", " "])
async def test_dependency_that_looks_like_an_option_is_rejected(
    tmp_path: Path, requirement: str
) -> None:
    manifest = PluginManifest(name="p", dependencies=[requirement])
    with pytest.raises(InstallError, match="Недопустимая"):
        await install_dependencies(manifest, tmp_path / "deps")


@pytest.mark.parametrize("name", ["..", "../x", "a/b", "A", ""])
def test_remove_plugin_rejects_names_that_could_leave_the_folder(tmp_path: Path, name: str) -> None:
    (tmp_path / "plugin.yaml").write_text("name: victim\n", encoding="utf-8")
    root = tmp_path / "plugins"
    root.mkdir()
    with pytest.raises(InstallError, match="Недопустимое"):
        remove_plugin(name, [root], tmp_path / "deps")
    assert (tmp_path / "plugin.yaml").exists()


# -- installation behaviour ------------------------------------------------------------


async def test_confirm_callback_can_cancel_and_sees_permissions(tmp_path: Path) -> None:
    src = write_plugin(tmp_path / "src", "risky", CODE, "permissions: [shell]\n")
    seen: list[list[str]] = []

    async def decline(manifest: PluginManifest) -> bool:
        seen.append(manifest.dangerous_permissions)
        return False

    with pytest.raises(InstallError, match="отменена"):
        await install_plugin(str(src), tmp_path / "installed", confirm=decline)
    assert seen == [["shell"]]
    assert not (tmp_path / "installed" / "risky").exists()

    async def accept(_m: PluginManifest) -> bool:
        return True

    await install_plugin(str(src), tmp_path / "installed", confirm=accept)
    assert (tmp_path / "installed" / "risky" / "main.py").exists()


async def test_preview_installs_nothing(tmp_path: Path) -> None:
    src = write_plugin(tmp_path / "src", "risky", CODE, "permissions: [files]\n")
    manifest = await preview_plugin(str(src))
    assert manifest.dangerous_permissions == ["files"]
    assert not (tmp_path / "installed").exists()


async def test_failed_reinstall_keeps_the_old_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = write_plugin(tmp_path / "src", "keeper", CODE)
    target = tmp_path / "installed"
    await install_plugin(str(src), target)
    (target / "keeper" / "marker.txt").write_text("old", encoding="utf-8")

    def broken_copy(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", broken_copy)
    with pytest.raises(OSError, match="disk full"):
        await install_plugin(str(src), target, force=True)
    assert (target / "keeper" / "marker.txt").read_text(encoding="utf-8") == "old"


async def test_symlinks_in_a_folder_source_are_not_copied(tmp_path: Path) -> None:
    src = write_plugin(tmp_path / "src", "linked", CODE)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    try:
        (src / "leak.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    await install_plugin(str(src), tmp_path / "installed")
    assert not (tmp_path / "installed" / "linked" / "leak.txt").exists()


# -- loading ---------------------------------------------------------------------------


async def test_entry_outside_the_plugin_folder_is_refused(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    write_plugin(plugins_dir, "sneaky", CODE, "entry: ../outside.py\n")
    (plugins_dir / "outside.py").write_text(CODE, encoding="utf-8")
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    async with aion:
        record = aion.plugins.records["sneaky"]
        assert record.status == "error"
        assert "вне папки" in (record.error or "")


async def test_plugins_load_side_by_side(app_store: ConfigStore, plugins_dir: Path) -> None:
    slow = """
    import asyncio
    import time
    from pathlib import Path
    from aion.sdk import Plugin

    class Slow(Plugin):
        async def on_load(self) -> None:
            Path(__file__).with_name("t.txt").write_text(str(time.monotonic()))
            await asyncio.sleep(0.5)
    """
    for i in range(4):
        write_plugin(plugins_dir, f"slow{i}", slow)
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    async with aion:
        assert all(aion.plugins.records[f"slow{i}"].status == "loaded" for i in range(4))
    starts = [float((plugins_dir / f"slow{i}" / "t.txt").read_text()) for i in range(4)]
    # one after another they would start 0.5 s apart; side by side they start together
    assert max(starts) - min(starts) < 0.3
