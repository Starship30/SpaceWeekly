from dataclasses import replace

from PySide6.QtCore import Qt
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
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
from i18n import tr

PROMPT_LABELS = {
    "summary": "prompt.summary",
    "filter": "prompt.filter",
    "title": "prompt.title",
}


class PromptStudioDialog(QDialog):
    """Prompt editor and local tester."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.original_settings = settings
        self.presets = load_presets()
        self.current_type = PROMPT_TYPES[0]
        self.setWindowTitle(tr("prompt.studio"))
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
            filter_prompt=self._prompt("filter"),
            title_prompt=self._prompt("title"),
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(tr("prompt.guide"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        top = QHBoxLayout()
        self.preset_combo = QComboBox()

        for preset in self.presets:
            self.preset_combo.addItem(preset.name)

        self.preset_combo.setCurrentText(self.original_settings.prompt_preset)
        self.preset_combo.currentTextChanged.connect(self._load_preset)
        top.addWidget(QLabel(tr("prompt.template")))
        top.addWidget(self.preset_combo, 1)
        top.addWidget(self._button(tr("save.preset"), self._save_preset))
        top.addWidget(self._button(tr("import"), self._import_preset))
        top.addWidget(self._button(tr("export.preset"), self._export_preset))
        layout.addLayout(top)
        layout.addWidget(self._splitter(), 1)
        footer = QHBoxLayout()
        self.char_label = QLabel()
        self.word_label = QLabel()
        footer.addWidget(self.char_label)
        footer.addWidget(self.word_label)
        footer.addStretch()
        footer.addWidget(self._button(tr("test.prompt"), self._test_prompt))
        footer.addWidget(self._button(tr("copy.rendered"), self._copy_rendered_prompt))
        footer.addWidget(self._button(tr("restore.default"), self._restore_default))
        layout.addLayout(footer)
        self.type_list.blockSignals(True)
        self.type_list.setCurrentRow(0)
        self.type_list.blockSignals(False)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("prompt.apply"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        type_box = QWidget()
        type_layout = QVBoxLayout(type_box)
        type_layout.addWidget(QLabel(tr("prompt.type")))
        self.type_list = QListWidget()

        for prompt_type in PROMPT_TYPES:
            self.type_list.addItem(tr(PROMPT_LABELS.get(prompt_type, prompt_type)))

        self.type_list.currentRowChanged.connect(self._change_prompt_type)
        type_layout.addWidget(self.type_list, 1)
        splitter.addWidget(type_box)
        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.addWidget(QLabel(tr("template.editor")))
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        editor_layout.addWidget(self.help_label)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr("prompt.placeholder"))
        self.editor.textChanged.connect(self._update_char_count)
        editor_layout.addWidget(self.editor)
        splitter.addWidget(editor_box)
        variables_box = QWidget()
        variables_layout = QVBoxLayout(variables_box)
        variables_layout.addWidget(QLabel(tr("variables")))
        self.variables_list = QListWidget()
        self.variables_list.setWordWrap(True)

        for variable in VARIABLES:
            item = QListWidgetItem(tr("prompt.variable." + variable) + "\n{" + variable + "}")
            item.setData(Qt.ItemDataRole.UserRole, "{" + variable + "}")
            self.variables_list.addItem(item)

        self.variables_list.itemDoubleClicked.connect(
            lambda item: self.editor.insertPlainText(item.data(Qt.ItemDataRole.UserRole))
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
        self.help_label.setText(tr("prompt.help." + self.current_type))
        self.editor.blockSignals(False)
        self._update_char_count()

    def _load_preset(self, name: str) -> None:
        self._save_current_prompt()
        preset = find_preset(name)
        self.original_settings = replace(
            self.original_settings,
            summary_prompt=preset.summary,
            filter_prompt=preset.filter,
            title_prompt=preset.title,
        )
        self._load_current_prompt()

    def _prompt(self, prompt_type: str) -> str:
        return (str(getattr(self.original_settings, prompt_type + "_prompt", ""))
                or str(getattr(find_preset(self.preset_combo.currentText()), prompt_type)))

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
            summary=self._prompt("summary"),
            filter=self._prompt("filter"),
            title=self._prompt("title"),
        )
        save_preset(preset)
        QMessageBox.information(self, tr("prompt.studio"), tr("preset.saved"))

    def _import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("import.prompt.preset"),
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
            QMessageBox.warning(self, tr("prompt.studio"), tr("import.failed", error=exc))
            return

        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("name", "summary", "filter")):
            QMessageBox.warning(self, tr("prompt.studio"), tr("prompt.invalid_template"))
            return
        if "title" in data and not isinstance(data["title"], str):
            QMessageBox.warning(self, tr("prompt.studio"), tr("prompt.invalid_template"))
            return
        name = data["name"]
        save_preset(
            PromptPreset(
                name=name,
                summary=str(data.get("summary", "")),
                filter=str(data.get("filter", "")),
                title=str(data.get("title") or default_preset().title),
            )
        )
        self.presets = load_presets()
        self.preset_combo.addItem(name)
        self.preset_combo.setCurrentText(name)
        QMessageBox.information(self, tr("prompt.studio"), tr("prompt.imported"))

    def _export_preset(self) -> None:
        self._save_current_prompt()
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export.prompt.preset"),
            "prompt.json",
            "JSON Files (*.json)",
        )

        if not path:
            return

        saved_path = save_preset(
            PromptPreset(
                name=self.preset_combo.currentText() or "Custom",
                summary=self._prompt("summary"),
                filter=self._prompt("filter"),
                title=self._prompt("title"),
            )
        )
        import shutil

        shutil.copyfile(saved_path, path)
        QMessageBox.information(self, tr("prompt.studio"), tr("prompt.exported"))

    def _restore_default(self) -> None:
        preset = default_preset()
        self.original_settings = replace(
            self.original_settings,
            summary_prompt=preset.summary,
            filter_prompt=preset.filter,
            title_prompt=preset.title,
        )
        self._load_current_prompt()
        QMessageBox.information(self, tr("prompt.studio"), tr("default.restored"))

    def _test_prompt(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("test.prompt"))
        dialog.resize(700, 440)
        layout = QVBoxLayout(dialog)
        hint = QLabel(tr("prompt.preview.hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        preview = QPlainTextEdit(self._render_current_prompt())
        preview.setReadOnly(True)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _copy_rendered_prompt(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._render_current_prompt())

    def _render_current_prompt(self) -> str:
        article = Article(
            news=News(
                source="NASA Science",
                title=tr("prompt.sample.title"),
                published="Thu, 02 Jul 2026 10:00:00 GMT",
                summary=tr("prompt.sample.summary"),
                url="https://science.nasa.gov/sample",
            ),
            body=tr("prompt.sample.body"),
            parser="Prompt Tester",
        )

        return render_prompt(self.editor.toPlainText(), article)

    def _update_char_count(self) -> None:
        text = self.editor.toPlainText()
        words = len([part for part in text.replace("\n", " ").split(" ") if part])
        self.char_label.setText(tr("char.count", count=len(text)))
        self.word_label.setText(tr("word.count", count=words))
