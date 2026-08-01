import logging

from PySide6.QtCore import QThread
from PySide6.QtCore import Signal

from services.feed_manager import FeedSource
from services.cancellation import CancellationToken
from services.cancellation import TaskCancelledError
from services.weekly_report import generate_weekly

logger = logging.getLogger(__name__)


class ReportWorker(QThread):
    """Run report generation without blocking the GUI."""

    finished_ok = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        feeds: list[FeedSource],
        sqlite_enabled: bool,
        markdown_enabled: bool,
        word_enabled: bool,
    ) -> None:
        super().__init__()
        self.feeds = feeds
        self.sqlite_enabled = sqlite_enabled
        self.markdown_enabled = markdown_enabled
        self.word_enabled = word_enabled
        self.cancel_token = CancellationToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            generate_weekly(
                feeds=self.feeds,
                export_sqlite=self.sqlite_enabled,
                export_markdown_enabled=self.markdown_enabled,
                export_word_enabled=self.word_enabled,
                cancel_token=self.cancel_token,
            )
        except TaskCancelledError:
            logger.warning("用户取消任务")
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("周报生成失败")
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit()
