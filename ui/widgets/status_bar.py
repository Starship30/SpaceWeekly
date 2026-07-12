from pathlib import Path

from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QStatusBar

from services.feed_manager import FeedSource
from services.settings import AppSettings
from i18n import tr


class SpaceStatusBar(QStatusBar):
    """Status bar with generation and configuration indicators."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.state_label = QLabel(tr("ready"))
        self.rss_label = QLabel(f"{tr('status.rss')} 0")
        self.provider_label = QLabel(f"{tr('status.provider')} -")
        self.sqlite_label = QLabel(f"{tr('status.sqlite')} -")
        self.token_label = QLabel(f"{tr('status.token')} 0")
        self.export_label = QLabel(f"{tr('status.export')} -")

        for label in [
            self.rss_label,
            self.provider_label,
            self.sqlite_label,
            self.token_label,
            self.export_label,
        ]:
            self.addPermanentWidget(label)

        self.show_state(tr("ready"))

    def show_state(self, state: str) -> None:
        self.state_label.setText(state)
        self.showMessage(state)

    def update_context(
        self,
        feeds: list[FeedSource],
        settings: AppSettings,
        token_estimate: int = 0,
    ) -> None:
        enabled_count = len([feed for feed in feeds if feed.enabled])
        self.rss_label.setText(f"{tr('status.rss')} {enabled_count}/{len(feeds)}")
        self.provider_label.setText(f"{tr('status.provider')} {settings.ai_provider}")
        self.sqlite_label.setText(
            tr("status.sqlite.on") if settings.export_sqlite else tr("status.sqlite.off")
        )
        self.sqlite_label.setToolTip(str(Path(settings.sqlite_path)))
        self.token_label.setText(f"{tr('status.token')} {token_estimate:,}")
        self.export_label.setText(f"{tr('status.export')} {self._export_text(settings)}")

    def _export_text(self, settings: AppSettings) -> str:
        formats = []

        if settings.export_markdown:
            formats.append("Markdown")

        if settings.export_word:
            formats.append("Word")

        if settings.export_sqlite:
            formats.append("SQLite")

        return "+".join(formats) or tr("none")
