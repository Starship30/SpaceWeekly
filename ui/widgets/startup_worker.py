from PySide6.QtCore import QThread
from PySide6.QtCore import Signal

from services.settings import AppSettings
from services.startup_check import run_startup_checks


class StartupCheckWorker(QThread):
    """Run network-aware startup diagnostics without blocking the GUI."""

    check_ready = Signal(str, bool, str)

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        for check in run_startup_checks(self.settings):
            self.check_ready.emit(check.name, check.ok, check.detail)
