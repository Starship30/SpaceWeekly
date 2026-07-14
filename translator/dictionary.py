import json
import re
from pathlib import Path

from resources import bundled_resource_path
from resources import resource_path

DEFAULT_DICTIONARY = {
    "Starlink Group": {"translation": "Starlink批次", "type": "mission"},
    "SpaceX": {"translation": "SpaceX", "type": "organization"},
    "Falcon 9": {"translation": "猎鹰9号", "type": "rocket"},
    "Starship": {"translation": "星舰", "type": "rocket"},
    "Soyuz": {"translation": "联盟号", "type": "rocket"},
    "Low Earth Orbit": {"translation": "近地轨道", "type": "orbit"},
    "Sun-Synchronous Orbit": {"translation": "太阳同步轨道", "type": "orbit"},
    "Geostationary Transfer Orbit": {
        "translation": "地球同步转移轨道",
        "type": "orbit"
    },
    "Launch Successful": {"translation": "发射成功", "type": "status"},
}


def load_dictionary() -> dict[str, dict[str, str]]:
    path = _dictionary_path()

    if path.exists():
        entries = _read_entries(path, {})
        entries.update(
            {key: dict(value) for key, value in DEFAULT_DICTIONARY.items()}
        )
        return entries

    legacy = resource_path("config", "aerospace_terms.json")

    if legacy.exists():
        entries = _read_entries(legacy, DEFAULT_DICTIONARY)
    else:
        bundled = bundled_resource_path("translator", "dictionary.json")
        entries = _read_entries(bundled, DEFAULT_DICTIONARY)

    save_dictionary(entries)
    return entries


def save_dictionary(entries: dict[str, object]) -> None:
    _write_entries(_dictionary_path(), _normalize_entries(entries))


def load_pending_terms() -> dict[str, dict[str, str]]:
    path = _pending_path()

    if path.exists():
        return _read_entries(path, {}, allow_empty=True)

    save_pending_terms({})
    return {}


def save_pending_terms(entries: dict[str, object]) -> None:
    _write_entries(
        _pending_path(),
        _normalize_entries(entries, allow_empty=True),
    )


def add_pending_term(
    term: str,
    term_type: str,
    translation: str = "",
) -> bool:
    source = str(term or "").strip()

    if not source:
        return False

    pending = load_pending_terms()
    is_new = source not in pending
    current = pending.get(source, {})
    pending[source] = {
        "translation": str(translation or current.get("translation", "")).strip(),
        "type": str(term_type or current.get("type", "term")).strip() or "term",
    }
    save_pending_terms(pending)
    return is_new


def confirm_pending_term(
    term: str,
    translation: str | None = None,
    term_type: str | None = None,
) -> bool:
    pending = load_pending_terms()
    entry = pending.get(term)

    if entry is None:
        return False

    confirmed_translation = str(
        translation if translation is not None else entry.get("translation", "")
    ).strip()

    if not confirmed_translation:
        return False

    dictionary = load_dictionary()
    dictionary[term] = {
        "translation": confirmed_translation,
        "type": str(term_type or entry.get("type", "term")).strip() or "term",
    }
    save_dictionary(dictionary)
    pending.pop(term, None)
    save_pending_terms(pending)
    return True


def remove_pending_term(term: str) -> None:
    pending = load_pending_terms()

    if term in pending:
        pending.pop(term)
        save_pending_terms(pending)


def dictionary_replace(
    text: str,
    entries: dict[str, dict[str, str]] | None = None,
) -> tuple[str, bool]:
    original = str(text or "").strip()

    if not original:
        return "", False

    dictionary = entries if entries is not None else load_dictionary()

    for source, entry in dictionary.items():
        if source.casefold() == original.casefold():
            return entry["translation"], True

    translated = original
    matched = False

    for source in sorted(dictionary, key=len, reverse=True):
        translated, count = re.subn(
            re.escape(source),
            dictionary[source]["translation"],
            translated,
            flags=re.IGNORECASE,
        )
        matched = matched or count > 0

    return translated, matched


def pending_translation(term: str) -> str:
    entry = load_pending_terms().get(term, {})

    return str(entry.get("translation", "")).strip()


def _dictionary_path() -> Path:
    return resource_path("translator", "dictionary.json")


def _pending_path() -> Path:
    return resource_path("translator", "pending_terms.json")


def _read_entries(
    path: Path,
    fallback: dict[str, dict[str, str]],
    allow_empty: bool = False,
) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {key: dict(value) for key, value in fallback.items()}

    if not isinstance(data, dict):
        return {key: dict(value) for key, value in fallback.items()}

    return _normalize_entries(data, allow_empty=allow_empty)


def _normalize_entries(
    entries: dict[str, object],
    allow_empty: bool = False,
) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}

    for source, raw_entry in entries.items():
        term = str(source).strip()

        if isinstance(raw_entry, dict):
            translation = str(raw_entry.get("translation", "")).strip()
            term_type = str(raw_entry.get("type", "term")).strip() or "term"
        else:
            translation = str(raw_entry).strip()
            term_type = "term"

        if term and (translation or allow_empty):
            normalized[term] = {"translation": translation, "type": term_type}

    return normalized


def _write_entries(path: Path, entries: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
