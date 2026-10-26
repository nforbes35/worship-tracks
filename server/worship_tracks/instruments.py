"""The play-along tracks the app recreates, and how each one is transcribed and played back."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    id: str
    label: str
    # Which separated stem the AI listens to when transcribing this part.
    stem: str
    # General MIDI program (0-based) used to play the recreated part. Ignored for drums.
    program: int = 0
    is_drum: bool = False
    # One note at a time (bass lines, melodies) instead of chords.
    monophonic: bool = False
    min_hz: float | None = None
    max_hz: float | None = None
    # Basic Pitch thresholds: higher values keep fewer, more confident notes.
    onset_threshold: float = 0.5
    frame_threshold: float = 0.3
    min_note_ms: float = 100.0
    # Join repeats of the same pitch separated by less than this (seconds), for sustained sounds.
    merge_gap: float = 0.0
    # Stereo position in the player, -1 (left) to 1 (right).
    pan: float = 0.0


TRACKS: list[Track] = [
    Track("drums", "Drums", stem="drums", is_drum=True),
    Track("bass", "Bass", stem="bass", program=33, monophonic=True, min_hz=30, max_hz=400,
          onset_threshold=0.45, min_note_ms=80),
    Track("guitar", "Guitar", stem="guitar", program=25, min_hz=75, max_hz=1400, pan=-0.35),
    Track("keys", "Keys", stem="piano", program=0, min_hz=27.5, max_hz=4200, pan=0.3),
    Track("pads", "Pads & other", stem="other", program=89, min_hz=55, max_hz=2000,
          onset_threshold=0.55, frame_threshold=0.25, min_note_ms=150, merge_gap=0.35, pan=0.15),
    Track("melody", "Vocal melody", stem="vocals", program=73, monophonic=True, min_hz=80,
          max_hz=1100, onset_threshold=0.55, frame_threshold=0.35, min_note_ms=90),
]

TRACKS_BY_ID = {t.id: t for t in TRACKS}

# General MIDI drum notes.
KICK, SNARE, CLOSED_HAT, CRASH = 36, 38, 42, 49
