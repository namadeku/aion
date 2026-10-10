"""Build edition: ``dev`` (private repo, full diagnostics) or ``public`` (installer, updates).

``scripts/release.py`` rewrites :data:`EDITION` to ``"public"`` when it exports the source
to the public repository; everything else is the same code.
"""

from __future__ import annotations

import sys
from typing import Literal

EDITION: Literal["dev", "public"] = "public"

# Releases (installer + updates) are published here.
PUBLIC_REPO = "namadeku/aion"


def is_public() -> bool:
    return EDITION == "public"


def is_installed() -> bool:
    """Running from the PyInstaller build (the installer puts Aion.exe there)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"
