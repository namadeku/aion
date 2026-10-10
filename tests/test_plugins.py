from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.core.events import PluginStatusChanged
from aion.plugins.installer import InstallError, install_plugin
from aion.plugins.manifest import ManifestError, load_manifest
from aion.plugins.scaffold import create_plugin
from tests.helpers import say, settle, write_plugin

GREETER = """
from aion.sdk import Context, Plugin, command, tool


class Greeter(Plugin):
    loaded = False

    async def on_load(self) -> None:
        Greeter.loaded = True

    @command(["поздоровайся", "поздоровайся с {name}"])
    async def hello(self, ctx: Context, name: str = "миром") -> None:
        await ctx.say(f"{ctx.config['greeting']}, {name}!")

    @tool("Поздороваться")
    async def greet(self, name: str) -> str:
        return f"hi {name}"
"""

GREETER_MANIFEST = """
settings:
  greeting:
    type: string
    default: Привет
"""


def make_app(store: ConfigStore) -> Aion:
    aion = Aion(store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    aion.dialog.ask_timeout = 1.0
    return aion


async def test_load_and_route(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    async with make_app(app_store) as aion:
        record = aion.plugins.records["greeter"]
        assert record.status == "loaded"
        assert [c.name for c in record.commands] == ["hello"]
        assert await say(aion, "поздоровайся") == ["Привет, миром!"]
        assert await say(aion, "поздоровайся с Тони") == ["Привет, тони!"]


async def test_builtins_are_loaded(app: Aion) -> None:
    loaded = {r.name for r in app.plugins.loaded()}
    assert {"assistant", "clock", "timers", "weather", "calc"} <= loaded


async def test_broken_plugin_is_isolated(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "broken", "this is not python(")
    write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    (plugins_dir / "badmanifest").mkdir()
    (plugins_dir / "badmanifest" / "plugin.yaml").write_text("name: Bad Name!\n", "utf-8")
    statuses: list[PluginStatusChanged] = []
    aion = make_app(app_store)
    aion.bus.subscribe(PluginStatusChanged, statuses.append)
    async with aion:
        assert aion.plugins.records["broken"].status == "error"
        assert "SyntaxError" in (aion.plugins.records["broken"].error or "")
        assert aion.plugins.records["badmanifest"].status == "error"
        assert aion.plugins.records["greeter"].status == "loaded"
        assert any(s.plugin == "broken" and s.status == "error" for s in statuses)


async def test_failing_on_load_is_isolated(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(
        plugins_dir,
        "faulty",
        """
        from aion.sdk import Plugin
        class Faulty(Plugin):
            async def on_load(self):
                raise RuntimeError("no config")
        """,
    )
    async with make_app(app_store) as aion:
        assert aion.plugins.records["faulty"].status == "error"
        assert "no config" in (aion.plugins.records["faulty"].error or "")


async def test_reload_picks_up_changes(app_store: ConfigStore, plugins_dir: Path) -> None:
    path = write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    async with make_app(app_store) as aion:
        code = GREETER.replace('f"{ctx.config', 'f"Йо! {ctx.config')
        (path / "main.py").write_text(code, encoding="utf-8")
        await aion.plugins.reload("greeter")
        assert await say(aion, "поздоровайся") == ["Йо! Привет, миром!"]


async def test_dir_change_handles_new_and_removed(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    async with make_app(app_store) as aion:
        path = write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
        await aion.plugins._on_dir_changed(path)  # pyright: ignore[reportPrivateUsage]
        assert aion.plugins.records["greeter"].status == "loaded"
        (path / "plugin.yaml").unlink()
        await aion.plugins._on_dir_changed(path)  # pyright: ignore[reportPrivateUsage]
        assert "greeter" not in aion.plugins.records
        assert aion.router.match("поздоровайся") is None


async def test_hot_reload_watcher(app_store: ConfigStore, plugins_dir: Path) -> None:
    path = write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=True)
    reloaded = asyncio.Event()
    aion.bus.subscribe(
        PluginStatusChanged,
        lambda e: reloaded.set() if e.plugin == "greeter" and e.status == "loaded" else None,
    )
    async with aion:
        await asyncio.sleep(0.5)  # let the watcher start
        reloaded.clear()
        code = GREETER.replace('f"{ctx.config', 'f"Обновлено. {ctx.config')
        (path / "main.py").write_text(code, encoding="utf-8")
        await asyncio.wait_for(reloaded.wait(), timeout=10)
        await settle(aion)
        assert await say(aion, "поздоровайся") == ["Обновлено. Привет, миром!"]


async def test_disable_and_enable(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    async with make_app(app_store) as aion:
        await aion.plugins.disable("greeter")
        assert aion.plugins.records["greeter"].status == "disabled"
        assert "greeter" in aion.config.plugins.disabled
        assert aion.router.match("поздоровайся") is None
        await aion.plugins.enable("greeter")
        assert aion.plugins.records["greeter"].status == "loaded"
        assert aion.router.match("поздоровайся") is not None


async def test_settings_update(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "greeter", GREETER, GREETER_MANIFEST)
    async with make_app(app_store) as aion:
        await aion.plugins.update_settings("greeter", {"greeting": "Здравия желаю"})
        assert await say(aion, "поздоровайся") == ["Здравия желаю, миром!"]
        with pytest.raises(ValueError, match="Неизвестная"):
            await aion.plugins.update_settings("greeter", {"nope": 1})


async def test_on_utterance_intercepts(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(
        plugins_dir,
        "parrot",
        """
        from aion.sdk import Context, Plugin
        class Parrot(Plugin):
            async def on_utterance(self, ctx: Context) -> bool:
                if ctx.text.startswith("попугай"):
                    await ctx.say(ctx.text.removeprefix("попугай").strip())
                    return True
                return False
        """,
    )
    async with make_app(app_store) as aion:
        assert await say(aion, "попугай который час") == ["который час"]


async def test_storage_persists_between_runs(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(
        plugins_dir,
        "counter",
        """
        from aion.sdk import Context, Plugin, command
        class Counter(Plugin):
            @command("посчитай меня")
            async def count(self, ctx: Context) -> None:
                n = await ctx.storage.get("n", 0) + 1
                await ctx.storage.set("n", n)
                await ctx.say(str(n))
        """,
    )
    async with make_app(app_store) as aion:
        assert await say(aion, "посчитай меня") == ["1"]
    async with make_app(app_store) as aion:
        assert await say(aion, "посчитай меня") == ["2"]


async def test_background_tasks_cancelled_on_unload(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    write_plugin(
        plugins_dir,
        "ticker",
        """
        import asyncio
        from aion.sdk import Plugin
        class Ticker(Plugin):
            async def on_load(self):
                self.task = self.create_task(asyncio.sleep(3600))
        """,
    )
    async with make_app(app_store) as aion:
        inst = aion.plugins.instance("ticker")
        assert inst is not None
        task = inst.task  # pyright: ignore[reportAttributeAccessIssue]
        await aion.plugins.disable("ticker")
        await settle(aion)
        assert task.cancelled()


async def test_plugin_with_helper_module(app_store: ConfigStore, plugins_dir: Path) -> None:
    path = write_plugin(
        plugins_dir,
        "multi",
        """
        from aion.sdk import Context, Plugin, command
        from .helpers import answer
        class Multi(Plugin):
            @command("главный вопрос")
            async def q(self, ctx: Context) -> None:
                await ctx.say(answer())
        """,
    )
    (path / "helpers.py").write_text("def answer() -> str:\n    return '42'\n", encoding="utf-8")
    async with make_app(app_store) as aion:
        assert await say(aion, "главный вопрос") == ["42"]


async def test_scaffold_creates_loadable_plugin(app_store: ConfigStore, plugins_dir: Path) -> None:
    create_plugin("my_skill", plugins_dir)
    assert load_manifest(plugins_dir / "my_skill").name == "my_skill"
    async with make_app(app_store) as aion:
        assert aion.plugins.records["my_skill"].status == "loaded"
        assert await say(aion, "тест my skill") == ["Привет, сэр!"]
        assert await say(aion, "тест my skill Тони") == ["Привет, тони!"]


def test_scaffold_rejects_bad_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="латиница"):
        create_plugin("Моё", tmp_path)


async def test_install_from_dir_and_zip(tmp_path: Path) -> None:
    src = write_plugin(tmp_path / "src", "greeter", GREETER, GREETER_MANIFEST)
    target = tmp_path / "installed"
    manifest = await install_plugin(str(src), target)
    assert manifest.name == "greeter"
    assert (target / "greeter" / "main.py").exists()
    with pytest.raises(InstallError, match="уже установлен"):
        await install_plugin(str(src), target)

    archive = tmp_path / "greeter.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for file in src.iterdir():
            zf.write(file, f"greeter-main/{file.name}")
    await install_plugin(str(archive), target, force=True)
    assert (target / "greeter" / "plugin.yaml").exists()


async def test_install_rejects_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.txt", "x")
        zf.writestr("plugin.yaml", "name: evil\n")
    with pytest.raises(InstallError, match="Небезопасный"):
        await install_plugin(str(archive), tmp_path / "installed")


def test_manifest_validation(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "plugin.yaml").write_text(
        "name: p\nsettings:\n  mode:\n    type: enum\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="options"):
        load_manifest(tmp_path / "p")
