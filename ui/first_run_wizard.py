from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QRadioButton
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWizard
from PySide6.QtWidgets import QWizardPage

from i18n import set_language
from i18n import tr
from services.workspace import default_workspace
from services.workspace import initialize_workspace


class FirstRunWizard(QWizard):
    """Wizard that creates the user workspace before the main window opens."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.workspace: Path | None = None
        self.language = _detect_system_language()
        set_language(self.language)
        self.language_page = _LanguagePage(self)
        self.welcome_page = _WelcomePage()
        self.workspace_page = _WorkspacePage(self)
        self.complete_page = _CompletePage()
        self.addPage(self.language_page)
        self.addPage(self.welcome_page)
        self.addPage(self.workspace_page)
        self.addPage(self.complete_page)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage)
        self.retranslate()

    def set_wizard_language(self, language: str) -> None:
        self.language = language
        set_language(language)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("first.run.title"))
        self.setButtonText(QWizard.WizardButton.BackButton, tr("wizard.back"))
        self.setButtonText(QWizard.WizardButton.NextButton, tr("wizard.next"))
        self.setButtonText(QWizard.WizardButton.FinishButton, tr("wizard.finish"))
        self.setButtonText(QWizard.WizardButton.CancelButton, tr("wizard.cancel"))

        for page in [
            self.language_page,
            self.welcome_page,
            self.workspace_page,
            self.complete_page,
        ]:
            page.retranslate()


class _LanguagePage(QWizardPage):
    def __init__(self, wizard: FirstRunWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.language_group = QButtonGroup(self)
        self.chinese_button = QRadioButton("简体中文")
        self.english_button = QRadioButton("English")
        self.language_group.addButton(self.chinese_button)
        self.language_group.addButton(self.english_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.description)
        layout.addWidget(self.chinese_button)
        layout.addWidget(self.english_button)
        layout.addStretch(1)

        if wizard.language == "zh_CN":
            self.chinese_button.setChecked(True)
        else:
            self.english_button.setChecked(True)

        self.chinese_button.toggled.connect(self._language_changed)
        self.english_button.toggled.connect(self._language_changed)

    def retranslate(self) -> None:
        self.setTitle(tr("wizard.language.title"))
        self.description.setText(tr("wizard.language.text"))
        self.chinese_button.setText(tr("chinese"))
        self.english_button.setText(tr("english"))

    def _language_changed(self, *_args) -> None:
        if self.chinese_button.isChecked():
            self._wizard.set_wizard_language("zh_CN")
            return

        if self.english_button.isChecked():
            self._wizard.set_wizard_language("en_US")


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def retranslate(self) -> None:
        self.setTitle(tr("wizard.welcome.title"))
        self.label.setText(tr("wizard.welcome.text"))


class _WorkspacePage(QWizardPage):
    def __init__(self, wizard: FirstRunWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self.path_edit = QLineEdit(str(default_workspace()))
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse)
        self.create_button = QPushButton()
        self.create_button.clicked.connect(self._create_folder)
        self.note = QLabel()
        self.note.setWordWrap(True)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.create_button)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def retranslate(self) -> None:
        self.setTitle(tr("wizard.workspace.title"))
        self.browse_button.setText(tr("browse"))
        self.create_button.setText(tr("create.folder"))
        self.note.setText(tr("wizard.workspace.note"))

    def validatePage(self) -> bool:
        raw_workspace = self.path_edit.text().strip()

        if not raw_workspace:
            self._show_error(tr("wizard.error.no.folder"))
            return False

        workspace = Path(raw_workspace).expanduser()

        try:
            self._wizard.workspace = initialize_workspace(
                workspace,
                language=self._wizard.language,
            )
        except OSError:
            self._show_error(tr("wizard.error.not.writable"))
            return False

        return True

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("wizard.choose.folder"),
            self.path_edit.text().strip(),
        )

        if folder:
            self.path_edit.setText(folder)

    def _create_folder(self) -> None:
        workspace = Path(self.path_edit.text().strip()).expanduser()

        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._show_error(tr("wizard.error.create.folder"))
            return

        QMessageBox.information(self, "SpaceWeekly", tr("wizard.folder.ready"))

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "SpaceWeekly", message)


class _CompletePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def retranslate(self) -> None:
        self.setTitle(tr("wizard.complete.title"))
        self.label.setText(tr("wizard.complete.text"))


def _detect_system_language() -> str:
    locale = QLocale.system()
    values = [
        locale.name(),
        locale.bcp47Name(),
        locale.nativeLanguageName(),
        locale.languageToString(locale.language()),
    ]
    normalized = " ".join(value.lower() for value in values if value)

    if (
        "zh_cn" in normalized
        or "zh-hans" in normalized
        or "hans" in normalized
        or "simplified" in normalized
        or "chinese" in normalized
        or "中文" in normalized
        or "简体" in normalized
    ):
        return "zh_CN"

    return "en_US"
