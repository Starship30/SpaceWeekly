from PySide6.QtCore import QDate, Qt
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QFrame
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QScrollArea
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr
from services.settings import AppSettings
from ui.widgets.combo_box import RoundedComboBox
from ui.widgets.date_range import LAUNCH_DATE_RANGES, NEWS_DATE_RANGES, date_edit


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
        self.rss_limit_spin.setValue(settings.rss_limit or 20)
        self.news_limit_combo.setCurrentIndex(1 if settings.rss_limit == 0 else 0)
        self.rss_limit_spin.setEnabled(settings.rss_limit != 0)
        self.news_dedup_check.setChecked(settings.news_dedup_enabled)
        for combo, start, end, mode, first, last in [
            (self.date_mode_combo, self.start_date_edit, self.end_date_edit,
             settings.date_mode, settings.start_date, settings.end_date),
            (self.launch_range_combo, self.launch_start_date_edit, self.launch_end_date_edit,
             settings.launch_range, settings.launch_start_date, settings.launch_end_date),
        ]:
            combo.setCurrentIndex(max(combo.findData(mode), 0))
            for edit, value, offset in [(start, first, -6), (end, last, 0)]:
                parsed = QDate.fromString(value, "yyyy-MM-dd")
                edit.setDate(parsed if parsed.isValid() else QDate.currentDate().addDays(offset))
        level = settings.news_filter_level or "High_Medium"

        for button in self.filter_level_group.buttons():
            button.setChecked(button.property("filter_level") == level)

        if not self.filter_level_group.checkedButton():
            self.filter_level_group.buttons()[1].setChecked(True)

    def settings_snapshot(self, settings: AppSettings) -> AppSettings:
        return replace(
            settings,
            report_title=self.report_title_line.text().strip() or "SpaceWeekly",
            rss_limit=0 if self.news_limit_combo.currentData() == "unlimited" else self.rss_limit_spin.value(),
            news_dedup_enabled=self.news_dedup_check.isChecked(),
            news_filter_level=self.news_filter_level(),
            date_mode=str(self.date_mode_combo.currentData()),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
            launch_range=str(self.launch_range_combo.currentData()),
            launch_start_date=self.launch_start_date_edit.date().toString("yyyy-MM-dd"),
            launch_end_date=self.launch_end_date_edit.date().toString("yyyy-MM-dd"),
        )

    def date_ranges_valid(self) -> bool:
        return all(
            combo.currentData() != "custom" or start.date() <= end.date()
            for combo, start, end in [
                (self.date_mode_combo, self.start_date_edit, self.end_date_edit),
                (self.launch_range_combo, self.launch_start_date_edit, self.launch_end_date_edit),
            ]
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
        layout.setSpacing(12)
        title = QLabel(tr("dashboard"))
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls = QWidget()
        controls.setObjectName("DashboardControls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        scroll.setWidget(controls)
        layout.addWidget(scroll, 1)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(form.labelAlignment())
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        self.report_title_line = QLineEdit()
        self.rss_limit_spin = QSpinBox()
        self.rss_limit_spin.setRange(1, 2_147_483_647)
        self.rss_limit_spin.setKeyboardTracking(False)
        self.rss_limit_spin.valueChanged.connect(
            lambda _value: self.settings_changed.emit()
        )
        self.report_title_line.textChanged.connect(
            lambda _text: self.settings_changed.emit()
        )
        form.addRow(tr("report.title"), self.report_title_line)
        self.news_limit_combo = RoundedComboBox()
        self.news_limit_combo.addItem(tr("news.limit.custom"), "custom")
        self.news_limit_combo.addItem(tr("news.limit.unlimited"), "unlimited")
        self.news_limit_combo.currentIndexChanged.connect(self._limit_changed)
        limit_row = QHBoxLayout()
        limit_row.setSpacing(8)
        limit_row.addWidget(self.news_limit_combo, 1)
        limit_row.addWidget(self.rss_limit_spin, 1)
        form.addRow(tr("rss.limit"), limit_row)
        self.news_dedup_check = QCheckBox(tr("news.deduplicate"))
        self.news_dedup_check.setToolTip(tr("news.deduplicate.hint"))
        self.news_dedup_check.toggled.connect(lambda: self.settings_changed.emit())
        form.addRow("", self.news_dedup_check)
        controls_layout.addLayout(form)
        limit_hint = QLabel(tr("news.limit.hint"))
        limit_hint.setObjectName("Eyebrow")
        limit_hint.setWordWrap(True)
        controls_layout.addWidget(limit_hint)
        controls_layout.addWidget(self._filter_level_box())
        news_frame, self.date_mode_combo, self.start_date_edit, self.end_date_edit = self._range_box(
            "dashboard.news.range", NEWS_DATE_RANGES,
        )
        launch_frame, self.launch_range_combo, self.launch_start_date_edit, self.launch_end_date_edit = self._range_box(
            "dashboard.launch.range", LAUNCH_DATE_RANGES,
        )
        controls_layout.addWidget(news_frame)
        controls_layout.addWidget(launch_frame)
        controls_layout.addStretch(1)
        scroll.setMinimumWidth(controls.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width())
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start_button = QPushButton(tr("start"))
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_requested.emit)
        action_row.addWidget(self.start_button)
        layout.addLayout(action_row)

    def _limit_changed(self) -> None:
        self.rss_limit_spin.setEnabled(self.news_limit_combo.currentData() != "unlimited")
        self.settings_changed.emit()

    def _range_box(self, title: str, presets: tuple):
        frame = QFrame()
        frame.setObjectName("SubtlePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        heading = QHBoxLayout()
        label = QLabel(tr(title))
        label.setObjectName("SectionTitle")
        heading.addWidget(label)
        combo = RoundedComboBox()
        for text, mode in presets:
            combo.addItem(tr(text), mode)
        combo.addItem(tr("date.custom"), "custom")
        heading.addWidget(combo, 1)
        layout.addLayout(heading)
        dates = QWidget()
        dates.setProperty("formGroup", True)
        date_layout = QHBoxLayout(dates)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(8)
        start, end = date_edit("", -6), date_edit("", 0)
        for edit, text in [(start, "start.date"), (end, "end.date")]:
            edit.setToolTip(tr(text))
            edit.setAccessibleName(f"{tr(title)} {tr(text)}")
            edit.dateChanged.connect(lambda _date: self.settings_changed.emit())
        date_layout.addWidget(start, 1)
        date_layout.addWidget(QLabel("—"))
        date_layout.addWidget(end, 1)
        layout.addWidget(dates)

        def update() -> None:
            custom = combo.currentData() == "custom"
            dates.setVisible(custom)
            dates.setEnabled(custom)
            self.settings_changed.emit()

        combo.currentIndexChanged.connect(update)
        update()
        return frame, combo, start, end

    def _filter_level_box(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SubtlePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
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
