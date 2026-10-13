"""Build scripts (PyInstaller / Inno Setup): the pure-Python parts and data-file coverage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "aion"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_script_{name}", ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_exe = _load("build_exe")
build_installer = _load("build_installer")


def _covered(rel: Path) -> bool:
    return any(rel == Path(source) or Path(source) in rel.parents for source, _ in build_exe.DATA)


def test_every_runtime_data_file_is_bundled() -> None:
    """A new YAML/JSON/asset under src/aion must be added to build_exe.DATA."""
    missing = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix not in {".py", ".pyc"}
        and "__pycache__" not in path.parts
        and not _covered(path.relative_to(SRC))
    ]
    assert missing == []


def test_data_sources_exist() -> None:
    for source, _ in build_exe.DATA:
        assert (SRC / source).exists(), source


def test_plugin_imports_are_third_party_only() -> None:
    found = build_exe.plugin_imports()
    assert found
    assert "aion" not in {m.split(".")[0] for m in found}


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.2.6", "(0, 2, 6, 0)"), ("1.0", "(1, 0, 0, 0)"), ("1.2.3.4", "(1, 2, 3, 4)")],
)
def test_version_info(version: str, expected: str) -> None:
    from PyInstaller.utils.win32 import versioninfo

    text = build_exe.version_info(version)
    assert f"filevers={expected}" in text
    assert f"StringStruct('FileVersion', '{version}')" in text
    # the same format PyInstaller evaluates for --version-file
    info = eval(text, dict(vars(versioninfo)))
    assert isinstance(info, versioninfo.VSVersionInfo)


def test_iscc_candidates_include_scoop(tmp_path: Path) -> None:
    env = {"USERPROFILE": str(tmp_path), "LOCALAPPDATA": str(tmp_path / "local")}
    paths = build_installer.iscc_candidates(env)
    assert tmp_path / "scoop" / "apps" / "inno-setup" / "current" / "ISCC.exe" in paths
    assert tmp_path / "local" / "Programs" / "Inno Setup 6" / "ISCC.exe" in paths


def test_find_iscc_explicit_and_scoop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert build_installer.find_iscc("X:/iscc.exe") == Path("X:/iscc.exe")
    iscc = tmp_path / "scoop" / "apps" / "inno-setup" / "current" / "ISCC.exe"
    iscc.parent.mkdir(parents=True)
    iscc.touch()
    monkeypatch.setattr(build_installer.shutil, "which", lambda _: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for name in ("SCOOP", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        monkeypatch.delenv(name, raising=False)
    assert build_installer.find_iscc(None) == iscc


def test_write_model_includes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_installer, "BUILD", tmp_path)
    build_installer.write_model_includes(
        [("https://h/a.onnx", "piper/a.onnx"), ("https://h/model.bin", "whisper-small/model.bin")]
    )
    code = (tmp_path / "models_code.iss").read_text("utf-8-sig")
    files = (tmp_path / "models_files.iss").read_text("utf-8-sig")
    assert "Page.Add('https://h/a.onnx', 'aion-model-1', '')" in code
    assert "FileExists(ModelsDir + '\\piper\\a.onnx')" in code
    assert 'DestDir: "{#ModelsDir}\\whisper-small"; DestName: "model.bin"' in files
    # model.bin is last: its presence marks a complete model
    assert files.strip().splitlines()[-1].endswith("uninsneveruninstall")
