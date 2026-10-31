"""Retranslate static widget chrome without rebuilding editors or their state."""
import json
from functools import lru_cache
from PySide6.QtWidgets import QAbstractButton,QLabel,QTabWidget,QComboBox,QLineEdit,QPlainTextEdit
from PySide6.QtGui import QAction
from i18n import tr
from resources import resource_path


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
