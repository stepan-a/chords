"""Chord symbols, parsing, and chord-tone generation.

A chord is represented as:
  - a *root* (a :class:`notes.Note`),
  - a list of *intervals* as ``(degree, alteration)`` pairs,
  - an optional *bass* note (for slash chords like ``C/E``).

A degree is one of 1..7 or 9, 11, 13 (compound). Alteration is an int in
[-2, +2] applied on top of the major-scale interval for that degree:

  degree    natural semitones from root
  ------    ---------------------------
    1                  0
    2                  2
    3                  4   (major 3rd)
    4                  5
    5                  7   (perfect 5th)
    6                  9
    7                 11   (major 7th)
    9                 14
   11                 17
   13                 21

So a minor 3rd is ``(3, -1)``, a minor 7th ``(7, -1)``, a diminished 7th
``(7, -2)``, a sharp eleventh ``(11, +1)``, etc.

The chord-tone generator picks the *letter* from the degree (one letter per
degree, so a triad never reuses a letter) and the *accidental* from the pitch
class — which is what makes spelling come out right: D major is {D, F#, A},
not {D, Gb, A}.

The parser accepts the anglo-saxon forms most commonly seen on lead sheets:

  C  Cm  Cmin  C-  Cdim  C°  Caug  C+  Csus2  Csus4
  C5  C6  Cm6  C7  Cmaj7  CM7  CΔ  Cm7  CmMaj7  Cdim7  C°7
  Cm7b5  Cø  Cø7  Cadd9  Cmadd9
  C9  Cmaj9  Cm9  C11  Cmaj11  Cm11  C13  Cmaj13  Cm13
  C7b5  C7#5  C7b9  C7#9  C7#11  C7b13  C7#5#9   (combined alterations)
  C/E  Db/F                                       (slash chords)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from notes import (
    LETTERS,
    Note,
    letter_at_degree,
    spell_at,
)

# --- Interval table ------------------------------------------------------

NATURAL_DEGREE_SEMITONES: dict[int, int] = {
    1: 0,
    2: 2,
    3: 4,
    4: 5,
    5: 7,
    6: 9,
    7: 11,
    9: 14,
    11: 17,
    13: 21,
}


# --- Base quality definitions -------------------------------------------
#
# Each entry maps a *suffix* (what follows the root, before any modifiers
# or slash bass) to the chord's interval set as ``(degree, alteration)``
# tuples. The order matters: the parser performs a longest-prefix match,
# so longer/more specific symbols come first.
#
# Equivalent symbols (``maj7`` vs ``M7`` vs ``Δ``) are listed separately
# to keep parsing strictly textual and explicit.

_TRIAD_MAJ = ((1, 0), (3, 0), (5, 0))
_TRIAD_MIN = ((1, 0), (3, -1), (5, 0))
_TRIAD_DIM = ((1, 0), (3, -1), (5, -1))
_TRIAD_AUG = ((1, 0), (3, 0), (5, 1))
_TRIAD_SUS2 = ((1, 0), (2, 0), (5, 0))
_TRIAD_SUS4 = ((1, 0), (4, 0), (5, 0))

BASE_QUALITIES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    # 13ths
    ("maj13", _TRIAD_MAJ + ((7, 0), (9, 0), (13, 0))),
    ("Maj13", _TRIAD_MAJ + ((7, 0), (9, 0), (13, 0))),
    ("M13",   _TRIAD_MAJ + ((7, 0), (9, 0), (13, 0))),
    ("Δ13",   _TRIAD_MAJ + ((7, 0), (9, 0), (13, 0))),
    ("m13",   _TRIAD_MIN + ((7, -1), (9, 0), (11, 0), (13, 0))),
    ("min13", _TRIAD_MIN + ((7, -1), (9, 0), (11, 0), (13, 0))),
    ("13",    _TRIAD_MAJ + ((7, -1), (9, 0), (13, 0))),

    # 11ths
    ("maj11", _TRIAD_MAJ + ((7, 0), (9, 0), (11, 0))),
    ("Maj11", _TRIAD_MAJ + ((7, 0), (9, 0), (11, 0))),
    ("M11",   _TRIAD_MAJ + ((7, 0), (9, 0), (11, 0))),
    ("Δ11",   _TRIAD_MAJ + ((7, 0), (9, 0), (11, 0))),
    ("m11",   _TRIAD_MIN + ((7, -1), (9, 0), (11, 0))),
    ("min11", _TRIAD_MIN + ((7, -1), (9, 0), (11, 0))),
    ("11",    _TRIAD_MAJ + ((7, -1), (9, 0), (11, 0))),

    # 9ths
    ("madd9", _TRIAD_MIN + ((9, 0),)),
    ("maj9",  _TRIAD_MAJ + ((7, 0), (9, 0))),
    ("Maj9",  _TRIAD_MAJ + ((7, 0), (9, 0))),
    ("M9",    _TRIAD_MAJ + ((7, 0), (9, 0))),
    ("Δ9",    _TRIAD_MAJ + ((7, 0), (9, 0))),
    ("m9",    _TRIAD_MIN + ((7, -1), (9, 0))),
    ("min9",  _TRIAD_MIN + ((7, -1), (9, 0))),
    ("add9",  _TRIAD_MAJ + ((9, 0),)),
    ("9",     _TRIAD_MAJ + ((7, -1), (9, 0))),

    # 7ths
    ("mMaj7", _TRIAD_MIN + ((7, 0),)),
    ("mM7",   _TRIAD_MIN + ((7, 0),)),
    ("maj7",  _TRIAD_MAJ + ((7, 0),)),
    ("Maj7",  _TRIAD_MAJ + ((7, 0),)),
    ("M7",    _TRIAD_MAJ + ((7, 0),)),
    ("Δ7",    _TRIAD_MAJ + ((7, 0),)),
    ("Δ",     _TRIAD_MAJ + ((7, 0),)),
    ("dim7",  _TRIAD_DIM + ((7, -2),)),
    ("°7",    _TRIAD_DIM + ((7, -2),)),
    ("m7b5",  _TRIAD_DIM + ((7, -1),)),
    ("ø7",    _TRIAD_DIM + ((7, -1),)),
    ("ø",     _TRIAD_DIM + ((7, -1),)),
    ("m7",    _TRIAD_MIN + ((7, -1),)),
    ("min7",  _TRIAD_MIN + ((7, -1),)),
    ("7",     _TRIAD_MAJ + ((7, -1),)),

    # 6ths
    ("m6",    _TRIAD_MIN + ((6, 0),)),
    ("min6",  _TRIAD_MIN + ((6, 0),)),
    ("6",     _TRIAD_MAJ + ((6, 0),)),

    # Triads
    ("min",   _TRIAD_MIN),
    ("dim",   _TRIAD_DIM),
    ("°",     _TRIAD_DIM),
    ("aug",   _TRIAD_AUG),
    ("+",     _TRIAD_AUG),
    ("sus2",  _TRIAD_SUS2),
    ("sus4",  _TRIAD_SUS4),
    ("sus",   _TRIAD_SUS4),
    ("m",     _TRIAD_MIN),

    # Power chord
    ("5",     ((1, 0), (5, 0))),

    # Major (no suffix). Must come last as it matches everything.
    ("",      _TRIAD_MAJ),
]


# --- Modifier tokenization ----------------------------------------------
#
# After the base quality, zero or more modifiers may appear. Each modifier
# is one of:
#
#   - alteration:   (b|#)(5|6|9|11|13)        e.g.  b5, #9, #11
#   - addition:     add(b|#)?(2|4|6|9|11|13)  e.g.  add9, add#11
#   - suspension:   sus(2|4)?                 e.g.  sus, sus2, sus4
#   - omission:     no(3|5)                   e.g.  no3, no5
#
# We tokenize greedily, longest-match-first, leaving anything unrecognized
# to be reported as a parse error.

_MOD_TOKENS = [
    re.compile(r"add(b|#)?(13|11|2|4|6|9)"),
    re.compile(r"no(3|5)"),
    re.compile(r"sus(2|4)"),
    re.compile(r"sus"),
    re.compile(r"(b|#)(11|13|5|6|9)"),
]


# --- Chord ---------------------------------------------------------------

@dataclass(frozen=True)
class Chord:
    root: Note
    intervals: tuple[tuple[int, int], ...]
    bass: Note | None = None
    # Original symbol, kept for display/debugging.
    symbol: str = ""

    def notes(self) -> tuple[Note, ...]:
        """Spelled chord tones, in degree order.

        Each tone uses the letter dictated by its degree (so a triad has
        three distinct letters, a 7-chord has four, etc.), with the
        accidental chosen so that the resulting pitch class matches
        ``root.pitch_class + natural_semitones[degree] + alteration``.
        """
        result: list[Note] = []
        for degree, alteration in self.intervals:
            letter = letter_at_degree(self.root.letter, degree)
            target_pc = (
                self.root.pitch_class
                + NATURAL_DEGREE_SEMITONES[degree]
                + alteration
            ) % 12
            result.append(spell_at(letter, target_pc))
        return tuple(result)

    def pitch_classes(self) -> tuple[int, ...]:
        """The pitch classes of the chord tones, in degree order.

        This is what voicing search will consult — spelling doesn't matter
        once we're looking for frets that produce a given pitch.
        """
        return tuple(n.pitch_class for n in self.notes())

    def __str__(self) -> str:
        if self.symbol:
            return self.symbol
        return f"{self.root}<intervals={self.intervals}>"


# --- Parser --------------------------------------------------------------

_ROOT_RE = re.compile(r"^([A-G](?:bb|##|[b#])?)")
_SLASH_RE = re.compile(r"/([A-G](?:bb|##|[b#])?)$")


class ChordParseError(ValueError):
    pass


def parse(symbol: str) -> Chord:
    """Parse a chord symbol like ``C``, ``F#m7``, ``Bb13#11``, ``D/F#``."""
    original = symbol
    s = symbol.strip()
    if not s:
        raise ChordParseError("empty chord symbol")

    # 1. Root.
    m = _ROOT_RE.match(s)
    if not m:
        raise ChordParseError(f"missing or invalid root in {original!r}")
    root = Note.parse(m.group(1))
    s = s[m.end():]

    # 2. Slash bass (at the end).
    bass: Note | None = None
    sm = _SLASH_RE.search(s)
    if sm:
        bass = Note.parse(sm.group(1))
        s = s[: sm.start()]

    # 3. Base quality (longest prefix).
    intervals: tuple[tuple[int, int], ...] | None = None
    for suffix, ivs in BASE_QUALITIES:
        if s.startswith(suffix):
            intervals = ivs
            s = s[len(suffix):]
            break
    if intervals is None:
        # Empty suffix matches everything, so this only triggers if
        # somehow nothing in the list matched.
        raise ChordParseError(f"could not parse quality in {original!r}")

    # 4. Modifiers.
    intervals = _apply_modifiers(intervals, s, original)

    return Chord(root=root, intervals=intervals, bass=bass, symbol=original)


def _apply_modifiers(
    intervals: tuple[tuple[int, int], ...],
    s: str,
    original: str,
) -> tuple[tuple[int, int], ...]:
    result = list(intervals)
    while s:
        matched = False
        for pattern in _MOD_TOKENS:
            m = pattern.match(s)
            if not m:
                continue
            result = _apply_one_modifier(result, m)
            s = s[m.end():]
            matched = True
            break
        if not matched:
            raise ChordParseError(
                f"unrecognized modifier {s!r} in chord {original!r}"
            )
    return tuple(sorted(result, key=lambda iv: _degree_sort_key(iv[0])))


def _degree_sort_key(degree: int) -> tuple[int, int]:
    # Sort the chord tones in voicing order: 1, 2/3/4 (chord third), 5/6,
    # 7, 9, 11, 13. Compound extensions follow their simple counterparts.
    order = {1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3, 9: 4, 11: 5, 13: 6}
    return (order[degree], degree)


def _apply_one_modifier(
    intervals: list[tuple[int, int]],
    m: re.Match[str],
) -> list[tuple[int, int]]:
    text = m.group(0)
    # sus modifier: replace 3rd (and 2nd/4th if present) with the suspended note.
    if text.startswith("sus"):
        suspended = 4 if text in ("sus", "sus4") else 2
        result = [(d, a) for d, a in intervals if d not in (2, 3, 4)]
        result.append((suspended, 0))
        return result
    # omission: drop a degree.
    if text.startswith("no"):
        omitted = int(text[2:])
        return [(d, a) for d, a in intervals if d != omitted]
    # addition: add (or replace) a degree with the given alteration.
    if text.startswith("add"):
        accidental_char = m.group(1) or ""
        degree = int(m.group(2))
        alteration = {"": 0, "#": 1, "b": -1}[accidental_char]
        result = [(d, a) for d, a in intervals if d != degree]
        result.append((degree, alteration))
        return result
    # plain alteration: b5, #5, b9, #9, #11, b13, …
    accidental_char = m.group(1)
    degree = int(m.group(2))
    alteration = {"#": 1, "b": -1}[accidental_char]
    result = [(d, a) for d, a in intervals if d != degree]
    result.append((degree, alteration))
    return result
