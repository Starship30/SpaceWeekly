import unittest
from unittest.mock import patch

from services.cancellation import TaskCancelledError
from ui.widgets.report_worker import ReportWorker


class ReportWorkerTests(unittest.TestCase):
    def run_worker(self, error=None):
        worker = ReportWorker([], False, True, False)
        events = []
        worker.finished_ok.connect(lambda: events.append("success"))
        worker.failed.connect(lambda message: events.append("failed"))
        worker.cancelled.connect(lambda: events.append("cancelled"))
        with patch("services.weekly_report.generate_weekly", side_effect=error) as generate:
            worker.run()
        return worker, events, generate

    def test_success_preserves_export_options(self):
        worker, events, generate = self.run_worker()
        self.assertEqual(events, ["success"])
        generate.assert_called_once_with(feeds=[], export_sqlite=False,
            export_markdown_enabled=True, export_word_enabled=False,
            cancel_token=worker.cancel_token)

    def test_failure_does_not_emit_success(self):
        _, events, _ = self.run_worker(RuntimeError("offline"))
        self.assertEqual(events, ["failed"])

    def test_cancel_remains_distinct_from_failure(self):
        _, events, _ = self.run_worker(TaskCancelledError())
        self.assertEqual(events, ["cancelled"])
