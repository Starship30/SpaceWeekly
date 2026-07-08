from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QStyle
from PySide6.QtWidgets import QToolBar


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
            "开始生成",
            QStyle.StandardPixmap.SP_MediaPlay,
            self.start_requested.emit,
        )
        self.addAction(self.start_action)
        self.addSeparator()
        self.addAction(
            self._action(
                "设置",
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
                self.settings_requested.emit,
            )
        )
        self.addAction(
            self._action(
                "Prompt Studio",
                QStyle.StandardPixmap.SP_FileDialogContentsView,
                self.prompt_studio_requested.emit,
            )
        )
        self.addAction(
            self._action(
                "打开输出目录",
                QStyle.StandardPixmap.SP_DirOpenIcon,
                self.output_requested.emit,
            )
        )
        self.addAction(
            self._action(
                "刷新 RSS",
                QStyle.StandardPixmap.SP_BrowserReload,
                self.refresh_requested.emit,
            )
        )
        self.addSeparator()
        self.addAction(
            self._action(
                "关于",
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
