"""The command engine: registers user commands in the router and runs their actions.

Triggers become commands of a virtual ``commands`` plugin in the router (group "commands"),
so matching is the router's. Each run reports its steps to the work log; dangerous steps
wait for a "yes" by voice or from the UI, whichever comes first.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import TypeAdapter

from aion.commands.keys import parse_combo
from aion.commands.models import (
    Action,
    Command,
    DeleteAction,
    DictateAction,
    FileAction,
    KeysAction,
    MinimizeAllAction,
    MouseAction,
    OpenAction,
    PlayAction,
    PowerAction,
    RunAction,
    SayAction,
    SearchAction,
    ShellAction,
    VolumeAction,
    WaitAction,
    WindowAction,
)
from aion.commands.platform import ActionError, Backend, DesktopBackend, expand_path
from aion.commands.recorder import MacroRecorder
from aion.commands.shell import ShellTimeout
from aion.commands.store import CommandStore, Entry
from aion.core.errors import user_message
from aion.core.events import (
    ConfigChanged,
    ConfirmRequested,
    StepFinished,
    StepStarted,
    StepStatus,
)
from aion.core.router import CommandSpec

if TYPE_CHECKING:
    from aion.app import Aion
    from aion.core.dialog import Turn

PLUGIN = "commands"
#: above every builtin command: on an equal score the user's command wins (the router ranks
#: by score first, so an exact builtin phrase still beats a fuzzy user match)
USER_PRIORITY = 50
MAX_NESTING = 5
DETAIL_CHARS = 2000
_SLOT = re.compile(r"\{(\w+)\}")
_ACTION = TypeAdapter[Action](Action)


class _Declined(Exception):
    """The user said "no" (or nothing) to a confirmation: the run stops quietly."""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _tail(text: str, limit: int = DETAIL_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


@dataclass
class _Run:
    turn: Turn
    slots: dict[str, str] = field(default_factory=dict)
    stack: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.turn.run_id


class CommandEngine:
    def __init__(
        self, app: Aion, *, backend: Backend | None = None, store: CommandStore | None = None
    ) -> None:
        self.app = app
        self.store = store or CommandStore(app.config.paths.data_dir / "commands")
        self.backend: Backend = backend or DesktopBackend(app)
        self.recorder = MacroRecorder()
        self._pending: dict[tuple[str, str], asyncio.Future[bool]] = {}
        self._watch_task: asyncio.Task[None] | None = None
        self._stop_watch = asyncio.Event()
        self._unsubscribe = app.bus.subscribe(ConfigChanged, self._on_config)

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self, *, watch: bool = True) -> None:
        try:
            await asyncio.to_thread(self.store.ensure)
        except OSError:
            logger.exception("Не удалось создать папку команд {}", self.store.root)
        self.reload()
        if watch and self.app.config.plugins.hot_reload and self._watch_task is None:
            self._stop_watch.clear()
            self._watch_task = asyncio.create_task(self._watch(), name="commands-watch")

    async def stop(self) -> None:
        self._unsubscribe()
        self.recorder.stop_listening()
        if self._watch_task is not None:
            self._stop_watch.set()
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
            self._watch_task = None
        for future in self._pending.values():
            future.cancel()
        self._pending.clear()

    def reload(self) -> None:
        """Re-read the folder and register the triggers again."""
        self.store.load()
        self.register()

    def register(self) -> None:
        specs: list[CommandSpec] = []
        if self.app.config.commands.enabled:
            for entry in self.store.entries.values():
                triggers = self.store.active_triggers(entry)
                if not triggers or entry.command is None:
                    continue
                # the phrases were checked against the router when the file was read
                specs.append(
                    CommandSpec(
                        plugin=PLUGIN,
                        name=entry.id,
                        patterns=tuple(triggers),
                        handler=self._handler(entry.id),
                        priority=USER_PRIORITY,
                        description=entry.command.name,
                    )
                )
        self.app.router.set_commands(specs, group=PLUGIN)
        logger.debug("Пользовательских команд: {}", len(specs))

    def _on_config(self, event: ConfigChanged) -> None:
        if "commands" in event.sections:
            self.register()

    async def _watch(self) -> None:
        from watchfiles import awatch

        root = self.store.root
        if not root.is_dir():
            return
        async for _ in awatch(root, debounce=700, stop_event=self._stop_watch):
            try:
                self.reload()
            except Exception:
                logger.exception("Ошибка перечитывания команд")

    # -- matching -------------------------------------------------------------------------

    def _handler(self, command_id: str) -> Any:
        async def handle(turn: Turn, **slots: Any) -> bool:
            config = self.app.config.commands
            if turn.source == "voice" and config.require_wake_name and not turn.named:
                logger.info("Команда {} пропущена: фраза без имени ассистента", command_id)
                return False
            await self.execute(command_id, turn, {k: str(v) for k, v in slots.items()})
            return True

        return handle

    # -- running ----------------------------------------------------------------------------

    async def execute(
        self, command_id: str, turn: Turn, slots: dict[str, str] | None = None
    ) -> None:
        """Run a command inside ``turn``; failures are said aloud and mark the run as error."""
        try:
            await self._run_command(command_id, _Run(turn, slots or {}))
        except _Declined:
            await turn.say("Отменено.")
        except ActionError as e:
            turn.failed = True
            await turn.say(f"Не получилось: {e}")

    async def start_test(self, command_id: str) -> str:
        """Run a command from the UI without a phrase; returns the run id at once."""
        entry = self.store.get(command_id)
        if entry is None or entry.command is None:
            raise KeyError(command_id)
        turn = await self.app.dialog.start(
            entry.command.name, lambda t: self.execute(entry.id, t), source="ui"
        )
        return turn.run_id

    async def _run_command(self, reference: str, run: _Run) -> None:
        entry = self.store.find(reference)
        if entry is None or entry.command is None:
            reason = f": {entry.error}" if entry is not None and entry.error else ""
            raise ActionError(f"нет команды «{reference}»{reason}")
        if entry.id in run.stack:
            raise ActionError(f"команда «{entry.command.name}» вызывает саму себя")
        if len(run.stack) >= MAX_NESTING:
            raise ActionError(f"слишком глубокая вложенность команд (больше {MAX_NESTING})")
        command = entry.command
        run.stack.append(entry.id)
        try:
            if command.confirm == "always" and not await self.confirm(
                run, f"Выполнить «{command.name}»?"
            ):
                raise _Declined
            for action in command.actions:
                await self._step(run, command, _fill(action, run.slots))
        finally:
            run.stack.pop()

    async def _step(self, run: _Run, command: Command, action: Action) -> None:
        bus = self.app.bus
        step_id = _new_id()
        title = action.title()
        status: StepStatus = "error"
        detail = ""
        cancelled = False
        try:  # a stop during the announcement still closes the step
            await bus.publish(
                StepStarted(run_id=run.run_id, step_id=step_id, kind="action", title=title)
            )
            question = f"Выполнить «{command.name}»: {title}?"
            ask = command.confirm == "auto" and self.needs_confirm(action)
            if ask and not await self.confirm(run, question):
                raise _Declined
            detail = await self._perform(action, run)
            status = "ok"
        except _Declined:
            status, detail = "cancelled", "Отменено"
            raise
        except asyncio.CancelledError:
            status, cancelled = "cancelled", True
            raise
        except ActionError as e:
            detail = str(e)
            raise
        except Exception as e:
            logger.exception("Ошибка действия {} команды {}", action.type, command.name)
            detail = user_message(e)
            raise ActionError(detail) from e
        finally:
            event = StepFinished(
                run_id=run.run_id, step_id=step_id, status=status, detail=_tail(detail)
            )
            if cancelled:
                bus.emit(event)  # the run is being cancelled: do not wait here
            else:
                await bus.publish(event)

    def needs_confirm(self, action: Action) -> bool:
        if action.dangerous:
            return True
        if isinstance(action, FileAction):
            with contextlib.suppress(ActionError):
                return expand_path(action.path).exists()  # overwriting is dangerous
        return False

    def is_dangerous(self, entry: Entry, seen: set[str] | None = None) -> bool:
        """Some action (or a nested command's action) is dangerous by its kind."""
        command = entry.command
        if command is None:
            return False
        if command.dangerous:
            return True
        seen = (seen or set()) | {entry.id}
        for action in command.actions:
            if isinstance(action, RunAction):
                nested = self.store.find(action.command)
                if nested is not None and nested.id not in seen and self.is_dangerous(nested, seen):
                    return True
        return False

    async def _perform(self, action: Action, run: _Run) -> str:  # noqa: PLR0912
        backend = self.backend
        match action:
            case OpenAction(target=target):
                return await backend.open(target)
            case SearchAction(query=query, engine=engine):
                await backend.search(query, engine)
                return f"Поиск открыт: {query}"
            case KeysAction(combo=str(combo)):
                await backend.keys(parse_combo(combo))
                return combo
            case KeysAction(text=str(text)):
                await backend.type_keys(text)
                return text
            case MouseAction():
                await backend.mouse(action.action, action.x, action.y, action.amount)
                return ""
            case WaitAction(seconds=seconds):
                await asyncio.sleep(seconds)
                return ""
            case VolumeAction():
                return await backend.volume(
                    set_to=action.set, change=action.change, mute=action.mute, app=action.app
                )
            case WindowAction(action=what, name=name):
                return await backend.window(what, name)
            case MinimizeAllAction():
                await backend.minimize_all()
                return "Окна свёрнуты"
            case ShellAction():
                return await self._shell(action)
            case PowerAction(action=what):
                await backend.power(what)
                return action.title()
            case DeleteAction(path=raw):
                path = expand_path(raw)
                check_deletable(path)
                await backend.recycle(path)
                return f"В корзине: {path}"
            case FileAction(path=raw, content=content):
                path = expand_path(raw)
                if path.is_dir():
                    raise ActionError(f"{path} — это папка")
                await asyncio.to_thread(_write_text, path, content)
                return f"Сохранён {path}"
            case DictateAction(prompt=prompt):
                text = await run.turn.ask(prompt or "Что напечатать?")
                if not text:
                    raise ActionError("не расслышал текст для диктовки")
                return await backend.paste(text)
            case PlayAction(path=raw):
                await backend.play(expand_path(raw))
                return ""
            case SayAction(text=text):
                await run.turn.say(text)
                return text
            case RunAction(command=reference):
                await self._run_command(reference, run)
                return ""
            case _:
                raise ActionError(f"неизвестное действие {action.type}")

    async def _shell(self, action: ShellAction) -> str:
        timeout = action.timeout or self.app.config.commands.shell_timeout
        try:
            result = await self.backend.shell(action.command, action.shell, timeout)
        except ShellTimeout as e:
            raise ActionError(f"команда {e}") from e
        except OSError as e:
            raise ActionError(f"не запускается: {e}") from e
        if result.code != 0:
            raise ActionError(f"код выхода {result.code}. {_tail(result.output, 500)}".strip())
        return result.output or "Выполнено"

    # -- confirmation -----------------------------------------------------------------------

    async def confirm(self, run: _Run, question: str) -> bool:
        """Ask "yes or no" by voice and in the UI at once; silence until the timeout is "no"."""
        bus = self.app.bus
        timeout = self.app.config.commands.confirm_timeout
        step_id = _new_id()
        key = (run.run_id, step_id)
        # both answers are awaited before the question is announced: a quick click must find it
        ui: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[key] = ui
        voice = asyncio.create_task(run.turn.confirm(question, timeout), name="command-confirm")
        answer: bool | None = None
        cancelled = False
        try:
            await bus.publish(
                StepStarted(run_id=run.run_id, step_id=step_id, kind="confirm", title=question)
            )
            await bus.publish(
                ConfirmRequested(
                    run_id=run.run_id, step_id=step_id, question=question, timeout=timeout
                )
            )
            done, _ = await asyncio.wait(
                {ui, voice}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if ui in done and not ui.cancelled():
                answer = ui.result()
            elif voice in done and not voice.cancelled() and voice.exception() is None:
                answer = voice.result()
            return bool(answer)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            self._pending.pop(key, None)
            if not ui.done():
                ui.cancel()
            if not voice.done():
                voice.cancel()
            detail = "Нет ответа" if answer is None else ("Да" if answer else "Нет")
            event = StepFinished(
                run_id=run.run_id,
                step_id=step_id,
                status="ok" if answer else "cancelled",
                detail="Отменено" if cancelled else detail,
            )
            if cancelled:
                bus.emit(event)
            else:
                await bus.publish(event)

    def answer(self, run_id: str, step_id: str, answer: bool) -> bool:
        """An answer from the UI buttons; False if nothing waits for it (any more)."""
        future = self._pending.get((run_id, step_id))
        if future is None or future.done():
            return False
        future.set_result(bool(answer))
        return True

    # -- views for the UI ---------------------------------------------------------------------

    def summary(self, entry: Entry) -> dict[str, Any]:
        command = entry.command
        return {
            "id": entry.id,
            "folder": entry.folder,
            "name": command.name if command else entry.id.rpartition("/")[2],
            "triggers": list(command.triggers) if command else [],
            "enabled": self.store.is_enabled(entry),
            "dangerous": self.is_dangerous(entry),
            "lang": command.lang if command else "ru",
            "description": command.description if command else "",
            "error": entry.error,
        }

    def listing(self) -> dict[str, Any]:
        entries = sorted(self.store.entries.values(), key=lambda e: e.id)
        return {
            "commands": [self.summary(e) for e in entries],
            "folders": self.store.folders(),
        }


def _fill(action: Action, slots: dict[str, str]) -> Action:
    """Put the phrase's ``{slot}`` values into the action's text fields."""
    if not slots:
        return action
    data = action.model_dump()
    changed = False
    for key, value in data.items():
        if isinstance(value, str) and key != "type" and _SLOT.search(value):
            data[key] = _SLOT.sub(lambda m: slots.get(m.group(1), m.group(0)), value)
            changed = True
    return _ACTION.validate_python(data) if changed else action


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def check_deletable(path: Path) -> None:
    """Refuse to touch the drive roots, the home folder itself and the system folders."""
    if not path.exists():
        raise ActionError(f"нет такого файла или папки: {path}")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ActionError(f"{path} удалять нельзя")
    protected = [
        os.environ.get("SYSTEMROOT", r"C:\Windows"),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
    ]
    for root in protected:
        if resolved.is_relative_to(Path(root).resolve()):
            raise ActionError(f"{path} — системная папка, удалять нельзя")
