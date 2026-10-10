"""Plugin manager: discovery, loading, hot reload, enable/disable and error isolation.

A plugin is a folder with ``plugin.yaml`` and an entry module (``main.py``) defining one
:class:`aion.sdk.Plugin` subclass. Each plugin is imported as its own package
``aion_plugins.<name>``, so it may contain helper modules and use relative imports.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

from loguru import logger

from aion.core.events import PluginStatusChanged
from aion.core.router import CommandSpec
from aion.plugins.installer import deps_ready, install_dependencies
from aion.plugins.manifest import MANIFEST_FILE, ManifestError, PluginManifest, load_manifest
from aion.plugins.tools import ToolSpec, build_tool
from aion.sdk.decorators import COMMAND_ATTR, TOOL_ATTR, CommandMeta, ToolMeta
from aion.sdk.plugin import Context, Plugin

if TYPE_CHECKING:
    from aion.app import Aion
    from aion.config import Config
    from aion.core.dialog import Turn

BUILTIN_DIR = Path(__file__).parent / "builtin"
PACKAGE_ROOT = "aion_plugins"

Status = Literal["loaded", "disabled", "error", "unloaded"]


@dataclass(eq=False)
class PluginRecord:
    name: str
    path: Path
    builtin: bool
    manifest: PluginManifest | None = None
    status: Status = "unloaded"
    error: str | None = None
    instance: Plugin | None = None
    commands: list[CommandSpec] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)

    def info(self) -> dict[str, Any]:
        m = self.manifest
        return {
            "name": self.name,
            "title": m.display_name if m else self.name,
            "version": m.version if m else "",
            "author": m.author if m else "",
            "description": m.description if m else "",
            "permissions": list(m.permissions) if m else [],
            "dangerous_permissions": m.dangerous_permissions if m else [],
            "settings_schema": {k: v.model_dump() for k, v in m.settings.items()} if m else {},
            "builtin": self.builtin,
            "path": str(self.path),
            "status": self.status,
            "error": self.error,
            "commands": [
                {"name": c.name, "patterns": list(c.patterns), "dangerous": c.dangerous}
                for c in self.commands
            ],
            "tools": [{"name": t.name, "description": t.description} for t in self.tools],
        }


def plugin_dirs(config: Config) -> list[tuple[Path, bool]]:
    """(directory, is_builtin) in override order: later entries win on name clashes."""
    dirs: list[tuple[Path, bool]] = [(BUILTIN_DIR, True), (config.paths.plugins, False)]
    dirs += [(Path(d).expanduser().resolve(), False) for d in config.plugins.dirs]
    unique: dict[Path, bool] = {}
    for d, builtin in dirs:
        unique.setdefault(d.resolve(), builtin)
    return list(unique.items())


def discover(dirs: list[tuple[Path, bool]]) -> dict[str, PluginRecord]:
    found: dict[str, PluginRecord] = {}
    for root, builtin in dirs:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not (child / MANIFEST_FILE).is_file():
                continue
            record = PluginRecord(name=child.name, path=child, builtin=builtin)
            try:
                record.manifest = load_manifest(child)
                record.name = record.manifest.name
            except ManifestError as e:
                record.status, record.error = "error", str(e)
            if record.name in found:
                logger.warning(
                    "Плагин {} из {} заменяет {}", record.name, child, found[record.name].path
                )
            found[record.name] = record
    return found


class PluginManager:
    def __init__(self, host: Aion) -> None:
        self.host = host
        self.records: dict[str, PluginRecord] = {}
        self._lock = asyncio.Lock()
        self._watch_task: asyncio.Task[None] | None = None
        self._stop_watch = asyncio.Event()

    # -- queries --------------------------------------------------------------------------

    def loaded(self) -> list[PluginRecord]:
        return [r for r in self.records.values() if r.status == "loaded" and r.instance]

    def commands(self) -> list[CommandSpec]:
        return [c for r in self.loaded() for c in r.commands]

    def tools(self) -> list[ToolSpec]:
        return [t for r in self.loaded() for t in r.tools]

    def instance(self, name: str) -> Plugin | None:
        record = self.records.get(name)
        return record.instance if record else None

    def is_enabled(self, name: str) -> bool:
        cfg = self.host.config.plugins
        if name in cfg.disabled:
            return False
        record = self.records.get(name)
        default = record.manifest.enabled_by_default if record and record.manifest else True
        return default or name in cfg.enabled

    def context(self, turn: Turn, plugin: str) -> Context | None:
        inst = self.instance(plugin)
        return Context(turn, inst) if inst else None

    # -- lifecycle ------------------------------------------------------------------------

    async def load_all(self) -> None:
        self.host.config.paths.plugins.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            self.records = discover(plugin_dirs(self.host.config))
            for record in self.records.values():
                if record.status == "error":
                    await self._publish(record)
                elif not self.is_enabled(record.name):
                    record.status = "disabled"
                else:
                    await self._load(record)
            self._refresh()
        names = ", ".join(r.name for r in self.loaded())
        logger.info("Плагины загружены ({}): {}", len(self.loaded()), names)

    async def shutdown(self) -> None:
        await self.stop_watching()
        async with self._lock:
            for record in self.loaded():
                await self._call_hook(record, "on_shutdown")
                await self._unload(record)
            self._refresh()

    async def reload(self, name: str) -> PluginRecord | None:
        async with self._lock:
            old = self.records.get(name)
            if old is None:
                return None
            await self._unload(old)
            fresh = discover([(old.path.parent, old.builtin)]).get(name) or PluginRecord(
                name=name, path=old.path, builtin=old.builtin, status="error", error="Не найден"
            )
            self.records[name] = fresh
            if fresh.status != "error":
                if self.is_enabled(name):
                    await self._load(fresh)
                else:
                    fresh.status = "disabled"
            self._refresh()
            return fresh

    async def enable(self, name: str) -> None:
        cfg = self.host.config.plugins
        disabled = [n for n in cfg.disabled if n != name]
        enabled = sorted({*cfg.enabled, name})
        self.host.store.update({"plugins": {"disabled": disabled, "enabled": enabled}})
        record = self.records.get(name)
        if record and record.status != "loaded":
            await self.reload(name)

    async def disable(self, name: str) -> None:
        cfg = self.host.config.plugins
        disabled = sorted({*cfg.disabled, name})
        enabled = [n for n in cfg.enabled if n != name]
        self.host.store.update({"plugins": {"disabled": disabled, "enabled": enabled}})
        async with self._lock:
            record = self.records.get(name)
            if record:
                await self._unload(record)
                record.status = "disabled"
                await self._publish(record)
            self._refresh()

    async def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        record = self.records.get(name)
        if record is None or record.manifest is None:
            raise KeyError(name)
        clean: dict[str, Any] = {}
        for key, value in values.items():
            spec = record.manifest.settings.get(key)
            if spec is None:
                raise ValueError(f"Неизвестная настройка {key!r}")
            clean[key] = spec.coerce(value)
        self.host.store.update({"plugins": {"settings": {name: clean}}})
        if record.instance:
            await self._call_hook(record, "on_settings_changed")
        return record.manifest.resolve_settings(clean)

    # -- dialog integration ---------------------------------------------------------------

    async def intercept(self, turn: Turn) -> bool:
        """``on_utterance`` hooks: the first plugin returning True consumes the phrase."""
        for record in self.loaded():
            inst = record.instance
            if inst is None or type(inst).on_utterance is Plugin.on_utterance:
                continue
            try:
                if await inst.on_utterance(Context(turn, inst)):
                    logger.debug("Фразу перехватил плагин {}", record.name)
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка в {}.on_utterance", record.name)
        return False

    async def broadcast(self, hook: str) -> None:
        for record in self.loaded():
            await self._call_hook(record, hook)

    # -- hot reload -----------------------------------------------------------------------

    def start_watching(self) -> None:
        if self._watch_task is None and self.host.config.plugins.hot_reload:
            self._stop_watch.clear()
            self._watch_task = asyncio.create_task(self._watch(), name="plugin-watch")

    async def stop_watching(self) -> None:
        if self._watch_task is not None:
            self._stop_watch.set()
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
            self._watch_task = None

    async def _watch(self) -> None:
        from watchfiles import awatch

        roots = [d for d, builtin in plugin_dirs(self.host.config) if not builtin and d.is_dir()]
        if not roots:
            return
        logger.debug("Слежу за изменениями плагинов в {}", roots)
        async for changes in awatch(*roots, debounce=700, stop_event=self._stop_watch):
            touched: set[Path] = set()
            for _, raw in changes:
                path = Path(raw)
                if "__pycache__" in path.parts or path.is_dir():
                    continue
                for root in roots:
                    try:
                        rel = path.resolve().relative_to(root)
                    except ValueError:
                        continue
                    if rel.parts:
                        touched.add(root / rel.parts[0])
            for plugin_dir in touched:
                try:
                    await self._on_dir_changed(plugin_dir)
                except Exception:
                    logger.exception("Ошибка горячей перезагрузки {}", plugin_dir)

    async def _on_dir_changed(self, plugin_dir: Path) -> None:
        record = next((r for r in self.records.values() if r.path == plugin_dir), None)
        if record is not None and not (plugin_dir / MANIFEST_FILE).exists():
            logger.info("Плагин {} удалён", record.name)
            async with self._lock:
                await self._unload(record)
                self.records.pop(record.name, None)
                self._refresh()
            return
        if record is not None:
            logger.info("Плагин {} изменён — перезагружаю", record.name)
            await self.reload(record.name)
            return
        if (plugin_dir / MANIFEST_FILE).exists():
            new = discover([(plugin_dir.parent, False)])
            for name, rec in new.items():
                if rec.path == plugin_dir and name not in self.records:
                    logger.info("Найден новый плагин {}", name)
                    async with self._lock:
                        self.records[name] = rec
                        if rec.status != "error" and self.is_enabled(name):
                            await self._load(rec)
                        self._refresh()

    # -- internals ------------------------------------------------------------------------

    async def _load(self, record: PluginRecord) -> None:
        manifest = record.manifest
        assert manifest is not None
        try:
            await self._ensure_dependencies(manifest)
            module = _import_plugin(record.path, manifest)
            cls = _find_plugin_class(module, manifest)
            inst = cls()
            inst._attach(self.host, manifest, record.path)  # pyright: ignore[reportPrivateUsage]
            record.instance = inst
            record.commands = _collect_commands(inst)
            record.tools = _collect_tools(inst)
            await inst.on_load()
            record.status, record.error = "loaded", None
            logger.debug(
                "Плагин {} {}: {} команд, {} инструментов",
                manifest.name,
                manifest.version,
                len(record.commands),
                len(record.tools),
            )
        except Exception as e:
            logger.opt(exception=e).error("Не удалось загрузить плагин {}", record.name)
            if record.instance is not None:
                await record.instance._cancel_tasks()  # pyright: ignore[reportPrivateUsage]
            record.instance, record.commands, record.tools = None, [], []
            record.status, record.error = "error", f"{type(e).__name__}: {e}"
            _purge_modules(record.name)
        await self._publish(record)

    async def _unload(self, record: PluginRecord) -> None:
        if record.instance is not None:
            await self._call_hook(record, "on_unload")
            await record.instance._cancel_tasks()  # pyright: ignore[reportPrivateUsage]
        record.instance, record.commands, record.tools = None, [], []
        if record.status == "loaded":
            record.status = "unloaded"
            await self._publish(record)
        _purge_modules(record.name)

    async def _call_hook(self, record: PluginRecord, hook: str) -> None:
        inst = record.instance
        if inst is None:
            return
        method: Callable[[], Awaitable[None]] = getattr(inst, hook)
        try:
            await method()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в {}.{}", record.name, hook)

    async def _ensure_dependencies(self, manifest: PluginManifest) -> None:
        if not manifest.dependencies:
            return
        target = self.host.config.paths.plugin_deps / manifest.name
        if not deps_ready(manifest, target):
            logger.info("Устанавливаю зависимости {}: {}", manifest.name, manifest.dependencies)
            await install_dependencies(manifest, target)
        if str(target) not in sys.path:
            sys.path.append(str(target))

    def _refresh(self) -> None:
        self.host.router.set_commands(self.commands())

    async def _publish(self, record: PluginRecord) -> None:
        status: Literal["loaded", "unloaded", "error", "disabled"] = record.status
        await self.host.bus.publish(
            PluginStatusChanged(plugin=record.name, status=status, error=record.error)
        )


def _import_plugin(path: Path, manifest: PluginManifest) -> types.ModuleType:
    _purge_modules(manifest.name)
    if PACKAGE_ROOT not in sys.modules:
        root = types.ModuleType(PACKAGE_ROOT)
        root.__path__ = []
        sys.modules[PACKAGE_ROOT] = root
    package_name = f"{PACKAGE_ROOT}.{manifest.name}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(path)]
    package.__package__ = package_name
    sys.modules[package_name] = package

    entry = path / manifest.entry
    module_name = f"{package_name}.{entry.stem}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"Не удалось импортировать {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _purge_modules(name: str) -> None:
    prefix = f"{PACKAGE_ROOT}.{name}"
    for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
        del sys.modules[key]


def _find_plugin_class(module: types.ModuleType, manifest: PluginManifest) -> type[Plugin]:
    if manifest.class_name:
        cls = getattr(module, manifest.class_name, None)
        if not (inspect.isclass(cls) and issubclass(cls, Plugin)):
            raise TypeError(f"{manifest.class_name} не найден или не наследует Plugin")
        return cls
    candidates = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and issubclass(obj, Plugin)
        and obj is not Plugin
        and obj.__module__ == module.__name__
    ]
    if len(candidates) != 1:
        raise TypeError(
            f"В {manifest.entry} должен быть ровно один класс-наследник Plugin "
            f"(найдено {len(candidates)}); или укажите class: в plugin.yaml"
        )
    return candidates[0]


def _param_names(func: Callable[..., Any]) -> list[str]:
    return [p for p in inspect.signature(func).parameters if p not in {"self", "ctx", "context"}]


def _collect_commands(inst: Plugin) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    for attr, member in inspect.getmembers(type(inst), callable):
        meta: CommandMeta | None = getattr(member, COMMAND_ATTR, None)
        if meta is None:
            continue
        hints = get_type_hints(member)
        slot_types = {p: hints[p] for p in _param_names(member) if hints.get(p) in (int, float)}
        specs.append(
            CommandSpec(
                plugin=inst.name,
                name=meta.name or attr,
                patterns=meta.patterns,
                handler=_make_handler(inst, getattr(inst, attr)),
                slot_types=slot_types,
                dangerous=meta.dangerous,
                priority=meta.priority,
                description=meta.description or (inspect.getdoc(member) or "").split("\n")[0],
            )
        )
    return specs


def _make_handler(
    inst: Plugin, bound: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    async def handler(turn: Turn, **slots: Any) -> Any:
        return await bound(Context(turn, inst), **slots)

    return handler


def _collect_tools(inst: Plugin) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for attr, member in inspect.getmembers(type(inst), callable):
        meta: ToolMeta | None = getattr(member, TOOL_ATTR, None)
        if meta is not None:
            tools.append(build_tool(inst.name, getattr(inst, attr), meta))
    return tools
