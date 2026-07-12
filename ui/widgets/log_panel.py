from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr


class LogPanel(QWidget):
    """Colorized application log view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def append_html(self, message: str) -> None:
        self.log_view.append(message)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def clear(self) -> None:
        self.log_view.clear()

    def apply_theme(self, theme_manager) -> None:
        self.log_view.document().setDefaultStyleSheet(theme_manager.log_stylesheet())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 18, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel(tr("logs"))
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        header.addStretch(1)
        clear_button = QPushButton(tr("clear.logs"))
        clear_button.clicked.connect(self.clear)
        header.addWidget(clear_button)
        layout.addLayout(header)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)
