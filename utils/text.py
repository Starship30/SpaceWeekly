import re


def clean_ai_text(value: object) -> str:
    """Normalize common whitespace and quote artifacts in AI-generated prose."""

    text = str(value or "").replace("\u00a0", " ").strip()
    text = re.sub(r"[“”]\s*[“”]", "", text)
    text = re.sub(r'["\']\s*["\']', "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", text)
    text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if len(text) >= 2 and text[0] in {'"', "'", "“", "‘"}:
        pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}

        if text[-1] == pairs[text[0]]:
            text = text[1:-1].strip()

    return text


def clean_keywords(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = [clean_ai_text(value) for value in values]
    return list(dict.fromkeys(value for value in cleaned if value))[:8]
