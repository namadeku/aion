"""Application container: wires config, bus, state, dialog, plugins and I/O together."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from types import TracebackType
from typing import TYPE_CHECKING, Any

from loguru import logger

from aion.commands import CommandEngine
from aion.config import Config, ConfigStore
from aion.core import DialogManager, EventBus, Router, SpeechOutput, StateMachine
from aion.core.errors import user_message
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
        defer_slow_plugins: bool = False,
    ) -> None:
        self.store = store
        self.bus = EventBus()
        self.state = StateMachine(self.bus)
        self.router = Router(fuzzy_threshold=store.config.plugins.fuzzy_threshold)
        self.speech = speech(self.bus, self.state, store.config)
        self.dialog = DialogManager(self.bus, self.state, self.speech, self.router, store)
        self.db = Database(store.config.paths.database)
        self.plugins = PluginManager(self)
        self.plugins.defer_slow = defer_slow_plugins
        self.initiative = Initiative(self)
        self.mood = MoodService(self)
        self.diary = Diary(self)
        self.mcp = McpHub(self)
        self.commands = CommandEngine(self)  # user commands and scenarios (YAML)
        self.transcript = TranscriptLog(self.bus, store.config.profile.name)
        self._warm_up_task: asyncio.Task[None] | None = None  # debounce of tools_changed
        self._llm_warm_task: asyncio.Task[None] | None = None
        self._reload_lock = asyncio.Lock()  # config edits in a row must not reload concurrently
        # The loop keeps only weak references to tasks: hold the fire-and-forget ones here
        # (and cancel them on stop) so they are neither collected mid-run nor leaked.
        self._background: set[asyncio.Task[Any]] = set()
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
        try:
            self.config.paths.data_dir.mkdir(parents=True, exist_ok=True)
            await self.db.open()
        except Exception as e:
            logger.exception("Не удалось открыть базу данных {}", self.config.paths.database)
            raise RuntimeError(user_message(e)) from e
        await self.mood.start()
        await self.speech.start()
        self._setup_llm()
        await self.plugins.load_all()
        await self.commands.start(watch=self._watch_plugins)
        self._warm_up_llm()  # after plugins: the prefill includes their tools
        self.initiative.start()
        self.diary.start()
        await self.mcp.sync()
        if self._watch_plugins:
            self.plugins.start_watching()
        logger.info("{} запущен", self.config.profile.name)

    async def stop(self) -> None:
        """Shut everything down; a failing step is logged and does not skip the rest."""
        await self._cancel_background()
        voice, self.voice = self.voice, None
        if voice is not None:
            await _guarded("голос", voice.stop())
        await _guarded("инициатива", self.initiative.stop())
        await _guarded("MCP", self.mcp.stop())
        await _guarded("диалог", self.dialog.interrupt())
        await _guarded("команды", self.commands.stop())
        await _guarded("плагины", self.plugins.shutdown())
        await _guarded("дневник", self.diary.stop())  # the last conversation goes into the diary
        await _guarded("речь", self.speech.close())
        await _guarded("настроение", self.mood.stop())
        await _guarded("шина", self.bus.drain())
        if self.llm is not None:
            await _guarded("LLM", self.llm.aclose())
        await _guarded("база данных", self.db.close())
        self.transcript.close()
        self._unsubscribe()
        logger.info("{} остановлен", self.config.profile.name)

    def spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        """Run ``coro`` in the background; it is cancelled when the assistant stops."""
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _cancel_background(self) -> None:
        tasks = [*self._background]
        if self._warm_up_task is not None:
            tasks.append(self._warm_up_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        async with self._reload_lock:
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
        if self._llm_warm_task is not None:
            self._llm_warm_task.cancel()  # a stale warm-up of the previous provider/prompt
            self._llm_warm_task = None
        if self.brain is not None:
            task = self.spawn(self.brain.warm_up(), "llm-warm-up")
            task.add_done_callback(_log_warm_up)
            self._llm_warm_task = task

    async def _on_wake(self, _: WakeDetected) -> None:
        await self.plugins.broadcast("on_wake")

    def _on_config_changed(self, config: Config, sections: set[str]) -> None:
        self.router.fuzzy_threshold = config.plugins.fuzzy_threshold
        self.transcript.name = config.profile.name
        if isinstance(self.speech, ConsoleOutput):
            self.speech.name = config.profile.name
        self.bus.emit(ConfigChanged(sections=sorted(sections)))
        if "llm" in sections:
            self.spawn(_logged(self.reload_llm()), "llm-reload")
        if "mcp" in sections:
            self.spawn(_logged(self.mcp.sync()), "mcp-sync")

    def prewarm_llm(self) -> None:
        """Load the main model and its prompt again (e.g. after another model used the GPU)."""
        self._warm_up_llm()

    def tools_changed(self) -> None:
        """The tool list changed (an MCP server came or went): re-read the prompt in advance."""
        if self._warm_up_task is not None:
            self._warm_up_task.cancel()
        self._warm_up_task = asyncio.get_running_loop().create_task(
            self._warm_up_later(), name="llm-warm-up-later"
        )

    async def _warm_up_later(self) -> None:
        await asyncio.sleep(2)  # several servers connect at once: warm up once
        self._warm_up_llm()


def _log_warm_up(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.warning("LLM пока недоступна: {}", user_message(exc))
        logger.opt(exception=exc).debug("Подробности прогрева LLM")


async def _logged(coro: Awaitable[None]) -> None:
    """Await a background job; its failure goes to the log instead of "never retrieved"."""
    try:
        await coro
    except Exception:
        logger.exception("Фоновая задача завершилась ошибкой")


async def _guarded(what: str, coro: Awaitable[object]) -> None:
    try:
        await coro
    except Exception:
        logger.exception("Ошибка при остановке ({})", what)
