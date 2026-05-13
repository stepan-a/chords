"""Tests for the SVG renderer.

We don't pixel-compare — too brittle. Instead we verify:
  - SVG output parses as valid XML
  - Expected structural elements are present (nut/no-nut, dots, barre, X/O)
  - Finger and degree labels appear where they should
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from chords import parse
from render import (
    RenderOptions,
    assign_fingers,
    degree_label,
    note_labels,
    render_chord_chart,
    render_voicing,
)
from tunings import OPEN_G, STANDARD
from voicings import find_voicings


def get_voicing(symbol: str, tuning=STANDARD):
    return find_voicings(parse(symbol), tuning)[0]


def parse_svg(svg: str) -> ET.Element:
    """Parse SVG and strip the default namespace from tag names so we can
    use simple-string lookups."""
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    for elem in root.iter():
        if elem.tag.startswith(ns):
            elem.tag = elem.tag[len(ns):]
    return root


# ----------------------------------------------------------------------
# Finger assignment
# ----------------------------------------------------------------------

class TestAssignFingers:
    def test_open_c_three_fingers(self):
        # x32010 → strings 1@3, 2@2, 4@1 (fret-ascending)
        v = find_voicings(parse("C"), STANDARD)[0]
        assert v.frets == (None, 3, 2, 0, 1, 0)
        fingers = assign_fingers(v)
        # Assigned in fret order: fret 1 → 1, fret 2 → 2, fret 3 → 3
        assert fingers == (None, 3, 2, None, 1, None)

    def test_open_e_minor(self):
        # 022000 → only two pressed strings, both at fret 2 (strings 1 and 2)
        v = find_voicings(parse("Em"), STANDARD)[0]
        assert v.frets == (0, 2, 2, 0, 0, 0)
        fingers = assign_fingers(v)
        # Fret 2 ascending by string: string 1 → 1, string 2 → 2
        assert fingers == (None, 1, 2, None, None, None)

    def test_f_barre_uses_finger_one(self):
        # 133211 with barre at fret 1
        v = find_voicings(parse("F"), STANDARD)[0]
        assert v.frets == (1, 3, 3, 2, 1, 1)
        assert v.barre is not None
        fingers = assign_fingers(v)
        # Strings at the barre (fret 1: strings 0, 4, 5) get finger 1.
        assert fingers[0] == 1
        assert fingers[4] == 1
        assert fingers[5] == 1
        # Other fingers handle the remaining pressed strings.
        assert fingers[3] in (2, 3, 4)  # fret 2
        assert fingers[1] in (2, 3, 4)  # fret 3
        assert fingers[2] in (2, 3, 4)  # fret 3

    def test_open_and_muted_are_none(self):
        v = find_voicings(parse("C"), STANDARD)[0]
        fingers = assign_fingers(v)
        # Index 0 is muted, indexes 3 and 5 are open
        assert fingers[0] is None
        assert fingers[3] is None
        assert fingers[5] is None


# ----------------------------------------------------------------------
# Degree labels (Roman numerals)
# ----------------------------------------------------------------------

class TestDegreeLabel:
    def test_triad_degrees(self):
        assert degree_label(1) == "I"
        assert degree_label(3) == "III"
        assert degree_label(5) == "V"

    def test_seventh(self):
        assert degree_label(7) == "VII"

    def test_extensions(self):
        assert degree_label(9) == "IX"
        assert degree_label(11) == "XI"
        assert degree_label(13) == "XIII"

    def test_none(self):
        assert degree_label(None) == ""


# ----------------------------------------------------------------------
# Note labels
# ----------------------------------------------------------------------

class TestNoteLabels:
    def test_c_major_open(self):
        c = parse("C")
        v = find_voicings(c, STANDARD)[0]   # x32010
        labels = note_labels(v, c)
        # Aligned to frets (None, 3, 2, 0, 1, 0). Notes for degrees 1/3/5 = C/E/G.
        assert labels[0] is None       # muted
        assert labels[1] == "C"
        assert labels[2] == "E"
        assert labels[3] == "G"
        assert labels[4] == "C"
        assert labels[5] == "E"

    def test_d_major_uses_fsharp(self):
        c = parse("D")
        v = find_voicings(c, STANDARD)[0]  # xx0232
        labels = note_labels(v, c)
        # Should use F# (not Gb) — enharmonic spelling at work.
        played = [n for n in labels if n]
        assert "F#" in played
        assert "Gb" not in played


# ----------------------------------------------------------------------
# SVG structural tests
# ----------------------------------------------------------------------

class TestSvgStructure:
    def test_is_valid_xml(self):
        v = get_voicing("C")
        svg = render_voicing(v, parse("C"))
        # Should parse cleanly.
        ET.fromstring(svg)

    def test_root_is_svg(self):
        svg = render_voicing(get_voicing("C"), parse("C"))
        root = parse_svg(svg)
        assert root.tag == "svg"

    def test_contains_chord_name(self):
        svg = render_voicing(get_voicing("Cmaj7"), parse("Cmaj7"))
        root = parse_svg(svg)
        texts = [t.text for t in root.iter("text") if t.text]
        assert "Cmaj7" in texts

    def test_omits_chord_name_when_disabled(self):
        opts = RenderOptions(show_chord_name=False)
        svg = render_voicing(get_voicing("Cmaj7"), parse("Cmaj7"), opts)
        root = parse_svg(svg)
        texts = [t.text for t in root.iter("text") if t.text]
        assert "Cmaj7" not in texts

    def test_open_c_has_muted_marker(self):
        # x32010 — first string muted, should produce a × somewhere
        svg = render_voicing(get_voicing("C"), parse("C"))
        assert "×" in svg

    def test_open_c_has_open_circles(self):
        # x32010 has 2 open strings → 2 open-circle markers above nut
        svg = render_voicing(get_voicing("C"), parse("C"))
        root = parse_svg(svg)
        # Open string circles use stroke=primary and fill=none;
        # filled dots use fill=primary. Count fill="none" circles.
        open_circles = [
            c for c in root.iter("circle")
            if c.get("fill") == "none"
        ]
        assert len(open_circles) == 2

    def test_barre_chord_has_rect(self):
        # F barre → expect a barre rectangle (rounded).
        svg = render_voicing(get_voicing("F"), parse("F"))
        root = parse_svg(svg)
        rects_with_rx = [
            r for r in root.iter("rect")
            if r.get("rx") and r.get("rx") not in ("0", "")
        ]
        assert rects_with_rx, "expected a rounded barre rectangle"

    def test_high_position_shows_fret_marker(self):
        # Pick a voicing that starts higher than fret 1.
        voicings = find_voicings(parse("C"), STANDARD)
        high = next(
            v for v in voicings
            if v.min_pressed_fret >= 3 and v.max_pressed_fret >= 4
        )
        svg = render_voicing(high, parse("C"))
        root = parse_svg(svg)
        # Expect a text like "3fr" or "5fr" somewhere on the right side.
        fr_texts = [
            t.text for t in root.iter("text")
            if t.text and t.text.endswith("fr")
        ]
        assert fr_texts, f"expected a '{high.min_pressed_fret}fr' marker"

    def test_label_mode_fingers(self):
        opts = RenderOptions(label_mode="fingers")
        svg = render_voicing(get_voicing("C"), parse("C"), opts)
        root = parse_svg(svg)
        # Pressed dots should have numeric finger labels (1-4).
        # Find text elements inside the chord area (not the title).
        all_texts = [t.text for t in root.iter("text") if t.text]
        digits = [t for t in all_texts if t in {"1", "2", "3", "4"}]
        assert digits, "expected at least one finger number"

    def test_label_mode_degrees(self):
        opts = RenderOptions(label_mode="degrees")
        svg = render_voicing(get_voicing("Cmaj7"), parse("Cmaj7"), opts)
        all_texts = [t.text for t in parse_svg(svg).iter("text") if t.text]
        # Cmaj7 → I, III, V, VII (some subset depending on voicing)
        assert any(t in {"I", "III", "V", "VII"} for t in all_texts)

    def test_label_mode_notes(self):
        opts = RenderOptions(label_mode="notes")
        svg = render_voicing(get_voicing("C"), parse("C"), opts)
        all_texts = [t.text for t in parse_svg(svg).iter("text") if t.text]
        assert "C" in all_texts
        assert "E" in all_texts

    def test_label_mode_none_has_no_labels_in_dots(self):
        opts = RenderOptions(label_mode="none")
        svg = render_voicing(get_voicing("C"), parse("C"), opts)
        # Should still contain the chord name title, but no finger digits.
        # No string-name labels yet either.
        all_texts = [t.text for t in parse_svg(svg).iter("text") if t.text]
        non_title = [t for t in all_texts if t != "C"]
        # "×" or "0fr"... shouldn't have isolated digit labels for fingers.
        # Quick check: no plain finger digit "1", "2", "3", "4"
        digits = [t for t in non_title if t in {"1", "2", "3", "4"}]
        assert not digits, f"unexpected finger labels in none mode: {digits}"

    def test_open_tuning_zero_voicing_well_formed(self):
        c = parse("G")
        v = find_voicings(c, OPEN_G)[1]   # the all-open (0,0,0,0,0,0)
        assert v.frets == (0, 0, 0, 0, 0, 0)
        svg = render_voicing(v, c)
        ET.fromstring(svg)  # parses
        # All 6 strings open → 6 open-circle markers.
        root = parse_svg(svg)
        open_circles = [
            c for c in root.iter("circle")
            if c.get("fill") == "none"
        ]
        assert len(open_circles) == 6


# ----------------------------------------------------------------------
# Chart (multi-voicing) rendering
# ----------------------------------------------------------------------

class TestChart:
    def test_chart_well_formed(self):
        c = parse("C")
        vs = find_voicings(c, STANDARD)[:6]
        svg = render_chord_chart(vs, c)
        ET.fromstring(svg)

    def test_empty_chart_is_minimal(self):
        c = parse("C")
        svg = render_chord_chart([], c)
        # Just enough to be valid XML.
        ET.fromstring(svg)
