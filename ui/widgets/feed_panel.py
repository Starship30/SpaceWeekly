from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr
from services.feed_manager import FeedSource
from services.feed_manager import save_feeds
from ui.feed_dialog import FeedDialog


class FeedPanel(QWidget):
    """RSS feed list with compact editing controls."""

    feeds_changed = Signal()

    def __init__(self, feeds: list[FeedSource], parent=None) -> None:
        super().__init__(parent)
        self.feeds = list(feeds)
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self._build_ui()
        self.refresh()

    def enabled_feeds(self) -> list[FeedSource]:
        self.save_state()
        return [feed for feed in self.feeds if feed.enabled]

    def set_feeds(self, feeds: list[FeedSource]) -> None:
        self.feeds = list(feeds)
        self.refresh()
        self.feeds_changed.emit()

    def save_state(self) -> None:
        self._sync_enabled_state()
        save_feeds(self.feeds)

    def refresh(self) -> None:
        self.feed_list.clear()

        for feed in self.feeds:
            item = QListWidgetItem(feed.name or feed.rss)
            item.setToolTip(feed.rss)
            item.setCheckState(
                Qt.CheckState.Checked
                if feed.enabled
                else Qt.CheckState.Unchecked
            )
            self.feed_list.addItem(item)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 12, 18)
        layout.setSpacing(10)
        title = QLabel(tr("feed.sources"))
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        self.feed_list = QListWidget()
        self.feed_list.setSpacing(3)
        self.feed_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.feed_list, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        for label, handler in [
            (tr("add"), self._add_feed),
            (tr("edit"), self._edit_feed),
            (tr("delete"), self._delete_feed),
            (tr("save"), self._save_clicked),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)

        layout.addLayout(buttons)

    def _sync_enabled_state(self) -> None:
        for index, feed in enumerate(self.feeds):
            item = self.feed_list.item(index)

            if item is not None:
                feed.enabled = item.checkState() == Qt.CheckState.Checked

    def _on_item_changed(self, _item=None) -> None:
        self._sync_enabled_state()
        self.feeds_changed.emit()

    def _save_clicked(self) -> None:
        self.save_state()
        self.feeds_changed.emit()

    def _add_feed(self) -> None:
        dialog = FeedDialog(parent=self)

        if dialog.exec():
            self.feeds.append(dialog.feed())
            self.refresh()
            self._save_clicked()

    def _edit_feed(self) -> None:
        index = self.feed_list.currentRow()

        if index < 0:
            return

        dialog = FeedDialog(self.feeds[index], self)

        if dialog.exec():
            self.feeds[index] = dialog.feed()
            self.refresh()
            self._save_clicked()

    def _delete_feed(self) -> None:
        index = self.feed_list.currentRow()

        if index < 0:
            return

        del self.feeds[index]
        self.refresh()
        self._save_clicked()
