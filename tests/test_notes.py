"""Tests for the notes module."""

from __future__ import annotations

import pytest

from notes import (
    Note,
    Pitch,
    letter_at_degree,
    spell_at,
)


class TestNoteParsing:
    def test_natural(self):
        assert Note.parse("C") == Note("C", 0)
        assert Note.parse("G") == Note("G", 0)

    def test_sharp_flat(self):
        assert Note.parse("F#") == Note("F", 1)
        assert Note.parse("Bb") == Note("B", -1)

    def test_double_accidental(self):
        assert Note.parse("G##") == Note("G", 2)
        assert Note.parse("Ebb") == Note("E", -2)

    def test_round_trip(self):
        for text in ("C", "C#", "Db", "F", "F#", "Gb", "G##", "Abb"):
            assert str(Note.parse(text)) == text

    def test_invalid(self):
        with pytest.raises(ValueError):
            Note.parse("H")
        with pytest.raises(ValueError):
            Note.parse("C###")
        with pytest.raises(ValueError):
            Note("C", 3)


class TestPitchClass:
    def test_naturals(self):
        # C, D, E, F, G, A, B = 0, 2, 4, 5, 7, 9, 11
        assert Note("C").pitch_class == 0
        assert Note("D").pitch_class == 2
        assert Note("E").pitch_class == 4
        assert Note("F").pitch_class == 5
        assert Note("G").pitch_class == 7
        assert Note("A").pitch_class == 9
        assert Note("B").pitch_class == 11

    def test_accidentals(self):
        assert Note("F", 1).pitch_class == 6   # F#
        assert Note("G", -1).pitch_class == 6  # Gb
        assert Note("B", 1).pitch_class == 0   # B# wraps to C
        assert Note("C", -1).pitch_class == 11 # Cb wraps to B
        assert Note("C", -2).pitch_class == 10 # Cbb = Bb (pitch class)

    def test_enharmonics(self):
        assert Note("F", 1).is_enharmonic(Note("G", -1))
        assert not Note("C").is_enharmonic(Note("D"))


class TestSpellAt:
    """The crux of enharmonic correctness."""

    def test_major_third(self):
        # Major 3rd of D: letter must be F (skip E), pitch class 6 → F#.
        third = spell_at("F", target_pitch_class=6)
        assert third == Note("F", 1)
        assert str(third) == "F#"

    def test_major_third_of_db(self):
        # Major 3rd of Db: letter must be F, pitch class 5 → F natural.
        third = spell_at("F", target_pitch_class=5)
        assert third == Note("F", 0)

    def test_diminished_seventh_uses_double_flat(self):
        # Diminished 7th of C: letter B, pitch class 9 → Bbb (not A).
        seventh = spell_at("B", target_pitch_class=9)
        assert seventh == Note("B", -2)
        assert str(seventh) == "Bbb"

    def test_unreachable_raises(self):
        # No accidental in [-2, +2] lets letter C reach pitch class 6.
        with pytest.raises(ValueError):
            spell_at("C", target_pitch_class=6)


class TestLetterAtDegree:
    def test_basic_triad(self):
        # C major triad letters: C, E, G.
        assert letter_at_degree("C", 1) == "C"
        assert letter_at_degree("C", 3) == "E"
        assert letter_at_degree("C", 5) == "G"

    def test_seventh(self):
        assert letter_at_degree("C", 7) == "B"
        assert letter_at_degree("D", 7) == "C"

    def test_extensions(self):
        # 9, 11, 13 collapse to 2, 4, 6 letter-wise.
        assert letter_at_degree("C", 9) == "D"
        assert letter_at_degree("C", 11) == "F"
        assert letter_at_degree("C", 13) == "A"

    def test_wraparound(self):
        # 3rd of A is C (wrapping past B).
        assert letter_at_degree("A", 3) == "C"
        # 5th of E is B.
        assert letter_at_degree("E", 5) == "B"
        # 7th of A is G.
        assert letter_at_degree("A", 7) == "G"


class TestPitch:
    def test_midi_middle_c(self):
        assert Pitch.parse("C4").midi == 60

    def test_midi_a4(self):
        assert Pitch.parse("A4").midi == 69

    def test_midi_guitar_low_e(self):
        # E2 is the lowest open string on a standard-tuned guitar: MIDI 40.
        assert Pitch.parse("E2").midi == 40

    def test_midi_round_trip(self):
        for text in ("E2", "A2", "D3", "G3", "B3", "E4"):
            p = Pitch.parse(text)
            assert Pitch.from_midi(p.midi).midi == p.midi

    def test_transpose(self):
        # E2 + 5 semitones = A2.
        assert Pitch.parse("E2").transpose(5).midi == Pitch.parse("A2").midi

    def test_octave_boundary(self):
        # B3 + 1 semitone = C4 (octave changes).
        assert Pitch.parse("B3").transpose(1).midi == Pitch.parse("C4").midi
