from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from resources import resource_path

_CACHE_LOCK = RLock()


def load_translation_cache() -> dict[str, dict[str, object]]:
    path = _cache_path()

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        str(original): entry
        for original, entry in data.items()
        if isinstance(entry, dict) and str(entry.get("translation") or "").strip()
    }


def get_cached_translation(original: str) -> dict[str, object] | None:
    folded = str(original or "").strip().casefold()

    with _CACHE_LOCK:
        for source, entry in load_translation_cache().items():
            if source.casefold() == folded:
                return dict(entry)

    return None


def cache_translation(
    original: str,
    translation: str,
    *,
    term_type: str,
    source: str,
    source_url: str,
    confidence: float,
) -> None:
    with _CACHE_LOCK:
        entries = load_translation_cache()
        entries[str(original).strip()] = {
            "original": str(original).strip(),
            "translation": str(translation).strip(),
            "type": str(term_type or "term").strip() or "term",
            "source": str(source or "").strip(),
            "source_url": str(source_url or "").strip(),
            "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_cache(entries)


def _cache_path() -> Path:
    return resource_path("translator", "translation_cache.json")


def _write_cache(entries: dict[str, dict[str, object]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
