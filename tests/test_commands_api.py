"""REST and WebSocket API of user commands (docs/commands-api.md, section 6)."""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.ui.server import UiServer
from tests.test_commands_engine import FakeBackend

TOKEN = "test-token"
CMD = {
    "name": "Лампа",
    "triggers": ["волшебная лампа сияй"],
    "lang": "ru",
    "description": "проверка",
    "actions": [{"type": "minimize_all"}, {"type": "say", "text": "Сияю"}],
}


@pytest.fixture
def client(app_store: ConfigStore) -> Iterator[tuple[TestClient, Aion, FakeBackend]]:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    backend = FakeBackend()
    aion.commands.backend = backend
    server = UiServer(aion, token=TOKEN)
    with TestClient(server.api) as tc:
        tc.portal.call(aion.start)  # pyright: ignore[reportOptionalMemberAccess]
        tc.headers["X-Aion-Token"] = TOKEN
        yield tc, aion, backend
        tc.portal.call(aion.stop)  # pyright: ignore[reportOptionalMemberAccess]


def test_auth_is_required(client: Any) -> None:
    tc, _, _ = client
    for method, url in [
        ("get", "/api/commands"),
        ("get", "/api/commands/examples/guests"),
        ("post", "/api/commands/examples/guests/test"),
        ("get", "/api/commands/export"),
        ("get", "/api/commands/packs"),
        ("post", "/api/commands/record/start"),
        ("get", "/api/history/search?q=x"),
    ]:
        assert getattr(tc, method)(url, headers={"X-Aion-Token": "wrong"}).status_code == 401


def test_list_and_get(client: Any) -> None:
    tc, _, _ = client
    listing = tc.get("/api/commands").json()
    guests = next(c for c in listing["commands"] if c["id"] == "examples/guests")
    assert guests == {
        "id": "examples/guests",
        "folder": "examples",
        "name": "Протокол гостей",
        "triggers": ["протокол гостей", "(гости пришли|к нам гости)"],
        "enabled": True,
        "dangerous": False,
        "lang": "ru",
        "description": "Свернуть все окна, сделать звук тише и открыть сайт для гостей",
        "error": None,
    }
    assert {"path": "examples", "enabled": True} in listing["folders"]
    full = tc.get("/api/commands/examples/guests").json()
    assert full["actions"][1] == {"type": "volume", "set": 20}
    assert full["confirm"] == "auto"
    assert tc.get("/api/commands/nope").status_code == 404


def test_save_validate_toggle_delete(client: Any) -> None:
    tc, aion, _ = client
    r = tc.put("/api/commands/home/lamp", json=CMD)
    assert r.status_code == 200
    assert r.json()["id"] == "home/lamp"
    assert aion.router.match("волшебная лампа сияй") is not None

    bad = tc.put(
        "/api/commands/home/lamp", json={**CMD, "actions": [{"type": "wait", "seconds": 0}]}
    )
    assert bad.status_code == 422
    assert bad.json()["error"] == ["действие 1, seconds: больше 0"]
    assert tc.put("/api/commands/../evil", json=CMD).status_code in (404, 422)
    assert tc.put("/api/commands/export", json=CMD).status_code in (404, 405, 422)

    assert tc.post("/api/commands/home/lamp/toggle", json={"enabled": False}).json() == {"ok": True}
    assert aion.router.match("волшебная лампа сияй") is None
    assert tc.get("/api/commands/home/lamp").json()["enabled"] is False
    tc.post("/api/commands/home/lamp/toggle", json={"enabled": True})
    assert (
        tc.post("/api/commands/folders/toggle", json={"path": "home", "enabled": False}).status_code
        == 200
    )
    assert aion.router.match("волшебная лампа сияй") is None
    folders = tc.get("/api/commands").json()["folders"]
    assert {"path": "home", "enabled": False} in folders
    tc.post("/api/commands/folders/toggle", json={"path": "home", "enabled": True})
    assert (
        tc.post("/api/commands/folders/toggle", json={"path": "zzz", "enabled": True}).status_code
        == 404
    )
    assert tc.post("/api/commands/zzz/toggle", json={"enabled": True}).status_code == 404

    assert tc.delete("/api/commands/home/lamp").json() == {"ok": True}
    assert aion.router.match("волшебная лампа сияй") is None
    assert tc.delete("/api/commands/home/lamp").status_code == 404


def test_broken_file_is_shown_for_fixing(client: Any) -> None:
    tc, aion, _ = client
    path = aion.commands.store.root / "broken.yaml"
    path.write_text("name: [", encoding="utf-8")
    aion.commands.reload()
    full = tc.get("/api/commands/broken").json()
    assert full["raw"] == "name: ["
    assert "YAML" in full["error"]


def test_import_and_export(client: Any) -> None:
    tc, _, _ = client
    text = "name: Привет\ntriggers: [скажи привет всем]\nactions: [{type: say, text: привет}]\n"
    r = tc.post(
        "/api/commands/import", files={"file": ("hello.yaml", text.encode(), "application/x-yaml")}
    )
    assert r.json() == {"imported": ["hello"], "errors": []}
    raw = tc.post("/api/commands/import?filename=hello.yaml", content=text.encode())
    assert raw.json() == {"imported": ["hello-2"], "errors": []}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../../escape.yaml", text)
    evil = tc.post(
        "/api/commands/import", files={"file": ("evil.zip", buffer.getvalue(), "application/zip")}
    )
    assert evil.status_code == 422
    assert "Небезопасный путь" in evil.json()["error"][0]

    exported = tc.get("/api/commands/export?ids=hello,examples/guests")
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        assert sorted(zf.namelist()) == ["examples/guests.yaml", "hello.yaml"]


def test_packs(client: Any) -> None:
    tc, aion, _ = client
    packs = {p["name"]: p for p in tc.get("/api/commands/packs").json()}
    assert set(packs) >= {"work", "games", "media"}
    assert packs["games"]["installed"] is False
    assert tc.post("/api/commands/packs/games", json={"enabled": True, "lang": "en"}).json() == {
        "ok": True
    }
    games = {p["name"]: p for p in tc.get("/api/commands/packs").json()}["games"]
    assert (games["installed"], games["enabled"], games["selected_lang"]) == (True, True, "en")
    assert games["commands"] >= 3
    assert aion.router.match("open game bar") is not None
    assert aion.router.match("открой игровую панель") is None or (
        aion.router.match("открой игровую панель").command.plugin != "commands"  # pyright: ignore[reportOptionalMemberAccess]
    )
    tc.post("/api/commands/packs/games", json={"enabled": False})
    assert aion.router.match("open game bar") is None
    assert tc.post("/api/commands/packs/nope", json={"enabled": True}).status_code == 404


def test_test_run_and_confirmation_over_websocket(client: Any) -> None:
    tc, _, backend = client
    danger = {"name": "Опасно", "actions": [{"type": "shell", "command": "echo 1"}]}
    assert tc.put("/api/commands/danger", json=danger).status_code == 200
    assert tc.get("/api/commands").json()["commands"][0]["dangerous"] is True

    with tc.websocket_connect(f"/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        run_id = tc.post("/api/commands/danger/test").json()["run_id"]
        seen: list[dict[str, Any]] = []
        for _ in range(50):
            msg = ws.receive_json()
            seen.append(msg)
            if msg["type"] == "confirm_requested":
                ws.send_json(
                    {
                        "type": "confirm",
                        "run_id": msg["run_id"],
                        "step_id": msg["step_id"],
                        "answer": True,
                    }
                )
            if msg["type"] == "run_finished" and msg["run_id"] == run_id:
                break
    kinds = [m["type"] for m in seen]
    assert kinds[0] == "run_started"
    assert {"step_started", "confirm_requested", "step_finished", "run_finished"} <= set(kinds)
    asked = next(m for m in seen if m["type"] == "confirm_requested")
    assert asked["question"] == "Выполнить «Опасно»: CMD: echo 1?"
    assert asked["run_id"] == run_id
    assert seen[-1]["status"] == "done"
    assert ("shell", "echo 1", "cmd", 60.0) in backend.calls
    assert tc.post("/api/commands/missing/test").status_code == 404


def test_record_start_stop(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tc, aion, _ = client
    recorder = aion.commands.recorder
    real_start = recorder.start
    monkeypatch.setattr(recorder, "start", lambda: real_start(listen=False))  # no OS hooks

    class Key:
        char = "a"
        name = None

    assert tc.post("/api/commands/record/start").json() == {"ok": True}
    recorder.on_press(Key())
    assert tc.post("/api/commands/record/stop").json() == {
        "actions": [{"type": "keys", "text": "a"}]
    }


def test_history_search(client: Any) -> None:
    tc, aion, _ = client
    tc.post("/api/say", json={"text": "который час"})
    for _ in range(100):
        if len(tc.get("/api/history").json()) >= 2:
            break
        time.sleep(0.02)
    tc.portal.call(aion.diary.store.add, 1000.0, 2000.0, "Обсуждали поездку на море в августе")
    found = tc.get("/api/history/search?q=который").json()
    assert found[0]["kind"] == "message"
    assert found[0]["text"] == "который час"
    assert found[0]["role"] == "user"
    diary = tc.get("/api/history/search?q=поездка на море").json()
    assert any(f["kind"] == "episode" and "море" in f["text"] for f in diary)
    assert tc.get("/api/history/search?q=").json() == []


def test_unknown_confirm_is_ignored(client: Any, tmp_path: Path) -> None:
    tc, _, _ = client
    with tc.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "confirm", "run_id": "x", "step_id": "y", "answer": True})
        ws.send_json({"type": "text", "text": "который час"})
        for _ in range(30):
            if ws.receive_json()["type"] == "assistant_reply":
                break
