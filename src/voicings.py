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

    inversions_only: bool = False
    """When False (the default), the chord's root must be in the bass —
    only root-position voicings are returned. When True, the search
    returns *only* inversions: voicings whose lowest sounding string is
    a chord tone other than the root.

    The flag is binary: False yields no inversions, True yields nothing
    *but* inversions. There is no "mixed" mode — a UI that wants both
    can call :func:`find_voicings` twice and concatenate.

    Slash chords (e.g. ``D/F#``) always force their explicit bass and
    ignore this option — asking for a specific bass is itself an
    explicit "I want this inversion" statement."""

    limit: int = 8
    """Maximum number of voicings to return."""

    diversify: bool = True
    """When True, the result is biased toward variety: a greedy pass
    rejects each candidate that is too similar to one already picked
    (so we get distinct shapes across the neck instead of N variants
    of the same fingering), then fills the remaining slots with the
    best-scored leftovers."""

    min_diversity_distance: int = 5
    """Minimum :func:`voicing_distance` between any two voicings kept
    by the diversification pass. Larger values give more variety but
    can leave the result short on candidates; smaller values let more
    near-duplicates through. ``0`` disables diversification."""


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

    # 2. Bass-note constraint.
    #
    # Three cases, in priority order:
    #   - Slash chord (``C/E``): the explicit bass is mandatory and
    #     overrides everything else — picking an inversion is the
    #     whole point of writing one.
    #   - ``inversions_only=True``: bass_pc stays None so the slash
    #     check doesn't fire, and _search applies the inverse rule
    #     (reject voicings whose bass IS the root) instead.
    #   - default: force the chord's root in the bass — only
    #     root-position voicings are returned.
    bass_pc: int | None
    if chord.bass is not None:
        bass_pc = chord.bass.pitch_class
        # The bass note itself must be reachable on some string somewhere;
        # add it to all_pcs even if it duplicates a chord tone.
        all_pcs = all_pcs | {bass_pc}
    elif options.inversions_only:
        bass_pc = None  # any chord tone but the root; enforced in _search
    else:
        bass_pc = chord.root.pitch_class

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
    #
    # The sort key tie-breaks identically-scored voicings by ease of
    # playing: fewer fingers first, then lower fret. Without it, IEEE-754
    # rounding noise can flip a 4-finger fret-5 grip ahead of the 1-finger
    # open shape they're nominally tied with.
    seen_shapes: set[tuple[int | None, ...]] = set()
    deduped: list[Voicing] = []
    for v in sorted(found, key=_voicing_rank_key):
        if v.shape_key() in seen_shapes:
            continue
        seen_shapes.add(v.shape_key())
        deduped.append(v)

    filtered = _dedup_by_fingering(deduped, chord)
    filtered = _drop_subsets(filtered)
    if options.diversify and options.min_diversity_distance > 0:
        return _diversify(
            filtered, options.limit, options.min_diversity_distance
        )
    return filtered[: options.limit]


def _voicing_rank_key(v: Voicing) -> tuple[float, int, int]:
    """Sort key used wherever voicings are ordered ‘best first’.

    Score primary (rounded so IEEE-754 rounding noise never decides the
    order), then fingers, then ``min_pressed_fret``. When two voicings
    score within rounding distance of each other, the easier-to-play
    and lower-on-the-neck one wins. The scorer's weights are all
    rational multiples of ``0.05`` so four decimal places is plenty."""
    return (round(v.score, 4), v.fingers, v.min_pressed_fret)


def _drop_subsets(voicings: list[Voicing]) -> list[Voicing]:
    """Drop voicings that are a strictly reduced version of another one
    in the list — same notes on a subset of strings, same bass, and no
    cheaper to play.

    Concretely: drop *B* if there exists *A* such that

      * ``A``'s sounding ``(string, pitch-class)`` pairs strictly
        contain ``B``'s — that is, every note ``B`` sounds is also in
        ``A`` on the same string, and ``A`` sounds at least one more
        string;
      * the two share the same bass pitch class (so an inversion is
        never confused with a reduced root-position variant);
      * ``A`` does **not** require more fingers than ``B``.

    The finger guard is what stops the rule from eating
    "easier-to-play" small voicings such as Open G's ``x00000`` (no
    fingers, G in the bass) in favour of the larger ``500000`` (one
    finger pressing the low D up to a G octave below): the larger
    version uses *more* fingers per added string, so the rule keeps
    the smaller one.

    For a chord like F major, the canonical full barre ``133211``
    (four fingers) dominates the partial ``xx3211`` (four fingers,
    same notes on strings 2–5) — adding strings 0 and 1 to the barre
    requires no additional fingers, just covering them with the index
    already laid across the neck.

    The check is on sounding *(string, pitch-class)* pairs rather than
    pressed positions. That matters when a string that would otherwise
    be silent in the larger voicing sounds a different note in the
    smaller one — e.g. ``1 0 3 2 x x`` (with the open A string) is
    *not* a sounding-subset of the full barre on string 1 (where the
    full barre sounds C, not A), so it's correctly kept.
    """
    if len(voicings) <= 1:
        return voicings

    sounds = [_sounding_set(v) for v in voicings]
    basses = [_bass_pc(v) for v in voicings]
    fingers = [v.fingers for v in voicings]
    keep = [True] * len(voicings)

    for i in range(len(voicings)):
        if not keep[i]:
            continue
        for j in range(len(voicings)):
            if i == j or not keep[j]:
                continue
            if basses[i] != basses[j]:
                continue
            if sounds[i] < sounds[j] and fingers[j] <= fingers[i]:
                keep[i] = False
                break
    return [v for v, k in zip(voicings, keep) if k]


def _sounding_set(v: Voicing) -> set[tuple[int, int]]:
    """The set of ``(string_index, pitch_class)`` pairs the voicing
    sounds — muted strings contribute nothing."""
    return {
        (i, pc) for i, pc in enumerate(v.pitch_classes) if pc is not None
    }


def _dedup_by_fingering(
    voicings: list[Voicing],
    chord: Chord,
) -> list[Voicing]:
    """Collapse voicings that share the same set of pressed (string, fret)
    pairs *and* the same bass note.

    Two voicings with identical pressed positions but different bass
    notes are *different chords* (e.g. ``x 4 2 2 2 0`` with C# in the
    bass and ``0 4 2 2 2 0`` with E in the bass for A — same shape on
    strings 1–5, but the lower string flips the inversion). We bucket
    them separately so each survives.

    When two voicings collide on this key, the canonical is the one
    that puts the root in the bass (preferred), then the one with more
    sounding strings, then the one with the better score.

    Example collapsed pair: ``x02210`` (open Am, root in bass) and
    ``002210`` (same fingering, E in the bass) only collide if both
    reach the dedup — under the default ``inversions_only=False`` the
    inversion is filtered upstream so only one of them is ever seen
    here anyway.
    """
    root_pc = chord.root.pitch_class
    canonical: dict[tuple, Voicing] = {}
    for v in voicings:
        key = (_fingering_signature(v), _bass_pc(v))
        existing = canonical.get(key)
        if existing is None or _better_canonical(v, existing, root_pc):
            canonical[key] = v
    kept_ids = {id(v) for v in canonical.values()}
    return [v for v in voicings if id(v) in kept_ids]


def _fingering_signature(v: Voicing) -> tuple[tuple[int, int], ...]:
    """The set of pressed (string, fret) pairs, fret >= 1."""
    return tuple(
        (i, f) for i, f in enumerate(v.frets)
        if f is not None and f >= 1
    )


def _better_canonical(a: Voicing, b: Voicing, root_pc: int) -> bool:
    """Return True when *a* is a better canonical representative than *b*
    for an equivalence class.

    Tie-breakers, in order:
      1. Root in the bass (chord identity beats inversion).
      2. More sounding strings (richer voicing wins).
      3. Better (lower) score.
    """
    a_root = (_bass_pc(a) == root_pc)
    b_root = (_bass_pc(b) == root_pc)
    if a_root != b_root:
        return a_root
    if a.num_sounding != b.num_sounding:
        return a.num_sounding > b.num_sounding
    return a.score < b.score


def _bass_pc(v: Voicing) -> int | None:
    for pc in v.pitch_classes:
        if pc is not None:
            return pc
    return None


def _diversify(
    voicings: list[Voicing],
    target: int,
    min_distance: int,
) -> list[Voicing]:
    """Pick ``target`` voicings, round-robin across distinct bass notes.

    The algorithm groups the score-sorted candidates by their bass
    pitch class, then takes one from each group in turn until the
    target count is reached. Within a group, voicings that are too
    similar (per :func:`_diversity_distance`) to a higher-scored
    sibling are dropped so each bass doesn't surface five near-clones.

    The output order is *not* purely by score — it interleaves the
    bass notes. For a plain chord with the root in the bass (the
    default mode), there's only one group and the result reads
    best-to-worst by score. For inversions-only mode on a chord with
    several reachable bass notes (say E and C# for A), the result
    alternates: best-E, best-C#, second-best-E, second-best-C#,
    third-best-E, … This guarantees every inversion shows up early
    rather than after all positions of the dominant bass.

    The first voicing is always the strongest of the strongest group,
    so the canonical pick still leads the list.
    """
    if not voicings or target <= 0:
        return []

    # Group candidates by bass pitch class, preserving the score order
    # already present in the input.
    groups: dict[int | None, list[Voicing]] = {}
    for v in voicings:
        groups.setdefault(_bass_pc(v), []).append(v)

    # Within each bass-pc group, drop voicings that sit too close to a
    # higher-scored sibling. The bass-pc bonus inside
    # _diversity_distance never fires here because all members share
    # a bass, so the check reduces to pure voicing_distance comparisons.
    deduped_groups: list[list[Voicing]] = []
    for group in groups.values():
        kept = [group[0]]
        for v in group[1:]:
            if all(_diversity_distance(v, p) >= min_distance for p in kept):
                kept.append(v)
        deduped_groups.append(kept)

    # Order the groups so the strongest-best-scored bass leads the
    # round-robin. The first output voicing is therefore the overall
    # best — what the player would expect under the score column.
    deduped_groups.sort(key=lambda g: _voicing_rank_key(g[0]))

    # Round-robin: one from each group per cycle, until we have `target`.
    picked: list[Voicing] = []
    cursors = [0] * len(deduped_groups)
    while len(picked) < target:
        progress = False
        for i, group in enumerate(deduped_groups):
            if cursors[i] < len(group):
                picked.append(group[cursors[i]])
                cursors[i] += 1
                progress = True
                if len(picked) >= target:
                    break
        if not progress:
            break

    return picked


def _bass_index(v: Voicing) -> int | None:
    """Index of the lowest sounding string, or None if all muted."""
    for i, f in enumerate(v.frets):
        if f is not None:
            return i
    return None


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


def _diversity_distance(a: Voicing, b: Voicing) -> int:
    """Distance metric used specifically for diversification.

    Built on top of :func:`voicing_distance` but adds a heavy penalty
    when the two voicings have different bass *notes* (pitch classes).
    Bass changes are aurally striking — root-position vs first vs
    second inversion sound noticeably different even when the fingers
    barely move — so we want diversification to never collapse two
    voicings with different bass notes into one. Without this boost,
    Open G's ``x00000`` (G in the bass) and ``000000`` (D in the bass)
    sit at distance 1 (one mute toggle), and one would be filtered as
    a near-duplicate even though they are distinct chords.
    """
    base = voicing_distance(a, b)
    if _bass_pc(a) != _bass_pc(b):
        base += 10
    return base


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

        # 3b. Inversions-only mode: reject voicings whose bass *is* the
        # root. Slash chords already short-circuit through the `bass_pc`
        # check above, so this only fires when the user explicitly asked
        # for inversions without specifying which one.
        if (
            options.inversions_only
            and chord.bass is None
            and actual_bass_pc == chord.root.pitch_class
        ):
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

        # 5b. Reject anatomically awkward partial barres.
        #
        # A barre that starts at the lowest string (low_s == 0) yet does
        # not extend all the way to the highest string, with an *open*
        # string sounding above it, requires the player to anchor the
        # index finger at the bass and lift its tip away from the
        # outermost string at the very last moment. Most players —
        # ours included — find that physically impossible.
        # The classic A-shape partial barre (``x02220``) does not match
        # this rule because its barre starts at string 2, not string 0.
        if finger_info.barre is not None:
            barre_fret, low_s, high_s = finger_info.barre
            if low_s == 0:
                last_string = tuning.num_strings - 1
                if high_s < last_string:
                    for i in range(high_s + 1, last_string + 1):
                        c = choices[i]
                        if not _is_muted(c) and c.fret == 0:
                            return  # awkward partial barre

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
