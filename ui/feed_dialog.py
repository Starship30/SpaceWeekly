from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QLineEdit

from i18n import tr
from services.feed_manager import FeedSource


class FeedDialog(QDialog):
    """Dialog for adding or editing one RSS feed source."""

    def __init__(self, feed: FeedSource | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("feed.dialog.title"))
        self.name_edit = QLineEdit(feed.name if feed else "")
        self.rss_edit = QLineEdit(feed.rss if feed else "")
        self.enabled_check = QCheckBox()
        self.enabled_check.setChecked(feed.enabled if feed else True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QFormLayout(self)
        layout.addRow(tr("name"), self.name_edit)
        layout.addRow("RSS", self.rss_edit)
        layout.addRow(tr("enabled"), self.enabled_check)
        layout.addRow(buttons)

    def feed(self) -> FeedSource:
        """Return the edited feed source."""
        return FeedSource(
            name=self.name_edit.text().strip(),
            rss=self.rss_edit.text().strip(),
            enabled=self.enabled_check.isChecked(),
        )
