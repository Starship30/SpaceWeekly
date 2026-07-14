from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QHeaderView
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QTableWidget
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QVBoxLayout

from i18n import tr
from translator.dictionary import add_pending_term
from translator.dictionary import confirm_pending_term
from translator.dictionary import load_dictionary
from translator.dictionary import load_pending_terms
from translator.dictionary import remove_pending_term
from translator.dictionary import save_dictionary
from translator.translator import Translator


class TermEditorDialog(QDialog):
    """Edit confirmed terms and promote reviewed pending translations."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.pending_sources: set[str] = set()
        self.pending_prefix = tr("terms.pending") + " "
        self.setWindowTitle(tr("terms.editor"))
        self.resize(760, 500)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [tr("terms.original"), tr("terms.translation"), tr("terms.type")]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._load_rows()
        self._build_layout()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        actions = QHBoxLayout()

        for label, handler in [
            (tr("terms.add"), self._add_row),
            (tr("terms.remove"), self._remove_rows),
            (tr("terms.ai.translate"), self._ai_translate),
            (tr("terms.confirm.pending"), self._confirm_pending),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)

        actions.addStretch(1)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_rows(self) -> None:
        for source, entry in load_dictionary().items():
            self._append_row(source, entry["translation"], entry["type"])

        for source, entry in load_pending_terms().items():
            self.pending_sources.add(source)
            self._append_row(
                source,
                entry.get("translation", ""),
                entry.get("type", "term"),
                pending=True,
            )

    def _append_row(
        self,
        source: str = "",
        translation: str = "",
        term_type: str = "term",
        pending: bool = False,
    ) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        prefix = self.pending_prefix if pending else ""
        self.table.setItem(row, 0, QTableWidgetItem(prefix + source))
        self.table.setItem(row, 1, QTableWidgetItem(translation))
        self.table.setItem(row, 2, QTableWidgetItem(term_type))

    def _add_row(self) -> None:
        self._append_row()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.currentItem())

    def _remove_rows(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)

        for row in rows:
            source = self._source(row)

            if source in self.pending_sources:
                remove_pending_term(source)
                self.pending_sources.discard(source)

            self.table.removeRow(row)

    def _ai_translate(self) -> None:
        row = self.table.currentRow()

        if row < 0 or not self._source(row):
            QMessageBox.warning(self, tr("terms.editor"), tr("terms.select"))
            return

        source = self._source(row)
        term_type = self._cell_text(row, 2) or "term"
        result = Translator(settings=self.settings, mode="ai").translate_term(
            source,
            term_type,
        )
        answer = QMessageBox.question(
            self,
            tr("terms.ai.result"),
            tr("terms.ai.confirm", source=source, translation=result),
        )

        if answer == QMessageBox.StandardButton.Yes:
            self.table.setItem(row, 1, QTableWidgetItem(result))
            add_pending_term(source, term_type, result)
            confirm_pending_term(source, result, term_type)
            self.pending_sources.discard(source)
            self.table.setItem(row, 0, QTableWidgetItem(source))

    def _confirm_pending(self) -> None:
        row = self.table.currentRow()

        if row < 0 or self._source(row) not in self.pending_sources:
            QMessageBox.warning(self, tr("terms.editor"), tr("terms.select.pending"))
            return

        source = self._source(row)
        translation = self._cell_text(row, 1)
        term_type = self._cell_text(row, 2) or "term"

        if not translation:
            QMessageBox.warning(self, tr("terms.editor"), tr("terms.enter.translation"))
            return

        confirm_pending_term(source, translation, term_type)
        self.pending_sources.discard(source)
        self.table.setItem(row, 0, QTableWidgetItem(source))

    def _save(self) -> None:
        entries = {}

        for row in range(self.table.rowCount()):
            source = self._source(row)
            translation = self._cell_text(row, 1)

            if source and translation and source not in self.pending_sources:
                entries[source] = {
                    "translation": translation,
                    "type": self._cell_text(row, 2) or "term",
                }

        save_dictionary(entries)
        self.accept()

    def _source(self, row: int) -> str:
        return self._cell_text(row, 0).removeprefix(self.pending_prefix).strip()

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)

        return item.text().strip() if item is not None else ""
