"""Tests for the i18n module — translation table sanity and lookup."""

from __future__ import annotations

import pytest

from i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    format_error,
    normalise,
    t,
)


class TestLanguageTables:
    def test_default_is_supported(self):
        assert DEFAULT_LANGUAGE in SUPPORTED_LANGUAGES

    def test_each_supported_has_a_table(self):
        for lang in SUPPORTED_LANGUAGES:
            assert lang in TRANSLATIONS

    def test_tables_have_the_same_keys(self):
        # Missing a key in one language is the most common i18n bug; pin it.
        keys_per_lang = {lang: set(table) for lang, table in TRANSLATIONS.items()}
        reference = keys_per_lang[DEFAULT_LANGUAGE]
        for lang, keys in keys_per_lang.items():
            assert keys == reference, (
                f"language {lang!r} has different keys than {DEFAULT_LANGUAGE!r}: "
                f"missing={reference - keys}, extra={keys - reference}"
            )

    def test_html_keys_contain_markup(self):
        # By convention, *_html keys carry HTML and must be assigned via
        # innerHTML at the call site. Keep that contract honest.
        for lang, table in TRANSLATIONS.items():
            for key, value in table.items():
                if key.endswith("_html"):
                    assert "<" in value, (
                        f"{lang}/{key} ends in _html but has no markup"
                    )


class TestNormalise:
    def test_known_languages(self):
        assert normalise("fr") == "fr"
        assert normalise("en") == "en"

    def test_regional_variants(self):
        assert normalise("fr-FR") == "fr"
        assert normalise("en-GB") == "en"

    def test_case_insensitive(self):
        assert normalise("FR") == "fr"

    def test_unknown_falls_back(self):
        assert normalise("de") == DEFAULT_LANGUAGE
        assert normalise("") == DEFAULT_LANGUAGE
        assert normalise(None) == DEFAULT_LANGUAGE


class TestLookup:
    def test_known_key(self):
        assert t("fr", "chord_label") == "Accords"
        assert t("en", "chord_label") == "Chords"

    def test_unknown_lang_falls_back(self):
        # German is not supported; we should still get the FR translation.
        assert t("de", "chord_label") == "Accords"

    def test_unknown_key_returns_key(self):
        assert t("fr", "nonexistent_key_xyz") == "nonexistent_key_xyz"


class TestFormatError:
    def test_french(self):
        assert format_error("fr", "bad input") == "Erreur : bad input"

    def test_english(self):
        assert format_error("en", "bad input") == "Error: bad input"
