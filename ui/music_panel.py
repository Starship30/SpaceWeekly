"""Edition playlist controls and local audio audition for the production workspace."""
from pathlib import Path

from PySide6.QtCore import QSignalBlocker,QUrl,Qt,Signal
from PySide6.QtWidgets import (QCheckBox,QDialog,QDialogButtonBox,QDoubleSpinBox,QFileDialog,QFormLayout,
    QHBoxLayout,QLabel,QListWidget,QListWidgetItem,QPushButton,QSizePolicy,QSlider,QVBoxLayout,QWidget)

from services.episode_music import AUDIO_FILTER,settings
from ui.widgets.combo_box import RoundedComboBox


class MusicPlaylist(QWidget):
    def __init__(self,edition,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        notice=QLabel('当前没有内置音乐。可导入本地音频，拖动曲目可调整播放顺序。')
        notice.setObjectName('MutedText');notice.setWordWrap(True);layout.addWidget(notice)
        self.list=QListWidget();self.list.setMaximumHeight(122)
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove);layout.addWidget(self.list)
        for path in edition.get('music',[]):self._add(path)
        row=QHBoxLayout()
        add=QPushButton('添加音频（可多选）');add.clicked.connect(self._import);row.addWidget(add)
        first=QPushButton('设为起始曲目');first.clicked.connect(self._move_first);row.addWidget(first)
        remove=QPushButton('移除选中');remove.clicked.connect(self._remove);row.addWidget(remove)
        row.addStretch();layout.addLayout(row)
        self.list.currentRowChanged.connect(lambda index:remove.setEnabled(index>=0))
        self.list.currentRowChanged.connect(lambda index:first.setEnabled(index>0))
        first.setEnabled(False);remove.setEnabled(False)
        self.options=settings(edition)
        form=QFormLayout()
        self.fades={}
        for key,label in (('fade_in','淡入'),('fade_out','淡出')):
            control=QDoubleSpinBox();control.setRange(0,30);control.setDecimals(1);control.setSingleStep(.1)
            control.setSuffix(' 秒');control.setValue(self.options[key]);form.addRow(label,control);self.fades[key]=control
        self.loop=QCheckBox('循环播放整个列表');self.loop.setChecked(self.options['loop']);form.addRow(self.loop)
        layout.addLayout(form)

    def _add(self,path):
        item=QListWidgetItem(Path(path).name);item.setToolTip(path)
        item.setData(Qt.ItemDataRole.UserRole,path);self.list.addItem(item)

    def _import(self):
        paths=QFileDialog.getOpenFileNames(self,'导入背景音乐','',AUDIO_FILTER)[0]
        existing=set(self.paths())
        for path in paths:
            resolved=str(Path(path).resolve())
            if resolved not in existing:self._add(resolved);existing.add(resolved)
        if self.list.count() and self.list.currentRow()<0:self.list.setCurrentRow(0)

    def _move_first(self):
        row=self.list.currentRow()
        if row>0:self.list.insertItem(0,self.list.takeItem(row));self.list.setCurrentRow(0)

    def _remove(self):
        if self.list.currentRow()>=0:self.list.takeItem(self.list.currentRow())

    def paths(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def values(self):
        return dict(music=self.paths(),music_settings={**self.options,
                    **{key:control.value() for key,control in self.fades.items()},
                    'loop':self.loop.isChecked()})


class MusicPanel(QWidget):
    changed=Signal(object)
    audition_started=Signal()
    mix_requested=Signal()
    failed=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('WorkspacePanel');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.edition={};self.player=None
        layout=QVBoxLayout(self);layout.setContentsMargins(12,8,12,8);layout.setSpacing(8)
        row=QHBoxLayout();title=QLabel('背景配乐');title.setObjectName('SectionTitle');row.addWidget(title)
        self.enabled=QCheckBox('启用');self.enabled.setAccessibleName('启用背景配乐');row.addWidget(self.enabled)
        self.current=RoundedComboBox();self.current.setAccessibleName('单独试听曲目')
        self.current.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.current.setMinimumWidth(118);row.addWidget(self.current,1)
        self.solo=QPushButton('试听音乐');self.solo.clicked.connect(self._audition);row.addWidget(self.solo)
        self.manage=QPushButton('选择音乐…');self.manage.setToolTip('导入、移除、排序、淡入淡出和循环')
        self.manage.clicked.connect(self._choose);row.addWidget(self.manage);layout.addLayout(row)

        levels=QHBoxLayout();self.sliders={}
        for key,text in (('voice_volume','配音音量'),('music_volume','配乐音量')):
            name=QLabel(text);name.setObjectName('MutedText');levels.addWidget(name)
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(0,100);slider.setAccessibleName(text)
            if key=='music_volume':slider.setToolTip('口播和停顿时保持同一配乐音量')
            name.setBuddy(slider);slider.setMinimumWidth(52);levels.addWidget(slider,1)
            value=QLabel();value.setObjectName('MutedText');value.setMinimumWidth(36);levels.addWidget(value)
            slider.valueChanged.connect(lambda n,display=value:display.setText(f'{n}%'))
            slider.valueChanged.connect(lambda n,k=key:self._change(k,n))
            self.sliders[key]=(slider,value)
        self.mix_button=QPushButton('试听混音');self.mix_button.setToolTip('使用当前配音与设置重新准备预览音轨')
        self.mix_button.clicked.connect(self.mix_requested);levels.addWidget(self.mix_button);layout.addLayout(levels)
        self.enabled.toggled.connect(lambda value:self._change('enabled',value))
        self.current.currentIndexChanged.connect(self._track_changed)
        self.set_edition({})

    def set_edition(self,edition):
        self.edition=edition
        value=settings(edition)
        with QSignalBlocker(self.enabled):self.enabled.setChecked(value['enabled'])
        for key,(slider,readout) in self.sliders.items():
            with QSignalBlocker(slider):slider.setValue(value[key])
            readout.setText(f'{value[key]}%')
        selection=self.current.currentData()
        paths=edition.get('music',[])
        with QSignalBlocker(self.current):
            self.current.clear()
            for path in paths:self.current.addItem(Path(path).name,path)
            if not paths:self.current.addItem('无背景音乐 · 可本地导入')
            self.current.setCurrentIndex(max(0,self.current.findData(selection)))
        self._track_changed()
        self.solo.setEnabled(bool(paths))

    def _change(self,key,value):
        self.changed.emit({'music_settings':{**settings(self.edition),key:value}})
        if self.player and key=='music_volume':self.output.setVolume(value/100)

    def _choose(self):
        dialog=QDialog(self);dialog.setWindowTitle('背景配乐 · 本期音乐');dialog.resize(520,355)
        layout=QVBoxLayout(dialog);playlist=MusicPlaylist(self.edition,dialog);layout.addWidget(playlist)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.changed.emit(playlist.values())

    def _track_changed(self):
        self.stop();self.current.setToolTip(self.current.currentData() or '当前没有内置音乐')

    def _audition(self):
        from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
        if self.player and self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.stop();return
        path=self.current.currentData()
        if not path or not Path(path).is_file():
            self.failed.emit('背景音乐文件已移走或不存在，请重新导入');return
        self.audition_started.emit()
        if self.player is None:
            self.player=QMediaPlayer(self);self.output=QAudioOutput(self);self.player.setAudioOutput(self.output)
            self.player.playbackStateChanged.connect(lambda state:self.solo.setText(
                '停止试听' if state==QMediaPlayer.PlaybackState.PlayingState else '试听音乐'))
            self.player.errorOccurred.connect(lambda *_:self.failed.emit('音乐试听失败：'+self.player.errorString()))
        self.solo.setToolTip(path);self.output.setVolume(settings(self.edition)['music_volume']/100)
        self.player.setSource(QUrl.fromLocalFile(path));self.player.play()

    def stop(self):
        if self.player:self.player.stop()
