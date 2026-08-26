import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

_TEST_APPDATA = Path(tempfile.gettempdir()) / "spaceweekly-translator-tests"
_TEST_APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(_TEST_APPDATA)

from ai.json_utils import AIEmptyResponseError
from ai.json_utils import AIResponseParseError
from ai.json_utils import parse_json_object
from ai.json_utils import request_json
from ai.providers import APIRequestError
from services.settings import default_settings
from translator.cache import cache_translation
from translator.cache import get_cached_translation
from translator.errors import TranslationErrorCode
from translator.search import SearchProvider
from translator.search import SearchProviderError
from translator.search import SearchResult
from translator.translator import Translator
from translator.translator import _validate_translation_payload


class StubSearchProvider(SearchProvider):
    name = "stub"

    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.queries = []

    def search(self, query, max_results=5):
        self.queries.append((query, max_results))

        if self.error is not None:
            raise SearchProviderError(self.error)

        return self.results[:max_results]


def official_result():
    return SearchResult(
        title="GRAVITY-1 launch vehicle",
        url="https://www.nasa.gov/example/gravity-1",
        snippet="GRAVITY-1 is a commercial launch vehicle.",
        official=True,
    )


class JsonResponseTests(unittest.TestCase):
    def test_normal_ai_json(self):
        self.assertEqual(
            parse_json_object('{"translation":"整流罩"}'),
            {"translation": "整流罩"},
        )

    def test_empty_ai_response_has_distinct_error(self):
        with self.assertRaises(AIEmptyResponseError):
            parse_json_object("   ")

    def test_invalid_json_has_distinct_error(self):
        with self.assertRaises(AIResponseParseError) as caught:
            parse_json_object("not-json")

        self.assertNotIsInstance(caught.exception, AIEmptyResponseError)

    def test_markdown_wrapped_json(self):
        self.assertEqual(
            parse_json_object('```json\n{"translation":"近地轨道"}\n```'),
            {"translation": "近地轨道"},
        )

    def test_empty_response_retries_then_succeeds(self):
        provider = Mock(model="test-model")

        with patch("ai.providers.complete", side_effect=["", '{"translation":"星舰"}']):
            data = request_json(
                "prompt",
                provider,
                module_name="translator_test",
                retry_base_delay=0,
            )

        self.assertEqual(data["translation"], "星舰")

    def test_api_error_remains_api_error(self):
        provider = Mock(model="test-model")
        error = APIRequestError("test", "temporary failure", status_code=503)

        with patch("ai.providers.complete", side_effect=error):
            with self.assertRaises(APIRequestError):
                request_json("prompt", provider, module_name="translator_test")


class TranslationValidationTests(unittest.TestCase):
    def test_strict_translation_schema(self):
        with self.assertRaises(AIResponseParseError):
            _validate_translation_payload(
                {"translation": "引力-1", "reason": "rocket"},
                original="Gravity-1",
                long_text=False,
            )

    def test_model_number_must_be_preserved(self):
        with self.assertRaises(AIResponseParseError):
            _validate_translation_payload(
                {"translation": "引力一号"},
                original="Gravity-1",
                long_text=False,
            )

        _validate_translation_payload(
            {"translation": "引力-1"},
            original="Gravity-1",
            long_text=False,
        )

    def test_mission_code_must_not_be_translated(self):
        with self.assertRaises(AIResponseParseError):
            _validate_translation_payload(
                {"translation": "国家侦察任务-145"},
                original="NROL-145",
                long_text=False,
            )

    def test_description_model_name_must_be_preserved(self):
        with self.assertRaises(AIResponseParseError):
            _validate_translation_payload(
                {"translation": "ZQ-3完成试射"},
                original="Zhuque-3 completed a test flight",
                long_text=True,
            )

        _validate_translation_payload(
            {"translation": "Zhuque-3完成试射"},
            original="Zhuque-3 completed a test flight",
            long_text=True,
        )
        _validate_translation_payload(
            {"translation": "朱雀-3号完成试射"},
            original="Zhuque-3 completed a test flight",
            long_text=True,
        )


class TranslatorPipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = default_settings()
        self.settings.web_search_enabled = True
        self.settings.web_search_max_results = 5
        self.settings.retry_count = 0
        self.patches = [
            patch("translator.translator.exact_dictionary_translation", return_value=""),
            patch("translator.translator.dictionary_replace", return_value=("", False)),
            patch("translator.translator.pending_translation", return_value=""),
            patch("translator.translator.add_pending_term", return_value=False),
        ]

        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_web_search_success_and_gravity_1_cache_write(self):
        search = StubSearchProvider([official_result()])

        with patch("translator.translator.get_cached_translation", return_value=None), patch(
            "translator.translator.cache_translation"
        ) as write_cache, self.assertLogs("translator.translator", level="INFO") as logs:
            translator = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=lambda *_args: "引力-1",
                search_provider=search,
            )
            result = translator.translate_term_result("Gravity-1", "rocket")

        self.assertTrue(result.success)
        self.assertEqual(result.translation, "引力-1")
        self.assertEqual(result.source, "web+llm")
        self.assertTrue(
            any(
                '"Gravity-1" rocket spacecraft launch aerospace' in query
                for query, _limit in search.queries
            )
        )
        self.assertEqual(write_cache.call_args.kwargs["source_url"], official_result().url)
        combined = "\n".join(logs.output)
        self.assertIn("cache=MISS", combined)
        self.assertIn("web_search=START", combined)
        self.assertIn("validation=PASS", combined)
        self.assertIn("cache=WRITE", combined)

    def test_web_search_failure_falls_back_to_ai(self):
        search = StubSearchProvider(error="offline")

        with patch("translator.translator.get_cached_translation", return_value=None), patch(
            "translator.translator.cache_translation"
        ) as write_cache:
            translator = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=lambda *_args: "整流罩",
                search_provider=search,
            )
            result = translator.translate_term_result("payload fairing", "term")

        self.assertTrue(result.success)
        self.assertEqual(result.translation, "整流罩")
        self.assertEqual(result.source, "llm_fallback")
        self.assertEqual(write_cache.call_args.kwargs["confidence"], 0.55)

    def test_persistent_cache_hit_skips_search_and_ai(self):
        ai = Mock(return_value="wrong")
        search = StubSearchProvider([official_result()])
        cached = {
            "translation": "引力-1",
            "source": "web+llm",
            "source_url": official_result().url,
        }

        with patch("translator.translator.get_cached_translation", return_value=cached):
            result = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=ai,
                search_provider=search,
            ).translate_term_result("Gravity-1", "rocket")

        self.assertTrue(result.cached)
        self.assertEqual(result.translation, "引力-1")
        ai.assert_not_called()
        self.assertEqual(search.queries, [])

    def test_cache_miss_uses_ai_when_web_search_disabled(self):
        self.settings.web_search_enabled = False

        with patch("translator.translator.get_cached_translation", return_value=None), patch(
            "translator.translator.cache_translation"
        ):
            result = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=lambda *_args: "近地轨道",
            ).translate_term_result("low Earth orbit", "orbit")

        self.assertTrue(result.success)
        self.assertEqual(result.translation, "近地轨道")
        self.assertEqual(result.source, "llm")

    def test_partial_glossary_match_continues_to_ai(self):
        self.settings.web_search_enabled = False
        ai = Mock(return_value="猎鹰9号Block 5｜Starlink Group 15-20")

        with patch(
            "translator.translator.dictionary_replace",
            return_value=("猎鹰9号 Block 5 | Starlink Group 15-20", True),
        ), patch(
            "translator.translator.get_cached_translation", return_value=None
        ), patch("translator.translator.cache_translation"):
            result = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=ai,
            ).translate_term_result(
                "Falcon 9 Block 5 | Starlink Group 15-20",
                "mission",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.source, "llm")
        ai.assert_called_once()

    def test_ai_disabled_miss_is_quiet_fallback_not_logged_error(self):
        with patch(
            "translator.translator.get_cached_translation", return_value=None
        ), self.assertLogs("translator.translator", level="INFO") as logs:
            result = Translator(
                settings=self.settings,
                mode="hybrid",
            ).translate_term_result("Electron", "rocket", allow_ai=False)

        self.assertFalse(result.success)
        self.assertIsNone(result.translation)
        combined = "\n".join(logs.output)
        self.assertIn("fallback=ORIGINAL reason=AI_DISABLED", combined)
        self.assertNotIn("translation=FAIL", combined)

    def test_description_logs_and_returns_postprocessed_text(self):
        with self.assertLogs("translator.translator", level="INFO") as logs:
            translated = Translator(
                settings=self.settings,
                mode="hybrid",
                ai_callable=lambda *_args: "一批Starlink巨型星座卫星用于Starlink项目。",
            ).translate_description("Starlink mission description")

        self.assertEqual(
            translated,
            "一批Starlink卫星星座卫星用于Starlink卫星互联网计划。",
        )
        self.assertIn("translation=一批Starlink卫星星座", "\n".join(logs.output))

    def test_empty_ai_response_returns_structured_failure(self):
        with patch("translator.translator.get_cached_translation", return_value=None):
            translator = Translator(
                settings=self.settings,
                mode="ai",
                ai_callable=lambda *_args: "",
                web_search=False,
            )
            result = translator.translate_term_result("new term")

        self.assertFalse(result.success)
        self.assertIsNone(result.translation)
        self.assertEqual(result.error_code, TranslationErrorCode.EMPTY_RESPONSE)
        self.assertEqual(translator.translate_term("new term"), "new term")

    def test_invalid_json_and_api_errors_are_classified(self):
        cases = [
            (AIResponseParseError("bad JSON"), TranslationErrorCode.INVALID_JSON),
            (
                APIRequestError("test", "unavailable", status_code=503),
                TranslationErrorCode.API_ERROR,
            ),
        ]

        for error, code in cases:
            with self.subTest(code=code), patch(
                "translator.translator.get_cached_translation", return_value=None
            ), patch("translator.translator._provider_translate", side_effect=error):
                result = Translator(
                    settings=self.settings,
                    mode="ai",
                    web_search=False,
                ).translate_term_result("unseen term")

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, code)


class TranslationCacheTests(unittest.TestCase):
    def test_cache_persists_required_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "translation_cache.json"

            with patch("translator.cache._cache_path", return_value=path):
                cache_translation(
                    "Gravity-1",
                    "引力-1",
                    term_type="rocket",
                    source="web+llm",
                    source_url="https://www.nasa.gov/example",
                    confidence=0.9,
                )
                cached = get_cached_translation("gravity-1")

            self.assertEqual(cached["original"], "Gravity-1")
            self.assertEqual(cached["translation"], "引力-1")
            self.assertEqual(cached["source"], "web+llm")
            self.assertEqual(cached["source_url"], "https://www.nasa.gov/example")
            self.assertEqual(cached["confidence"], 0.9)
            self.assertTrue(cached["timestamp"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["Gravity-1"]["type"], "rocket")


class WordTranslationCallTests(unittest.TestCase):
    def test_word_launch_terms_allow_configured_ai_pipeline(self):
        from exporters.word import _translated_term

        translator = Mock()
        translator.translate_term.return_value = "电子号"

        self.assertEqual(_translated_term("Electron", "rocket", translator), "电子号")
        translator.translate_term.assert_called_once_with(
            "Electron",
            "rocket",
            allow_ai=True,
        )


if __name__ == "__main__":
    unittest.main()
