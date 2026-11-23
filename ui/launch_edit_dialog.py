"""Explicit edition-only field and narration editing with preserved raw material."""
from copy import deepcopy
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,QComboBox,QLabel,QDialogButtonBox,QScrollArea,QWidget
from services import launch_roundup as roundup
from services.launch_display import resolve_launch,overrides,edit_fields
from ui.caption_editor import CaptionTable
from utils.time import parse_datetime


class LaunchEditDialog(QDialog):
    def __init__(self,workspace,item):
        super().__init__(workspace)
        self.workspace,self.item=workspace,item
        self.setWindowTitle('编辑本期发射内容');self.resize(720,700)
        outer=QVBoxLayout(self);hint=QLabel('原始来源单独保留。画面字段独立保存；下方口播修改后需更新对应配音。')
        hint.setWordWrap(True);outer.addWidget(hint)
        area=QScrollArea();area.setWidgetResizable(True);body=QWidget();form=QFormLayout(body);area.setWidget(body);outer.addWidget(area,1)
        names=resolve_launch(item);self.fields={};self.initial={};self.captions=[]
        fields=(('name','任务名称'),('title','视频主标题'),('rocket','完整火箭配置'),('upper_stage','上面级'),
            ('provider','执行机构 / 公司'),('organization_type','机构类型'),('operator_country','机构所属国家'),('group_label','分组标签（可独立修改）'),
            ('manufacturer','制造商'),('customer','任务委托方'),('net','发射时间（含时区）'),('site_name','发射场'),('pad_name','工位'),
            ('payload','载荷名称 / 已确认载荷信息'),('orbit','目标轨道'),('payload_count','数量（无可靠依据留空）'),('recovery','回收结果（未知留空）'),('summary','任务简介（不作为载荷）'))
        for key,label in fields:
            value=names.get(key,'');value='' if value is None else str(value)
            widget=QLineEdit(value);self.fields[key]=widget;self.initial[key]=value;form.addRow(label,widget)
        self.result=QComboBox()
        for label,value in (('成功',3),('失败',4),('部分失败',7),('待确认',None)):
            self.result.addItem(label,value)
        index=self.result.findData(names.get('status_id'))
        self.result.setCurrentIndex(index if index>=0 else 3);self.initial_status=self.result.currentData();form.addRow('发射结果',self.result)
        self.segments=[]
        for segment in workspace.roundup.draft['segments']:
            if item['ll2_id'] in segment['task_ids']:
                edit=QPlainTextEdit(roundup.narration_text(segment));edit.setMinimumHeight(95)
                form.addRow('口播 · '+roundup.segment_title(workspace.roundup.draft,segment),edit);self.segments.append((segment,edit))
                captions=CaptionTable(segment);form.addRow('字幕（不影响配音）',captions);self.captions.append(captions)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存本期修改');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(lambda:workspace.guard(self.save));buttons.rejected.connect(self.reject);outer.addWidget(buttons)

    def save(self):
        if self.workspace.busy():raise ValueError('请等待正在运行的任务结束')
        fields={key:widget.text().strip() for key,widget in self.fields.items()}
        if fields['net'] and not parse_datetime(fields['net']):raise ValueError('请填写有效日期时间及其时区，例如 2026-11-01T08:00:00+08:00')
        count=fields['payload_count']
        if count and (not count.isdecimal() or int(count)>100000):raise ValueError('数量应为非负整数或留空')
        fields['payload_count']=int(count) if count else None
        if any(not editor.toPlainText().strip() for _,editor in self.segments):
            raise ValueError('口播不能留空，可在稿件中取消勾选该段')
        self.workspace.checkpoint();item=self.item
        edits=deepcopy(overrides(item))
        for key,value in fields.items():
            original=self.initial[key]
            if str(value if value is not None else '')!=original:edits[key]=value
        if self.result.currentData()!=self.initial_status:
            edits.update(status_id=self.result.currentData(),status_name=self.result.currentText())
        edit_fields(item,edits)
        for captions in self.captions:captions.save()
        for segment,editor in self.segments:
            text=editor.toPlainText().strip()
            if text!=roundup.narration_text(segment):
                segment.setdefault('spoken_history',[]).append(dict(text=roundup.narration_text(segment),origin='before_edition_edit'))
                segment['spoken']=text;segment.pop('narration',None);segment['edited']=True
        self.workspace.roundup._show_tasks()
        self.workspace.roundup._show_segments(self.workspace.roundup.segment_list.currentRow())
        self.workspace._frame_key=self.workspace._timeline_key=None;self.workspace._launch_detail();self.workspace._changed();self.accept()
