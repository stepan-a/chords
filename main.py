"""Entry point for the PyScript-powered chord progression viewer.

Runs inside the browser (via Pyodide/PyScript).

The UI lets the user type a *progression* — a sequence of chord symbols
separated by spaces, commas or pipes — and shows one chord-diagram tile
per chord. Each tile is *collapsed* by default (only the currently-active
voicing visible); clicking the tile expands it to reveal the 5 alternatives.
Clicking an alternative makes it the active voicing.

Default voicing selection uses voice-leading distance: for each chord
after the first, the default is the candidate closest to the previously-
active voicing.  The user can override any tile's choice; subsequent
defaults then cascade from that choice, but tiles the user has manually
selected are sticky (they don't get auto-changed when something earlier
in the progression moves).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode

import js
from js import localStorage, navigator
from pyscript import document, when
from pyscript.ffi import create_proxy, to_js

from chords import Chord, ChordParseError, parse
from i18n import SUPPORTED_LANGUAGES, format_error, normalise, t
from render import RenderOptions, render_voicing
from tunings import ALL_TUNINGS, by_name
from voicings import (
    SearchOptions,
    Voicing,
    find_voicings,
    pick_closest_index,
)


# --- Persistent UI state ----------------------------------------------
#
# The user's current configuration (progression text, tuning, slider
# values, label mode, language) lives in two places:
#
#   * localStorage under `chord-app-state` — survives reloads.
#   * The URL fragment (#chord=…&tuning=…&…) — survives copy-paste and
#     makes the configuration shareable as a link.
#
# When both are present at page load, the **URL fragment wins**: it
# represents an explicit "open with this config" intent. The fragment
# is updated on every change so the user can always grab the address
# bar and send the link.
#
# The legacy single-key `chord-app-lang` localStorage entry written by
# older versions is read as a fallback so existing visitors keep their
# language preference; we don't write to it any more.

_STATE_KEY = "chord-app-state"
_LEGACY_LANG_KEY = "chord-app-lang"

_VALID_LABEL_MODES = ("fingers", "degrees", "notes", "none")


def _initial_language() -> str:
    """Default language used before state restoration runs.

    Reads the legacy single-key storage and falls back to the browser's
    Accept-Language list; the unified state restorer can still override
    this when it runs.
    """
    saved = None
    try:
        saved = localStorage.getItem(_LEGACY_LANG_KEY)
    except Exception:
        saved = None
    if saved in SUPPORTED_LANGUAGES:
        return saved
    return normalise(getattr(navigator, "language", None))


_lang: str = _initial_language()


def _ui_state() -> dict:
    """Snapshot every persisted UI control's current value.

    Returns plain strings (not the active-voicing tuple of integers,
    which is per-render state that we don't persist).
    """
    return {
        "chord": document.getElementById("chord-input").value,
        "tuning": document.getElementById("tuning-select").value,
        "label": _label_mode(),
        "positions": document.getElementById("positions-slider").value,
        "variety": document.getElementById("variety-slider").value,
        "inversions": "1" if document.getElementById("inversions-toggle").checked else "0",
        "lang": _lang,
    }


def _apply_ui_state(state: dict) -> None:
    """Push a restored or shared state dict back into the DOM controls.

    Every value is validated before being applied so a malformed URL
    fragment (or a stale localStorage entry from an older schema) can
    only leave the controls in a state already representable by the
    HTML defaults.
    """
    global _lang

    if isinstance(state.get("chord"), str):
        document.getElementById("chord-input").value = state["chord"]

    tuning = state.get("tuning")
    if isinstance(tuning, str) and tuning in {t.name for t in ALL_TUNINGS}:
        document.getElementById("tuning-select").value = tuning

    label = state.get("label")
    if label in _VALID_LABEL_MODES:
        for radio in document.querySelectorAll('input[name="label-mode"]'):
            radio.checked = (radio.value == label)

    positions = state.get("positions")
    if positions is not None:
        try:
            n = int(positions)
        except (TypeError, ValueError):
            n = None
        if n is not None and 1 <= n <= 16:
            document.getElementById("positions-slider").value = str(n)
            _sync_slider_output("positions-slider")

    variety = state.get("variety")
    if variety is not None:
        try:
            n = int(variety)
        except (TypeError, ValueError):
            n = None
        if n is not None and 0 <= n <= 12:
            document.getElementById("variety-slider").value = str(n)
            _sync_slider_output("variety-slider")

    inversions = state.get("inversions")
    if inversions is not None:
        # Accept both stringified "1"/"0" (from the URL fragment) and the
        # bool/int Python types JSON may decode from localStorage.
        toggle = document.getElementById("inversions-toggle")
        if isinstance(inversions, bool):
            toggle.checked = inversions
        elif isinstance(inversions, (int, float)):
            toggle.checked = bool(inversions)
        elif isinstance(inversions, str):
            toggle.checked = inversions in ("1", "true", "True", "on")

    lang = state.get("lang")
    if isinstance(lang, str) and lang in SUPPORTED_LANGUAGES:
        _lang = lang


def _state_from_url() -> dict | None:
    """Parse the URL fragment as `key=value&…` if any keys are present."""
    raw = js.window.location.hash
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("#"):
        raw = raw[1:]
    if not raw:
        return None
    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    return parsed or None


def _state_from_storage() -> dict | None:
    """Read the JSON blob from localStorage, ignoring corrupted entries."""
    try:
        raw = localStorage.getItem(_STATE_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _persist_state() -> None:
    """Write current UI state to localStorage and to the URL fragment.

    Called at the end of every render pass; both writes are best-effort
    (private-mode browsers may refuse localStorage; some embeddings may
    refuse history.replaceState).
    """
    state = _ui_state()
    try:
        localStorage.setItem(_STATE_KEY, json.dumps(state))
    except Exception:
        pass
    try:
        new_hash = "#" + urlencode(state)
        js.window.history.replaceState(None, "", new_hash)
    except Exception:
        pass


def _restore_state() -> None:
    """Apply state from the URL fragment if any, else from localStorage."""
    state = _state_from_url() or _state_from_storage()
    if state:
        _apply_ui_state(state)


# --- Debouncing -------------------------------------------------------
#
# A keystroke in the chord field, or a fingertip dragging the variety
# slider, fires the `input` event on every pixel. Each of those
# triggers update_progression() which can take 100–200 ms on mobile —
# without coalescing we burn through events faster than they can be
# processed and the UI lags.
#
# debounce(key, fn, delay_ms) schedules `fn` to run after `delay_ms`
# of quiet. A new call with the same key cancels the previously-
# pending timer, so only the final event in a burst actually runs.
# Each event source has its own key so chord-input and slider
# debounces don't interfere with each other.

_DEBOUNCE_TIMERS: dict[str, int] = {}


def debounce(key: str, fn, delay_ms: int) -> None:
    pending = _DEBOUNCE_TIMERS.get(key)
    if pending is not None:
        js.clearTimeout(pending)
    _DEBOUNCE_TIMERS[key] = js.setTimeout(create_proxy(fn), delay_ms)


# --- Progression state -------------------------------------------------

@dataclass
class ChordSlot:
    """One chord position in the progression, with its computed voicings."""
    symbol: str
    chord: Chord | None
    error: str | None
    voicings: list[Voicing] = field(default_factory=list)
    active_index: int = 0
    is_user_picked: bool = False
    expanded: bool = False


_progression: list[ChordSlot] = []


def _split_progression(text: str) -> list[str]:
    """Tokenise a progression string.

    Accepts whitespace, commas and pipes as separators. Multiple
    consecutive separators collapse to one; leading/trailing whitespace
    is stripped.
    """
    # Normalise separators to whitespace, then split-on-whitespace.
    cleaned = re.sub(r"[|,]+", " ", text)
    return cleaned.split()


def _options() -> RenderOptions:
    return RenderOptions(label_mode=_label_mode())


def _label_mode() -> str:
    for radio in document.querySelectorAll('input[name="label-mode"]'):
        if radio.checked:
            return radio.value
    return "fingers"


def _current_tuning():
    name = document.getElementById("tuning-select").value
    return by_name(name)


def _search_options() -> SearchOptions:
    """Read the sliders and the inversions toggle and build a
    SearchOptions object.

    Slider boundaries (mirroring the HTML attributes):
      - positions-slider:  1..16, default 8
      - variety-slider:    0..12, default 5  (mapped to min_diversity_distance)

    When the variety slider sits at 0 we disable diversification entirely;
    everything else just feeds the min_diversity_distance threshold. The
    inversions toggle is binary: off = root-position only,
    on = inversions only. A slash chord forces its bass either way.
    """
    try:
        limit = int(document.getElementById("positions-slider").value)
    except (TypeError, ValueError):
        limit = 8
    try:
        variety = int(document.getElementById("variety-slider").value)
    except (TypeError, ValueError):
        variety = 5
    inversions = bool(document.getElementById("inversions-toggle").checked)
    return SearchOptions(
        limit=max(1, limit),
        diversify=(variety > 0),
        min_diversity_distance=max(0, variety),
        inversions_only=inversions,
    )


# --- Progression update -----------------------------------------------

def update_progression() -> None:
    """Re-parse the input text, refresh the slot list, cascade defaults."""
    try:
        text = document.getElementById("chord-input").value
        symbols = _split_progression(text)

        try:
            tuning = _current_tuning()
        except KeyError:
            tuning = ALL_TUNINGS[0]  # fall back to standard

        search_opts = _search_options()
        new_state: list[ChordSlot] = []
        for i, sym in enumerate(symbols):
            slot = _build_slot(sym, tuning, search_opts, previous=_get(i))
            new_state.append(slot)

        _progression[:] = new_state
        _cascade_defaults()
        _render()
        _persist_state()
    except Exception as exc:
        _show_fatal_error(exc)


def _show_fatal_error(exc: BaseException) -> None:
    """Surface unexpected exceptions to the page so we don't fail silently
    in the browser. Comment out once we've shaken out the obvious bugs."""
    import traceback

    container = document.getElementById("voicings")
    if container is None:
        return
    tb = traceback.format_exc()
    container.innerHTML = (
        '<div style="background:#fff5f5; border:1px solid #c00;'
        ' padding:12px; border-radius:6px; color:#900;'
        ' font-family:ui-monospace,monospace; font-size:12px;'
        ' white-space:pre-wrap;">'
        + _html_escape(f"{type(exc).__name__}: {exc}\n\n{tb}")
        + "</div>"
    )


def _get(index: int) -> ChordSlot | None:
    if 0 <= index < len(_progression):
        return _progression[index]
    return None


def _build_slot(
    symbol: str,
    tuning,
    search_opts: SearchOptions,
    previous: ChordSlot | None,
) -> ChordSlot:
    """Build a slot for one chord symbol, preserving user state when relevant.

    Carrying over ``is_user_picked`` across rebuilds (triggered by tuning,
    label-mode or slider changes) needs care: the index of a user-picked
    voicing can shift — or disappear — when the candidate list is
    recomputed with new parameters. We look up the previous active voicing
    by fret pattern in the new list. If it still exists, we re-anchor on
    that shape. If it's gone (e.g. the user lowered the limit slider
    below where their pick used to sit), we drop the user pick and fall
    back to the default cascade.
    """
    try:
        chord = parse(symbol)
    except ChordParseError as e:
        return ChordSlot(symbol=symbol, chord=None, error=str(e))

    voicings = find_voicings(chord, tuning, search_opts)

    active_index = 0
    is_user_picked = False
    expanded = False
    if (
        previous is not None
        and previous.symbol == symbol
        and previous.chord is not None
        and voicings
    ):
        expanded = previous.expanded
        if previous.is_user_picked and previous.voicings:
            target_shape = previous.voicings[previous.active_index].frets
            new_idx = next(
                (i for i, v in enumerate(voicings) if v.frets == target_shape),
                None,
            )
            if new_idx is not None:
                active_index = new_idx
                is_user_picked = True
            # else: the user-picked voicing is no longer available,
            # so we fall through to the default cascade.
        else:
            # No manual pick to preserve; the cascade will overwrite the
            # active index anyway, but we still clamp defensively.
            active_index = min(previous.active_index, len(voicings) - 1)

    return ChordSlot(
        symbol=symbol,
        chord=chord,
        error=None,
        voicings=voicings,
        active_index=active_index,
        is_user_picked=is_user_picked,
        expanded=expanded,
    )


def _cascade_defaults() -> None:
    """Re-pick the default voicing of every non-user-picked slot.

    Cascades left-to-right: each non-sticky slot's default is the voicing
    in its candidates list closest to the previous slot's *currently
    active* voicing (whether that one is sticky or itself a default).
    """
    prev_voicing: Voicing | None = None
    for slot in _progression:
        if slot.chord is None or not slot.voicings:
            continue
        if not slot.is_user_picked:
            slot.active_index = pick_closest_index(slot.voicings, prev_voicing)
        prev_voicing = slot.voicings[slot.active_index]


# --- Slot-level interactions ------------------------------------------

def toggle_expanded(chord_idx: int) -> None:
    if not (0 <= chord_idx < len(_progression)):
        return
    slot = _progression[chord_idx]
    slot.expanded = not slot.expanded
    _render()


def pick_voicing(chord_idx: int, voicing_idx: int) -> None:
    """User selected a non-default voicing — mark it sticky and re-cascade.

    When the picked voicing is an inversion of a plain chord (no slash
    bass in the input), we rewrite the chord text in the input field
    so it becomes the slash form (e.g. ``F`` → ``F/C``). This keeps the
    UI honest — the chord name reflects what's actually played — and
    makes the choice survive a reload because the URL fragment / local
    storage now carry the explicit slash chord.

    Re-parsing as a slash chord forces the bass on all subsequent
    voicing alternatives of that slot. To revert to root-position F,
    the user edits the text manually to drop the ``/C``.
    """
    if not (0 <= chord_idx < len(_progression)):
        return
    slot = _progression[chord_idx]
    if not (0 <= voicing_idx < len(slot.voicings)):
        return

    selected = slot.voicings[voicing_idx]
    new_symbol = _display_title(selected, slot.chord)

    if new_symbol is not None and new_symbol != slot.symbol:
        # The pick is an inversion of a non-slash chord. Rewrite the
        # slot in place as a slash chord, then mirror the change back
        # into the chord-input field.
        try:
            new_chord = parse(new_symbol)
        except ChordParseError:
            return  # we built this symbol ourselves; shouldn't fail
        try:
            tuning = _current_tuning()
        except KeyError:
            tuning = ALL_TUNINGS[0]
        new_voicings = find_voicings(new_chord, tuning, _search_options())
        # Keep the user's exact fret pattern if it still appears in the
        # new candidate list; otherwise default to the first voicing.
        target_shape = selected.frets
        new_active_idx = next(
            (i for i, v in enumerate(new_voicings) if v.frets == target_shape),
            0,
        )
        _progression[chord_idx] = ChordSlot(
            symbol=new_symbol,
            chord=new_chord,
            error=None,
            voicings=new_voicings,
            active_index=new_active_idx,
            is_user_picked=True,
            expanded=False,
        )
        _replace_chord_in_input(chord_idx, new_symbol)
    else:
        # Same symbol — just bookmark the selection in memory.
        slot.active_index = voicing_idx
        slot.is_user_picked = True
        slot.expanded = False

    # Cascade forward through subsequent non-user-picked slots. Earlier
    # slots are upstream of this choice and stay put.
    _cascade_defaults()
    _render()
    _persist_state()


def _replace_chord_in_input(idx: int, new_symbol: str) -> None:
    """Rewrite the ``idx``-th chord token in the chord-input field.

    We walk the raw text alternately by chord-token / separator runs so
    that the user's original separators (``|``, commas, padded spaces)
    survive the rewrite — only the targeted chord changes.
    """
    input_el = document.getElementById("chord-input")
    raw = input_el.value
    # Split into alternating non-separator and separator chunks. The
    # capturing group keeps the separators in the output.
    parts = re.split(r"([\s,|]+)", raw)
    seen = 0
    for i, part in enumerate(parts):
        if not part:
            continue
        if re.fullmatch(r"[\s,|]+", part):
            continue
        if seen == idx:
            parts[i] = new_symbol
            input_el.value = "".join(parts)
            return
        seen += 1
    # Token not found (input changed concurrently?) — last-resort
    # rejoin so we at least don't lose the user's data.
    tokens = _split_progression(raw)
    if 0 <= idx < len(tokens):
        tokens[idx] = new_symbol
        input_el.value = " ".join(tokens)


# --- DOM rendering ----------------------------------------------------

def _render() -> None:
    """Rebuild the #voicings container from _progression."""
    try:
        container = document.getElementById("voicings")
        container.innerHTML = ""

        if not _progression:
            return

        opts = _options()
        for idx, slot in enumerate(_progression):
            container.appendChild(_render_slot(idx, slot, opts))
    except Exception as exc:
        _show_fatal_error(exc)


def _render_slot(idx: int, slot: ChordSlot, opts: RenderOptions):
    """Build one chord-slot element."""
    slot_el = document.createElement("div")
    slot_el.className = "chord-slot"
    if slot.expanded:
        slot_el.className += " expanded"
    slot_el.setAttribute("data-idx", str(idx))

    if slot.error is not None or slot.chord is None:
        msg = format_error(_lang, slot.error or "—")
        slot_el.innerHTML = (
            f'<div class="chord-error">'
            f'<div class="chord-error-symbol">{_html_escape(slot.symbol)}</div>'
            f'<div class="chord-error-message">{_html_escape(msg)}</div>'
            f'</div>'
        )
        return slot_el

    if not slot.voicings:
        slot_el.innerHTML = (
            f'<div class="chord-error">'
            f'<div class="chord-error-symbol">{_html_escape(slot.symbol)}</div>'
            f'<div class="chord-error-message">{_html_escape(t(_lang, "no_voicings"))}</div>'
            f'</div>'
        )
        return slot_el

    # Active voicing (always shown).
    active = slot.voicings[slot.active_index]
    active_el = document.createElement("div")
    active_el.className = "voicing active"
    active_el.setAttribute("data-vidx", str(slot.active_index))
    active_el.setAttribute(
        "title",
        t(_lang, "alternatives_hint"),
    )
    active_el.innerHTML = render_voicing(
        active, slot.chord, opts,
        title_override=_display_title(active, slot.chord),
    )
    slot_el.appendChild(active_el)

    # Notes caption.
    notes_el = document.createElement("div")
    notes_el.className = "slot-notes"
    caption = (
        t(_lang, "notes_prefix")
        + t(_lang, "notes_separator").join(str(n) for n in slot.chord.notes())
    )
    bass_text = _bass_caption(active, slot.chord)
    if bass_text:
        # The bass note differs from the chord's root — it's an inversion.
        # Flag it inline so the user can tell at a glance.
        caption += "  ·  " + t(_lang, "bass_label") + " : " + bass_text
    notes_el.textContent = caption
    slot_el.appendChild(notes_el)

    # Alternatives panel (only when expanded).
    if slot.expanded and len(slot.voicings) > 1:
        alts_el = document.createElement("div")
        alts_el.className = "alternatives"
        for vidx, v in enumerate(slot.voicings):
            if vidx == slot.active_index:
                continue
            alt = document.createElement("div")
            alt.className = "voicing alternative"
            alt.setAttribute("data-vidx", str(vidx))
            alt.innerHTML = render_voicing(
                v, slot.chord, opts,
                title_override=_display_title(v, slot.chord),
            )
            alts_el.appendChild(alt)
        slot_el.appendChild(alts_el)

    return slot_el


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _display_title(voicing: Voicing, chord: Chord) -> str | None:
    """Compute the chord-name string to draw above this voicing.

    When the voicing is an inversion *and* the user didn't already
    write a slash chord, we append the bass note to the chord symbol
    so the diagram shows ``"F/C"`` rather than just ``"F"``. When the
    chord input already carries an explicit bass (e.g. ``"F/C"``), or
    when the voicing is in root position, we return ``None`` so the
    renderer falls back to ``chord.symbol``.
    """
    if chord.bass is not None:
        # The user typed the slash explicitly; chord.symbol already
        # carries the bass — don't double it up to ``F/C/C``.
        return None
    bass_text = _bass_caption(voicing, chord)
    if not bass_text:
        return None
    return f"{chord.symbol}/{bass_text}"


def _bass_caption(voicing: Voicing, chord: Chord) -> str:
    """Return the spelled name of the voicing's bass note when it
    differs from the chord's root, else an empty string.

    Used to label inversions in the slot caption — root-position
    voicings get nothing extra, inversions get a ``"basse : E"`` /
    ``"bass: E"`` tail.
    """
    bass_idx = next(
        (i for i, f in enumerate(voicing.frets) if f is not None),
        None,
    )
    if bass_idx is None:
        return ""
    bass_pc = voicing.pitch_classes[bass_idx]
    if bass_pc is None or bass_pc == chord.root.pitch_class:
        return ""
    bass_degree = voicing.degrees[bass_idx]
    if bass_degree is None:
        return ""
    # Look the spelled name up via the chord's notes(), which preserve
    # the enharmonic spelling (e.g. F# rather than Gb for D major).
    for (deg, _), note in zip(chord.intervals, chord.notes()):
        if deg == bass_degree:
            return str(note)
    return ""


# --- Translation application ------------------------------------------

def apply_translations() -> None:
    document.documentElement.lang = _lang
    document.title = t(_lang, "page_title")
    for el in document.querySelectorAll("[data-i18n]"):
        key = el.getAttribute("data-i18n")
        if key.endswith("_html"):
            el.innerHTML = t(_lang, key)
        else:
            el.textContent = t(_lang, key)
    for btn in document.querySelectorAll(".lang-switcher button"):
        if btn.getAttribute("data-lang") == _lang:
            btn.classList.add("active")
        else:
            btn.classList.remove("active")


# --- One-time DOM setup -----------------------------------------------

_select = document.getElementById("tuning-select")
_select.innerHTML = ""
for tuning in ALL_TUNINGS:
    opt = document.createElement("option")
    opt.value = tuning.name
    pitches = " ".join(str(p.note) for p in tuning.strings)
    opt.textContent = f"{tuning.name} ({pitches})"
    _select.appendChild(opt)

document.getElementById("loading").classList.add("hidden")


# --- Event wiring -----------------------------------------------------

@when("input", "#chord-input")
def _on_chord_input(event):
    # Slightly more debouncing on the chord field because half-typed
    # chord symbols ("Cm" mid-typing "Cmaj7") routinely fail to parse,
    # so we'd flash an error to the user between each pair of keystrokes.
    debounce("chord", update_progression, 200)


@when("change", "#tuning-select")
def _on_tuning_change(event):
    update_progression()


@when("change", 'input[name="label-mode"]')
def _on_label_change(event):
    # Only the SVG rendering changes; voicings and active indices stay.
    _render()
    _persist_state()


@when("input", "#positions-slider")
def _on_positions_input(event):
    _sync_slider_output("positions-slider")
    debounce("slider", update_progression, 120)


@when("input", "#variety-slider")
def _on_variety_input(event):
    _sync_slider_output("variety-slider")
    debounce("slider", update_progression, 120)


@when("change", "#inversions-toggle")
def _on_inversions_change(event):
    update_progression()


def _sync_slider_output(slider_id: str) -> None:
    """Mirror a slider's current value into the adjacent <output>.

    The two are linked semantically via the ``for`` attribute on the
    <output>, but browsers don't update it automatically — we do it
    in Python so the user sees the live value while dragging.
    """
    slider = document.getElementById(slider_id)
    out = document.querySelector(f'output[for="{slider_id}"]')
    if slider is None or out is None:
        return
    out.textContent = slider.value


@when("click", ".lang-switcher button")
def _on_lang_change(event):
    global _lang
    new_lang = event.currentTarget.getAttribute("data-lang")
    if new_lang not in SUPPORTED_LANGUAGES or new_lang == _lang:
        return
    _lang = new_lang
    apply_translations()
    _render()  # so per-slot notes/error captions pick up the new language
    _persist_state()


# --- Sharing ----------------------------------------------------------
#
# The URL fragment is kept in sync by _persist_state(), so the canonical
# shareable URL is just window.location.href at any moment. The button
# tries the native Web Share API first (which on Android and iOS opens
# the OS share sheet — Messages, Mail, WhatsApp, etc.) and falls back
# to clipboard.writeText on desktop or wherever Web Share is missing.

@when("click", "#share-button")
async def _on_share_click(event):
    url = js.window.location.href
    nav = js.navigator

    # 1. Native share sheet — Android, iOS Safari, recent macOS Safari.
    if getattr(nav, "share", None) is not None:
        try:
            payload = to_js({
                "title": t(_lang, "share_title"),
                "url": url,
            })
            await nav.share(payload)
            return
        except Exception:
            # User cancelled the share, or the platform refused
            # (e.g. share required user activation which we lost
            # crossing the await). Fall through to clipboard.
            pass

    # 2. Clipboard fallback — desktop browsers, restricted contexts.
    try:
        await nav.clipboard.writeText(url)
        _flash_share_feedback(t(_lang, "share_copied"), error=False)
    except Exception:
        _flash_share_feedback(t(_lang, "share_failed"), error=True)


def _flash_share_feedback(message: str, error: bool, duration_ms: int = 2500) -> None:
    """Briefly show *message* next to the share button, then fade it out."""
    el = document.getElementById("share-feedback")
    if el is None:
        return
    el.textContent = message
    el.classList.remove("error")
    if error:
        el.classList.add("error")
    el.classList.add("visible")

    def _hide():
        el.classList.remove("visible")
    js.setTimeout(create_proxy(_hide), duration_ms)


@when("click", "#voicings")
def _on_voicings_click(event):
    """Event-delegated handler for clicks on chord-slot tiles."""
    target = event.target
    voicing_el = target.closest(".voicing")
    if voicing_el is None:
        return
    slot_el = voicing_el.closest(".chord-slot")
    if slot_el is None:
        return
    try:
        chord_idx = int(slot_el.getAttribute("data-idx"))
        voicing_idx = int(voicing_el.getAttribute("data-vidx"))
    except (TypeError, ValueError):
        return

    if voicing_el.classList.contains("active"):
        toggle_expanded(chord_idx)
    elif voicing_el.classList.contains("alternative"):
        pick_voicing(chord_idx, voicing_idx)


# --- Initial render ---------------------------------------------------
#
# Order matters: state restoration may overwrite the default language,
# so we apply translations *after* it. The initial update_progression()
# then runs against the restored controls and re-persists the result
# (which writes the canonical URL fragment if the page was opened
# without one).

_restore_state()
apply_translations()
update_progression()
