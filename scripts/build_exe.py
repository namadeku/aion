"""Build a Windows executable with PyInstaller: ``uv run python scripts/build_exe.py``.

Produces ``dist/Aion/Aion.exe`` (one folder, no console window). Models are not bundled —
they are downloaded on first start into %LOCALAPPDATA%\\Aion, like in the source install.

Options:
  --cuda      include cuBLAS/cuDNN for Whisper on NVIDIA GPUs (+~1 GB)
  --console   keep a console window (useful for debugging)

Licensing note: the build bundles piper-tts (GPL-3.0); distribute it under GPL-compatible terms.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "aion"
ENTRY = ROOT / "build" / "aion_entry.py"

# Files the app reads at runtime (not imports): (source in src/aion, folder inside the bundle).
# tests/test_build_scripts.py checks that every non-Python file under src/aion is covered.
DATA: list[tuple[str, str]] = [
    ("plugins/builtin", "aion/plugins/builtin"),
    ("ui/static", "aion/ui/static"),
    ("commands/examples", "aion/commands/examples"),
    ("commands/packs", "aion/commands/packs"),
    ("tts/presets.yaml", "aion/tts"),
]

# Never needed by the assistant (+~35 MB): the Hebrew and Arabic-diacritics models of piper's
# phonemizer (only loaded for he/ar voices), removed after the build.
PRUNE = [
    "piper/hebrew/nakdimon.onnx",
    "piper/tashkeel/model.onnx",
]
EXCLUDED_MODULES = [
    "torch", "tkinter", "PIL._avif", "pip", "setuptools", "pkg_resources", "_distutils_hack",
]  # fmt: skip


def plugin_imports() -> list[str]:
    """Modules imported by the builtin plugins.

    Plugins are loaded from files at runtime, so PyInstaller never sees their imports
    (pycaw, comtypes, winrt, PIL.ImageGrab...) unless they are listed as hidden imports.
    """
    found: set[str] = set()
    for path in (SRC / "plugins" / "builtin").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module)
                # "from PIL import ImageGrab": ImageGrab may be a submodule
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return sorted(
        m for m in found if m.split(".")[0] not in {"aion", "__future__"} and _is_module(m)
    )


def version_info(version: str) -> str:
    """PyInstaller ``--version-file`` text: what Explorer shows in the exe properties."""
    nums = [int(p) for p in re.findall(r"\d+", version)[:4]]
    nums += [0] * (4 - len(nums))
    tup = tuple(nums)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004,
    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'namadeku'),
      StringStruct('FileDescription', 'Aion voice assistant'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Aion'),
      StringStruct('LegalCopyright', 'MIT License; bundles piper-tts (GPL-3.0)'),
      StringStruct('OriginalFilename', 'Aion.exe'),
      StringStruct('ProductName', 'Aion'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # "pathlib.Path": the parent is not a package
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--cuda", action="store_true", help="bundle CUDA libraries for Whisper")
    parser.add_argument("--console", action="store_true", help="show a console window")
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):  # CI consoles are often cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not (SRC / "ui" / "static" / "index.html").exists():
        sys.exit("Интерфейс не собран: cd frontend && pnpm install && pnpm build")

    ENTRY.parent.mkdir(exist_ok=True)
    ENTRY.write_text(
        "import sys\n"
        "from aion.cli import main\n\n"
        "if len(sys.argv) == 1:\n"
        "    sys.argv.append('run')  # double-click starts the assistant\n"
        "main()\n",
        encoding="utf-8",
    )

    sys.path.insert(0, str(ROOT / "src"))
    from aion import __version__
    from aion.ui.desktop import ensure_icon

    icon = ensure_icon(ROOT / "build" / "aion.ico")
    version_file = ROOT / "build" / "version_info.txt"
    version_file.write_text(version_info(__version__), encoding="utf-8")
    sep = ";"  # Windows PyInstaller data separator
    cmd = [
        sys.executable, "-m", "PyInstaller", str(ENTRY),
        "--name", "Aion",
        "--icon", str(icon),
        "--noconfirm",
        "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--additional-hooks-dir", str(ROOT / "scripts" / "pyinstaller_hooks"),
        # plugins and the web UI are loaded from files at runtime
        "--version-file", str(version_file),
        "--collect-submodules", "aion",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "num2words",
        "--collect-data", "piper",
        "--collect-binaries", "piper",
        "--collect-data", "faster_whisper",
        "--collect-binaries", "ctranslate2",
        "--collect-binaries", "vosk",
        "--collect-binaries", "onnxruntime",
        "--collect-all", "webview",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--hidden-import", "pystray._win32",
    ]  # fmt: skip
    for source, target in DATA:
        cmd += ["--add-data", f"{SRC / source}{sep}{target}"]
    for module in EXCLUDED_MODULES:
        cmd += ["--exclude-module", module]
    for module in plugin_imports():
        cmd += ["--hidden-import", module]
    if args.cuda:
        cmd += ["--collect-binaries", "nvidia"]
    else:
        cmd += ["--exclude-module", "nvidia"]
    if not args.console:
        cmd.append("--windowed")

    print(" ".join(cmd[3:6]), "…")
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / "Aion" / "Aion.exe"
    for name in PRUNE:
        (exe.parent / "_internal" / name).unlink(missing_ok=True)
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"\nГотово: {exe} ({size:.0f} МБ)")
    shutil.rmtree(ROOT / "build" / "pyinstaller", ignore_errors=True)


if __name__ == "__main__":
    main()
