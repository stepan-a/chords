"""User-facing strings, indexed by language code.

Only the UI shell strings live here — chord notation is anglo-saxon in
both languages (``Cmaj7`` is ``Cmaj7`` regardless of the user's locale).
Chord-parser error messages bubble up in English; we localise only the
``Error:`` / ``Erreur :`` prefix in :func:`format_error`.

Convention: keys whose value contains HTML markup end with ``_html`` so
the renderer knows to assign via ``innerHTML`` instead of ``textContent``.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "fr"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("fr", "en")

TRANSLATIONS: dict[str, dict[str, str]] = {
    "fr": {
        "page_title": "Accords pour guitare",
        "h1": "Accords pour guitare",
        "lead_html": (
            "Suite d'accords en notation anglo-saxonne, séparés par "
            "espace, virgule ou <code>|</code> "
            "(ex. <code>Am F C G</code>, <code>Cmaj7 | D/F# | Bbm9</code>)."
        ),
        "chord_label": "Accords",
        "tuning_label": "Accordage",
        "labels_legend": "Étiquettes :",
        "label_fingers": "Doigts",
        "label_degrees": "Degrés (romains)",
        "label_notes": "Notes",
        "label_none": "Aucune",
        "loading": "Chargement du moteur Python (~10 Mo, mis en cache ensuite)…",
        "notes_prefix": "Notes : ",
        "notes_separator": " · ",
        "error_prefix": "Erreur : ",
        "no_voicings": (
            "Aucune position jouable trouvée "
            "(essaie un accordage différent ou simplifie l'accord)."
        ),
        "lang_label": "Langue :",
        "alternatives_hint": "Clique sur une position pour la sélectionner.",
    },
    "en": {
        "page_title": "Guitar chord diagrams",
        "h1": "Guitar chord diagrams",
        "lead_html": (
            "Chord progression in Anglo-Saxon notation, separated by "
            "space, comma or <code>|</code> "
            "(e.g. <code>Am F C G</code>, <code>Cmaj7 | D/F# | Bbm9</code>)."
        ),
        "chord_label": "Chords",
        "tuning_label": "Tuning",
        "labels_legend": "Labels:",
        "label_fingers": "Fingers",
        "label_degrees": "Degrees (Roman)",
        "label_notes": "Notes",
        "label_none": "None",
        "loading": "Loading Python runtime (~10 MB, cached afterwards)…",
        "notes_prefix": "Notes: ",
        "notes_separator": " · ",
        "error_prefix": "Error: ",
        "no_voicings": (
            "No playable voicings found "
            "(try a different tuning or simplify the chord)."
        ),
        "lang_label": "Language:",
        "alternatives_hint": "Click a position to select it.",
    },
}


def normalise(lang: str | None) -> str:
    """Return the closest supported language code, defaulting to FR."""
    if not lang:
        return DEFAULT_LANGUAGE
    # Accept "fr-FR", "en-US", etc.
    base = lang.split("-", 1)[0].lower()
    if base in SUPPORTED_LANGUAGES:
        return base
    return DEFAULT_LANGUAGE


def t(lang: str, key: str) -> str:
    """Look up a string for *lang*; fall back to FR, then to the key itself."""
    table = TRANSLATIONS.get(lang) or TRANSLATIONS[DEFAULT_LANGUAGE]
    if key in table:
        return table[key]
    fallback = TRANSLATIONS[DEFAULT_LANGUAGE]
    return fallback.get(key, key)


def format_error(lang: str, message: str) -> str:
    """Prefix a chord-parser error message with the localised label."""
    return t(lang, "error_prefix") + message
