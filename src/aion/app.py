"""Application container: wires config, bus, state, dialog, plugins and I/O together."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING

from loguru import logger

from aion.config import Config, ConfigStore
from aion.core import DialogManager, EventBus, Router, SpeechOutput, StateMachine
from aion.core.events import ConfigChanged, WakeDetected
from aion.core.initiative import Initiative
from aion.core.mood import MoodService
from aion.core.speech import ConsoleOutput
from aion.core.transcript import TranscriptLog
from aion.llm.episodes import Diary
from aion.llm.mcp_hub import McpHub
from aion.plugins.manager import PluginManager
from aion.storage import Database

if TYPE_CHECKING:
    from aion.audio.pipeline import VoicePipeline
    from aion.llm.base import LlmProvider
    from aion.llm.brain import Brain
    from aion.models import Progress

SpeechFactory = Callable[[EventBus, StateMachine, Config], SpeechOutput]


def console_speech(bus: EventBus, state: StateMachine, config: Config) -> SpeechOutput:
    return ConsoleOutput(bus, state, name=config.profile.name)


class Aion:
    def __init__(
        self,
        store: ConfigStore,
        speech: SpeechFactory = console_speech,
        *,
        watch_plugins: bool = True,
    ) -> None:
        self.store = store
        self.bus = EventBus()
        self.state = StateMachine(self.bus)
        self.router = Router(fuzzy_threshold=store.config.plugins.fuzzy_threshold)
        self.speech = speech(self.bus, self.state, store.config)
        self.dialog = DialogManager(self.bus, self.state, self.speech, self.router, store)
        self.db = Database(store.config.paths.database)
        self.plugins = PluginManager(self)
        self.initiative = Initiative(self)
        self.mood = MoodService(self)
        self.diary = Diary(self)
        self.mcp = McpHub(self)
        self.transcript = TranscriptLog(self.bus, store.config.profile.name)
        self._warm_up_task: asyncio.Task[None] | None = None
        self.llm: LlmProvider | None = None
        self.brain: Brain | None = None
        self.voice: VoicePipeline | None = None  # set in voice mode
        self.progress: Progress | None = None  # model downloads, shown by the UI
        self._watch_plugins = watch_plugins
        self._unsubscribe = store.subscribe(self._on_config_changed)
        self.dialog.interceptors.append(self.plugins.intercept)
        self.bus.subscribe(WakeDetected, self._on_wake)

    @property
    def config(self) -> Config:
        return self.store.config

    async def start(self) -> None:
        self.config.paths.data_dir.mkdir(parents=True, exist_ok=True)
        await self.db.open()
        await self.mood.start()
        await self.speech.start()
        self._setup_llm()
        await self.plugins.load_all()
        self._warm_up_llm()  # after plugins: the prefill includes their tools
        self.initiative.start()
        self.diary.start()
        await self.mcp.sync()
        if self._watch_plugins:
            self.plugins.start_watching()
        logger.info("{} запущен", self.config.profile.name)

    async def stop(self) -> None:
        if self.voice is not None:
            await self.voice.stop()
            self.voice = None
        await self.initiative.stop()
        await self.mcp.stop()
        await self.dialog.interrupt()
        await self.plugins.shutdown()
        await self.diary.stop()  # the last conversation goes into the diary
        await self.speech.close()
        await self.mood.stop()
        await self.bus.drain()
        if self.llm is not None:
            await self.llm.aclose()
        await self.db.close()
        self._unsubscribe()
        logger.info("{} остановлен", self.config.profile.name)

    async def __aenter__(self) -> Aion:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int | None = None
    ) -> str:
        """One-shot LLM completion for plugins."""
        if self.llm is None:
            raise RuntimeError("Языковая модель не настроена")
        result: str = await self.llm.complete(prompt, system=system, max_tokens=max_tokens)
        return result

    def _setup_llm(self) -> None:
        from aion.llm.base import LlmError
        from aion.llm.brain import Brain, create_provider

        try:
            self.llm = create_provider(self.config)
        except LlmError as e:
            logger.warning("LLM не подключена: {}", e)
            self.llm = None
        if self.llm is None:
            self.brain = None
            self.dialog.fallback = None
            return
        from aion.llm.ollama import OllamaProvider

        if isinstance(self.llm, OllamaProvider):
            self.llm.progress = self.progress  # the first start pulls the model
        self.brain = Brain(self, self.llm)
        self.dialog.fallback = self.brain.respond
        logger.info("LLM: {} ({})", self.config.llm.provider, getattr(self.llm, "model", ""))

    async def reload_llm(self) -> None:
        old = self.llm
        history = self.brain.memory if self.brain else None
        if self.brain is not None:
            self.brain.close()
        self._setup_llm()
        if self.brain is not None and history is not None:
            self.brain.memory = history
        self._warm_up_llm()
        if old is not None:
            from aion.llm.ollama import OllamaProvider

            # Without this a switched-away Ollama model keeps its VRAM for KEEP_ALIVE.
            if isinstance(old, OllamaProvider) and not old.serves_same_model(self.llm):
                await old.unload()
            await old.aclose()

    def _warm_up_llm(self) -> None:
        """Start Ollama, load the model and pre-read the prompt without blocking startup."""
        if self.brain is not None:
            task = asyncio.get_running_loop().create_task(self.brain.warm_up())
            task.add_done_callback(_log_warm_up)

    async def _on_wake(self, _: WakeDetected) -> None:
        await self.plugins.broadcast("on_wake")

    def _on_config_changed(self, config: Config, sections: set[str]) -> None:
        self.router.fuzzy_threshold = config.plugins.fuzzy_threshold
        self.transcript.name = config.profile.name
        if isinstance(self.speech, ConsoleOutput):
            self.speech.name = config.profile.name
        self.bus.emit(ConfigChanged(sections=sorted(sections)))
        if "llm" in sections:
            asyncio.get_running_loop().create_task(self.reload_llm())
        if "mcp" in sections:
            asyncio.get_running_loop().create_task(self.mcp.sync())

    def prewarm_llm(self) -> None:
        """Load the main model and its prompt again (e.g. after another model used the GPU)."""
        self._warm_up_llm()

    def tools_changed(self) -> None:
        """The tool list changed (an MCP server came or went): re-read the prompt in advance."""
        if self._warm_up_task is not None:
            self._warm_up_task.cancel()
        self._warm_up_task = asyncio.get_running_loop().create_task(self._warm_up_later())

    async def _warm_up_later(self) -> None:
        await asyncio.sleep(2)  # several servers connect at once: warm up once
        self._warm_up_llm()


def _log_warm_up(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.warning("LLM пока недоступна: {}", exc)
