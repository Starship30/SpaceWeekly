import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from i18n import set_language
from services.settings import default_settings
from services import prompt_manager
from ui.prompt_studio_dialog import PromptStudioDialog


class PromptStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        patcher = patch.object(prompt_manager, 'PROMPTS_DIR', Path(self.scratch.name))
        patcher.start();self.addCleanup(patcher.stop)
        set_language('zh_CN')

    def dialog(self, **kwargs):
        dialog = PromptStudioDialog(replace(default_settings(), **kwargs))
        self.addCleanup(dialog.close)
        return dialog

    def test_open_preserves_existing_summary_and_filter_edits(self):
        d = self.dialog(summary_prompt='我的摘要规则', filter_prompt='我的筛选规则')
        self.assertEqual(d.editor.toPlainText(), '我的筛选规则')
        self.assertEqual(d.settings().summary_prompt, '我的摘要规则')
        d.editor.setPlainText('只收录已公布的进展')
        d.type_list.setCurrentRow(1)
        self.assertEqual(d.editor.toPlainText(), '我的摘要规则')
        d.editor.setPlainText('摘要只写两句话')
        d.type_list.setCurrentRow(0)
        self.assertEqual(d.editor.toPlainText(), '只收录已公布的进展')
        self.assertEqual(d.settings().summary_prompt, '摘要只写两句话')

    def test_effective_default_rules_shown_and_saved_for_both_steps(self):
        d = self.dialog(summary_prompt='', filter_prompt='')
        preset = prompt_manager.default_preset()
        self.assertEqual(d.editor.toPlainText(), preset.filter)
        with patch('ui.prompt_studio_dialog.QMessageBox.information'):
            d._save_preset()
        saved = prompt_manager.find_preset(d.preset_combo.currentText())
        self.assertEqual((saved.filter,saved.summary), (preset.filter,preset.summary))

    def test_variable_inserts_token_only_and_preview_uses_fictional_news(self):
        d = self.dialog(filter_prompt='{title}\n{body}')
        item = d.variables_list.item(0)
        d.editor.clear();d.variables_list.itemDoubleClicked.emit(item)
        self.assertEqual(d.editor.toPlainText(), '{title}')
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole), '{title}')
        self.assertIn('虚构', d._render_current_prompt())
        self.assertNotIn('{title}', d._render_current_prompt())
        for language in ['zh_CN', 'en_US']:
            set_language(language)
            d = self.dialog()
            self.assertTrue(d.help_label.text())
            self.assertNotEqual(d.windowTitle(), 'prompt.studio')

    def test_invalid_import_is_rejected_without_saving(self):
        d = self.dialog()
        path = Path(self.scratch.name)/'invalid.json';path.write_text('[]',encoding='utf-8')
        with patch('ui.prompt_studio_dialog.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
             patch('ui.prompt_studio_dialog.QMessageBox.warning') as warning, \
             patch('ui.prompt_studio_dialog.save_preset') as save:
            d._import_preset()
        warning.assert_called_once();save.assert_not_called()

if __name__ == '__main__': unittest.main()
