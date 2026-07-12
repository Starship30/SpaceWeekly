from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QStyle
from PySide6.QtWidgets import QToolBar

from i18n import tr


class AppToolBar(QToolBar):
    """Application toolbar with primary workspace actions."""

    start_requested = Signal()
    settings_requested = Signal()
    prompt_studio_requested = Signal()
    output_requested = Signal()
    refresh_requested = Signal()
    about_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("SpaceWeekly", parent)
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(self.iconSize())
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._build_actions()

    def set_running(self, running: bool) -> None:
        self.start_action.setEnabled(not running)

    def _build_actions(self) -> None:
        self.start_action = self._action(
            tr("start"),
            QStyle.StandardPixmap.SP_MediaPlay,
            self.start_requested.emit,
        )
        self.addAction(self.start_action)
        self.addSeparator()
        self.addAction(
            self._action(
                tr("settings"),
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
                self.settings_requested.emit,
            )
        )
        self.addAction(
            self._action(
                tr("prompt.studio"),
                QStyle.StandardPixmap.SP_FileDialogContentsView,
                self.prompt_studio_requested.emit,
            )
        )
        self.addAction(
            self._action(
                tr("open.output"),
                QStyle.StandardPixmap.SP_DirOpenIcon,
                self.output_requested.emit,
            )
        )
        self.addAction(
            self._action(
                tr("refresh.rss"),
                QStyle.StandardPixmap.SP_BrowserReload,
                self.refresh_requested.emit,
            )
        )
        self.addSeparator()
        self.addAction(
            self._action(
                tr("about"),
                QStyle.StandardPixmap.SP_MessageBoxInformation,
                self.about_requested.emit,
            )
        )

    def _action(
        self,
        text: str,
        icon: QStyle.StandardPixmap,
        handler,
    ) -> QAction:
        action = QAction(self.style().standardIcon(icon), text, self)
        action.triggered.connect(handler)

        return action
