"""Tests for the tunings module."""

from __future__ import annotations

import pytest

from notes import Note, Pitch
from tunings import (
    ALL_TUNINGS,
    DADGAD,
    DROP_D,
    OPEN_D,
    OPEN_G,
    STANDARD,
    Tuning,
    by_name,
)


class TestStandardTuning:
    def test_six_strings(self):
        assert STANDARD.num_strings == 6

    def test_open_notes(self):
        # Low to high: E A D G B E.
        expected = ["E", "A", "D", "G", "B", "E"]
        assert [str(s.note) for s in STANDARD.strings] == expected

    def test_open_octaves(self):
        # Standard guitar octaves (scientific pitch notation).
        expected = [2, 2, 3, 3, 3, 4]
        assert [s.octave for s in STANDARD.strings] == expected

    def test_low_e_midi(self):
        # 6th string open = MIDI 40.
        assert STANDARD.strings[0].midi == 40

    def test_fret_arithmetic(self):
        # 5th fret of low E should be A (same pitch as open 5th string).
        fifth_fret_low_e = STANDARD.pitch_at(string=0, fret=5)
        open_a = STANDARD.pitch_at(string=1, fret=0)
        assert fifth_fret_low_e.midi == open_a.midi

    def test_note_at(self):
        # 3rd fret of A string = C.
        assert STANDARD.note_at(string=1, fret=3).pitch_class == 0

    def test_open_pitch_classes(self):
        # E, A, D, G, B, E → 4, 9, 2, 7, 11, 4.
        assert STANDARD.open_pitch_classes() == (4, 9, 2, 7, 11, 4)


class TestAlternateTunings:
    def test_drop_d_lowest(self):
        # Only the 6th string changes (E → D).
        assert STANDARD.strings[1:] == DROP_D.strings[1:]
        assert DROP_D.strings[0].note.pitch_class == 2  # D

    def test_open_g_pitch_classes(self):
        # D G D G B D — pitch classes 2, 7, 2, 7, 11, 2.
        assert OPEN_G.open_pitch_classes() == (2, 7, 2, 7, 11, 2)

    def test_open_d_has_fsharp(self):
        # 3rd string of Open D is F#, not Gb.
        assert OPEN_D.strings[3].note == Note("F", 1)

    def test_dadgad(self):
        # D A D G A D — pitch classes 2, 9, 2, 7, 9, 2.
        assert DADGAD.open_pitch_classes() == (2, 9, 2, 7, 9, 2)


class TestValidation:
    def test_disordered_raises(self):
        with pytest.raises(ValueError, match="low-to-high"):
            Tuning.from_string("Bad", "E4 A2")

    def test_negative_fret_raises(self):
        with pytest.raises(ValueError):
            STANDARD.pitch_at(string=0, fret=-1)

    def test_string_out_of_range(self):
        with pytest.raises(ValueError):
            STANDARD.pitch_at(string=6, fret=0)


class TestLookup:
    def test_by_name_exact(self):
        assert by_name("Standard") is STANDARD

    def test_by_name_case_insensitive(self):
        assert by_name("standard") is STANDARD
        assert by_name("OPEN G") is OPEN_G

    def test_by_name_missing(self):
        with pytest.raises(KeyError):
            by_name("Nonexistent")


class TestPresetsCount:
    def test_all_tunings_unique(self):
        names = [t.name for t in ALL_TUNINGS]
        assert len(set(names)) == len(names)
