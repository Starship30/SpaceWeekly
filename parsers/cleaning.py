import re

KNOWN_AUTHOR_NAMES = {
    "karen fox",
    "molly wasser",
    "nasa science editorial team",
}

NOISE_PREFIXES = (
    "author:",
    "credit:",
    "credits:",
    "follow us",
    "get the jpl newsletter",
    "image credit",
    "photo credit",
    "newsletter",
    "share",
    "social links",
    "subscribe",
    "video credit",
)

NOISE_PHRASES = (
    "get the latest jpl news",
    "news from infinity and beyond",
)

AUTHOR_NAME_PATTERN = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}$")


def clean_body(text: str) -> str:
    """Remove common non-article text from parsed article body."""
    lines = []

    for line in text.splitlines():
        cleaned = line.strip()

        if _is_noise(cleaned):
            continue

        lines.append(cleaned)

    return "\n".join(lines)


def _is_noise(text: str) -> bool:
    if not text:
        return True

    lower_text = text.lower()

    if lower_text in KNOWN_AUTHOR_NAMES:
        return True

    if lower_text.startswith(NOISE_PREFIXES):
        return True

    if lower_text in NOISE_PHRASES:
        return True

    return _looks_like_author_name(text)


def _looks_like_author_name(text: str) -> bool:
    if len(text) > 40:
        return False

    if any(character in text for character in ".,:;!?()[]"):
        return False

    return AUTHOR_NAME_PATTERN.match(text) is not None
