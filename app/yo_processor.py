"""Module for restoring the letter 'ё' in Russian text before TTS.

Uses the yoditor library and its dictionary base yobase.
"""

import sys
from pathlib import Path

# Setup sys.path to access libs/yoditor.py
# app/yo_processor.py -> app/ -> project_root/ -> project_root/libs
BASE_DIR = Path(__file__).resolve().parents[1]
LIBS_DIR = BASE_DIR / "libs"

if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

try:
    import yoditor  # type: ignore
except ImportError:
    yoditor = None


def fix_yo_sure(text: str) -> str:
    """Restore all unambiguous cases of the letter 'ё'.

    Uses yoditor.recover_yo_sure, which:
    - finds words in the text that always contain 'ё' according to the dictionary;
    - replaces 'е' with 'ё' in them;
    - does not touch ambiguous cases (все/всё, etc.).
    """
    if not text or not yoditor:
        return text
    return yoditor.recover_yo_sure(text)
