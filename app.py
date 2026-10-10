import ctypes
import logging
import sys

from dependency_check import dependency_message
from dependency_check import missing_dependencies
from logging_config import setup_logging


def main() -> None:
    """Start the SpaceWeekly desktop application."""
    missing = missing_dependencies()

    if missing:
        _show_dependency_error(dependency_message(missing))
        return

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWidgets import QDialog

    from resources import resource_path
    from services.workspace import needs_first_run_wizard
    from ui.first_run_wizard import FirstRunWizard

    app = QApplication(sys.argv)
    icon_path = resource_path("assets", "sspo.ico")

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        logging.getLogger(__name__).warning("Icon not found %s", icon_path)

    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from pathlib import Path
        from ui.settings_dialog import SettingsDialog
        from services.settings import default_settings
        from ui.theme import ThemeManager
        from config import app_version
        from ui.widgets.dashboard_panel import DashboardPanel
        from ui.video_cards_dialog import VideoCardsDialog
        from ui.video_intro_dialog import VideoIntroDialog
        from tools import video_intro
        ThemeManager(app).apply("light")
        dialog = SettingsDialog(default_settings())
        dialog.show()
        app.processEvents()
        dialog.close()
        marker = Path(sys.argv[2])
        dashboard = DashboardPanel(default_settings())
        cards = VideoCardsDialog(str(marker.parent))
        intro = VideoIntroDialog(str(marker.parent))
        frame = video_intro.render_frame(video_intro.prepare("2026-01-01", end_date="2026-01-07"), 3)
        assert frame.size == (1920, 1080), "Packaged opener renderer failed"
        dashboard.close()
        cards.close()
        intro.close()
        marker.write_text(app_version(), encoding="utf-8")
        return

    if needs_first_run_wizard():
        wizard = FirstRunWizard()

        if wizard.exec() != QDialog.DialogCode.Accepted:
            return

    setup_logging()
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _show_dependency_error(message: str) -> None:
    if sys.platform.startswith("win"):
        ctypes.windll.user32.MessageBoxW(None, message, "SpaceWeekly", 0x10)
        return

    sys.stderr.write(message + "\n")


if __name__ == "__main__":
    main()
