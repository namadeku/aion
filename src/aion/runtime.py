"""Running the assistant: voice, web UI, desktop window, tray and hotkey together."""

from __future__ import annotations

import asyncio
import contextlib
import random
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger
from rich.console import Console

from aion.app import Aion, console_speech
from aion.config import Config, ConfigStore
from aion.core.events import AssistantReply, SpeechRecognized
from aion.models import Progress

if TYPE_CHECKING:
    from aion.ui.desktop import Tray, Window
    from aion.ui.mascot import DesktopMascot
    from aion.ui.server import UiServer

EXIT_WORDS = {"выход", "exit", "quit", ":q"}


@dataclass
class RunOptions:
    voice: bool = True
    ui: bool = True
    window: bool = True
    open_browser: bool = True
    console_input: bool = True
    greet: bool = True


@dataclass
class Session:
    """Handles shared between the asyncio thread and the main (window) thread."""

    loop: asyncio.AbstractEventLoop | None = None
    aion: Aion | None = None
    server: UiServer | None = None
    url: str | None = None
    stop: asyncio.Event | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None

    def request_stop(self) -> None:
        if self.loop is not None and self.stop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop.set)

    def toggle_mute(self) -> None:
        aion = self.aion
        if self.loop is not None and aion is not None and aion.voice is not None:
            voice = aion.voice
            self.loop.call_soon_threadsafe(lambda: voice.set_muted(not voice.muted))

    def push_to_talk(self) -> None:
        loop, aion = self.loop, self.aion
        if loop is not None and aion is not None and aion.voice is not None:
            voice = aion.voice
            loop.call_soon_threadsafe(lambda: loop.create_task(voice.push_to_talk()))


async def serve(  # noqa: PLR0915 - startup orchestration
    store: ConfigStore,
    options: RunOptions,
    session: Session,
    progress: Progress | None = None,
    console: Console | None = None,
) -> None:
    from aion.ui.desktop import Hotkey, set_autostart

    session.loop = asyncio.get_running_loop()
    session.loop.set_exception_handler(_quiet_connection_resets)
    session.stop = asyncio.Event()
    progress = _startup_progress(progress, session)

    if options.voice:
        from aion.voice import voice_output_factory

        speech = voice_output_factory(store, progress)
    else:
        speech = console_speech
    aion = Aion(store, speech=speech)
    aion.progress = progress
    session.aion = aion

    if console is not None:
        _echo_dialog(aion, console, voice=options.voice)

    def on_config(config: Config, sections: set[str]) -> None:
        if "ui" in sections:
            with contextlib.suppress(Exception):
                set_autostart(config.ui.autostart)

    store.subscribe(on_config)

    hotkey: Hotkey | None = None
    server = None
    async with aion:
        try:
            if options.ui:
                from aion.ui.server import UiServer

                server = UiServer(aion)
                server.maintenance.request_quit = session.request_stop
                await server.start()
                session.server = server
                session.url = server.url
        except BaseException as e:
            session.error = e
            session.ready.set()
            raise
        # the window opens now: the first start downloads voice models, the page shows progress
        session.ready.set()

        if options.voice and await _start_voice(aion, server, progress):
            assert aion.voice is not None
            if aion.config.ui.push_to_talk:
                hotkey = Hotkey(aion.config.ui.push_to_talk, session.loop, aion.voice.push_to_talk)
                hotkey.start()

        if options.greet:
            await aion.dialog.say(greeting(aion.config), emotion="joy", wait=False)

        tasks: list[asyncio.Task[object]] = [asyncio.create_task(session.stop.wait())]
        if options.console_input and sys.stdin is not None and sys.stdin.isatty():
            tasks.append(asyncio.create_task(_console_input(aion)))
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()

        if hotkey is not None:
            hotkey.stop()
        if server is not None:
            await server.stop()


def greeting(config: Config, now: datetime | None = None) -> str:
    """A varied, time-of-day aware greeting."""
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        hello = "Доброе утро"
    elif 12 <= hour < 17:
        hello = "Добрый день"
    elif 17 <= hour < 23:
        hello = "Добрый вечер"
    else:
        hello = "Доброй ночи"
    address = config.assistant.user_address
    female = config.profile.gender == "female"
    tails = [
        "Я на связи.",
        "Чем займёмся?",
        "Рада тебя слышать." if female else "Рад вас слышать.",
        "Как настроение?",
        f"{config.profile.name} к вашим услугам.",
    ]
    return f"{hello}, {address}. {random.choice(tails)}"


async def _start_voice(aion: Aion, server: UiServer | None, progress: Progress) -> bool:
    """Load the voice models (downloads them on the first run) and start listening.

    With the UI a failure is shown on the page and Aion keeps working in text mode.
    """
    from aion.tts.output import VoiceOutput
    from aion.voice import create_pipeline

    if server is not None:
        server.set_startup("loading")
    try:
        assert isinstance(aion.speech, VoiceOutput)
        await aion.speech.warm_up()
        pipeline = await create_pipeline(aion, progress)
        try:
            await pipeline.start()  # loads the STT model: the page shows voice controls after it
        except Exception:
            with contextlib.suppress(Exception):
                await pipeline.stop()
            raise
        aion.voice = pipeline
    except Exception as e:
        if server is None:
            raise
        logger.exception("Голос не запустился, работаю в текстовом режиме")
        if isinstance(aion.speech, VoiceOutput):
            aion.speech.silent = True
        server.set_startup("failed", f"{type(e).__name__}: {e}")
        return False
    if server is not None:
        server.set_startup("ready")
    return True


def _startup_progress(console_progress: Progress | None, session: Session) -> Progress:
    """Report model downloads to the console (if any) and to the page."""

    def report(label: str, done: int, total: int) -> None:
        if console_progress is not None:
            console_progress(label, done, total)
        if session.server is not None:
            session.server.report_download(label, done, total)

    return report


def _quiet_connection_resets(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
    """Windows' proactor loop logs a traceback whenever a browser drops a socket; ignore it."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


def _echo_dialog(aion: Aion, console: Console, *, voice: bool) -> None:
    def user(e: SpeechRecognized) -> None:
        if e.source != "text":
            console.print(f"[bold green]Вы:[/] {e.text}")

    def reply(e: AssistantReply) -> None:
        if e.text:
            console.print(f"[bold cyan]{aion.config.profile.name}:[/] {e.text}")

    aion.bus.subscribe(SpeechRecognized, user)
    if voice:  # in text mode ConsoleOutput prints replies itself
        aion.bus.subscribe(AssistantReply, reply)


async def _console_input(aion: Aion) -> None:
    while True:
        try:
            line = await asyncio.to_thread(input, "")
        except EOFError:  # no console (background, shortcut): keep running without it
            await asyncio.Event().wait()
            return
        if line.strip().lower() in EXIT_WORDS:
            return
        await aion.dialog.submit(line, source="text")


def run(
    store: ConfigStore, options: RunOptions, progress: Progress | None, console: Console
) -> None:
    """Blocking entry point used by ``aion run``."""
    from aion.ui.desktop import hold_instance_mutex

    hold_instance_mutex()
    session = Session()
    if not (options.ui and options.window):
        if options.ui:
            _open_browser_when_ready(session, launch=options.open_browser)
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(serve(store, options, session, progress, console))
        return

    # Window mode: the core runs in a background thread, pywebview owns the main thread.
    def core() -> None:
        try:
            asyncio.run(serve(store, options, session, progress, console))
        except Exception as e:  # reported to the main thread
            session.error = session.error or e
            session.ready.set()

    thread = threading.Thread(target=core, name="aion-core", daemon=True)
    thread.start()

    from aion.ui.desktop import Window, webview2_installed

    window = Window(store.config.profile.name)
    extras = _Extras()

    def attach() -> None:
        """Runs once the window is up: switch it to the interface when the core is ready."""
        session.ready.wait()
        if session.error is not None or session.url is None:
            window.close()
            return
        window.navigate(session.url)
        extras.mascot = mascot = _desktop_mascot(store, session, window)
        if session.server is not None:  # an update closes the window too
            session.server.maintenance.request_quit = lambda: _quit(session, window, mascot)
        extras.tray = _tray(store, session, window, mascot)

    try:
        # without WebView2 pywebview falls back to Internet Explorer, which cannot show the UI
        if not (webview2_installed() and window.run(attach)):
            logger.warning("Окно недоступно (нет WebView2 или pywebview) — открываю браузер")
            _raise_if_failed(session, thread)
            assert session.url is not None
            extras.tray = _tray(store, session, None, None)
            webbrowser.open(session.url)
            with contextlib.suppress(KeyboardInterrupt):
                thread.join()
    finally:
        if extras.mascot is not None:
            extras.mascot.close()
        session.request_stop()
        if extras.tray is not None:
            extras.tray.stop()
        thread.join(10)
    _raise_if_failed(session, thread)


@dataclass
class _Extras:
    """Desktop helpers created once the core is up."""

    mascot: DesktopMascot | None = None
    tray: Tray | None = None


def _raise_if_failed(session: Session, thread: threading.Thread) -> None:
    session.ready.wait()
    if session.error is not None or session.url is None:
        thread.join(5)
        raise RuntimeError(f"Не удалось запустить: {session.error}")


def _tray(
    store: ConfigStore, session: Session, window: Window | None, mascot: DesktopMascot | None
) -> Tray | None:
    if not store.config.ui.tray:
        return None
    from aion.ui.desktop import Tray

    tray = Tray(
        store.config.profile.name,
        on_open=window.show if window is not None else lambda: _open_url(session),
        on_toggle_mute=session.toggle_mute,
        is_muted=lambda: bool(session.aion and session.aion.voice and session.aion.voice.muted),
        on_quit=lambda: _quit(session, window, mascot),
        on_toggle_desktop=mascot.toggle if mascot else None,
        is_desktop=lambda: bool(mascot and mascot.enabled),
    )
    tray.start()
    return tray


def _open_url(session: Session) -> None:
    if session.url:
        webbrowser.open(session.url)


def _desktop_mascot(store: ConfigStore, session: Session, window: object) -> DesktopMascot | None:
    """The "character on the desktop" mode (Windows only, needs the UI server)."""
    from aion.ui import mascot

    if not mascot.available() or session.server is None or session.url is None:
        return None
    controller = mascot.DesktopMascot(
        url=session.url,
        main_window=window,
        send=session.server.send_threadsafe,
        push_to_talk=session.push_to_talk,
        scale=store.config.ui.desktop_scale,
    )
    session.server.desktop = controller
    return controller


def _quit(session: Session, window: object, mascot: DesktopMascot | None = None) -> None:
    if mascot is not None:
        mascot.close()
    session.request_stop()
    close = getattr(window, "close", None)
    if close is not None:
        close()


def _open_browser_when_ready(session: Session, *, launch: bool = True) -> None:
    def wait_and_open() -> None:
        session.ready.wait()
        if session.url:
            logger.info("Интерфейс: {}", session.url)
            if launch:
                webbrowser.open(session.url)

    threading.Thread(target=wait_and_open, daemon=True).start()
