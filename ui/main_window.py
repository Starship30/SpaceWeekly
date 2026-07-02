import logging
import os
from dataclasses import replace
from datetime import date
from datetime import timedelta

from PySide6.QtCore import QDate
from PySide6.QtCore import QThread
from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QIcon
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QDateEdit
from PySide6.QtWidgets import QGroupBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QMainWindow
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr
from resources import resource_path
from services.feed_manager import FeedSource
from services.feed_manager import load_feeds
from services.feed_manager import save_feeds
from services.settings import load_settings
from services.settings import save_settings
from services.weekly_report import estimate_generation
from services.weekly_report import generate_weekly
from ui.feed_dialog import FeedDialog
from ui.log_handler import QtLogHandler
from ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class ReportWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)

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

    def run(self) -> None:
        try:
            generate_weekly(
                feeds=self.feeds,
                export_sqlite=self.sqlite_enabled,
                export_markdown_enabled=self.markdown_enabled,
                export_word_enabled=self.word_enabled,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit()


class MainWindow(QMainWindow):
    """Main desktop window for SpaceWeekly."""

    def __init__(self) -> None:
        super().__init__()
        self.feeds = load_feeds()
        self.settings = load_settings()
        self.worker: ReportWorker | None = None
        self.log_handler = QtLogHandler()
        self._setup_window()
        self._build_ui()
        self._connect_logging()
        self._refresh_feed_list()
        self._update_estimate()
        self.statusBar().showMessage(tr("ready"))

    def _setup_window(self) -> None:
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 760)
        self.setMinimumSize(1080, 650)
        icon_path = resource_path("assets", "sspo.ico")

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning("图标不存在 %s", icon_path)

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        columns = QHBoxLayout()
        columns.addWidget(self._feeds_box(), 2)
        columns.addWidget(self._options_box(), 2)
        columns.addWidget(self._logs_box(), 4)
        layout.addLayout(columns)
        self.estimate_label = QLabel()
        layout.addWidget(self.estimate_label)
        bottom = QHBoxLayout()
        self.start_button = QPushButton("🚀 " + tr("start"))
        self.start_button.setMinimumHeight(46)
        self.start_button.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.start_button.clicked.connect(self._start_report)
        self.open_output_button = QPushButton("📂 " + tr("open.output"))
        self.open_output_button.clicked.connect(self._open_output_dir)
        bottom.addWidget(self.start_button, 3)
        bottom.addWidget(self.open_output_button, 1)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

    def _feeds_box(self) -> QGroupBox:
        box = QGroupBox(tr("feed.sources"))
        layout = QVBoxLayout(box)
        self.feed_list = QListWidget()
        self.feed_list.setSpacing(2)
        layout.addWidget(self.feed_list)
        button_row = QHBoxLayout()

        for text, handler in [
            ("➕ " + tr("add"), self._add_feed),
            ("✏ " + tr("edit"), self._edit_feed),
            ("🗑 " + tr("delete"), self._delete_feed),
            ("💾 " + tr("save"), self._save_feed_state),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            button_row.addWidget(button)

        layout.addLayout(button_row)
        utility_row = QHBoxLayout()
        settings_button = QPushButton("⚙ " + tr("settings"))
        settings_button.clicked.connect(self._open_settings)
        about_button = QPushButton("ℹ " + tr("about"))
        about_button.clicked.connect(self._show_about)
        utility_row.addWidget(settings_button)
        utility_row.addWidget(about_button)
        layout.addLayout(utility_row)

        return box

    def _options_box(self) -> QGroupBox:
        box = QGroupBox(tr("output.options"))
        layout = QVBoxLayout(box)
        self.sqlite_check = QCheckBox(tr("sqlite"))
        self.markdown_check = QCheckBox(tr("markdown"))
        self.word_check = QCheckBox(tr("word"))
        self.sqlite_check.setChecked(True)
        self.markdown_check.setChecked(self.settings.export_markdown)
        self.word_check.setChecked(self.settings.export_word)
        layout.addWidget(self.sqlite_check)
        layout.addWidget(self.markdown_check)
        layout.addWidget(self.word_check)
        layout.addWidget(self._ai_box())
        layout.addWidget(self._body_mode_box())
        layout.addWidget(self._date_box())
        layout.addStretch()

        return box

    def _ai_box(self) -> QGroupBox:
        box = QGroupBox(tr("ai.options"))
        layout = QVBoxLayout(box)
        self.ai_translation_check = QCheckBox(tr("ai.translation"))
        self.ai_summary_check = QCheckBox(tr("ai.summary"))
        self.ai_keywords_check = QCheckBox(tr("ai.keywords"))
        self.ai_category_check = QCheckBox(tr("ai.category"))
        self.ai_importance_check = QCheckBox(tr("ai.importance"))
        self.ai_translation_check.setChecked(self.settings.ai_translation_enabled)
        self.ai_summary_check.setChecked(self.settings.ai_summary_enabled)
        self.ai_keywords_check.setChecked(self.settings.ai_keywords_enabled)
        self.ai_category_check.setChecked(self.settings.ai_category_enabled)
        self.ai_importance_check.setChecked(self.settings.ai_importance_enabled)

        for checkbox in [
            self.ai_translation_check,
            self.ai_summary_check,
            self.ai_keywords_check,
            self.ai_category_check,
            self.ai_importance_check,
        ]:
            checkbox.stateChanged.connect(self._update_estimate)
            layout.addWidget(checkbox)

        return box

    def _body_mode_box(self) -> QGroupBox:
        box = QGroupBox(tr("body.mode"))
        layout = QVBoxLayout(box)
        self.body_mode_group = QButtonGroup(self)
        self.body_original = QRadioButton(tr("body.original"))
        self.body_bilingual = QRadioButton(tr("body.bilingual"))
        self.body_translated = QRadioButton(tr("body.translated"))
        modes = [
            (self.body_original, "original"),
            (self.body_bilingual, "bilingual"),
            (self.body_translated, "translated"),
        ]

        for button, mode in modes:
            self.body_mode_group.addButton(button)
            button.setProperty("mode", mode)
            button.setChecked(self.settings.body_mode == mode)
            layout.addWidget(button)

        if not self.body_mode_group.checkedButton():
            self.body_original.setChecked(True)

        return box

    def _date_box(self) -> QGroupBox:
        box = QGroupBox(tr("date.filter"))
        layout = QVBoxLayout(box)
        self.date_mode_combo = QComboBox()
        for label, mode in [
            (tr("today"), "today"),
            (tr("last3"), "last_3_days"),
            (tr("last7"), "last_7_days"),
            (tr("last30"), "last_30_days"),
            (tr("custom"), "custom"),
        ]:
            self.date_mode_combo.addItem(label, mode)
        self.date_mode_combo.setCurrentIndex(
            max(self.date_mode_combo.findData(self.settings.date_mode), 0)
        )
        self.date_mode_combo.currentIndexChanged.connect(self._update_estimate)
        self.start_date_edit = QDateEdit()
        self.end_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.end_date_edit.setCalendarPopup(True)
        self._set_default_dates()
        layout.addWidget(self.date_mode_combo)
        layout.addWidget(QLabel(tr("start.date")))
        layout.addWidget(self.start_date_edit)
        layout.addWidget(QLabel(tr("end.date")))
        layout.addWidget(self.end_date_edit)

        return box

    def _logs_box(self) -> QGroupBox:
        box = QGroupBox(tr("logs"))
        layout = QVBoxLayout(box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setDefaultStyleSheet(
            ".info { color: #1f6feb; }"
            ".warning { color: #b7791f; font-weight: 600; }"
            ".error { color: #c53030; font-weight: 700; }"
        )
        layout.addWidget(self.log_view)

        return box

    def _set_default_dates(self) -> None:
        today = QDate.currentDate()
        start = QDate.fromString(self.settings.start_date, "yyyy-MM-dd")
        end = QDate.fromString(self.settings.end_date, "yyyy-MM-dd")
        self.start_date_edit.setDate(start if start.isValid() else today.addDays(-6))
        self.end_date_edit.setDate(end if end.isValid() else today)

    def _connect_logging(self) -> None:
        self.log_handler.emitter.message.connect(self._append_log)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

    def _append_log(self, message: str) -> None:
        self.log_view.append(message)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _refresh_feed_list(self) -> None:
        self.feed_list.clear()

        for feed in self.feeds:
            item = QListWidgetItem(f"{feed.name}  {feed.rss}")
            item.setCheckState(
                Qt.CheckState.Checked
                if feed.enabled
                else Qt.CheckState.Unchecked
            )
            self.feed_list.addItem(item)

    def _save_feed_state(self) -> None:
        for index, feed in enumerate(self.feeds):
            feed.enabled = (
                self.feed_list.item(index).checkState()
                == Qt.CheckState.Checked
            )

        save_feeds(self.feeds)
        self._update_estimate()

    def _add_feed(self) -> None:
        dialog = FeedDialog(parent=self)

        if dialog.exec():
            self.feeds.append(dialog.feed())
            self._refresh_feed_list()
            self._save_feed_state()

    def _edit_feed(self) -> None:
        index = self.feed_list.currentRow()

        if index < 0:
            return

        dialog = FeedDialog(self.feeds[index], self)

        if dialog.exec():
            self.feeds[index] = dialog.feed()
            self._refresh_feed_list()
            self._save_feed_state()

    def _delete_feed(self) -> None:
        index = self.feed_list.currentRow()

        if index < 0:
            return

        del self.feeds[index]
        self._refresh_feed_list()
        self._save_feed_state()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(load_settings(), self)

        if dialog.exec():
            save_settings(dialog.settings())
            self.settings = load_settings()
            self._update_estimate()

    def _show_about(self) -> None:
        QMessageBox.about(self, tr("about"), tr("about.text"))

    def _start_report(self) -> None:
        self._save_feed_state()
        self._save_runtime_settings()

        if not self._confirm_ai_limit():
            return

        feeds = [feed for feed in self.feeds if feed.enabled]
        self.start_button.setEnabled(False)
        self.statusBar().showMessage(tr("running"))
        self.worker = ReportWorker(
            feeds=feeds,
            sqlite_enabled=self.sqlite_check.isChecked(),
            markdown_enabled=self.markdown_check.isChecked(),
            word_enabled=self.word_check.isChecked(),
        )
        self.worker.finished_ok.connect(self._report_finished)
        self.worker.failed.connect(self._report_failed)
        self.worker.start()

    def _confirm_ai_limit(self) -> bool:
        if self.settings.ai_limit_action != "ask":
            return True

        feeds = [feed for feed in self.feeds if feed.enabled]
        article_count, _, _ = estimate_generation(feeds, self.settings)
        api_features = int(self.settings.ai_summary_enabled) + int(
            self.settings.ai_translation_enabled
        )
        estimated_calls = article_count * api_features

        if estimated_calls <= self.settings.max_daily_api_calls:
            return True

        result = QMessageBox.question(
            self,
            "AI 成本控制",
            "预计 API 调用次数超过每日上限，是否继续？",
        )

        return result == QMessageBox.StandardButton.Yes

    def _save_runtime_settings(self) -> None:
        selected_body = self.body_mode_group.checkedButton()
        body_mode = selected_body.property("mode") if selected_body else "original"
        self.settings = replace(
            load_settings(),
            export_markdown=self.markdown_check.isChecked(),
            export_word=self.word_check.isChecked(),
            ai_translation_enabled=self.ai_translation_check.isChecked(),
            ai_summary_enabled=self.ai_summary_check.isChecked(),
            ai_keywords_enabled=self.ai_keywords_check.isChecked(),
            ai_category_enabled=self.ai_category_check.isChecked(),
            ai_importance_enabled=self.ai_importance_check.isChecked(),
            body_mode=str(body_mode),
            date_mode=str(self.date_mode_combo.currentData()),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
        )
        save_settings(self.settings)

    def _report_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.statusBar().showMessage(tr("finished"))
        QMessageBox.information(self, "SpaceWeekly", "周报生成完成")

    def _report_failed(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.statusBar().showMessage(tr("ready"))
        QMessageBox.warning(self, "SpaceWeekly", message)

    def _open_output_dir(self) -> None:
        path = load_settings().output_dir

        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _update_estimate(self) -> None:
        settings = replace(
            load_settings(),
            ai_summary_enabled=getattr(self, "ai_summary_check", None).isChecked()
            if hasattr(self, "ai_summary_check")
            else self.settings.ai_summary_enabled,
            ai_translation_enabled=getattr(self, "ai_translation_check", None).isChecked()
            if hasattr(self, "ai_translation_check")
            else self.settings.ai_translation_enabled,
        )
        articles, tokens, seconds = estimate_generation(self.feeds, settings)

        if hasattr(self, "estimate_label"):
            self.estimate_label.setText(
                tr("estimate", articles=articles, tokens=tokens, seconds=seconds)
            )
