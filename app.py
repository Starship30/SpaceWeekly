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
        from ui.video_download_dialog import VideoDownloadDialog
        from services.video_download import backend_command, _run_process
        from services.cancellation import no_cancel
        from models.video_download import VideoInfo, VideoFormat
        from qrcode import make as make_qr
        from qrcode.image.pil import PilImage
        from io import BytesIO
        from ui.prompt_studio_dialog import PromptStudioDialog
        from services.news_dates import publication_evidence
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
        writing = PromptStudioDialog(default_settings())
        downloads = VideoDownloadDialog(marker.parent, [
            dict(video_type="launch", title="Smoke test", summary="Smoke mission",
                 published="2026-10-09T08:00:00+08:00")])
        downloads.rights.setChecked(True)
        assert downloads.demo.isEnabled(), "Packaged batch download UI failed"
        formats = [VideoFormat("low", 360, "avc1"), VideoFormat("high", 1080, "avc1")]
        downloads._show_parsed_video(VideoInfo("smoke", "Smoke video",
            "https://www.bilibili.com/video/BV1xx411c7mD", formats=formats))
        assert downloads.formats.currentData() == formats[1], "Highest default quality failed"
        backend = Path(backend_command()[0])
        assert backend.name.lower() == "yt-dlp.exe" and backend.is_file(), "Download backend missing"
        backend_output = []
        _run_process([str(backend), "--version"], no_cancel, backend_output.append, timeout=30)
        assert backend_output and backend_output[-1] == "2026.08.19", "Packaged backend execution failed"
        with BytesIO() as qr:
            make_qr("SpaceWeekly 3.0.2 package check", image_factory=PilImage).save(qr, format="PNG")
            assert qr.getvalue().startswith(bytes([137, 80, 78, 71])), "Packaged QR renderer failed"
        downloads.show()
        app.processEvents()
        downloads.close()
        assert writing.current_type == "filter", "Writing rules failed to initialize"
        evidence = publication_evidence(
            '<meta name="PubDate" content="2026-09-30"><article>'
            + "A fictional mission announces its first science images and begins science operations."
            + "</article>", "https://www.cas.cn/test")
        assert evidence["published"] == "2026-09-30", "Packaged date parser failed"
        frame = video_intro.render_frame(video_intro.prepare("2026-01-01", end_date="2026-01-07"), 3)
        assert frame.size == (1920, 1080), "Packaged opener renderer failed"
        writing.close()
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
