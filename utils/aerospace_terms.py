"""Compatibility helpers for the translator package."""

from typing import Callable

from translator.dictionary import dictionary_replace
from translator.dictionary import load_dictionary
from translator.dictionary import save_dictionary
from translator.translator import Translator


def load_terms() -> dict[str, str]:
    return {
        source: entry["translation"]
        for source, entry in load_dictionary().items()
    }


def save_terms(terms: dict[str, str]) -> None:
    save_dictionary(
        {
            source: {"translation": translation, "type": "term"}
            for source, translation in terms.items()
        }
    )


def dictionary_translation(text: str, terms: dict[str, str] | None = None) -> str:
    entries = None

    if terms is not None:
        entries = {
            source: {"translation": translation, "type": "term"}
            for source, translation in terms.items()
        }

    translated, _matched = dictionary_replace(text, entries)
    return translated


def translate_term(
    text: str,
    enabled: bool,
    mode: str,
    ai_translator: Callable[[str], str] | None = None,
) -> str:
    if not enabled:
        return text

    selected_mode = "hybrid" if mode == "ai_confirm" else mode
    callback = None

    if ai_translator is not None:
        callback = lambda value, _term_type, _long_text: ai_translator(value)

    return Translator(mode=selected_mode, ai_callable=callback).translate_term(text)


def ai_translate_term(text: str, settings) -> str:
    return Translator(settings=settings, mode="ai").translate_term(text)
