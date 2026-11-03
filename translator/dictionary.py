import json
import re
import shutil
from uuid import uuid4
from pathlib import Path

from resources import bundled_resource_path
from resources import resource_path

DEFAULT_DICTIONARY = {
    "Starlink Group": {"translation": "Starlink Group", "type": "mission"},
    "SpaceX": {"translation": "SpaceX", "type": "organization"},
    "Falcon 9": {"translation": "猎鹰9号", "type": "rocket"},
    "Starship": {"translation": "星舰", "type": "rocket"},
    "Soyuz": {"translation": "联盟号", "type": "rocket"},
    "Gravity-1": {"translation": "引力-1", "type": "rocket", "aliases": ["引力一号"],
                  "source_url": "https://orienspace.com/productPage"},
    "Orienspace": {"translation": "东方空间", "type": "organization",
                   "source_url": "https://www.orienspace.com/aboutPage"},
    "Jiuquan": {"translation": "酒泉", "type": "location"},
    "Taiyuan": {"translation": "太原", "type": "location"},
    "Xichang": {"translation": "西昌", "type": "location"},
    "Wenchang": {"translation": "文昌", "type": "location"},
    "Haiyang": {"translation": "海阳", "type": "location"},
    "Low Earth Orbit": {"translation": "近地轨道", "type": "orbit"},
    "Sun-Synchronous Orbit": {"translation": "太阳同步轨道", "type": "orbit"},
    "Geostationary Transfer Orbit": {
        "translation": "地球同步转移轨道",
        "type": "orbit"
    },
    "Launch Successful": {"translation": "发射成功", "type": "status"},
}


def load_dictionary() -> dict[str, dict[str, str]]:
    # Resource defaults are a baseline; workspace edits and confirmed legacy terms win.
    entries = {key: dict(value) for key, value in DEFAULT_DICTIONARY.items()}
    bundled = bundled_resource_path("translator", "dictionary.json")
    entries.update(_read_entries(bundled, {}))
    defaults = {key.casefold(): value['translation'] for key, value in entries.items()}
    legacy = resource_path("config", "aerospace_terms.json")
    for source in (legacy, _dictionary_path()):
        if not source.exists():
            continue
        # Never turn a damaged user dictionary into a new empty one.
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("术语词典格式无效，请从备份恢复")
        for key, entry in _normalize_entries(raw).items():
            previous = next((k for k in entries if k.casefold() == key.casefold()), key)
            if (entries.get(previous, {}).get('human_status') == 'confirmed'
                    and entry.get('human_status') != 'confirmed'):
                continue
            if (entry.get('human_status') != 'confirmed' and previous in entries
                    and entry['translation'] == defaults.get(key.casefold())
                    and entries[previous]['translation'] != entry['translation']):
                continue
            if previous != key:
                entries.pop(previous)
            entries[key] = entry
    return entries


def save_dictionary(entries: dict[str, object]) -> None:
    _write_entries(_dictionary_path(), _normalize_entries(entries))


def load_pending_terms() -> dict[str, dict[str, str]]:
    path = _pending_path()

    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('待确认术语格式无效，请从备份恢复')
        return _normalize_entries(data, allow_empty=True)

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
        "human_status": "confirmed",
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

    if not dictionary:
        return original, False
    folded = {k.casefold(): v['translation'] for k, v in dictionary.items()}
    pattern = '|'.join(re.escape(k) for k in sorted(dictionary, key=len, reverse=True))
    # One pass, at Latin word boundaries, preserves model numbers and replacement text.
    translated, count = re.subn(r'(?<![A-Za-z0-9])(?:' + pattern + r')(?![A-Za-z0-9])',
                               lambda m: folded[m.group().casefold()], original, flags=re.I)
    return translated, bool(count)


def exact_dictionary_translation(
    term: str,
    entries: dict[str, dict[str, str]] | None = None,
) -> str:
    """Return only a complete glossary match, never a substring replacement."""

    original = str(term or "").strip()

    if not original:
        return ""

    dictionary = entries if entries is not None else load_dictionary()

    for source, entry in dictionary.items():
        if source.casefold() == original.casefold():
            return str(entry.get("translation", "")).strip()

    return ""


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
            normalized[term] = dict(raw_entry) if isinstance(raw_entry, dict) else {}
            normalized[term].update(translation=translation, type=term_type)

    return normalized


def _write_entries(path: Path, entries: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.name == 'dictionary.json':
        shutil.copy2(path, path.with_name(path.name + '.backup-' + uuid4().hex[:8]))
    temporary = path.with_name('.terms-' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
