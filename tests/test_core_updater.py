"""Updater: trusted sources only, a checksum is mandatory, failures are readable."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from aion import updater
from aion.core.errors import NO_INTERNET

GITHUB = "https://github.com/namadeku/aion/releases/download/v0.3.0/Aion-Setup-0.3.0.exe"
REAL_CLIENT = httpx.AsyncClient  # captured before any test patches it
SHA = hashlib.sha256(b"payload").hexdigest()


def release(**overrides: Any) -> updater.Release:
    fields: dict[str, Any] = {
        "version": "0.3.0",
        "notes": "",
        "url": "",
        "asset_url": GITHUB,
        "asset_name": "Aion-Setup-0.3.0.exe",
        "size": 1,
        "sha256": SHA,
    }
    fields.update(overrides)
    return updater.Release(**fields)


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def download_file(url: str, target: Path, label: str, progress: object = None) -> None:
        calls.append(url)
        target.write_bytes(b"payload")

    monkeypatch.setattr(updater, "download_file", download_file)
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x/Aion-Setup-0.3.0.exe",  # not https
        "https://evil.example/Aion-Setup-0.3.0.exe",
        "https://github.com.evil.example/Aion-Setup-0.3.0.exe",  # suffix trick
        "https://notgithub.com/Aion-Setup-0.3.0.exe",
        "",
    ],
)
async def test_installer_from_an_untrusted_url_is_refused(
    tmp_path: Path, fake_download: list[str], url: str
) -> None:
    with pytest.raises(updater.UpdateError, match="не похож на GitHub"):
        await updater.download(release(asset_url=url), tmp_path)
    assert fake_download == []


async def test_github_cdn_hosts_are_accepted(tmp_path: Path, fake_download: list[str]) -> None:
    url = "https://objects.githubusercontent.com/github-production-release-asset/x"
    path = await updater.download(release(asset_url=url), tmp_path)
    assert path.name == "Aion-Setup-0.3.0.exe"
    assert fake_download == [url]


async def test_release_without_checksum_is_refused(
    tmp_path: Path, fake_download: list[str]
) -> None:
    with pytest.raises(updater.UpdateError, match="контрольной суммы"):
        await updater.download(release(sha256=None), tmp_path)
    assert fake_download == []
    assert not list(tmp_path.glob("*.exe"))


async def test_weird_asset_name_is_refused(tmp_path: Path, fake_download: list[str]) -> None:
    with pytest.raises(updater.UpdateError, match="имя файла"):
        await updater.download(release(asset_name="..\\..\\evil.exe"), tmp_path)


async def test_network_failure_while_downloading_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def offline(*_a: Any, **_k: Any) -> None:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(updater, "download_file", offline)
    with pytest.raises(updater.UpdateError) as info:
        await updater.download(release(), tmp_path)
    assert str(info.value) == NO_INTERNET


def _api(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr(
        updater.httpx,
        "AsyncClient",
        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


async def test_check_without_internet_gives_a_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _api(monkeypatch, offline)
    with pytest.raises(updater.UpdateError, match="интернету"):
        await updater.check()


async def test_check_survives_rate_limit_and_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    _api(monkeypatch, lambda _r: httpx.Response(403, json={"message": "rate limit"}))
    with pytest.raises(updater.UpdateError, match="ограничил"):
        await updater.check()
    _api(monkeypatch, lambda _r: httpx.Response(200, content=b"<html>login</html>"))
    with pytest.raises(updater.UpdateError, match="непонятный"):
        await updater.check()


async def test_no_release_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _api(monkeypatch, lambda _r: httpx.Response(404))
    assert await updater.check() is None


def test_asset_without_download_url_does_not_crash() -> None:
    data = {
        "tag_name": "v9.0.0",
        "assets": [{"name": "Aion-Setup-9.0.0.exe", "size": 1, "digest": "sha256:" + "a" * 64}],
    }
    parsed = updater.parse_release(data)
    assert parsed is not None
    assert parsed.asset_url == ""  # refused later by download()
