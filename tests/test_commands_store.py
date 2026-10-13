"""Command files: loading, enable/disable state, packs, import and export."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from aion.commands.models import Command
from aion.commands.store import BUILTIN_PACKS, CommandError, CommandStore, check_id

SAY = "name: {name}\ntriggers: [{trigger}]\nactions:\n  - {{type: say, text: привет}}\n"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path) -> CommandStore:
    return CommandStore(tmp_path / "commands")


def test_first_start_copies_the_examples(store: CommandStore) -> None:
    store.ensure()
    store.load()
    examples = {cid for cid in store.entries if cid.startswith("examples/")}
    assert {"examples/guests", "examples/focus", "examples/short-break"} <= examples
    assert all(store.entries[cid].error is None for cid in examples)
    guests = store.entries["examples/guests"].command
    assert guests is not None
    assert [a.type for a in guests.actions][:2] == ["minimize_all", "volume"]
    assert not any(store.entries[cid].command.dangerous for cid in examples)  # type: ignore[union-attr]
    # a second start keeps the user's changes: nothing is copied again
    (store.root / "examples" / "guests.yaml").unlink()
    store.ensure()
    assert not (store.root / "examples" / "guests.yaml").exists()


def test_load_ids_folders_and_broken_files(store: CommandStore) -> None:
    write(store.root, "hello.yaml", SAY.format(name="Привет", trigger="привет"))
    write(store.root, "work/standup.yaml", SAY.format(name="Стендап", trigger="стендап"))
    write(store.root, "work/broken.yaml", "name: [")
    write(store.root, "work/invalid.yaml", "name: X\nactions: [{type: fly}]\n")
    write(store.root, "notes.txt", "not a command")
    store.load()
    assert set(store.entries) == {"hello", "work/standup", "work/broken", "work/invalid"}
    assert store.entries["work/standup"].folder == "work"
    assert "YAML" in (store.entries["work/broken"].error or "")
    assert "неизвестный тип действия «fly»" in (store.entries["work/invalid"].error or "")
    assert store.active_triggers(store.entries["work/broken"]) == []
    assert {"path": "work", "enabled": True} in store.folders()


def test_state_disables_commands_and_folders_without_touching_yaml(store: CommandStore) -> None:
    hello = write(store.root, "hello.yaml", SAY.format(name="Привет", trigger="привет"))
    write(store.root, "work/standup.yaml", SAY.format(name="Стендап", trigger="стендап"))
    store.load()
    before = hello.read_text(encoding="utf-8")

    store.toggle("hello", False)
    store.toggle_folder("work", False)
    state = json.loads((store.root / "state.json").read_text(encoding="utf-8"))
    assert sorted(state["disabled"]) == ["hello", "work/"]
    assert hello.read_text(encoding="utf-8") == before

    store.load()  # the state survives a reload
    assert not store.is_enabled(store.entries["hello"])
    assert not store.is_enabled(store.entries["work/standup"])
    assert {"path": "work", "enabled": False} in store.folders()

    store.toggle("hello", True)
    store.toggle_folder("work", True)
    assert store.is_enabled(store.entries["hello"])
    assert store.is_enabled(store.entries["work/standup"])
    with pytest.raises(KeyError):
        store.toggle("missing", True)
    with pytest.raises(KeyError):
        store.toggle_folder("missing", True)


def test_enabling_a_command_switched_off_in_its_file(store: CommandStore) -> None:
    write(store.root, "x.yaml", "name: X\nenabled: false\nactions: [{type: say, text: a}]\n")
    store.load()
    assert not store.is_enabled(store.entries["x"])
    store.toggle("x", True)
    store.load()
    assert store.is_enabled(store.entries["x"])


def test_save_and_delete(store: CommandStore) -> None:
    command = Command.model_validate(
        {"name": "Утро", "triggers": ["доброе утро"], "actions": [{"type": "wait", "seconds": 1}]}
    )
    entry = store.save("routine/morning", command)
    assert entry.path == (store.root / "routine" / "morning.yaml").resolve()
    text = entry.path.read_text(encoding="utf-8")
    assert "доброе утро" in text  # UTF-8, not \u escapes
    store.load()
    assert store.entries["routine/morning"].command == command
    assert store.delete("routine/morning")
    assert not entry.path.exists()
    assert not store.delete("routine/morning")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../evil",
        "a/../../b",
        "export",
        "folders/x",
        "a/b/c/d/e",
        "packs/x",
        "x/pack",
        ".hidden",
    ],
)
def test_bad_ids_are_refused(bad: str) -> None:
    with pytest.raises(CommandError):
        check_id(bad)


def test_unicode_ids_are_fine() -> None:
    assert check_id("работа/утро") == "работа/утро"
    assert check_id("\\games\\aim\\") == "games/aim"


def test_packs_install_on_first_enable_and_choose_a_language(store: CommandStore) -> None:
    store.ensure()
    packs = {p["name"]: p for p in store.packs()}
    assert {"work", "games", "media"} <= set(packs)
    assert not packs["games"]["installed"]
    assert "античит" in packs["games"]["description"]

    store.set_pack("games", True, "en")
    store.load()
    assert (store.root / "packs" / "games" / "pack.yaml").is_file()
    entries = [e for e in store.entries.values() if e.id.startswith("packs/games/")]
    assert entries
    for entry in entries:
        assert entry.command is not None
        assert entry.command.lang == "both"
        triggers = store.active_triggers(entry)
        assert triggers
        assert all(not any("а" <= c <= "я" for c in t.lower()) for t in triggers)

    store.set_pack("games", True, "ru")
    assert all(any("а" <= c <= "я" for c in t) for e in entries for t in store.active_triggers(e))

    store.set_pack("games", False)
    assert all(store.active_triggers(e) == [] for e in entries)
    assert {p["name"]: p for p in store.packs()}["games"]["enabled"] is False
    with pytest.raises(KeyError):
        store.set_pack("nope", True)


def test_builtin_packs_are_valid_and_safe() -> None:
    for pack in ("work", "games", "media"):
        meta = CommandStore.pack_meta(BUILTIN_PACKS / pack)
        assert meta is not None
        files = [p for p in (BUILTIN_PACKS / pack).glob("*.yaml") if p.name != "pack.yaml"]
        assert len(files) >= 3
        for path in files:
            entry = CommandStore._load_entry(path.stem, path)  # pyright: ignore[reportPrivateUsage]
            assert entry.error is None, (path, entry.error)
            assert entry.command is not None
            assert not entry.command.dangerous, path
            langs = {
                ("ru" if any("а" <= c <= "я" for c in t) else "en") for t in entry.command.triggers
            }
            assert langs == {"ru", "en"}, path


# -- import / export ---------------------------------------------------------------------------


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def test_import_yaml_never_overwrites(store: CommandStore) -> None:
    store.ensure()
    store.load()
    data = SAY.format(name="Привет", trigger="привет").encode()
    assert store.import_file("hello.yaml", data) == (["hello"], [])
    assert store.import_file("hello.yaml", data) == (["hello-2"], [])
    with pytest.raises(CommandError, match="действия"):
        store.import_file("bad.yaml", b"name: X\n")
    with pytest.raises(CommandError, match=r"\.yaml и \.zip"):
        store.import_file("hello.exe", data)


def test_import_zip_keeps_folders_and_reports_bad_files(store: CommandStore) -> None:
    store.ensure()
    store.load()
    archive = make_zip(
        {
            "work/standup.yaml": SAY.format(name="Стендап", trigger="стендап"),
            "work/bad.yaml": "name: X\n",
            "packs/mine/pack.yaml": "name: Мой пак\nlang: ru\n",
            "packs/mine/hi.yaml": SAY.format(name="Хай", trigger="хай"),
            "readme.md": "ignored",
        }
    )
    imported, errors = store.import_file("set.zip", archive)
    assert sorted(imported) == ["packs/mine/hi", "work/standup"]
    assert len(errors) == 1
    assert errors[0].startswith("work/bad.yaml")
    assert (store.root / "packs" / "mine" / "pack.yaml").is_file()


@pytest.mark.parametrize(
    "evil", ["../evil.yaml", "/abs/evil.yaml", "C:/evil.yaml", "a/../../evil.yaml"]
)
def test_zip_slip_is_refused(store: CommandStore, tmp_path: Path, evil: str) -> None:
    store.ensure()
    archive = make_zip({evil: SAY.format(name="Зло", trigger="зло")})
    with pytest.raises(CommandError, match="Небезопасный путь"):
        store.import_file("evil.zip", archive)
    assert not (tmp_path / "evil.yaml").exists()
    assert not list(tmp_path.rglob("evil.yaml"))


def test_imported_dangerous_commands_always_ask(store: CommandStore) -> None:
    store.ensure()
    store.load()
    text = "name: X\nconfirm: never\nactions: [{type: shell, command: del x}]\n"
    imported, notes = store.import_file("x.yaml", text.encode())
    assert len(notes) == 1
    assert "подтверждение" in notes[0]
    store.load()
    command = store.entries[imported[0]].command
    assert command is not None
    assert command.confirm == "auto"
    archive = make_zip(
        {"a/x.yaml": text, "a/safe.yaml": text.replace("shell, command: del x", "minimize_all")}
    )
    imported, notes = store.import_file("set.zip", archive)
    assert len(imported) == 2
    assert [n.split(":")[0] for n in notes] == ["a/x.yaml"]


def test_broken_zip(store: CommandStore) -> None:
    with pytest.raises(CommandError, match="zip"):
        store.import_file("x.zip", b"not a zip")


def test_export_round_trip(store: CommandStore, tmp_path: Path) -> None:
    store.ensure()
    store.set_pack("media", True)
    store.load()
    data = store.export(["examples/guests", "packs/media/rewind", "missing"])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == [
            "examples/guests.yaml",
            "packs/media/pack.yaml",
            "packs/media/rewind.yaml",
        ]
    other = CommandStore(tmp_path / "other", examples=None)
    other.ensure()
    other.load()
    imported, errors = other.import_file("export.zip", data)
    assert sorted(imported) == ["examples/guests", "packs/media/rewind"]
    assert errors == []
    with zipfile.ZipFile(io.BytesIO(store.export())) as zf:  # everything
        assert len(zf.namelist()) > 3
