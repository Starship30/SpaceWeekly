import json

from resources import resource_path

DEFAULT_LANGUAGE = "zh_CN"
_LANGUAGE = DEFAULT_LANGUAGE
_MESSAGES: dict[str, str] | None = None


def set_language(language: str) -> None:
    """Set active GUI language."""
    global _LANGUAGE, _MESSAGES

    _LANGUAGE = language if language in {"zh_CN", "en_US"} else DEFAULT_LANGUAGE
    _MESSAGES = None


def current_language() -> str:
    """Return active GUI language."""
    return _LANGUAGE


def tr(key: str, **kwargs: object) -> str:
    """Translate a GUI text key."""
    messages = _load_messages()
    template = messages.get(key, key)

    return template.format(**kwargs)


def _load_messages() -> dict[str, str]:
    global _MESSAGES

    if _MESSAGES is None:
        path = resource_path("language", f"{_LANGUAGE}.json")
        _MESSAGES = json.loads(path.read_text(encoding="utf-8"))

    return _MESSAGES
