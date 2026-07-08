from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QFrame
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from services.settings import AppSettings


REPORT_STYLES = [
    ("complete", "完整模式（全部新闻）"),
    ("standard", "标准模式（重点 + 推荐）"),
    ("curated", "精选模式（仅重点）"),
    ("custom", "自定义"),
]


class DashboardPanel(QWidget):
    """Primary report setup and generation panel."""

    settings_changed = Signal()
    start_requested = Signal()

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.apply_settings(settings)

    def apply_settings(self, settings: AppSettings) -> None:
        self.report_title_line.setText(settings.report_title)
        self.rss_limit_spin.setValue(max(1, settings.rss_limit or 20))
        style = settings.report_style or "standard"

        for button in self.report_style_group.buttons():
            button.setChecked(button.property("style") == style)

        if not self.report_style_group.checkedButton():
            self.report_style_group.buttons()[1].setChecked(True)

    def settings_snapshot(self, settings: AppSettings) -> AppSettings:
        return replace(
            settings,
            report_title=self.report_title_line.text().strip() or "SpaceWeekly",
            rss_limit=self.rss_limit_spin.value(),
            report_style=self.report_style(),
            ai_importance_enabled=True,
        )

    def report_style(self) -> str:
        button = self.report_style_group.checkedButton()

        if button is None:
            return "standard"

        return str(button.property("style"))

    def set_estimate(self, articles: int, tokens: int, seconds: int) -> None:
        self.article_value.setText(str(articles))
        self.token_value.setText(f"{tokens:,}")
        self.time_value.setText(f"约 {seconds} 秒")

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.start_button.setText("生成中..." if running else "开始生成")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)
        title = QLabel("Dashboard")
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
        form.addRow("周报标题", self.report_title_line)
        form.addRow("RSS 最大读取数量", self.rss_limit_spin)
        layout.addLayout(form)
        layout.addWidget(self._style_box())
        layout.addWidget(self._estimate_box())
        layout.addStretch(1)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start_button = QPushButton("开始生成")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_requested.emit)
        action_row.addWidget(self.start_button)
        layout.addLayout(action_row)

    def _style_box(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SubtlePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        label = QLabel("周报风格")
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        self.report_style_group = QButtonGroup(self)

        for value, text in REPORT_STYLES:
            button = QRadioButton(text)
            button.setProperty("style", value)
            button.toggled.connect(lambda _checked: self.settings_changed.emit())
            self.report_style_group.addButton(button)
            layout.addWidget(button)

        return frame

    def _estimate_box(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SubtlePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        label = QLabel("预计")
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.article_value = self._metric_value()
        self.token_value = self._metric_value()
        self.time_value = self._metric_value()

        for column, (name, value) in enumerate(
            [
                ("文章数量", self.article_value),
                ("Token", self.token_value),
                ("耗时", self.time_value),
            ]
        ):
            metric_name = QLabel(name)
            metric_name.setObjectName("MetricLabel")
            grid.addWidget(metric_name, 0, column)
            grid.addWidget(value, 1, column)

        layout.addLayout(grid)

        return frame

    def _metric_value(self) -> QLabel:
        label = QLabel("0")
        label.setObjectName("MetricValue")

        return label
