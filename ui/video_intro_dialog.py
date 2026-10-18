"""Small opener editor with one saved local template."""
import json
import logging
from pathlib import Path

from PySide6.QtCore import QDate, QElapsedTimer, QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDateEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from i18n import tr
from services.cancellation import CancellationToken, TaskCancelledError, no_cancel
from tools import video_cards, video_intro
from ui.widgets.combo_box import RoundedComboBox as QComboBox


class IntroWorker(QThread):
    progress = Signal(float)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, output, options, parent=None):
        super().__init__(parent)
        self.output, self.options = output, dict(options)
        self.token = CancellationToken()

    def cancel(self):
        self.token.cancel()

    def run(self):
        try:
            path = video_intro.generate(self.output, **self.options,
                cancel_check=self.token.check, on_progress=self.progress.emit)
        except TaskCancelledError:
            self.cancelled.emit()
        except Exception as error:
            logging.getLogger(__name__).exception('Opener encoding failed')
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(str(path))


class IntroPreviewWorker(QThread):
    ready = Signal(dict)
    failed = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = dict(options)
        self.token = CancellationToken()

    def run(self):
        try:
            card = video_intro.prepare(**self.options, cancel_check=self.token.check)
        except TaskCancelledError:
            pass
        except Exception as error:
            logging.getLogger(__name__).exception('Opener animation preview failed')
            self.failed.emit(str(error))
        else:
            self.ready.emit(card)


class VideoIntroDialog(QDialog):
    def __init__(self, output, parent=None):
        super().__init__(parent)
        self.output = Path(output)
        self.template = self.output/'intro-template.json'
        self.worker = None
        self.preview_worker = None
        self._preview_card = None
        self._logo_full = False
        self._preview_resume = False
        self._pending_play = False
        self._play_clock = QElapsedTimer()
        self._play_offset = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(33)
        self._preview_timer.timeout.connect(self._tick_preview)
        self.result_path = None
        self._closing = False
        self._logo_key = object()
        self._logo_data = None
        self.setWindowTitle(tr('video.intro'))
        self.resize(1080, 920)
        self.setMinimumSize(900, 820)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        self.inputs = QWidget()
        self.inputs.setObjectName('SettingsCard')
        self.inputs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inputs = QVBoxLayout(self.inputs)
        inputs.setContentsMargins(24, 22, 24, 22)
        inputs.setSpacing(16)
        heading = QLabel(tr('video.intro'))
        heading.setObjectName('PanelTitle')
        inputs.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(14)
        self.title = QLineEdit('航天周报')
        self.subtitle = QLineEdit('SPACEWEEKLY')
        self.date = QDateEdit(QDate.currentDate().addDays(-6))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat('yyyy-MM-dd')
        date_row = QHBoxLayout()
        date_row.setSpacing(12)
        date_row.addWidget(self.date, 1)
        date_row.addWidget(QLabel('—'))
        date_row.addWidget(self.end_date, 1)
        self.logo = QLineEdit()
        self.logo.setPlaceholderText(tr('video.intro_logo_hint'))
        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        logo_row.addWidget(self.logo, 1)
        choose = QPushButton(tr('video.intro_choose_logo'))
        choose.clicked.connect(self._choose_logo)
        logo_row.addWidget(choose)
        self.logo_motion = QComboBox()
        for motion in video_intro.LOGO_MOTIONS:
            self.logo_motion.addItem(tr('video.intro_motion_'+motion), motion)
        self.logo_motion.setToolTip(tr('video.intro_motion_hint'))
        form.addRow(tr('video.intro_name'), self.title)
        form.addRow(tr('video.intro_subtitle'), self.subtitle)
        form.addRow(tr('video.intro_date'), date_row)
        form.addRow(tr('video.intro_logo'), logo_row)
        form.addRow(tr('video.intro_motion'), self.logo_motion)
        inputs.addLayout(form)
        note = QLabel(tr('video.intro_note'))
        note.setObjectName('Eyebrow')
        note.setWordWrap(True)
        inputs.addWidget(note)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(640, 320)
        inputs.addWidget(self.preview, 1)
        playback = QHBoxLayout()
        self.preview_play = QPushButton(tr('video.play'))
        self.preview_play.setToolTip(tr('video.intro_preview_hint'))
        self.preview_play.clicked.connect(self._toggle_preview)
        playback.addWidget(self.preview_play)
        self.preview_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_slider.setObjectName('VideoPreviewSlider')
        self.preview_slider.setRange(0, video_intro.SECONDS*video_intro.FPS)
        self.preview_slider.setAccessibleName(tr('video.intro_preview_progress'))
        self.preview_slider.valueChanged.connect(self._seek_preview)
        playback.addWidget(self.preview_slider, 1)
        self.preview_time = QLabel()
        playback.addWidget(self.preview_time)
        inputs.addLayout(playback)
        layout.addWidget(self.inputs, 1)
        self.progress = QProgressBar()
        self.progress.setObjectName('VideoProgress')
        layout.addWidget(self.progress)
        self.status = QLabel(tr('video.ready'))
        self.status.setObjectName('Eyebrow')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.save = QPushButton(tr('video.intro_save_template'))
        self.save.clicked.connect(self._save_template)
        row.addWidget(self.save)
        self.open = QPushButton(tr('video.intro_open'))
        self.open.setEnabled(False)
        self.open.clicked.connect(self._open_result)
        row.addWidget(self.open)
        row.addStretch()
        self.generate = QPushButton(tr('video.intro_generate'))
        self.generate.setObjectName('PrimaryButton')
        self.generate.clicked.connect(self._start)
        row.addWidget(self.generate)
        self.cancel = QPushButton(tr('video.cancel'))
        self.cancel.setEnabled(False)
        self.cancel.hide()
        self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.cancel)
        close = QPushButton(tr('video.close'))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh = QTimer(self)
        self.refresh.setSingleShot(True)
        self.refresh.timeout.connect(self._preview)
        for edit in (self.title, self.subtitle, self.logo):
            edit.textChanged.connect(lambda: self.refresh.start(150))
        self.date.dateChanged.connect(lambda: self.refresh.start(150))
        self.end_date.dateChanged.connect(lambda: self.refresh.start(150))
        self.logo_motion.currentIndexChanged.connect(lambda: self.refresh.start(150))
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._load_template()
        self._preview()

    def options(self):
        return dict(date_text=self.date.date().toString('yyyy-MM-dd'), title=self.title.text(),
                    subtitle=self.subtitle.text(), logo_path=self.logo.text().strip() or None,
                    end_date=self.end_date.date().toString('yyyy-MM-dd'),
                    logo_motion=self.logo_motion.currentData())

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('video.intro_choose_logo'), str(self.output),
                                             'Logo media (*.png *.jpg *.jpeg *.webp *.gif *.mp4 *.mov *.m4v)')
        if path:
            self.logo.setText(str(Path(path).resolve()))

    def _prepare_card(self):
        options = self.options()
        path = Path(options['logo_path']) if options['logo_path'] else None
        stat = path.stat() if path else None
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size) if path else None
        if key != self._logo_key:
            self._logo_data = video_intro.load_logo(path, preview=True)
            self._logo_full = not self._logo_data['animated']
            self._logo_key = key
        card = video_intro.prepare(**options, logo_data=self._logo_data)
        self.logo_motion.setEnabled(not card['logo_animated'])
        return card

    def _preview(self, update_status=True):
        self._stop_preview()
        self._preview_resume = False
        try:
            self._preview_card = self._prepare_card()
            with QSignalBlocker(self.preview_slider):
                self.preview_slider.setValue(3*video_intro.FPS)
            self._draw_preview()
            self.preview_play.setEnabled(True)
            self.preview_slider.setEnabled(True)
            if update_status:
                self.status.setText(tr('video.intro_ready'))
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).exception('Opener preview failed (text_font=%s, number_font=%s, logo=%s)',
                video_cards.FONT, video_cards.NUMBER_FONT, self.logo.text())
            self._preview_card = None
            self.preview_play.setEnabled(False)
            self.preview_slider.setEnabled(False)
            self.preview.clear()
            self.status.setText(tr('video.preview_error', error=error))

    def _draw_preview(self):
        if self._preview_card is None:
            return
        t = self.preview_slider.value()/video_intro.FPS
        frame = video_intro.render_frame(self._preview_card, t)
        image = QImage(frame.tobytes(), video_intro.W, video_intro.H, video_intro.W*4,
                       QImage.Format.Format_RGBA8888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.preview_time.setText(f'{t:.1f} / {video_intro.SECONDS:.1f} s')

    def _seek_preview(self, *_):
        self._preview_resume = True
        self._play_offset = self.preview_slider.value()
        if self._preview_timer.isActive():
            self._play_clock.start()
        if self._preview_card is not None and not self._logo_full and self.preview_worker is None and self.worker is None:
            self._load_preview_animation(False)
        else:
            self._draw_preview()

    def _stop_preview(self):
        self._preview_timer.stop()
        if hasattr(self, 'preview_play'):
            self.preview_play.setText(tr('video.play'))

    def _start_playback(self):
        if not self._preview_resume or self.preview_slider.value() >= self.preview_slider.maximum():
            with QSignalBlocker(self.preview_slider):
                self.preview_slider.setValue(0)
        self._preview_resume = True
        self._play_offset = self.preview_slider.value()
        self._play_clock.start()
        self._draw_preview()
        self.preview_play.setText(tr('video.pause'))
        self._preview_timer.start()

    def _toggle_preview(self):
        if self.worker is not None or self.preview_worker is not None:
            return
        if self._preview_timer.isActive():
            self._stop_preview()
            return
        if self.refresh.isActive():
            self.refresh.stop()
            self._preview()
        if self._preview_card is None:
            return
        if not self._logo_full:
            self._load_preview_animation(True)
        else:
            self._start_playback()

    def _load_preview_animation(self, play):
        self._play_after_load = play
        self.cancel.setText(tr('video.intro_cancel_preview'))
        self.status.setText(tr('video.intro_preview_loading'))
        self.inputs.setEnabled(False)
        self.save.setEnabled(False)
        self.generate.setEnabled(False)
        self.cancel.setEnabled(True)
        self.cancel.show()
        self._pending_play = False
        self.preview_worker = IntroPreviewWorker(self.options(), self)
        self.preview_worker.ready.connect(self._preview_loaded)
        self.preview_worker.failed.connect(lambda error: self.status.setText(tr('video.preview_error', error=error)))
        self.preview_worker.finished.connect(self._preview_finished)
        self.preview_worker.start()

    def _preview_loaded(self, card):
        if self._closing:
            return
        self._logo_data = dict(frames=card['logo_frames'], ends=card['logo_ends'],
                               animated=card['logo_animated'], loop=card['logo_loop'])
        self._logo_full = True
        self._preview_card = card
        self._pending_play = self._play_after_load

    def _preview_finished(self):
        cancelled = self.preview_worker.token.is_cancelled
        self.cancel.setText(tr('video.cancel'))
        self.preview_worker.deleteLater()
        self.preview_worker = None
        self.inputs.setEnabled(True)
        self.save.setEnabled(True)
        self.generate.setEnabled(True)
        self.cancel.setEnabled(False)
        self.cancel.hide()
        if self._closing:
            super().reject()
        elif cancelled:
            self.status.setText(tr('video.cancelled'))
        elif self._pending_play:
            self.status.setText(tr('video.intro_ready'))
            self._start_playback()
        elif self._logo_full:
            self.status.setText(tr('video.intro_ready'))
            self._draw_preview()
        self._pending_play = False

    def _tick_preview(self):
        value = min(self.preview_slider.maximum(), self._play_offset+round(self._play_clock.elapsed()*video_intro.FPS/1000))
        with QSignalBlocker(self.preview_slider):
            self.preview_slider.setValue(value)
        self._draw_preview()
        if value == self.preview_slider.maximum():
            self._stop_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'preview_slider'):
            self._draw_preview()

    def _save_template(self):
        try:
            options = self.options()
            self._prepare_card()
            self.output.mkdir(parents=True, exist_ok=True)
            options.pop('date_text')
            options.pop('end_date')
            video_cards.save_manifest(self.template, dict(version=1, **options), no_cancel)
            self.status.setText(tr('video.intro_saved', path=self.template))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.intro'), str(error))

    def _load_template(self):
        if not self.template.exists():
            return
        try:
            data = json.loads(self.template.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or not isinstance(data.get('title'), str) or not data['title'].strip()
                    or not isinstance(data.get('subtitle'), str)
                    or data.get('logo_path') is not None and not isinstance(data['logo_path'], str)
                    or data.get('logo_motion', 'fade') not in video_intro.LOGO_MOTIONS):
                raise ValueError('Invalid opener template')
            self.title.setText(data['title'])
            self.subtitle.setText(data['subtitle'])
            self.logo.setText(data.get('logo_path') or '')
            self.logo_motion.setCurrentIndex(self.logo_motion.findData(data.get('logo_motion', 'fade')))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.intro'), str(error))

    def _start(self):
        if self.worker is not None or self.preview_worker is not None:
            return
        try:
            options = self.options()
            self._prepare_card()
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).exception('Opener preparation failed (text_font=%s, number_font=%s, logo=%s)',
                video_cards.FONT, video_cards.NUMBER_FONT, self.logo.text())
            QMessageBox.warning(self, tr('video.intro'), str(error))
            return
        self.refresh.stop()
        self._stop_preview()
        self.inputs.setEnabled(False)
        self.save.setEnabled(False)
        self.generate.setEnabled(False)
        self.cancel.setEnabled(True)
        self.cancel.show()
        self.progress.setValue(0)
        self.status.setText(tr('video.working'))
        self.worker = IntroWorker(self.output, options, self)
        self.worker.progress.connect(lambda fraction: self.progress.setValue(round(fraction*95)))
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(lambda error: self.status.setText(tr('video.failed', error=error)))
        self.worker.cancelled.connect(lambda: self.status.setText(tr('video.cancelled')))
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _done(self, path):
        self.result_path = Path(path)
        self.open.setEnabled(True)
        self.progress.setValue(100)
        self.status.setText(tr('video.intro_done', path=path))

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.inputs.setEnabled(True)
        self.save.setEnabled(True)
        self.generate.setEnabled(True)
        self.cancel.setEnabled(False)
        self.cancel.hide()
        if self._closing:
            super().reject()

    def _cancel(self):
        if self.preview_worker is not None:
            self.preview_worker.token.cancel()
            self.cancel.setEnabled(False)
            self.status.setText(tr('video.intro_preview_canceling'))
        elif self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)
            self.status.setText(tr('video.canceling'))

    def _open_result(self):
        if self.result_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_path.resolve())))

    def reject(self):
        self.refresh.stop()
        self._stop_preview()
        if self.worker is not None or self.preview_worker is not None:
            self._closing = True
            self._cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None or self.preview_worker is not None:
            self.reject()
            event.ignore()
        else:
            self.refresh.stop()
            self._stop_preview()
            event.accept()
