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

from i18n import tr
from resources import resource_path
from services.settings import AppSettings
from ui.prompt_studio_dialog import PromptStudioDialog


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
            date_mode=str(self.date_mode_combo.currentData()),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
            min_news_score=self.min_score_spin.value(),
            report_style_custom_high=self.custom_high_check.isChecked(),
            report_style_custom_medium=self.custom_medium_check.isChecked(),
            report_style_custom_low=self.custom_low_check.isChecked(),
            release_mode=self.release_mode_check.isChecked(),
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
        self.debug_ai_check = self._check("显示 AI 调试信息", settings.debug_ai_enabled)
        self.release_mode_check = self._check("普通用户模式", settings.release_mode)
        self.user_agent_edit = QLineEdit(settings.user_agent)
        self.sqlite_edit = QLineEdit(settings.sqlite_path)
        self.output_edit = QLineEdit(settings.output_dir)
        self.sqlite_check = self._check("", settings.export_sqlite)
        self.markdown_check = self._check("", settings.export_markdown)
        self.word_check = self._check("", settings.export_word)
        self.include_title_check = self._check("标题", settings.include_title)
        self.include_source_check = self._check("来源", settings.include_source)
        self.include_published_check = self._check("发布时间", settings.include_published)
        self.include_link_check = self._check("链接", settings.include_link)
        self.include_importance_check = self._check("重要程度与原因", settings.include_score)
        self.include_summary_check = self._check("摘要", settings.include_summary)
        self.include_translation_check = self._check("翻译", settings.include_translation)
        self.include_categories_check = self._check("分类", settings.include_categories)
        self.include_keywords_check = self._check("关键词", settings.include_keywords)
        self.include_body_check = self._check("正文", settings.include_body)
        self.include_original_check = self._check("原文", settings.include_original)
        self.include_chinese_check = self._check("中文", settings.include_chinese)
        self.ai_summary_check = self._check("摘要", settings.pipeline_summary_enabled)
        self.ai_translation_check = self._check("翻译", settings.pipeline_translation_enabled)
        self.ai_keywords_check = self._check("关键词", settings.pipeline_keywords_enabled)
        self.ai_category_check = self._check("分类", settings.pipeline_category_enabled)
        self.pipeline_filter_check = self._check("编辑策略", settings.pipeline_filter_enabled)
        self.auto_filter_check = self._check("自动筛选", settings.ai_auto_filter_enabled)
        self.max_article_tokens_spin = self._spin(500, 100000, settings.max_article_tokens)
        self.max_daily_calls_spin = self._spin(1, 10000, settings.max_daily_api_calls)
        self.daily_token_limit_spin = self._spin(1000, 10000000, settings.daily_token_limit)
        self.min_score_spin = self._spin(0, 100, settings.min_news_score)
        self.limit_action_combo = self._action_combo(settings.ai_limit_action)
        self.token_action_combo = self._action_combo(settings.token_limit_action)
        self.date_mode_combo = self._date_mode_combo(settings.date_mode)
        self.start_date_edit = self._date_edit(settings.start_date, -6)
        self.end_date_edit = self._date_edit(settings.end_date, 0)
        self.custom_high_check = self._check("High", settings.report_style_custom_high)
        self.custom_medium_check = self._check("Medium", settings.report_style_custom_medium)
        self.custom_low_check = self._check("Low", settings.report_style_custom_low)
        self.body_mode_group = QButtonGroup(self)

    def _build_layout(self) -> None:
        main = QVBoxLayout(self)
        content = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.stack = QStackedWidget()

        for title, page in [
            ("AI", self._ai_page()),
            ("Prompt", self._prompt_page()),
            ("导出", self._export_page()),
            ("网络", self._network_page()),
            ("成本控制", self._cost_page()),
            ("高级", self._advanced_page()),
            ("关于", self._about_page()),
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

    def _ai_page(self) -> QWidget:
        page, layout = self._form_page("AI")
        layout.addRow("Provider", self.provider_combo)
        layout.addRow("API Key", self.api_key_edit)
        layout.addRow("Base URL", self.base_url_edit)
        layout.addRow("Model", self.model_edit)
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._test_connection)
        layout.addRow(test_button)

        return page

    def _prompt_page(self) -> QWidget:
        page, layout = self._form_page("Prompt")
        layout.addRow("当前模板", QLabel(self.original_settings.prompt_preset))
        open_button = QPushButton("打开 Prompt Studio")
        open_button.clicked.connect(self._open_prompt_studio)
        layout.addRow(open_button)

        return page

    def _export_page(self) -> QWidget:
        page, layout = self._form_page("导出")
        layout.addRow("SQLite", self.sqlite_check)
        layout.addRow("Markdown", self.markdown_check)
        layout.addRow("Word", self.word_check)
        layout.addRow("SQLite 路径", self._path_row(self.sqlite_edit, False))
        layout.addRow("导出目录", self._path_row(self.output_edit, True))
        layout.addRow("正文模式", self._body_mode_row())
        layout.addRow("导出字段", self._checks_grid(
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
        page, layout = self._form_page("网络")
        layout.addRow("User-Agent", self.user_agent_edit)
        layout.addRow("请求超时", self.timeout_spin)
        layout.addRow("连接超时", self.connect_timeout_spin)
        layout.addRow("读取超时", self.read_timeout_spin)
        layout.addRow("重试次数", self.retry_count_spin)

        return page

    def _cost_page(self) -> QWidget:
        page, layout = self._form_page("成本控制")
        layout.addRow("每篇新闻最大 Token", self.max_article_tokens_spin)
        layout.addRow("每天最大 API 调用次数", self.max_daily_calls_spin)
        layout.addRow("每日 Token 限额", self.daily_token_limit_spin)
        layout.addRow("调用超限处理", self.limit_action_combo)
        layout.addRow("Token 超限处理", self.token_action_combo)

        return page

    def _advanced_page(self) -> QWidget:
        page, layout = self._form_page("高级")
        layout.addRow("高级参数", self._advanced_ai_row())
        layout.addRow("AI Workflow", self._workflow_row())
        layout.addRow("时间筛选", self.date_mode_combo)
        layout.addRow("开始日期", self.start_date_edit)
        layout.addRow("结束日期", self.end_date_edit)
        layout.addRow("自定义周报风格", self._checks_grid(
            [self.custom_high_check, self.custom_medium_check, self.custom_low_check]
        ))
        layout.addRow("兼容评分阈值", self.min_score_spin)
        layout.addRow(self.release_mode_check)
        layout.addRow(self.debug_ai_check)

        return page

    def _about_page(self) -> QWidget:
        page, layout = self._form_page("关于")
        layout.addRow("Version", QLabel("v2.1 Professional Edition"))
        layout.addRow("GitHub", QLabel("https://github.com/"))
        layout.addRow("License", QLabel("See project license"))
        layout.addRow("Python Version", QLabel(sys.version.split()[0]))
        layout.addRow("Qt Version", QLabel(qVersion()))
        layout.addRow("作者", QLabel("SpaceWeekly contributors"))

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
            ("Max Tokens", self.max_tokens_spin),
        ]:
            layout.addWidget(QLabel(label))
            layout.addWidget(control)

        return widget

    def _workflow_row(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("RSS → Parser → 重要程度 → 编辑策略 → 分类 → 摘要 → 翻译 → 导出"))
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
            ("不导出正文", "none"),
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
        layout = QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)

        for index, check in enumerate(checks):
            layout.addWidget(check, index // 3, index % 3)

        return widget

    def _path_row(self, edit: QLineEdit, directory: bool) -> QWidget:
        widget = QWidget()
        button = QPushButton("选择")
        button.clicked.connect(lambda: self._select_path(edit, directory))
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)

        return widget

    def _action_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("自动停止", "stop")
        combo.addItem("跳过", "skip")
        combo.addItem("询问用户", "ask")
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
            path = QFileDialog.getExistingDirectory(self, "选择目录")
        else:
            path, _ = QFileDialog.getSaveFileName(self, "选择 SQLite 文件")

        if path:
            edit.setText(path)

    def _open_prompt_studio(self) -> None:
        dialog = PromptStudioDialog(self.settings(), self)

        if dialog.exec():
            self.original_settings = dialog.settings()

    def _test_connection(self) -> None:
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "测试连接", "请先填写 API Key。")
            return

        if not self.base_url_edit.text().strip() or not self.model_edit.text().strip():
            QMessageBox.warning(self, "测试连接", "请填写 Base URL 和 Model。")
            return

        QMessageBox.information(self, "测试连接", "配置已填写，可用于生成时连接。")
