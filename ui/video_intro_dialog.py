"""Small opener editor with one saved local template."""
import json
from pathlib import Path

from PySide6.QtCore import QDate, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDateEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget)

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
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(str(path))


class VideoIntroDialog(QDialog):
    def __init__(self, output, parent=None):
        super().__init__(parent)
        self.output = Path(output)
        self.template = self.output/'intro-template.json'
        self.worker = None
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
        self.preview.setMinimumSize(640, 360)
        inputs.addWidget(self.preview, 1)
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
            self._logo_key = key
        card = video_intro.prepare(**options, logo_data=self._logo_data)
        self.logo_motion.setEnabled(not card['logo_animated'])
        return card

    def _preview(self, update_status=True):
        try:
            frame = video_intro.render_frame(self._prepare_card(), 3)
            image = QImage(frame.tobytes(), video_intro.W, video_intro.H, video_intro.W*4,
                           QImage.Format.Format_RGBA8888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            if update_status:
                self.status.setText(tr('video.intro_ready'))
        except (OSError, ValueError) as error:
            self.preview.clear()
            self.status.setText(tr('video.preview_error', error=error))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'status'):
            self._preview(update_status=False)

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
        if self.worker is not None:
            return
        try:
            options = self.options()
            self._prepare_card()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.intro'), str(error))
            return
        self.refresh.stop()
        self.inputs.setEnabled(False)
        self.save.setEnabled(False)
        self.generate.setEnabled(False)
        self.cancel.setEnabled(True)
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
        if self._closing:
            super().reject()

    def _cancel(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)
            self.status.setText(tr('video.canceling'))

    def _open_result(self):
        if self.result_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_path.resolve())))

    def reject(self):
        self.refresh.stop()
        if self.worker is not None:
            self._closing = True
            self._cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            self.refresh.stop()
            event.accept()
