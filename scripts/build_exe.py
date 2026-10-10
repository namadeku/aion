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
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "aion"
ENTRY = ROOT / "build" / "aion_entry.py"


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
    from aion.ui.desktop import ensure_icon

    icon = ensure_icon(ROOT / "build" / "aion.ico")
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
        "--add-data", f"{SRC / 'plugins' / 'builtin'}{sep}aion/plugins/builtin",
        "--add-data", f"{SRC / 'ui' / 'static'}{sep}aion/ui/static",
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
        "--exclude-module", "torch",
        "--exclude-module", "tkinter",
    ]  # fmt: skip
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
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"\nГотово: {exe} ({size:.0f} МБ)")
    shutil.rmtree(ROOT / "build" / "pyinstaller", ignore_errors=True)


if __name__ == "__main__":
    main()
