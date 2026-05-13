"""Guitar tunings — pitch of each open string.

Convention used throughout the code:

  - A tuning is a tuple of :class:`notes.Pitch`, **ordered from the lowest
    pitch (6th string, played by the thumb side) to the highest pitch
    (1st string)**.  Index 0 is the low E on a standard-tuned 6-string.

  - When drawing a chord diagram vertically (the most common style for
    didactic charts), the convention is the opposite: the 6th string is on
    the LEFT and the 1st string on the RIGHT. The renderer is responsible
    for the flip; voicing search and the public API work in the
    low-to-high order. This makes interval arithmetic between adjacent
    strings natural ("the string above" = the next index).

Open tunings are just preset Tunings; nothing else in the system needs to
change to support them, which is the whole point of computing voicings
rather than hardcoding chord shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from notes import Note, Pitch


@dataclass(frozen=True)
class Tuning:
    """An ordered sequence of open-string pitches, low to high."""

    name: str
    strings: tuple[Pitch, ...]

    def __post_init__(self) -> None:
        if len(self.strings) < 2:
            raise ValueError("a tuning needs at least 2 strings")
        # Sanity check: strings should be ordered from low to high.
        # We allow ties (which never happen in practice but are not invalid).
        midi = [s.midi for s in self.strings]
        if midi != sorted(midi):
            raise ValueError(
                f"tuning {self.name!r}: strings must be ordered low-to-high, "
                f"got MIDI {midi}"
            )

    @property
    def num_strings(self) -> int:
        return len(self.strings)

    def pitch_at(self, string: int, fret: int) -> Pitch:
        """Pitch produced by pressing *string* at *fret*.

        ``string`` is the low-to-high index (0 = lowest). ``fret`` 0 means
        the open string; negative frets are not allowed.
        """
        if not 0 <= string < self.num_strings:
            raise ValueError(f"string {string} out of range")
        if fret < 0:
            raise ValueError(f"fret {fret} must be non-negative")
        return self.strings[string].transpose(fret)

    def note_at(self, string: int, fret: int) -> Note:
        """Just the note (no octave). Cheap shortcut for voicing search."""
        return self.pitch_at(string, fret).note

    def open_pitch_classes(self) -> tuple[int, ...]:
        return tuple(s.note.pitch_class for s in self.strings)

    @classmethod
    def from_string(cls, name: str, pitches: str) -> "Tuning":
        """Build a tuning from a space-separated string like ``"E2 A2 D3 G3 B3 E4"``."""
        strings = tuple(Pitch.parse(tok) for tok in pitches.split())
        return cls(name=name, strings=strings)


# --- Preset tunings -----------------------------------------------------
#
# Listed low-to-high. The string-numbering convention guitarists use
# (1 = highest, 6 = lowest) is reversed at display time.

STANDARD = Tuning.from_string("Standard", "E2 A2 D3 G3 B3 E4")
"""Standard 6-string tuning: E A D G B E."""

DROP_D = Tuning.from_string("Drop D", "D2 A2 D3 G3 B3 E4")
"""Drop the 6th string from E to D — keeps everything else standard."""

DROP_C = Tuning.from_string("Drop C", "C2 G2 C3 F3 A3 D4")
"""Whole step down + dropped 6th."""

DADGAD = Tuning.from_string("DADGAD", "D2 A2 D3 G3 A3 D4")
"""Celtic / fingerstyle tuning."""

OPEN_G = Tuning.from_string("Open G", "D2 G2 D3 G3 B3 D4")
"""Open G major (Keith Richards' favorite)."""

OPEN_D = Tuning.from_string("Open D", "D2 A2 D3 F#3 A3 D4")
"""Open D major — common slide tuning."""

OPEN_E = Tuning.from_string("Open E", "E2 B2 E3 G#3 B3 E4")
"""Open E major — same intervals as Open D, a tone higher."""

OPEN_C = Tuning.from_string("Open C", "C2 G2 C3 G3 C4 E4")
"""Open C major — popular with John Fahey, Devin Townsend."""

HALF_STEP_DOWN = Tuning.from_string("Eb Standard", "Eb2 Ab2 Db3 Gb3 Bb3 Eb4")
"""Standard tuned down a half step (Hendrix, SRV)."""

WHOLE_STEP_DOWN = Tuning.from_string("D Standard", "D2 G2 C3 F3 A3 D4")
"""Standard tuned down a whole step."""


ALL_TUNINGS: tuple[Tuning, ...] = (
    STANDARD,
    DROP_D,
    DROP_C,
    DADGAD,
    OPEN_G,
    OPEN_D,
    OPEN_E,
    OPEN_C,
    HALF_STEP_DOWN,
    WHOLE_STEP_DOWN,
)


def by_name(name: str) -> Tuning:
    """Look up a preset tuning by name (case-insensitive)."""
    key = name.strip().lower()
    for t in ALL_TUNINGS:
        if t.name.lower() == key:
            return t
    raise KeyError(f"no preset tuning named {name!r}")
