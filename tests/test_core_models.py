"""Model downloads: resume, retries without hammering, hostile or truncated archives."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from aion import models

REAL_CLIENT = httpx.AsyncClient


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


async def test_download_resumes_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"0123456789abcdef"
    target = tmp_path / "m.bin"
    (tmp_path / "m.bin.part").write_bytes(body[:5])
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("range"))
        return httpx.Response(206, content=body[5:])

    _serve(monkeypatch, handler)
    await models.download_file("https://example.com/m.bin", target, "m")
    assert seen == ["bytes=5-"]
    assert target.read_bytes() == body
    assert not (tmp_path / "m.bin.part").exists()


async def test_client_errors_are_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    _serve(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await models.download_file("https://example.com/none", tmp_path / "x", "x")
    assert calls == 1


async def test_server_errors_are_retried_with_a_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pauses: list[float] = []

    async def no_sleep(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(models.asyncio, "sleep", no_sleep)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200, content=b"ok")

    _serve(monkeypatch, handler)
    await models.download_file("https://example.com/m", tmp_path / "m", "m")
    assert (tmp_path / "m").read_bytes() == b"ok"
    assert pauses == [2.0, 4.0]  # exponential, never a tight loop


def _zip(path: Path, entries: dict[str, bytes], *, link: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if link:
            info = zipfile.ZipInfo(link)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "C:/Windows/win.ini")
    return path


def test_model_archive_with_one_folder_is_flattened(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"vosk-model/am/final.mdl": b"x", "vosk-model/conf": b"y"})
    target = tmp_path / "out" / "vosk-model"
    target.parent.mkdir()
    models._extract_single_dir(archive, target)  # pyright: ignore[reportPrivateUsage]
    assert (target / "am" / "final.mdl").read_bytes() == b"x"


def test_model_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"../../evil.txt": b"x"})
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="Небезопасный"):
        models._extract_single_dir(archive, tmp_path / "out" / "m")  # pyright: ignore[reportPrivateUsage]
    assert not (tmp_path / "evil.txt").exists()


def test_model_archive_symlink_is_rejected(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"m/a": b"x"}, link="m/link")
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="ссылки"):
        models._extract_single_dir(archive, tmp_path / "out" / "m")  # pyright: ignore[reportPrivateUsage]


def test_truncated_archive_is_discarded_with_a_clear_message(tmp_path: Path) -> None:
    broken = tmp_path / "m.zip.part"
    broken.write_bytes(b"PK\x03\x04 not really a zip")
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="повреждён"):
        models._extract_single_dir(broken, tmp_path / "out" / "m")  # pyright: ignore[reportPrivateUsage]
    assert not broken.exists()  # the next attempt downloads it afresh
