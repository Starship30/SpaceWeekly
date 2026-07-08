import time
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSplitter
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from models.article import Article
from models.news import News
from services.prompt_manager import PROMPT_TYPES
from services.prompt_manager import VARIABLES
from services.prompt_manager import PromptPreset
from services.prompt_manager import default_preset
from services.prompt_manager import find_preset
from services.prompt_manager import load_presets
from services.prompt_manager import render_prompt
from services.prompt_manager import save_preset
from services.settings import AppSettings

PROMPT_LABELS = {
    "summary": "摘要 Prompt",
    "translation": "翻译 Prompt",
    "category": "分类 Prompt",
    "score": "重要程度 Prompt",
    "filter": "编辑策略 Prompt",
    "custom": "自定义 Prompt",
}


class PromptStudioDialog(QDialog):
    """Prompt editor and local tester."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.original_settings = settings
        self.presets = load_presets()
        self.current_type = "summary"
        self.setWindowTitle("Prompt Studio")
        self.resize(980, 680)
        self._build_ui()
        self._load_current_prompt()

    def settings(self) -> AppSettings:
        """Return settings with edited prompts."""
        self._save_current_prompt()

        return replace(
            self.original_settings,
            prompt_preset=self.preset_combo.currentText(),
            summary_prompt=self._prompt("summary"),
            translation_prompt=self._prompt("translation"),
            category_prompt=self._prompt("category"),
            score_prompt=self._prompt("score"),
            filter_prompt=self._prompt("filter"),
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.preset_combo = QComboBox()

        for preset in self.presets:
            self.preset_combo.addItem(preset.name)

        self.preset_combo.setCurrentText(self.original_settings.prompt_preset)
        self.preset_combo.currentTextChanged.connect(self._load_preset)
        top.addWidget(QLabel("Prompt 模板"))
        top.addWidget(self.preset_combo, 1)
        top.addWidget(self._button("保存预设", self._save_preset))
        top.addWidget(self._button("导入", self._import_preset))
        top.addWidget(self._button("导出", self._export_preset))
        layout.addLayout(top)
        layout.addWidget(self._splitter(), 1)
        footer = QHBoxLayout()
        self.char_label = QLabel()
        self.word_label = QLabel()
        footer.addWidget(self.char_label)
        footer.addWidget(self.word_label)
        footer.addStretch()
        footer.addWidget(self._button("测试 Prompt", self._test_prompt))
        footer.addWidget(self._button("复制渲染结果", self._copy_rendered_prompt))
        footer.addWidget(self._button("恢复默认", self._restore_default))
        layout.addLayout(footer)
        self.type_list.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        type_box = QWidget()
        type_layout = QVBoxLayout(type_box)
        type_layout.addWidget(QLabel("Prompt 类型"))
        self.type_list = QListWidget()

        for prompt_type in PROMPT_TYPES:
            self.type_list.addItem(PROMPT_LABELS.get(prompt_type, prompt_type))

        self.type_list.currentRowChanged.connect(self._change_prompt_type)
        type_layout.addWidget(self.type_list, 1)
        splitter.addWidget(type_box)
        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.addWidget(QLabel("模板编辑"))
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.textChanged.connect(self._update_char_count)
        editor_layout.addWidget(self.editor)
        splitter.addWidget(editor_box)
        variables_box = QWidget()
        variables_layout = QVBoxLayout(variables_box)
        variables_layout.addWidget(QLabel("变量列表"))
        self.variables_list = QListWidget()

        for variable in VARIABLES:
            self.variables_list.addItem("{" + variable + "}")

        self.variables_list.itemDoubleClicked.connect(
            lambda item: self.editor.insertPlainText(item.text())
        )
        variables_layout.addWidget(self.variables_list, 1)
        splitter.addWidget(variables_box)
        splitter.setSizes([180, 620, 180])

        return splitter

    def _button(self, text: str, handler) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(handler)

        return button

    def _change_prompt_type(self, index: int) -> None:
        self._save_current_prompt()
        self.current_type = PROMPT_TYPES[max(index, 0)]
        self._load_current_prompt()

    def _load_current_prompt(self) -> None:
        self.editor.blockSignals(True)
        self.editor.setPlainText(self._prompt(self.current_type))
        self.editor.blockSignals(False)
        self._update_char_count()

    def _load_preset(self, name: str) -> None:
        self._save_current_prompt()
        preset = find_preset(name)
        self.original_settings = replace(
            self.original_settings,
            summary_prompt=preset.summary,
            translation_prompt=preset.translation,
            category_prompt=preset.category,
            score_prompt=preset.score,
            filter_prompt=preset.filter,
        )
        self._load_current_prompt()

    def _prompt(self, prompt_type: str) -> str:
        return str(getattr(self.original_settings, prompt_type + "_prompt", ""))

    def _save_current_prompt(self) -> None:
        field_name = self.current_type + "_prompt"

        if hasattr(self.original_settings, field_name):
            self.original_settings = replace(
                self.original_settings,
                **{field_name: self.editor.toPlainText()},
            )

    def _save_preset(self) -> None:
        self._save_current_prompt()
        preset = PromptPreset(
            name=self.preset_combo.currentText() or "Custom",
            summary=self.original_settings.summary_prompt,
            translation=self.original_settings.translation_prompt,
            category=self.original_settings.category_prompt,
            score=self.original_settings.score_prompt,
            filter=self.original_settings.filter_prompt,
            custom="",
        )
        save_preset(preset)
        QMessageBox.information(self, "Prompt Studio", "预设已保存。")

    def _import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Prompt 预设",
            "",
            "JSON Files (*.json)",
        )

        if not path:
            return

        import json

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Prompt Studio", f"导入失败：{exc}")
            return

        name = str(data.get("name", "Imported"))
        save_preset(
            PromptPreset(
                name=name,
                summary=str(data.get("summary", "")),
                translation=str(data.get("translation", "")),
                category=str(data.get("category", "")),
                score=str(data.get("score", "")),
                filter=str(data.get("filter", "")),
                custom=str(data.get("custom", "")),
            )
        )
        self.presets = load_presets()
        self.preset_combo.addItem(name)
        self.preset_combo.setCurrentText(name)
        QMessageBox.information(self, "Prompt Studio", "Prompt 模板已导入。")

    def _export_preset(self) -> None:
        self._save_current_prompt()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Prompt 预设",
            "prompt.json",
            "JSON Files (*.json)",
        )

        if not path:
            return

        saved_path = save_preset(
            PromptPreset(
                name=self.preset_combo.currentText() or "Custom",
                summary=self.original_settings.summary_prompt,
                translation=self.original_settings.translation_prompt,
                category=self.original_settings.category_prompt,
                score=self.original_settings.score_prompt,
                filter=self.original_settings.filter_prompt,
                custom="",
            )
        )
        import shutil

        shutil.copyfile(saved_path, path)
        QMessageBox.information(self, "Prompt Studio", "Prompt 模板已导出。")

    def _restore_default(self) -> None:
        preset = default_preset()
        self.original_settings = replace(
            self.original_settings,
            summary_prompt=preset.summary,
            translation_prompt=preset.translation,
            category_prompt=preset.category,
            score_prompt=preset.score,
            filter_prompt=preset.filter,
        )
        self._load_current_prompt()
        QMessageBox.information(self, "Prompt Studio", "已恢复默认模板。")

    def _test_prompt(self) -> None:
        started_at = time.perf_counter()
        rendered = self._render_current_prompt()
        elapsed = round(time.perf_counter() - started_at, 3)
        tokens = max(len(rendered) // 4, 1)
        QMessageBox.information(
            self,
            "Prompt 测试",
            f"渲染成功\n\n预计 Token：{tokens}\n耗时：{elapsed} 秒\n\n{rendered[:1200]}",
        )

    def _copy_rendered_prompt(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._render_current_prompt())

    def _render_current_prompt(self) -> str:
        article = Article(
            news=News(
                source="NASA Science",
                title="Sample Mars Mission Update",
                published="Thu, 02 Jul 2026 10:00:00 GMT",
                summary="A sample RSS summary for prompt testing.",
                url="https://science.nasa.gov/sample",
            ),
            body="NASA released a sample article body for testing Prompt Studio.",
            parser="Prompt Tester",
        )

        return render_prompt(self.editor.toPlainText(), article)

    def _update_char_count(self) -> None:
        text = self.editor.toPlainText()
        words = len([part for part in text.replace("\n", " ").split(" ") if part])
        self.char_label.setText(f"字符数：{len(text)}")
        self.word_label.setText(f"字数：{words}")
