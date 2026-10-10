from __future__ import annotations

import contextlib
import hashlib
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aion import cuda, updater
from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.ui import server as server_module
from aion.ui.server import UiServer

TOKEN = "test-token"


def _release_json(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag_name": "v0.3.0",
        "body": "- новое",
        "html_url": "https://github.com/namadeku/aion/releases/tag/v0.3.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://x/sums", "size": 90},
            {
                "name": "Aion-Setup-0.3.0.exe",
                "browser_download_url": "https://x/Aion-Setup-0.3.0.exe",
                "size": 300_000_000,
                "digest": "sha256:" + "ab" * 32,
            },
        ],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("0.2.0", "0.1.0", True),
        ("v0.1.10", "0.1.9", True),
        ("0.1", "0.1.0", False),
        ("0.1.0", "0.1.0", False),
        ("1.0.0-rc1", "0.9.9", True),
        ("0.0.9", "0.1.0", False),
    ],
)
def test_is_newer(candidate: str, current: str, newer: bool) -> None:
    assert updater.is_newer(candidate, current) is newer


def test_parse_release_picks_installer() -> None:
    release = updater.parse_release(_release_json())
    assert release is not None
    assert release.version == "0.3.0"
    assert release.asset_name == "Aion-Setup-0.3.0.exe"
    assert release.sha256 == "ab" * 32


def test_parse_release_skips_drafts_and_missing_installer() -> None:
    assert updater.parse_release(_release_json(prerelease=True)) is None
    assert updater.parse_release(_release_json(assets=[])) is None


async def test_download_verifies_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"installer bytes"

    async def fake_download(url: str, target: Path, label: str, progress: object = None) -> None:
        target.write_bytes(payload)

    monkeypatch.setattr(updater, "download_file", fake_download)
    good = updater.Release(
        "0.3.0", "", "", "u", "Aion-Setup-0.3.0.exe", 1, hashlib.sha256(payload).hexdigest()
    )
    (tmp_path / "Aion-Setup-0.2.0.exe").write_bytes(b"old")
    path = await updater.download(good, tmp_path)
    assert path.read_bytes() == payload
    assert not (tmp_path / "Aion-Setup-0.2.0.exe").exists()

    bad = updater.Release("0.4.0", "", "", "u", "Aion-Setup-0.4.0.exe", 1, "00" * 32)
    with pytest.raises(ValueError, match="Контрольная сумма"):
        await updater.download(bad, tmp_path)
    assert not (tmp_path / "Aion-Setup-0.4.0.exe").exists()


def test_clean_downloads_keeps_only_newer(tmp_path: Path) -> None:
    for version in ("0.0.1", updater.__version__, "999.0.0"):
        (tmp_path / f"Aion-Setup-{version}.exe").write_bytes(b"x")
    updater.clean_downloads(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["Aion-Setup-999.0.0.exe"]
    updater.clean_downloads(tmp_path / "missing")  # no folder yet: nothing to do


def test_self_update_only_in_installed_public_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "is_public", lambda: True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    assert not updater.supported()
    monkeypatch.setattr(updater, "is_installed", lambda: True)
    assert updater.supported()


async def test_cuda_download_keeps_only_dlls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel_path = tmp_path / "src.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr("nvidia/cublas/bin/cublas64_12.dll", b"dll")
        zf.writestr("nvidia/cublas/bin/nvblas64_12.dll", b"skip")
        zf.writestr("nvidia/cublas/include/cublas.h", b"header")
    data = wheel_path.read_bytes()
    wheel = cuda.Wheel("cuBLAS", "https://x/w.whl", hashlib.sha256(data).hexdigest(), len(data))
    monkeypatch.setattr(cuda, "WHEELS", (wheel,))

    async def fake_download(url: str, target: Path, label: str, progress: object = None) -> None:
        target.write_bytes(data)

    monkeypatch.setattr(cuda, "download_file", fake_download)
    data_dir = tmp_path / "data"
    assert not cuda.installed(data_dir)
    bin_dir = await cuda.download(data_dir)
    assert sorted(p.name for p in bin_dir.iterdir()) == ["cublas64_12.dll"]
    assert cuda.installed(data_dir)
    assert not list(cuda.cuda_dir(data_dir).glob("*.whl"))  # archives are removed

    marker = cuda.cuda_dir(data_dir) / cuda.MARKER
    marker.write_text(json.dumps({"wheels": ["old"]}), encoding="utf-8")
    assert not cuda.installed(data_dir)  # other versions need a new download


async def test_cuda_download_rejects_bad_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = cuda.Wheel("cuDNN", "https://x/w.whl", "00" * 32, 3)
    monkeypatch.setattr(cuda, "WHEELS", (wheel,))

    async def fake_download(url: str, target: Path, label: str, progress: object = None) -> None:
        target.write_bytes(b"bad")

    monkeypatch.setattr(cuda, "download_file", fake_download)
    with pytest.raises(ValueError, match="Контрольная сумма"):
        await cuda.download(tmp_path)
    assert not cuda.installed(tmp_path)


@contextlib.contextmanager
def _client(store: ConfigStore) -> Iterator[TestClient]:
    aion = Aion(store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    server = UiServer(aion, token=TOKEN)
    with TestClient(server.api) as tc:
        tc.portal.call(aion.start)  # pyright: ignore[reportOptionalMemberAccess]
        tc.headers["X-Aion-Token"] = TOKEN
        try:
            yield tc
        finally:
            tc.portal.call(aion.stop)  # pyright: ignore[reportOptionalMemberAccess]


def test_dev_edition_has_logs_and_update_status(
    app_store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "is_public", lambda: False)
    monkeypatch.setattr(updater, "is_public", lambda: False)
    with _client(app_store) as tc:
        assert tc.get("/api/logs").status_code == 200
        status = tc.get("/api/update").json()
        assert status["supported"] is False
        assert tc.post("/api/update/install").status_code == 409
        assert set(tc.get("/api/cuda").json()) >= {"gpu", "available", "size"}


def test_public_edition_hides_logs(app_store: ConfigStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_module, "is_public", lambda: True)
    with _client(app_store) as tc:
        assert tc.get("/api/logs").status_code in (404, 405)
