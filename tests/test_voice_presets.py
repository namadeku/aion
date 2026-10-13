"""Character voice presets: data integrity, licences and the REST endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aion.app import Aion
from aion.config import ConfigStore
from aion.config.schema import VoiceConfig
from aion.core import NullOutput
from aion.tts.piper_engine import KNOWN_VOICES
from aion.tts.presets import VoicePreset, load_presets
from aion.ui.server import UiServer

TOKEN = "test-token"
# Piper voices whose MODEL_CARD forbids commercial use or states no licence at all
BLOCKED_PIPER = {"ru_RU-ruslan-medium", "ru_RU-irina-medium"}
# trademarks / personalities that must never be part of a preset name
FORBIDDEN_WORDS = {"джарвис", "jarvis", "пятница", "friday", "алиса", "siri", "alexa", "hal"}


@pytest.fixture
def client(app_store: ConfigStore, tmp_path: Path) -> Iterator[tuple[TestClient, Aion]]:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    server = UiServer(aion, token=TOKEN)
    with TestClient(server.api) as tc:
        tc.portal.call(aion.start)  # pyright: ignore[reportOptionalMemberAccess]
        tc.headers["X-Aion-Token"] = TOKEN
        yield tc, aion
        tc.portal.call(aion.stop)  # pyright: ignore[reportOptionalMemberAccess]


def test_presets_load_and_are_unique() -> None:
    presets = load_presets()
    assert len(presets) >= 5
    assert len({p.id for p in presets}) == len(presets)
    assert len({p.title for p in presets}) == len(presets)
    assert {"Дворецкий", "Пилот", "Робот", "Рассказчик"} <= {p.title for p in presets}


def test_presets_fit_the_voice_config() -> None:
    for preset in load_presets():
        voice = VoiceConfig.model_validate(preset.voice_fields())
        assert preset.matches(voice)


def test_only_redistributable_piper_voices() -> None:
    piper = [p for p in load_presets() if p.engine == "piper"]
    assert piper
    known = {vid for vid, _, _ in KNOWN_VOICES["ru"]}
    for preset in piper:
        assert preset.voice not in BLOCKED_PIPER
        assert preset.voice in known
        assert preset.license == "CC0"
        assert not preset.non_commercial


def test_licence_flags_match_the_engine() -> None:
    for preset in load_presets():
        assert preset.license, preset.id
        assert preset.non_commercial == (preset.engine == "silero"), preset.id
        assert preset.online == (preset.engine == "edge"), preset.id


def test_names_are_our_own() -> None:
    for preset in load_presets():
        text = f"{preset.title} {preset.description} {preset.id}".lower()
        assert not any(word in text.split() or word in preset.id for word in FORBIDDEN_WORDS)


def test_broken_file_gives_empty_list(tmp_path: Path) -> None:
    bad = tmp_path / "presets.yaml"
    bad.write_text("presets: [{id: x}]", encoding="utf-8")
    assert load_presets(bad) == ()
    assert load_presets(tmp_path / "missing.yaml") == ()
    dup = tmp_path / "dup.yaml"
    item = "{id: a, title: A, engine: piper, voice: v}"
    dup.write_text(f"presets: [{item}, {item}]", encoding="utf-8")
    assert load_presets(dup) == ()


def test_preset_rejects_unknown_fields_and_ranges() -> None:
    with pytest.raises(ValueError, match="validation error"):
        VoicePreset.model_validate(
            {"id": "a", "title": "A", "engine": "piper", "voice": "v", "pitch": 50}
        )
    with pytest.raises(ValueError, match="validation error"):
        VoicePreset.model_validate(
            {"id": "a", "title": "A", "engine": "piper", "voice": "v", "emotion": "joy"}
        )


def test_endpoint_lists_and_marks_current(client: Any) -> None:
    tc, aion = client
    r = tc.get("/api/voice/presets")
    assert r.status_code == 200
    items = {p["id"]: p for p in r.json()}
    assert items["butler"]["title"] == "Дворецкий"
    assert items["butler"]["installed"] is False  # nothing downloaded in the test data dir
    assert items["announcer"]["installed"] is None  # online
    assert items["captain"]["non_commercial"] is True
    assert not any(p["current"] for p in items.values())

    r = tc.patch("/api/config", json=items["robot"]["patch"])
    assert r.status_code == 200
    voice = aion.config.profile.voice
    assert (voice.voice, voice.effect) == ("ru_RU-denis-medium", "metallic")
    after = {p["id"]: p for p in tc.get("/api/voice/presets").json()}
    assert [i for i, p in after.items() if p["current"]] == ["robot"]


def test_endpoint_requires_token(client: Any) -> None:
    tc, _ = client
    assert tc.get("/api/voice/presets", headers={"X-Aion-Token": "wrong"}).status_code == 401
