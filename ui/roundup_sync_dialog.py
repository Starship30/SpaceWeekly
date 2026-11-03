"""Lightweight unit association and timing calibration, using the shared draft."""
from copy import deepcopy
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,
    QTableWidgetItem,QHeaderView,QListWidget,QListWidgetItem,QPlainTextEdit,QDialogButtonBox,QMessageBox)
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from ui.component import configure_table
from services import roundup_sync as sync

LABELS={'group':'国家 / 公司分组','task':'单任务详情','summary':'分组总结','transition':'过渡','news':'新闻内容'}


class RoundupSyncDialog(QDialog):
    def __init__(self,workspace):
        super().__init__(workspace)
        self.workspace=workspace;self.r=workspace.roundup
        self.draft=workspace.media_draft();self.news=self.draft.get('content_type')=='news'
        self.segment=self.draft['segments'][0 if self.news else self.r.segment_list.currentRow()]
        self.setWindowTitle('同步校准');self.resize(980,580)
        layout=QVBoxLayout(self);self.info=QLabel();self.info.setWordWrap(True);layout.addWidget(self.info)
        self.table=QTableWidget(0,7);configure_table(self.table)
        self.table.setHorizontalHeaderLabels(['讲解单元','关联新闻' if self.news else '关联任务','展示','开始（秒）','结束（秒）','状态','屏幕文字'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6,QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False);layout.addWidget(self.table,1)
        self.table.cellDoubleClicked.connect(lambda row,col:self.edit_unit() if col not in (3,4) else None)
        actions=QHBoxLayout()
        for text,action in [('重建单元',self.rebuild),('修正关联 / 分界',self.edit_unit),('自动对齐本段',self.align),
                            ('试听附近',self.listen),('恢复自动结果',self.restore),('保存校准',self.save)]:
            button=QPushButton(text);button.clicked.connect(lambda _=False,fn=action:workspace.guard(fn));actions.addWidget(button)
        layout.addLayout(actions)
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        close.rejected.connect(self.reject);layout.addWidget(close)
        self.refresh()

    def refresh(self):
        self.schedule=sync.segment_schedule(self.draft,self.segment)
        self.info.setText(self.schedule['message']+'。时间以本段实际音频为准；关联的确认只针对画面，不代表背景事实已核实。')
        from services.launch_research import display_names
        names=({key:item['title'] for key,item in self.draft['news_items'].items()} if self.news else
            {i['ll2_id']:display_names(i)['name'] for i in self.draft['snapshot']['items']})
        timed={u['id']:u for u in self.schedule['units']}
        self.table.setRowCount(0)
        for row,u in enumerate(self.segment.get('units',[])):
            self.table.insertRow(row);span=timed.get(u['id'])
            values=[u['text'],names.get(u.get('news_id'),'关联已失效') if self.news else '、'.join(names.get(i,'关联已失效') for i in u['task_ids']) or '未关联',LABELS[u['display']],
                f"{span['start_sample']/sync.RATE:.3f}" if span else '', f"{span['end_sample']/sync.RATE:.3f}" if span else '',
                '人工校准' if span and span['status']=='manual' else '自动对齐' if span else
                '待关联' if u.get('association_status')!='confirmed' else '待音频' if not self.schedule['audio'] else '待校准',u['screen']]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setToolTip(value)
                if col not in (3,4):cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row,col,cell)
        for col,width in ((1,130),(2,130),(3,90),(4,90),(5,100)):self.table.setColumnWidth(col,width)

    def changed(self):
        self.workspace._stop_preview();self.workspace._frame_key=None
        self.workspace._voice_refresh();self.refresh()

    def rebuild(self):
        if self.segment.get('units') and QMessageBox.question(self,'重建讲解单元',
            '按当前最终朗读稿的句子重建关联，旧人工时间会标记待复查。继续？')!=QMessageBox.StandardButton.Yes:return
        self.workspace.checkpoint();sync.rebuild_units(self.segment);self.changed()

    def edit_unit(self):
        row=self.table.currentRow()
        if row<0:raise ValueError('请选择讲解单元')
        unit=self.segment['units'][row]
        if self.segment.get('units_basis')!=sync.digest(sync.narration_text(self.segment)):
            raise ValueError('朗读稿已变化，请先重建讲解单元')
        dialog=QDialog(self);dialog.setWindowTitle('修正讲解单元');dialog.resize(620,540);layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('朗读原文（只读；可将光标放在需要拆分的位置）'))
        original=QPlainTextEdit(unit['text']);original.setReadOnly(True);layout.addWidget(original)
        display=QComboBox()
        for value,label in LABELS.items():
            if self.news==(value=='news'):display.addItem(label,value)
        display.setCurrentIndex(display.findData(unit['display']));layout.addWidget(display)
        tasks=QListWidget()
        from services.launch_research import display_names
        for item in self.draft.get('snapshot',{}).get('items',[]):
            if item['ll2_id'] not in self.segment['task_ids']:continue
            cell=QListWidgetItem(display_names(item)['name']);cell.setData(Qt.ItemDataRole.UserRole,item['ll2_id'])
            cell.setFlags(cell.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            cell.setCheckState(Qt.CheckState.Checked if item['ll2_id'] in unit['task_ids'] else Qt.CheckState.Unchecked);tasks.addItem(cell)
        if not self.news:layout.addWidget(tasks)
        else:layout.addWidget(QLabel('关联当前新闻：'+self.draft['news_items'][self.draft['news_id']]['title']))
        layout.addWidget(QLabel('屏幕文字 / 字幕'));screen=QPlainTextEdit(unit['screen']);layout.addWidget(screen)
        split=QPushButton('在光标处分为两个单元')
        def split_unit():
            position=original.textCursor().position()
            if not 0<position<len(unit['text']):raise ValueError('请将光标放在原文中间的分界处')
            self.workspace.checkpoint();other=deepcopy(unit);other.update(id=uuid4().hex,start=unit['start']+position,
                text=unit['text'][position:],screen=unit['text'][position:],association_status='needs_review')
            unit.update(end=other['start'],text=unit['text'][:position],screen=unit['text'][:position])
            self.segment['units'].insert(row+1,other);dialog.reject();self.changed()
        split.clicked.connect(lambda:self.workspace.guard(split_unit));layout.addWidget(split)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存关联');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        ids=[tasks.item(n).data(Qt.ItemDataRole.UserRole) for n in range(tasks.count()) if tasks.item(n).checkState()==Qt.CheckState.Checked]
        kind=display.currentData()
        if kind=='task' and len(ids)!=1 or kind in ('group','summary') and not ids:raise ValueError('单任务须选一项，分组与总结须选关联任务')
        if not screen.toPlainText().strip():raise ValueError('请填写屏幕文字 / 字幕')
        self.workspace.checkpoint();unit.update(task_ids=ids,display=kind,screen=screen.toPlainText(),association_status='confirmed')
        self.changed()

    def align(self):
        self.accept();self.workspace.align_roundup(self.segment['id'])

    def listen(self):
        row=self.table.currentRow()
        if row<0:raise ValueError('请选择讲解单元')
        value=self.table.item(row,3).text()
        if not value:raise ValueError('请先对齐或填写本句开始时间')
        seconds=max(0,float(value)-.8)
        self.accept();self.workspace.audition_sync(self.segment['id'],seconds,seconds+5)

    def restore(self):
        self.workspace.checkpoint();self.segment.pop('sync_manual',None);self.changed()

    def save(self):
        try:times=[(float(self.table.item(n,3).text()),float(self.table.item(n,4).text())) for n in range(self.table.rowCount())]
        except ValueError:raise ValueError('请为每个单元填写有效的开始和结束秒数') from None
        self.workspace.checkpoint()
        sync.save_manual(self.draft,self.segment,times);self.workspace.save();self.changed()
