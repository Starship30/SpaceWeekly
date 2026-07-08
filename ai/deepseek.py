import json

from ai.prompts import build_analysis_prompt
from ai.prompts import build_summary_prompt
from ai.prompts import build_translation_prompt
from ai.providers import ProviderConfig
from ai import providers
from ai.providers import complete
from config import DEEPSEEK_API_KEY
from config import DEEPSEEK_BASE_URL
from config import DEEPSEEK_MODEL
from config import REQUEST_TIMEOUT
from models.ai_summary import AISummary
from models.article import Article
from models.news_analysis import NewsAnalysis

DEEPSEEK_TEMPERATURE = 0.2
DEEPSEEK_MAX_TOKENS = 2000


def summarize(article: Article) -> AISummary:
    """Summarize an article with the configured AI provider."""
    _validate_config()
    content = complete(build_summary_prompt(article), _provider_config("DeepSeek"))

    return _parse_summary(content)


def translate(article: Article) -> str:
    """Translate an article body into Simplified Chinese."""
    _validate_config()

    return complete(build_translation_prompt(article), _provider_config("DeepSeek"))


def analyze(
    article: Article,
    provider: str = "DeepSeek",
    summary_prompt: str = "",
    translation_prompt: str = "",
    category_prompt: str = "",
    score_prompt: str = "",
    filter_prompt: str = "",
) -> NewsAnalysis:
    """Analyze an article with the configured OpenAI-compatible provider."""
    _validate_config()
    prompt = build_analysis_prompt(
        article,
        summary_prompt=summary_prompt,
        translation_prompt=translation_prompt,
        category_prompt=category_prompt,
        score_prompt=score_prompt,
        filter_prompt=filter_prompt,
    )
    content = complete(prompt, _provider_config(provider))

    return _parse_analysis(content)


def _provider_config(provider: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=DEEPSEEK_TEMPERATURE,
        max_tokens=DEEPSEEK_MAX_TOKENS,
        timeout=REQUEST_TIMEOUT,
        connect_timeout=providers.CONNECT_TIMEOUT,
        read_timeout=providers.READ_TIMEOUT,
        retry_count=providers.RETRY_COUNT,
    )


def _validate_config() -> None:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured.")

    if not DEEPSEEK_BASE_URL:
        raise ValueError("DEEPSEEK_BASE_URL is not configured.")

    if not DEEPSEEK_MODEL:
        raise ValueError("DEEPSEEK_MODEL is not configured.")


def _parse_summary(content: str) -> AISummary:
    data = json.loads(_strip_json_block(content))
    keywords = data.get("keywords", [])

    if not isinstance(keywords, list):
        keywords = []

    return AISummary(
        summary=str(data.get("summary", "")),
        keywords=[str(keyword) for keyword in keywords],
        category=str(data.get("category", "")),
        importance=str(data.get("importance", "")),
    )


def _parse_analysis(content: str) -> NewsAnalysis:
    data = json.loads(_strip_json_block(content))
    keywords = data.get("keywords", [])
    categories = data.get("categories", [])
    importance = _safe_importance(data.get("importance"))

    if not isinstance(keywords, list):
        keywords = []

    if not isinstance(categories, list):
        categories = []

    return NewsAnalysis(
        summary=str(data.get("summary", "")),
        translation=str(data.get("translation", "")),
        keywords=[str(keyword) for keyword in keywords],
        categories=[str(category) for category in categories],
        importance=importance,
        score=_safe_score(data.get("score"), importance),
        keep=bool(data.get("keep", True)),
        reason=str(data.get("reason", "")),
    )


def _safe_importance(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "high": "High",
        "高": "High",
        "medium": "Medium",
        "中": "Medium",
        "low": "Low",
        "低": "Low",
    }

    return aliases.get(text, "Medium")


def _safe_score(value: object, importance: str = "Medium") -> int:
    if value is None:
        return {"High": 90, "Medium": 70, "Low": 30}.get(importance, 70)

    try:
        score = int(value or 0)
    except (TypeError, ValueError):
        return {"High": 90, "Medium": 70, "Low": 30}.get(importance, 70)

    return max(0, min(100, score))


def _strip_json_block(content: str) -> str:
    text = content.strip()

    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()

    if text.startswith("```"):
        text = text.removeprefix("```").strip()

    if text.endswith("```"):
        text = text.removesuffix("```").strip()

    return text
