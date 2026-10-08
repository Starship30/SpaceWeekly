from PySide6.QtCore import Qt
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QFrame
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr
from services.settings import AppSettings


FILTER_LEVELS = [
    ("High_only", "filter.level.high"),
    ("High_Medium", "filter.level.high.medium"),
    ("All", "filter.level.all"),
]


class DashboardPanel(QWidget):
    """Primary report setup and generation panel."""

    settings_changed = Signal()
    start_requested = Signal()

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkspacePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build_ui()
        self.apply_settings(settings)

    def apply_settings(self, settings: AppSettings) -> None:
        self.report_title_line.setText(settings.report_title)
        self.rss_limit_spin.setValue(max(1, settings.rss_limit or 20))
        level = settings.news_filter_level or "High_Medium"

        for button in self.filter_level_group.buttons():
            button.setChecked(button.property("filter_level") == level)

        if not self.filter_level_group.checkedButton():
            self.filter_level_group.buttons()[1].setChecked(True)

    def settings_snapshot(self, settings: AppSettings) -> AppSettings:
        return replace(
            settings,
            report_title=self.report_title_line.text().strip() or "SpaceWeekly",
            rss_limit=self.rss_limit_spin.value(),
            news_filter_level=self.news_filter_level(),
        )

    def news_filter_level(self) -> str:
        button = self.filter_level_group.checkedButton()

        if button is None:
            return "High_Medium"

        return str(button.property("filter_level"))

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.start_button.setText(tr("generating") if running else tr("start"))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)
        title = QLabel(tr("dashboard"))
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.setLabelAlignment(form.labelAlignment())
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        self.report_title_line = QLineEdit()
        self.rss_limit_spin = QSpinBox()
        self.rss_limit_spin.setRange(1, 500)
        self.rss_limit_spin.setKeyboardTracking(False)
        self.rss_limit_spin.valueChanged.connect(
            lambda _value: self.settings_changed.emit()
        )
        self.report_title_line.textChanged.connect(
            lambda _text: self.settings_changed.emit()
        )
        form.addRow(tr("report.title"), self.report_title_line)
        form.addRow(tr("rss.limit"), self.rss_limit_spin)
        layout.addLayout(form)
        layout.addWidget(self._filter_level_box())
        layout.addStretch(1)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start_button = QPushButton(tr("start"))
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_requested.emit)
        action_row.addWidget(self.start_button)
        layout.addLayout(action_row)

    def _filter_level_box(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SubtlePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        label = QLabel(tr("news.filter.level"))
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        self.filter_level_group = QButtonGroup(self)

        for value, text in FILTER_LEVELS:
            button = QRadioButton(tr(text))
            button.setProperty("filter_level", value)
            button.toggled.connect(lambda _checked: self.settings_changed.emit())
            self.filter_level_group.addButton(button)
            layout.addWidget(button)

        return frame
