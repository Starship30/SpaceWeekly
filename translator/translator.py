import json
import logging
import re
from typing import Callable

import config
from translator.dictionary import add_pending_term
from translator.dictionary import dictionary_replace
from translator.dictionary import pending_translation

logger = logging.getLogger(__name__)

VALID_MODES = {"dictionary", "ai", "hybrid"}


class Translator:
    """Translate aerospace terms with a persistent dictionary and AI fallback."""

    def __init__(
        self,
        settings=None,
        mode: str | None = None,
        ai_callable: Callable[[str, str, bool], str] | None = None,
    ) -> None:
        selected_mode = mode or config.TRANSLATION_MODE
        self.mode = selected_mode if selected_mode in VALID_MODES else "hybrid"
        self.settings = settings
        self.ai_callable = ai_callable
        self._cache: dict[tuple[str, str, bool, bool], str] = {}

    def translate_term(
        self,
        text: str,
        term_type: str = "term",
        allow_ai: bool = True,
    ) -> str:
        original = str(text or "").strip()

        if not original:
            return ""

        cache_key = (original, term_type, False, allow_ai)

        if cache_key in self._cache:
            return self._cache[cache_key]

        if self.mode != "ai":
            dictionary_result, matched = dictionary_replace(original)

            if matched:
                self._cache[cache_key] = dictionary_result
                return dictionary_result

        if self.mode == "dictionary" or not allow_ai:
            self._record_unknown(original, term_type)
            result = original
        elif self.mode == "hybrid":
            candidate = pending_translation(original)

            if candidate:
                result = candidate
            else:
                self._record_unknown(original, term_type)
                result = self._translate_with_ai(original, term_type, False)
                add_pending_term(original, term_type, result if result != original else "")
        else:
            self._record_unknown(original, term_type)
            result = self._translate_with_ai(original, term_type, False)
            add_pending_term(original, term_type, result if result != original else "")

        self._cache[cache_key] = result
        return result

    def translate_description(self, text: str) -> str:
        original = str(text or "").strip()

        if not original or self.mode == "dictionary":
            return original

        cache_key = (original, "description", True, True)

        if cache_key not in self._cache:
            self._cache[cache_key] = _postprocess_description(
                self._translate_with_ai(
                    original,
                    "description",
                    True,
                )
            )

        return self._cache[cache_key]

    def _record_unknown(self, term: str, term_type: str) -> None:
        if add_pending_term(term, term_type):
            logger.info("[Translator] New term detected: %s", term)

    def _translate_with_ai(
        self,
        text: str,
        term_type: str,
        long_text: bool,
    ) -> str:
        try:
            if self.ai_callable is not None:
                result = self.ai_callable(text, term_type, long_text)
            else:
                result = _provider_translate(text, term_type, long_text, self.settings)
        except Exception as exc:
            logger.warning("[Translator] AI translation failed for %s: %s", text, exc)
            return text

        return str(result or "").strip() or text


def _provider_translate(text: str, term_type: str, long_text: bool, settings) -> str:
    if settings is None:
        raise ValueError("Translator AI settings are unavailable.")

    from ai.providers import ProviderConfig
    from ai.providers import complete

    provider_config = ProviderConfig(
        provider=settings.ai_provider,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=settings.temperature,
        max_tokens=min(settings.max_tokens, 1000 if long_text else 256),
        timeout=settings.request_timeout,
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
        retry_count=settings.retry_count,
    )
    if long_text:
        prompt = (
            "你是专业航天新闻编辑。请将以下任务描述改写为简洁、自然的中文航天新闻表述，"
            "保留卫星数量、任务目的、机构、火箭和轨道等事实，不逐字翻译，不添加原文没有的事实。"
            "品牌名 SpaceX 和 Starlink 保持英文；将 Starlink Group 表述为 Starlink批次，"
            "避免使用‘星链组’‘XX项目’‘XX巨型星座’等数据库式或机器翻译表达。"
            "输出一至两句，并严格返回 JSON：{\"translation\": \"...\"}。原文："
            + text
        )
    else:
        prompt = (
            f"Translate the aerospace {term_type} term into concise Simplified Chinese. "
            "Preserve brands, numbers, and model designations. Return strict JSON only: "
            '{"translation": "..."}. Term: '
            + text
        )
    content = complete(prompt, provider_config)
    data = json.loads(content)

    return str(data.get("translation", "")).strip()


def _postprocess_description(text: str) -> str:
    result = str(text or "").strip()
    replacements = {
        "太空探索技术公司": "SpaceX",
        "星链组": "Starlink批次",
        "Starlink组": "Starlink批次",
        "星链巨型星座": "Starlink卫星星座",
        "Starlink巨型星座": "Starlink卫星星座",
        "星链项目": "Starlink卫星互联网计划",
        "Starlink项目": "Starlink卫星互联网计划",
        "Starlink卫星星座项目": "Starlink卫星星座",
        "Starlink批次项目": "Starlink批次",
    }

    for source, replacement in replacements.items():
        result = result.replace(source, replacement)

    result = re.sub(r"([A-Za-z][A-Za-z0-9-]*)组", r"\1批次", result)
    result = re.sub(r"([A-Za-z][A-Za-z0-9-]*)巨型星座", r"\1卫星星座", result)

    return result
