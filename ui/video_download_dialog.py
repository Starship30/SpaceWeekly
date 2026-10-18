"""Video search/download panel opened from the existing video assets window."""
from datetime import datetime
from pathlib import Path
import shutil

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget, QMenu)

from i18n import tr
from services.cancellation import CancellationToken, TaskCancelledError
from services import video_download as service
from services import video_download_state as state
from utils.time import parse_datetime
from ui.widgets.combo_box import RoundedComboBox as QComboBox


class VideoDownloadWorker(QThread):
    result = Signal(object)
    progress = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    qr_ready = Signal(bytes)
    login_status = Signal(str)
    search_ready = Signal(object)
    video_ready = Signal(object)
    task_started = Signal(object)
    task_finished = Signal(object)

    def __init__(self, action, value, workspace, parent=None, cookies=None):
        super().__init__(parent)
        self.action, self.value, self.workspace = action, value, Path(workspace)
        self.token = CancellationToken()
        self.cookies = cookies.copy() if cookies is not None else None

    def cancel(self):
        self.token.cancel()

    def run(self):
        try:
            auth = dict(cookies=self.cookies) if self.cookies else {}
            if self.action == 'login':
                from services.bilibili_login import login_bilibili
                result = login_bilibili(self.token, self.qr_ready.emit, self.login_status.emit)
            elif self.action == 'restore_login':
                from services.bilibili_login import session_status
                saved = state.load_session(self.workspace)
                try:
                    status = session_status(saved, self.token) if saved else False
                    self.token.check()
                    if status is False:
                        state.clear_session(self.workspace)
                        result = (None, False)
                    else:
                        result = (saved.copy(), status)
                finally:
                    if saved is not None:
                        saved.clear()
            elif self.action == 'search':
                options = self.value if isinstance(self.value, dict) else dict(keyword=self.value)
                result = service.search_videos(cancel_check=self.token.check, **options, **auth)
            elif self.action == 'demo':
                result = service.demo_download_tasks(workspace=self.workspace, cancel_check=self.token.check,
                    on_progress=self.progress.emit, on_search=self.search_ready.emit,
                    on_video=self.video_ready.emit, on_task_started=self.task_started.emit,
                    on_task_finished=self.task_finished.emit, **self.value, **auth)
            elif self.action == 'parse':
                result = service.parse_video(self.value, self.token.check, **auth)
            else:
                result = service.download_video(*self.value, self.workspace,
                    cancel_check=self.token.check, on_progress=self.progress.emit, **auth)
        except TaskCancelledError:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error if isinstance(error, service.VideoDownloadError)
                             else service.VideoDownloadError('operation', str(error)))
        else:
            self.result.emit(result)
        finally:
            if self.cookies is not None:
                self.cookies.clear()


class BilibiliLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cookies = None
        self.worker = None
        self._close_requested = False
        self.setWindowTitle(tr('video_download.login'))
        self.setMinimumSize(380, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        hint = QLabel(tr('video_download.login_hint'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setMinimumSize(300, 300)
        layout.addWidget(self.qr, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.retry = QPushButton(tr('video_download.login_retry'))
        self.retry.clicked.connect(self._start)
        row.addWidget(self.retry)
        close = QPushButton(tr('video_download.close'))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        QTimer.singleShot(0, self._start)

    def _start(self):
        if self.worker is not None or self._close_requested:
            return
        self.qr.clear()
        self.status.setText(tr('video_download.login_working'))
        self.retry.setEnabled(False)
        self.worker = VideoDownloadWorker('login', None, Path(), self)
        self.worker.qr_ready.connect(self._qr_ready)
        self.worker.login_status.connect(lambda key: self.status.setText(tr('video_download.'+key)))
        self.worker.result.connect(self._result)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _qr_ready(self, data):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.qr.setPixmap(pixmap.scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.FastTransformation))

    def _result(self, cookies):
        if self._close_requested:
            cookies.clear()
        else:
            self.cookies = cookies

    def _failed(self, error):
        self.qr.clear()
        self.status.setText(tr('video_download.error_'+error.code))

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.retry.setEnabled(True)
        if self.cookies is not None:
            super().accept()
        elif self._close_requested:
            super().reject()

    def reject(self):
        self._close_requested = True
        self.qr.clear()
        if self.worker is not None:
            self.worker.cancel()
            self.retry.setEnabled(False)
            self.status.setText(tr('video_download.canceling'))
        else:
            if self.cookies is not None:
                self.cookies.clear()
                self.cookies = None
            super().reject()

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.worker is not None else event.accept()


class VideoDownloadDialog(QDialog):
    def __init__(self, workspace, snapshot_items=(), parent=None):
        super().__init__(parent)
        self.workspace = Path(workspace)
        self.worker = None
        self.info = None
        self.downloaded_file = None
        self.cookies = None
        self.login_dialog = None
        self._close_requested = False
        self._network = QNetworkAccessManager(self)
        self._replies = set()
        self.setWindowTitle(tr('video_download.title'))
        self.resize(960, 760)
        self.setMinimumSize(760, 620)
        self.snapshot_items = list(snapshot_items)
        self._restore_pending = True
        self._build_ui(snapshot_items)
        QTimer.singleShot(0, self._restore_login)

    def _button(self, key, slot):
        button = QPushButton(tr('video_download.'+key))
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def _build_ui(self, items):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)
        title = QLabel(tr('video_download.title'))
        title.setObjectName('PanelTitle')
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        self.auth_status = QLabel(tr('video_download.logged_out'))
        self.auth_status.setObjectName('Eyebrow')
        header.addWidget(self.auth_status)
        self.login_button = self._button('login', self._login)
        header.addWidget(self.login_button)
        outer.addLayout(header)
        note = QLabel(tr('video_download.hint'))
        note.setWordWrap(True)
        note.setObjectName('Eyebrow')
        outer.addWidget(note)
        self.inputs = QWidget()
        layout = QVBoxLayout(self.inputs)
        layout.setContentsMargins(0, 0, 0, 0)
        task_row = QHBoxLayout()
        task_row.addWidget(QLabel(tr('video_download.task')))
        self.tasks = QComboBox()
        self.tasks.setPlaceholderText(tr('video_download.no_tasks'))
        self.tasks.currentIndexChanged.connect(self._choose_task)
        task_row.addWidget(self.tasks, 1)
        self.include_news = QCheckBox(tr('video_download.include_news'))
        self.include_news.toggled.connect(self._populate_tasks)
        task_row.addWidget(self.include_news)
        layout.addLayout(task_row)
        filters = QHBoxLayout()
        self.up_ids = QLineEdit()
        self.up_ids.setPlaceholderText(tr('video_download.up_ids'))
        self.up_ids.setToolTip(tr('video_download.up_hint'))
        self.up_ids.editingFinished.connect(self._save_preferences)
        filters.addWidget(self.up_ids, 1)
        self.order = QComboBox()
        for mode in ('asc', 'desc', 'relevance'):
            self.order.addItem(tr('video_download.order_'+mode), mode)
        filters.addWidget(self.order)
        layout.addLayout(filters)
        preferences = state.load_preferences(self.workspace)
        try:
            saved_ids = preferences.get('up_ids', [])
            if not isinstance(saved_ids, list) or not all(isinstance(v, str) for v in saved_ids):
                raise ValueError()
            self.up_ids.setText(', '.join(service.parse_up_ids(' '.join(saved_ids))))
        except ValueError:
            pass
        index = self.order.findData(preferences.get('order'))
        self.order.setCurrentIndex(max(0, index))
        self.order.currentIndexChanged.connect(self._order_changed)
        date_row = QHBoxLayout()
        self.use_task_date = QCheckBox(tr('video_download.use_task_date'))
        self.use_task_date.setChecked(True)
        self.use_task_date.toggled.connect(self._update_date_range)
        date_row.addWidget(self.use_task_date)
        self.date_hint = QLabel()
        self.date_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.date_hint.setWordWrap(True)
        self.date_hint.setObjectName('Eyebrow')
        date_row.addWidget(self.date_hint, 1)
        layout.addLayout(date_row)
        search = QHBoxLayout()
        self.keyword = QComboBox()
        self.keyword.setEditable(True)
        self.keyword.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.keyword.lineEdit().setPlaceholderText(tr('video_download.keyword'))
        self.keyword.lineEdit().returnPressed.connect(self._search)
        search.addWidget(self.keyword, 1)
        self.search_button = self._button('search', self._search)
        self.keyword.editTextChanged.connect(self._update_download)
        search.addWidget(self.search_button)
        layout.addLayout(search)
        self._populate_tasks()
        self.results = QListWidget()
        self.results.setObjectName('VideoDownloadResults')
        self.results.setMinimumHeight(88)
        self.results.setIconSize(QSize(128, 72))
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.currentItemChanged.connect(self._select_result)
        self.results.itemDoubleClicked.connect(lambda *_: self._parse())
        layout.addWidget(self.results, 1)
        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText(tr('video_download.url'))
        self.url.textChanged.connect(self._invalidate)
        self.url.returnPressed.connect(self._parse)
        url_row.addWidget(self.url, 1)
        self.parse_button = self._button('parse', self._parse)
        url_row.addWidget(self.parse_button)
        layout.addLayout(url_row)
        self.details = QLabel(tr('video_download.select_hint'))
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        quality = QHBoxLayout()
        quality.addWidget(QLabel(tr('video_download.quality')))
        self.formats = QComboBox()
        self.formats.currentIndexChanged.connect(self._update_download)
        quality.addWidget(self.formats, 1)
        self.rights = QCheckBox(tr('video_download.rights'))
        self.rights.toggled.connect(self._update_download)
        layout.addLayout(quality)
        layout.addWidget(self.rights)
        outer.addWidget(self.inputs, 1)
        location = QLabel(tr('video_download.location', path=self.workspace/'video-downloads'))
        location.setTextFormat(Qt.TextFormat.PlainText)
        location.setWordWrap(True)
        location.setObjectName('Eyebrow')
        outer.addWidget(location)
        self.batch_outcomes = []
        self.batch_directory = None
        self.batch_label = QLabel()
        self.batch_label.setTextFormat(Qt.TextFormat.PlainText)
        self.batch_label.setWordWrap(True)
        self.batch_progress = QProgressBar()
        self.batch_progress.setObjectName('VideoProgress')
        self.batch_progress.setFormat(tr('video_download.batch_progress'))
        self.batch_log = QPlainTextEdit()
        self.batch_log.setReadOnly(True)
        self.batch_log.setAccessibleName(tr('video_download.batch_results'))
        self.batch_log.setPlaceholderText(tr('video_download.batch_results'))
        self.batch_log.setMaximumHeight(88)
        for widget in (self.batch_label, self.batch_progress, self.batch_log):
            widget.hide()
            outer.addWidget(widget)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName('VideoProgress')
        self.progress_bar.setRange(0, 100)
        outer.addWidget(self.progress_bar)
        self.status = QLabel(tr('video_download.ready'))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName('Eyebrow')
        outer.addWidget(self.status)
        footer = QHBoxLayout()
        self.more_menu = QMenu(self)
        self.more_menu.setToolTipsVisible(True)
        self.demo = self.more_menu.addAction(tr('video_download.demo'), self._demo)
        self.demo.setToolTip(tr('video_download.demo_hint'))
        self.more_menu.addSeparator()
        self.original_action = self.more_menu.addAction(tr('video_download.original'), self._open_original)
        self.search_page_action = self.more_menu.addAction(tr('video_download.open_search'), self._open_search)
        self.add_up_action = self.more_menu.addAction(tr('video_download.add_up'), self._add_up)
        self.more_menu.addSeparator()
        self.folder_action = self.more_menu.addAction(tr('video_download.open_folder'), self._open_folder)
        self.open_file = self.more_menu.addAction(tr('video_download.open_file'), self._open_file)
        self.open_file.setEnabled(False)
        self.more = QPushButton(tr('video_download.more'))
        self.more.setAutoDefault(False)
        self.more.setMenu(self.more_menu)
        footer.addWidget(self.more)
        footer.addStretch()
        self.download = self._button('download', self._download)
        self.download.setObjectName('PrimaryButton')
        self.download.setEnabled(False)
        self.cancel = self._button('cancel', self._cancel)
        self.cancel.setEnabled(False)
        self.cancel.hide()
        footer.addWidget(self.download)
        footer.addWidget(self.cancel)
        footer.addWidget(self._button('close', self.reject))
        outer.addLayout(footer)
        self._update_download()

    def _populate_tasks(self, *_):
        previous = self.tasks.currentData()
        items = [item for item in self.snapshot_items
                 if item.get('video_type', 'news') == 'launch' or self.include_news.isChecked()]
        mode = self.order.currentData()
        if mode != 'relevance':
            def date_key(item):
                date = parse_datetime(item.get('published', ''))
                return (date is None, (-1 if mode == 'desc' else 1)*date.timestamp() if date else 0)
            items.sort(key=date_key)
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for item in items:
            # The exporter's summary is the mission; title is the rocket name.
            name = item.get('summary') if item.get('video_type') == 'launch' else item.get('title')
            name = name or item.get('title', '')
            date = parse_datetime(item.get('published', ''))
            label = str(item.get('published', ''))[:10]
            try:
                if date is not None:
                    label = date.astimezone(service.VIDEO_TIMEZONE).strftime('%Y-%m-%d')
            except (ValueError, OverflowError):
                pass
            self.tasks.addItem(label+' · '+str(name), item)
        selected = next((i for i, item in enumerate(items) if item == previous), 0)
        self.tasks.setCurrentIndex(selected if items else -1)
        self.tasks.blockSignals(False)
        if self.tasks.currentData() != previous:
            self._choose_task()
        else:
            self._update_date_range()

    def _choose_task(self, *_):
        self._update_date_range()
        item = self.tasks.currentData()
        if item is None:
            return
        if item.get('video_type') == 'launch':
            queries = service.mission_search_keywords(item)
            self.keyword.blockSignals(True)
            self.keyword.clear()
            self.keyword.addItems(queries)
            self.keyword.blockSignals(False)
            keyword = queries[0] if queries else ''
        else:
            words = item.get('keywords', [])
            keyword = ' '.join(str(word) for word in words if word) if isinstance(words, list) else words
            keyword = keyword or item.get('title', '')
        self.keyword.setEditText(str(keyword)[:200])

    def _task_range(self):
        item = self.tasks.currentData()
        return service.mission_date_range(item) if self.use_task_date.isChecked() and item is not None else None

    def _update_date_range(self, *_):
        if hasattr(self, 'results') and self.worker is None:
            self._abort_thumbnails()
            self.results.clear()
            self.url.clear()
            self._invalidate()
        try:
            window = self._task_range()
            if window is None:
                text = tr('video_download.date_unrestricted')
            else:
                begin, end = (datetime.fromtimestamp(stamp, service.VIDEO_TIMEZONE).strftime('%Y-%m-%d')
                              for stamp in (window[0], window[1]-1))
                text = tr('video_download.date_window', begin=begin, end=end)
        except service.VideoDownloadError:
            text = tr('video_download.error_task_date')
        self.date_hint.setText(text)
        self._update_download()

    def _search_options(self):
        ids = service.parse_up_ids(self.up_ids.text())
        state.save_preferences(self.workspace, ids, self.order.currentData())
        keyword = self.keyword.currentText()
        item = self.tasks.currentData()
        queries = service.mission_search_keywords(item) if item and item.get('video_type') == 'launch' else []
        fallbacks = [query for query in queries if query != keyword] if keyword in queries else []
        return dict(keyword=keyword, up_ids=ids, prefer_up_ids=True, fallback_keywords=fallbacks,
                    order=self.order.currentData(), date_range=self._task_range())

    def _demo(self):
        if self.worker is not None:
            return
        items = [item for item in self.snapshot_items if item.get('video_type') == 'launch']
        if not items or not self.use_task_date.isChecked():
            self._failed(service.VideoDownloadError('demo_task'))
            return
        if not self.rights.isChecked():
            self._failed(service.VideoDownloadError('demo_rights'))
            return
        try:
            ids = service.parse_up_ids(self.up_ids.text())
            state.save_preferences(self.workspace, ids, self.order.currentData())
            options = dict(items=items, up_ids=ids, order=self.order.currentData(), prefer_up_ids=True)
        except (ValueError, OSError) as error:
            self._failed(error if isinstance(error, service.VideoDownloadError)
                         else service.VideoDownloadError('preferences'))
            return
        self._abort_thumbnails()
        self.results.clear()
        self._invalidate()
        self.downloaded_file = None
        self.open_file.setEnabled(False)
        self.batch_outcomes = []
        self.batch_directory = None
        self.batch_log.clear()
        self.batch_progress.setRange(0, len(items))
        self.batch_progress.setValue(0)
        self.batch_label.setText(tr('video_download.demo_working'))
        for widget in (self.batch_label, self.batch_progress, self.batch_log):
            widget.show()
        self.resize(self.width(), max(self.height(), 860))
        self._begin('demo', options)

    def _save_preferences(self):
        try:
            ids = service.parse_up_ids(self.up_ids.text())
            state.save_preferences(self.workspace, ids, self.order.currentData())
        except (ValueError, OSError) as error:
            self._failed(error if isinstance(error, service.VideoDownloadError)
                         else service.VideoDownloadError('preferences'))

    def _order_changed(self, *_):
        self._populate_tasks()
        self._save_preferences()

    def _add_up(self):
        item = self.results.currentItem()
        info = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if info is None or not info.author_id:
            self._failed(service.VideoDownloadError('up_missing'))
            return
        try:
            ids = service.parse_up_ids(self.up_ids.text()+', '+info.author_id)
        except service.VideoDownloadError as error:
            self._failed(error)
            return
        self.up_ids.setText(', '.join(ids))
        self._save_preferences()

    def _restore_login(self):
        if not self._restore_pending or self._close_requested:
            return
        if self.worker is not None or self.login_dialog is not None:
            return
        self._restore_pending = False
        try:
            if state.state_path(self.workspace, '.bilibili-session.bin').is_file():
                self.auth_status.setText(tr('video_download.restore_login_working'))
                self._begin('restore_login', None)
        except (ValueError, OSError) as error:
            self._failed(error if isinstance(error, service.VideoDownloadError)
                         else service.VideoDownloadError('session_store'))

    def _invalidate(self):
        self.info = None
        self.formats.clear()
        self.details.setText(tr('video_download.select_hint'))
        self._update_download()

    def _update_download(self, *_):
        if not hasattr(self, 'download'):
            return
        idle = self.worker is None
        has_keyword = bool(self.keyword.currentText().strip())
        has_url = bool(self.url.text().strip())
        self.search_button.setEnabled(idle and has_keyword)
        self.parse_button.setEnabled(idle and has_url)
        self.parse_button.setText(tr('video_download.reparse' if self.info else 'video_download.parse'))
        self.original_action.setEnabled(idle and has_url)
        self.search_page_action.setEnabled(idle and has_keyword)
        row = self.results.currentItem()
        selected_video = row.data(Qt.ItemDataRole.UserRole) if row else None
        self.add_up_action.setEnabled(idle and selected_video is not None and bool(selected_video.author_id))
        folder = self.downloaded_file.parent if self.downloaded_file else (self.batch_directory or self.workspace/'video-downloads')
        self.folder_action.setEnabled(folder.is_dir())
        self.cancel.setVisible(not idle)
        self.download.setToolTip(tr('video_download.confirm_rights' if self.info and not self.rights.isChecked()
                                   else 'video_download.select_hint' if self.info is None else 'video_download.parsed'))
        self.demo.setEnabled(self.worker is None and self.rights.isChecked()
                             and self.use_task_date.isChecked()
                             and any(item.get('video_type') == 'launch' for item in self.snapshot_items))
        selected = self.formats.currentData()
        self.download.setEnabled(self.worker is None and self.info is not None
            and selected is not None and self.rights.isChecked()
            and (not selected.requires_merge or shutil.which('ffmpeg') is not None))

    def _begin(self, action, value):
        if self.worker is not None:
            return
        self.status.setText(tr('video_download.'+action+'_working'))
        self.progress_bar.setRange(0, 0)
        self.inputs.setEnabled(False)
        self.login_button.setEnabled(False)
        self.download.setEnabled(False)
        self.demo.setEnabled(False)
        self.cancel.setEnabled(True)
        self.worker = VideoDownloadWorker(action, value, self.workspace, self, cookies=self.cookies)
        self.worker.result.connect(self._result)
        self.worker.search_ready.connect(self._show_search_results)
        self.worker.video_ready.connect(self._show_demo_video)
        self.worker.task_started.connect(self._task_started)
        self.worker.task_finished.connect(self._task_finished)
        self.worker.progress.connect(self._progress)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.finished.connect(self._finished)
        self._update_download()
        self.worker.start()

    def _search(self):
        if self.worker is not None:
            return
        self._abort_thumbnails()
        self.results.clear()
        self.url.clear()
        self._invalidate()
        try:
            options = self._search_options()
        except (ValueError, OSError) as error:
            self._failed(error if isinstance(error, service.VideoDownloadError)
                         else service.VideoDownloadError('preferences'))
            return
        self._begin('search', options)

    def _parse(self):
        if self.worker is not None:
            return
        try:
            url = service.normalize_video_url(self.url.text())
        except service.VideoDownloadError as error:
            self._failed(error)
            return
        self._invalidate()
        self._begin('parse', url)

    def _download(self):
        if self.download.isEnabled():
            self.downloaded_file = None
            self.open_file.setEnabled(False)
            self._begin('download', (self.info, self.formats.currentData()))

    def _select_result(self, item, *_):
        if self.worker is not None:
            return
        if item is not None:
            video = item.data(Qt.ItemDataRole.UserRole)
            self.url.setText(video.url)
        else:
            self.url.clear()
        self._update_download()

    def _result(self, value):
        action = self.worker.action
        if action == 'restore_login':
            cookies, verified = value
            if self._close_requested:
                if cookies is not None:
                    cookies.clear()
                return
            self.cookies = cookies
            if cookies is not None:
                self.login_button.setText(tr('video_download.logout'))
                key = 'logged_in' if verified else 'login_unverified'
                self.auth_status.setText(tr('video_download.'+key))
                self.status.setText(tr('video_download.'+key))
            else:
                self.auth_status.setText(tr('video_download.logged_out'))
                self.status.setText(tr('video_download.session_expired'))
        elif action == 'search':
            self._show_search_results(value)
        elif action == 'parse':
            self._show_parsed_video(value)
        elif action == 'demo':
            successes = sum(item['path'] is not None for item in value)
            key = 'batch_done' if successes else 'batch_no_success'
            self.status.setText(tr('video_download.'+key, total=len(value),
                                   success=successes, failed=len(value)-successes, folder=value[0]['directory']))
            self.batch_label.setText(self.status.text())
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100 if value[-1]['path'] is not None else 0)
        else:
            self.downloaded_file = Path(value)
            self.open_file.setEnabled(True)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.status.setText(tr('video_download.done', path=value))

    def _show_search_results(self, value):
        for info in value.items:
            row = QListWidgetItem(f'{info.title}\n{info.platform} · {info.author} · '
                                  f'{info.duration} · {info.published}')
            row.setData(Qt.ItemDataRole.UserRole, info)
            row.setToolTip(info.title+'\n'+info.url)
            self.results.addItem(row)
            self._thumbnail(info, row)
        self.status.setText(tr('video_download.search_done', count=len(value.items),
                               method=tr('video_download.method_'+value.method)))
        if value.keyword:
            self.status.setText(self.status.text()+'\n'+tr('video_download.used_query', keyword=value.keyword))
        if value.warning:
            self.status.setText(self.status.text()+'\n'+tr('video_download.search_partial')+'\n'+value.warning)

    def _show_parsed_video(self, value):
        self.info = value
        self.url.blockSignals(True)
        self.url.setText(value.url)
        self.url.blockSignals(False)
        self.details.setText(f'{value.title}\n{value.platform} · {value.author} · '
                             f'{value.duration} s · {value.published}')
        selected = service.preferred_format(value.formats)
        for fmt in value.formats:
            label = f'{fmt.height or "?"}p · {fmt.ext.upper()} · {fmt.codec}'
            if fmt.requires_merge:
                label += ' · '+tr('video_download.merge')
                if not shutil.which('ffmpeg'):
                    label += ' · '+tr('video_download.needs_ffmpeg')
            self.formats.addItem(label, fmt)
            if fmt == selected:
                self.formats.setCurrentIndex(self.formats.count()-1)
        self.status.setText(tr('video_download.parsed'))

    def _show_demo_video(self, value):
        info, selected = value
        self._show_parsed_video(info)
        for index in range(self.formats.count()):
            if self.formats.itemData(index) == selected:
                self.formats.setCurrentIndex(index)
                break
        self.status.setText(tr('video_download.download_working'))

    def _task_started(self, value):
        index, total, item, keyword, directory = value
        self.batch_directory = directory
        self._abort_thumbnails()
        self.results.clear()
        self._invalidate()
        for task_index in range(self.tasks.count()):
            if self.tasks.itemData(task_index) == item:
                self.tasks.setCurrentIndex(task_index)
                break
        self.keyword.setEditText(keyword[:200])
        self._update_date_range()
        self.batch_label.setText(tr('video_download.batch_current', index=index, total=total, name=keyword))
        self.progress_bar.setRange(0, 0)

    def _task_finished(self, outcome):
        self.batch_outcomes.append(outcome)
        self.batch_progress.setValue(len(self.batch_outcomes))
        if outcome['path'] is not None:
            self.downloaded_file = Path(outcome['path'])
            self.open_file.setEnabled(True)
            text = tr('video_download.batch_saved', path=outcome['path'])
        else:
            error = outcome['error']
            text = tr('video_download.error_'+error.code)
            if error.detail:
                text += '\n'+error.detail
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        self.batch_log.appendPlainText(f"{outcome['index']}/{outcome['total']} · {outcome['keyword']}\n{text}\n")

    def _cancelled(self):
        if self.worker.action == 'demo':
            success = sum(item['path'] is not None for item in self.batch_outcomes)
            text = tr('video_download.batch_cancelled', processed=len(self.batch_outcomes),
                      total=self.batch_progress.maximum(), success=success)
            self.batch_label.setText(text)
            self.status.setText(text)
        else:
            self.status.setText(tr('video_download.cancelled'))

    def _thumbnail(self, info, row):
        if not info.thumbnail:
            return
        request = QNetworkRequest(QUrl(info.thumbnail))
        request.setTransferTimeout(8000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        reply = self._network.get(request)
        self._replies.add(reply)
        reply.readyRead.connect(lambda: reply.abort() if reply.bytesAvailable() > 2*1024*1024 else None)

        def done():
            if reply in self._replies:
                self._replies.remove(reply)
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    pixmap = QPixmap()
                    if pixmap.loadFromData(reply.readAll()) and self.results.row(row) >= 0:
                        row.setIcon(QIcon(pixmap.scaled(128, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation)))
            reply.deleteLater()

        reply.finished.connect(done)

    def _abort_thumbnails(self):
        replies = list(self._replies)
        self._replies.clear()
        for reply in replies:
            reply.abort()

    def _progress(self, event):
        if event.percent is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(round(event.percent))
        text = tr('video_download.phase_'+event.phase)
        if event.percent is not None:
            text += f' · {event.percent:.1f}%'
        if event.speed is not None:
            text += f' · {event.speed/1024/1024:.2f} MB/s'
        if event.eta is not None:
            text += f' · {tr("video_download.eta", seconds=round(event.eta))}'
        self.status.setText(text)

    def _failed(self, error):
        if self.worker is not None and self.worker.action == 'restore_login':
            self.auth_status.setText(tr('video_download.logged_out'))
        message = tr('video_download.error_'+error.code)
        if error.detail:
            message += '\n'+error.detail
        self.status.setText(message)
        if self.worker is not None and self.worker.action == 'demo':
            self.batch_label.setText(message)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

    def _finished(self):
        if self.worker.action == 'restore_login' and self.cookies is None:
            self.auth_status.setText(tr('video_download.logged_out'))
        self.worker.deleteLater()
        self.worker = None
        self.inputs.setEnabled(True)
        self.login_button.setEnabled(True)
        self.cancel.setEnabled(False)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        self._update_download()
        if self._close_requested:
            self.reject()
        elif self._restore_pending:
            QTimer.singleShot(0, self._restore_login)

    def _cancel(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)
            self.status.setText(tr('video_download.canceling'))

    def _login(self):
        if self.worker is not None or self.login_dialog is not None:
            return
        self._restore_pending = False
        if self.cookies is not None:
            try:
                state.clear_session(self.workspace)
            except (ValueError, OSError):
                self._failed(service.VideoDownloadError('session_store'))
                return
            self.cookies.clear()
            self.cookies = None
            self.login_button.setText(tr('video_download.login'))
            self.auth_status.setText(tr('video_download.logged_out'))
            self._invalidate()
            self.status.setText(tr('video_download.logged_out'))
            return
        self.login_dialog = BilibiliLoginDialog(self)
        dialog = self.login_dialog
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.cookies = dialog.cookies
                dialog.cookies = None
                self._invalidate()
                self.login_button.setText(tr('video_download.logout'))
                try:
                    state.save_session(self.workspace, self.cookies)
                except (ValueError, OSError):
                    self.auth_status.setText(tr('video_download.login_unsaved'))
                    self.status.setText(tr('video_download.login_unsaved'))
                else:
                    self.auth_status.setText(tr('video_download.logged_in'))
                    self.status.setText(tr('video_download.login_done'))
        finally:
            self.login_dialog = None
            dialog.deleteLater()
        if self._close_requested:
            self.reject()

    def _open_search(self):
        try:
            if self.keyword.currentText().strip():
                QDesktopServices.openUrl(QUrl(service.search_page_url(
                    self.keyword.currentText(), self.order.currentData(), self._task_range())))
        except service.VideoDownloadError as error:
            self._failed(error)

    def _open_original(self):
        try:
            url = service.normalize_video_url(self.url.text())
        except service.VideoDownloadError as error:
            self._failed(error)
        else:
            QDesktopServices.openUrl(QUrl(url))

    def _open_folder(self):
        folder = self.downloaded_file.parent if self.downloaded_file else (self.batch_directory or self.workspace/'video-downloads')
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _open_file(self):
        if self.downloaded_file and self.downloaded_file.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.downloaded_file)))

    def reject(self):
        if not self._close_requested and self.worker is None:
            self._save_preferences()
        self._close_requested = True
        self._restore_pending = False
        self._abort_thumbnails()
        if self.login_dialog is not None:
            self._close_requested = True
            self.login_dialog.reject()
            return
        if self.worker is not None:
            self._close_requested = True
            self._cancel()
            return
        if self.cookies is not None:
            self.cookies.clear()
            self.cookies = None
        super().reject()

    def closeEvent(self, event):
        self.reject()
        if self.worker is not None or self.login_dialog is not None:
            event.ignore()
        else:
            event.accept()
