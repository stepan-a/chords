"""SVG rendering of guitar chord diagrams.

Output is a complete, standalone ``<svg>`` element string — easy to write
to a file, embed in HTML, or display inline in a notebook. No external
fonts or stylesheets; everything is inline so the SVG is portable.

Layout (vertical):

       Cmaj7              ← chord name (title)
   ×  ○  ○  ○  ○  ○        ← open / muted markers above the nut
   ┏━━━━━━━━━━━━━━┓        ← nut (thick, only when fret 1 is shown)
   ┃  │  │  │  │ ┃
   ┠──●──┼──┼──┼──┨        ← fret line + a dot at fret 1
   ┠──┼──●──┼──┼──┨        ← fret 2
   ┠──┼──┼──┼──┼──┨        ← fret 3   (with "5fr" on the right when
   ┠──┼──┼──┼──┼──┨            the diagram starts higher up)
   ┗━━━━━━━━━━━━━━┛
   E  A  D  G  B  E        ← string-name row (optional)

The leftmost column is the lowest-pitched string (6th string), the
rightmost is the highest-pitched (1st string).  This matches the
``Voicing.frets`` tuple, which is also ordered low-to-high.

Labels inside dots can show: nothing, the *finger* number (1-4), the
chord *degree* in Roman numerals (I, III, V, …), or the *spelled note*
(C, F#, …).  Open strings receive the same label above the diagram
when label_mode is "degrees" or "notes".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chords import Chord
from voicings import Voicing


LabelMode = Literal["fingers", "degrees", "notes", "none"]


@dataclass(frozen=True)
class RenderOptions:
    cell_size: int = 26
    """Pixel size of one (string, fret) cell — the grid resolution."""

    num_frets_displayed: int = 5
    """How many frets the diagram shows.  4 is tight, 5 is the comfort
    default, 6 is roomy."""

    show_chord_name: bool = True
    show_string_names: bool = False
    """E A D G B E (or the corresponding open notes) below the diagram."""

    label_mode: LabelMode = "fingers"

    # Colors (anything CSS-valid).
    background: str = "white"
    primary: str = "#1c1c1e"
    dot_fill: str = "#1c1c1e"
    dot_text: str = "white"
    muted_marker: str = "#666"
    accent: str = "#444"

    # Font sizes.
    title_size: int = 16
    label_size: int = 10
    marker_size: int = 14

    # Layout knobs.
    padding: int = 8


# ---------------------------------------------------------------------
# Finger assignment
# ---------------------------------------------------------------------

def is_barre_visually_useful(voicing: Voicing) -> bool:
    """Should the barre be drawn (and fingered) as a real barre?

    The voicing engine optimistically counts barres whenever possible
    (a tighter lower bound on fingers makes more voicings pass the
    playability filter). Musically, though, a "barre" that only spans
    two strings is just two adjacent fingers — players don't really
    barre Em or D's two-note groupings.

    We treat the barre as visually real when:
      - it covers ≥ 3 strings *at the barre fret*, or
      - the total pressed-string count exceeds 4 (then a barre is the
        only way to fit the chord under four fingers).
    """
    if voicing.barre is None:
        return False
    fret, low_s, high_s = voicing.barre
    strings_at_barre_fret = sum(
        1 for i in range(low_s, high_s + 1)
        if voicing.frets[i] == fret
    )
    pressed_count = sum(
        1 for f in voicing.frets if f is not None and f >= 1
    )
    return strings_at_barre_fret >= 3 or pressed_count > 4


def assign_fingers(voicing: Voicing) -> tuple[int | None, ...]:
    """Return the finger number used on each string of the voicing.

    Conventions:
      - 1 = index, 2 = middle, 3 = ring, 4 = pinky.
      - Open and muted strings get ``None``.
      - If the voicing carries a *visually useful* barre, finger 1 covers
        all strings spanned at the barre fret; remaining pressed strings
        are assigned 2, 3, 4 in fret-ascending order.
      - Otherwise, pressed strings are assigned 1..4 in fret-ascending
        order (ties broken by string index).

    See :func:`is_barre_visually_useful` for the barre threshold.
    """
    fingers: list[int | None] = [None] * len(voicing.frets)

    if is_barre_visually_useful(voicing):
        assert voicing.barre is not None
        barre_fret, low_s, high_s = voicing.barre
        # Index finger lays across every string in the barre that is at
        # the barre fret. Strings above the barre keep their own finger.
        for i in range(low_s, high_s + 1):
            if voicing.frets[i] == barre_fret:
                fingers[i] = 1
        remaining = [
            (i, voicing.frets[i])
            for i, f in enumerate(voicing.frets)
            if f is not None and f > barre_fret
        ]
        remaining.sort(key=lambda x: (x[1], x[0]))
        next_finger = 2
        for i, _ in remaining:
            fingers[i] = next_finger
            next_finger = min(next_finger + 1, 4)
    else:
        pressed = [
            (i, f)
            for i, f in enumerate(voicing.frets)
            if f is not None and f >= 1
        ]
        pressed.sort(key=lambda x: (x[1], x[0]))
        next_finger = 1
        for i, _ in pressed:
            fingers[i] = next_finger
            next_finger = min(next_finger + 1, 4)

    return tuple(fingers)


# ---------------------------------------------------------------------
# Degree labels (Roman numerals)
# ---------------------------------------------------------------------

_ROMAN: dict[int, str] = {
    1: "I",
    2: "II",
    3: "III",
    4: "IV",
    5: "V",
    6: "VI",
    7: "VII",
    9: "IX",
    11: "XI",
    13: "XIII",
}


def degree_label(degree: int | None) -> str:
    if degree is None:
        return ""
    return _ROMAN.get(degree, str(degree))


# ---------------------------------------------------------------------
# Note labels (spelled notes for each played string)
# ---------------------------------------------------------------------

def note_labels(voicing: Voicing, chord: Chord) -> tuple[str | None, ...]:
    """Spelled note for each sounding string, using the chord's spelling.

    Each chord tone is spelled exactly once in ``chord.notes()``; we look
    up the note for each played string by matching its degree.
    """
    notes_by_degree = {
        d: str(n) for (d, _), n in zip(chord.intervals, chord.notes())
    }
    out: list[str | None] = []
    for f, d in zip(voicing.frets, voicing.degrees):
        if f is None or d is None:
            out.append(None)
        else:
            out.append(notes_by_degree.get(d, ""))
    return tuple(out)


# ---------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------

def render_voicing(
    voicing: Voicing,
    chord: Chord,
    options: RenderOptions | None = None,
) -> str:
    """Return a complete ``<svg>…</svg>`` element string for the voicing."""

    o = options or RenderOptions()

    n_strings = len(voicing.frets)
    n_frets = o.num_frets_displayed
    cell = o.cell_size
    pad = o.padding

    start_fret = voicing.display_start_fret
    show_nut = (start_fret == 1)

    # --- Layout slots (top-to-bottom) ---
    # The fret-number label (e.g. "5fr") for higher-position voicings is
    # placed in the title row, right-aligned, so it can never collide with
    # a dot inside the grid — whether on the lowest or the highest string.
    title_h = (o.title_size + 8) if (o.show_chord_name or not show_nut) else 0
    marker_h = o.marker_size + 6  # row of ○ / × above the nut
    grid_w = (n_strings - 1) * cell
    grid_h = n_frets * cell
    # Left and right gutters just absorb the dot/circle overflow on the
    # outermost string columns.
    gutter = 12
    string_name_h = (o.label_size + 6) if o.show_string_names else 0

    total_w = pad + gutter + grid_w + gutter + pad
    total_h = pad + title_h + marker_h + grid_h + string_name_h + pad

    grid_x0 = pad + gutter
    grid_y0 = pad + title_h + marker_h

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}" '
        f'font-family="-apple-system, BlinkMacSystemFont, sans-serif" '
        f'fill="{o.primary}" stroke="{o.primary}">'
    )

    # Background (helps when the SVG is embedded against a colored page).
    parts.append(
        f'<rect x="0" y="0" width="{total_w}" height="{total_h}" '
        f'fill="{o.background}" stroke="none"/>'
    )

    # --- Chord name (title) + optional fret-number marker on the right ---
    if o.show_chord_name:
        title = chord.symbol or _fallback_symbol(chord)
        cx = grid_x0 + grid_w / 2
        cy = pad + o.title_size
        parts.append(
            f'<text x="{cx}" y="{cy}" font-size="{o.title_size}" '
            f'font-weight="600" text-anchor="middle" stroke="none">'
            f'{_xml_escape(title)}</text>'
        )
    if not show_nut:
        # Place the fret-number marker in the title row, right-aligned to
        # the grid's right edge. This keeps it well clear of any dot on
        # either the highest or the lowest string at the top fret row.
        fret_x = grid_x0 + grid_w
        fret_y = pad + o.title_size
        parts.append(
            f'<text x="{fret_x}" y="{fret_y}" '
            f'font-size="{o.title_size - 3}" '
            f'fill="{o.accent}" stroke="none" '
            f'text-anchor="end">{start_fret}fr</text>'
        )

    # --- Open / muted markers above the nut ---
    open_labels = _open_string_labels(voicing, chord, o)
    # When we have to fit a label like "III" or "VII" inside the open-string
    # circle, the default tiny marker isn't big enough. Grow the marker
    # to match the in-grid dot when labels are on.
    labels_in_markers = o.label_mode in ("degrees", "notes")
    marker_r = (cell * 0.34) if labels_in_markers else (o.marker_size / 2.4)
    marker_y = grid_y0 - (marker_r + 4)
    open_label_size = o.label_size if labels_in_markers else (o.label_size - 1)
    for i, fret in enumerate(voicing.frets):
        x = grid_x0 + i * cell
        if fret is None:
            # ×
            parts.append(
                f'<text x="{x}" y="{marker_y}" font-size="{o.marker_size}" '
                f'fill="{o.muted_marker}" stroke="none" '
                f'text-anchor="middle" dominant-baseline="middle">×</text>'
            )
        elif fret == 0:
            # ○ (with optional label inside if we want degree/note for open)
            parts.append(
                f'<circle cx="{x}" cy="{marker_y}" r="{marker_r}" '
                f'fill="none" stroke="{o.primary}" stroke-width="1.2"/>'
            )
            label = open_labels[i]
            if label:
                parts.append(
                    f'<text x="{x}" y="{marker_y}" font-size="{open_label_size}" '
                    f'font-weight="600" fill="{o.primary}" stroke="none" '
                    f'text-anchor="middle" dominant-baseline="central">'
                    f'{_xml_escape(label)}</text>'
                )

    # --- Nut (or top fret line) ---
    nut_y = grid_y0
    if show_nut:
        parts.append(
            f'<line x1="{grid_x0 - 1}" y1="{nut_y}" '
            f'x2="{grid_x0 + grid_w + 1}" y2="{nut_y}" '
            f'stroke="{o.primary}" stroke-width="3" stroke-linecap="square"/>'
        )
    else:
        # Top is just a regular fret line; the "Nfr" annotation already
        # lives in the title row (see chord-name section above).
        parts.append(
            f'<line x1="{grid_x0}" y1="{nut_y}" '
            f'x2="{grid_x0 + grid_w}" y2="{nut_y}" '
            f'stroke="{o.primary}" stroke-width="1"/>'
        )

    # --- Fret lines (below the nut) ---
    for f in range(1, n_frets + 1):
        y = grid_y0 + f * cell
        parts.append(
            f'<line x1="{grid_x0}" y1="{y}" '
            f'x2="{grid_x0 + grid_w}" y2="{y}" '
            f'stroke="{o.primary}" stroke-width="1"/>'
        )

    # --- String lines (vertical) ---
    for s in range(n_strings):
        x = grid_x0 + s * cell
        parts.append(
            f'<line x1="{x}" y1="{grid_y0}" '
            f'x2="{x}" y2="{grid_y0 + grid_h}" '
            f'stroke="{o.primary}" stroke-width="1"/>'
        )

    # --- Barre (drawn under the dots) ---
    draw_barre = is_barre_visually_useful(voicing)
    if draw_barre and voicing.barre is not None:
        barre_fret, low_s, high_s = voicing.barre
        local = barre_fret - start_fret + 1
        if 1 <= local <= n_frets:
            cy = grid_y0 + (local - 0.5) * cell
            x1 = grid_x0 + low_s * cell
            x2 = grid_x0 + high_s * cell
            radius = cell * 0.34
            parts.append(
                f'<rect x="{x1 - radius}" y="{cy - radius}" '
                f'width="{x2 - x1 + 2 * radius}" height="{2 * radius}" '
                f'rx="{radius}" ry="{radius}" '
                f'fill="{o.dot_fill}" stroke="none"/>'
            )

    # --- Pressed-note dots + labels ---
    fingers = assign_fingers(voicing)
    notes_text = note_labels(voicing, chord) if o.label_mode == "notes" else None

    for i, fret in enumerate(voicing.frets):
        if fret is None or fret == 0:
            continue
        local = fret - start_fret + 1
        if not (1 <= local <= n_frets):
            # Pressed fret is outside the displayed range — render small
            # arrow at the bottom edge as a hint.
            continue
        cx = grid_x0 + i * cell
        cy = grid_y0 + (local - 0.5) * cell

        # Dot — skip the circle when this position is already covered by
        # the barre's rectangle (avoids double-stroke artifacts).
        under_barre = (
            draw_barre
            and voicing.barre is not None
            and voicing.barre[0] == fret
            and voicing.barre[1] <= i <= voicing.barre[2]
        )
        if not under_barre:
            r = cell * 0.34
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r}" '
                f'fill="{o.dot_fill}" stroke="none"/>'
            )

        # Label
        label = _dot_label(i, fret, voicing, chord, fingers, notes_text, o)
        if label:
            parts.append(
                f'<text x="{cx}" y="{cy}" font-size="{o.label_size}" '
                f'font-weight="600" fill="{o.dot_text}" stroke="none" '
                f'text-anchor="middle" dominant-baseline="central">'
                f'{_xml_escape(label)}</text>'
            )

    # --- String-name row below the grid ---
    if o.show_string_names:
        # We use the *open* note of each string of the tuning — but the
        # renderer doesn't know the tuning directly. Best effort: skip
        # this when not provided; the user can pass the labels explicitly
        # via show_string_names_labels if we add that later. For now we
        # just omit gracefully.
        pass

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------
# Convenience: render a bunch of voicings into one SVG
# ---------------------------------------------------------------------

def render_chord_chart(
    voicings: list[Voicing],
    chord: Chord,
    options: RenderOptions | None = None,
    columns: int = 3,
) -> str:
    """Tile several voicings of the same chord into a single SVG."""
    if not voicings:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"/>'

    o = options or RenderOptions()
    # Render each in isolation, then strip the per-cell viewport and pack.
    individual = [render_voicing(v, chord, o) for v in voicings]
    # Cheap and cheerful: vertical strip of individual SVGs side by side.
    # The renderer puts each in its own <g> with a translate.
    # We need dimensions; read them from the first.
    sample_w, sample_h = _svg_dimensions(individual[0])

    rows = (len(individual) + columns - 1) // columns
    total_w = columns * sample_w
    total_h = rows * sample_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}">'
    ]
    for idx, svg in enumerate(individual):
        r, c = divmod(idx, columns)
        x = c * sample_w
        y = r * sample_h
        # Inline-include by wrapping inside a <g>. The cleanest way is to
        # use <svg x= y= width= height=> as a viewport. But nested <svg>
        # tags need the inner element preserved; strip the xmlns of inner
        # to avoid duplicates.
        inner = svg.replace(
            ' xmlns="http://www.w3.org/2000/svg"', ""
        )
        parts.append(f'<g transform="translate({x},{y})">{inner}</g>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------

def _fallback_symbol(chord: Chord) -> str:
    return f"{chord.root}"


def _open_string_labels(
    voicing: Voicing, chord: Chord, o: RenderOptions
) -> tuple[str, ...]:
    """For each string: a label to display *inside* the open-string ○.

    Only filled in for ``label_mode`` in {degrees, notes}.  Returns empty
    strings otherwise.
    """
    if o.label_mode == "degrees":
        return tuple(degree_label(d) for d in voicing.degrees)
    if o.label_mode == "notes":
        labels = note_labels(voicing, chord)
        return tuple(label or "" for label in labels)
    return tuple("" for _ in voicing.frets)


def _dot_label(
    string_idx: int,
    fret: int,
    voicing: Voicing,
    chord: Chord,
    fingers: tuple[int | None, ...],
    notes_text: tuple[str | None, ...] | None,
    o: RenderOptions,
) -> str:
    mode = o.label_mode
    if mode == "none":
        return ""
    if mode == "fingers":
        f = fingers[string_idx]
        return str(f) if f else ""
    if mode == "degrees":
        return degree_label(voicing.degrees[string_idx])
    if mode == "notes":
        n = notes_text[string_idx] if notes_text else None
        return n or ""
    return ""


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _svg_dimensions(svg: str) -> tuple[int, int]:
    """Extract width and height from an SVG string (best effort, regex-free)."""
    import re
    m = re.search(r'width="(\d+)"\s+height="(\d+)"', svg)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 200, 240
