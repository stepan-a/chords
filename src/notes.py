"""Notes, accidentals, and pitches with enharmonic-aware spelling.

Music theory primer (just enough for the rest of the code):

- A *pitch class* is one of the 12 equal-tempered notes per octave, numbered
  0..11 with C=0. F# and Gb belong to the same pitch class (6) but are spelled
  differently. Two notes are *enharmonic* when they share a pitch class but
  use different letters.

- A *Note* in this module is a spelling: a letter A..G plus an accidental
  in [-2, +2] (double-flat to double-sharp). We never reduce to a pitch
  class internally — that would lose the spelling.

- A *Pitch* is a Note placed in an octave. We use scientific pitch notation
  (C4 = middle C, A4 = 440 Hz). MIDI value of C4 is 60.

Spelling matters for chord display: D major is {D, F#, A}, never {D, Gb, A}.
The :func:`spell_at` helper enforces this — pick the letter first (from the
chord degree), then derive the accidental that lands on the right pitch class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

# Letters in scale order, indexed 0..6.
LETTERS: tuple[str, ...] = ("C", "D", "E", "F", "G", "A", "B")
LETTER_INDEX: dict[str, int] = {letter: i for i, letter in enumerate(LETTERS)}

# Pitch class of each natural letter (C=0).
LETTER_PITCH_CLASS: dict[str, int] = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}

# Accidental rendering. We use ASCII (#, b) for input and display by default.
ACCIDENTAL_SYMBOLS: dict[int, str] = {
    -2: "bb",
    -1: "b",
    0: "",
    +1: "#",
    +2: "##",
}

# Unicode rendering (used by the SVG renderer later).
ACCIDENTAL_UNICODE: dict[int, str] = {
    -2: "\U0001D12B",  # 𝄫
    -1: "♭",      # ♭
    0: "",
    +1: "♯",      # ♯
    +2: "\U0001D12A",  # 𝄪
}


@dataclass(frozen=True, order=False)
class Note:
    """A note spelling: letter (C..B) and accidental (-2..+2)."""

    letter: str
    accidental: int = 0

    def __post_init__(self) -> None:
        if self.letter not in LETTER_INDEX:
            raise ValueError(f"invalid letter {self.letter!r}")
        if not -2 <= self.accidental <= 2:
            raise ValueError(f"accidental out of range: {self.accidental}")

    @property
    def pitch_class(self) -> int:
        """0..11, taking the accidental into account."""
        return (LETTER_PITCH_CLASS[self.letter] + self.accidental) % 12

    def __str__(self) -> str:
        return f"{self.letter}{ACCIDENTAL_SYMBOLS[self.accidental]}"

    def to_unicode(self) -> str:
        """Pretty form using ♭/♯/𝄫/𝄪."""
        return f"{self.letter}{ACCIDENTAL_UNICODE[self.accidental]}"

    def is_enharmonic(self, other: Note) -> bool:
        return self.pitch_class == other.pitch_class

    # --- Parsing -----------------------------------------------------------

    _PARSE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-G])(bb|##|[b#])?$"
    )

    @classmethod
    def parse(cls, text: str) -> Note:
        """Parse 'C', 'F#', 'Bb', 'G##', 'Ebb', etc."""
        m = cls._PARSE_RE.match(text)
        if not m:
            raise ValueError(f"could not parse note {text!r}")
        letter, acc = m.group(1), m.group(2) or ""
        accidental = {"": 0, "#": 1, "b": -1, "##": 2, "bb": -2}[acc]
        return cls(letter, accidental)


def spell_at(letter: str, target_pitch_class: int) -> Note:
    """Return the Note with the given *letter* whose pitch class matches.

    Used when a chord degree dictates the letter (e.g. the 3rd of D must be
    an E-something) but the chord quality dictates the pitch class
    (major 3rd = 4 semitones up, so E + 1 = F# — written as F#, not Gb).

    Raises ValueError if the required accidental would exceed double-sharp
    or double-flat (this only happens for theoretically valid but practically
    useless spellings, such as a major 3rd above E# being Gx).
    """
    natural = LETTER_PITCH_CLASS[letter]
    # Adjustment in semitones, wrapped to the nearest representative in [-6, 6].
    diff = (target_pitch_class - natural) % 12
    if diff > 6:
        diff -= 12
    if not -2 <= diff <= 2:
        raise ValueError(
            f"cannot spell pitch class {target_pitch_class} on letter {letter!r}"
            f" (would need accidental {diff:+d})"
        )
    return Note(letter, diff)


def letter_at_degree(root_letter: str, degree: int) -> str:
    """Return the letter at the given *degree* above the root.

    Degree 1 is the root, 3 is two letters up, 5 is four letters up,
    9 is the 2nd in the next octave (one letter past the root), etc.
    Compound degrees (9, 11, 13) collapse modulo 7 onto letters.
    """
    if degree < 1:
        raise ValueError(f"degree must be >= 1, got {degree}")
    offset = (degree - 1) % 7
    return LETTERS[(LETTER_INDEX[root_letter] + offset) % 7]


# --- Pitched notes (with octave) -----------------------------------------

# Semitones above C natural in the *same* written octave for each letter.
# This matters because scientific pitch notation increments the octave at C:
# B3 → C4 is one semitone up, but A3 → C4 spans the octave boundary.

@dataclass(frozen=True, order=False)
class Pitch:
    """A Note placed in a specific octave (scientific pitch notation)."""

    note: Note
    octave: int

    @property
    def midi(self) -> int:
        """MIDI number. C4 (middle C) = 60. A4 = 69."""
        return 12 * (self.octave + 1) + self.note.pitch_class

    def transpose(self, semitones: int) -> Pitch:
        """Move by *semitones*; keeps the result as a sharp spelling.

        Prefer :func:`spell_at` when you know the target letter — this method
        is for cases where you just need any spelling at a given MIDI value.
        """
        new_midi = self.midi + semitones
        return Pitch.from_midi(new_midi, prefer_sharps=True)

    @classmethod
    def from_midi(cls, midi: int, prefer_sharps: bool = True) -> Pitch:
        """Build a Pitch from a MIDI number with a default spelling."""
        pc = midi % 12
        octave = midi // 12 - 1
        # Default spellings: C, C#, D, D#, E, F, F#, G, G#, A, A#, B
        sharp_table = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        flat_table = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
        text = (sharp_table if prefer_sharps else flat_table)[pc]
        return cls(Note.parse(text), octave)

    def __str__(self) -> str:
        return f"{self.note}{self.octave}"

    # --- Parsing -----------------------------------------------------------

    _PARSE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-G](?:bb|##|[b#])?)(-?\d+)$"
    )

    @classmethod
    def parse(cls, text: str) -> Pitch:
        """Parse 'C4', 'F#3', 'Bb2', etc."""
        m = cls._PARSE_RE.match(text)
        if not m:
            raise ValueError(f"could not parse pitch {text!r}")
        note = Note.parse(m.group(1))
        return cls(note, int(m.group(2)))
