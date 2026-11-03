from ui.component import SegmentedTabs,configure_table
from PySide6.QtWidgets import QDialog
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QHeaderView
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QTableWidget
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtWidgets import QLineEdit, QTabWidget, QWidget
from PySide6.QtCore import Qt
import json

from i18n import tr
from translator.dictionary import add_pending_term
from translator.dictionary import confirm_pending_term
from translator.dictionary import load_dictionary
from translator.dictionary import load_pending_terms
from translator.dictionary import remove_pending_term
from translator.dictionary import save_dictionary
from translator.dictionary import save_pending_terms
from translator.translator import Translator
from services.launch_research import load_launch_research, confirm_launch, request_recheck

TERM_TYPES = {'mission':'任务','rocket':'火箭','organization':'机构','location':'发射场',
              'orbit':'轨道','status':'状态','term':'术语'}


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
        configure_table(self.table)
        self.table.setHorizontalHeaderLabels(
            [tr("terms.original"), tr("terms.translation"), tr("terms.type")]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._load_rows()
        self.launch_table = QTableWidget(0, 5)
        configure_table(self.launch_table)
        self.launch_table.setHorizontalHeaderLabels(['LL2 ID', '英文任务名', '中文名（可纠正）', '中文简介（可纠正）', '资料 / 人工状态'])
        self.launch_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._load_launches()
        self._build_layout()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        self.search=QLineEdit();self.search.setPlaceholderText('查询原文或中文名称')
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        tabs=SegmentedTabs();layout.addWidget(tabs,1)
        terms=QWidget();terms_layout=QVBoxLayout(terms);terms_layout.addWidget(self.table)
        tabs.addTab(terms,'术语词典与待确认候选')
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
        terms_layout.addLayout(actions)
        launches=QWidget();launch_layout=QVBoxLayout(launches);launch_layout.addWidget(self.launch_table)
        tabs.addTab(launches,'任务资料')
        self.launch_table.setColumnHidden(0,True)
        launch_actions = QHBoxLayout()
        for label, handler in [('查看发射资料与出处', self._show_launch), ('人工确认任务资料', self._confirm_launch),
                               ('重新核对任务（下次生成）', self._recheck_launch)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            launch_actions.addWidget(button)
        launch_layout.addLayout(launch_actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        layout.addWidget(buttons)

    def _filter(self,text):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row,text.casefold() not in ' '.join(self._cell_text(row,c) for c in (0,1)).casefold())

    def _load_rows(self) -> None:
        self.original_entries = load_dictionary()
        for source, entry in self.original_entries.items():
            self._append_row(source, entry["translation"], entry["type"])

        for source, entry in load_pending_terms().items():
            if any(k.casefold() == source.casefold() for k in self.original_entries):
                continue
            self.pending_sources.add(source)
            self._append_row(
                source,
                entry.get("translation", ""),
                entry.get("type", "term"),
                pending=True,
            )

    def _load_launches(self):
        try:
            self.launch_records = load_launch_research()
        except (OSError, ValueError) as error:
            self.launch_records = {}
            QMessageBox.warning(self, tr('terms.editor'), str(error))
        self.launch_table.setRowCount(0)
        for identifier, record in self.launch_records.items():
            row = self.launch_table.rowCount()
            self.launch_table.insertRow(row)
            values = record.get('manual', {}) if record.get('human_status') == 'confirmed' else record
            evidence = str(record.get('evidence_status') or 'unverified')
            human = str(record.get('human_status') or 'unreviewed')
            state = {'supported': '有证据', 'pending': '待核对', 'conflict': '来源冲突'}.get(evidence, '未核实')
            state += ' / ' + ('人工确认' if human == 'confirmed' else '未人工确认')
            if record.get('needs_review') or record.get('recheck_requested'):
                state += ' / 待重新核对'
            for column, value in enumerate([identifier, record.get('english_name', ''), values.get('chinese_name', ''),
                                             values.get('summary', ''), state]):
                cell = QTableWidgetItem(str(value or ''))
                cell.setToolTip(str(value or ''))
                if column not in (2, 3):
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.launch_table.setItem(row, column, cell)

    def _launch_selection(self):
        row = self.launch_table.currentRow()
        return (row, self.launch_table.item(row, 0).text()) if row >= 0 else (-1, '')

    def _show_launch(self):
        row, identifier = self._launch_selection()
        if row < 0:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('发射资料与出处 — ' + identifier)
        dialog.resize(850, 600)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(self.launch_records[identifier], ensure_ascii=False, indent=2))
        layout.addWidget(view)
        dialog.exec()

    def _confirm_launch(self):
        row, identifier = self._launch_selection()
        if row < 0:
            return
        try:
            confirm_launch(identifier, self.launch_table.item(row, 2).text(), self.launch_table.item(row, 3).text())
            self._load_launches()
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, tr('terms.editor'), str(error))

    def _recheck_launch(self):
        row, identifier = self._launch_selection()
        if row < 0:
            return
        try:
            request_recheck(identifier)
            self._load_launches()
            QMessageBox.information(self, tr('terms.editor'), '已标记。下次生成包含该 LL2 任务的内容时重新核对；需开启联网核对。人工纠正不会被自动覆盖。')
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, tr('terms.editor'), str(error))

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
        self.table.setItem(row, 2, QTableWidgetItem(TERM_TYPES.get(term_type,term_type)))

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
        translation_result = Translator(
            settings=self.settings,
            mode="ai",
        ).translate_term_result(
            source,
            term_type,
        )

        if not translation_result.success or translation_result.translation is None:
            QMessageBox.warning(
                self,
                tr("terms.ai.result"),
                tr(
                    "terms.ai.failed",
                    error=translation_result.error_code.value
                    if translation_result.error_code
                    else translation_result.error_message,
                ),
            )
            return

        result = translation_result.translation
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
        pending = load_pending_terms()

        for row in range(self.table.rowCount()):
            source = self._source(row)
            translation = self._cell_text(row, 1)
            if source in self.pending_sources:
                pending[source] = dict(pending.get(source, {}), translation=translation,
                                       type=self._cell_text(row, 2) or 'term')

            if source and translation and source not in self.pending_sources:
                entries[source] = {
                    **self.original_entries.get(source, {}),
                    "translation": translation,
                    "type": self._cell_text(row, 2) or "term",
                }
                if (source not in self.original_entries
                        or self.original_entries[source].get('translation') != translation):
                    entries[source]['human_status'] = 'confirmed'

        save_dictionary(entries)
        save_pending_terms(pending)
        self.accept()

    def _source(self, row: int) -> str:
        return self._cell_text(row, 0).removeprefix(self.pending_prefix).strip()

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        value = item.text().strip() if item is not None else ""
        return {v:k for k,v in TERM_TYPES.items()}.get(value,value) if column == 2 else value
