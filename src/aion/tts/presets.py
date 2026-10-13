"""Character voice presets: a licensed voice plus rate, pitch and effect under our own name.

The data lives in ``presets.yaml`` next to this file. A preset is applied by patching the
existing config fields (``profile.voice``), see :meth:`VoicePreset.patch`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PRESETS_FILE = Path(__file__).with_name("presets.yaml")


class VoicePreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str = ""
    engine: Literal["piper", "silero", "edge"]
    voice: str
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=0.0, ge=-12.0, le=12.0)
    effect: Literal["none", "metallic", "radio"] = "none"
    license: str = ""
    non_commercial: bool = False
    online: bool = False

    def voice_fields(self) -> dict[str, Any]:
        """The ``VoiceConfig`` fields this preset sets."""
        return {
            "engine": self.engine,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
            "effect": self.effect,
        }

    def patch(self, profile_id: str) -> dict[str, Any]:
        """The body for ``PATCH /api/config`` that makes this preset the voice of a profile."""
        return {"profiles": {profile_id: {"voice": self.voice_fields()}}}

    def matches(self, voice: Any) -> bool:
        """Whether a ``VoiceConfig`` currently equals this preset."""
        return (
            voice.engine == self.engine
            and voice.voice == self.voice
            and abs(voice.rate - self.rate) < 1e-6
            and abs(voice.pitch - self.pitch) < 1e-6
            and voice.effect == self.effect
        )


@lru_cache(maxsize=4)
def load_presets(path: Path = PRESETS_FILE) -> tuple[VoicePreset, ...]:
    """Read the presets; a broken or missing file yields an empty list, never an exception."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = data.get("presets", [])
        presets = tuple(VoicePreset.model_validate(item) for item in items)
    except (OSError, yaml.YAMLError, ValidationError, AttributeError, TypeError) as e:
        logger.warning("Пресеты голосов недоступны ({}): {}", path, e)
        return ()
    ids = [p.id for p in presets]
    if len(set(ids)) != len(ids):
        logger.warning("В пресетах голосов повторяются id: {}", ids)
        return ()
    return presets
