"""Tests for the chords module: parser + chord-tone spelling."""

from __future__ import annotations

import pytest

from chords import Chord, ChordParseError, parse
from notes import Note


def names(chord: Chord) -> list[str]:
    """Convenience: stringified note spellings."""
    return [str(n) for n in chord.notes()]


# ----------------------------------------------------------------------
# Basic triads
# ----------------------------------------------------------------------

class TestTriads:
    def test_c_major(self):
        assert names(parse("C")) == ["C", "E", "G"]

    def test_d_major_uses_fsharp(self):
        # The crux of enharmonic spelling — the 3rd must be F# (not Gb).
        assert names(parse("D")) == ["D", "F#", "A"]

    def test_db_major(self):
        assert names(parse("Db")) == ["Db", "F", "Ab"]

    def test_c_minor(self):
        assert names(parse("Cm")) == ["C", "Eb", "G"]

    def test_e_minor(self):
        # E minor has a G natural (not F##).
        assert names(parse("Em")) == ["E", "G", "B"]

    def test_min_alias(self):
        assert names(parse("Cmin")) == names(parse("Cm"))

    def test_diminished(self):
        # C°: C, Eb, Gb.
        assert names(parse("Cdim")) == ["C", "Eb", "Gb"]
        assert names(parse("C°")) == ["C", "Eb", "Gb"]

    def test_augmented(self):
        # C+: C, E, G#.
        assert names(parse("Caug")) == ["C", "E", "G#"]
        assert names(parse("C+")) == ["C", "E", "G#"]

    def test_sus2(self):
        assert names(parse("Csus2")) == ["C", "D", "G"]

    def test_sus4(self):
        assert names(parse("Csus4")) == ["C", "F", "G"]
        # Bare "sus" defaults to sus4.
        assert names(parse("Csus")) == names(parse("Csus4"))


# ----------------------------------------------------------------------
# Seventh chords
# ----------------------------------------------------------------------

class TestSevenths:
    def test_dom7(self):
        assert names(parse("C7")) == ["C", "E", "G", "Bb"]

    def test_maj7(self):
        assert names(parse("Cmaj7")) == ["C", "E", "G", "B"]
        assert names(parse("CM7")) == names(parse("Cmaj7"))

    def test_m7(self):
        assert names(parse("Cm7")) == ["C", "Eb", "G", "Bb"]

    def test_mMaj7(self):
        assert names(parse("CmMaj7")) == ["C", "Eb", "G", "B"]

    def test_dim7_uses_double_flat(self):
        # C°7: C, Eb, Gb, Bbb.  Strict spelling; enharmonic to A.
        assert names(parse("Cdim7")) == ["C", "Eb", "Gb", "Bbb"]
        assert names(parse("C°7")) == names(parse("Cdim7"))

    def test_m7b5(self):
        # Half-diminished: C, Eb, Gb, Bb.
        assert names(parse("Cm7b5")) == ["C", "Eb", "Gb", "Bb"]
        assert names(parse("Cø")) == names(parse("Cm7b5"))
        assert names(parse("Cø7")) == names(parse("Cm7b5"))


# ----------------------------------------------------------------------
# Extensions: 9, 11, 13
# ----------------------------------------------------------------------

class TestExtensions:
    def test_dom9(self):
        # C9 = C7 + 9 = C, E, G, Bb, D.
        assert names(parse("C9")) == ["C", "E", "G", "Bb", "D"]

    def test_maj9(self):
        assert names(parse("Cmaj9")) == ["C", "E", "G", "B", "D"]

    def test_m9(self):
        assert names(parse("Cm9")) == ["C", "Eb", "G", "Bb", "D"]

    def test_add9_has_no_seventh(self):
        # add9 = triad + 9, no 7th.
        assert names(parse("Cadd9")) == ["C", "E", "G", "D"]

    def test_madd9(self):
        assert names(parse("Cmadd9")) == ["C", "Eb", "G", "D"]

    def test_dom11(self):
        # C11 = C7 + 9 + 11.
        assert names(parse("C11")) == ["C", "E", "G", "Bb", "D", "F"]

    def test_dom13_omits_11(self):
        # Dominant 13 conventionally omits the 11.
        assert names(parse("C13")) == ["C", "E", "G", "Bb", "D", "A"]

    def test_six(self):
        assert names(parse("C6")) == ["C", "E", "G", "A"]

    def test_minor_six(self):
        assert names(parse("Cm6")) == ["C", "Eb", "G", "A"]


# ----------------------------------------------------------------------
# Altered dominants
# ----------------------------------------------------------------------

class TestAltered:
    def test_7b5(self):
        assert names(parse("C7b5")) == ["C", "E", "Gb", "Bb"]

    def test_7sharp5(self):
        assert names(parse("C7#5")) == ["C", "E", "G#", "Bb"]

    def test_7b9(self):
        # b9 of C is Db.
        assert names(parse("C7b9")) == ["C", "E", "G", "Bb", "Db"]

    def test_7sharp9(self):
        # #9 of C is D# (strict spelling, even though it clashes with E enharmonically).
        assert names(parse("C7#9")) == ["C", "E", "G", "Bb", "D#"]

    def test_7sharp11(self):
        # Strict reading: 7#11 adds #11 to dom7, no implicit 9.
        # The user writes 9#11 or 13#11 if they want the 9 included.
        assert names(parse("C7#11")) == ["C", "E", "G", "Bb", "F#"]

    def test_9sharp11(self):
        # Now with explicit 9.
        assert names(parse("C9#11")) == ["C", "E", "G", "Bb", "D", "F#"]

    def test_7b13(self):
        assert names(parse("C7b13")) == ["C", "E", "G", "Bb", "Ab"]

    def test_13b9(self):
        # Explicit 13 with altered 9.
        assert names(parse("C13b9")) == ["C", "E", "G", "Bb", "Db", "A"]

    def test_combined_alterations(self):
        # 7#5#9: alters 5th and adds #9. Order of modifiers must not matter.
        a = names(parse("C7#5#9"))
        b = names(parse("C7#9#5"))
        assert sorted(a) == sorted(b)
        assert set(a) == {"C", "E", "G#", "Bb", "D#"}

    def test_alt_in_other_keys(self):
        # F#7b9: root F#, then A#, C#, E, G.
        assert names(parse("F#7b9")) == ["F#", "A#", "C#", "E", "G"]


# ----------------------------------------------------------------------
# Slash chords
# ----------------------------------------------------------------------

class TestSlashChords:
    def test_basic(self):
        c = parse("C/E")
        assert c.bass == Note.parse("E")
        # Slash bass does not alter the chord tones themselves.
        assert names(c) == ["C", "E", "G"]

    def test_slash_with_accidental(self):
        c = parse("D/F#")
        assert c.bass == Note.parse("F#")
        assert names(c) == ["D", "F#", "A"]

    def test_slash_with_quality(self):
        c = parse("Am7/G")
        assert c.bass == Note.parse("G")
        assert names(c) == ["A", "C", "E", "G"]


# ----------------------------------------------------------------------
# Root parsing
# ----------------------------------------------------------------------

class TestRoot:
    def test_sharp_root(self):
        assert names(parse("F#m")) == ["F#", "A", "C#"]

    def test_flat_root(self):
        # Bb minor: Bb, Db, F.
        assert names(parse("Bbm")) == ["Bb", "Db", "F"]

    def test_double_sharp_root(self):
        # Just check it parses; spelling will be unusual.
        c = parse("F##")
        assert c.root == Note("F", 2)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class TestErrors:
    def test_empty(self):
        with pytest.raises(ChordParseError):
            parse("")

    def test_bad_root(self):
        with pytest.raises(ChordParseError):
            parse("Hm")  # H is German notation, not supported.

    def test_unknown_modifier(self):
        with pytest.raises(ChordParseError):
            parse("Cm7xyz")


# ----------------------------------------------------------------------
# Cross-cutting: pitch_classes
# ----------------------------------------------------------------------

class TestPitchClasses:
    def test_c_major(self):
        # C=0, E=4, G=7.
        assert parse("C").pitch_classes() == (0, 4, 7)

    def test_dim7_enharmonic_to_a(self):
        # Bbb has pitch class 9, same as A.
        c = parse("Cdim7")
        assert c.pitch_classes() == (0, 3, 6, 9)
