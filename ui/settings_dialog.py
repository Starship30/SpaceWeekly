from dataclasses import replace

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QDoubleSpinBox
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QGroupBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from i18n import tr
from resources import resource_path
from services.settings import AppSettings


class SettingsDialog(QDialog):
    """Dialog for editing config.json user settings."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.original_settings = settings
        self.setWindowTitle(tr("settings"))
        self.resize(720, 620)
        self._set_icon()
        self._create_fields(settings)
        self._build_layout()

    def settings(self) -> AppSettings:
        """Return edited settings."""
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
            sqlite_path=self.sqlite_edit.text().strip(),
            output_dir=self.output_edit.text().strip(),
            export_markdown=self.markdown_check.isChecked(),
            export_word=self.word_check.isChecked(),
            ai_summary_enabled=self.ai_summary_check.isChecked(),
            ai_translation_enabled=self.ai_translation_check.isChecked(),
            max_article_tokens=self.max_article_tokens_spin.value(),
            max_daily_api_calls=self.max_daily_calls_spin.value(),
            ai_limit_action=self.limit_action_combo.currentData(),
            summary_prompt=self.summary_prompt_edit.toPlainText().strip(),
            translation_prompt=self.translation_prompt_edit.toPlainText().strip(),
            category_prompt=self.category_prompt_edit.toPlainText().strip(),
            score_prompt=self.score_prompt_edit.toPlainText().strip(),
            filter_prompt=self.filter_prompt_edit.toPlainText().strip(),
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
        self.user_agent_edit = QLineEdit(settings.user_agent)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(settings.request_timeout)
        self.sqlite_edit = QLineEdit(settings.sqlite_path)
        self.output_edit = QLineEdit(settings.output_dir)
        self.markdown_check = QCheckBox()
        self.markdown_check.setChecked(settings.export_markdown)
        self.word_check = QCheckBox()
        self.word_check.setChecked(settings.export_word)
        self.ai_summary_check = QCheckBox("启用 AI 摘要")
        self.ai_summary_check.setChecked(settings.ai_summary_enabled)
        self.ai_translation_check = QCheckBox("启用 AI 翻译")
        self.ai_translation_check.setChecked(settings.ai_translation_enabled)
        self.max_article_tokens_spin = QSpinBox()
        self.max_article_tokens_spin.setRange(500, 100000)
        self.max_article_tokens_spin.setValue(settings.max_article_tokens)
        self.max_daily_calls_spin = QSpinBox()
        self.max_daily_calls_spin.setRange(1, 10000)
        self.max_daily_calls_spin.setValue(settings.max_daily_api_calls)
        self.limit_action_combo = QComboBox()
        self.limit_action_combo.addItem("自动停止", "stop")
        self.limit_action_combo.addItem("跳过", "skip")
        self.limit_action_combo.addItem("询问用户", "ask")
        self.limit_action_combo.setCurrentIndex(
            max(self.limit_action_combo.findData(settings.ai_limit_action), 0)
        )
        self.summary_prompt_edit = QTextEdit(settings.summary_prompt)
        self.translation_prompt_edit = QTextEdit(settings.translation_prompt)
        self.category_prompt_edit = QTextEdit(settings.category_prompt)
        self.score_prompt_edit = QTextEdit(settings.score_prompt)
        self.filter_prompt_edit = QTextEdit(settings.filter_prompt)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._ai_config_box())
        layout.addWidget(self._prompt_box())
        layout.addWidget(self._ai_cost_box())
        layout.addWidget(self._path_box())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _ai_config_box(self) -> QGroupBox:
        box = QGroupBox("AI 设置")
        layout = QFormLayout(box)
        layout.addRow("Provider", self.provider_combo)
        layout.addRow("DeepSeek API Key", self.api_key_edit)
        layout.addRow("Base URL", self.base_url_edit)
        layout.addRow("Model", self.model_edit)
        layout.addRow("Temperature", self.temperature_spin)
        layout.addRow("Max Tokens", self.max_tokens_spin)
        layout.addRow(self.ai_summary_check)
        layout.addRow(self.ai_translation_check)
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._test_connection)
        layout.addRow(test_button)

        return box

    def _prompt_box(self) -> QGroupBox:
        box = QGroupBox("自定义 Prompt")
        layout = QFormLayout(box)
        layout.addRow("摘要 Prompt", self.summary_prompt_edit)
        layout.addRow("翻译 Prompt", self.translation_prompt_edit)
        layout.addRow("分类 Prompt", self.category_prompt_edit)
        layout.addRow("评分 Prompt", self.score_prompt_edit)
        layout.addRow("筛选 Prompt", self.filter_prompt_edit)
        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._reset_prompts)
        layout.addRow(reset_button)

        return box

    def _ai_cost_box(self) -> QGroupBox:
        box = QGroupBox("AI 成本控制")
        layout = QFormLayout(box)
        layout.addRow("每篇新闻最大 Token", self.max_article_tokens_spin)
        layout.addRow("每天最大 API 调用次数", self.max_daily_calls_spin)
        layout.addRow("超过限制", self.limit_action_combo)

        return box

    def _path_box(self) -> QGroupBox:
        box = QGroupBox("路径与导出")
        layout = QFormLayout(box)
        layout.addRow("User-Agent", self.user_agent_edit)
        layout.addRow("请求超时", self.timeout_spin)
        layout.addRow("SQLite 路径", self._path_row(self.sqlite_edit, False))
        layout.addRow("导出目录", self._path_row(self.output_edit, True))
        layout.addRow("Markdown", self.markdown_check)
        layout.addRow("Word", self.word_check)

        return box

    def _path_row(self, edit: QLineEdit, directory: bool) -> QWidget:
        widget = QWidget()
        button = QPushButton("选择")
        button.clicked.connect(lambda: self._select_path(edit, directory))
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)

        return widget

    def _select_path(self, edit: QLineEdit, directory: bool) -> None:
        if directory:
            path = QFileDialog.getExistingDirectory(self, "选择目录")
        else:
            path, _ = QFileDialog.getSaveFileName(self, "选择 SQLite 文件")

        if path:
            edit.setText(path)

    def _test_connection(self) -> None:
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "测试连接", "请先填写 API Key。")
            return

        if not self.base_url_edit.text().strip() or not self.model_edit.text().strip():
            QMessageBox.warning(self, "测试连接", "请填写 Base URL 和 Model。")
            return

        QMessageBox.information(self, "测试连接", "配置已填写，可用于生成时连接。")

    def _reset_prompts(self) -> None:
        for editor in [
            self.summary_prompt_edit,
            self.translation_prompt_edit,
            self.category_prompt_edit,
            self.score_prompt_edit,
            self.filter_prompt_edit,
        ]:
            editor.clear()
