"""Version helper."""

from __future__ import annotations

import sys
from pathlib import Path

# IMPORTANT: Update this constant when releasing a new version!
# This is used in the compiled .exe, while VERSION file is used only in dev mode.
FROZEN_VERSION = "1.7.0"


def _read_version() -> str:
    # In frozen (compiled) mode, use hardcoded version
    if getattr(sys, 'frozen', False):
        return FROZEN_VERSION
    
    # In development mode, read from VERSION file
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return "0.0.0"


__version__ = _read_version()
