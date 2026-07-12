import logging
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtCore import Qt
from PySide6.QtCore import qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMainWindow
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QSplitter

from i18n import set_language
from i18n import tr
from resources import resource_path
from services.feed_manager import load_feeds
from services.settings import AppSettings
from services.settings import load_settings
from services.settings import save_settings
from services.weekly_report import estimate_generation
from ui.log_handler import SUCCESS_LEVEL
from ui.log_handler import QtLogHandler
from ui.prompt_studio_dialog import PromptStudioDialog
from ui.settings_dialog import SettingsDialog
from ui.theme import ThemeManager
from ui.widgets.app_toolbar import AppToolBar
from ui.widgets.dashboard_panel import DashboardPanel
from ui.widgets.feed_panel import FeedPanel
from ui.widgets.log_panel import LogPanel
from ui.widgets.report_worker import ReportWorker
from ui.widgets.status_bar import SpaceStatusBar

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Main desktop window for SpaceWeekly."""

    def __init__(self) -> None:
        super().__init__()
        self.feeds = load_feeds()
        self.settings = load_settings()
        set_language(self.settings.language)
        self.worker: ReportWorker | None = None
        self.log_handler = QtLogHandler()
        app = QApplication.instance()

        if app is None:
            raise RuntimeError("ThemeManager requires an active QApplication")

        self.theme_manager = ThemeManager(app)
        self._setup_window()
        self._build_ui()
        self._connect_ui()
        self._connect_logging()
        self._update_estimate()
        self.status_bar.show_state(tr("ready"))

    def _setup_window(self) -> None:
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 760)
        self.setMinimumSize(1080, 650)
        icon_path = resource_path("assets", "sspo.ico")

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning("图标不存在：%s", icon_path)

    def _build_ui(self) -> None:
        self._apply_style()
        self.toolbar = AppToolBar(self)
        self.addToolBar(self.toolbar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.feed_panel = FeedPanel(self.feeds, self)
        self.dashboard_panel = DashboardPanel(self.settings, self)
        self.log_panel = LogPanel(self)
        self.log_panel.apply_theme(self.theme_manager)
        self.theme_manager.theme_changed.connect(
            lambda _theme: self.log_panel.apply_theme(self.theme_manager)
        )
        splitter.addWidget(self.feed_panel)
        splitter.addWidget(self.dashboard_panel)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([320, 430, 530])
        self.setCentralWidget(splitter)
        self.status_bar = SpaceStatusBar(self)
        self.setStatusBar(self.status_bar)

    def _apply_style(self) -> None:
        self.theme_manager.apply(self.settings.theme_mode)

    def _connect_ui(self) -> None:
        self.toolbar.start_requested.connect(self._start_report)
        self.toolbar.settings_requested.connect(self._open_settings)
        self.toolbar.prompt_studio_requested.connect(self._open_prompt_studio)
        self.toolbar.output_requested.connect(self._open_output_dir)
        self.toolbar.refresh_requested.connect(self._refresh_feeds)
        self.toolbar.about_requested.connect(self._show_about)
        self.feed_panel.feeds_changed.connect(self._update_estimate)
        self.dashboard_panel.settings_changed.connect(self._update_estimate)
        self.dashboard_panel.start_requested.connect(self._start_report)

    def _connect_logging(self) -> None:
        self.log_handler.emitter.message.connect(self.log_panel.append_html)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

    def _refresh_feeds(self) -> None:
        self.feeds = load_feeds()
        self.feed_panel.set_feeds(self.feeds)
        self._update_estimate()
        logger.info("RSS 列表已刷新")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(load_settings(), self)

        if dialog.exec():
            previous_language = self.settings.language
            save_settings(dialog.settings())
            self.settings = load_settings()
            set_language(self.settings.language)
            self.setWindowTitle(tr("app.title"))
            if previous_language != self.settings.language:
                self._rebuild_translated_ui()
            else:
                self._apply_style()
                self.log_panel.apply_theme(self.theme_manager)
                self.dashboard_panel.apply_settings(self.settings)
            self._update_estimate()

    def _rebuild_translated_ui(self) -> None:
        self.removeToolBar(self.toolbar)
        self.toolbar.deleteLater()
        central = self.takeCentralWidget()

        if central is not None:
            central.deleteLater()

        old_status_bar = self.statusBar()

        if old_status_bar is not None:
            old_status_bar.deleteLater()

        try:
            self.log_handler.emitter.message.disconnect()
        except TypeError:
            pass

        self._build_ui()
        self._connect_ui()
        self.log_handler.emitter.message.connect(self.log_panel.append_html)

    def _open_prompt_studio(self) -> None:
        dialog = PromptStudioDialog(load_settings(), self)

        if dialog.exec():
            save_settings(dialog.settings())
            self.settings = load_settings()

    def _show_about(self) -> None:
        QMessageBox.about(
           self,
            tr("about"),
            """
            <h3>{title}</h3>
            <p>
            {version}: v2.2<br>
            {github}:
            <a href="https://github.com/Starship30/SpaceWeekly">
            https://github.com/Starship30/SpaceWeekly
            </a>
            <br>
            {python_version}: {python}<br>
            {qt_version}: {qt}<br>
            {author}: RMS-TITANIC
            </p>
            """.format(
                title=tr("app.title"),
                version=tr("version"),
                github=tr("github.link"),
                python_version=tr("python.version"),
                python=sys.version.split()[0],
                qt_version=tr("qt.version"),
                qt=qVersion(),
                author=tr("author"),
            ),
        )

    def _start_report(self) -> None:
        self._save_runtime_settings()

        if not self._confirm_ai_limit():
            return

        self._set_running(True)
        self.worker = ReportWorker(
            feeds=self.feed_panel.enabled_feeds(),
            sqlite_enabled=self.settings.export_sqlite,
            markdown_enabled=self.settings.export_markdown,
            word_enabled=self.settings.export_word,
        )
        self.worker.finished_ok.connect(self._report_finished)
        self.worker.failed.connect(self._report_failed)
        self.worker.start()

    def _save_runtime_settings(self) -> None:
        self.feed_panel.save_state()
        self.feeds = self.feed_panel.feeds
        self.settings = self.dashboard_panel.settings_snapshot(load_settings())
        save_settings(self.settings)
        self._update_estimate()

    def _confirm_ai_limit(self) -> bool:
        if self.settings.ai_limit_action != "ask":
            return True

        article_count, _, _ = estimate_generation(self.feeds, self.settings)
        estimated_calls = article_count if self._ai_enabled(self.settings) else 0

        if estimated_calls <= self.settings.max_daily_api_calls:
            return True

        result = QMessageBox.question(
            self,
            tr("api.cost.control"),
            tr("api.limit.confirm"),
        )

        return result == QMessageBox.StandardButton.Yes

    def _set_running(self, running: bool) -> None:
        self.dashboard_panel.set_running(running)
        self.toolbar.set_running(running)
        self.status_bar.show_state(tr("running") if running else tr("ready"))

    def _report_finished(self) -> None:
        self._set_running(False)
        self.status_bar.show_state(tr("finished"))
        logging.getLogger().log(SUCCESS_LEVEL, tr("report.finished"))
        QMessageBox.information(self, "SpaceWeekly", tr("report.finished"))

    def _report_failed(self, message: str) -> None:
        self._set_running(False)
        self.status_bar.show_state(tr("ready"))
        QMessageBox.warning(self, "SpaceWeekly", message)

    def _open_output_dir(self) -> None:
        path = load_settings().output_dir

        if path:
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _update_estimate(self) -> None:
        self.feeds = self.feed_panel.feeds
        settings = self.dashboard_panel.settings_snapshot(load_settings())
        articles, tokens, seconds = estimate_generation(self.feeds, settings)
        self.dashboard_panel.set_estimate(articles, tokens, seconds)
        self.status_bar.update_context(self.feeds, settings, tokens)

    def _ai_enabled(self, settings: AppSettings) -> bool:
        return any(
            [
                settings.pipeline_score_enabled,
                settings.pipeline_filter_enabled,
                settings.pipeline_category_enabled,
                settings.pipeline_keywords_enabled,
                settings.pipeline_summary_enabled,
                settings.pipeline_translation_enabled,
                settings.ai_summary_enabled,
                settings.ai_translation_enabled,
            ]
        )
