"""Frame-accurate source preview for a news video's saved in/out range."""
from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                              QSlider, QVBoxLayout)

from services.news_video import VideoFrames, frame_range, validate_asset
from tools.video_cards import FPS


class NewsTrimDialog(QDialog):
    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle('剪裁新闻视频');self.resize(720,520)
        self.first,self.end=frame_range(asset)
        self.reader=VideoFrames(validate_asset(asset),asset['frames'],fps=asset.get('fps',FPS),preview_size=(640,360))
        self.clock=QElapsedTimer();self.play_start=0
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(round(1000/self.reader.fps));self.timer.timeout.connect(self.advance)
        layout=QVBoxLayout(self)
        self.picture=QLabel();self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter);self.picture.setMinimumSize(640,360)
        layout.addWidget(self.picture,1)
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,asset['frames']-1)
        self.slider.setValue(self.first);self.slider.valueChanged.connect(self.render)
        self.slider.sliderPressed.connect(self.pause);layout.addWidget(self.slider)
        self.info=QLabel();layout.addWidget(self.info)
        row=QHBoxLayout();self.play=QPushButton('播放');self.play.clicked.connect(self.toggle);row.addWidget(self.play)
        for label,key,action in (('设入点','I',self.mark_in),('设出点','O',self.mark_out),('重置入出点','',self.reset)):
            button=QPushButton(f'{label}（{key}）' if key else label);button.clicked.connect(action);row.addWidget(button)
            if key:QShortcut(QKeySequence(key),self,activated=action)
        row.addStretch(1);layout.addLayout(row)
        QShortcut(QKeySequence('Space'),self,activated=self.toggle)
        self.message=QLabel('拖动进度条逐帧定位。出点包含当前帧。');layout.addWidget(self.message)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.finished.connect(self.release)
        self.render(self.first)

    def render(self, frame):
        fps=self.reader.fps
        try:
            image=self.reader.frame(frame/fps)
        except (RuntimeError,ValueError,OSError) as error:
            self.pause();self.message.setText(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            return
        pixmap=QPixmap.fromImage(QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888))
        self.picture.setPixmap(pixmap)
        self.info.setText(f'当前 {frame/fps:.3f} 秒    入点 {self.first/fps:.3f} 秒    出点 {(self.end-1)/fps:.3f} 秒    选段 {(self.end-self.first)/fps:.3f} 秒')

    def toggle(self):
        if self.timer.isActive():self.pause();return
        self.play_start=self.slider.value()
        if self.play_start==self.slider.maximum():self.play_start=self.first
        self.clock.start();self.timer.start();self.play.setText('暂停')

    def pause(self):
        self.timer.stop();self.play.setText('播放')

    def advance(self):
        frame=self.play_start+self.clock.elapsed()*self.reader.fps//1000
        self.slider.setValue(min(frame,self.slider.maximum()))
        if frame>=self.slider.maximum():self.pause()

    def mark_in(self):
        frame=self.slider.value()
        if frame>=self.end:
            self.message.setText('该位置已超过出点，请先调整出点。');return
        self.first=frame;self.render(frame)

    def mark_out(self):
        frame=self.slider.value()
        if frame<self.first:
            self.message.setText('该位置早于入点，请先调整入点。');return
        self.end=frame+1;self.render(frame)

    def reset(self):
        self.first,self.end=0,self.reader.frames
        self.render(self.slider.value())

    def release(self):
        self.pause();self.reader.close()
