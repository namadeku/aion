"""Command-line interface: ``aion text``, ``aion run``, ``aion config ...``, ``aion plugin ...``."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import traceback
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import TaskID

from aion.config import ConfigStore, dump_yaml
from aion.config.loader import find_config_path
from aion.log import report_fatal, setup_logging

app = typer.Typer(help="Aion — голосовой ассистент.", no_args_is_help=True)
config_app = typer.Typer(help="Работа с конфигом.", no_args_is_help=True)
app.add_typer(config_app, name="config")

console = Console(highlight=False)

ConfigOption = Annotated[
    Path | None, typer.Option("--config", "-c", help="Путь к config.yaml", show_default=False)
]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Подробные логи в консоли")]

_EXIT_WORDS = {"выход", "exit", "quit", ":q"}


@app.command()
def text(config: ConfigOption = None, verbose: VerboseOption = False) -> None:
    """Текстовый режим: ввод и вывод в консоли, без звука."""
    setup_logging("DEBUG" if verbose else "WARNING")
    store = ConfigStore.load(config)
    setup_logging("DEBUG" if verbose else "WARNING", store.config.paths.logs)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_text_loop(store))


async def _text_loop(store: ConfigStore) -> None:
    from aion.app import Aion

    async with Aion(store) as aion:
        name = aion.config.profile.name
        console.print(f"[dim]{name} в текстовом режиме. «выход» — завершить.[/]")
        while True:
            try:
                line = await asyncio.to_thread(input, "")
            except EOFError:
                break
            if line.strip().lower() in _EXIT_WORDS:
                break
            await aion.dialog.submit(line, source="text")
            # Let the turn run until it finishes or starts waiting for an answer.
            while aion.dialog.busy and not aion.dialog.awaiting_answer:
                await asyncio.sleep(0.02)
        await aion.dialog.wait()


class _ProgressBars:
    """Rich progress bars for model downloads (callback for :mod:`aion.models`)."""

    def __init__(self) -> None:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
        )

        self._progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )
        self._tasks: dict[str, TaskID] = {}

    def __call__(self, label: str, done: int, total: int) -> None:
        if label not in self._tasks:
            self._progress.start()
            self._tasks[label] = self._progress.add_task(label, total=total or None)
        self._progress.update(self._tasks[label], completed=done, total=total or None)
        if total and done >= total:
            self._progress.remove_task(self._tasks.pop(label))
            if not self._tasks:
                self._progress.stop()


@app.command()
def run(
    config: ConfigOption = None,
    verbose: VerboseOption = False,
    voice: Annotated[bool, typer.Option("--voice/--no-voice", help="Микрофон и голос")] = True,
    ui: Annotated[bool, typer.Option("--ui/--no-ui", help="Веб-интерфейс")] = True,
    window: Annotated[
        bool, typer.Option("--window/--browser", help="Отдельное окно или вкладка браузера")
    ] = True,
    headless: Annotated[
        bool, typer.Option("--headless", help="Без окна и браузера (ссылка в консоли)")
    ] = False,
) -> None:
    """Запустить ассистента: голос + интерфейс (окно, трей, горячая клавиша)."""
    from aion.runtime import RunOptions
    from aion.runtime import run as run_assistant

    setup_logging("DEBUG" if verbose else "INFO")
    store = ConfigStore.load(config)
    setup_logging("DEBUG" if verbose else "INFO", store.config.paths.logs)
    options = RunOptions(
        voice=voice,
        ui=ui and store.config.ui.enabled,
        window=window and store.config.ui.window and not headless,
        open_browser=not headless,
    )
    try:
        run_assistant(store, options, _ProgressBars(), console)
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        report_fatal(str(e))
        raise typer.Exit(1) from e


@app.command()
def say(
    phrase: Annotated[str, typer.Argument(help="Что сказать")],
    engine: Annotated[str | None, typer.Option(help="piper / silero / edge / xtts")] = None,
    voice: Annotated[str | None, typer.Option(help="Голос движка")] = None,
    effect: Annotated[str | None, typer.Option(help="none / metallic / radio")] = None,
    save: Annotated[Path | None, typer.Option(help="Сохранить в WAV вместо проигрывания")] = None,
    config: ConfigOption = None,
) -> None:
    """Произнести фразу текущим (или указанным) голосом — проверка TTS."""
    store = _quiet_store(config)
    patch = {k: v for k, v in {"engine": engine, "voice": voice, "effect": effect}.items() if v}
    voice_cfg = store.config.profile.voice.model_copy(update=patch)

    async def go() -> None:
        from aion.audio.player import AudioPlayer
        from aion.tts.output import create_engine, render_voice

        tts = create_engine(voice_cfg.engine, store.config.paths.models, _ProgressBars(), voice_cfg)
        await tts.prepare(voice_cfg.voice)
        audio = await render_voice(tts, phrase, voice_cfg)
        if save is not None:
            _write_wav(save, audio.samples, audio.sample_rate)
            console.print(f"[green]Сохранено: {save}[/]")
            return
        player = AudioPlayer(store.config.audio.output_device)
        player.start()
        try:
            await player.play(audio.samples, audio.sample_rate)
            await asyncio.sleep(0.2)
        finally:
            player.close()

    asyncio.run(go())


def _write_wav(path: Path, samples: object, sample_rate: int) -> None:
    import wave

    import numpy as np

    data = (np.clip(np.asarray(samples), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())


@app.command()
def shortcut(config: ConfigOption = None) -> None:
    """Создать ярлык Aion на рабочем столе (запуск без окна консоли)."""
    from aion.ui.desktop import create_desktop_shortcut, ensure_icon

    store = _quiet_store(config)
    icon = ensure_icon(store.config.paths.data_dir / "aion.ico")
    try:
        link = create_desktop_shortcut(icon)
    except Exception as e:
        console.print(f"[red]Не удалось создать ярлык: {e}[/]")
        raise typer.Exit(1) from e
    console.print(f"[green]Ярлык создан: {link}[/]")


@app.command()
def devices() -> None:
    """Список аудиоустройств (для audio.input_device / audio.output_device)."""
    from rich.table import Table

    from aion.audio.devices import list_devices

    table = Table("#", "Устройство", "API", "Вход", "Выход")
    for d in list_devices():
        mark_in = "✓ (по умолч.)" if d.is_default_input else ("✓" if d.inputs else "")
        mark_out = "✓ (по умолч.)" if d.is_default_output else ("✓" if d.outputs else "")
        table.add_row(str(d.index), d.name, d.hostapi, mark_in, mark_out)
    console.print(table)


models_app = typer.Typer(help="Модели распознавания и синтеза.", no_args_is_help=True)
app.add_typer(models_app, name="models")


@models_app.command("download")
def models_download(config: ConfigOption = None) -> None:
    """Скачать все модели, нужные текущему конфигу (для офлайн-работы)."""
    store = _quiet_store(config)

    async def go() -> None:
        from aion.stt import create_stt
        from aion.tts.output import create_engine
        from aion.voice import create_vad, create_wake

        cfg = store.config
        progress = _ProgressBars()
        console.print("VAD…")
        await create_vad(cfg, progress)
        console.print("Wake word…")
        await create_wake(cfg, progress)
        console.print(f"Голос {cfg.profile.voice.engine}/{cfg.profile.voice.voice}…")
        await create_engine(cfg.profile.voice.engine, cfg.paths.models, progress).prepare(
            cfg.profile.voice.voice
        )
        console.print(f"Распознавание {cfg.stt.backend}…")
        await create_stt(cfg, progress).load()
        console.print(f"[green]Готово. Модели в {cfg.paths.models}[/]")

    asyncio.run(go())


@config_app.command("path")
def config_path(config: ConfigOption = None) -> None:
    """Показать, какой config.yaml используется."""
    console.print(str(find_config_path(config).resolve()))


@config_app.command("show")
def config_show(config: ConfigOption = None) -> None:
    """Показать итоговый конфиг (файл + значения по умолчанию)."""
    setup_logging("WARNING")
    console.print(dump_yaml(ConfigStore.load(config).config), markup=False)


@config_app.command("init")
def config_init(
    config: ConfigOption = None,
    force: Annotated[bool, typer.Option("--force", help="Перезаписать существующий")] = False,
) -> None:
    """Создать config.yaml со всеми настройками по умолчанию."""
    setup_logging("WARNING")
    path = find_config_path(config)
    if path.exists() and not force:
        console.print(f"[yellow]{path} уже существует (--force чтобы перезаписать)[/]")
        raise typer.Exit(1)
    store = ConfigStore.load(config)
    store.path = path
    store.save()
    console.print(f"[green]Создан {path.resolve()}[/]")


plugin_app = typer.Typer(help="Управление плагинами.", no_args_is_help=True)
app.add_typer(plugin_app, name="plugin")


def _quiet_store(config: Path | None) -> ConfigStore:
    setup_logging("WARNING")
    return ConfigStore.load(config)


@plugin_app.command("list")
def plugin_list(config: ConfigOption = None) -> None:
    """Список найденных плагинов."""
    from rich.table import Table

    from aion.plugins.manager import discover, plugin_dirs

    store = _quiet_store(config)
    table = Table("Плагин", "Версия", "Статус", "Права", "Путь")
    for name, record in sorted(discover(plugin_dirs(store.config)).items()):
        m = record.manifest
        if record.status == "error":
            status = "[red]ошибка[/]"
        elif name in store.config.plugins.disabled or (
            m and not m.enabled_by_default and name not in store.config.plugins.enabled
        ):
            status = "[yellow]выключен[/]"
        else:
            status = "[green]включён[/]"
        kind = " (встроенный)" if record.builtin else ""
        table.add_row(
            f"{name}{kind}",
            m.version if m else "-",
            status,
            ", ".join(m.permissions) if m else "",
            str(record.path),
        )
    console.print(table)


@plugin_app.command("new")
def plugin_new(
    name: str,
    directory: Annotated[
        Path | None, typer.Option("--dir", help="Куда создать (по умолчанию — папка плагинов)")
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Создать заготовку плагина."""
    from aion.plugins.scaffold import create_plugin

    store = _quiet_store(config)
    root = directory or store.config.paths.plugins
    try:
        path = create_plugin(name, root)
    except (ValueError, FileExistsError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e
    console.print(f"[green]Создан {path}[/] — отредактируйте main.py, изменения подхватятся сами.")


@plugin_app.command("install")
def plugin_install(
    source: Annotated[str, typer.Argument(help="Папка, .zip (файл или URL) или git-URL")],
    force: Annotated[bool, typer.Option("--force", help="Заменить установленный")] = False,
    config: ConfigOption = None,
) -> None:
    """Установить плагин и его зависимости."""
    from aion.plugins.installer import InstallError, install_dependencies, install_plugin
    from aion.plugins.manifest import ManifestError

    store = _quiet_store(config)
    paths = store.config.paths

    async def run() -> None:
        manifest = await install_plugin(source, paths.plugins, force=force)
        if manifest.dangerous_permissions:
            console.print(
                f"[yellow]Внимание: плагин запрашивает права {manifest.dangerous_permissions}. "
                "Плагины выполняются с полными правами пользователя — ставьте только из "
                "доверенных источников.[/]"
            )
        if manifest.dependencies:
            console.print(f"Ставлю зависимости: {', '.join(manifest.dependencies)}…")
            await install_dependencies(manifest, paths.plugin_deps / manifest.name)
        console.print(f"[green]Установлен {manifest.display_name} {manifest.version}[/]")

    try:
        asyncio.run(run())
    except (InstallError, ManifestError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


@plugin_app.command("remove")
def plugin_remove(name: str, config: ConfigOption = None) -> None:
    """Удалить установленный плагин."""
    from aion.plugins.installer import InstallError, remove_plugin

    store = _quiet_store(config)
    roots = [store.config.paths.plugins, *store.config.plugins.dirs]
    try:
        path = remove_plugin(name, roots, store.config.paths.plugin_deps)
    except InstallError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e
    console.print(f"[green]Удалён {path}[/]")


@plugin_app.command("enable")
def plugin_enable(name: str, config: ConfigOption = None) -> None:
    """Включить плагин."""
    store = _quiet_store(config)
    cfg = store.config.plugins
    store.update(
        {
            "plugins": {
                "disabled": [n for n in cfg.disabled if n != name],
                "enabled": sorted({*cfg.enabled, name}),
            }
        }
    )
    console.print(f"[green]{name} включён[/]")


@plugin_app.command("disable")
def plugin_disable(name: str, config: ConfigOption = None) -> None:
    """Выключить плагин."""
    store = _quiet_store(config)
    cfg = store.config.plugins
    store.update(
        {
            "plugins": {
                "disabled": sorted({*cfg.disabled, name}),
                "enabled": [n for n in cfg.enabled if n != name],
            }
        }
    )
    console.print(f"[yellow]{name} выключен[/]")


def main() -> None:
    # pythonw (desktop shortcut, autostart) and the windowed exe have no console: send output
    # nowhere (report_fatal shows a message box instead)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # piped input is UTF-8 nowadays (a frozen exe ignores PYTHONIOENCODING); the interactive
    # Windows console delivers Unicode regardless of this setting
    if isinstance(sys.stdin, io.TextIOWrapper) and not sys.stdin.isatty():
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    try:
        app()
    except Exception as e:
        report_fatal(f"{type(e).__name__}: {e}", traceback.format_exc())
        raise
