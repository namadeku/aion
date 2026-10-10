from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from aion.app import Aion
from aion.config import Config, ConfigStore
from aion.config.schema import LlmConfig, PathsConfig, PluginsConfig
from aion.core import DialogManager, EventBus, NullOutput, Router, StateMachine


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    config = Config(paths=PathsConfig(data_dir=tmp_path / "data"))
    return ConfigStore(config, tmp_path / "config.yaml")


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def state(bus: EventBus) -> StateMachine:
    return StateMachine(bus)


@pytest.fixture
async def speech(bus: EventBus, state: StateMachine) -> AsyncIterator[NullOutput]:
    out = NullOutput(bus, state)
    await out.start()
    yield out
    await out.close()


@pytest.fixture
def plugins_dir(tmp_path: Path) -> Path:
    path = tmp_path / "user_plugins"
    path.mkdir()
    return path


@pytest.fixture
def app_store(tmp_path: Path, plugins_dir: Path) -> ConfigStore:
    config = Config(
        paths=PathsConfig(data_dir=tmp_path / "data"),
        plugins=PluginsConfig(dirs=[plugins_dir]),
        llm=LlmConfig(provider="none"),
    )
    return ConfigStore(config, tmp_path / "config.yaml")


@pytest.fixture
async def app(app_store: ConfigStore) -> AsyncIterator[Aion]:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    aion.dialog.ask_timeout = 1.0
    await aion.start()
    yield aion
    await aion.stop()


@pytest.fixture
def dialog(
    bus: EventBus, state: StateMachine, speech: NullOutput, store: ConfigStore
) -> DialogManager:
    d = DialogManager(bus, state, speech, Router(), store)
    d.ask_timeout = 1.0
    return d
