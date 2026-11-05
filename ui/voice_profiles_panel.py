"""Edit and audition local reference voices through the shared voice worker."""
from pathlib import Path
import shutil
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,QPushButton,QLabel,QFileDialog,QMessageBox
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from resources import resource_path
from services import voice_profiles as profiles


class VoiceProfilesPanel(QWidget):
    def __init__(self,setup):
        super().__init__(setup)
        self.setup=setup;self.preview=None;self.original=''
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.voices=QComboBox();form.addRow('参考音色',self.voices)
        self.name=QLineEdit();self.name.setMaxLength(48);form.addRow('名称',self.name)
        audio=QHBoxLayout();self.audio=QLabel('尚未选择录音');audio.addWidget(self.audio,1)
        pick=QPushButton('导入录音');pick.clicked.connect(self.choose_audio);audio.addWidget(pick)
        form.addRow('录音',audio)
        self.transcript=QPlainTextEdit();self.transcript.setMaximumHeight(100)
        self.transcript.setPlaceholderText('填写录音里实际说出的文字')
        form.addRow('对应原文',self.transcript)
        template=QLabel('录制参考文案\n'+profiles.REFERENCE_TEXT);template.setWordWrap(True)
        template.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(template)
        buttons=QHBoxLayout();self.buttons=[pick]
        for label,action in (('生成试听',self.audition),('重播试听',self.play_sample),('保存音色',self.save),('删除音色',self.remove)):
            button=QPushButton(label);button.clicked.connect(lambda _=False,fn=action:setup.guard(fn))
            buttons.addWidget(button);self.buttons.append(button)
        _,_,self.play_button,_,self.remove_button=self.buttons
        buttons.addStretch();layout.addLayout(buttons)
        self.hint=QLabel('参考录音和原文保存在本机。录音建议清晰、单人、3 至 30 秒。首次使用请在自动配置中准备参考音色组件。')
        self.hint.setWordWrap(True);layout.addWidget(self.hint);layout.addStretch(1)
        self.voices.currentIndexChanged.connect(self.select)
        self.transcript.textChanged.connect(self.invalidate)
        self.refresh()

    def invalidate(self):
        self.preview=None
        self.play_button.setEnabled(False)

    def refresh(self,identifier=None):
        self.voices.blockSignals(True);self.voices.clear();self.voices.addItem('新建音色',None)
        for record in profiles.list_profiles():self.voices.addItem(record['name'],record['id'])
        self.voices.setCurrentIndex(max(0,self.voices.findData(identifier)));self.voices.blockSignals(False);self.select()

    def select(self,*_):
        identifier=self.voices.currentData()
        self.saved=profiles.load(identifier) if identifier else {}
        self.remove_button.setEnabled(bool(self.saved))
        self.name.setText(self.saved.get('name',''));self.transcript.setPlainText(self.saved.get('text',''))
        self.original=self.saved.get('original','')
        self.audio.setText(Path(self.original).name if self.original else '尚未选择录音')
        self.invalidate()

    def choose_audio(self):
        path,_=QFileDialog.getOpenFileName(self,'选择参考录音','','录音 (*.wav *.mp3 *.m4a *.flac *.ogg);;所有文件 (*)')
        if path:self.original=path;self.audio.setText(Path(path).name);self.invalidate()

    def busy(self,running):
        for widget in (*self.buttons,self.voices,self.name,self.transcript):widget.setEnabled(not running)
        self.play_button.setEnabled(not running and bool(self.preview))
        self.remove_button.setEnabled(not running and bool(self.saved))

    def audition(self):
        text=self.transcript.toPlainText().strip();name=self.name.text().strip()
        if not self.original or not text or not 1<=len(name)<=48 or len(text)>2000:
            raise ValueError('请选择录音，填写实际录音原文及音色名称')
        model=Path(self.saved['model']) if self.saved else profiles.base_model(self.setup.root.text().strip())
        if not (model/'spaceweekly-model.json').is_file():
            self.setup.tabs.setCurrentIndex(0)
            raise ValueError('请先在自动配置中准备参考音色组件')
        source=Path(self.original);r=self.setup.workspace.roundup
        if not source.is_file() or not 0<source.stat().st_size<=64*1024*1024:
            raise ValueError('请选择小于 64 MB 的非空参考录音')
        settings=r.voice_settings()['tts']
        if not settings['python'] or not Path(settings['python']).is_file():raise ValueError('请先配置可用的语音环境')
        identifier=self.voices.currentData()
        def action(token,_progress):
            from ai.speech import SpeechRequest,generate_speech
            from services.roundup_audio import file_hash
            target=resource_path('cache','voice_samples',uuid4().hex)
            original=target/('original'+source.suffix.lower())
            target.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,original)
            reference=profiles.decode(original,target/'reference.wav',token)
            r.tts_provider.configure(settings)
            request=SpeechRequest(profiles.REFERENCE_TEXT,model=str(model),reference_audio=str(reference),
                reference_text=text,reference_sha256=file_hash(reference))
            sound=generate_speech(request,target,provider=r.tts_provider,cancel_check=token.check)
            return dict(id=identifier,text=text,model=str(model),reference=reference,original=original,audio=sound.path)
        self.setup.start('reference',action)

    def ready(self,result):
        self.preview=result;self.hint.setText('试听已生成，可以保存此音色。')
        self.play_button.setEnabled(True)
        self.play_sample()

    def play_sample(self):
        if not self.preview or not Path(self.preview['audio']).is_file():
            raise ValueError('请先为当前录音和对应原文生成试听')
        self.setup.workspace.roundup._play(self.preview['audio'])

    def save(self):
        if self.setup.workspace.busy():raise ValueError('请先等待正在运行的任务结束')
        identifier=self.voices.currentData()
        if self.preview:
            p=self.preview;identifier=profiles.save(self.name.text(),p['text'],p['model'],p['original'],p['reference'],identifier)
        elif identifier and self.original==self.saved['original'] and self.transcript.toPlainText().strip()==self.saved['text']:
            profiles.rename(identifier,self.name.text())
        else:raise ValueError('请先生成试听，确认当前录音与文字')
        self.refresh(identifier);self.setup.workspace.roundup.refresh_speakers()
        self.setup.workspace._voice_refresh()
        self.hint.setText('已保存。可在新闻和发射配音的音色菜单中选择。')

    def remove(self):
        if self.setup.workspace.busy():raise ValueError('请先等待正在运行的任务结束')
        identifier=self.voices.currentData()
        if not identifier:return
        if QMessageBox.question(self,'删除参考音色','删除此音色的参考录音与对应文字？已导出的作品仍可播放。')!=QMessageBox.StandardButton.Yes:return
        profiles.delete(identifier);self.refresh();self.setup.workspace.roundup.refresh_speakers()
        self.setup.workspace._voice_refresh()
