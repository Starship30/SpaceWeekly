"""Small desktop entry for the shared offline news-strip renderer."""
import copy
import hashlib
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSlider, QSplitter,
    QVBoxLayout, QWidget, QScrollArea)
from PIL import Image, ImageDraw

from i18n import tr
from services.cancellation import CancellationToken, TaskCancelledError, no_cancel
from tools import video_cards
from ui.widgets.combo_box import RoundedComboBox as QComboBox


class VideoCardsWorker(QThread):
    progress = Signal(dict)
    finished_ok = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, data, output, seconds, selected_ids, parent=None, encoding='png', sort_order='asc'):
        super().__init__(parent)
        self.data = copy.deepcopy(data)
        self.output = Path(output)
        self.seconds = seconds
        self.selected_ids = set(selected_ids)
        self.token = CancellationToken()
        self.encoding = encoding
        self.sort_order = sort_order

    def cancel(self):
        self.token.cancel()

    def run(self):
        try:
            result = video_cards.generate(self.data, self.output, self.seconds,
                cancel_check=self.token.check, selected_ids=self.selected_ids,
                on_progress=self.progress.emit, encoding=self.encoding, sort_order=self.sort_order)
        except TaskCancelledError:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.finished_ok.emit(str(result))


class VideoCardsDialog(QDialog):
    def __init__(self, output_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('video.title'))
        self.resize(1240, 920)
        self.setMinimumSize(1000, 800)
        self.output_root = Path(output_dir)/'video-cards'
        self.data = None
        self.source = None
        self.index = -1
        self.worker = None
        self.intro_dialog = None
        self.manifest = None
        self._changing = False
        self._close_requested = False
        self._preview_card = None
        self._preview_seconds = 12
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._tick_preview)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._prepare_preview)
        self._checker = Image.new('RGBA', (video_cards.W, video_cards.H), (45, 45, 45, 255))
        draw = ImageDraw.Draw(self._checker)
        for y in range(0, video_cards.H, 80):
            for x in range(0, video_cards.W, 80):
                if (x//80+y//80) % 2:
                    draw.rectangle((x, y, x+79, y+79), fill=(65, 65, 65, 255))
        self._build_ui()

    @property
    def running(self):
        return self.worker is not None and self.worker.isRunning()

    def _button(self, label, slot):
        button = QPushButton(tr(label))
        button.clicked.connect(slot)
        return button

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(14)
        self.inputs = QWidget()
        layout = QVBoxLayout(self.inputs)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel(tr('video.title'))
        title.setObjectName('PanelTitle')
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._button('video.add_launch', self._add_launch))
        header.addWidget(self._button('video.intro', self._open_intro))
        layout.addLayout(header)
        top = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText(tr('video.load_first'))
        top.addWidget(self.source_edit, 1)
        top.addWidget(self._button('video.open_snapshot', self._choose_snapshot))
        top.addWidget(self._button('video.save_snapshot', self._save_snapshot))
        layout.addLayout(top)
        note = QLabel(tr('video.window_hint'))
        note.setObjectName('Eyebrow')
        note.setToolTip(tr('video.edits_note'))
        note.setWordWrap(True)
        layout.addWidget(note)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_box = QWidget()
        list_box.setObjectName('WorkspacePanel')
        list_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(16, 16, 16, 16)
        list_layout.setSpacing(12)
        list_title = QLabel(tr('video.asset_list'))
        list_title.setObjectName('SectionTitle')
        list_layout.addWidget(list_title)
        self.sort_order = QComboBox()
        self.sort_order.addItem(tr('video.order_asc'), 'asc')
        self.sort_order.addItem(tr('video.order_desc'), 'desc')
        self.sort_order.setToolTip(tr('video.order_hint'))
        self.sort_order.currentIndexChanged.connect(self._sort_changed)
        list_layout.addWidget(self.sort_order)
        self.items = QListWidget()
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.items.currentRowChanged.connect(self._change_item)
        self.items.itemChanged.connect(self._selection_changed)
        list_layout.addWidget(self.items, 1)
        select = QHBoxLayout()
        select.addWidget(self._button('video.select_all', lambda: self._check_all(True)))
        select.addWidget(self._button('video.select_none', lambda: self._check_all(False)))
        list_layout.addLayout(select)
        splitter.addWidget(list_box)
        editor_box = QWidget()
        editor_box.setObjectName('WorkspacePanel')
        editor_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        editor = QVBoxLayout(editor_box)
        editor.setContentsMargins(18, 16, 18, 16)
        editor.setSpacing(12)
        editor_title = QLabel(tr('video.edit_preview'))
        editor_title.setObjectName('SectionTitle')
        editor.addWidget(editor_title)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.kind = QComboBox()
        self.kind.addItem(tr('video.news_type'), 'news')
        self.kind.addItem(tr('video.launch_type'), 'launch')
        self.kind.currentIndexChanged.connect(self._type_changed)
        form.addRow(tr('video.asset_type'), self.kind)
        self.title_edit = QLineEdit()
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setMaximumHeight(100)
        self.title_edit.textChanged.connect(self._edit_changed)
        self.summary_edit.textChanged.connect(self._edit_changed)
        self.title_label = QLabel(tr('video.title_text'))
        self.summary_label = QLabel(tr('video.summary_text'))
        form.addRow(self.title_label, self.title_edit)
        form.addRow(self.summary_label, self.summary_edit)
        self.launch_box = QWidget()
        self.launch_box.setProperty('formGroup', True)
        launch_form = QFormLayout(self.launch_box)
        launch_form.setContentsMargins(0, 0, 0, 0)
        launch_form.setHorizontalSpacing(18)
        launch_form.setVerticalSpacing(12)
        self.launch_fields = {}
        for key in ('orbit', 'payload', 'site', 'time'):
            field = QLineEdit()
            field.textChanged.connect(self._edit_changed)
            self.launch_fields[key] = field
            launch_form.addRow(tr('video.launch_'+key), field)
        self.launch_box.hide()
        form.addRow(self.launch_box)
        form_widget = QWidget()
        form_widget.setProperty('formGroup', True)
        form_widget.setLayout(form)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setMinimumHeight(155)
        form_scroll.setWidget(form_widget)
        editor.addWidget(form_scroll, 1)
        splitter.addWidget(editor_box)
        preview_box = QWidget()
        preview_box.setObjectName('WorkspacePanel')
        preview_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(12)
        preview_title = QLabel(tr('video.preview'))
        preview_title.setObjectName('SectionTitle')
        preview_layout.addWidget(preview_title)
        self.preview = QLabel(tr('video.load_first'))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(400, 225)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.preview, 1)
        playback = QHBoxLayout()
        self.play = self._button('video.play', self._toggle_preview)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName('VideoPreviewSlider')
        self.slider.setRange(0, 120)
        self.slider.valueChanged.connect(self._draw_preview)
        self.time_label = QLabel()
        playback.addWidget(self.play)
        playback.addWidget(self.slider, 1)
        playback.addWidget(self.time_label)
        preview_layout.addLayout(playback)
        splitter.addWidget(preview_box)
        splitter.setSizes([230, 340, 650])
        layout.addWidget(splitter, 1)
        settings = QHBoxLayout()
        settings.setSpacing(10)
        settings.addWidget(QLabel(tr('video.minimum_seconds')))
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(6, 600)
        self.seconds.setDecimals(1)
        self.seconds.setValue(12)
        self.seconds.valueChanged.connect(lambda: self._refresh_timer.start(150))
        settings.addWidget(self.seconds)
        self.encoding = QComboBox()
        self.encoding.addItem(tr('video.encoding_png'), 'png')
        self.encoding.addItem('ProRes 4444', 'prores')
        settings.addWidget(self.encoding)
        settings.addWidget(QLabel(tr('video.output')))
        self.output_edit = QLineEdit(str(self.output_root))
        self.output_edit.setReadOnly(True)
        settings.addWidget(self.output_edit, 1)
        settings.addWidget(self._button('video.choose_output', self._choose_output))
        layout.addLayout(settings)
        outer.addWidget(self.inputs, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName('VideoProgress')
        self.progress_bar.setRange(0, 100)
        outer.addWidget(self.progress_bar)
        self.status = QLabel(tr('video.ready'))
        self.status.setObjectName('Eyebrow')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(80)
        outer.addWidget(self.log)
        footer = QHBoxLayout()
        self.open_output = self._button('video.open_output', self._open_output)
        footer.addWidget(self.open_output)
        footer.addStretch()
        self.generate_button = self._button('video.generate_selected', self._start)
        self.generate_button.setObjectName('PrimaryButton')
        self.cancel_button = self._button('video.cancel', self._cancel)
        self.cancel_button.setEnabled(False)
        footer.addWidget(self.generate_button)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self._button('video.close', self.reject))
        outer.addLayout(footer)
        self.generate_button.setEnabled(False)

    def _choose_snapshot(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr('video.open_snapshot'),
            str(self.source.parent if self.source else self.output_root.parent), 'JSON (*.json)')
        if filename:
            self.load_snapshot(Path(filename))

    def load_snapshot(self, path):
        if self.worker is not None:
            return
        try:
            data = video_cards.load_snapshot(Path(path))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.title'), str(error))
            return
        self._set_data(data, Path(path))

    def _set_data(self, data, path, selected_ids=None, current_id=None):
        self._stop_preview()
        self._refresh_timer.stop()
        self._changing = True
        data = dict(data, items=video_cards.sort_items(data['items'], self.sort_order.currentData()),
                    order_policy=f'video_type_then_published_{self.sort_order.currentData()}')
        self.source, self.data, self.index = Path(path), data, -1
        self.manifest = None
        self.source_edit.setText(str(path))
        self.items.clear()
        for item in data['items']:
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, item['id'])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if selected_ids is None or item['id'] in selected_ids
                             else Qt.CheckState.Unchecked)
            self.items.addItem(row)
        self._update_item_labels()
        self._changing = False
        self.items.setCurrentRow(next((i for i, item in enumerate(data['items'])
                                       if item['id'] == current_id), 0))
        self.generate_button.setEnabled(bool(self.selected_ids()))
        self.status.setText(tr('video.ready'))
        self.progress_bar.setValue(0)

    def _sort_changed(self):
        if self._changing or self.data is None or self.worker is not None:
            return
        self._store_editor()
        current_id = self.data['items'][self.index]['id'] if self.index >= 0 else None
        self._set_data(self.data, self.source, self.selected_ids(), current_id)

    def _add_launch(self):
        if self.worker is not None:
            return
        self._store_editor()
        selected = self.selected_ids()
        data = copy.deepcopy(self.data) if self.data is not None else {
            'protocol_version': '1.0', 'job_id': str(uuid4()), 'report_title': tr('video.launch_type'),
            'language': 'zh_CN', 'items': []}
        identifier = str(uuid4())
        data['items'].append(dict(id='launch-manual:'+identifier, title=tr('video.rocket'),
            summary=tr('video.mission'), source='Local user input', published='',
            url='https://example.invalid/launch/'+identifier, categories=[], video_type='launch',
            launch={key: '' for key in self.launch_fields}))
        selected.add('launch-manual:'+identifier)
        self._set_data(data, self.source or self.output_root.parent/'launch-cards.json',
                       selected, 'launch-manual:'+identifier)

    def _open_intro(self):
        if self.worker is not None:
            return
        from ui.video_intro_dialog import VideoIntroDialog
        dialog = VideoIntroDialog(self.output_root.parent/'video-intro', self)
        self.intro_dialog = dialog
        dialog.exec()
        self.intro_dialog = None
        dialog.deleteLater()
        if self._close_requested:
            super().reject()

    def _type_changed(self):
        is_launch = self.kind.currentData() == 'launch'
        self.launch_box.setVisible(is_launch)
        self.title_label.setText(tr('video.rocket' if is_launch else 'video.title_text'))
        self.summary_label.setText(tr('video.mission' if is_launch else 'video.summary_text'))
        self.summary_edit.setMaximumHeight(48 if is_launch else 100)
        if not self._changing and self.data is not None and self.index >= 0:
            item = self.data['items'][self.index]
            item['video_type'] = self.kind.currentData()
            self._edit_changed()
            self._sort_changed()

    def _store_editor(self):
        if self.data is None or self.index < 0 or self._changing:
            return
        item = self.data['items'][self.index]
        if self.kind.currentData() == 'launch':
            item.setdefault('launch', {}).update({key: field.text() for key, field in self.launch_fields.items()})
        if item['title'] != self.title_edit.text():
            item['title'] = self.title_edit.text()
            item['title_origin'] = 'user_edit'
        if item['summary'] != self.summary_edit.toPlainText():
            item['summary'] = self.summary_edit.toPlainText()
            item['summary_origin'] = 'user_edit'
            item['summary_fallback'] = None
        self._update_item_labels()

    def _update_item_labels(self):
        ordinals = video_cards.card_ordinals(self.data['items'], self.selected_ids())
        with QSignalBlocker(self.items):
            for index, (item, ordinal) in enumerate(zip(self.data['items'], ordinals)):
                label = tr('video.launch_type' if item.get('video_type') == 'launch' else 'video.news_type')
                number = f' {ordinal:02d}' if ordinal is not None else ''
                self.items.item(index).setText(f'{label}{number}  {item["title"]}')
                self.items.item(index).setToolTip(item['title'])

    def _selection_changed(self, *_):
        if self._changing or self.data is None or self.worker is not None:
            return
        self._update_item_labels()
        self.generate_button.setEnabled(bool(self.selected_ids()))
        self._refresh_timer.stop()
        self._prepare_preview()

    def _change_item(self, index):
        if self._changing:
            return
        self._store_editor()
        self.index = index
        if self.data is None or index < 0:
            return
        self._changing = True
        item = self.data['items'][index]
        self.title_edit.setText(item['title'])
        self.summary_edit.setPlainText(item['summary'])
        self.kind.setCurrentIndex(1 if item.get('video_type') == 'launch' else 0)
        for key, field in self.launch_fields.items():
            value = item.get('launch', {}).get(key, '')
            field.setText(video_cards.launch_time_text(value) if key == 'time' else value)
        self._type_changed()
        self._changing = False
        self._prepare_preview()

    def _edit_changed(self):
        if not self._changing:
            self._store_editor()
            self._refresh_timer.start(150)

    def _check_all(self, checked):
        with QSignalBlocker(self.items):
            for i in range(self.items.count()):
                self.items.item(i).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._selection_changed()

    def selected_ids(self):
        return {self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())
                if self.items.item(i).checkState() == Qt.CheckState.Checked}

    def _prepare_preview(self):
        if self.data is None or self.index < 0:
            return
        self._stop_preview()
        ordinal = video_cards.card_ordinals(self.data['items'], self.selected_ids())[self.index]
        self.play.setEnabled(ordinal is not None)
        self.slider.setEnabled(ordinal is not None)
        if ordinal is None:
            self._preview_card = None
            self.preview.setText(tr('video.unselected_preview'))
            self.time_label.clear()
            self.slider.setValue(0)
            return
        try:
            self._preview_card = video_cards.make_card(self.data['items'][self.index], ordinal)
            frames = video_cards.card_frames(self._preview_card, self.seconds.value())
            self._preview_seconds = frames/video_cards.FPS
            self.slider.setRange(0, int((frames-1)/video_cards.FPS*10))
            self.slider.setValue(min(40, self.slider.maximum()))
            self._draw_preview()
        except (OSError, ValueError) as error:
            self._preview_card = None
            self.preview.clear()
            self.preview.setText(tr('video.preview_error', error=error))

    def _draw_preview(self, *_):
        if self._preview_card is None:
            return
        t = self.slider.value()/10
        frame = self._checker.copy()
        frame.alpha_composite(video_cards.render_frame(self._preview_card, t, self._preview_seconds))
        raw = frame.tobytes()
        image = QImage(raw, video_cards.W, video_cards.H, video_cards.W*4, QImage.Format.Format_RGBA8888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.time_label.setText(f'{t:.1f} / {self._preview_seconds:.1f} s')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'preview'):
            self._draw_preview()

    def _toggle_preview(self):
        if self._preview_timer.isActive():
            self._stop_preview()
        elif self._preview_card is not None:
            self.slider.setValue(0)
            self._preview_timer.start()
            self.play.setText(tr('video.pause'))

    def _stop_preview(self):
        self._preview_timer.stop()
        if hasattr(self, 'play'):
            self.play.setText(tr('video.play'))

    def _tick_preview(self):
        if self.slider.value() >= self.slider.maximum():
            self._stop_preview()
        else:
            self.slider.setValue(self.slider.value()+1)

    def _save_snapshot(self):
        self._store_editor()
        if self.data is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, tr('video.save_snapshot'),
            str(self.output_root.parent/(self.source.stem+'.edited.json')), 'JSON (*.json)')
        if not filename:
            return
        try:
            video_cards.validate_snapshot(self.data)
            video_cards.save_manifest(Path(filename), self.data, no_cancel)
            self.status.setText(tr('video.snapshot_saved', path=filename))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.title'), str(error))

    def _choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, tr('video.choose_output'), str(self.output_root))
        if directory:
            self.output_root = Path(directory)
            self.manifest = None
            self.output_edit.setText(directory)

    def _start(self):
        if self.worker is not None or self.data is None:
            return
        self._store_editor()
        selected = self.selected_ids()
        if not selected:
            QMessageBox.information(self, tr('video.title'), tr('video.empty_selection'))
            return
        try:
            data = copy.deepcopy(video_cards.validate_snapshot(self.data))
            folder = self.output_root/hashlib.sha256(data['job_id'].encode('utf-8')).hexdigest()[:16]
            folder.mkdir(parents=True, exist_ok=True)
            edited = folder/'edited-snapshot.json'
            video_cards.save_manifest(edited, data, no_cancel)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('video.title'), str(error))
            return
        self._stop_preview()
        self._refresh_timer.stop()
        self.inputs.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status.setText(tr('video.working'))
        self.log.appendPlainText(tr('video.snapshot_saved', path=edited))
        self.worker = VideoCardsWorker(data, self.output_root, self.seconds.value(), selected, self,
                                      encoding=self.encoding.currentData(), sort_order=self.sort_order.currentData())
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(lambda: self.status.setText(tr('video.cancelled')))
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _on_progress(self, event):
        fraction = event['fraction'] if event['status'] == 'rendering' else 0
        self.progress_bar.setValue(round(100*(event['completed']+fraction)/event['total']))
        item = next(item for item in self.data['items'] if item['id'] == event['id'])
        if event['status'] in ('done', 'skipped'):
            text = tr('video.item_'+event['status'], title=item['title'], path=event['file'])
            self.log.appendPlainText(text)
        elif event['message']:
            text = f'{item["title"]}: {event["message"]}'
            self.log.appendPlainText(text)
        else:
            text = tr('video.progress', completed=event['completed'], total=event['total'],
                      title=item['title'], percent=round(fraction*100))
        self.status.setText(text)

    def _done(self, path):
        self.manifest = Path(path)
        self.progress_bar.setValue(100)
        self.status.setText(tr('video.done', path=path))
        self.log.appendPlainText(tr('video.done', path=path))

    def _failed(self, error):
        text = tr('video.failed', error=error)
        self.status.setText(text)
        self.log.appendPlainText(text)

    def _worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.inputs.setEnabled(True)
        self.generate_button.setEnabled(self.data is not None and bool(self.selected_ids()))
        self.cancel_button.setEnabled(False)
        if self._close_requested:
            super().reject()

    def _cancel(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText(tr('video.canceling'))

    def _open_output(self):
        path = self.manifest.parent if self.manifest is not None else self.output_root
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def reject(self):
        self._stop_preview()
        self._refresh_timer.stop()
        if self.intro_dialog is not None:
            self._close_requested = True
            self.intro_dialog.reject()
            return
        if self.worker is not None:
            self._close_requested = True
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None or self.intro_dialog is not None:
            self.reject()
            event.ignore()
        else:
            self._stop_preview()
            self._refresh_timer.stop()
            event.accept()
