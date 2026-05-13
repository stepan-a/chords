"""Voicing search: find playable positions for a chord on a given tuning.

The strategy is straightforward search with aggressive pruning:

1.  For each string, compute the candidate frets in [0, max_fret] whose pitch
    class is one of the chord's pitch classes — plus a "muted" option.
2.  Depth-first search through these per-string choices, low string to high.
    Prune branches early using the running fret-span (we know there's an
    upper bound on how far the four fretting fingers can reach).
3.  At each leaf, validate playability (finger count, slash bass, completeness)
    and score the voicing.
4.  Sort by score, deduplicate identical fret patterns, and keep the top N.

The cost is dominated by the per-string fanout (typically 4–6 fret candidates
plus muted = ~6 per string).  6**6 ≈ 47000 leaves before pruning; with span
pruning, real-world calls finish in a few milliseconds.

The output is a list of :class:`Voicing` instances, each carrying everything
the renderer needs: which fret on each string, which chord degree each note
plays, whether a barre is used, finger count, score (for debugging).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from chords import NATURAL_DEGREE_SEMITONES, Chord
from tunings import Tuning


# --- Public data type ---------------------------------------------------

@dataclass(frozen=True)
class Voicing:
    """A complete fingering for one chord on one tuning.

    ``frets[i]`` is the fret pressed on string ``i`` (low-to-high indexing,
    so ``frets[0]`` is the lowest-pitched string). ``None`` means the string
    is muted (or skipped). The arrays ``pitch_classes`` and ``degrees`` are
    aligned with ``frets`` and provide what each played string contributes
    to the chord.
    """

    frets: tuple[int | None, ...]
    pitch_classes: tuple[int | None, ...]
    degrees: tuple[int | None, ...]
    fingers: int
    barre: tuple[int, int, int] | None  # (fret, lowest_string, highest_string)
    min_pressed_fret: int  # 0 if no pressed string (all open or muted)
    max_pressed_fret: int  # 0 likewise
    score: float

    @property
    def num_sounding(self) -> int:
        return sum(1 for f in self.frets if f is not None)

    @property
    def num_muted(self) -> int:
        return sum(1 for f in self.frets if f is None)

    @property
    def display_start_fret(self) -> int:
        """Suggested first fret to show on a chord diagram.

        Compact voicings around the nut start at fret 1. Higher-up voicings
        center on their pressed range and the renderer prints a "5fr" marker.
        """
        if self.max_pressed_fret <= 4:
            return 1
        return self.min_pressed_fret

    def shape_key(self) -> tuple[int | None, ...]:
        """Hashable identity for dedup: just the frets tuple."""
        return self.frets


# --- Search parameters --------------------------------------------------

@dataclass(frozen=True)
class SearchOptions:
    max_fret: int = 15
    max_span: int = 4
    """Largest span (highest_pressed - lowest_pressed) allowed when more
    than one string is pressed at a non-zero fret."""

    min_sounding_strings: int = 4
    max_fingers: int = 4

    allow_omit_root: bool = False
    allow_omit_fifth: bool = True
    """Always treated as True if the 5th is altered (b5, #5)."""

    allow_skip_strings: bool = True
    """If True, muted strings between sounding strings are allowed but
    penalized; if False, sounding strings must be a contiguous block."""

    limit: int = 6
    """Maximum number of voicings to return."""


# --- Search -------------------------------------------------------------

def find_voicings(
    chord: Chord,
    tuning: Tuning,
    options: SearchOptions = SearchOptions(),
) -> list[Voicing]:
    """Return up to ``options.limit`` playable voicings, best first."""

    # 1. Split the chord tones into required vs optional pitch classes.
    required_pcs, optional_pcs, pc_to_degree = _classify_chord_tones(
        chord, options
    )
    if not required_pcs and not optional_pcs:
        return []

    all_pcs = required_pcs | optional_pcs

    # 2. Slash-bass handling. The chord.bass note (if present) MUST be the
    # bass of the voicing; otherwise we just prefer the root (scoring).
    bass_pc: int | None
    if chord.bass is not None:
        bass_pc = chord.bass.pitch_class
        # The bass note itself must be reachable on some string somewhere;
        # add it to all_pcs even if it duplicates a chord tone.
        all_pcs = all_pcs | {bass_pc}
    else:
        bass_pc = None  # any chord tone may be in the bass; root preferred.

    # 3. Per-string candidate frets.
    candidates_per_string: list[list[_StringChoice]] = []
    for s in range(tuning.num_strings):
        choices: list[_StringChoice] = [_MUTED]
        for f in range(0, options.max_fret + 1):
            pc = tuning.note_at(s, f).pitch_class
            if pc in all_pcs:
                degree = pc_to_degree.get(pc)
                choices.append(_StringChoice(fret=f, pitch_class=pc, degree=degree))
        candidates_per_string.append(choices)

    # 4. DFS through the combinations.
    found: list[Voicing] = []
    _search(
        string_idx=0,
        choices=[],
        min_pressed=None,
        max_pressed=None,
        seen_pcs=set(),
        chord=chord,
        tuning=tuning,
        candidates_per_string=candidates_per_string,
        required_pcs=required_pcs,
        bass_pc=bass_pc,
        pc_to_degree=pc_to_degree,
        options=options,
        out=found,
    )

    # 5. Sort + dedup by exact shape + filter dominated + limit.
    seen_shapes: set[tuple[int | None, ...]] = set()
    deduped: list[Voicing] = []
    for v in sorted(found, key=lambda v: v.score):
        if v.shape_key() in seen_shapes:
            continue
        seen_shapes.add(v.shape_key())
        deduped.append(v)

    filtered = _filter_dominated(deduped)
    return filtered[: options.limit]


def _filter_dominated(voicings: list[Voicing]) -> list[Voicing]:
    """Drop voicings strictly dominated by another in the list.

    A voicing *A* dominates *B* when:

    1.  A and B agree on every string where B sounds (same fret).
    2.  A sounds on at least one additional string where B is muted.
    3.  A and B share the same bass string index, so the chord's bass
        note is identical — otherwise A is a different voicing (e.g. an
        inversion), not a richer version of B.

    Under these conditions, B offers nothing a player can't get from A
    by simply not strumming a string — a duplicate listing wastes a slot.
    Listing both clutters the UI and confuses the user with "the same
    fingering with different scores".

    Implementation note: a single pass sorted by ``num_sounding`` desc
    lets us only ever check a candidate against already-kept voicings
    with the same bass index. That keeps the cost near linear in the
    number of voicings.
    """
    # Index of accepted voicings, keyed by bass string. Bass-mismatched
    # candidates can never dominate each other, so the buckets stay small.
    accepted_by_bass: dict[int | None, list[Voicing]] = {}

    # Sort by num_sounding descending; ties keep original (score) order.
    order = sorted(
        range(len(voicings)),
        key=lambda i: -voicings[i].num_sounding,
    )
    keep_flags = [True] * len(voicings)

    for idx in order:
        v = voicings[idx]
        bass = _bass_index(v)
        bucket = accepted_by_bass.get(bass, [])
        if any(_dominates(a, v) for a in bucket):
            keep_flags[idx] = False
            continue
        bucket.append(v)
        accepted_by_bass[bass] = bucket

    return [v for v, keep in zip(voicings, keep_flags) if keep]


def _bass_index(v: Voicing) -> int | None:
    for i, f in enumerate(v.frets):
        if f is not None:
            return i
    return None


def _dominates(a: Voicing, b: Voicing) -> bool:
    if a.num_sounding <= b.num_sounding:
        return False
    for af, bf in zip(a.frets, b.frets):
        if bf is None:
            continue
        if af != bf:
            return False
    return _bass_index(a) == _bass_index(b)


# --- Voice-leading distance (for chord progressions) -----------------------

def voicing_distance(prev: Voicing, curr: Voicing) -> int:
    """Approximate fretboard distance between two voicings.

    Designed for default-voicing selection in a progression: minimising
    this from one chord to the next keeps the player's hand from leaping
    across the neck.

    The metric sums two contributions:

    * **Hand position shift** — the absolute difference between the
      lowest pressed fret of each voicing.  This is the dominant factor
      in practice (any guitarist will tell you that crossing five frets
      is far harder than nudging two fingers).  Weighted ``×3``.
    * **Per-string motion** — for each string, the absolute change in
      fret (or a flat ``1`` if the string toggles between mute and
      sounded).  Captures finger-level continuity once the hand is in
      position.

    The hand-shift weight (3) was picked empirically so that ``C → F``
    (E-shape barre at fret 1) is preferred over ``C → F`` at fret 8 by
    an order of magnitude.  Tweakable if voice-leading defaults turn out
    too aggressive in some direction.
    """
    hand_shift = abs(prev.min_pressed_fret - curr.min_pressed_fret)
    string_motion = 0
    for f1, f2 in zip(prev.frets, curr.frets):
        if f1 is None and f2 is None:
            continue
        if f1 is None or f2 is None:
            # A string switching between muted and sounded counts as one
            # unit of motion — the equivalent of a one-fret move.
            string_motion += 1
        else:
            string_motion += abs(f1 - f2)
    return 3 * hand_shift + string_motion


def pick_closest_index(
    candidates: list[Voicing],
    prev: Voicing | None,
) -> int:
    """Index into *candidates* of the voicing closest to *prev*.

    With no previous voicing (start of a progression) the function
    returns ``0`` — i.e. defers to the best-scored candidate.

    Ties on distance are broken by preserving the candidates' incoming
    score order: since :func:`find_voicings` returns its results sorted
    best-first, the first candidate at the minimum distance is also the
    best-scored one at that distance.
    """
    if not candidates:
        raise ValueError("no candidates to pick from")
    if prev is None:
        return 0
    distances = [voicing_distance(prev, c) for c in candidates]
    return distances.index(min(distances))


# --- Internals -----------------------------------------------------------

@dataclass(frozen=True)
class _StringChoice:
    fret: int  # 0 = open, ≥1 = pressed
    pitch_class: int
    degree: int | None  # which chord degree this note plays, if known


_MUTED = _StringChoice(fret=-1, pitch_class=-1, degree=None)
# Sentinel: a string choice that means "muted". Distinguished from real
# frets by fret == -1. Using a sentinel keeps the inner loop branch-free.


def _is_muted(c: _StringChoice) -> bool:
    return c.fret < 0


def _classify_chord_tones(
    chord: Chord,
    options: SearchOptions,
) -> tuple[set[int], set[int], dict[int, int]]:
    """Return (required_pcs, optional_pcs, pc -> degree) for the chord.

    A pitch class is *optional* if the chord can be voiced without it without
    losing identity. By default that's just the natural 5th of triads and
    7-chords (and only if the 5th is unaltered). The root is optional only
    when ``allow_omit_root=True``.
    """
    required: set[int] = set()
    optional: set[int] = set()
    pc_to_degree: dict[int, int] = {}

    root_pc = chord.root.pitch_class
    for degree, alteration in chord.intervals:
        pc = (
            root_pc + NATURAL_DEGREE_SEMITONES[degree] + alteration
        ) % 12
        # Remember which degree this pc came from (for diagnostics & display).
        # If two degrees collide on the same pitch class, keep the lower one.
        if pc not in pc_to_degree or degree < pc_to_degree[pc]:
            pc_to_degree[pc] = degree

        is_optional_5th = (
            degree == 5 and alteration == 0 and options.allow_omit_fifth
        )
        is_optional_root = (
            degree == 1 and options.allow_omit_root
        )
        if is_optional_5th or is_optional_root:
            optional.add(pc)
        else:
            required.add(pc)

    return required, optional, pc_to_degree


def _search(
    *,
    string_idx: int,
    choices: list[_StringChoice],
    min_pressed: int | None,
    max_pressed: int | None,
    seen_pcs: set[int],
    chord: Chord,
    tuning: Tuning,
    candidates_per_string: list[list[_StringChoice]],
    required_pcs: set[int],
    bass_pc: int | None,
    pc_to_degree: dict[int, int],
    options: SearchOptions,
    out: list[Voicing],
) -> None:
    """Recursive DFS over per-string choices, low string to high."""

    if string_idx == tuning.num_strings:
        # 1. All required chord tones must be present.
        missing = required_pcs - seen_pcs
        if missing:
            return

        # 2. At least min_sounding_strings strings must sound.
        num_sounding = sum(0 if _is_muted(c) else 1 for c in choices)
        if num_sounding < options.min_sounding_strings:
            return

        # 3. Slash-bass constraint: lowest sounding string's pc == bass_pc.
        lowest_sounding_idx = next(
            (i for i, c in enumerate(choices) if not _is_muted(c)),
            None,
        )
        if lowest_sounding_idx is None:
            return  # all muted; can't happen if min_sounding_strings >= 1
        actual_bass_pc = choices[lowest_sounding_idx].pitch_class
        if bass_pc is not None and actual_bass_pc != bass_pc:
            return

        # 4. Skip-strings hard constraint (if disabled).
        if not options.allow_skip_strings:
            sounding_indices = [
                i for i, c in enumerate(choices) if not _is_muted(c)
            ]
            if sounding_indices[-1] - sounding_indices[0] + 1 != len(
                sounding_indices
            ):
                return

        # 5. Finger feasibility & barre detection.
        finger_info = _estimate_fingers(choices)
        if finger_info is None or finger_info.fingers > options.max_fingers:
            return

        # 6. Build Voicing and score it.
        v = _make_voicing(
            choices=choices,
            chord=chord,
            min_pressed=min_pressed or 0,
            max_pressed=max_pressed or 0,
            finger_info=finger_info,
            seen_pcs=seen_pcs,
            options=options,
        )
        out.append(v)
        return

    # Recurse: try each candidate on this string.
    for c in candidates_per_string[string_idx]:
        if _is_muted(c):
            choices.append(c)
            _search(
                string_idx=string_idx + 1,
                choices=choices,
                min_pressed=min_pressed,
                max_pressed=max_pressed,
                seen_pcs=seen_pcs,
                chord=chord,
                tuning=tuning,
                candidates_per_string=candidates_per_string,
                required_pcs=required_pcs,
                bass_pc=bass_pc,
                pc_to_degree=pc_to_degree,
                options=options,
                out=out,
            )
            choices.pop()
        else:
            # If this is a pressed fret (>0), update min/max_pressed and check span.
            if c.fret >= 1:
                new_min = c.fret if min_pressed is None else min(min_pressed, c.fret)
                new_max = c.fret if max_pressed is None else max(max_pressed, c.fret)
                if new_max - new_min > options.max_span:
                    # Pruning: this branch can't lead to a playable voicing
                    # because span will only grow.
                    continue
            else:
                new_min, new_max = min_pressed, max_pressed

            added_pc = c.pitch_class not in seen_pcs
            if added_pc:
                seen_pcs.add(c.pitch_class)
            choices.append(c)
            _search(
                string_idx=string_idx + 1,
                choices=choices,
                min_pressed=new_min,
                max_pressed=new_max,
                seen_pcs=seen_pcs,
                chord=chord,
                tuning=tuning,
                candidates_per_string=candidates_per_string,
                required_pcs=required_pcs,
                bass_pc=bass_pc,
                pc_to_degree=pc_to_degree,
                options=options,
                out=out,
            )
            choices.pop()
            if added_pc:
                seen_pcs.discard(c.pitch_class)


# --- Finger counting ----------------------------------------------------

@dataclass(frozen=True)
class _FingerInfo:
    fingers: int
    barre: tuple[int, int, int] | None  # (fret, low_string, high_string)


def _estimate_fingers(choices: list[_StringChoice]) -> _FingerInfo | None:
    """Estimate the number of fingers required for this voicing.

    Model:
    - Open strings need no finger.
    - Pressed strings need one finger each.
    - We may apply one *barre* on the lowest pressed fret if it covers
      at least 2 strings and the strings spanned by the barre are either
      (a) pressed at that same fret, or (b) pressed at a higher fret.
      A muted string inside the barre's span breaks the barre and the
      voicing is rejected (a real player can't mute a barred string).
    """
    pressed = [
        (i, c.fret) for i, c in enumerate(choices)
        if not _is_muted(c) and c.fret >= 1
    ]
    if not pressed:
        return _FingerInfo(fingers=0, barre=None)

    fingers_without_barre = len(pressed)

    # Try to apply a barre at the lowest pressed fret.
    min_fret = min(f for _, f in pressed)
    strings_at_min = [i for i, f in pressed if f == min_fret]
    if len(strings_at_min) >= 2:
        low_s, high_s = min(strings_at_min), max(strings_at_min)
        # Check that the strings between low_s and high_s on the barre fret
        # are not muted (a barre can't leave a hole) and have fret >= min_fret.
        barre_ok = True
        for i in range(low_s, high_s + 1):
            c = choices[i]
            if _is_muted(c):
                barre_ok = False
                break
            if c.fret < min_fret:
                # Open string inside the barre — physically impossible
                # because the barre depresses all covered strings.
                barre_ok = False
                break
        if barre_ok:
            # Barre uses 1 finger for all strings at min_fret; remaining
            # pressed strings (above min_fret) cost 1 finger each.
            n_above = sum(1 for i, f in pressed if f > min_fret)
            return _FingerInfo(
                fingers=1 + n_above,
                barre=(min_fret, low_s, high_s),
            )

    return _FingerInfo(fingers=fingers_without_barre, barre=None)


# --- Voicing construction & scoring -------------------------------------

def _make_voicing(
    *,
    choices: list[_StringChoice],
    chord: Chord,
    min_pressed: int,
    max_pressed: int,
    finger_info: _FingerInfo,
    seen_pcs: set[int],
    options: SearchOptions,
) -> Voicing:
    frets = tuple(None if _is_muted(c) else c.fret for c in choices)
    pcs = tuple(None if _is_muted(c) else c.pitch_class for c in choices)
    degrees = tuple(None if _is_muted(c) else c.degree for c in choices)

    v_score = _score_voicing(
        choices=choices,
        chord=chord,
        min_pressed=min_pressed,
        max_pressed=max_pressed,
        finger_info=finger_info,
        seen_pcs=seen_pcs,
    )

    return Voicing(
        frets=frets,
        pitch_classes=pcs,
        degrees=degrees,
        fingers=finger_info.fingers,
        barre=finger_info.barre,
        min_pressed_fret=min_pressed,
        max_pressed_fret=max_pressed,
        score=v_score,
    )


def _score_voicing(
    *,
    choices: list[_StringChoice],
    chord: Chord,
    min_pressed: int,
    max_pressed: int,
    finger_info: _FingerInfo,
    seen_pcs: set[int],
) -> float:
    """Lower is better. Hand-tuned weights; tests pin down the rankings
    on common shapes so future tweaks stay honest.

    A note on the inversion penalty: putting the root in the bass is
    generally preferable, but putting the *5th* in the bass is so common
    (every power chord, every Open G strum, the low E in many barred
    voicings) that we don't penalize it. Putting the 3rd in the bass is a
    proper inversion and gets a modest penalty.
    """

    score = 0.0

    # Position: prefer lower frets.
    score += 0.4 * min_pressed

    # Span: penalize wide stretches; 3-fret span is the comfort zone.
    pressed_span = max(0, max_pressed - min_pressed) if min_pressed > 0 else 0
    if pressed_span >= 3:
        score += 1.5 * (pressed_span - 2)

    # Sounding strings: bonus for fuller chords.
    num_sounding = sum(0 if _is_muted(c) else 1 for c in choices)
    score -= 0.8 * num_sounding

    # Open strings: bonus per ringing open. Rewards voicings that exploit
    # the tuning (the whole point of Open G/D, but also nice in standard).
    num_open = sum(
        1 for c in choices
        if not _is_muted(c) and c.fret == 0
    )
    score -= 0.4 * num_open

    # Muted strings: mild penalty. Muting strings is normal on guitar but
    # all else equal a fuller chord is preferable.
    num_muted = sum(1 for c in choices if _is_muted(c))
    score += 0.3 * num_muted

    # Bass note relative to chord identity.
    lowest_sounding_idx = next(
        (i for i, c in enumerate(choices) if not _is_muted(c)),
        None,
    )
    if lowest_sounding_idx is not None:
        bass_pc = choices[lowest_sounding_idx].pitch_class
        bass_degree = choices[lowest_sounding_idx].degree
        if bass_pc == chord.root.pitch_class:
            score -= 2.5  # root in bass
        elif chord.bass is not None and bass_pc == chord.bass.pitch_class:
            # Slash bass satisfied — already enforced as a hard constraint
            # earlier, so no scoring bonus needed.
            pass
        elif bass_degree == 5:
            # 5th in the bass: idiomatic on guitar, no penalty.
            pass
        elif bass_degree == 3:
            score += 1.0
        else:
            # 7th or extension in the bass — unusual.
            score += 1.5

    # Skip-strings: count gaps in the sounding pattern.
    sounding_indices = [
        i for i, c in enumerate(choices) if not _is_muted(c)
    ]
    if sounding_indices:
        gaps = (
            sounding_indices[-1] - sounding_indices[0] + 1
            - len(sounding_indices)
        )
        score += 2.5 * gaps

    # Barre: small malus (just slightly harder than no-barre).
    if finger_info.barre is not None:
        score += 0.5

    # Finger count: 3 fingers is ideal; 4 is fine; barred low position fine.
    if finger_info.fingers >= 4:
        score += 0.3 * (finger_info.fingers - 3)

    # No fingers needed: extra bonus. A voicing made entirely of open
    # strings is effortless to play and rings the loudest — the prototype
    # of an open-tuning shape (G in Open G, D in DADGAD, etc.).
    if finger_info.fingers == 0:
        score -= 1.0

    # Doubling: very mild penalty for redundant notes. Reduced from 0.25 so
    # that resonant "every-string-rings" voicings in open tunings aren't
    # punished simply for repeating chord tones.
    pc_counts: dict[int, int] = {}
    for c in choices:
        if not _is_muted(c):
            pc_counts[c.pitch_class] = pc_counts.get(c.pitch_class, 0) + 1
    doublings = sum(max(0, n - 1) for n in pc_counts.values())
    score += 0.1 * doublings

    return score
