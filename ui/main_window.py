"""SpaceWeekly production shell: four persistent pages and one workspace lifecycle."""
import logging
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication,QMainWindow,QMessageBox
from config import app_version
from i18n import set_language,tr
from resources import resource_path,workspace_path
from services.feed_manager import load_feeds
from services.settings import load_settings
from ui.log_handler import QtLogHandler
from ui.theme import ThemeManager
from ui.production_workspace import ProductionWorkspace

logger=logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings=load_settings();set_language(self.settings.language)
        self.theme_manager=ThemeManager(QApplication.instance());self.theme_manager.apply(self.settings.theme_mode)
        self._closing=False
        self.log_handler=QtLogHandler();logging.getLogger().addHandler(self.log_handler);logging.getLogger().setLevel(logging.INFO)
        self.setWindowTitle(tr('app.title',version=app_version()));self.resize(1440,900);self.setMinimumSize(900,620)
        icon=resource_path('assets','sspo.ico')
        if icon.exists():self.setWindowIcon(QIcon(str(icon)))
        self._build_workspace()
        logger.info('SpaceWeekly %s production workspace',app_version())

    def _build_workspace(self):
        self.production=ProductionWorkspace(self.settings,load_feeds(),self.theme_manager,self)
        self.setCentralWidget(self.production)
        self._attach_workspace()

    def _attach_workspace(self):
        self.feed_panel=self.production.feed_panel;self.dashboard_panel=self.production.dashboard;self.log_panel=self.production.log
        self.roundup_dialog=self.production.roundup;self.video_dialog=self.production.cards
        self.production.close_ready.connect(self._close_ready)
        self.production.update_ready.connect(self._apply_update)
        self.production.uninstall_ready.connect(self._uninstall)
        self.production.workspace_requested.connect(self._switch_workspace)
        self.production.settings_applied.connect(self._settings_applied)
        self.log_handler.emitter.message.connect(self.log_panel.append_html)

    def _settings_applied(self):
        self.settings=self.production.settings;self.setWindowTitle(tr('app.title',version=app_version()))
        self.production.retranslate()

    def _switch_workspace(self,path):
        from services.workspace import initialize_workspace
        previous=workspace_path()
        old=self.production
        old._release_preview()
        old.timer.stop()
        try:
            initialize_workspace(Path(path),self.settings.language)
            settings=load_settings()
            replacement=ProductionWorkspace(settings,load_feeds(),self.theme_manager,self)
            self.log_handler.emitter.message.disconnect(old.log.append_html)
            self.takeCentralWidget();self.settings=settings;self.production=replacement
            self.setCentralWidget(replacement);self._attach_workspace();old.deleteLater()
        except (OSError,ValueError) as error:
            if previous:
                from resources import set_workspace_path
                from services.workspace import _refresh_module_paths
                set_workspace_path(previous);_refresh_module_paths(previous)
            self.production=old;self.setCentralWidget(old)
            old._discard=False;old._pending_leave=None;old.timer.start()
            old.task_text.setText(str(error))

    def _close_ready(self):
        self._closing=True;QTimer.singleShot(0,self.close)

    def _apply_update(self):
        try:
            from services.updates import handoff
            handoff();self._close_ready()
        except (OSError,ValueError,RuntimeError) as error:
            self.production.updates.details.setPlainText(str(error))
            self.production.updates.status.setText('更新安装尚未开始；当前版本保留，可重新检查后重试。')

    def closeEvent(self,event):
        if self._closing:
            self.production._release_preview()
            self.production.timer.stop()
            logging.getLogger().removeHandler(self.log_handler)
            event.accept()
        else:
            event.ignore();self.production.guard(lambda:self.production.request_leave('close'))

    def _uninstall(self):
        try:
            from services.updates import handoff_uninstall
            handoff_uninstall();self._close_ready()
        except (OSError,ValueError,RuntimeError) as error:
            self.production.updates.status.setText('未开始卸载，可重试；用户数据保留。')
            self.production.updates.details.setPlainText(str(error))

    # Existing command callers now route to pages, without opening modal production windows.
    def _open_launch_roundup(self):
        self.production.content.setCurrentIndex(1);self.production.navigation.setCurrentRow(1)
    def _open_video_cards(self):self.production.media_route(1)
    def _open_settings(self):self.production.show_settings()
    def _open_prompt_studio(self):self.production.open_rules()
    def _start_report(self):self.production.guard(self.production.start_news_write)
    def _cancel_report(self):self.production.cancel_all()
    def _open_output_dir(self):self.production.open_output()
