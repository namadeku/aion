from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.ui.server import UiServer

TOKEN = "test-token"


@pytest.fixture
def client(app_store: ConfigStore, tmp_path: Path) -> Iterator[tuple[TestClient, Aion]]:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    server = UiServer(aion, token=TOKEN)
    with TestClient(server.api) as tc:
        # start the app on the TestClient's event loop
        tc.portal.call(aion.start)  # pyright: ignore[reportOptionalMemberAccess]
        tc.headers["X-Aion-Token"] = TOKEN
        yield tc, aion
        tc.portal.call(aion.stop)  # pyright: ignore[reportOptionalMemberAccess]


def test_token_is_required(client: Any) -> None:
    tc, _ = client
    assert tc.get("/api/config", headers={"X-Aion-Token": "wrong"}).status_code == 401
    assert tc.get("/api/config").status_code == 200


def test_config_patch_and_validation(client: Any) -> None:
    tc, aion = client
    r = tc.patch("/api/config", json={"assistant": {"user_address": "мэм"}})
    assert r.status_code == 200
    assert aion.config.assistant.user_address == "мэм"
    bad = tc.patch("/api/config", json={"llm": {"temperature": 50}})
    assert bad.status_code == 422
    assert "temperature" in bad.json()["error"][0]


def test_profiles_create_and_delete(client: Any) -> None:
    tc, aion = client
    assert tc.post("/api/profiles/friday", json={"name": "Пятница"}).status_code == 200
    assert aion.config.profiles["friday"].name == "Пятница"
    assert tc.delete("/api/profiles/friday").status_code == 200
    assert "friday" not in aion.config.profiles
    assert tc.delete("/api/profiles/aion").status_code == 400  # the only one left


def test_plugins_list_and_toggle(client: Any) -> None:
    tc, aion = client
    plugins = {p["name"]: p for p in tc.get("/api/plugins").json()}
    assert plugins["weather"]["settings"]["city"] == "Москва"
    assert plugins["weather"]["settings_schema"]["city"]["type"] == "string"
    assert tc.post("/api/plugins/weather/disable").status_code == 200
    assert aion.plugins.records["weather"].status == "disabled"
    assert tc.post("/api/plugins/weather/enable").status_code == 200
    assert aion.plugins.records["weather"].status == "loaded"
    r = tc.put("/api/plugins/weather/settings", json={"city": "Казань"})
    assert r.json()["city"] == "Казань"
    assert tc.delete("/api/plugins/weather").status_code == 400  # builtins can't be removed


def test_websocket_snapshot_and_text(client: Any) -> None:
    tc, _ = client
    with tc.websocket_connect(f"/ws?token={TOKEN}") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["name"] == "Aion"
        ws.send_json({"type": "text", "text": "который час"})
        seen: list[str] = []
        for _ in range(30):
            msg = ws.receive_json()
            seen.append(msg["type"])
            if msg["type"] == "assistant_reply" and msg["text"].startswith("Сейчас"):
                break
        assert "speech_recognized" in seen
        assert "intent_matched" in seen
    history = tc.get("/api/history").json()
    assert history[0]["text"] == "который час"
    assert history[1]["text"].startswith("Сейчас")


def test_websocket_rejects_bad_token_and_foreign_origin(client: Any) -> None:
    tc, _ = client
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), tc.websocket_connect("/ws?token=nope") as ws:
        ws.receive_json()
    with (
        pytest.raises(WebSocketDisconnect),
        tc.websocket_connect(
            f"/ws?token={TOKEN}", headers={"origin": "https://evil.example"}
        ) as ws,
    ):
        ws.receive_json()


def test_avatar_files_are_confined(client: Any, tmp_path: Path) -> None:
    tc, aion = client
    models = tmp_path / "models"
    models.mkdir()
    (models / "me.vrm").write_bytes(b"glTF")
    (tmp_path / "secret.txt").write_text("nope")
    aion.store.update(
        {"profiles": {"aion": {"avatar": {"kind": "vrm", "path": str(models / "me.vrm")}}}}
    )
    assert tc.get(f"/avatar/{TOKEN}/me.vrm").content == b"glTF"
    assert tc.get("/avatar/wrong/me.vrm").status_code == 401
    assert tc.get(f"/avatar/{TOKEN}/..%2Fsecret.txt").status_code == 404


def test_secrets_are_never_returned(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tc, _ = client
    monkeypatch.setenv("AION_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (
        tc.put("/api/secrets", json={"name": "OPENAI_API_KEY", "value": "sk-test"}).status_code
        == 200
    )
    listed = tc.get("/api/secrets").json()
    assert listed["OPENAI_API_KEY"] is True
    assert "sk-test" not in str(listed)
    assert "sk-test" in (tmp_path / "home" / ".env").read_text()


def test_mcp_servers_add_list_delete(client: Any) -> None:
    tc, aion = client
    # a command that can't start: the server shows up with an error, the app keeps working
    server = {"command": "aion-no-such-mcp-server", "tools": ["a"], "confirm": "always"}
    assert tc.put("/api/mcp/shop", json=server).status_code == 200
    assert aion.config.mcp["shop"].confirm == "always"
    assert tc.put("/api/mcp/bad name", json=server).status_code == 422
    assert tc.put("/api/mcp/x", json={"confirm": "sometimes"}).status_code == 422
    (item,) = tc.get("/api/mcp").json()
    assert item["name"] == "shop"
    assert item["state"] in {"connecting", "error"}
    assert tc.delete("/api/mcp/shop").status_code == 200
    assert aion.config.mcp == {}
    assert tc.delete("/api/mcp/shop").status_code == 404
