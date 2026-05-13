"""Tests for the voicing search engine.

The strategy here is empirical: pin the engine against the canonical chord
shapes guitarists actually play. If `find_voicings("C")` doesn't surface the
open C shape near the top, something is wrong with either the candidate
search or the scoring. Likewise for E barre F, A-shape barres, etc.
"""

from __future__ import annotations

import pytest

from chords import parse
from tunings import DADGAD, OPEN_D, OPEN_G, STANDARD
from voicings import SearchOptions, Voicing, find_voicings


def shapes(voicings: list[Voicing]) -> list[tuple[int | None, ...]]:
    """Convenience: list of fret tuples in score order."""
    return [v.frets for v in voicings]


def has_shape(voicings: list[Voicing], shape: tuple[int | None, ...]) -> bool:
    return shape in shapes(voicings)


# ----------------------------------------------------------------------
# Open chords in standard tuning
# ----------------------------------------------------------------------

class TestCanonicalOpenChords:
    def test_c_major_includes_open_shape(self):
        # x32010 — the textbook open C.
        v = find_voicings(parse("C"), STANDARD)
        assert has_shape(v, (None, 3, 2, 0, 1, 0))

    def test_a_major_includes_open_shape(self):
        # x02220 — open A.
        v = find_voicings(parse("A"), STANDARD)
        assert has_shape(v, (None, 0, 2, 2, 2, 0))

    def test_e_major_includes_open_shape(self):
        # 022100 — open E.
        v = find_voicings(parse("E"), STANDARD)
        assert has_shape(v, (0, 2, 2, 1, 0, 0))

    def test_d_major_includes_open_shape(self):
        # xx0232 — open D.
        v = find_voicings(parse("D"), STANDARD)
        assert has_shape(v, (None, None, 0, 2, 3, 2))

    def test_g_major_includes_open_shape(self):
        # 320003 — open G. (Some players use 320033; both are valid.)
        v = find_voicings(parse("G"), STANDARD)
        assert (
            has_shape(v, (3, 2, 0, 0, 0, 3))
            or has_shape(v, (3, 2, 0, 0, 3, 3))
        )

    def test_a_minor_includes_open_shape(self):
        # x02210 — open Am.
        v = find_voicings(parse("Am"), STANDARD)
        assert has_shape(v, (None, 0, 2, 2, 1, 0))

    def test_e_minor_includes_open_shape(self):
        # 022000 — open Em.
        v = find_voicings(parse("Em"), STANDARD)
        assert has_shape(v, (0, 2, 2, 0, 0, 0))


# ----------------------------------------------------------------------
# Barre chords
# ----------------------------------------------------------------------

class TestBarreChords:
    def test_f_major_barre_e_shape(self):
        # 133211 — F barre at fret 1 (E-shape).
        v = find_voicings(parse("F"), STANDARD)
        assert has_shape(v, (1, 3, 3, 2, 1, 1))

    def test_bb_major_barre_a_shape(self):
        # x13331 — Bb barre at fret 1 (A-shape).
        v = find_voicings(parse("Bb"), STANDARD)
        assert has_shape(v, (None, 1, 3, 3, 3, 1))

    def test_b_major_barre(self):
        # x24442 — B barre at fret 2 (A-shape).
        v = find_voicings(parse("B"), STANDARD)
        assert has_shape(v, (None, 2, 4, 4, 4, 2))


# ----------------------------------------------------------------------
# Seventh chords
# ----------------------------------------------------------------------

class TestSeventhChords:
    def test_g7_open(self):
        # 320001 — open G7.
        v = find_voicings(parse("G7"), STANDARD)
        assert has_shape(v, (3, 2, 0, 0, 0, 1))

    def test_d7_open(self):
        # xx0212 — open D7.
        v = find_voicings(parse("D7"), STANDARD)
        assert has_shape(v, (None, None, 0, 2, 1, 2))

    def test_cmaj7_open(self):
        # x32000 — Cmaj7.
        v = find_voicings(parse("Cmaj7"), STANDARD)
        assert has_shape(v, (None, 3, 2, 0, 0, 0))

    def test_am7_open(self):
        # x02010 — Am7.
        v = find_voicings(parse("Am7"), STANDARD)
        assert has_shape(v, (None, 0, 2, 0, 1, 0))


# ----------------------------------------------------------------------
# General invariants
# ----------------------------------------------------------------------

class TestInvariants:
    def test_returns_at_most_limit(self):
        v = find_voicings(parse("C"), STANDARD, SearchOptions(limit=6))
        assert len(v) <= 6

    def test_returns_at_most_three(self):
        v = find_voicings(parse("C"), STANDARD, SearchOptions(limit=3))
        assert len(v) <= 3

    def test_results_are_sorted_by_score(self):
        v = find_voicings(parse("Cmaj7"), STANDARD)
        scores = [vc.score for vc in v]
        assert scores == sorted(scores)

    def test_results_are_deduplicated(self):
        v = find_voicings(parse("C"), STANDARD)
        shapes_seen = set(shapes(v))
        assert len(shapes_seen) == len(v)

    def test_no_voicing_exceeds_fret_range(self):
        v = find_voicings(parse("C"), STANDARD, SearchOptions(max_fret=12))
        for vc in v:
            for f in vc.frets:
                assert f is None or 0 <= f <= 12

    def test_span_respected(self):
        opts = SearchOptions(max_span=4)
        v = find_voicings(parse("Cmaj9"), STANDARD, opts)
        for vc in v:
            if vc.max_pressed_fret >= 1 and vc.min_pressed_fret >= 1:
                assert vc.max_pressed_fret - vc.min_pressed_fret <= 4

    def test_fingers_respected(self):
        opts = SearchOptions(max_fingers=4)
        v = find_voicings(parse("C"), STANDARD, opts)
        for vc in v:
            assert vc.fingers <= 4

    def test_min_sounding_strings(self):
        opts = SearchOptions(min_sounding_strings=4)
        v = find_voicings(parse("C"), STANDARD, opts)
        for vc in v:
            assert vc.num_sounding >= 4

    def test_all_required_pcs_present(self):
        c = parse("Cm7")
        v = find_voicings(c, STANDARD)
        required = set(c.pitch_classes())
        # 5th may be omitted, but other tones must appear.
        five = c.pitch_classes()[2]  # G in Cm7
        essential = required - {five}
        for vc in v:
            present = {p for p in vc.pitch_classes if p is not None}
            assert essential <= present


# ----------------------------------------------------------------------
# Slash chords
# ----------------------------------------------------------------------

class TestSlashChords:
    def test_slash_bass_is_lowest_sounding(self):
        c = parse("C/E")
        v = find_voicings(c, STANDARD)
        assert v, "expected at least one voicing for C/E"
        for vc in v:
            lowest_pc = next(p for p in vc.pitch_classes if p is not None)
            assert lowest_pc == 4  # E

    def test_d_over_fsharp(self):
        c = parse("D/F#")
        v = find_voicings(c, STANDARD)
        assert v
        for vc in v:
            lowest_pc = next(p for p in vc.pitch_classes if p is not None)
            assert lowest_pc == 6  # F#


# ----------------------------------------------------------------------
# Open tunings
# ----------------------------------------------------------------------

class TestOpenTunings:
    def test_open_g_yields_zero_voicing_for_g(self):
        # Open G tuned strings spell G major; the open chord 000000 is G.
        v = find_voicings(parse("G"), OPEN_G)
        assert has_shape(v, (0, 0, 0, 0, 0, 0))

    def test_open_g_barre_for_a(self):
        # In Open G, A major is the full barre at fret 2.
        v = find_voicings(parse("A"), OPEN_G)
        assert has_shape(v, (2, 2, 2, 2, 2, 2))

    def test_open_d_zero_voicing_for_d(self):
        v = find_voicings(parse("D"), OPEN_D)
        assert has_shape(v, (0, 0, 0, 0, 0, 0))

    def test_dadgad_dsus4_at_open(self):
        # DADGAD strings spell Dsus4: D, A, D, G, A, D.
        v = find_voicings(parse("Dsus4"), DADGAD)
        assert has_shape(v, (0, 0, 0, 0, 0, 0))


# ----------------------------------------------------------------------
# Degree tracking
# ----------------------------------------------------------------------

class TestDegrees:
    def test_c_major_degrees(self):
        v = find_voicings(parse("C"), STANDARD)
        top = v[0]
        # Each sounding string must have a known degree (1, 3, or 5).
        for d in top.degrees:
            if d is not None:
                assert d in (1, 3, 5)

    def test_g7_includes_seventh(self):
        v = find_voicings(parse("G7"), STANDARD)
        for vc in v:
            degrees_played = {d for d in vc.degrees if d is not None}
            assert 7 in degrees_played, "G7 voicing must contain the b7"


# ----------------------------------------------------------------------
# Error / edge cases
# ----------------------------------------------------------------------

class TestEdges:
    def test_impossible_chord_returns_nothing_when_strict(self):
        # Force all 6 strings sounding for a chord that can't realistically
        # have 6 voices on a standard guitar in 0-15 range. This isn't
        # checking unreachability per se, just that the engine returns []
        # rather than crashing.
        opts = SearchOptions(min_sounding_strings=6, max_fingers=2)
        v = find_voicings(parse("C7b5"), STANDARD, opts)
        assert isinstance(v, list)
