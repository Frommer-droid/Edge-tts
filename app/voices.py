"""Default voice options to populate the UI selector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class VoiceOption:
    label: str
    voice_id: str


VOICE_CHOICES: List[VoiceOption] = [
    VoiceOption("Russian – Dmitry (male)", "ru-RU-DmitryNeural"),
    VoiceOption("Russian – Svetlana (female)", "ru-RU-SvetlanaNeural"),
    VoiceOption("English – Jenny (female)", "en-US-JennyNeural"),
    VoiceOption("English – Guy (male)", "en-US-GuyNeural"),
    VoiceOption("English – Aria (female)", "en-US-AriaNeural"),
]


def find_voice_index(voice_id: str) -> int:
    """Return the index of a voice id in VOICE_CHOICES or 0 as a fallback."""
    for idx, voice in enumerate(VOICE_CHOICES):
        if voice.voice_id == voice_id:
            return idx
    return 0
