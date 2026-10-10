"""Typed events that flow through the bus.

Every event has a snake_case ``type`` derived from its class name (``WakeDetected`` ->
``wake_detected``); the UI receives them over WebSocket as ``{"type": ..., **fields}``.
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ClassVar[str] = "event"
    ts: float = Field(default_factory=time.time)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.type = _snake(cls.__name__)

    def to_message(self) -> dict[str, Any]:
        return {"type": self.type, **self.model_dump(mode="json")}


Source = Literal["voice", "text", "ui", "plugin"]
State = Literal["idle", "listening", "thinking", "speaking"]


class StateChanged(Event):
    old: State
    new: State


class WakeDetected(Event):
    word: str
    score: float = 1.0


class SpeechStarted(Event):
    """VAD detected the beginning of user speech."""


class SpeechRecognized(Event):
    text: str
    source: Source = "voice"


class IntentMatched(Event):
    plugin: str
    command: str
    pattern: str
    score: float
    slots: dict[str, str] = Field(default_factory=dict)


class AssistantReply(Event):
    """Text the assistant says (or streams). ``final`` marks the end of a reply."""

    text: str
    final: bool = True


class TtsStarted(Event):
    text: str


class TtsFinished(Event):
    text: str
    interrupted: bool = False


class AudioLevel(Event):
    """Output/input loudness for visualizers and lip sync (0..1)."""

    level: float
    channel: Literal["input", "output"] = "output"


class EmotionChanged(Event):
    emotion: Literal["neutral", "joy", "thinking", "surprise", "sad", "angry"]
    intensity: float = 1.0


class MoodChanged(Event):
    """The character's mood (0..1 each), for the avatar's idle behaviour."""

    energy: float
    affection: float
    boredom: float


class Notification(Event):
    title: str
    text: str = ""
    level: Literal["info", "success", "warning", "error"] = "info"


class ToolCalled(Event):
    plugin: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class PluginStatusChanged(Event):
    plugin: str
    status: Literal["loaded", "unloaded", "error", "disabled"]
    error: str | None = None


class ConfigChanged(Event):
    sections: list[str]


class BargeIn(Event):
    reason: Literal["wake", "speech", "text", "hotkey"]


class LogRecord(Event):
    level: str
    message: str
    module: str = ""
