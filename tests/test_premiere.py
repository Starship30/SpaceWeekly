"""Offline export checks; no network, AI requests, or Premiere host needed."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from exporters import premiere
from models.ai_summary import AISummary
from models.article import Article
from models.news import News
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from services.weekly_report import _export_context, _export_reports


def article(number: int) -> Article:
    return Article(News(
        source="虚构航天社", title=f"Original {number}",
        published=f"Thu, 0{number} Oct 2026 10:00:00 GMT",
        summary=f"原始摘要 {number}", url=f"https://example.com/news/{number}",
    ), "不应导出正文", "test")


class PremiereExportTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "build"
        scratch.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.patch = patch.object(premiere, "OUTPUT_DIR", self.output)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.settings = replace(default_settings(), output_dir=str(self.output))
        self.context = _export_context(self.settings, {})

    def export(self, articles=None, summaries=None, **kwargs):
        return premiere.export_premiere(
            articles if articles is not None else [article(1)],
            summaries or {}, self.context, "zh_CN", **kwargs,
        )

    def test_title_summary_precedence_fallback_and_utf8(self):
        items = [article(3), article(1), article(2)]
        self.context.localized_titles = {items[0].news.url: "翻译标题三", items[1].news.url: " "}
        summaries = {
            items[0].news.url: AISummary("中文 AI 摘要", [], ["科学", "探测"]),
            items[1].news.url: AISummary(" ", [], ["发射"]),
        }
        path = self.export(items, summaries)
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertIn("中文 AI 摘要", raw)
        self.assertNotIn("\\u4e2d", raw)
        self.assertEqual(data["protocol_version"], "1.0")
        self.assertEqual(data["order_policy"], "selected_articles_input_order")
        first, second, third = data["items"]
        self.assertEqual(first["title"], "翻译标题三")
        self.assertEqual(first["summary"], "中文 AI 摘要")
        self.assertIsNone(first["summary_fallback"])
        self.assertEqual(first["categories"], ["科学", "探测"])
        self.assertEqual(second["title"], "Original 1")
        self.assertEqual(second["summary"], "原始摘要 1")
        self.assertEqual(second["summary_fallback"], "empty_ai_summary")
        self.assertEqual(third["summary_fallback"], "missing_ai_summary")
        self.assertEqual(first["published"], items[0].news.published)
        self.assertNotIn("body", first)
        self.assertNotIn("deepseek_api_key", raw)

    def test_order_stable_ids_unique_jobs_and_non_overwrite(self):
        first_path = self.export([article(2), article(1)])
        first = json.loads(first_path.read_text(encoding="utf-8"))
        second_path = self.export([article(1), article(2)])
        second = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertEqual([i["url"] for i in first["items"]], [article(2).news.url, article(1).news.url])
        self.assertEqual(first["items"][0]["id"], second["items"][1]["id"])
        self.assertEqual(first["items"][1]["id"], second["items"][0]["id"])
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(json.loads(first_path.read_text(encoding="utf-8")), first)

    def test_cancel_after_temp_write_removes_all_new_files(self):
        token = CancellationToken()

        def check():
            if any(path.name.startswith(".") for path in self.output.iterdir()):
                token.cancel()
            token.check()

        with self.assertRaises(TaskCancelledError):
            self.export(cancel_check=check)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_before_export_creates_nothing(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelledError):
            self.export(cancel_check=token.check)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_failed_partial_write_removes_reservation_and_temp(self):
        def broken_write(path, content, **kwargs):
            path.write_bytes(b'{"items":')
            raise OSError("disk full")

        with patch.object(Path, "write_text", broken_write):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_replace_failure_preserves_previous_report(self):
        previous = self.export()
        content = previous.read_bytes()
        with patch("utils.output_files.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(list(self.output.iterdir()), [previous])
        self.assertEqual(previous.read_bytes(), content)

    def test_invalid_or_duplicate_input_leaves_no_files(self):
        with self.assertRaises(ValueError):
            self.export([article(1), article(1)])
        bad = article(1)
        bad.news.title = ""
        with self.assertRaises(ValueError):
            self.export([bad])
        with self.assertRaises(ValueError):
            self.export(summaries={article(1).news.url: AISummary("AI", [], "wrong")})
        self.assertEqual(list(self.output.iterdir()), [])

    def test_settings_load_save_and_workspace_output(self):
        from services import settings
        config_path = self.output / "config.json"
        with patch.object(settings, "CONFIG_PATH", config_path):
            config_path.write_text("{}", encoding="utf-8")
            self.assertFalse(settings.load_settings().export_premiere)
            enabled = replace(self.settings, export_premiere=True)
            settings.save_settings(enabled)
            self.assertTrue(settings.load_settings().export_premiere)
            # Restore module paths after testing existing apply_settings machinery.
            with patch.object(premiere, "OUTPUT_DIR"), patch("exporters.word.OUTPUT_DIR"), patch("exporters.markdown.OUTPUT_DIR"), patch("database.sqlite.DATABASE_PATH"):
                settings.apply_settings(enabled)
                self.assertEqual(premiere.OUTPUT_DIR, self.output)

    def test_disabled_option_keeps_existing_dispatch(self):
        with patch("services.weekly_report.export_premiere") as new, patch("services.weekly_report.export_markdown") as md, patch("services.weekly_report.export_word") as word:
            _export_reports([article(1)], {}, {}, self.context, [],
                            self.settings, True, True, CancellationToken())
            new.assert_not_called()
            md.assert_called_once()
            word.assert_called_once()

    def test_enabled_dispatch_reuses_same_in_memory_result(self):
        articles = [article(1)]
        summaries = {articles[0].news.url: AISummary("本次 AI 结果", [], [])}
        with patch("services.weekly_report.export_premiere") as new, patch("services.weekly_report.export_markdown") as md, patch("services.weekly_report.export_word") as word:
            _export_reports(articles, summaries, {}, self.context, [],
                            replace(self.settings, export_premiere=True), False, False,
                            CancellationToken())
            args = new.call_args.args
            self.assertIs(args[0], articles)
            self.assertIs(args[1], summaries)
            self.assertIs(args[2], self.context)
            self.assertEqual(args[3], self.settings.language)
            md.assert_not_called()
            word.assert_not_called()


if __name__ == "__main__":
    unittest.main()
