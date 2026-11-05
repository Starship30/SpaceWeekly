"""One persistent voice setup entry; deployment runs through the existing cancellable worker."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil

from PySide6.QtCore import Signal,QTimer,QUrl,Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QLabel,
    QPushButton,QProgressBar,QPlainTextEdit,QFileDialog,QMessageBox)
from PySide6.QtGui import QDesktopServices
from ui.component import SegmentedTabs
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from ui.launch_roundup_dialog import RoundupWorker
from services import voice_deployment as service
from services.voice_storage import default_root,remove_managed,inside,OWNER
from services.voice_download import redact

STATES={'unconfigured':'尚未配置','configuring':'配置中','cancelled':'已取消','interrupted':'已中断',
    'failed':'配置失败','needs_check':'需要检查','ready':'已可用'}


class VoiceSetupWorker(RoundupWorker):
    progress=Signal(object)


class VoiceSetupPanel(QWidget):
    changed=Signal()

    def __init__(self,workspace,existing):
        super().__init__(workspace);self.workspace=workspace;self.worker=None;self.result=None;self.request=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.tabs=SegmentedTabs();layout.addWidget(self.tabs)
        page=QWidget();body=QVBoxLayout(page);form=QFormLayout();body.addLayout(form)
        self.device=QComboBox();self.device.addItem('NVIDIA 显卡','cu128');self.device.addItem('CPU（可能较慢）','cpu')
        self.quality=QComboBox();self.quality.addItem('轻量配音（推荐）','lite');self.quality.addItem('标准配音（占用更多空间）','standard')
        self.source=QComboBox();self.source.addItem('ModelScope','modelscope');self.source.addItem('Hugging Face','huggingface')
        self.root=QLineEdit(str(default_root()));browse=QPushButton('选择位置');browse.clicked.connect(self.choose_root);self.browse=browse
        row=QHBoxLayout();row.addWidget(self.root,1);row.addWidget(browse)
        form.addRow('运行方案',self.device);form.addRow('配音方案',self.quality);form.addRow('模型来源',self.source);form.addRow('保存位置',row)
        self.summary=QLabel('先检测设备与空间，再开始配置。');self.summary.setWordWrap(True);body.addWidget(self.summary)
        actions=QHBoxLayout();self.detect_button=QPushButton('检测设备与空间');self.detect_button.clicked.connect(lambda:self.guard(self.inspect))
        self.start_button=QPushButton('开始自动配置');self.start_button.setObjectName('PrimaryButton');self.start_button.clicked.connect(lambda:self.guard(self.deploy))
        self.start_button.setEnabled(False)
        actions.addWidget(self.detect_button);actions.addWidget(self.start_button);actions.addStretch();body.addLayout(actions)
        base=QPushButton('准备参考音色组件（约 2.52 GB，Hugging Face）')
        base.clicked.connect(lambda:self.guard(self.prepare_reference_model));body.addWidget(base,0)
        body.addStretch(1)
        self.tabs.addTab(page,'自动配置')
        existing_page=QWidget();existing_layout=QVBoxLayout(existing_page)
        use=QPushButton('检查并使用这些设置');use.clicked.connect(lambda:self.guard(self.use_existing));existing_layout.addWidget(use)
        self.operation_buttons=[use,base]
        existing_layout.addWidget(existing);self.tabs.addTab(existing_page,'使用已有环境')
        maintenance=QWidget();maintenance_layout=QVBoxLayout(maintenance)
        note=QLabel('检查与修复采用“自动配置”中选定的方案。重建时保留上次配置，待新环境验证通过后切换。')
        note.setWordWrap(True);maintenance_layout.addWidget(note)
        controls=QHBoxLayout()
        for text,action in [('运行检查',lambda:self.deploy(check=True)),('检查与修复',lambda:self.deploy(repair=True)),('试听检查音频',self.listen),('导出诊断',self.export_diagnostics)]:
            button=QPushButton(text);button.clicked.connect(lambda _=False,fn=action:self.guard(fn));controls.addWidget(button);self.operation_buttons.append(button)
            if text=='检查与修复':button.setObjectName('PrimaryButton')
        controls.addStretch()
        maintenance_layout.addLayout(controls)
        self.environments=QComboBox();environment_row=QHBoxLayout();environment_row.addWidget(QLabel('要移除的环境'))
        environment_row.addWidget(self.environments,1);maintenance_layout.addLayout(environment_row)
        cleanup=QHBoxLayout()
        for text,action in [('清理下载缓存',self.clean_cache),('移除所选应用环境',self.remove_environment)]:
            button=QPushButton(text);button.setObjectName('TextButton');button.clicked.connect(lambda _=False,fn=action:self.guard(fn));cleanup.addWidget(button);self.operation_buttons.append(button)
        cleanup.addStretch();maintenance_layout.addLayout(cleanup);maintenance_layout.addStretch(1)
        self.tabs.addTab(maintenance,'检查与修复')
        from ui.voice_profiles_panel import VoiceProfilesPanel
        self.my_voices=VoiceProfilesPanel(self);self.tabs.addTab(self.my_voices,'我的音色')
        self.status=QLabel('尚未配置');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.hide();layout.addWidget(self.progress)
        bottom=QHBoxLayout();self.cancel=QPushButton('取消配置');self.cancel.clicked.connect(self.cancel_work);self.cancel.hide();bottom.addWidget(self.cancel)
        self.return_button=QPushButton('返回配音页面');self.return_button.clicked.connect(lambda:workspace.media_route(0));bottom.addWidget(self.return_button)
        self.details_toggle=QPushButton('展开详情与日志');self.details_toggle.setCheckable(True);bottom.addWidget(self.details_toggle);bottom.addStretch();layout.addLayout(bottom)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(190);self.details.hide();layout.addWidget(self.details)
        self.details_toggle.toggled.connect(self.details.setVisible)
        self._plan=None;self._plan_text='';self._loaded_root=None
        for control in (self.device,self.quality,self.source):control.currentIndexChanged.connect(self.invalidate)
        self.root.textChanged.connect(self.invalidate)
        self.root.editingFinished.connect(lambda:self.guard(self.root_finished))
        self.refresh_state(load_request=True)

    def guard(self,action):
        try:action()
        except (OSError,ValueError,RuntimeError,KeyError) as error:self.status.setText(redact(str(error)))

    def invalidate(self,*_):
        self._plan=None;self.start_button.setEnabled(False);self.summary.setText('方案已变更，请检测设备与空间后开始。')
        self.status.setText('所选方案待检查；原有可用配置保留')

    def choose_root(self):
        directory=QFileDialog.getExistingDirectory(self,'选择专用语音目录',self.root.text())
        if directory:self.root.setText(directory);self.guard(self.root_finished)

    def root_finished(self):
        if Path(self.root.text().strip()).expanduser().resolve()!=self._loaded_root:
            self._plan_text='';self.refresh_state(load_request=True)

    def values(self):
        return dict(root=self.root.text().strip(),device=self.device.currentData(),quality=self.quality.currentData(),source=self.source.currentData())

    def start(self,kind,action):
        if self.worker or self.workspace.worker or self.workspace.roundup.worker:raise ValueError('请先等待或取消当前语音任务')
        worker=VoiceSetupWorker(None,{},self)
        def run(data,token):
            if kind not in ('detect','reference'):self.workspace.roundup.tts_provider.unload()
            return action(token,worker.progress.emit)
        worker.action=run;worker.progress.connect(self.on_progress);worker.finished.connect(lambda:self.finished(kind,worker))
        self.worker=worker;self.cancel.show();self.progress.setRange(0,0);self.progress.show()
        self.status.setText('正在检测' if kind=='detect' else '正在配置，可继续查看其他页面')
        self.busy_controls(True);worker.start();self.changed.emit()

    def busy_controls(self,busy):
        for widget in (self.root,self.browse,self.device,self.quality,self.source,self.start_button,self.detect_button,*self.operation_buttons):widget.setEnabled(not busy)
        self.my_voices.busy(busy)

    def prepare_reference_model(self):
        root=self.root.text().strip()
        if not root:raise ValueError('请选择参考音色组件的保存位置')
        self.start('base',lambda token,emit:service.VoiceDeployment(root,token,emit).prepare_base())

    def inspect(self):
        options=self.values()
        self.start('detect',lambda token,emit:service.plan(**options))

    def show_plan(self,value):
        self._plan=value;hardware=value['hardware'];device=self.device.currentData()
        memory=hardware['memory'];gpus=hardware['gpus']
        graphics='；'.join(g['name'] for g in gpus) or '未检测到支持自动配置的 NVIDIA 显卡'
        if device=='cpu':graphics=hardware.get('cpu_name','处理器（CPU）')
        self.summary.setText(f"设备：{graphics}\n内存：{memory.get('total_bytes',0)/service.GIB:.1f} GiB，当前可用 {memory.get('available_bytes',0)/service.GIB:.1f} GiB\n"
            f"配音与对齐模型合计 {value['model_bytes']/service.GIB:.2f} GiB；环境占用约 {value['environment_estimate_bytes']/service.GIB:.0f} GiB（估算）\n"
            f"磁盘剩余 {hardware['disk_free_bytes']/service.GIB:.1f} GiB；配置预留约 {value['required_free_bytes']/service.GIB:.1f} GiB。\n"
            +('CPU 运行可能较慢，具体耗时以本机检查为准。' if device=='cpu' else '配音与对齐分阶段加载；CUDA 是否可用以实际推理检查为准。'))
        self._plan_text=('方案版本：'+value['version']+'\n独立 Python：'+value['python']+'\n'+
            '环境来源：Astral uv / Python、PyPI、PyTorch 官方轮子；模型来源：'+self.source.currentText()+'\n'+
            json.dumps(dict(models={r:dict(repo=m['repo'],revision=m['revision'],bytes=m['download_bytes']) for r,m in value['models'].items()},hardware=hardware),ensure_ascii=False,indent=2))
        self.details.setPlainText(self._plan_text+'\n\n'+self.saved_log())
        self.start_button.setEnabled(hardware['supported'] and hardware['disk_free_bytes']>=value['required_free_bytes'] and (device=='cpu' or bool(gpus)))
        if not hardware['supported']:self.status.setText('自动配置目前仅支持 Windows x64；已有环境仍可检查')
        elif device!='cpu' and not gpus:self.status.setText('未检测到支持的显卡，请选择 CPU 或使用已有环境')
        elif hardware['disk_free_bytes']<value['required_free_bytes']:self.status.setText('空间不足，请选择其他磁盘')
        else:self.status.setText('检测完成。请确认方案、来源和保存位置后开始配置。')

    def saved_log(self):
        try:return redact((Path(self.root.text().strip())/'deployment.log').read_text(encoding='utf-8',errors='replace'))
        except FileNotFoundError:return ''
        except OSError:return '历史日志暂时不可读，可导出诊断或重新检查保存位置。'

    def deploy(self,check=False,repair=False):
        options=self.values()
        if not self._plan and not check and not repair:
            self.inspect();return
        self.request=options
        self.start('deploy',lambda token,emit:service.VoiceDeployment(options['root'],token,emit).run(
            device=options['device'],quality=options['quality'],source=options['source'],check_only=check))

    def use_existing(self):
        r=self.workspace.roundup;r._sync(validate=False);tts=deepcopy(r.draft['tts']);align=deepcopy(r.draft.get('aligner',{}))
        root=self.root.text().strip()
        self.start('existing',lambda token,emit:service.VoiceDeployment(root,token,emit).run_existing(tts,align))

    def on_progress(self,event):
        if 'activity' in event:
            self.details.appendPlainText(event['activity']);return
        self.status.setText(STATES.get(event.get('status'),event.get('status',''))+' · '+event.get('message',''))
        if event.get('total_bytes'):
            done,total=event['downloaded_bytes'],event['total_bytes']
            self.progress.setRange(0,1000);self.progress.setValue(min(1000,done*1000//total))
            self.progress.setFormat(f'{done/service.GIB:.2f} / {total/service.GIB:.2f} GiB')
        else:self.progress.setRange(0,0);self.progress.setFormat('')

    def finished(self,kind,worker):
        self.worker=None;self.busy_controls(False);self.cancel.hide();self.progress.hide()
        if worker.cancelled:self.status.setText('已取消；缓存与上次有效设置保留，下次会重新检查')
        elif worker.error:
            self.details.appendPlainText(redact(worker.error))
            message=redact(worker.error)
            self.status.setText('未完成：'+message if len(message)<200 else '配置未完成，可重试；详细原因见日志，已有缓存保留')
        elif kind=='detect':self.show_plan(worker.value)
        elif kind=='base':
            self.status.setText('参考音色模型已下载并校验，请生成试听')
            self.tabs.setCurrentWidget(self.my_voices)
        elif kind=='reference':
            self.my_voices.ready(worker.value);self.status.setText('参考音色试听已生成')
        elif kind in ('deploy','existing'):
            self.result=worker.value
            try:self.adopt(worker.value);self.refresh_state()
            except (OSError,ValueError,RuntimeError,KeyError) as error:
                self.details.appendPlainText(redact(str(error)));self.status.setText('运行检查已结束，但配置保存未完成，请重试保存或查看日志')
        else:self.refresh_state()
        self.workspace.task_text.setText('语音配置：'+self.status.text())
        self.changed.emit();worker.deleteLater()

    def adopt(self,value):
        active=value.get('validation',{});r=self.workspace.roundup
        if not any(v.get('ready') for v in active.values()):return
        r._store_segment()
        self.workspace.checkpoint();selected=r.segment_list.currentRow()
        if active.get('tts',{}).get('ready'):
            old_speaker=r.speaker_id();old_style=r.style.text()
            r.draft['tts'].update({k:active['tts'][k] for k in ('python','model','speaker','style','device','timeout')})
            capabilities=active['tts'].get('capabilities',{})
            supported=capabilities.get('speakers',[])
            chosen=next((s for s in supported if s.casefold()==old_speaker.casefold()),None)
            if chosen or old_speaker.startswith('my:'):r.draft['tts']['speaker']=old_speaker
            if capabilities.get('style'):r.draft['tts']['style']=old_style
        if active.get('align',{}).get('ready'):
            r.draft.setdefault('aligner',{}).update({k:active['align'][k] for k in ('python','model','device','language','timeout')})
        r.draft['voice_deployment']=dict(root=self.root.text().strip(),version=value.get('active',{}).get('version'),
            tts_ready=active.get('tts',{}).get('ready',False),align_ready=active.get('align',{}).get('ready',False),verified_at=service.now())
        r._show_draft();r.segment_list.setCurrentRow(selected)
        from resources import resource_path
        path=resource_path('config','roundup_voice.json')
        if path.exists():shutil.copyfile(path,path.with_name('roundup_voice.before-auto-'+service.now().replace(':','-')+'.json'))
        r._save_voice();self.workspace.save();self.workspace._voice_refresh()

    def refresh_state(self,load_request=False):
        try:
            value=service.read_state(self.root.text().strip());self.result=value
            if load_request:
                self._loaded_root=Path(self.root.text().strip()).expanduser().resolve()
                self.details.setPlainText(self._plan_text+'\n\n'+self.saved_log())
                request=value.get('request',{})
                for control,key in ((self.device,'device'),(self.quality,'quality'),(self.source,'source')):
                    index=control.findData(request.get(key))
                    if index>=0:
                        control.blockSignals(True);control.setCurrentIndex(index);control.blockSignals(False)
            message=value.get('message','尚无运行检查记录')
            if len(message)>200:
                self.details.appendPlainText(redact(message));message='上次配置未完成，详细原因见日志；可重新检查后继续'
            self.status.setText(STATES.get(value['status'],value['status'])+' · '+message)
            self.environments.clear();root=Path(self.root.text()).resolve()
            for marker in sorted((root/'environments').glob('*/spaceweekly-environment.json')):
                data=json.loads(marker.read_text(encoding='utf-8'))
                if data.get('owner')==OWNER:
                    role={'tts':'配音环境','align':'对齐环境'}.get(data.get('role'),'应用环境')
                    device='CPU' if marker.parent.name.startswith('cpu-') else 'NVIDIA 显卡'
                    date=datetime.fromtimestamp(marker.stat().st_mtime).strftime('%m月%d日 %H:%M')
                    self.environments.addItem(role+' · '+device+' · '+date,str(marker.parent.relative_to(root)))
                    self.environments.setItemData(self.environments.count()-1,str(marker.parent),Qt.ItemDataRole.ToolTipRole)
        except (OSError,ValueError,KeyError) as error:self.status.setText('需要检查：'+str(error))

    def cancel_work(self):
        if self.worker:self.worker.token.cancel();self.status.setText('正在取消，等待当前操作结束；缓存会保留')

    def listen(self):
        value=service.read_state(self.root.text())
        sound=value.get('validation',{}).get('tts',{}).get('test_audio') or value.get('active',{}).get('tts',{}).get('test_audio')
        if not sound or not Path(sound).is_file():raise ValueError('尚无通过检查的测试音频')
        self.workspace.roundup._play(sound)

    def export_diagnostics(self):
        path,_=QFileDialog.getSaveFileName(self,'导出语音诊断','SpaceWeekly-语音诊断.json','JSON (*.json)')
        if path:service.diagnostics(self.root.text(),path);self.status.setText('诊断已导出，不含凭据')

    def clean_cache(self):
        if QMessageBox.question(self,'清理下载缓存','仅清理应用的下载与安装缓存，保留模型、已安装环境和工作区。继续？')!=QMessageBox.StandardButton.Yes:return
        root=self.root.text()
        self.start('clean',lambda token,emit:[remove_managed(root,name) for name in ('downloads','uv-cache')])

    def remove_environment(self):
        relative=self.environments.currentData()
        if not relative:raise ValueError('请选择应用管理的环境；不能移除用户自行提供的环境')
        root=self.root.text()
        if QMessageBox.question(self,'移除应用环境','将移除 '+str(Path(root)/relative)+'。保留模型和工作区；需要时可重新配置。继续？')!=QMessageBox.StandardButton.Yes:return
        self.start('remove',lambda token,emit:remove_managed(root,relative))
