"""Build the Windows installer: ``uv run python scripts/build_installer.py``.

Runs ``scripts/build_exe.py`` (PyInstaller, CPU only — CUDA is downloaded from the app) and
compiles ``installer/aion.iss`` with Inno Setup into ``dist/Aion-Setup-<version>.exe`` plus a
``.sha256`` file. The installer downloads the default voice models while it installs; their
list comes from :mod:`aion.models` (``build/models_*.iss``). The version always comes from
``aion.__version__``: the updater compares it with the release tag, so they must match.

Options:
  --skip-exe   reuse an existing dist/Aion (only recompile the installer)
  --iscc PATH  Inno Setup compiler (default: PATH, then the usual install folders)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def find_iscc(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if found := shutil.which("iscc"):
        return Path(found)
    bases = [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
    ]
    for base in filter(None, bases):
        for sub in (
            "Programs/Inno Setup 7",
            "Programs/Inno Setup 6",
            "Inno Setup 7",
            "Inno Setup 6",
        ):
            candidate = Path(base, sub, "ISCC.exe")
            if candidate.exists():
                return candidate
    sys.exit("Не найден Inno Setup (ISCC.exe): https://jrsoftware.org/isdl.php")


def default_models() -> list[tuple[str, str]]:
    """(url, path relative to the models dir) of what the default config needs on first start.

    ``model.bin`` of Whisper comes last: its presence marks a complete model (aion.models).
    """
    from aion import models
    from aion.config.schema import Config

    config = Config()
    files = [
        *models.silero_vad().files,
        *models.piper_voice(config.profile.voice.voice).files,
    ]
    result = [(f.url, f.dest.as_posix()) for f in files]
    name = "small" if config.stt.whisper_model == "auto" else config.stt.whisper_model
    repo = models.whisper_repo(name)
    target = models.whisper_dir(Path(), name).as_posix()
    for file, _ in asyncio.run(models.whisper_files(repo)):
        result.append((f"{models.HF_BASE}/{repo}/resolve/main/{file}", f"{target}/{file}"))
    return result


def write_model_includes(files: list[tuple[str, str]]) -> None:
    """Inno Setup snippets: the downloads (Pascal) and the copies into the models dir ([Files])."""
    code = [
        "procedure AddModelDownloads(Page: TDownloadWizardPage; ModelsDir: String);",
        "begin",
    ]
    entries = []
    for i, (url, dest) in enumerate(files, 1):
        temp = f"aion-model-{i}"
        path = dest.replace("/", "\\")
        folder, _, name = path.rpartition("\\")
        code += [
            f"  if not FileExists(ModelsDir + '\\{path}') then",
            f"    Page.Add('{url}', '{temp}', '');",
        ]
        dest_dir = "{#ModelsDir}" + (f"\\{folder}" if folder else "")
        entries.append(
            f'Source: "{{tmp}}\\{temp}";DestDir: "{dest_dir}"; DestName: "{name}"; '
            "Flags: external skipifsourcedoesntexist ignoreversion uninsneveruninstall"
        )
    code.append("end;")
    BUILD.mkdir(exist_ok=True)
    (BUILD / "models_code.iss").write_text("\n".join(code) + "\n", "utf-8-sig")
    (BUILD / "models_files.iss").write_text("\n".join(entries) + "\n", "utf-8-sig")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--skip-exe", action="store_true", help="reuse dist/Aion")
    parser.add_argument("--iscc", help="path to ISCC.exe")
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):  # CI consoles are often cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sys.path.insert(0, str(ROOT / "src"))
    from aion import __version__
    from aion.edition import EDITION

    iscc = find_iscc(args.iscc)
    if not args.skip_exe:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_exe.py")], check=True)
    if not (DIST / "Aion" / "Aion.exe").exists():
        sys.exit("Нет dist/Aion/Aion.exe — запустите без --skip-exe")

    files = default_models()
    write_model_includes(files)
    print(f"Inno Setup: Aion {__version__} ({EDITION}), моделей в установщике: {len(files)}…")
    subprocess.run(
        [
            str(iscc),
            "/Qp",
            f"/DAppVersion={__version__}",
            f"/DSourceDir={DIST / 'Aion'}",
            f"/DIconFile={ROOT / 'build' / 'aion.ico'}",
            f"/DOutputDir={DIST}",
            f"/DModelIncludes={BUILD}",
            str(ROOT / "installer" / "aion.iss"),
        ],
        check=True,
    )
    setup = DIST / f"Aion-Setup-{__version__}.exe"
    digest = sha256(setup)
    setup.with_name(setup.name + ".sha256").write_text(f"{digest}  {setup.name}\n", "utf-8")
    print(f"\nГотово: {setup} ({setup.stat().st_size / 1e6:.0f} МБ)\nSHA-256: {digest}")


if __name__ == "__main__":
    main()
