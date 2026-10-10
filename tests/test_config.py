from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aion.config import Config, ConfigStore, dump_yaml


def test_defaults_roundtrip() -> None:
    config = Config()
    assert config.profile.name == "Aion"
    assert "аион" in config.profile.wake_names()
    restored = Config.model_validate(yaml.safe_load(dump_yaml(config)))
    assert restored == config


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"assistant": {"nmae": "typo"}})


def test_update_persists_and_notifies(store: ConfigStore, tmp_path: Path) -> None:
    seen: list[set[str]] = []
    store.subscribe(lambda _cfg, sections: seen.append(sections))

    store.update({"profiles": {"aion": {"name": "Пятница"}}})

    assert store.config.profile.name == "Пятница"
    assert seen == [{"profiles", "profile"}]
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert saved["profiles"]["aion"]["name"] == "Пятница"
    reloaded = ConfigStore.load(tmp_path / "config.yaml")
    assert reloaded.config.profile.name == "Пятница"


def test_invalid_update_keeps_old_config(store: ConfigStore) -> None:
    with pytest.raises(ValidationError):
        store.update({"llm": {"temperature": 99}})
    assert store.config.llm.temperature == 0.6


def test_default_hotkey_is_valid_pynput_syntax() -> None:
    keyboard = pytest.importorskip("pynput.keyboard")
    hotkey = Config().ui.push_to_talk
    assert hotkey is not None
    assert len(keyboard.HotKey.parse(hotkey)) == 3


def test_icon_is_generated(tmp_path: Path) -> None:
    from aion.ui.desktop import ensure_icon

    icon = ensure_icon(tmp_path / "aion.ico")
    assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00"  # ICO header
