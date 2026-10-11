import sys
from dataclasses import replace

from PySide6.QtCore import QTimer
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtCore import qVersion
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QCheckBox
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from ui.widgets.model_worker import ModelWorker
from ui.widgets.date_range import LAUNCH_DATE_RANGES, NEWS_DATE_RANGES, date_edit
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QDateEdit
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QDoubleSpinBox
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QScrollArea
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QStackedWidget
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from config import app_version
from ai.configuration import PROVIDER_URLS
from ai.configuration import provider_config_from_settings
from ai.providers import test_connection
from i18n import tr
from resources import resource_path
from services.settings import AppSettings
from services.settings import load_settings
from ui.first_run_wizard import FirstRunWizard
from ui.prompt_studio_dialog import PromptStudioDialog
from ui.term_editor_dialog import TermEditorDialog
from ui.theme import THEME_DARK
from ui.theme import THEME_LIGHT
from ui.theme import THEME_SYSTEM


class SettingsDialog(QDialog):
    """Sectioned settings dialog for user and advanced configuration."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.original_settings = settings
        self.setWindowTitle(tr("settings"))
        self.resize(1040, 760)
        self.setMinimumSize(900, 680)
        self._set_icon()
        self._create_fields(settings)
        self._build_layout()

    def settings(self) -> AppSettings:
        """Return edited settings."""
        selected_body = self.body_mode_group.checkedButton()
        body_mode = selected_body.property("mode") if selected_body else "original"

        return replace(
            self.original_settings,
            deepseek_api_key=self.api_key_edit.text().strip(),
            deepseek_base_url=self.base_url_edit.text().strip(),
            deepseek_model=self.model_combo.currentText().strip(),
            model_candidates=list(
                dict.fromkeys(
                    [
                        self.model_combo.currentText().strip(),
                        *[self.model_combo.itemText(i) for i in range(self.model_combo.count())],
                    ]
                )
            ),
            ai_provider=str(self.provider_combo.currentData()),
            temperature=self.temperature_spin.value(),
            user_agent=self.user_agent_edit.text().strip(),
            web_search_provider=str(self.search_provider_combo.currentData()),
            web_search_api_key=self.search_api_key_edit.text().strip(),
            request_timeout=self.timeout_spin.value(),
            connect_timeout=self.connect_timeout_spin.value(),
            read_timeout=self.read_timeout_spin.value(),
            retry_count=self.retry_count_spin.value(),
            debug_ai_enabled=self.debug_ai_check.isChecked(),
            sqlite_path=self.sqlite_edit.text().strip(),
            output_dir=self.output_edit.text().strip(),
            export_sqlite=self.sqlite_check.isChecked(),
            export_markdown=self.markdown_check.isChecked(),
            export_word=self.word_check.isChecked(),
            export_premiere=self.premiere_check.isChecked(),
            include_title=self.include_title_check.isChecked(),
            include_source=self.include_source_check.isChecked(),
            include_published=self.include_published_check.isChecked(),
            include_link=self.include_link_check.isChecked(),
            include_score=self.include_importance_check.isChecked(),
            include_summary=self.include_summary_check.isChecked(),
            include_keywords=self.include_keywords_check.isChecked(),
            include_body=self.include_body_check.isChecked(),
            include_original=self.include_original_check.isChecked(),
            body_mode=str(body_mode),
            theme_mode=str(self.theme_mode_combo.currentData()),
            language=str(self.language_combo.currentData()),
            news_event_date_check_enabled=self.news_date_check.isChecked(),
            date_mode="custom" if self.news_custom_radio.isChecked() else str(self.date_mode_combo.currentData()),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
            release_mode=self.release_mode_check.isChecked(),
            launch_range="custom" if self.launch_custom_radio.isChecked() else str(self.launch_range_combo.currentData()),
            launch_start_date=self.launch_start_date_edit.date().toString("yyyy-MM-dd"),
            launch_end_date=self.launch_end_date_edit.date().toString("yyyy-MM-dd"),
            aerospace_translation_enabled=self.aerospace_translation_check.isChecked(),
            aerospace_translation_mode=str(self.aerospace_translation_mode_combo.currentData()),
            display_timezone=str(self.display_timezone_combo.currentData()),
        )

    def accept(self) -> None:
        for custom, start, end in [
            (self.news_custom_radio, self.start_date_edit, self.end_date_edit),
            (self.launch_custom_radio, self.launch_start_date_edit, self.launch_end_date_edit),
        ]:
            if custom.isChecked() and start.date() > end.date():
                QMessageBox.warning(self, tr("settings"), tr("date.range.invalid"))
                return
        super().accept()

    def _set_icon(self) -> None:
        icon_path = resource_path("assets", "sspo.ico")

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _create_fields(self, settings: AppSettings) -> None:
        self.api_key_edit = QLineEdit(settings.deepseek_api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.provider_combo = QComboBox()
        for name in PROVIDER_URLS:
            self.provider_combo.addItem(name, name)
        self.provider_combo.addItem(tr("provider.custom"), "Custom")
        provider = settings.ai_provider
        expected_url = PROVIDER_URLS.get(provider, "")
        configured_url = settings.deepseek_base_url.rstrip("/")
        if provider not in PROVIDER_URLS or (configured_url and configured_url not in {expected_url, expected_url + "/v1"}):
            provider = "Custom"
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(provider))
        self._current_provider = provider
        self._provider_values = {}
        self.base_url_edit = QLineEdit(configured_url or expected_url)
        self.base_url_edit.setReadOnly(provider != "Custom")
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.addItems(list(dict.fromkeys([settings.deepseek_model, *settings.model_candidates])))
        self.model_combo.setCurrentText(settings.deepseek_model)
        self.model_status = QLabel(tr("models.key.required"))
        self.model_status.setObjectName("Eyebrow")
        self.model_status.setWordWrap(True)
        self._model_request = None
        self._model_timer = QTimer(self)
        self._model_timer.setSingleShot(True)
        self._model_timer.setInterval(400)
        self._model_timer.timeout.connect(self._discover_models)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.api_key_edit.editingFinished.connect(self._queue_model_discovery)
        self.base_url_edit.editingFinished.connect(self._queue_model_discovery)
        if settings.deepseek_api_key:
            self._model_timer.start()
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0, 2)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(settings.temperature)
        self.timeout_spin = self._spin(1, 300, settings.request_timeout)
        self.connect_timeout_spin = self._spin(1, 120, settings.connect_timeout)
        self.read_timeout_spin = self._spin(1, 300, settings.read_timeout)
        self.retry_count_spin = self._spin(0, 10, max(settings.retry_count, 3))
        self.debug_ai_check = self._check(tr("show.ai.debug"), settings.debug_ai_enabled)
        self.release_mode_check = self._check(tr("release.mode"), settings.release_mode)
        self.user_agent_edit = QLineEdit(settings.user_agent)
        self.search_provider_combo = QComboBox()
        for key in ('duckduckgo', 'brave', 'wikipedia'):
            self.search_provider_combo.addItem(tr('search.provider.'+key), key)
        self.search_provider_combo.setCurrentIndex(max(0, self.search_provider_combo.findData(settings.web_search_provider.lower())))
        self.search_api_key_edit = QLineEdit(settings.web_search_api_key)
        self.search_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.search_api_key_edit.setPlaceholderText(tr('search.api_key.hint'))
        self.search_api_key_edit.setEnabled(self.search_provider_combo.currentData() == 'brave')
        self.search_provider_combo.currentIndexChanged.connect(
            lambda: self.search_api_key_edit.setEnabled(self.search_provider_combo.currentData() == 'brave'))
        self.sqlite_edit = QLineEdit(settings.sqlite_path)
        self.output_edit = QLineEdit(settings.output_dir)
        self.sqlite_check = self._check("", settings.export_sqlite)
        self.markdown_check = self._check("", settings.export_markdown)
        self.word_check = self._check("", settings.export_word)
        self.premiere_check = self._check("", settings.export_premiere)
        self.include_title_check = self._check(tr("include.title"), settings.include_title)
        self.include_source_check = self._check(tr("include.source"), settings.include_source)
        self.include_published_check = self._check(tr("include.published"), settings.include_published)
        self.include_link_check = self._check(tr("include.link"), settings.include_link)
        self.include_importance_check = self._check(tr("include.score"), settings.include_score)
        self.include_summary_check = self._check(tr("include.summary"), settings.include_summary)
        self.include_keywords_check = self._check(tr("include.keywords"), settings.include_keywords)
        self.include_body_check = self._check(tr("include.body"), settings.include_body)
        self.include_original_check = self._check(tr("include.original"), settings.include_original)
        self.theme_mode_combo = self._theme_mode_combo(settings.theme_mode)
        self.language_combo = self._language_combo(settings.language)
        self.news_date_check = self._check(tr("news.event_date_check"), settings.news_event_date_check_enabled)
        self.news_date_check.setToolTip(tr("news.event_date_check.hint"))
        self.date_mode_combo = self._date_mode_combo(settings.date_mode)
        self.start_date_edit = date_edit(settings.start_date, -6)
        self.end_date_edit = date_edit(settings.end_date, 0)
        self.body_mode_group = QButtonGroup(self)
        self.launch_range_combo = self._launch_range_combo(settings.launch_range)
        self.launch_start_date_edit = date_edit(settings.launch_start_date, -6)
        self.launch_end_date_edit = date_edit(settings.launch_end_date, 0)
        self.news_range_selector, self.news_custom_radio = self._range_selector(
            settings.date_mode, self.date_mode_combo, self.start_date_edit, self.end_date_edit
        )
        self.launch_range_selector, self.launch_custom_radio = self._range_selector(
            settings.launch_range, self.launch_range_combo, self.launch_start_date_edit, self.launch_end_date_edit
        )
        self.display_timezone_combo = self._display_timezone_combo(
            settings.display_timezone
        )
        self.aerospace_translation_check = self._check(
            tr("aerospace.translation.enabled"),
            settings.aerospace_translation_enabled,
        )
        self.aerospace_translation_mode_combo = self._aerospace_translation_mode_combo(
            settings.aerospace_translation_mode
        )
        self.aerospace_translation_mode_combo.setEnabled(
            settings.aerospace_translation_enabled
        )
        self.aerospace_translation_check.toggled.connect(
            self.aerospace_translation_mode_combo.setEnabled
        )

    def _build_layout(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(24, 22, 24, 22)
        main.setSpacing(20)
        header = QHBoxLayout()
        title = QLabel(tr("settings"))
        title.setObjectName("HeroTitle")
        header.addWidget(title)
        header.addStretch()
        brand = QLabel(f"SPACEWEEKLY   /   v{app_version()}")
        brand.setObjectName("Eyebrow")
        header.addWidget(brand)
        main.addLayout(header)
        content = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(200)
        self.nav.setSpacing(8)
        content.setSpacing(22)
        self.stack = QStackedWidget()

        for title, page in [
            (tr("appearance"), self._appearance_page()),
            (tr("ai"), self._ai_page()),
            (tr("prompt"), self._prompt_page()),
            (tr("export"), self._export_page()),
            (tr("news.settings"), self._news_page()),
            (tr("launch.settings"), self._launch_page()),
            (tr("network"), self._network_page()),
            (tr("advanced"), self._advanced_page()),
            (tr("about"), self._about_page()),
        ]:
            self.nav.addItem(title)
            self.nav.item(self.nav.count() - 1).setSizeHint(QSize(160, 48))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        content.addWidget(self.nav)
        content.addWidget(self.stack, 1)
        main.addLayout(content, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("save"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

    def _appearance_page(self) -> QWidget:
        page, layout = self._form_page(tr("appearance"))
        layout.addRow(tr("theme"), self.theme_mode_combo)
        layout.addRow(tr("language"), self.language_combo)
        reset_button = QPushButton(tr("reset.workspace"))
        reset_button.clicked.connect(self._reset_workspace)
        layout.addRow(reset_button)

        return page

    def _ai_page(self) -> QWidget:
        page, layout = self._form_page(tr("ai"))
        layout.addRow(tr("provider"), self.provider_combo)
        layout.addRow("API Key", self.api_key_edit)
        layout.addRow("Base URL", self.base_url_edit)
        layout.addRow(tr("model"), self.model_combo)
        refresh = QPushButton(tr("models.refresh"))
        refresh.clicked.connect(self._queue_model_discovery)
        layout.addRow(refresh)
        layout.addRow(self.model_status)
        test_button = QPushButton(tr("test.connection"))
        test_button.clicked.connect(self._test_connection)
        layout.addRow(test_button)

        return page

    def _prompt_page(self) -> QWidget:
        page, layout = self._form_page(tr("prompt"))
        layout.addRow(tr("current.template"), QLabel(self.original_settings.prompt_preset))
        open_button = QPushButton(tr("open.prompt.studio"))
        open_button.clicked.connect(self._open_prompt_studio)
        layout.addRow(open_button)

        return page

    def _export_page(self) -> QWidget:
        page, layout = self._form_page(tr("export"))
        layout.addRow("SQLite", self.sqlite_check)
        layout.addRow("Markdown", self.markdown_check)
        layout.addRow("Word", self.word_check)
        layout.addRow(tr("export.premiere"), self.premiere_check)
        layout.addRow(tr("field.note"), QLabel(tr("field.note.text")))
        layout.addRow(tr("sqlite.path"), self._path_row(self.sqlite_edit, False))
        layout.addRow(tr("output.dir"), self._path_row(self.output_edit, True))
        layout.addRow(tr("body.mode"), self._body_mode_row())
        layout.addRow(tr("export.fields"), self._checks_grid(
            [
                self.include_title_check,
                self.include_source_check,
                self.include_published_check,
                self.include_link_check,
                self.include_importance_check,
                self.include_keywords_check,
                self.include_summary_check,
                self.include_body_check,
                self.include_original_check,
            ]
        ))

        return page

    def _network_page(self) -> QWidget:
        page, layout = self._form_page(tr("network"))
        layout.addRow(tr('search.provider'), self.search_provider_combo)
        layout.addRow(tr('search.api_key'), self.search_api_key_edit)
        hint = QLabel(tr('search.provider.hint'))
        hint.setWordWrap(True)
        layout.addRow(hint)
        layout.addRow("User-Agent", self.user_agent_edit)
        layout.addRow(tr("request.timeout"), self.timeout_spin)
        layout.addRow(tr("connect.timeout"), self.connect_timeout_spin)
        layout.addRow(tr("read.timeout"), self.read_timeout_spin)
        layout.addRow(tr("retry.count"), self.retry_count_spin)

        return page

    def _launch_page(self) -> QWidget:
        page, layout = self._form_page(tr("launch.settings"))
        layout.addRow(tr("launch.range"), self.launch_range_selector)
        layout.addRow(tr("date.preset"), self.launch_range_combo)
        layout.addRow(tr("start.date"), self.launch_start_date_edit)
        layout.addRow(tr("end.date"), self.launch_end_date_edit)
        layout.addRow(tr("display.timezone"), self.display_timezone_combo)
        layout.addRow(self.aerospace_translation_check)
        layout.addRow(
            tr("aerospace.translation.mode"),
            self.aerospace_translation_mode_combo,
        )
        edit_button = QPushButton(tr("terms.edit"))
        edit_button.clicked.connect(self._open_term_editor)
        layout.addRow(edit_button)

        return page

    def _news_page(self) -> QWidget:
        page, layout = self._form_page(tr("news.settings"))
        layout.addRow(tr("date.filter"), self.news_range_selector)
        layout.addRow(tr("date.preset"), self.date_mode_combo)
        layout.addRow(tr("start.date"), self.start_date_edit)
        layout.addRow(tr("end.date"), self.end_date_edit)
        layout.addRow(self.news_date_check)
        hint = QLabel(tr("news.event_date_check.hint"))
        hint.setWordWrap(True)
        layout.addRow(hint)
        return page

    def _advanced_page(self) -> QWidget:
        page, layout = self._form_page(tr("advanced"))
        layout.addRow(tr("advanced.params"), self._advanced_ai_row())
        layout.addRow(self.release_mode_check)
        layout.addRow(self.debug_ai_check)

        return page

    def _about_page(self) -> QWidget:
        page, layout = self._form_page(tr("about"))
        layout.addRow(
            tr("version"),
            QLabel(f"v{app_version()} Professional Edition"),
        )
        github_label = QLabel(
        '<a href="https://github.com/Starship30/SpaceWeekly/">https://github.com/Starship30/SpaceWeekly/</a>'
        )
        github_label.setOpenExternalLinks(True)

        layout.addRow(tr("github.link"), github_label)
        layout.addRow(tr("python.version"), QLabel(sys.version.split()[0]))
        layout.addRow(tr("qt.version"), QLabel(qVersion()))
        layout.addRow(tr("author"), QLabel("RMS-TITANIC"))

        return page

    def _form_page(self, title: str) -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        wrapper = QVBoxLayout(page)
        wrapper.setContentsMargins(0, 0, 0, 0)
        card = QWidget()
        card.setObjectName("SettingsCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 26)
        card_layout.setSpacing(24)
        heading = QLabel(title)
        heading.setObjectName("PanelTitle")
        card_layout.addWidget(heading)
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.setHorizontalSpacing(22)
        layout.setVerticalSpacing(18)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        card_layout.addLayout(layout)
        wrapper.addWidget(card)
        wrapper.addStretch(1)
        return page, layout

    def _advanced_ai_row(self) -> QWidget:
        widget = QWidget()
        widget.setProperty("formGroup", True)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Temperature"))
        layout.addWidget(self.temperature_spin)

        return widget

    def _body_mode_row(self) -> QWidget:
        widget = QWidget()
        widget.setProperty("formGroup", True)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        for label, mode in [
            (tr("body.original"), "original"),
            (tr("body.none"), "none"),
        ]:
            button = QRadioButton(label)
            button.setProperty("mode", mode)
            button.setChecked(self.original_settings.body_mode == mode)
            self.body_mode_group.addButton(button)
            layout.addWidget(button)

        if not self.body_mode_group.checkedButton():
            self.body_mode_group.buttons()[0].setChecked(True)

        return widget

    def _checks_grid(self, checks: list[QCheckBox]) -> QWidget:
        widget = QWidget()
        widget.setProperty("formGroup", True)
        layout = QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)

        for index, check in enumerate(checks):
            layout.addWidget(check, index // 3, index % 3)

        return widget

    def _path_row(self, edit: QLineEdit, directory: bool) -> QWidget:
        widget = QWidget()
        widget.setProperty("formGroup", True)
        button = QPushButton(tr("select"))
        button.clicked.connect(lambda: self._select_path(edit, directory))
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)

        return widget

    def _range_selector(self, mode: str, combo: QComboBox, start: QDateEdit, end: QDateEdit):
        widget = QWidget()
        widget.setProperty("formGroup", True)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        preset = QRadioButton(tr("date.preset"))
        custom = QRadioButton(tr("date.custom"))
        preset.setProperty("rangeOption", True)
        custom.setProperty("rangeOption", True)
        layout.setSpacing(6)
        layout.addWidget(preset)
        layout.addWidget(custom)

        def update(custom_selected: bool) -> None:
            combo.setEnabled(not custom_selected)
            start.setEnabled(custom_selected)
            end.setEnabled(custom_selected)

        custom.toggled.connect(update)
        custom.setChecked(mode == "custom")
        preset.setChecked(mode != "custom")
        update(mode == "custom")
        return widget, custom

    def _date_mode_combo(self, current: str) -> QComboBox:
        combo = QComboBox()

        for label, mode in NEWS_DATE_RANGES:
            combo.addItem(tr(label), mode)

        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _theme_mode_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("follow.system"), THEME_SYSTEM)
        combo.addItem(tr("light.mode"), THEME_LIGHT)
        combo.addItem(tr("dark.mode"), THEME_DARK)
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _language_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("chinese"), "zh_CN")
        combo.addItem(tr("english"), "en_US")
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _launch_range_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        for label, mode in LAUNCH_DATE_RANGES:
            combo.addItem(tr(label), mode)
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _aerospace_translation_mode_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("translation.mode.dictionary"), "dictionary")
        combo.addItem(tr("translation.mode.ai"), "ai")
        combo.addItem(tr("translation.mode.hybrid"), "hybrid")
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _display_timezone_combo(self, current: str) -> QComboBox:
        combo = QComboBox()

        for label, timezone_name in [
            (tr("timezone.system"), "system"),
            (tr("timezone.utc"), "UTC"),
            (tr("timezone.beijing"), "Asia/Shanghai"),
            (tr("timezone.tokyo"), "Asia/Tokyo"),
            (tr("timezone.london"), "Europe/London"),
            (tr("timezone.new.york"), "America/New_York"),
        ]:
            combo.addItem(label, timezone_name)

        combo.setCurrentIndex(max(combo.findData(current), 0))
        return combo

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)

        return spin

    def _check(self, label: str, checked: bool) -> QCheckBox:
        check = QCheckBox(label)
        check.setChecked(checked)

        return check

    def _select_path(self, edit: QLineEdit, directory: bool) -> None:
        if directory:
            path = QFileDialog.getExistingDirectory(self, tr("select.folder"))
        else:
            path, _ = QFileDialog.getSaveFileName(self, tr("select.sqlite"))

        if path:
            edit.setText(path)

    def _open_prompt_studio(self) -> None:
        dialog = PromptStudioDialog(self.settings(), self)

        if dialog.exec():
            self.original_settings = dialog.settings()

    def _open_term_editor(self) -> None:
        TermEditorDialog(self.settings(), self).exec()

    def _reset_workspace(self) -> None:
        dialog = FirstRunWizard(self)

        if dialog.exec():
            self.original_settings = load_settings()
            self.sqlite_edit.setText(self.original_settings.sqlite_path)
            self.output_edit.setText(self.original_settings.output_dir)
            QMessageBox.information(self, "SpaceWeekly", tr("workspace.updated"))

    def _provider_changed(self) -> None:
        self._provider_values[self._current_provider] = (
            self.api_key_edit.text(), self.base_url_edit.text(),
            self.model_combo.currentText(),
            [self.model_combo.itemText(i) for i in range(self.model_combo.count())],
        )
        provider = str(self.provider_combo.currentData())
        key, url, model, models = self._provider_values.get(provider, ("", PROVIDER_URLS.get(provider, ""), "", []))
        self._current_provider = provider
        self.api_key_edit.setText(key)
        self.base_url_edit.setText(url)
        self.base_url_edit.setReadOnly(provider != "Custom")
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(model)
        self._model_request = None
        self._queue_model_discovery()

    def _queue_model_discovery(self) -> None:
        self._model_request = None
        self.model_status.setText(tr("models.loading") if self.api_key_edit.text().strip() else tr("models.key.required"))
        self._model_timer.start()

    def _discover_models(self) -> None:
        if not self.api_key_edit.text().strip() or not self.base_url_edit.text().strip():
            self.model_status.setText(tr("models.key.required"))
            return
        config = provider_config_from_settings(self.settings())
        self._model_request = config
        self.model_status.setText(tr("models.loading"))
        # QApplication owns in-flight workers so closing the dialog cannot destroy a running thread.
        worker = ModelWorker(config, QApplication.instance())
        QApplication.instance().aboutToQuit.connect(worker.wait)
        worker.result_ready.connect(self._models_discovered)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _models_discovered(self, config, models, error: str) -> None:
        if config != self._model_request or config != provider_config_from_settings(self.settings()):
            return
        if error:
            self.model_status.setText(tr("models.failed", error=error))
            return
        if not models:
            self.model_status.setText(tr("models.manual"))
            return
        selected = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(list(dict.fromkeys(models)))
        if selected in models:
            self.model_combo.setCurrentText(selected)
        self.model_status.setText(tr("models.found", count=len(models)))

    def _test_connection(self) -> None:
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, tr("test.connection"), tr("missing.api.key"))
            return

        if not self.base_url_edit.text().strip():
            QMessageBox.warning(self, tr("test.connection"), tr("missing.model.config"))
            return

        settings = self.settings()

        try:
            result = test_connection(
                provider_config_from_settings(settings),
                settings.model_candidates,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("test.connection"),
                f"{settings.ai_provider}{tr('connection.test.title')}\n\n"
                f"✗ {exc}",
            )
            return

        self.model_combo.clear()
        self.model_combo.addItems(result.available_models or [result.model])
        self.model_combo.setCurrentText(result.model)
        models_source = (
            tr("connection.models.api")
            if result.models_api_supported
            else tr("connection.models.config")
        )
        QMessageBox.information(
            self,
            tr("test.connection"),
            (
                f"{settings.ai_provider}{tr('connection.test.title')}\n\n"
                f"✓ {tr('connection.api.key.valid')}\n"
                f"✓ {tr('connection.endpoint.ok')}\n"
                f"✓ {tr('connection.model.available')}\n\n"
                f"{tr('connection.current.model')}{tr('label_separator')}{result.model}\n"
                f"{tr('connection.model.source')}{tr('label_separator')}{models_source}\n"
                f"{tr('connection.response.time')}{tr('label_separator')}{result.elapsed_ms} ms"
            ),
        )
