from __future__ import annotations

import logging
import re

from ai.configuration import provider_config_from_settings
from ai.json_utils import AIResponseParseError
from ai.json_utils import request_json
from ai.prompts import build_content_prompt
from ai.prompts import build_importance_prompt
from ai.prompts import build_title_translation_prompt
from ai.prompts import language_instruction
from models.ai_summary import AISummary
from models.article import Article
from models.news_assessment import NewsAssessment
from services.news_categories import category_labels
from services.news_categories import category_limit
from services.news_categories import normalize_categories
from utils.text import clean_ai_text
from utils.text import clean_keywords

logger = logging.getLogger(__name__)


def assess_importance(
    article: Article,
    settings,
    *,
    custom_prompt: str = "",
    cancel_token=None,
) -> NewsAssessment:
    """Stage one: return only importance and reason."""

    provider = provider_config_from_settings(settings)
    prompt = build_importance_prompt(
        article,
        settings.language,
        custom_prompt,
    )
    data = request_json(
        prompt,
        provider,
        module_name="importance",
        cancel_token=cancel_token,
        validator=_validate_assessment,
    )
    assessment = _assessment_from_data(data)

    if _language_mismatch(assessment.reason, settings.language):
        logger.warning("AI 筛选原因语言不匹配，正在重新请求")
        data = request_json(
            prompt + "\n\n" + language_instruction(settings.language),
            provider,
            module_name="importance_language_retry",
            cancel_token=cancel_token,
            validator=_validate_assessment,
        )
        assessment = _assessment_from_data(data)

        if _language_mismatch(assessment.reason, settings.language):
            raise AIResponseParseError("筛选原因在语言重试后仍与程序设置不一致")

    return assessment


def generate_content(
    article: Article,
    settings,
    *,
    custom_prompt: str = "",
    cancel_token=None,
) -> AISummary:
    """Stage two: return summary, keywords, and controlled categories."""

    provider = provider_config_from_settings(settings)
    prompt = build_content_prompt(
        article,
        settings.language,
        custom_prompt,
    )
    data = request_json(
        prompt,
        provider,
        module_name="content",
        cancel_token=cancel_token,
        validator=lambda value: _validate_content(value, settings.language),
    )
    summary = _summary_from_data(data, settings.language)
    language_sample = " ".join(
        [summary.summary, *summary.keywords, *summary.categories]
    )

    if _language_mismatch(language_sample, settings.language):
        logger.warning("AI 摘要语言不匹配，正在重新请求")
        data = request_json(
            prompt + "\n\n" + language_instruction(settings.language),
            provider,
            module_name="content_language_retry",
            cancel_token=cancel_token,
            validator=lambda value: _validate_content(value, settings.language),
        )
        summary = _summary_from_data(data, settings.language)

        if _language_mismatch(
            " ".join(
                [summary.summary, *summary.keywords, *summary.categories]
            ),
            settings.language,
        ):
            raise AIResponseParseError("摘要在语言重试后仍与程序设置不一致")

    return summary


def translate_titles(
    titles: dict[str, str],
    settings,
    *,
    cancel_token=None,
    custom_prompt: str = "",
    article_contexts: dict | None = None,
) -> dict[str, str]:
    """Translate RSS titles, or generate headlines from supplied article context."""

    if not titles:
        return {}

    provider = provider_config_from_settings(settings)
    prompt = build_title_translation_prompt(titles, settings.language, custom_prompt, article_contexts)

    def validate(data: dict[str, object]) -> None:
        if set(data) != set(titles):
            raise AIResponseParseError("标题翻译返回的 ID 与输入不一致")

        if not all(isinstance(value, str) and value.strip() for value in data.values()):
            raise AIResponseParseError("标题翻译包含空值或非字符串")

        if any(_language_mismatch(str(value), settings.language) for value in data.values()):
            raise AIResponseParseError("标题翻译语言与程序设置不一致")

    data = request_json(
        prompt,
        provider,
        module_name="title_generation" if custom_prompt else "title_translation",
        cancel_token=cancel_token,
        validator=validate,
    )
    return {
        key: clean_ai_text(value)
        for key, value in data.items()
        if key in titles
    }


def _validate_assessment(data: dict[str, object]) -> None:
    if set(data) != {"importance", "reason"}:
        raise AIResponseParseError(
            "筛选 JSON 必须且只能包含 importance、reason"
        )

    if str(data.get("importance", "")).strip().lower() not in {
        "high",
        "medium",
        "low",
        "高",
        "中",
        "低",
    }:
        raise AIResponseParseError("importance 不是 High、Medium 或 Low")

    if not clean_ai_text(data.get("reason")):
        raise AIResponseParseError("reason 为空")


def _validate_content(data: dict[str, object], language: str) -> None:
    if set(data) != {"summary", "keywords", "category"}:
        raise AIResponseParseError(
            "内容 JSON 必须且只能包含 summary、keywords、category"
        )

    if not clean_ai_text(data.get("summary")):
        raise AIResponseParseError("summary 为空")

    if not isinstance(data.get("keywords"), list):
        raise AIResponseParseError("keywords 不是数组")

    keyword_count = len(clean_keywords(data.get("keywords")))

    if keyword_count < 3 or keyword_count > 8:
        raise AIResponseParseError("keywords 必须包含 3 到 8 个有效关键词")

    raw_categories = data.get("category")

    if not isinstance(raw_categories, list):
        raise AIResponseParseError("category 不是数组")

    if len(raw_categories) > category_limit():
        raise AIResponseParseError(
            f"category 最多只能包含 {category_limit()} 项"
        )

    if not all(isinstance(item, str) and item.strip() for item in raw_categories):
        raise AIResponseParseError("category 包含空值或非字符串")

    allowed = set(category_labels(language))
    selected = [str(item).strip() for item in raw_categories]

    if any(item not in allowed for item in selected):
        raise AIResponseParseError("category 包含统一分类列表以外的名称")

    if len(set(selected)) != len(selected):
        raise AIResponseParseError("category 包含重复分类")


def _assessment_from_data(data: dict[str, object]) -> NewsAssessment:
    aliases = {
        "high": "High",
        "高": "High",
        "medium": "Medium",
        "中": "Medium",
        "low": "Low",
        "低": "Low",
    }
    importance = aliases[str(data["importance"]).strip().lower()]
    return NewsAssessment(
        importance=importance,
        reason=clean_ai_text(data["reason"]),
    )


def _summary_from_data(data: dict[str, object], language: str) -> AISummary:
    return AISummary(
        summary=clean_ai_text(data["summary"]),
        keywords=clean_keywords(data["keywords"]),
        categories=normalize_categories(data.get("category", []), language),
    )


def _language_mismatch(text: str, language: str) -> bool:
    letters = re.findall(r"[A-Za-z\u4e00-\u9fff]", str(text or ""))

    if len(letters) < 20:
        return False

    chinese = len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
    ratio = chinese / len(letters)
    return ratio < 0.18 if language == "zh_CN" else ratio > 0.35
