"""Entry point for the PyScript-powered chord viewer.

Runs inside the browser (via Pyodide/PyScript). Responsibilities:

  - apply the i18n strings to the static layout (everything with a
    ``data-i18n`` attribute) and persist the language preference,
  - read the controls (chord symbol, tuning, label mode, language),
  - parse the chord, search voicings, render SVG,
  - inject the result into the DOM and update the chord-tones line.

DOM updates use ``textContent`` for plain text (auto-escaped) and
``innerHTML`` only for our own trusted SVG output and translation strings
that carry markup (keys ending in ``_html``).
"""

from js import localStorage, navigator
from pyscript import document, when

from chords import ChordParseError, parse
from i18n import SUPPORTED_LANGUAGES, format_error, normalise, t
from render import RenderOptions, render_voicing
from tunings import ALL_TUNINGS, by_name
from voicings import find_voicings


# --- Language state ----------------------------------------------------

_STORAGE_KEY = "chord-app-lang"


def _initial_language() -> str:
    """Pick the language: saved preference, else browser locale, else FR."""
    saved = None
    try:
        saved = localStorage.getItem(_STORAGE_KEY)
    except Exception:
        # localStorage may be unavailable (private mode in some browsers,
        # sandboxed iframes, etc.). Ignore and fall through.
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


# --- Translation application ------------------------------------------

def apply_translations() -> None:
    """Walk every ``[data-i18n]`` element and update its text/HTML.

    By convention, keys ending in ``_html`` (e.g. ``lead_html``) carry
    HTML markup and use ``innerHTML``; everything else uses
    ``textContent`` (XSS-safe even if a future translation contains
    user-influenced data — which it won't, but belt and braces).
    """
    document.documentElement.lang = _lang
    document.title = t(_lang, "page_title")
    for el in document.querySelectorAll("[data-i18n]"):
        key = el.getAttribute("data-i18n")
        if key.endswith("_html"):
            el.innerHTML = t(_lang, key)
        else:
            el.textContent = t(_lang, key)
    # Update the active button.
    for btn in document.querySelectorAll(".lang-switcher button"):
        if btn.getAttribute("data-lang") == _lang:
            btn.classList.add("active")
        else:
            btn.classList.remove("active")


# --- One-time DOM setup -----------------------------------------------

# Replace the static <option> list with one driven by the tuning registry
# so that adding a new preset in src/tunings.py automatically shows up.
_select = document.getElementById("tuning-select")
_select.innerHTML = ""
for tuning in ALL_TUNINGS:
    opt = document.createElement("option")
    opt.value = tuning.name
    pitches = " ".join(str(p.note) for p in tuning.strings)
    opt.textContent = f"{tuning.name} ({pitches})"
    _select.appendChild(opt)

document.getElementById("loading").classList.add("hidden")


# --- Helpers ----------------------------------------------------------

def _label_mode() -> str:
    for radio in document.querySelectorAll('input[name="label-mode"]'):
        if radio.checked:
            return radio.value
    return "fingers"


def _set_error(msg: str) -> None:
    document.getElementById("error").textContent = msg


def _set_notes(text: str) -> None:
    document.getElementById("chord-notes").textContent = text


def _clear_voicings() -> None:
    document.getElementById("voicings").innerHTML = ""


# --- Main update loop -------------------------------------------------

def update(*_args, **_kwargs):
    """Re-parse the chord and re-render the grid. Idempotent and cheap
    enough to call on every keystroke (~50 ms for a 6-voicing render).
    """
    symbol = document.getElementById("chord-input").value.strip()
    if not symbol:
        _set_notes("")
        _set_error("")
        _clear_voicings()
        return

    try:
        chord = parse(symbol)
    except ChordParseError as e:
        _set_error(format_error(_lang, str(e)))
        _set_notes("")
        _clear_voicings()
        return

    _set_error("")
    _set_notes(
        t(_lang, "notes_prefix")
        + t(_lang, "notes_separator").join(str(n) for n in chord.notes())
    )

    tuning_name = document.getElementById("tuning-select").value
    try:
        tuning = by_name(tuning_name)
    except KeyError:
        _set_error(format_error(_lang, f"unknown tuning {tuning_name!r}"))
        _clear_voicings()
        return

    options = RenderOptions(label_mode=_label_mode())
    voicings = find_voicings(chord, tuning)

    container = document.getElementById("voicings")
    if not voicings:
        container.innerHTML = (
            f'<div class="empty">{t(_lang, "no_voicings")}</div>'
        )
        return

    parts = []
    for v in voicings:
        parts.append(
            f'<div class="voicing">{render_voicing(v, chord, options)}</div>'
        )
    container.innerHTML = "".join(parts)


# --- Event wiring -----------------------------------------------------

@when("input", "#chord-input")
def _on_chord_input(event):
    update()


@when("change", "#tuning-select")
def _on_tuning_change(event):
    update()


@when("change", 'input[name="label-mode"]')
def _on_label_change(event):
    update()


@when("click", ".lang-switcher button")
def _on_lang_change(event):
    global _lang
    new_lang = event.currentTarget.getAttribute("data-lang")
    if new_lang not in SUPPORTED_LANGUAGES or new_lang == _lang:
        return
    _lang = new_lang
    _save_language(_lang)
    apply_translations()
    # Re-render so that error messages, notes prefix, "no voicings"
    # placeholder, etc. pick up the new language.
    update()


# --- Initial render ---------------------------------------------------

apply_translations()
update()
