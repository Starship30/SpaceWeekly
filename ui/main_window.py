import logging
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtCore import Qt
from PySide6.QtCore import qVersion
from PySide6.QtCore import QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMainWindow
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QSplitter

from config import APP_VERSION
from feeds.launches import get_launches
from feeds.launches import last_error as launch_last_error
from i18n import set_language
from i18n import tr
from resources import resource_path
from services.feed_manager import load_feeds
from services.settings import AppSettings
from services.settings import load_settings
from services.settings import save_settings
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
from ui.widgets.startup_worker import StartupCheckWorker
from translator.translator import Translator
from utils.time import format_datetime

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Main desktop window for SpaceWeekly."""

    def __init__(self) -> None:
        super().__init__()
        self.feeds = load_feeds()
        self.settings = load_settings()
        set_language(self.settings.language)
        self.worker: ReportWorker | None = None
        self.startup_worker: StartupCheckWorker | None = None
        self._close_after_cancel = False
        self.log_handler = QtLogHandler()
        app = QApplication.instance()

        if app is None:
            raise RuntimeError("ThemeManager requires an active QApplication")

        self.theme_manager = ThemeManager(app)
        self._setup_window()
        self._build_ui()
        self._connect_ui()
        self._connect_logging()
        logger.info("SpaceWeekly v%s", APP_VERSION)
        logger.info("AI Model: %s", self.settings.deepseek_model or "-")
        self._update_context()
        self.status_bar.show_state(tr("ready"))
        QTimer.singleShot(0, self._start_startup_checks)

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
        self.toolbar.stop_requested.connect(self._cancel_report)
        self.toolbar.settings_requested.connect(self._open_settings)
        self.toolbar.prompt_studio_requested.connect(self._open_prompt_studio)
        self.toolbar.output_requested.connect(self._open_output_dir)
        self.toolbar.refresh_requested.connect(self._refresh_feeds)
        self.toolbar.launch_test_requested.connect(self._test_launch_data)
        self.toolbar.about_requested.connect(self._show_about)
        self.feed_panel.feeds_changed.connect(self._update_context)
        self.dashboard_panel.settings_changed.connect(self._update_context)
        self.dashboard_panel.start_requested.connect(self._start_report)

    def _connect_logging(self) -> None:
        self.log_handler.emitter.message.connect(self.log_panel.append_html)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

    def _refresh_feeds(self) -> None:
        self.feeds = load_feeds()
        self.feed_panel.set_feeds(self.feeds)
        self._update_context()
        logger.info("RSS 列表已刷新")

    def _test_launch_data(self) -> None:
        settings = load_settings()
        launches = get_launches(limit=5, launch_range=settings.launch_range)
        error = launch_last_error()
        translator = (
            Translator(settings=settings, mode="dictionary")
            if settings.aerospace_translation_enabled and settings.language == "zh_CN"
            else None
        )

        if error:
            QMessageBox.warning(
                self,
                tr("launch_test_title"),
                tr("launch_test_failed", error=error),
            )
            return

        lines = [tr("launch_test_count", count=len(launches))]
        separator = tr("label_separator")

        for launch in launches[:5]:
            rocket = _launch_field(launch.body, "Rocket") or tr("not_available")
            provider = (
                _launch_field(launch.body, "Launch service provider")
                or tr("not_available")
            )

            if translator is not None:
                rocket = translator.translate_term(rocket, "rocket", allow_ai=False)
                provider = translator.translate_term(
                    provider,
                    "organization",
                    allow_ai=False,
                )

            lines.extend(
                [
                    "",
                    f"{tr('launch_name')}{separator}{launch.news.title}",
                    (
                        f"{tr('launch_time')}{separator}"
                        f"{format_datetime(launch.news.published, settings.display_timezone) or '-'}"
                    ),
                    f"{tr('rocket')}{separator}{rocket}",
                    f"{tr('launch_service_provider')}{separator}{provider}",
                ]
            )

        QMessageBox.information(self, tr("launch_test_title"), "\n".join(lines))

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
                self.feeds = load_feeds()
                self.feed_panel.set_feeds(self.feeds)
                self.dashboard_panel.apply_settings(self.settings)
            self._update_context()

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
            {version}: v{app_version}<br>
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
                app_version=APP_VERSION,
                github=tr("github.link"),
                python_version=tr("python.version"),
                python=sys.version.split()[0],
                qt_version=tr("qt.version"),
                qt=qVersion(),
                author=tr("author"),
            ),
        )

    def _start_report(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        self._save_runtime_settings()

        self._set_running(True)
        self.worker = ReportWorker(
            feeds=self.feed_panel.enabled_feeds(),
            sqlite_enabled=self.settings.export_sqlite,
            markdown_enabled=self.settings.export_markdown,
            word_enabled=self.settings.export_word,
        )
        self.worker.finished_ok.connect(self._report_finished)
        self.worker.failed.connect(self._report_failed)
        self.worker.cancelled.connect(self._report_cancelled)
        self.worker.start()

    def _cancel_report(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return

        self.toolbar.stop_action.setEnabled(False)
        self.status_bar.show_state(tr("cancelling"))
        self.worker.cancel()

    def _save_runtime_settings(self) -> None:
        self.feed_panel.save_state()
        self.feeds = self.feed_panel.feeds
        self.settings = self.dashboard_panel.settings_snapshot(load_settings())
        save_settings(self.settings)
        self._update_context()

    def _set_running(self, running: bool) -> None:
        self.dashboard_panel.set_running(running)
        self.toolbar.set_running(running)
        self.status_bar.show_state(tr("running") if running else tr("ready"))

    def _report_finished(self) -> None:
        self._set_running(False)
        self.status_bar.show_state(tr("finished"))
        logging.getLogger().log(SUCCESS_LEVEL, tr("report.finished"))
        if not self._close_after_cancel:
            QMessageBox.information(self, "SpaceWeekly", tr("report.finished"))
        self.worker = None
        self._finish_pending_close()

    def _report_failed(self, message: str) -> None:
        self._set_running(False)
        self.status_bar.show_state(tr("ready"))
        if not self._close_after_cancel:
            QMessageBox.warning(self, "SpaceWeekly", message)
        self.worker = None
        self._finish_pending_close()

    def _start_startup_checks(self) -> None:
        self.startup_worker = StartupCheckWorker(self.settings, self)
        self.startup_worker.check_ready.connect(self._show_startup_check)
        self.startup_worker.finished.connect(self._startup_checks_finished)
        self.startup_worker.start()

    def _show_startup_check(self, name: str, ok: bool, detail: str) -> None:
        marker = "✓" if ok else "✗"
        log = logger.info if ok else logger.warning
        log("启动检查 %s %s：%s", marker, name, detail)

    def _startup_checks_finished(self) -> None:
        self.startup_worker = None

    def _report_cancelled(self) -> None:
        self._set_running(False)
        self.status_bar.show_state(tr("cancelled"))
        if not self._close_after_cancel:
            QMessageBox.information(self, "SpaceWeekly", tr("report.cancelled"))
        self.worker = None
        self._finish_pending_close()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._close_after_cancel = True
            self._cancel_report()
            event.ignore()
            return

        event.accept()

    def _finish_pending_close(self) -> None:
        if self._close_after_cancel:
            self._close_after_cancel = False
            QTimer.singleShot(0, self.close)

    def _open_output_dir(self) -> None:
        path = load_settings().output_dir

        if path:
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _update_context(self) -> None:
        self.feeds = self.feed_panel.feeds
        settings = self.dashboard_panel.settings_snapshot(load_settings())
        self.status_bar.update_context(self.feeds, settings)


def _launch_field(body: str, label: str) -> str:
    prefix = f"{label}:"

    for line in body.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()

    return ""
