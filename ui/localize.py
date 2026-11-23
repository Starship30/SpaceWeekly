"""Retranslate static widget chrome without rebuilding editors or their state."""
import json
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import QLibraryInfo,QTranslator
from PySide6.QtWidgets import QApplication,QAbstractButton,QLabel,QTabWidget,QComboBox,QLineEdit,QPlainTextEdit
from PySide6.QtGui import QAction
from i18n import current_language,set_language,tr
from resources import bundled_resource_path,resource_path


def set_ui_language(language):
    set_language(language)
    app=QApplication.instance()
    if app is None:return
    translator=getattr(app,'_spaceweekly_qt_translator',None)
    if current_language()!='zh_CN':
        if translator is not None:
            app.removeTranslator(translator)
            translator.deleteLater()
            app._spaceweekly_qt_translator=None
        return
    if translator is not None:return
    name='qtbase_zh_CN.qm'
    paths=(bundled_resource_path('qt-translations',name),
           Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))/name)
    translator=QTranslator(app)
    if any(translator.load(str(path)) for path in paths):
        app.installTranslator(translator)
        app._spaceweekly_qt_translator=translator
    else:translator.deleteLater()


@lru_cache(maxsize=1)
def _keys():
    result={}
    for language in ('zh_CN','en_US'):
        for key,value in json.loads(resource_path('language',language+'.json').read_text(encoding='utf-8')).items():
            if '{' not in value:
                result.setdefault(value,key)
    return result


def chrome(text):
    key=_keys().get(text)
    return tr(key) if key else text


def retranslate(root):
    for action in root.findChildren(QAction):action.setText(chrome(action.text()))
    for kind in (QAbstractButton,QLabel):
        for widget in root.findChildren(kind):
            widget.setText(chrome(widget.text()))
    for widget in root.findChildren(QTabWidget):
        for index in range(widget.count()):widget.setTabText(index,chrome(widget.tabText(index)))
    for widget in root.findChildren(QComboBox):
        widget.blockSignals(True)
        for index in range(widget.count()):widget.setItemText(index,chrome(widget.itemText(index)))
        widget.blockSignals(False)
    for kind in (QLineEdit,QPlainTextEdit):
        for widget in root.findChildren(kind):widget.setPlaceholderText(chrome(widget.placeholderText()))
