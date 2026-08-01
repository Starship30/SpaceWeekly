import logging
import re
from typing import Callable

import config
from ai.configuration import provider_config_from_settings
from ai.json_utils import request_json
from translator.dictionary import add_pending_term
from translator.dictionary import dictionary_replace
from translator.dictionary import pending_translation
from services.cancellation import TaskCancelledError

logger = logging.getLogger(__name__)

VALID_MODES = {"dictionary", "ai", "hybrid"}


class Translator:
    """Translate aerospace terms with a persistent dictionary and AI fallback."""

    def __init__(
        self,
        settings=None,
        mode: str | None = None,
        ai_callable: Callable[[str, str, bool], str] | None = None,
        cancel_token=None,
    ) -> None:
        selected_mode = mode or config.TRANSLATION_MODE
        self.mode = selected_mode if selected_mode in VALID_MODES else "hybrid"
        self.settings = settings
        self.ai_callable = ai_callable
        self.cancel_token = cancel_token
        self._cache: dict[tuple[str, str, bool, bool], str] = {}

    def translate_term(
        self,
        text: str,
        term_type: str = "term",
        allow_ai: bool = True,
    ) -> str:
        original = str(text or "").strip()
        self._check_cancel()

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
        self._check_cancel()

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
                result = _provider_translate(
                    text,
                    term_type,
                    long_text,
                    self.settings,
                    self.cancel_token,
                )
        except TaskCancelledError:
            raise
        except Exception as exc:
            logger.warning("[Translator] AI translation failed for %s: %s", text, exc)
            return text

        return str(result or "").strip() or text

    def _check_cancel(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.check()


def _provider_translate(
    text: str,
    term_type: str,
    long_text: bool,
    settings,
    cancel_token=None,
) -> str:
    if settings is None:
        raise ValueError("Translator AI settings are unavailable.")

    provider_config = provider_config_from_settings(settings)
    if settings.language == "en_US":
        prompt = (
            f"Rewrite the following aerospace {term_type} in concise, natural English. "
            "Preserve brands, numbers, model designations, and facts. "
            'Return strict JSON only: {"translation": "..."}. Text: '
            + text
        )
    elif long_text:
        prompt = (
            "你是专业航天新闻编辑。请将以下任务描述改写为简洁、自然的中文航天新闻表述，"
            "保留卫星数量、任务目的、机构、火箭和轨道等事实，不逐字翻译，不添加原文没有的事实。"
            "品牌名 SpaceX、Starlink 和术语 Starlink Group 保持英文原样，"
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
    data = request_json(
        prompt,
        provider_config,
        module_name="translator",
        cancel_token=cancel_token,
    )

    return str(data.get("translation", "")).strip()


def _postprocess_description(text: str) -> str:
    result = str(text or "").strip()
    replacements = {
        "太空探索技术公司": "SpaceX",
        "星链组": "Starlink Group",
        "Starlink组": "Starlink Group",
        "星链巨型星座": "Starlink卫星星座",
        "Starlink巨型星座": "Starlink卫星星座",
        "星链项目": "Starlink卫星互联网计划",
        "Starlink项目": "Starlink卫星互联网计划",
        "Starlink卫星星座项目": "Starlink卫星星座",
        "Starlink批次项目": "Starlink Group",
    }

    for source, replacement in replacements.items():
        result = result.replace(source, replacement)

    result = re.sub(r"([A-Za-z][A-Za-z0-9-]*)组", r"\1批次", result)
    result = re.sub(r"([A-Za-z][A-Za-z0-9-]*)巨型星座", r"\1卫星星座", result)

    return result
