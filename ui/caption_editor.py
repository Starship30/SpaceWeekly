"""Subtitle text changes retain unit identity, audio and aligned sample positions."""
from PySide6.QtWidgets import QTableWidget,QTableWidgetItem,QDialog,QVBoxLayout,QLabel,QDialogButtonBox,QCheckBox
from PySide6.QtCore import Qt
from ui.component import configure_table

class CaptionTable(QTableWidget):
    def __init__(self,segment):
        super().__init__(0,2);self.segment=segment
        self.units=segment.get('units') or [segment]
        configure_table(self);self.setHorizontalHeaderLabels(['朗读原文（只读）','字幕文字（可留空）'])
        self.setRowCount(len(self.units))
        for n,u in enumerate(self.units):
            original=QTableWidgetItem(u.get('text',segment['spoken']));original.setFlags(original.flags()&~Qt.ItemFlag.ItemIsEditable)
            self.setItem(n,0,original);self.setItem(n,1,QTableWidgetItem(u.get('screen',segment['spoken']) if u is not segment else segment.get('caption',segment['spoken'])))
        self.horizontalHeader().setStretchLastSection(True);self.setColumnWidth(0,250);self.setMinimumHeight(130)
    def save(self):
        changes=[]
        for n,u in enumerate(self.units):
            key='caption' if u is self.segment else 'screen';text=self.item(n,1).text()
            if text!=u.get(key):changes.append(dict(unit_id=u.get('id'),before=u.get(key),after=text));u[key]=text
        if changes:self.segment.setdefault('caption_history',[]).append(changes)

def edit_news_captions(owner):
    draft=owner.voice_draft()
    if not draft:return
    dialog=QDialog(owner);dialog.setWindowTitle('编辑本条字幕');dialog.resize(760,450);layout=QVBoxLayout(dialog)
    layout.addWidget(QLabel('仅修改字幕，不修改口播、音频或已有对齐时间。'))
    table=CaptionTable(draft['segments'][0]);layout.addWidget(table)
    overlay=QCheckBox('在新闻画面上直接显示字幕');overlay.setChecked(draft.get('caption_overlay',False));layout.addWidget(overlay)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存字幕');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    if dialog.exec()==QDialog.DialogCode.Accepted:
        table.save();draft['caption_overlay']=overlay.isChecked();owner.changed.emit()
