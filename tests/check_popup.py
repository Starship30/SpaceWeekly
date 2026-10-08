"""Check native popup layout: python tests/check_popup.py."""
import os
import tempfile
os.environ['QT_QPA_PLATFORM']='windows'
os.environ['APPDATA']=tempfile.mkdtemp(prefix='spaceweekly-popup-')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ui.theme import ThemeManager
from ui.widgets.combo_box import RoundedComboBox
app=QApplication([])
theme=ThemeManager(app)
for mode in ['light','dark']:
    theme.apply(mode)
    for editable in [False,True]:
        combo=RoundedComboBox()
        combo.setEditable(editable)
        combo.addItems(['One','Two','Three'])
        combo.resize(300,42)
        combo.show()
        for _ in range(2):
            combo.showPopup()
            QTest.qWait(50)
            popup=combo.view().window()
            assert combo.view().geometry()==popup.rect(), 'Native menu strips surround the list'
            assert not popup.mask().isEmpty(), 'Popup must retain rounded corners'
            view = combo.view()
            rect = view.visualRect(view.currentIndex())
            point = QPoint(rect.right() - 12, rect.center().y())
            pixel = QPoint(round(point.x() * view.devicePixelRatioF()), round(point.y() * view.devicePixelRatioF()))
            QTest.mouseMove(view.viewport(), QPoint(-1, -1))
            QTest.qWait(50)
            normal = view.viewport().grab().toImage().pixelColor(pixel)
            QTest.mouseMove(view.viewport(), rect.center())
            QTest.qWait(50)
            hovered = view.viewport().grab().toImage().pixelColor(pixel)
            assert normal == hovered, 'Selected row changes color when hovered'
            assert normal.name() == theme.theme.colors['accent'], 'Selected row loses theme accent'
            combo.hidePopup()
        combo.close()
print('Windows popup layout passed')
