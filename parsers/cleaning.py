NOISE_PREFIXES = (
    "author:",
    "by ",
    "credit:",
    "credits:",
    "image credit:",
    "newsletter",
)

NOISE_PHRASES = (
    "nasa science editorial team",
    "molly wasser",
    "get the latest jpl news",
    "news from infinity and beyond",
    "more information about",
    "sign up",
    "subscribe",
)


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

    if lower_text.startswith(NOISE_PREFIXES):
        return True

    return any(phrase in lower_text for phrase in NOISE_PHRASES)
