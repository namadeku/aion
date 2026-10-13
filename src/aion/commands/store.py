"""Command files on disk: ``<data_dir>/commands`` with folders, packs and ``state.json``.

A command id is its path relative to the commands folder, without ``.yaml`` and with ``/``:
``greet``, ``work/standup``, ``packs/media/pause``. Folders can be switched off as a whole;
the switch lives in ``state.json`` and the YAML files are not touched.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import ValidationError

from aion.commands.models import Command, Lang, PackMeta, explain, triggers_for

SUFFIX = ".yaml"
PACKS = "packs"
PACK_FILE = "pack.yaml"
STATE_FILE = "state.json"
EXAMPLES = "examples"
BUILTIN_PACKS = Path(__file__).parent / "packs"
BUILTIN_EXAMPLES = Path(__file__).parent / "examples"
MAX_FILE_BYTES = 256 << 10
MAX_DEPTH = 4  # "packs/<pack>/<folder>/<id>"
MAX_IMPORT_BYTES = 5 << 20
MAX_IMPORT_FILES = 500
_PART = re.compile(r"^[\w][\w\-. ]{0,63}$")  # Unicode letters: "работа/утро" is fine
#: first path segments the REST routes use (``/api/commands/export`` is not a command)
RESERVED = frozenset({"export", "import", "folders", "record"})


class CommandError(Exception):
    """A user-facing problem with a command file or an import (the text is Russian)."""


def check_id(command_id: str) -> str:
    """Validate a command id (``folder/name``) and return it normalized."""
    cid = command_id.strip().replace("\\", "/").strip("/")
    parts = cid.split("/")
    if not cid or len(parts) > MAX_DEPTH:
        raise CommandError(f"Неверный id команды «{command_id}»")
    for part in parts:
        if not _PART.fullmatch(part) or part.endswith((".", " ")) or ".." in part:
            raise CommandError(
                f"Неверный id «{command_id}»: буквы, цифры, пробел, _ - . и / между папками"
            )
    if parts[0].lower() in RESERVED:
        raise CommandError(f"Имя «{parts[0]}» занято, выберите другое")
    if parts[0] == PACKS and len(parts) < 3:
        raise CommandError("Команды пака лежат в packs/<пак>/<id>")
    if parts[-1].lower() == Path(PACK_FILE).stem:
        raise CommandError("Имя pack зарезервировано для описания пака")
    return cid


def folder_of(command_id: str) -> str:
    return command_id.rpartition("/")[0]


def pack_of(command_id: str) -> str | None:
    parts = command_id.split("/")
    return parts[1] if parts[0] == PACKS and len(parts) >= 3 else None


@dataclass
class Entry:
    id: str
    path: Path
    command: Command | None = None
    error: str | None = None

    @property
    def folder(self) -> str:
        return folder_of(self.id)


@dataclass
class State:
    disabled: list[str] = field(default_factory=list)
    packs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"disabled": sorted(set(self.disabled)), "packs": self.packs}


class CommandStore:
    def __init__(
        self,
        root: Path,
        *,
        builtin_packs: Path = BUILTIN_PACKS,
        examples: Path | None = BUILTIN_EXAMPLES,
    ) -> None:
        self.root = root
        self.builtin_packs = builtin_packs
        self.examples = examples
        self.entries: dict[str, Entry] = {}
        self.state = State()

    # -- loading --------------------------------------------------------------------------

    def ensure(self) -> None:
        """Create the folder; the very first time put the example scenarios into it."""
        if self.root.is_dir():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if self.examples is not None and self.examples.is_dir():
            shutil.copytree(self.examples, self.root / EXAMPLES, dirs_exist_ok=True)

    def load(self) -> None:
        self.state = self._read_state()
        entries: dict[str, Entry] = {}
        if self.root.is_dir():
            for path in sorted(self.root.rglob(f"*{SUFFIX}")):
                if path.name == PACK_FILE or not path.is_file():
                    continue
                rel = path.relative_to(self.root).with_suffix("").as_posix()
                try:
                    cid = check_id(rel)
                except CommandError as e:
                    logger.warning("Файл команды пропущен: {}", e)
                    continue
                entries[cid] = self._load_entry(cid, path)
        self.entries = entries
        bad = [e for e in entries.values() if e.error]
        for entry in bad:
            logger.warning("Команда {} с ошибкой: {}", entry.id, entry.error)

    @staticmethod
    def _load_entry(cid: str, path: Path) -> Entry:
        entry = Entry(cid, path)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise CommandError("файл слишком большой")
            entry.command = parse_command(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            entry.error = f"не читается: {e}"
        except CommandError as e:
            entry.error = str(e)
        return entry

    def _read_state(self) -> State:
        path = self.root / STATE_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return State()
        except (OSError, ValueError) as e:
            logger.warning("Не читается {}: {}", path, e)
            return State()
        disabled = [str(x) for x in raw.get("disabled", []) if isinstance(x, str)]
        packs = raw.get("packs", {})
        packs = {str(k): v for k, v in packs.items() if isinstance(v, dict)}
        return State(disabled, packs)

    def _write_state(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.root / STATE_FILE, json.dumps(self.state.to_json(), indent=2))

    # -- queries --------------------------------------------------------------------------

    def get(self, command_id: str) -> Entry | None:
        return self.entries.get(command_id)

    def find(self, reference: str) -> Entry | None:
        """An entry by id, or by its bare name when that name is unique (for ``run``)."""
        if entry := self.entries.get(reference.strip().strip("/")):
            return entry
        tail = reference.strip().rpartition("/")[2]
        hits = [e for e in self.entries.values() if e.id.rpartition("/")[2] == tail]
        return hits[0] if len(hits) == 1 else None

    def folder_enabled(self, folder: str) -> bool:
        disabled = set(self.state.disabled)
        parts = folder.split("/") if folder else []
        return not any("/".join(parts[:i]) + "/" in disabled for i in range(1, len(parts) + 1))

    def is_enabled(self, entry: Entry) -> bool:
        return (
            entry.command is not None
            and entry.command.enabled
            and entry.id not in self.state.disabled
            and self.folder_enabled(entry.folder)
        )

    def pack_lang(self, pack: str) -> Lang:
        chosen = self.state.packs.get(pack, {}).get("lang")
        if chosen in ("ru", "en", "both"):
            return chosen
        meta = self.pack_meta(self.root / PACKS / pack) or self.pack_meta(self.builtin_packs / pack)
        return meta.lang if meta else "both"

    def active_triggers(self, entry: Entry) -> list[str]:
        """The phrases to register for an enabled entry (a pack's language choice applies)."""
        if entry.command is None or not self.is_enabled(entry):
            return []
        pack = pack_of(entry.id)
        return triggers_for(entry.command, self.pack_lang(pack) if pack else "both")

    def folders(self) -> list[dict[str, Any]]:
        found: set[str] = set()
        if self.root.is_dir():
            for path in self.root.rglob("*"):
                if path.is_dir() and "__pycache__" not in path.parts:
                    rel = path.relative_to(self.root).as_posix()
                    if len(rel.split("/")) < MAX_DEPTH:
                        found.add(rel)
        return [{"path": f, "enabled": self.folder_enabled(f)} for f in sorted(found)]

    # -- editing --------------------------------------------------------------------------

    def path_for(self, command_id: str) -> Path:
        cid = check_id(command_id)
        path = (self.root / f"{cid}{SUFFIX}").resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise CommandError(f"Неверный id «{command_id}»")
        return path

    def save(self, command_id: str, command: Command) -> Entry:
        path = self.path_for(command_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, dump_command(command))
        entry = Entry(check_id(command_id), path, command)
        self.entries[entry.id] = entry
        return entry

    def delete(self, command_id: str) -> bool:
        entry = self.entries.pop(command_id, None)
        if entry is None:
            return False
        entry.path.unlink(missing_ok=True)
        if command_id in self.state.disabled:
            self.state.disabled.remove(command_id)
            self._write_state()
        return True

    def toggle(self, command_id: str, enabled: bool) -> None:
        entry = self.entries.get(command_id)
        if entry is None:
            raise KeyError(command_id)
        disabled = [d for d in self.state.disabled if d != command_id]
        if not enabled:
            disabled.append(command_id)
        elif entry.command is not None and not entry.command.enabled:
            # switched off in the file itself: turning it on rewrites that one field
            entry.command = entry.command.model_copy(update={"enabled": True})
            _atomic_write(entry.path, dump_command(entry.command))
        self.state.disabled = disabled
        self._write_state()

    def toggle_folder(self, folder: str, enabled: bool) -> None:
        clean = folder.strip().strip("/").replace("\\", "/")
        if not clean or not (self.root / clean).is_dir():
            raise KeyError(folder)
        mark = clean + "/"
        disabled = [d for d in self.state.disabled if d != mark]
        if not enabled:
            disabled.append(mark)
        self.state.disabled = disabled
        self._write_state()

    # -- packs ----------------------------------------------------------------------------

    @staticmethod
    def pack_meta(folder: Path) -> PackMeta | None:
        try:
            data = yaml.safe_load((folder / PACK_FILE).read_text(encoding="utf-8"))
            return PackMeta.model_validate(data)
        except (OSError, yaml.YAMLError, ValidationError):
            return None

    def packs(self) -> list[dict[str, Any]]:
        names: set[str] = set()
        for base in (self.builtin_packs, self.root / PACKS):
            if base.is_dir():
                names |= {p.name for p in base.iterdir() if (p / PACK_FILE).is_file()}
        out: list[dict[str, Any]] = []
        for name in sorted(names):
            installed = self.root / PACKS / name
            is_installed = (installed / PACK_FILE).is_file()
            meta = self.pack_meta(installed if is_installed else self.builtin_packs / name)
            if meta is None:
                continue
            prefix = f"{PACKS}/{name}/"
            out.append(
                {
                    "name": name,
                    "title": meta.name,
                    "description": meta.description,
                    "lang": meta.lang,
                    "selected_lang": self.pack_lang(name),
                    "builtin": (self.builtin_packs / name / PACK_FILE).is_file(),
                    "installed": is_installed,
                    "enabled": is_installed and self.folder_enabled(f"{PACKS}/{name}"),
                    "commands": sum(1 for cid in self.entries if cid.startswith(prefix)),
                }
            )
        return out

    def set_pack(self, name: str, enabled: bool, lang: Lang | None = None) -> None:
        if not _PART.fullmatch(name) or ".." in name:
            raise KeyError(name)
        installed = self.root / PACKS / name
        source = self.builtin_packs / name
        if enabled and not (installed / PACK_FILE).is_file():
            if not (source / PACK_FILE).is_file():
                raise KeyError(name)
            shutil.copytree(source, installed, dirs_exist_ok=True)
        elif not (installed / PACK_FILE).is_file() and not (source / PACK_FILE).is_file():
            raise KeyError(name)
        if lang is not None:
            self.state.packs.setdefault(name, {})["lang"] = lang
        mark = f"{PACKS}/{name}/"
        disabled = [d for d in self.state.disabled if d != mark]
        if not enabled and (installed / PACK_FILE).is_file():
            disabled.append(mark)
        self.state.disabled = disabled
        self._write_state()

    # -- import / export ------------------------------------------------------------------

    def import_file(self, filename: str, data: bytes) -> tuple[list[str], list[str]]:
        """Import a ``.yaml`` command or a ``.zip`` of them. Returns (ids, errors)."""
        if len(data) > MAX_IMPORT_BYTES:
            raise CommandError("Файл слишком большой")
        lowered = filename.lower()
        if lowered.endswith((".yaml", ".yml")):
            stem = Path(filename.replace("\\", "/")).stem
            try:
                command = parse_command(data.decode("utf-8"))
            except UnicodeDecodeError as e:
                raise CommandError("Файл не в UTF-8") from e
            notes: list[str] = []
            cid = self._free_id(_safe_stem(stem))
            self.save(cid, _guarded(command, filename, notes))
            return [cid], notes
        if lowered.endswith(".zip"):
            return self._import_zip(data)
        raise CommandError("Импортируются файлы .yaml и .zip")

    def _import_zip(self, data: bytes) -> tuple[list[str], list[str]]:
        from aion.plugins.installer import (  # pyright: ignore[reportPrivateUsage]
            InstallError,
            _safe_extract,
        )

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if len(zf.infolist()) > MAX_IMPORT_FILES:
                    raise CommandError("В архиве слишком много файлов")
        except zipfile.BadZipFile as e:
            raise CommandError("Архив повреждён или это не zip-файл") from e
        imported: list[str] = []
        errors: list[str] = []
        with tempfile.TemporaryDirectory(prefix="aion-commands-") as tmp:
            archive = Path(tmp) / "import.zip"
            archive.write_bytes(data)
            unpacked = Path(tmp) / "unpacked"
            unpacked.mkdir()
            try:
                _safe_extract(archive, unpacked)  # no path escapes, no links, bounded size
            except InstallError as e:
                raise CommandError(str(e)) from e
            for path in sorted(unpacked.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                rel = path.relative_to(unpacked).as_posix()
                if path.name == PACK_FILE:
                    self._import_pack_file(path, rel, errors)
                    continue
                if path.suffix.lower() not in (".yaml", ".yml"):
                    continue
                try:
                    cid = check_id(rel.rsplit(".", 1)[0])
                    if path.stat().st_size > MAX_FILE_BYTES:
                        raise CommandError("файл слишком большой")
                    command = parse_command(path.read_text(encoding="utf-8"))
                    cid = self._free_id(cid)
                    self.save(cid, _guarded(command, rel, errors))
                    imported.append(cid)
                except (CommandError, UnicodeDecodeError, OSError) as e:
                    errors.append(f"{rel}: {e}")
        return imported, errors

    def _import_pack_file(self, path: Path, rel: str, errors: list[str]) -> None:
        parts = rel.split("/")
        if len(parts) != 3 or parts[0] != PACKS or not _PART.fullmatch(parts[1]):
            errors.append(f"{rel}: pack.yaml бывает только в packs/<пак>/")
            return
        if self.pack_meta(path.parent) is None:
            errors.append(f"{rel}: неверное описание пака")
            return
        target = self.root / PACKS / parts[1] / PACK_FILE
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def export(self, ids: list[str] | None = None) -> bytes:
        """A zip of the chosen commands (all when ``ids`` is empty), paths kept."""
        chosen = (
            [self.entries[i] for i in ids if i in self.entries] if ids else self.entries.values()
        )
        buffer = io.BytesIO()
        packs: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in chosen:
                if entry.path.is_file():
                    zf.write(entry.path, f"{entry.id}{SUFFIX}")
                if pack := pack_of(entry.id):
                    packs.add(pack)
            for pack in sorted(packs):
                meta = self.root / PACKS / pack / PACK_FILE
                if meta.is_file():
                    zf.write(meta, f"{PACKS}/{pack}/{PACK_FILE}")
        return buffer.getvalue()

    def _free_id(self, cid: str) -> str:
        """``cid`` or ``cid-2``, ``cid-3``... — imports never overwrite existing commands."""
        candidate, n = cid, 1
        while candidate in self.entries or self.path_for(candidate).exists():
            n += 1
            candidate = f"{cid}-{n}"
        return candidate


def parse_command(text: str) -> Command:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise CommandError(f"ошибка YAML: {e}") from e
    if not isinstance(data, dict):
        raise CommandError("в файле должна быть команда: name, triggers, actions")
    try:
        return Command.model_validate(data)
    except ValidationError as e:
        raise CommandError("; ".join(explain(e))) from e


def _guarded(command: Command, name: str, notes: list[str]) -> Command:
    """A file from elsewhere must not run dangerous actions without asking."""
    if command.dangerous and command.confirm == "never":
        notes.append(f"{name}: для опасных действий включено подтверждение (confirm: auto)")
        return command.model_copy(update={"confirm": "auto"})
    return command


def dump_command(command: Command) -> str:
    return yaml.safe_dump(
        command.to_yaml_data(), allow_unicode=True, sort_keys=False, default_flow_style=None
    )


def _safe_stem(stem: str) -> str:
    clean = re.sub(r"[^\w\-. ]", "_", stem).strip(" .") or "command"
    clean = clean[:64]
    with contextlib.suppress(CommandError):
        return check_id(clean)
    return f"cmd-{clean}"[:64]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
