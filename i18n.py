import json

from resources import resource_path

DEFAULT_LANGUAGE = "zh_CN"
_MESSAGES: dict[str, str] | None = None


def tr(key: str, **kwargs: object) -> str:
    """Translate a GUI text key."""
    messages = _load_messages()
    template = messages.get(key, key)

    return template.format(**kwargs)


def _load_messages() -> dict[str, str]:
    global _MESSAGES

    if _MESSAGES is None:
        path = resource_path("language", f"{DEFAULT_LANGUAGE}.json")
        _MESSAGES = json.loads(path.read_text(encoding="utf-8"))

    return _MESSAGES
