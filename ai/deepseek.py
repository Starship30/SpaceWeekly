import json
from urllib import error
from urllib import request

from ai.prompts import build_summary_prompt
from ai.prompts import build_analysis_prompt
from ai.prompts import build_translation_prompt
from ai.providers import ProviderConfig
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
    """Summarize an article with DeepSeek OpenAI-compatible API."""
    _validate_config()
    payload = _build_payload(article)
    response_data = _post_chat_completion(payload)
    content = _extract_content(response_data)

    return _parse_summary(content)


def translate(article: Article) -> str:
    """Translate an article body into Simplified Chinese."""
    _validate_config()
    payload = _build_translation_payload(article)
    response_data = _post_chat_completion(payload)

    return _extract_content(response_data).strip()


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
    content = complete(
        prompt,
        ProviderConfig(
            provider=provider,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            temperature=DEEPSEEK_TEMPERATURE,
            max_tokens=DEEPSEEK_MAX_TOKENS,
            timeout=REQUEST_TIMEOUT,
        ),
    )

    return _parse_analysis(content)


def _validate_config() -> None:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured.")

    if not DEEPSEEK_BASE_URL:
        raise ValueError("DEEPSEEK_BASE_URL is not configured.")

    if not DEEPSEEK_MODEL:
        raise ValueError("DEEPSEEK_MODEL is not configured.")


def _build_payload(article: Article) -> dict[str, object]:
    return {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是专业航天新闻编辑，输出严格 JSON。",
            },
            {
                "role": "user",
                "content": build_summary_prompt(article),
            },
        ],
        "temperature": DEEPSEEK_TEMPERATURE,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
    }


def _build_translation_payload(article: Article) -> dict[str, object]:
    return {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是专业航天新闻翻译，输出简体中文。",
            },
            {
                "role": "user",
                "content": build_translation_prompt(article),
            },
        ],
        "temperature": DEEPSEEK_TEMPERATURE,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
    }


def _post_chat_completion(payload: dict[str, object]) -> dict[str, object]:
    url = DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    api_request = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=REQUEST_TIMEOUT) as response:
            response_text = response.read().decode("utf-8")
    except error.URLError as exc:
        raise RuntimeError("DeepSeek request failed.") from exc

    return json.loads(response_text)


def _extract_content(response_data: dict[str, object]) -> str:
    choices = response_data.get("choices")

    if not isinstance(choices, list) or not choices:
        raise ValueError("DeepSeek response has no choices.")

    message = choices[0].get("message")

    if not isinstance(message, dict):
        raise ValueError("DeepSeek response has no message.")

    content = message.get("content")

    if not isinstance(content, str):
        raise ValueError("DeepSeek response content is invalid.")

    return content


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


def _strip_json_block(content: str) -> str:
    text = content.strip()

    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()

    if text.startswith("```"):
        text = text.removeprefix("```").strip()

    if text.endswith("```"):
        text = text.removesuffix("```").strip()

    return text


def _parse_analysis(content: str) -> NewsAnalysis:
    data = json.loads(_strip_json_block(content))
    keywords = data.get("keywords", [])
    categories = data.get("categories", [])

    if not isinstance(keywords, list):
        keywords = []

    if not isinstance(categories, list):
        categories = []

    return NewsAnalysis(
        summary=str(data.get("summary", "")),
        translation=str(data.get("translation", "")),
        keywords=[str(keyword) for keyword in keywords],
        categories=[str(category) for category in categories],
        importance=str(data.get("importance", "")),
        score=int(data.get("score", 0) or 0),
        keep=bool(data.get("keep", True)),
        reason=str(data.get("reason", "")),
    )
