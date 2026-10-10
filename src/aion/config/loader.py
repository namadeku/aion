"""Loading, saving and live-updating the YAML config."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger

from aion.config.schema import Config, default_data_dir

ChangeListener = Callable[[Config, set[str]], None]


def find_config_path(explicit: Path | None = None) -> Path:
    """Resolve the config file: explicit > $AION_CONFIG > ./config.yaml > data_dir/config.yaml."""
    if explicit:
        return explicit
    if env := os.environ.get("AION_CONFIG"):
        return Path(env)
    local = Path("config.yaml")
    if local.exists():
        return local
    return default_data_dir() / "config.yaml"


def secrets_path() -> Path:
    """API keys live here (outside the repository), loaded into the environment at start."""
    return default_data_dir() / ".env"


def set_secret(name: str, value: str) -> None:
    """Store an API key in the user's .env and the current environment (never logged)."""
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{name}=")]
    if value:
        lines.append(f"{name}={value}")
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)  # pyright: ignore[reportUnknownArgumentType]
        else:
            out[key] = value
    return out


def dump_yaml(config: Config) -> str:
    data = config.model_dump(mode="json")
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


class ConfigStore:
    """Holds the live config, persists it and notifies listeners about changed sections."""

    def __init__(self, config: Config, path: Path | None = None) -> None:
        self._config = config
        self.path = path
        self._listeners: list[ChangeListener] = []

    @classmethod
    def load(cls, path: Path | None = None) -> ConfigStore:
        load_dotenv()
        load_dotenv(secrets_path())
        resolved = find_config_path(path)
        raw: dict[str, Any] = {}
        if resolved.exists():
            loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                raise ValueError(f"{resolved}: корень конфига должен быть словарём")
            raw = loaded or {}
            logger.info("Конфиг загружен: {}", resolved)
        else:
            logger.info("Конфиг {} не найден — используются значения по умолчанию", resolved)
        if port := os.environ.get("AION_UI_PORT"):
            raw.setdefault("ui", {})["port"] = int(port)
        return cls(Config.model_validate(raw), resolved)

    @property
    def config(self) -> Config:
        return self._config

    def subscribe(self, listener: ChangeListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def update(self, patch: dict[str, Any], *, save: bool = True) -> Config:
        """Apply a partial update (deep merge), validate, persist and notify listeners."""
        return self.replace(deep_merge(self._config.model_dump(mode="json"), patch), save=save)

    def replace(self, data: dict[str, Any], *, save: bool = True) -> Config:
        """Replace the whole config (needed to delete keys, e.g. a profile)."""
        current = self._config.model_dump(mode="json")
        new = Config.model_validate(data)
        dumped = new.model_dump(mode="json")
        changed = {key for key in dumped if dumped[key] != current.get(key)}
        changed |= self._profile_change(self._config, new)
        self._config = new
        if save:
            self.save()
        for listener in list(self._listeners):
            try:
                listener(new, changed)
            except Exception:
                logger.exception("Ошибка в обработчике изменения конфига")
        return new

    @staticmethod
    def _profile_change(old: Config, new: Config) -> set[str]:
        return {"profile"} if old.profile != new.profile else set()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dump_yaml(self._config), encoding="utf-8")
