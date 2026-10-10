"""Base class for plugins and the per-command context."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from aion.core.initiative import Observation

if TYPE_CHECKING:
    from loguru import Logger

    from aion.app import Aion
    from aion.core.dialog import Turn
    from aion.plugins.manifest import PluginManifest
    from aion.storage import KeyValueStore


class LLMAccess:
    """What a plugin may do with the language model."""

    def __init__(self, host: Aion) -> None:
        self._host = host

    @property
    def available(self) -> bool:
        return self._host.llm is not None

    async def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int | None = None
    ) -> str:
        """One-shot completion without dialog history or tools."""
        return await self._host.complete(prompt, system=system, max_tokens=max_tokens)


class Plugin:
    """Subclass this in ``main.py`` of your plugin.

    Lifecycle hooks (all optional, all async): :meth:`on_load`, :meth:`on_unload`,
    :meth:`on_wake`, :meth:`on_utterance`, :meth:`on_shutdown`, :meth:`on_settings_changed`.
    """

    manifest: PluginManifest
    path: Path
    _host: Aion
    _tasks: set[asyncio.Task[Any]]

    # -- wiring (done by the plugin manager) --------------------------------------------

    def _attach(self, host: Aion, manifest: PluginManifest, path: Path) -> None:
        self._host = host
        self.manifest = manifest
        self.path = path
        self._tasks = set()

    # -- properties -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def config(self) -> dict[str, Any]:
        """Plugin settings: declared defaults merged with the user's values."""
        overrides = self._host.config.plugins.settings.get(self.name, {})
        return self.manifest.resolve_settings(overrides)

    @property
    def storage(self) -> KeyValueStore:
        return self._host.db.kv(f"plugin:{self.name}")

    @property
    def log(self) -> Logger:
        return logger.bind(plugin=self.name)

    @property
    def llm(self) -> LLMAccess:
        return LLMAccess(self._host)

    @property
    def assistant_name(self) -> str:
        return self._host.config.profile.name

    @property
    def user_address(self) -> str:
        return self._host.config.assistant.user_address

    def g(self, masculine: str, feminine: str) -> str:
        """Pick the word form for the character's gender: ``self.g("Записал", "Записала")``."""
        return feminine if self._host.config.profile.gender == "female" else masculine

    @property
    def host(self) -> Aion:
        """Full application object — for advanced plugins; prefer the helpers above."""
        return self._host

    # -- actions outside a dialog turn (timers, background events) -----------------------

    async def say(self, text: str, *, emotion: str | None = None, wait: bool = True) -> None:
        await self._host.dialog.say(text, emotion=emotion, wait=wait)

    async def notify(self, title: str, text: str = "", level: str = "info") -> None:
        await self._host.dialog.notify(title, text, level)

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        await self._host.dialog.set_emotion(emotion, intensity)

    def react(self, what: str | Observation, **fields: Any) -> bool:
        """Report something worth a remark; the assistant decides whether and how to say it.

        ``self.react("Пользователь сделал эйс", importance=1.0, quick=["Эйс!"])`` — see
        :class:`~aion.core.initiative.Observation` for the fields. Unlike :meth:`say`, it does
        not interrupt anyone, respects the talkativeness setting and lets the LLM phrase the
        remark in character. Returns False if the observation was dropped right away.
        """
        obs = what if isinstance(what, Observation) else Observation(what, **fields)
        obs.source = self.name
        return self._host.initiative.submit(obs)

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Start a background task that is cancelled automatically when the plugin unloads."""
        task = asyncio.get_running_loop().create_task(coro, name=name or f"plugin:{self.name}")
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            self.log.opt(exception=exc).error("Фоновая задача плагина упала")

    async def _cancel_tasks(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- lifecycle hooks --------------------------------------------------------------------

    async def on_load(self) -> None:
        """Called after the plugin is loaded (and after every hot reload)."""

    async def on_unload(self) -> None:
        """Called before the plugin is unloaded or reloaded."""

    async def on_wake(self) -> None:
        """Called when the wake word is detected."""

    async def on_utterance(self, ctx: Context) -> bool:
        """See every phrase before routing. Return True to consume it."""
        return False

    async def on_settings_changed(self) -> None:
        """Called after the user changed this plugin's settings."""

    async def on_shutdown(self) -> None:
        """Called once when the assistant exits (before ``on_unload``)."""


class Context:
    """Passed to command handlers (and tools that declare a ``ctx`` parameter)."""

    def __init__(self, turn: Turn, plugin: Plugin) -> None:
        self.turn = turn
        self.plugin = plugin

    @property
    def text(self) -> str:
        """The original user phrase."""
        return self.turn.text

    @property
    def source(self) -> str:
        return self.turn.source

    @property
    def config(self) -> dict[str, Any]:
        return self.plugin.config

    @property
    def storage(self) -> KeyValueStore:
        return self.plugin.storage

    @property
    def llm(self) -> LLMAccess:
        return self.plugin.llm

    @property
    def log(self) -> Logger:
        return self.plugin.log

    @property
    def user_address(self) -> str:
        return self.plugin.user_address

    @property
    def assistant_name(self) -> str:
        return self.plugin.assistant_name

    def g(self, masculine: str, feminine: str) -> str:
        """Word form for the character's gender: ``ctx.g("Понял", "Поняла")``."""
        return self.plugin.g(masculine, feminine)

    async def say(self, text: str, *, emotion: str | None = None, wait: bool = True) -> None:
        await self.turn.say(text, emotion=emotion, wait=wait)

    async def ask(self, question: str, timeout: float | None = None) -> str | None:
        """Ask a clarifying question; returns the answer or None on timeout."""
        return await self.turn.ask(question, timeout)

    async def confirm(self, question: str, timeout: float | None = None) -> bool:
        return await self.turn.confirm(question, timeout)

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        await self.plugin.set_emotion(emotion, intensity)

    async def notify(self, title: str, text: str = "", level: str = "info") -> None:
        await self.plugin.notify(title, text, level)
