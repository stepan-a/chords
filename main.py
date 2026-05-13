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

import re
from dataclasses import dataclass, field

from js import localStorage, navigator
from pyscript import document, when

from chords import Chord, ChordParseError, parse
from i18n import SUPPORTED_LANGUAGES, format_error, normalise, t
from render import RenderOptions, render_voicing
from tunings import ALL_TUNINGS, by_name
from voicings import Voicing, find_voicings, pick_closest_index


# --- Language state ----------------------------------------------------

_STORAGE_KEY = "chord-app-lang"


def _initial_language() -> str:
    saved = None
    try:
        saved = localStorage.getItem(_STORAGE_KEY)
    except Exception:
        saved = None
    if saved in SUPPORTED_LANGUAGES:
        return saved
    return normalise(getattr(navigator, "language", None))


_lang: str = _initial_language()


def _save_language(lang: str) -> None:
    try:
        localStorage.setItem(_STORAGE_KEY, lang)
    except Exception:
        pass


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

        new_state: list[ChordSlot] = []
        for i, sym in enumerate(symbols):
            slot = _build_slot(sym, tuning, previous=_get(i))
            new_state.append(slot)

        _progression[:] = new_state
        _cascade_defaults()
        _render()
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
    previous: ChordSlot | None,
) -> ChordSlot:
    """Build a slot for one chord symbol, preserving user state when relevant."""
    try:
        chord = parse(symbol)
    except ChordParseError as e:
        return ChordSlot(symbol=symbol, chord=None, error=str(e))

    voicings = find_voicings(chord, tuning)

    # If this slot used to hold the same symbol, preserve user-picked
    # state and active index (clamped to the new voicings list).
    active_index = 0
    is_user_picked = False
    expanded = False
    if previous is not None and previous.symbol == symbol and previous.chord is not None:
        if voicings:
            active_index = min(previous.active_index, len(voicings) - 1)
        is_user_picked = previous.is_user_picked
        expanded = previous.expanded

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
    """User selected a non-default voicing — mark it sticky and re-cascade."""
    if not (0 <= chord_idx < len(_progression)):
        return
    slot = _progression[chord_idx]
    if not (0 <= voicing_idx < len(slot.voicings)):
        return
    slot.active_index = voicing_idx
    slot.is_user_picked = True
    slot.expanded = False

    # Cascade forward from this slot. (No cascade backward — earlier
    # slots are upstream of this choice.) We re-run the full cascade
    # for simplicity; tiles whose is_user_picked is True stay put.
    _cascade_defaults()
    _render()


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
    active_el.innerHTML = render_voicing(active, slot.chord, opts)
    slot_el.appendChild(active_el)

    # Notes caption.
    notes_el = document.createElement("div")
    notes_el.className = "slot-notes"
    notes_el.textContent = (
        t(_lang, "notes_prefix")
        + t(_lang, "notes_separator").join(str(n) for n in slot.chord.notes())
    )
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
            alt.innerHTML = render_voicing(v, slot.chord, opts)
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
    update_progression()


@when("change", "#tuning-select")
def _on_tuning_change(event):
    update_progression()


@when("change", 'input[name="label-mode"]')
def _on_label_change(event):
    # Only the SVG rendering changes; voicings and active indices stay.
    _render()


@when("click", ".lang-switcher button")
def _on_lang_change(event):
    global _lang
    new_lang = event.currentTarget.getAttribute("data-lang")
    if new_lang not in SUPPORTED_LANGUAGES or new_lang == _lang:
        return
    _lang = new_lang
    _save_language(_lang)
    apply_translations()
    _render()  # so per-slot notes/error captions pick up the new language


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

apply_translations()
update_progression()
