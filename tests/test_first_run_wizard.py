"""First-run dialogs must be themed before Windows can paint their controls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QWidget, QWizard

from ui.first_run_wizard import FirstRunWizard
from ui.theme import ThemeManager


class FirstRunAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_startup_applies_system_theme_before_constructing_wizard(self):
        import app
        for dark in (False, True):
            with self.subTest(dark=dark):
                def construct():
                    colors = self.app.property('spaceweeklyThemeColors')
                    self.assertIsNotNone(colors)
                    self.assertEqual(self.app.palette().color(QPalette.Window).name(), colors['window_bg'])
                    self.assertEqual(self.app.palette().color(QPalette.WindowText).name(), colors['text'])
                    self.assertTrue(self.app.styleSheet())
                    wizard = Mock()
                    wizard.exec.return_value = QDialog.Rejected
                    return wizard
                with patch('PySide6.QtWidgets.QApplication', return_value=self.app), \
                     patch('dependency_check.missing_dependencies', return_value=[]), \
                     patch('services.workspace.needs_first_run_wizard', return_value=True), \
                     patch('ui.theme.ThemeManager._system_prefers_dark', return_value=dark), \
                     patch('ui.theme.ThemeManager._connect_system_theme'), \
                     patch('ui.first_run_wizard.FirstRunWizard', side_effect=construct):
                    app.main()

    def test_pages_have_visible_text_and_fit_minimum_size_in_both_themes(self):
        manager = ThemeManager(self.app)
        for mode in ('light', 'dark'):
            with self.subTest(mode=mode):
                manager.apply(mode)
                wizard = FirstRunWizard()
                wizard.resize(wizard.minimumSize())
                wizard.show()
                try:
                    self.assertEqual(wizard.wizardStyle(), QWizard.ModernStyle)
                    for page_id in wizard.pageIds():
                        wizard.setStartId(page_id)
                        wizard.restart()
                        self.app.processEvents()
                        page = wizard.currentPage()
                        self.assertTrue(page.title())
                        for widget in page.findChildren(QWidget):
                            if widget.isVisible() and widget.isWidgetType():
                                palette = widget.palette()
                                text = palette.color(QPalette.WindowText)
                                background = palette.color(QPalette.Window)
                                parent = widget.parentWidget()
                                while background.alpha() == 0 and parent is not None:
                                    background = parent.palette().color(QPalette.Window)
                                    parent = parent.parentWidget()
                                self.assertGreater(abs(text.lightness() - background.lightness()), 100,
                                    f'{mode} {page_id} {type(widget).__name__}: {text.name()} / {background.name()}')
                                self.assertTrue(page.rect().contains(widget.geometry()), widget.objectName())
                        for button in (QWizard.NextButton, QWizard.CancelButton, QWizard.FinishButton):
                            control = wizard.button(button)
                            if control.isVisible():
                                self.assertGreaterEqual(control.width(), control.sizeHint().width())
                finally:
                    wizard.close()
                    wizard.deleteLater()
        manager.deleteLater()


if __name__ == '__main__':
    unittest.main()
