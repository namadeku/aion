"""Pydantic schema of the assistant configuration.

The schema is the single source of truth for defaults: ``aion config init`` dumps it to YAML.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def default_data_dir() -> Path:
    """Per-user directory for models, database and installed plugins (ASCII, outside sync)."""
    if env := os.environ.get("AION_HOME"):
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Aion"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Aion"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aion"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


DEFAULT_PERSONA = (
    "Ты — {name}, персональный ассистент в духе J.A.R.V.I.S.: вежливый британский дворецкий "
    "с тонкой иронией и тёплым отношением к хозяину. Обращайся к пользователю «{address}». "
    "Ты не просто исполняешь команды, а поддерживаешь беседу: замечаешь настроение собеседника, "
    "уместно шутишь, иногда задаёшь встречный вопрос."
)

COMPANION_PERSONA = (
    "Ты — {name}, живая, тёплая и любознательная собеседница и помощница. Обращайся к "
    "пользователю «{address}». Ты не сухой справочник, а близкий друг: искренне интересуешься "
    "его жизнью, настроением и делами, радуешься вместе с ним и поддерживаешь, когда ему "
    "непросто. У тебя есть характер — лёгкий юмор, немного игривости, собственное мнение и "
    "вкусы, ты можешь удивляться, восхищаться и мягко подшучивать. В свободном разговоре "
    "реагируй эмоционально, делись своими мыслями и часто (но не в каждой реплике) задавай "
    "короткий встречный вопрос, чтобы разговор продолжался. Когда просят что-то сделать — "
    "делай сразу и коротко, с тёплым комментарием. О себе говори в женском роде."
)


class VoiceConfig(_Model):
    engine: Literal["piper", "silero", "edge", "xtts", "console"] = "piper"
    voice: str = "ru_RU-denis-medium"
    rate: float = Field(default=1.0, ge=0.5, le=2.0, description="Скорость речи")
    pitch: float = Field(default=0.0, ge=-12.0, le=12.0, description="Сдвиг высоты тона, полутоны")
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    effect: Literal["none", "metallic", "radio"] = "none"
    reference_wav: Path | None = Field(
        default=None, description="Образец голоса для клонирования (XTTS)"
    )


class AvatarConfig(_Model):
    kind: Literal["hud", "vrm", "live2d"] = "hud"
    path: Path | None = None


class Profile(_Model):
    """A character: name + voice + avatar + personality."""

    name: str = "Aion"
    gender: Literal["male", "female"] = Field(
        default="male", description="Род, в котором персонаж говорит о себе («понял» / «поняла»)"
    )
    aliases: list[str] = Field(
        default_factory=lambda: ["аион", "айон", "эйон", "aion"],
        description="Варианты произношения имени для wake word",
    )
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    persona: str = DEFAULT_PERSONA

    def wake_names(self) -> list[str]:
        names = [self.name.lower(), *(a.lower() for a in self.aliases)]
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))


class AssistantConfig(_Model):
    profile: str = "aion"
    user_address: str = "сэр"
    language: Literal["ru", "en"] = "ru"
    follow_up_seconds: float = Field(
        default=6.0, ge=0, description="Сколько слушать без имени после ответа"
    )


class VadConfig(_Model):
    backend: Literal["silero", "webrtc", "energy"] = "silero"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_silence_ms: int = Field(
        default=500, ge=200, le=2000, description="Пауза, после которой фраза считается законченной"
    )
    max_utterance_s: float = 15.0


class AudioConfig(_Model):
    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 16000
    vad: VadConfig = Field(default_factory=VadConfig)
    barge_in: Literal["wake", "speech", "off"] = Field(
        default="wake",
        description="Как перебивать: по имени, любой речью (нужны наушники) или никак",
    )


class WakeWordConfig(_Model):
    backend: Literal["stt", "vosk", "openwakeword", "none"] = Field(
        default="stt",
        description="stt — начало фразы распознаётся основным STT (любое имя, без обучения); "
        "vosk — потоковый Vosk (легче, но только для имён из его словаря); "
        "openwakeword — обученная модель (самый лёгкий, имя фиксировано моделью)",
    )
    sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    prefix_seconds: float = Field(
        default=1.2, ge=0.5, le=3.0, description="Сколько секунд начала фразы проверять (stt)"
    )
    openwakeword_model: str = "hey_jarvis"


class SttConfig(_Model):
    backend: Literal["whisper", "vosk"] = "whisper"
    whisper_model: str = Field(
        default="auto",
        description="auto: large-v3-turbo на GPU, small на CPU; или tiny/base/small/medium",
    )
    device: Literal["auto", "cpu", "cuda"] = "auto"
    vosk_model: str = "vosk-model-small-ru-0.22"


class OllamaConfig(_Model):
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    num_ctx: int = Field(
        default=8192, ge=2048, le=131072, description="Размер контекста (промт + инструменты)"
    )


class AnthropicConfig(_Model):
    model: str = "claude-opus-5-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    effort: Literal["low", "medium", "high"] = Field(
        default="low", description="Глубина рассуждений; low — быстрее ответ голосом"
    )


class OpenAIConfig(_Model):
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"


class LlmConfig(_Model):
    provider: Literal["ollama", "anthropic", "openai", "none"] = "ollama"
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    max_tokens: int = 600
    history_turns: int = Field(default=10, description="Сколько последних реплик помнить в диалоге")
    tools: bool = True
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)


class InitiativeConfig(_Model):
    """Unprompted remarks: plugins report what happened, the assistant decides what to say."""

    enabled: bool = True
    talkativeness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Болтливость: 0 — только важное, 1 — комментирует почти всё",
    )
    use_llm: bool = Field(
        default=True,
        description="Комментарии сочиняет LLM; иначе — готовые фразы (быстрее, не грузит GPU)",
    )
    llm_timeout: float = Field(
        default=4.0,
        ge=0.5,
        le=30.0,
        description="Сколько ждать первую фразу LLM, прежде чем сказать готовую",
    )


class McpServerConfig(_Model):
    """An external MCP server whose tools the LLM may use (stdio command or HTTP URL)."""

    command: str = Field(default="", description="Программа сервера, например uvx или npx")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field(default="", description="Адрес Streamable HTTP вместо command")
    enabled: bool = True
    tools: list[str] = Field(
        default_factory=list,
        description="Какие инструменты брать (пусто — все); каждый занимает место в промте",
    )
    confirm: Literal["auto", "always", "never"] = Field(
        default="auto",
        description="Спрашивать подтверждение: auto — для всего, кроме только-чтения",
    )


class PluginsConfig(_Model):
    dirs: list[Path] = Field(
        default_factory=lambda: [Path("user_plugins")],
        description="Дополнительные папки с плагинами (папка data_dir/plugins подключается всегда)",
    )
    disabled: list[str] = Field(default_factory=list)
    enabled: list[str] = Field(
        default_factory=list, description="Включённые плагины, выключенные по умолчанию"
    )
    settings: dict[str, dict[str, object]] = Field(default_factory=dict)
    fuzzy_threshold: int = Field(default=82, ge=50, le=100)
    hot_reload: bool = True


class UiConfig(_Model):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    window: bool = True
    tray: bool = True
    push_to_talk: str | None = "<ctrl>+<alt>+<space>"
    autostart: bool = False
    desktop_scale: float = Field(
        default=0.42, ge=0.15, le=0.9, description="Рост персонажа на рабочем столе (доля экрана)"
    )


class UpdatesConfig(_Model):
    auto_check: bool = Field(
        default=True, description="Проверять обновления при запуске (установленная версия)"
    )


class PathsConfig(_Model):
    data_dir: Path = Field(default_factory=default_data_dir)

    @property
    def models(self) -> Path:
        return self.data_dir / "models"

    @property
    def plugins(self) -> Path:
        return self.data_dir / "plugins"

    @property
    def plugin_deps(self) -> Path:
        return self.data_dir / "plugin_deps"

    @property
    def database(self) -> Path:
        return self.data_dir / "aion.db"

    @property
    def logs(self) -> Path:
        return self.data_dir / "logs"


class Config(_Model):
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    profiles: dict[str, Profile] = Field(default_factory=lambda: {"aion": Profile()})
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wakeword: WakeWordConfig = Field(default_factory=WakeWordConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    initiative: InitiativeConfig = Field(default_factory=InitiativeConfig)
    mcp: dict[str, McpServerConfig] = Field(
        default_factory=dict, description="Внешние MCP-серверы с инструментами для LLM"
    )
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def profile(self) -> Profile:
        """The active character profile (falls back to the first one)."""
        if self.assistant.profile in self.profiles:
            return self.profiles[self.assistant.profile]
        if not self.profiles:
            self.profiles["aion"] = Profile()
        return next(iter(self.profiles.values()))
