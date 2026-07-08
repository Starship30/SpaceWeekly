from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget


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

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 18, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("日志")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        header.addStretch(1)
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(self.clear)
        header.addWidget(clear_button)
        layout.addLayout(header)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setDefaultStyleSheet(
            ".info { color: #2563eb; }"
            ".success { color: #16833a; font-weight: 600; }"
            ".warning { color: #b45309; font-weight: 600; }"
            ".error, .critical { color: #dc2626; font-weight: 700; }"
        )
        layout.addWidget(self.log_view, 1)
