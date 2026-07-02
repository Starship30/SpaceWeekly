import ctypes
import logging
import sys

from dependency_check import dependency_message
from dependency_check import missing_dependencies


def main() -> None:
    """Start the SpaceWeekly desktop application."""
    missing = missing_dependencies()

    if missing:
        _show_dependency_error(dependency_message(missing))
        return

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from resources import resource_path
    from ui.main_window import MainWindow

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    app = QApplication(sys.argv)
    icon_path = resource_path("assets", "sspo.ico")

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        logging.getLogger(__name__).warning("Icon not found %s", icon_path)

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
