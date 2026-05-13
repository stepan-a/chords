"""Entry point for the PyScript-powered chord viewer.

This script runs inside the browser (via Pyodide/PyScript). It owns:

  - reading the controls (chord symbol, tuning, label mode)
  - parsing the chord, searching voicings, rendering SVG
  - injecting the result into the DOM and updating the chord-tones line

DOM updates use ``textContent`` for plain text (auto-escaped) and
``innerHTML`` only for our own trusted SVG output.
"""

from pyscript import document, when

from chords import ChordParseError, parse
from render import RenderOptions, render_voicing
from tunings import ALL_TUNINGS, by_name
from voicings import find_voicings


# --- One-time DOM setup -------------------------------------------------

# Replace the static <option> list with one driven by the tuning registry
# so that adding a new preset in src/tunings.py automatically shows up here.
_select = document.getElementById("tuning-select")
_select.innerHTML = ""
for tuning in ALL_TUNINGS:
    opt = document.createElement("option")
    opt.value = tuning.name
    pitches = " ".join(str(p.note) for p in tuning.strings)
    opt.textContent = f"{tuning.name} ({pitches})"
    _select.appendChild(opt)

document.getElementById("loading").classList.add("hidden")


# --- Helpers ------------------------------------------------------------

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


# --- Main update loop ---------------------------------------------------

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
        _set_error(f"Erreur : {e}")
        _set_notes("")
        _clear_voicings()
        return

    _set_error("")
    _set_notes("Notes : " + " · ".join(str(n) for n in chord.notes()))

    tuning_name = document.getElementById("tuning-select").value
    try:
        tuning = by_name(tuning_name)
    except KeyError:
        _set_error(f"Accordage inconnu : {tuning_name}")
        _clear_voicings()
        return

    options = RenderOptions(label_mode=_label_mode())
    voicings = find_voicings(chord, tuning)

    container = document.getElementById("voicings")
    if not voicings:
        container.innerHTML = (
            '<div class="empty">Aucune position jouable trouvée '
            "(essaie un accordage différent ou simplifie l'accord).</div>"
        )
        return

    parts = []
    for v in voicings:
        parts.append(
            f'<div class="voicing">{render_voicing(v, chord, options)}</div>'
        )
    container.innerHTML = "".join(parts)


# --- Event wiring -------------------------------------------------------

@when("input", "#chord-input")
def _on_chord_input(event):
    update()


@when("change", "#tuning-select")
def _on_tuning_change(event):
    update()


@when("change", 'input[name="label-mode"]')
def _on_label_change(event):
    update()


# Initial render so the page isn't blank on load.
update()
