import sys
from dataclasses import replace

from PySide6.QtCore import QDate
from PySide6.QtCore import qVersion
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
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
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QStackedWidget
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from config import APP_VERSION
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


AI_CATEGORY_OPTIONS = [
    ("astronomy", "category.astronomy", "天文"),
    ("spaceflight", "category.spaceflight", "航天"),
    ("humanities", "category.humanities", "人文"),
]


class SettingsDialog(QDialog):
    """Sectioned settings dialog for user and advanced configuration."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.original_settings = settings
        self.setWindowTitle(tr("settings"))
        self.resize(900, 660)
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
            deepseek_model=self.model_edit.text().strip(),
            ai_provider=self.provider_combo.currentText(),
            temperature=self.temperature_spin.value(),
            max_tokens=self.max_tokens_spin.value(),
            user_agent=self.user_agent_edit.text().strip(),
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
            include_title=self.include_title_check.isChecked(),
            include_source=self.include_source_check.isChecked(),
            include_published=self.include_published_check.isChecked(),
            include_link=self.include_link_check.isChecked(),
            include_score=self.include_importance_check.isChecked(),
            include_summary=self.include_summary_check.isChecked(),
            include_translation=self.include_translation_check.isChecked(),
            include_categories=self.include_categories_check.isChecked(),
            include_keywords=self.include_keywords_check.isChecked(),
            include_body=self.include_body_check.isChecked(),
            include_original=self.include_original_check.isChecked(),
            include_chinese=self.include_chinese_check.isChecked(),
            body_mode=str(body_mode),
            ai_summary_enabled=self.ai_summary_check.isChecked(),
            ai_translation_enabled=self.ai_translation_check.isChecked(),
            ai_keywords_enabled=self.ai_keywords_check.isChecked(),
            ai_category_enabled=self.ai_category_check.isChecked(),
            ai_importance_enabled=True,
            ai_auto_filter_enabled=self.auto_filter_check.isChecked(),
            ai_category_options=self._selected_category_options(),
            pipeline_score_enabled=True,
            pipeline_filter_enabled=self.pipeline_filter_check.isChecked(),
            pipeline_category_enabled=self.ai_category_check.isChecked(),
            pipeline_keywords_enabled=self.ai_keywords_check.isChecked(),
            pipeline_summary_enabled=self.ai_summary_check.isChecked(),
            pipeline_translation_enabled=self.ai_translation_check.isChecked(),
            max_article_tokens=self.max_article_tokens_spin.value(),
            max_daily_api_calls=self.max_daily_calls_spin.value(),
            ai_limit_action=str(self.limit_action_combo.currentData()),
            daily_token_limit=self.daily_token_limit_spin.value(),
            token_limit_action=str(self.token_action_combo.currentData()),
            theme_mode=str(self.theme_mode_combo.currentData()),
            language=str(self.language_combo.currentData()),
            date_mode=str(self.date_mode_combo.currentData()),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
            min_news_score=self.min_score_spin.value(),
            report_style_custom_high=self.custom_high_check.isChecked(),
            report_style_custom_medium=self.custom_medium_check.isChecked(),
            report_style_custom_low=self.custom_low_check.isChecked(),
            release_mode=self.release_mode_check.isChecked(),
            launch_range=str(self.launch_range_combo.currentData()),
            aerospace_translation_enabled=self.aerospace_translation_check.isChecked(),
            aerospace_translation_mode=str(self.aerospace_translation_mode_combo.currentData()),
        )

    def _set_icon(self) -> None:
        icon_path = resource_path("assets", "sspo.ico")

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _create_fields(self, settings: AppSettings) -> None:
        self.api_key_edit = QLineEdit(settings.deepseek_api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(
            ["DeepSeek", "OpenAI", "Gemini", "Claude", "OpenRouter", "SiliconFlow"]
        )
        self.provider_combo.setCurrentText(settings.ai_provider)
        self.base_url_edit = QLineEdit(settings.deepseek_base_url)
        self.model_edit = QLineEdit(settings.deepseek_model)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0, 2)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(settings.temperature)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 128000)
        self.max_tokens_spin.setValue(settings.max_tokens)
        self.timeout_spin = self._spin(1, 300, settings.request_timeout)
        self.connect_timeout_spin = self._spin(1, 120, settings.connect_timeout)
        self.read_timeout_spin = self._spin(1, 300, settings.read_timeout)
        self.retry_count_spin = self._spin(0, 10, max(settings.retry_count, 3))
        self.debug_ai_check = self._check(tr("show.ai.debug"), settings.debug_ai_enabled)
        self.release_mode_check = self._check(tr("release.mode"), settings.release_mode)
        self.user_agent_edit = QLineEdit(settings.user_agent)
        self.sqlite_edit = QLineEdit(settings.sqlite_path)
        self.output_edit = QLineEdit(settings.output_dir)
        self.sqlite_check = self._check("", settings.export_sqlite)
        self.markdown_check = self._check("", settings.export_markdown)
        self.word_check = self._check("", settings.export_word)
        self.include_title_check = self._check(tr("include.title"), settings.include_title)
        self.include_source_check = self._check(tr("include.source"), settings.include_source)
        self.include_published_check = self._check(tr("include.published"), settings.include_published)
        self.include_link_check = self._check(tr("include.link"), settings.include_link)
        self.include_importance_check = self._check(tr("include.score"), settings.include_score)
        self.include_summary_check = self._check(tr("include.summary"), settings.include_summary)
        self.include_translation_check = self._check(tr("include.translation"), settings.include_translation)
        self.include_categories_check = self._check(tr("include.categories"), settings.include_categories)
        self.include_keywords_check = self._check(tr("include.keywords"), settings.include_keywords)
        self.include_body_check = self._check(tr("include.body"), settings.include_body)
        self.include_original_check = self._check(tr("include.original"), settings.include_original)
        self.include_chinese_check = self._check(tr("include.chinese"), settings.include_chinese)
        self.ai_summary_check = self._check(tr("ai.summary.generate"), settings.pipeline_summary_enabled)
        self.ai_translation_check = self._check(tr("ai.translation.generate"), settings.pipeline_translation_enabled)
        self.ai_keywords_check = self._check(tr("ai.keywords.extract"), settings.pipeline_keywords_enabled)
        self.ai_category_check = self._check(tr("ai.category.generate"), settings.pipeline_category_enabled)
        self.pipeline_filter_check = self._check(tr("ai.filter.editor"), settings.pipeline_filter_enabled)
        self.auto_filter_check = self._check(tr("ai.auto.filter"), settings.ai_auto_filter_enabled)
        self.category_option_checks = [
            self._category_option_check(value, key, legacy, settings.ai_category_options)
            for value, key, legacy in AI_CATEGORY_OPTIONS
        ]

        if not any(check.isChecked() for check in self.category_option_checks):
            for check in self.category_option_checks:
                check.setChecked(True)

        self.max_article_tokens_spin = self._spin(500, 100000, settings.max_article_tokens)
        self.max_daily_calls_spin = self._spin(1, 10000, settings.max_daily_api_calls)
        self.daily_token_limit_spin = self._spin(1000, 10000000, settings.daily_token_limit)
        self.min_score_spin = self._spin(0, 100, settings.min_news_score)
        self.theme_mode_combo = self._theme_mode_combo(settings.theme_mode)
        self.language_combo = self._language_combo(settings.language)
        self.limit_action_combo = self._action_combo(settings.ai_limit_action)
        self.token_action_combo = self._action_combo(settings.token_limit_action)
        self.date_mode_combo = self._date_mode_combo(settings.date_mode)
        self.start_date_edit = self._date_edit(settings.start_date, -6)
        self.end_date_edit = self._date_edit(settings.end_date, 0)
        self.custom_high_check = self._check(tr("high"), settings.report_style_custom_high)
        self.custom_medium_check = self._check(tr("medium"), settings.report_style_custom_medium)
        self.custom_low_check = self._check(tr("low"), settings.report_style_custom_low)
        self.body_mode_group = QButtonGroup(self)
        self.launch_range_combo = self._launch_range_combo(settings.launch_range)
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
        content = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.stack = QStackedWidget()

        for title, page in [
            (tr("appearance"), self._appearance_page()),
            (tr("ai"), self._ai_page()),
            (tr("prompt"), self._prompt_page()),
            (tr("export"), self._export_page()),
            (tr("launch.settings"), self._launch_page()),
            (tr("network"), self._network_page()),
            (tr("cost.control"), self._cost_page()),
            (tr("advanced"), self._advanced_page()),
            (tr("about"), self._about_page()),
        ]:
            self.nav.addItem(title)
            self.stack.addWidget(page)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        content.addWidget(self.nav)
        content.addWidget(self.stack, 1)
        main.addLayout(content, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
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
        layout.addRow(tr("model"), self.model_edit)
        layout.addRow(tr("category.direction"), self._checks_grid(self.category_option_checks))
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
                self.include_categories_check,
                self.include_keywords_check,
                self.include_summary_check,
                self.include_translation_check,
                self.include_body_check,
                self.include_original_check,
                self.include_chinese_check,
            ]
        ))

        return page

    def _network_page(self) -> QWidget:
        page, layout = self._form_page(tr("network"))
        layout.addRow("User-Agent", self.user_agent_edit)
        layout.addRow(tr("request.timeout"), self.timeout_spin)
        layout.addRow(tr("connect.timeout"), self.connect_timeout_spin)
        layout.addRow(tr("read.timeout"), self.read_timeout_spin)
        layout.addRow(tr("retry.count"), self.retry_count_spin)

        return page

    def _launch_page(self) -> QWidget:
        page, layout = self._form_page(tr("launch.settings"))
        layout.addRow(tr("launch.range"), self.launch_range_combo)
        layout.addRow(self.aerospace_translation_check)
        layout.addRow(
            tr("aerospace.translation.mode"),
            self.aerospace_translation_mode_combo,
        )
        edit_button = QPushButton(tr("terms.edit"))
        edit_button.clicked.connect(self._open_term_editor)
        layout.addRow(edit_button)

        return page

    def _cost_page(self) -> QWidget:
        page, layout = self._form_page(tr("cost.control"))
        layout.addRow(tr("token.note"), QLabel(tr("token.note.text")))
        layout.addRow(tr("article.input.tokens"), self.max_article_tokens_spin)
        layout.addRow(tr("daily.api.calls"), self.max_daily_calls_spin)
        layout.addRow(tr("daily.token.budget"), self.daily_token_limit_spin)
        layout.addRow(tr("api.limit.action"), self.limit_action_combo)
        layout.addRow(tr("token.limit.action"), self.token_action_combo)

        return page

    def _advanced_page(self) -> QWidget:
        page, layout = self._form_page(tr("advanced"))
        layout.addRow(tr("advanced.params"), self._advanced_ai_row())
        layout.addRow(tr("ai.workflow"), self._workflow_row())
        layout.addRow(tr("date.filter"), self.date_mode_combo)
        layout.addRow(tr("start.date"), self.start_date_edit)
        layout.addRow(tr("end.date"), self.end_date_edit)
        layout.addRow(tr("custom.report.style"), self._checks_grid(
            [self.custom_high_check, self.custom_medium_check, self.custom_low_check]
        ))
        layout.addRow(tr("score.threshold"), self.min_score_spin)
        layout.addRow(self.release_mode_check)
        layout.addRow(self.debug_ai_check)

        return page

    def _about_page(self) -> QWidget:
        page, layout = self._form_page(tr("about"))
        layout.addRow(
            tr("version"),
            QLabel(f"v{APP_VERSION} Professional Edition"),
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
        heading = QLabel(title)
        heading.setObjectName("PanelTitle")
        wrapper.addWidget(heading)
        layout = QFormLayout()
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(12)
        wrapper.addLayout(layout)
        wrapper.addStretch(1)

        return page, layout

    def _advanced_ai_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        for label, control in [
            ("Temperature", self.temperature_spin),
            (tr("ai.output.tokens"), self.max_tokens_spin),
        ]:
            layout.addWidget(QLabel(label))
            layout.addWidget(control)

        return widget

    def _workflow_row(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(tr("workflow.note")))
        layout.addWidget(self._checks_grid(
            [
                self.pipeline_filter_check,
                self.ai_category_check,
                self.ai_keywords_check,
                self.ai_summary_check,
                self.ai_translation_check,
                self.auto_filter_check,
            ]
        ))

        return widget

    def _body_mode_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        for label, mode in [
            (tr("body.original"), "original"),
            (tr("body.bilingual"), "bilingual"),
            (tr("body.translated"), "translated"),
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

    def _selected_category_options(self) -> list[str]:
        selected = [
            str(check.property("value"))
            for check in self.category_option_checks
            if check.isChecked()
        ]

        return selected or [value for value, _key, _legacy in AI_CATEGORY_OPTIONS]

    def _category_option_check(
        self,
        value: str,
        label_key: str,
        legacy_value: str,
        selected_values: list[str],
    ) -> QCheckBox:
        selected = value in selected_values or legacy_value in selected_values
        check = self._check(tr(label_key), selected)
        check.setProperty("value", value)

        return check

    def _checks_grid(self, checks: list[QCheckBox]) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)

        for index, check in enumerate(checks):
            layout.addWidget(check, index // 3, index % 3)

        return widget

    def _path_row(self, edit: QLineEdit, directory: bool) -> QWidget:
        widget = QWidget()
        button = QPushButton(tr("select"))
        button.clicked.connect(lambda: self._select_path(edit, directory))
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)

        return widget

    def _action_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("auto.stop"), "stop")
        combo.addItem(tr("skip"), "skip")
        combo.addItem(tr("ask.user"), "ask")
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _date_mode_combo(self, current: str) -> QComboBox:
        combo = QComboBox()

        for label, mode in [
            (tr("today"), "today"),
            (tr("last3"), "last_3_days"),
            (tr("last7"), "last_7_days"),
            (tr("last30"), "last_30_days"),
            (tr("custom"), "custom"),
        ]:
            combo.addItem(label, mode)

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
        combo.addItem(tr("launch.range.past"), "past_week")
        combo.addItem(tr("launch.range.next"), "next_week")
        combo.addItem(tr("disabled"), "disabled")
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _aerospace_translation_mode_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("translation.mode.dictionary"), "dictionary")
        combo.addItem(tr("translation.mode.ai"), "ai")
        combo.addItem(tr("translation.mode.hybrid"), "hybrid")
        combo.setCurrentIndex(max(combo.findData(current), 0))

        return combo

    def _date_edit(self, value: str, fallback_days: int) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        parsed = QDate.fromString(value, "yyyy-MM-dd")
        edit.setDate(parsed if parsed.isValid() else QDate.currentDate().addDays(fallback_days))

        return edit

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

    def _test_connection(self) -> None:
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, tr("test.connection"), tr("missing.api.key"))
            return

        if not self.base_url_edit.text().strip() or not self.model_edit.text().strip():
            QMessageBox.warning(self, tr("test.connection"), tr("missing.model.config"))
            return

        QMessageBox.information(self, tr("test.connection"), tr("connection.ready"))
