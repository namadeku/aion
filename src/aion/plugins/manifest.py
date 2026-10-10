"""``plugin.yaml`` schema."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MANIFEST_FILE = "plugin.yaml"

Permission = Literal[
    "network",  # HTTP requests
    "system",  # volume, power, lock screen, processes
    "shell",  # running arbitrary commands
    "files",  # reading/writing user files
    "clipboard",
    "notifications",
]
DANGEROUS_PERMISSIONS: frozenset[str] = frozenset({"system", "shell", "files"})


class SettingSpec(BaseModel):
    """One user-editable plugin setting; the UI renders a form field for it."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "text", "int", "float", "bool", "enum", "secret"] = "string"
    title: str = ""
    description: str = ""
    default: Any = None
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None
    env: str | None = Field(default=None, description="Переменная окружения для значения")

    @model_validator(mode="after")
    def _check_enum(self) -> SettingSpec:
        if self.type == "enum" and not self.options:
            raise ValueError("для type: enum нужен список options")
        return self

    def coerce(self, value: Any) -> Any:
        if value is None:
            return None
        match self.type:
            case "int":
                return int(value)
            case "float":
                return float(value)
            case "bool":
                if isinstance(value, str):
                    return value.strip().lower() in {"1", "true", "yes", "да", "on"}
                return bool(value)
            case "enum":
                if value not in (self.options or []):
                    raise ValueError(f"{value!r} не входит в {self.options}")
                return value
            case _:
                return str(value)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = ""
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    entry: str = "main.py"
    class_name: str | None = Field(default=None, alias="class")
    dependencies: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    settings: dict[str, SettingSpec] = Field(default_factory=dict)
    enabled_by_default: bool = True

    @property
    def display_name(self) -> str:
        return self.title or self.name

    @property
    def dangerous_permissions(self) -> list[str]:
        return [p for p in self.permissions if p in DANGEROUS_PERMISSIONS]

    def resolve_settings(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Defaults <- environment variables <- user overrides, coerced to declared types."""
        values: dict[str, Any] = {}
        overrides = overrides or {}
        for key, spec in self.settings.items():
            value = spec.default
            if spec.env and os.environ.get(spec.env):
                value = os.environ[spec.env]
            if key in overrides and overrides[key] not in (None, ""):
                value = overrides[key]
            try:
                values[key] = spec.coerce(value)
            except (TypeError, ValueError):
                values[key] = spec.default
        return values


class ManifestError(Exception):
    pass


def load_manifest(plugin_dir: Path) -> PluginManifest:
    path = plugin_dir / MANIFEST_FILE
    if not path.exists():
        raise ManifestError(f"{path}: нет файла {MANIFEST_FILE}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return PluginManifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ManifestError(f"{path}: {e}") from e
