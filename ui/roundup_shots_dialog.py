"""Optional group cards and diagnostics; edits never alter the narration clock."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QCheckBox,QLabel,QPushButton,
    QTableWidget,QTableWidgetItem,QHeaderView,QDialogButtonBox,QDoubleSpinBox)

from services.roundup_sync import build_timeline,closing_hold
from tools.roundup_visuals import group_cues,shot_plan,visual_settings
from ui.component import SegmentedTabs,configure_table


class RoundupShotsDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.production=parent;self.draft=parent.roundup.draft
        self.timeline=build_timeline(self.draft);self.options=visual_settings(self.draft);self.history=[]
        self.cues=group_cues(self.draft,self.timeline)
        self.setWindowTitle('镜头编排');self.resize(860,600)
        layout=QVBoxLayout(self)
        self.enabled=QCheckBox('显示独立的国家或公司分组过场');layout.addWidget(self.enabled)
        hint=QLabel('默认关闭独立标题页。旁白进入下一分组时，选框下滑并更新该组摘要。分组过场设置保留原配音时点。')
        hint.setWordWrap(True);layout.addWidget(hint)
        hold_row=QHBoxLayout();hold_row.addWidget(QLabel('发射部分收尾停留'))
        self.hold=QDoubleSpinBox();self.hold.setRange(1,2);self.hold.setDecimals(2);self.hold.setSingleStep(.1)
        self.hold.setSuffix(' 秒');self.hold.setValue(closing_hold(self.draft));self._last_hold=self.hold.value()
        hold_row.addWidget(self.hold);hold_row.addStretch();layout.addLayout(hold_row)
        tabs=SegmentedTabs();layout.addWidget(tabs,1)
        self.groups=QTableWidget(0,3);configure_table(self.groups)
        self.groups.setHorizontalHeaderLabels(['保留过场','国家或公司','讲解位置'])
        self.groups.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.groups.setColumnWidth(0,100);self.groups.setColumnWidth(2,180)
        tabs.addTab(self.groups,'分组过场')
        self.shots=QTableWidget(0,5);configure_table(self.shots)
        self.shots.setHorizontalHeaderLabels(['起止时间','时长','画面','关联任务','检查'])
        self.shots.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        for n,width in ((0,160),(1,100),(2,100),(4,130)):self.shots.setColumnWidth(n,width)
        tabs.addTab(self.shots,'镜头检查')
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        actions=QHBoxLayout();self.undo=QPushButton('撤销调整');self.defaults=QPushButton('恢复默认')
        actions.addWidget(self.undo);actions.addWidget(self.defaults);actions.addStretch(1);layout.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('应用画面编排')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.enabled.toggled.connect(self.edit);self.groups.itemChanged.connect(self.edit)
        self.undo.clicked.connect(self.undo_edit);self.defaults.clicked.connect(self.reset)
        self.hold.valueChanged.connect(self.edit_hold)
        self.render()

    def render(self):
        self.enabled.blockSignals(True);self.enabled.setChecked(self.options['group_cards']);self.enabled.blockSignals(False)
        self.groups.blockSignals(True);self.groups.setRowCount(len(self.cues))
        for n,cue in enumerate(self.cues):
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked if cue['key'] in self.options['removed_groups'] else Qt.CheckState.Checked)
            self.groups.setItem(n,0,check)
            for col,text in ((1,cue['label']),(2,f"{cue['start_seconds']:.2f}—{cue['end_seconds']:.2f} 秒")):
                item=QTableWidgetItem(text);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.groups.setItem(n,col,item)
        self.groups.blockSignals(False);self.undo.setEnabled(bool(self.history))
        view=dict(self.draft,visuals=self.options,closing_hold_seconds=self.hold.value());table=shot_plan(view,build_timeline(view))
        from services.launch_research import display_names
        names={i['ll2_id']:display_names(i)['name'] for i in self.draft['snapshot']['items']}
        self.shots.setRowCount(len(table['shots']))
        for n,shot in enumerate(table['shots']):
            scene=shot['scene'];kind={'group':'分组过场','task':'任务详情','summary':'分组摘要' if scene['active_ids'] else '本期概览'}[scene['display']]
            values=[f"{shot['start_seconds']:.2f}—{shot['end_seconds']:.2f}",f"{shot['duration_seconds']:.2f} 秒",kind,
                '、'.join(names[i] for i in scene['active_ids']), '不足3秒，需检查' if n in table['short_shots'] else '']
            for col,text in enumerate(values):
                item=QTableWidgetItem(text);item.setToolTip(text);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                self.shots.setItem(n,col,item)
        self.summary.setText(f"{len(table['shots'])} 个画面，{table['transition_count']} 次切换；{len(table['short_shots'])} 个不足3秒。"
            '阈值只用于检查，不延长语音；无音频部分仍为预览时间。')

    def edit(self,*_):
        removed=set(self.options['removed_groups'])
        for n,cue in enumerate(self.cues):
            if self.groups.item(n,0).checkState()==Qt.CheckState.Checked:removed.discard(cue['key'])
            else:removed.add(cue['key'])
        value=dict(group_cards=self.enabled.isChecked(),removed_groups=sorted(removed))
        if value!=self.options:self.history.append((deepcopy(self.options),self._last_hold));self.options=value;self.render()

    def edit_hold(self,value):
        self.history.append((deepcopy(self.options),self._last_hold))
        self._last_hold=value;self.render()

    def restore_options(self,options,hold):
        self.options=options;self._last_hold=hold
        self.hold.blockSignals(True);self.hold.setValue(hold);self.hold.blockSignals(False);self.render()

    def undo_edit(self):
        if self.history:self.restore_options(*self.history.pop())

    def reset(self):
        self.history.append((deepcopy(self.options),self._last_hold));self.restore_options(visual_settings({}),1.5)

    def apply(self):
        if self.options!=visual_settings(self.draft) or self.hold.value()!=closing_hold(self.draft):
            self.production.checkpoint();self.draft['visuals']=deepcopy(self.options)
            self.draft['closing_hold_seconds']=self.hold.value()
            self.production._stop_preview();self.production._frame_key=None;self.production._changed()
        self.accept()
