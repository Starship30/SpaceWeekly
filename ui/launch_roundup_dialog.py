from ui.component import ProductionComponent,SegmentedTabs,configure_table,StatusDelegate
"""Weekly launch roundup: explicit data, script editing, optional speech, handoff."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QDate, Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QDateEdit, QTableWidget, QTableWidgetItem, QListWidget, QPlainTextEdit,
    QLineEdit, QDoubleSpinBox, QSpinBox, QFileDialog, QMessageBox, QSplitter, QAbstractItemView,QCheckBox)
from ui.widgets.combo_box import RoundedComboBox as QComboBox

from resources import resource_path
from i18n import tr
from services.cancellation import CancellationToken, TaskCancelledError
from services.launch_data import load_existing, refresh_collection, save_collection, write_json
from services import launch_roundup as roundup
from services.roundup_audio import current_audio, generate_audio, speech_text, reading_text
from services.roundup_polish import polish_segments, apply_polish, organize_segments
from services.roundup_export import export_complete_video, export_package
from ai.qwen_tts import QwenTTSProvider, capabilities


class RoundupWorker(QThread):
    def __init__(self, action, payload, parent=None, *, protect_cache=True):
        super().__init__(parent)
        self.action, self.payload = action, payload
        self.token = CancellationToken()
        self.value, self.error, self.cancelled = None, '', False
        self.protect_cache=protect_cache

    def run(self):
        try:
            from contextlib import nullcontext
            from services.cache_maintenance import using_media
            with using_media() if self.protect_cache else nullcontext():
                self.value = self.action(self.payload, self.token)
        except TaskCancelledError:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)


class LaunchRoundupDialog(ProductionComponent):
    tts_state_changed = Signal(str)
    job_changed = Signal()
    draft_changed = Signal()
    preview_ready = Signal(object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, None
        self._loading, self._current, self._close_after = False, -1, False
        self._voice_dirty = False
        self.setWindowTitle('每周发射综述')
        self.resize(1120, 780)
        warning = ''
        try:
            self.collection = load_existing(Path(settings.output_dir))
        except (OSError, ValueError, TypeError) as error:
            warning = '本地数据读取失败，请显式刷新；旧文件暂保留：' + str(error)
            self.collection = dict(items=[], complete=False, start='', end='', reason=warning)
        self.draft = roundup.create_draft(self.collection, *roundup.default_range(display_timezone=settings.display_timezone), settings.display_timezone)
        voice = resource_path('config', 'roundup_voice.json')
        if voice.exists():
            try:
                saved = json.loads(voice.read_text(encoding='utf-8'))
                settings_voice = dict(self.draft['tts'], **saved.get('tts', {}))
                rules = saved.get('pronunciations', {})
                if any(not isinstance(settings_voice[k], str) for k in ('python', 'model', 'speaker', 'style', 'device')):
                    raise ValueError('语音字段必须是文本')
                settings_voice['timeout'] = max(10, min(3600, int(settings_voice.get('timeout', 600))))
                speech_text('', rules)
                self.draft['tts'], self.draft['pronunciations'] = settings_voice, rules
                from services.roundup_sync import DEFAULT_ALIGNER,validate_aligner
                alignment=dict(DEFAULT_ALIGNER,**saved.get('aligner',{}));validate_aligner(alignment)
                self.draft['aligner']=alignment
            except (OSError, ValueError, TypeError, AttributeError) as error:
                warning = '独立语音配置读取失败，请重新配置；稿件仍可使用：' + str(error)
        else:
            from translator.dictionary import load_dictionary
            self.draft['pronunciations'] = {k: v['translation'] for k, v in load_dictionary().items()
                                            if v.get('human_status') == 'confirmed'}
        self._build()
        self.tts_provider = QwenTTSProvider(self.draft['tts'], self.tts_state_changed.emit)
        self.tts_state_changed.connect(self._tts_state)
        self._show_draft()
        if warning:
            self.status.setText(warning)

    def _button(self, layout, text, action):
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setProperty('roundupAction', getattr(action, '__name__', ''))
        button.clicked.connect(lambda _checked=False: self._guard(action))
        layout.addWidget(button)
        return button

    def _guard(self, action):
        try:
            action()
        except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as error:
            if getattr(self,'workflow_embedded',False):
                self.status.setText(str(error))
                if hasattr(self,'inline_error'):self.inline_error(str(error))
            else:QMessageBox.warning(self, '每周发射综述', str(error))

    def _build(self):
        outer = QVBoxLayout(self)
        self.tabs = SegmentedTabs()
        outer.addWidget(self.tabs)
        data_page = QWidget()
        self.data_page = data_page
        layout = QVBoxLayout(data_page)
        dates = QHBoxLayout()
        self.start, self.end = QDateEdit(), QDateEdit()
        self.date_label = QLabel('日期')
        for label, edit in [(self.date_label, self.start), (QLabel('至'), self.end)]:
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            edit.setMaximumWidth(145)
            dates.addWidget(label)
            dates.addWidget(edit)
        self._button(dates, '最近7天', self._default_dates).setToolTip('最近七个完整自然日，不含今天')
        dates.addStretch(1)
        self._button(dates, '联网刷新', self._refresh).setObjectName('PrimaryButton')
        self.range_timer = QTimer(self)
        self.range_timer.setSingleShot(True)
        self.range_timer.setInterval(500)
        self.range_timer.timeout.connect(lambda: self._guard(self._apply_range))
        for edit in (self.start, self.end):
            edit.dateChanged.connect(lambda *_: self.range_timer.start() if not self._loading else None)
        layout.addLayout(dates)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        summary=QWidget();summary_row=QHBoxLayout(summary);summary_row.setContentsMargins(0,0,0,0)
        self.include_all=QCheckBox('全选');self.include_all.stateChanged.connect(self._include_all)
        summary_row.addWidget(self.include_all);summary_row.addWidget(self.stats,1);layout.addWidget(summary)
        self.tasks = QTableWidget(0, 14)
        configure_table(self.tasks);self.tasks.setItemDelegateForColumn(13,StatusDelegate(self.tasks))
        self.tasks.setHorizontalHeaderLabels(['纳入', '重点', '任务名（悬停查看ID）', '分类', '分组（可编辑）', '北京时间', '发射地国家', '机构',
                                              '资料准备', '资料来源', '独立来源支持', '人工确认（名称）', '火箭', '发射结果'])
        for index in (11, 10, 9, 8):
            self.tasks.horizontalHeader().moveSection(self.tasks.horizontalHeader().visualIndex(index), 2)
        self.tasks.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.tasks)
        actions = QHBoxLayout()
        self._button(actions, '按资料推荐重点', self._recommend)
        self._button(actions, '任务上移', lambda: self._move_task(-1))
        self._button(actions, '任务下移', lambda: self._move_task(1))
        self._button(actions, '查看任务出处', self._task_sources)
        self._button(actions, '关联本地素材', self._associate)
        layout.addLayout(actions)
        actions = QHBoxLayout()
        self._button(actions, '补充缺失资料（联网）', self._supplement_materials)
        self._button(actions, '重新核对选中任务（联网）', self._recheck_materials)
        self._button(actions, '同步已保存资料（本地）', self._sync_materials)
        layout.addLayout(actions)
        hint = QLabel('资料操作沿用当前搜索服务，临时启用本次联网核对；不改变网络开关。引文匹配与转述支持分别核验，机器复核不等于人工确认。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs.addTab(data_page, '1 · 日期、任务与统计')

        script_page = QWidget()
        self.script_page = script_page
        layout = QVBoxLayout(script_page)
        actions = QHBoxLayout()
        self.target = QComboBox()
        self.target.addItems(['60', '90', '120'])
        actions.addWidget(QLabel('目标口播秒数（非实际时长）'))
        actions.addWidget(self.target)
        self._button(actions, '生成 AI 综述（整稿重写）', self._generate_all)
        self._button(actions, '只重写选中段', self._generate_one)
        self._button(actions, '打开草稿', self._open)
        self._button(actions, '保存草稿', self._save)
        self._button(actions, '写作规则', self._writing_rules)
        layout.addLayout(actions)
        self.script_origin = QLabel('尚无稿件；AI综述与本地模板稿会分别标记')
        self.script_origin.setWordWrap(True)
        layout.addWidget(self.script_origin)
        split = QSplitter()
        self.segment_list = QListWidget()
        self.segment_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.segment_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.segment_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.segment_list.setToolTip('Ctrl/Shift多选；口播润色只作用于选中的重点/补充段落')
        self.segment_list.currentRowChanged.connect(self._select_segment)
        split.addWidget(self.segment_list)
        editor = QWidget()
        form = QFormLayout(editor)
        self.spoken, self.screen, self.narration_edit = QPlainTextEdit(), QPlainTextEdit(), QPlainTextEdit()
        self.text_tabs = SegmentedTabs()
        self.text_tabs.addTab(self.spoken, '事实原稿')
        self.text_tabs.addTab(self.screen, '屏幕文字')
        reading_page = QWidget()
        reading_layout = QVBoxLayout(reading_page)
        reading_layout.addWidget(QLabel('朗读文字单独保存；留空沿用原稿。人工编辑仍需复核事实。'))
        reading_layout.addWidget(self.narration_edit)
        self.reading_input_label=QLabel('实际送入TTS的文本（含发音替换，只读预览）')
        reading_layout.addWidget(self.reading_input_label)
        self.reading_preview = QPlainTextEdit()
        self.reading_preview.setReadOnly(True)
        self.reading_preview.setMaximumHeight(90)
        reading_layout.addWidget(self.reading_preview)
        self.text_tabs.addTab(reading_page, '最终朗读')
        self.names = QLineEdit()
        self.names.setReadOnly(True)
        self.ids = QLineEdit()
        self.preview_seconds = QDoubleSpinBox()
        self.preview_seconds.setRange(.1, 600)
        self.preview_seconds.setSuffix(' 秒')
        for label, control in [('名称/原名（只读）', self.names), ('分别编辑和保存', self.text_tabs),
                               ('关联 LL2 ID（英文逗号分隔）', self.ids), ('无音频时预览时长', self.preview_seconds)]:
            form.addRow(label, control)
        self.audio_state = QLabel('尚无段落')
        self.audio_state.setWordWrap(True)
        form.addRow(self.audio_state)
        for edit in (self.spoken, self.screen):
            edit.textChanged.connect(self._store_segment)
        self.narration_edit.textChanged.connect(self._store_narration)
        self.ids.editingFinished.connect(self._store_segment)
        self.preview_seconds.valueChanged.connect(self._store_segment)
        split.addWidget(editor)
        split.setStretchFactor(1, 3)
        layout.addWidget(split)
        actions = QHBoxLayout()
        self._button(actions, '段落上移', lambda: self._move_segment(-1))
        self._button(actions, '段落下移', lambda: self._move_segment(1))
        self._button(actions, '新增人工段落', self._add_segment)
        self._button(actions, '删除选中段', self._remove_segment)
        self._button(actions, '查看段落出处', self._segment_sources)
        self._button(actions, '润色选中段口播', self._polish_selected)
        self._button(actions, '口播整理', self._organize_selected)
        layout.addLayout(actions)
        self.tabs.addTab(script_page, '2 · 结构化稿件与编辑')

        voice_page = QWidget()
        self.voice_page = voice_page
        layout = QVBoxLayout(voice_page)
        hint = QLabel('语音为可选功能：使用独立 Python 环境和已下载的 CustomVoice 权重。打开此窗口不会下载或加载模型。\n'
                      '配置说明见 tools/ROUNDUP.md；0.6B 不提供风格指令。试听和批量生成都按完整段落进行。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.voice_form = form
        self.python, self.model = QLineEdit(), QLineEdit()
        self.speaker = QComboBox()
        self.speaker.setEditable(True)
        self.style = QLineEdit()
        self.style_label = QLabel('风格指令（当前模型支持时显示）')
        self.device = QComboBox()
        self.device.addItems(['cpu', 'cuda:0'])
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 3600)
        self.pause = QDoubleSpinBox()
        self.pause.setRange(0, 3)
        self.pause.setSingleStep(.1)
        self.pronunciations = QPlainTextEdit()
        self.pronunciations.setMaximumHeight(150)
        for label, edit, choose in [('TTS 环境 python.exe', self.python, self._choose_python),
                                   ('本地 CustomVoice 模型目录', self.model, self._choose_model)]:
            row = QHBoxLayout()
            row.addWidget(edit)
            self._button(row, '浏览', choose)
            form.addRow(label, row)
        form.addRow('说话人（读取本地模型配置）', self.speaker)
        form.addRow(self.style_label, self.style)
        form.addRow('计算设备', self.device)
        form.addRow('单段语音超时（秒）', self.timeout)
        form.addRow('段间停顿（秒）', self.pause)
        form.addRow('发音替换 JSON（仅送给TTS，不改正式名称）', self.pronunciations)
        layout.addLayout(form)
        self.model.editingFinished.connect(self._capabilities)
        for control in (self.python, self.model, self.style):
            control.textChanged.connect(self._voice_changed)
        self.speaker.currentTextChanged.connect(self._voice_changed)
        self.speaker.currentIndexChanged.connect(self._speaker_controls)
        self.device.currentTextChanged.connect(self._voice_changed)
        self.timeout.valueChanged.connect(self._voice_changed)
        self.pronunciations.textChanged.connect(self._voice_changed)
        self.voice_info = QLabel()
        self.voice_info.setWordWrap(True)
        layout.addWidget(self.voice_info)
        actions = QHBoxLayout()
        self._button(actions, '保存独立语音配置', self._save_voice)
        self._button(actions, '试听选中段', self._listen)
        self._button(actions, '停止播放', self._stop_playback)
        self._button(actions, '生成缺失/过期段落', self._audio_all)
        self._button(actions, '强制重新生成选中段', self._audio_one)
        self._button(actions, '卸载语音模型', self._unload_voice)
        layout.addLayout(actions)
        self.export_controls = QWidget()
        export_layout = QVBoxLayout(self.export_controls)
        self._button(export_layout, '导出稿件与快照（含可用配音）', lambda: self._export(False))
        self._button(export_layout, '导出带配音的 MP4', lambda: self._export(True))
        export_layout.addWidget(QLabel(tr('production.legacy.60')))
        layout.addWidget(self.export_controls)
        layout.addStretch()
        self.tabs.addTab(voice_page, '3 · 可选配音、试听与导出')
        footer = QHBoxLayout()
        self.status = QLabel('读取本地数据；不会自动联网刷新')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.cancel = self._button(footer, '取消当前任务', self._cancel)
        self.cancel.setEnabled(False)
        outer.addLayout(footer)

    def _show_draft(self):
        roundup.sync_materials(self.draft)
        roundup.sync_names(self.draft)
        self._loading = True
        self.start.setDate(QDate.fromString(self.draft['start'], 'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(self.draft['end'], 'yyyy-MM-dd'))
        self.target.setCurrentText(str(self.draft['target_seconds']))
        voice = self.draft['tts']
        self.python.setText(voice['python'])
        self.model.setText(voice['model'])
        self.style.setText(voice.get('style', ''))
        device=voice.get('device','cpu')
        if self.device.findText(device)<0:self.device.addItem(device)
        self.device.setCurrentText(device)
        self.timeout.setValue(int(voice.get('timeout', 600)))
        self.pause.setValue(float(self.draft['pause_seconds']))
        self.pronunciations.setPlainText(json.dumps(self.draft['pronunciations'], ensure_ascii=False, indent=2))
        if hasattr(self,'align_python'):
            from services.roundup_sync import DEFAULT_ALIGNER
            aligner=dict(DEFAULT_ALIGNER,**self.draft.get('aligner',{}))
            self.align_python.setText(aligner['python']);self.align_model.setText(aligner['model'])
            self.align_device.setText(aligner['device']);self.align_language.setCurrentText(aligner['language'])
            self.align_timeout.setValue(int(aligner['timeout']))
        self._loading = False
        self.refresh_speakers(voice['speaker'])
        self._voice_dirty = False
        self._show_tasks()
        self._show_segments()
        self.draft_changed.emit()

    def _show_tasks(self):
        current=self.tasks.currentRow()
        selected_id=self.tasks.item(current,0).data(Qt.ItemDataRole.UserRole) if current>=0 and self.tasks.item(current,0) else None
        scroll=self.tasks.verticalScrollBar().value()
        self.date_label.setText('日期')
        self.tasks.horizontalHeaderItem(5).setText(roundup.timezone_label(self.draft))
        self._loading = True
        self.tasks.setRowCount(0)
        items = {i['ll2_id']: i for i in self.draft['snapshot']['items']}
        from services.launch_research import confirmed_values
        from services.roundup_style import event_result
        from translator.dictionary import load_dictionary
        terms=load_dictionary()
        from utils.time import format_datetime
        for row, selection in enumerate(self.draft['selection']):
            self.tasks.insertRow(row)
            item = items[selection['id']]
            names = roundup.display_names(item,terms)
            name = names['name']
            classification = {'actual': '实际发射', 'planned': '计划', 'delayed': '延期', 'cancelled': '取消', 'pending': '待确认'}
            state = roundup.material_status(item)
            values = ['', '', name, classification[roundup.category(item, roundup.parse_datetime(self.draft['as_of']))], selection['group'], format_datetime(item.get('net', ''), self.draft.get('timezone', 'Asia/Shanghai')),
                      item.get('site_country', ''), item.get('provider', ''),
                      state['preparation_label'], state['source_label'], state['independent_label'], state['human_label'],
                      names['rocket'], event_result(item,self.draft)]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole,selection['id']);cell.setToolTip(str(value))
                if column == 5:cell.setText(str(value)[:16])
                if 8 <= column <= 11:
                    cell.setToolTip(json.dumps(state, ensure_ascii=False, indent=2) + '\n独立支持指已有外部原句与事件匹配；机器复核不等于官方或人工确认。')
                if column in (0, 1):
                    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                    cell.setCheckState(Qt.CheckState.Checked if selection['included' if column == 0 else 'featured'] else Qt.CheckState.Unchecked)
                elif column != 4:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tasks.setItem(row, column, cell)
        self.tasks.resizeColumnsToContents()
        self.tasks.setColumnWidth(2, min(320, self.tasks.columnWidth(2)))
        if hasattr(self,'task_column_sizes'):self.task_column_sizes()
        self._loading = False
        if self.draft['selection']:
            row=next((n for n,s in enumerate(self.draft['selection']) if s['id']==selected_id),0)
            self.tasks.setCurrentCell(row,2)
        self.tasks.verticalScrollBar().setValue(scroll)
        self._show_stats()

    def _selection_changed(self, *_):
        if self._loading:
            return
        if _ and hasattr(_[0], 'column') and _[0].column() == 1:
            self.draft['focus_mode'] = 'manual'
        for row, item in enumerate(self.draft['selection']):
            item['included'] = self.tasks.item(row, 0).checkState() == Qt.CheckState.Checked
            item['featured'] = self.tasks.item(row, 1).checkState() == Qt.CheckState.Checked
            item['group'] = self.tasks.item(row, 4).text()
        self._show_stats()

    def _include_all(self,state):
        if self._loading or self.worker:return
        self._loading=True
        for row in range(self.tasks.rowCount()):self.tasks.item(row,0).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self._loading=False;self._selection_changed()

    def _show_stats(self):
        stats = roundup.statistics(self.draft)
        self.include_all.blockSignals(True);self.include_all.setChecked(bool(self.draft['selection']) and all(s['included'] for s in self.draft['selection']));self.include_all.blockSignals(False)
        self.stats.setText(f"已选{stats['total']}项，已发射{stats['actual']}项，成功{stats['results']['success']}项"
            + (' · 当前收录不完整或仅选中部分任务' if not stats['complete'] else ''))
        self.stats.setToolTip(roundup.overview(self.draft))

    def _store_segment(self, *_):
        if self._loading or not 0 <= self._current < len(self.draft['segments']):
            return
        segment = self.draft['segments'][self._current]
        ids = [i.strip() for i in self.ids.text().split(',') if i.strip()]
        new = dict(spoken=self.spoken.toPlainText(), screen=self.screen.toPlainText(), task_ids=ids)
        if any(segment.get(key) != value for key, value in new.items()):
            segment.update(new, edited=True)
        segment['preview_seconds'] = self.preview_seconds.value()
        self.audio_state.setText('人工编辑' if segment.get('edited') else '资料约束生成')
        audio = current_audio(self.draft, segment) if not self._voice_dirty else None
        self.audio_state.setText(self.audio_state.text() + (f" · 当前配音 {audio['duration_seconds']:.2f} 秒" if audio else
            ' · 旧音频需重新生成' if segment.get('audio') else ' · 尚未生成配音'))
        self._refresh_reading()

    def _refresh_reading(self):
        if not 0 <= self._current < len(self.draft['segments']):
            return
        segment = self.draft['segments'][self._current]
        value = roundup.current_narration(segment)
        if segment.get('narration') and not value:
            self.audio_state.setText(self.audio_state.text() + ' · 朗读润色已过期，当前使用事实原稿')
        elif value:
            label = {'ai_polish':'AI口播润色（非人工确认）', 'event_organize':'口播整理（待复核）'}.get(value.get('origin'), '人工朗读编辑（待复核）')
            self.audio_state.setText(self.audio_state.text() + ' · ' + label)
        names = roundup.task_names(self.draft, segment['task_ids'])
        self.names.setText(' / '.join(n['confirmed_chinese_name'] or n['original_name'] for n in names))
        self.names.setToolTip(json.dumps(names, ensure_ascii=False, indent=2))
        try:
            rules = json.loads(self.pronunciations.toPlainText())
            self.reading_preview.setPlainText(speech_text(roundup.narration_text(segment), rules))
        except (ValueError, TypeError) as error:
            self.reading_preview.setPlainText('发音规则尚未有效：' + str(error))

    def _store_narration(self):
        if self._loading or not 0 <= self._current < len(self.draft['segments']):
            return
        segment = self.draft['segments'][self._current]
        text = self.narration_edit.toPlainText()
        if not text.strip():
            segment.pop('narration', None)
        elif text != (segment.get('narration') or {}).get('text') or not roundup.current_narration(segment):
            refs = [dict(s, rendered_text=text, support_review='manual_edit_unreviewed')
                    for s in roundup.narration_sources(segment)]
            segment['narration'] = dict(text=text, based_on=roundup.narration_basis(segment), origin='manual_edit',
                                        source_refs=refs, human_confirmed=False)
        self._store_segment()

    def _select_segment(self, index):
        if self._loading:
            return
        self._store_segment()
        self._current = index
        self._loading = True
        segment = self.draft['segments'][index] if index >= 0 else {}
        self.spoken.setPlainText(segment.get('spoken', ''))
        self.screen.setPlainText(segment.get('screen', ''))
        self.narration_edit.setPlainText((segment.get('narration') or {}).get('text', ''))
        self.ids.setText(','.join(segment.get('task_ids', [])))
        self.preview_seconds.setValue(segment.get('preview_seconds', 12))
        self._loading = False
        self._store_segment()

    def _show_segments(self, selected=0):
        self._loading = True
        self.segment_list.clear()
        for segment in self.draft['segments']:
            origin = {'ai': 'AI综述', 'local_template': '本地模板', 'program_statistics': '程序统计'}.get(segment.get('origin'), '来源未记录')
            self.segment_list.addItem(roundup.segment_title(self.draft, segment))
            self.segment_list.item(self.segment_list.count()-1).setToolTip(roundup.segment_title(self.draft,segment)+'\n'+origin)
        origins = set(s.get('origin', 'unknown') for s in self.draft['segments'])
        reasons = list(dict.fromkeys(s.get('generation_reason', '') for s in self.draft['segments'] if s.get('generation_reason')))
        label = 'AI综述（资料约束生成）' if origins == {'ai'} else '本地模板稿（非AI综述）' if origins == {'local_template'} else '混合稿/人工稿，请查看各段来源'
        detail = (label + ('；' + '；'.join(reasons) if reasons else '')) if origins else '尚无稿件'
        if any(s.get('edited') or (s.get('narration') or {}).get('origin') == 'manual_edit' for s in self.draft['segments']):
            detail += '（含人工编辑，需复查）'
        count = sum(bool(roundup.current_narration(s)) for s in self.draft['segments'])
        if count:
            detail += f'；{count}段有独立朗读文本'
        self.script_origin.setText(label + (' · 含人工编辑' if any(s.get('edited') for s in self.draft['segments']) else '') if origins else '尚无稿件')
        self.script_origin.setToolTip(detail)
        self._current = -1
        self._loading = False
        self.segment_list.setCurrentRow(selected if self.draft['segments'] else -1)

    def _sync(self, validate=True):
        if (self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd')) != (self.draft['start'], self.draft['end']):
            raise ValueError('日期已改动，请先应用范围；不会悄悄改变草稿快照')
        self._store_segment()
        self._selection_changed()
        self.draft['target_seconds'] = int(self.target.currentText())
        self.draft['pause_seconds'] = self.pause.value()
        self.draft.update(self.voice_settings())
        self._voice_dirty = False
        self._store_segment()
        if validate:
            roundup.validate_draft(self.draft)

    def voice_settings(self):
        voice=dict(tts=dict(python=self.python.text().strip(), model=self.model.text().strip(),
            speaker=self.speaker_id(), style=self.style.text() if not self.style.isHidden() else '',
            device=self.device.currentText(), timeout=self.timeout.value()))
        rules = json.loads(self.pronunciations.toPlainText())
        speech_text('', rules)
        voice['pronunciations'] = rules
        if hasattr(self,'align_python'):
            voice['aligner']=dict(python=self.align_python.text().strip(),model=self.align_model.text().strip(),
                device=self.align_device.text().strip() or 'cpu',language=self.align_language.currentText(),timeout=self.align_timeout.value())
        return voice

    def _default_dates(self):
        start, end = roundup.default_range(display_timezone=self.settings.display_timezone)
        self.start.setDate(QDate.fromString(start, 'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(end, 'yyyy-MM-dd'))

    def _allow_new_snapshot(self):
        return not self.draft['segments'] or QMessageBox.question(self, '新建快照草稿',
            '应用新日期/数据会新建草稿。当前稿件不会自动迁移，请先保存。继续？') == QMessageBox.StandardButton.Yes

    def _apply_range(self):
        if self._loading or (self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd')) == (self.draft['start'], self.draft['end']):
            return
        roundup.range_bounds(self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd'), self.settings.display_timezone)
        if self._allow_new_snapshot():
            self._checkpoint()
            self._new_snapshot(self.collection)
        else:
            self._loading = True
            self.start.setDate(QDate.fromString(self.draft['start'], 'yyyy-MM-dd'))
            self.end.setDate(QDate.fromString(self.draft['end'], 'yyyy-MM-dd'))
            self._loading = False

    def _new_snapshot(self, collection):
        voice, rules = deepcopy(self.draft['tts']), deepcopy(self.draft['pronunciations'])
        aligner=deepcopy(self.draft.get('aligner',{}))
        hold=self.draft.get('closing_hold_seconds',1.5)
        self.collection = collection
        self.draft = roundup.create_draft(collection, self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd'), self.settings.display_timezone)
        self.draft.update(tts=voice, pronunciations=rules,aligner=aligner,closing_hold_seconds=hold)
        self._show_draft()

    def _refresh(self):
        if not self._allow_new_snapshot():
            return
        start, end = self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd')
        roundup.range_bounds(start, end, self.settings.display_timezone)
        settings = self.settings
        def refresh(_data, token):
            data = refresh_collection(start, end, settings, token)
            if settings.aerospace_translation_enabled:
                from services.launch_research import LaunchResearchService
                service = LaunchResearchService(settings, token)
                for item in data['items']:
                    token.check()
                    try:
                        item['research'] = service.review(item)
                    except (OSError, ValueError) as error:
                        item['research'] = dict(evidence_status='pending', reason=str(error))
                data = save_collection(data['items'], start, end, data['complete'], data['reason'], data['pages'], display_timezone=settings.display_timezone)
            return data
        self._start_job('refresh', refresh, {})

    def _recommend(self):
        self._selection_changed()
        roundup.recommend(self.draft)
        self._show_tasks()

    def _checkpoint(self):
        if self.draft['segments']:
            path = resource_path('output', 'roundup-drafts', self.draft['id'] + '-' + uuid4().hex[:8] + '.json')
            write_json(path, self.draft)

    def _sync_materials(self):
        self._sync(validate=False)
        self._checkpoint()
        changed = roundup.sync_materials(self.draft)
        self._show_tasks()
        self.status.setText(f'已同步 {len(changed)} 项本地资料；未改稿件或人工编辑，统计有变化时请更新总览。')

    def _supplement_materials(self, recheck_id=None):
        self._sync(validate=False)
        self._checkpoint()
        self._start_job('materials', lambda data, token: roundup.prepare_materials(
            data, self.settings, token, recheck_id=recheck_id), deepcopy(self.draft))

    def _recheck_materials(self):
        row = self.tasks.currentRow()
        if row < 0:
            raise ValueError('请选择一项任务')
        self._supplement_materials(self.draft['selection'][row]['id'])

    def _move_task(self, direction):
        row = self.tasks.currentRow()
        if row < 0 or not 0 <= row + direction < len(self.draft['selection']):
            return
        self._selection_changed()
        rows = self.draft['selection']
        rows[row], rows[row+direction] = rows[row+direction], rows[row]
        self._show_tasks()
        self.tasks.setCurrentCell(row+direction, 2)

    def _move_segment(self, direction):
        self._store_segment()
        row = self._current
        if row < 0 or not 0 <= row+direction < len(self.draft['segments']):
            return
        rows = self.draft['segments']
        rows[row], rows[row+direction] = rows[row+direction], rows[row]
        self._show_segments(row+direction)

    def _add_segment(self):
        self._store_segment()
        self.draft['segments'].append(dict(id='manual_' + uuid4().hex[:8], kind='remaining', spoken='', screen='',
            task_ids=[], source_refs=[], edited=True, preview_seconds=12, audio=None))
        self._show_segments(len(self.draft['segments'])-1)

    def _remove_segment(self):
        if self._current >= 0:
            self.draft['segments'].pop(self._current)
            self._show_segments()

    def _generate_all(self):
        self._sync(validate=False)
        if self.draft['segments'] and QMessageBox.question(self, '整稿重写', '明确重写整稿会替换所有人工编辑。继续？') != QMessageBox.StandardButton.Yes:
            return
        self._checkpoint()
        self._start_job('script', lambda data, token: roundup.generate_script(data, self.settings, token), deepcopy(self.draft))

    def _generate_one(self):
        self._sync(validate=False)
        if self._current < 0:
            raise ValueError('请选择段落')
        identifier = self.draft['segments'][self._current]['id']
        segment = self.draft['segments'][self._current]
        if (segment.get('edited') or (segment.get('narration') or {}).get('origin') == 'manual_edit') and QMessageBox.question(self, '重写本段', '只替换本段的人工编辑，其他段保留。继续？') != QMessageBox.StandardButton.Yes:
            return
        if self.draft['segments'][self._current]['kind'] in ('overview', 'transition'):
            if self.draft.get('timezone', 'Asia/Shanghai') != self.settings.display_timezone:
                raise ValueError('草稿时区与当前显示设置不同，请先显式应用日期范围')
            text = roundup.opening(self.draft) if self.draft['segments'][self._current]['kind'] == 'overview' else roundup.transition(self.draft, roundup.statistics(self.draft))
            self.draft['segments'][self._current].update(spoken=text, screen=text,
                statistics_key=roundup.digest(roundup.statistics(self.draft)), edited=False, origin='program_statistics')
            self._show_segments(self._current)
            self.status.setText('开头/结尾已用当前范围及程序统计更新，无需调用文本模型')
            return
        self._single_id = identifier
        self._start_job('segment', lambda data, token: roundup.generate_script(data, self.settings, token, single_id=identifier), deepcopy(self.draft))

    def _polish_selected(self):
        self._sync()
        ids = [self.draft['segments'][self.segment_list.row(item)]['id'] for item in self.segment_list.selectedItems()]
        if not ids:
            raise ValueError('请选择重点或补充段落；可用Ctrl/Shift多选')
        selected = [s for s in self.draft['segments'] if s['id'] in ids]
        if any(s.get('edited') or (s.get('narration') or {}).get('origin') == 'manual_edit' for s in selected):
            if QMessageBox.question(self, '润色选中段口播', '只替换选中段的朗读文字，保留事实原稿、屏幕文字及其他段落；前后版本会保存。继续？') != QMessageBox.StandardButton.Yes:
                return
        self._checkpoint()
        self._start_job('polish', lambda data, token: polish_segments(data, ids, self.settings, token), deepcopy(self.draft))

    def _organize_selected(self):
        self._sync()
        ids = [self.draft['segments'][self.segment_list.row(item)]['id'] for item in self.segment_list.selectedItems()]
        self._review_narration(organize_segments(self.draft, ids), '口播整理')

    def _review_narration(self, result, title):
        dialog = QDialog(self)
        dialog.setWindowTitle(title + ' · 修改预览')
        dialog.resize(940, 620)
        layout = QVBoxLayout(dialog)
        note = QLabel('采用后只更新选中段口播；事实原稿、屏幕文字和前后版本保留。相关旧配音与对齐需重新生成。'
                      + ('本次按已有事件资料整理，省略额外背景，请核对后采用。' if result.get('operation') == 'event_organize' else '机器复核仍需人工检查。'))
        note.setWordWrap(True); layout.addWidget(note)
        tabs = SegmentedTabs()
        before, after, final = [], [], []
        for update in result['updates']:
            before.append(roundup.narration_text(update['before']))
            after.append(roundup.narration_text(update['after']))
            final.append(reading_text(self.draft, update['after']))
        for label, rows in [('修改前', before), ('整理后口播', after), ('最终 TTS 输入', final)]:
            editor = QPlainTextEdit('\n\n'.join(rows)); editor.setReadOnly(True); tabs.addTab(editor, label)
        layout.addWidget(tabs, 1)
        buttons = QHBoxLayout(); buttons.addStretch(1)
        cancel = QPushButton('保留原稿'); cancel.clicked.connect(dialog.reject); buttons.addWidget(cancel)
        accept = QPushButton('采用口播'); accept.setObjectName('PrimaryButton'); accept.clicked.connect(dialog.accept); buttons.addWidget(accept)
        layout.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status.setText('已保留原稿，未采用口播整理结果')
            return False
        self._checkpoint()
        apply_polish(self.draft, result)
        selected_id = result['updates'][0]['id']
        self._show_segments(next(i for i, s in enumerate(self.draft['segments']) if s['id'] == selected_id))
        self.text_tabs.setCurrentIndex(2)
        self.status.setText('已采用口播并保留前后版本；旧配音与时间标记需重新生成。读法请试听确认。')
        return True

    def _writing_rules(self):
        if hasattr(self, 'open_rules'):
            self.open_rules()
            return
        from ui.prompt_studio_dialog import PromptStudioDialog
        from services.settings import save_settings
        from services.prompt_manager import PROMPT_TYPES
        dialog = PromptStudioDialog(self.settings, self)
        dialog.type_list.setCurrentRow(PROMPT_TYPES.index('roundup'))
        if dialog.exec():
            self.settings = dialog.settings()
            save_settings(self.settings)
            self.status.setText('写作规则已保存；仅在主动生成、重写或润色时应用，已有稿件未改动')

    def _save(self):
        self._sync()
        path, _ = QFileDialog.getSaveFileName(self, '保存可继续编辑的草稿', str(Path(self.settings.output_dir) / 'launch-roundup.json'), 'JSON (*.json)')
        if path:
            roundup.save_draft(self.draft, path)
            self.status.setText('已保存草稿和固定数据快照：' + path)

    def _open(self):
        if self.draft['segments'] and QMessageBox.question(self, '打开草稿', '当前未保存的编辑会离开，继续？') != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开综述草稿', self.settings.output_dir, 'JSON (*.json)')
        if path:
            self.open_draft(path)

    def open_draft(self,path):
        if self.worker is not None:raise ValueError('请等待当前任务结束后打开草稿')
        draft = roundup.load_draft(path)
        changed = roundup.changed_tasks(draft, load_existing(Path(self.settings.output_dir)))
        self.draft = draft
        self._show_draft()
        self.status.setText(('保留草稿原快照。当前资料有变化：' + ', '.join(changed) if changed else '已打开草稿；保留原快照')
                            + '。可点击“同步已保存资料（本地）”采用新核对结果，人工编辑保留。')

    def _show_json(self, data):
        if hasattr(self, 'inspect_data'):
            self.inspect_data(data)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('资料与出处（只读引用）')
        dialog.resize(850, 600)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        layout.addWidget(text)
        dialog.exec()

    def _task_sources(self):
        row = self.tasks.currentRow()
        if row >= 0:
            identifier = self.draft['selection'][row]['id']
            material = next(m for m in roundup.writing_materials(self.draft) if m['task_id'] == identifier) if self.draft['selection'][row]['included'] else {}
            self._show_json(dict(task=next(i for i in self.draft['snapshot']['items'] if i['ll2_id'] == identifier),
                                 selection=self.draft['selection'][row], writing_material=material))

    def _segment_sources(self):
        if self._current >= 0:
            self._store_segment()
            self._show_json(self.draft['segments'][self._current])

    def _associate(self):
        row = self.tasks.currentRow()
        if row >= 0:
            paths, _ = QFileDialog.getOpenFileNames(self, '关联本地素材（保留原文件，不自动下载）', self.settings.output_dir)
            if paths:
                self.draft['selection'][row]['assets'] = paths
                self.status.setText('素材已关联，将在导出清单中保留原路径')

    def _choose_python(self):
        path, _ = QFileDialog.getOpenFileName(self, '独立 TTS Python', '', 'Python (python.exe python)')
        if path:
            self.python.setText(path)

    def _choose_model(self):
        path = QFileDialog.getExistingDirectory(self, '本地 CustomVoice 模型目录')
        if path:
            self.model.setText(path)
            self._capabilities()

    def _capabilities(self):
        self.refresh_speakers()

    def speaker_id(self):
        return self.speaker.currentData() or self.speaker.currentText().strip()

    def _speaker_controls(self,*_):
        supported=getattr(self,'_preset_style',False) and not self.speaker_id().startswith('my:')
        self.style.setVisible(supported);self.style_label.setVisible(supported)

    def refresh_speakers(self,previous=None):
        from services.voice_profiles import list_profiles
        from PySide6.QtCore import QSignalBlocker
        previous=previous or self.speaker_id() or 'Vivian'
        speakers=[]
        try:
            info = capabilities(self.model.text().strip())
            self._preset_style=info['style'];speakers=info['speakers']
            self.voice_info.setText('已读取本地配置（尚未加载权重）。模型大小：' + info['size'])
        except (OSError, ValueError, KeyError) as error:
            self._preset_style=False
            self.voice_info.setText(str(error))
        with QSignalBlocker(self.speaker):
            self.speaker.clear();self.speaker.addItems(speakers)
            for profile in list_profiles():self.speaker.addItem('我的音色 · '+profile['name'],profile['id'])
            index=self.speaker.findData(previous) if previous.startswith('my:') else self.speaker.findText(previous)
            if index<0:
                self.speaker.addItem('已移除的参考音色' if previous.startswith('my:') else previous,previous if previous.startswith('my:') else None)
                index=self.speaker.count()-1
            self.speaker.setCurrentIndex(index)
        self._speaker_controls()
        if not self._loading:self._voice_changed()

    def _save_voice(self):
        self._sync(validate=False)
        from services.voice_storage import validate_voice_locations
        for value in (self.draft['tts'],self.draft.get('aligner',{})):validate_voice_locations(value)
        write_json(resource_path('config', 'roundup_voice.json'), dict(tts=self.draft['tts'], pronunciations=self.draft['pronunciations'],aligner=self.draft.get('aligner',{})))
        self.status.setText('语音配置已独立保存，未修改文本模型配置')

    def _voice_changed(self, *_):
        if not self._loading:
            self._voice_dirty = True
            self.audio_state.setText('语音设置已变更，原音频需要重新核验；试听/导出时按新设置检查')
            self._refresh_reading()

    def _audio_job(self, identifier=None, force=False, play=False):
        if getattr(self,'voice_setup_busy',lambda:False)():raise ValueError('语音环境正在配置，请等待完成或取消')
        self._sync()
        if not self.draft['segments']:
            raise ValueError('请先生成稿件或添加人工段落')
        self._play_after = identifier if play else None
        settings = deepcopy(self.draft['tts'])
        needed = [s for s in self.draft['segments'] if (not identifier or s['id'] == identifier)
                  and (force or not current_audio(self.draft, s))]
        if needed:
            from services.voice_profiles import PREFIX,configured,load
            if hasattr(self,'request_voice_setup') and not configured(settings):
                self.request_voice_setup();self.status.setText('请先自动配置语音，或检查已有环境；稿件和无声预览仍可使用');return
            capabilities(load(settings['speaker'])['model'] if settings['speaker'].startswith(PREFIX) else settings['model'])
            if not settings['python'] or not Path(settings['python']).is_file():
                raise ValueError('请在语音页配置独立 TTS 环境的 python.exe；参见 tools/ROUNDUP.md。稿件可直接导出。')
        def synthesize(data, token):
            self.tts_provider.configure(settings)
            errors=generate_audio(data, self.tts_provider, token, segment_id=identifier, force=force)
            from services.roundup_sync import align_draft
            from ai.qwen_aligner import QwenForcedAligner
            aligner=data.get('aligner',{})
            if aligner.get('python') and aligner.get('model'):
                self.tts_provider.unload()
                align_draft(data,QwenForcedAligner(aligner,self.tts_state_changed.emit),token,segment_id=identifier)
            else:
                for s in data['segments']:
                    if not identifier or s['id']==identifier:s['alignment_error']='尚未配置强制对齐模型，保留配音并使用段落级展示'
            return errors
        self._start_job('audio', synthesize, deepcopy(self.draft))

    def _tts_state(self, message):
        self.voice_info.setText(message)
        if self.worker is not None and self._job_kind in ('audio', 'unload','align'):
            self.status.setText(message)

    def _unload_voice(self):
        self._start_job('unload', lambda _data, _token: self.tts_provider.unload(), {})

    def _audio_all(self):
        self._audio_job()

    def _audio_one(self):
        if self._current < 0:
            raise ValueError('请选择段落')
        self._audio_job(self.draft['segments'][self._current]['id'], True)

    def _listen(self):
        self._sync()
        if self._current < 0:
            raise ValueError('请在稿件页选择段落')
        segment = self.draft['segments'][self._current]
        sound = current_audio(self.draft, segment)
        if sound:
            self._play(sound['path'])
        else:
            self._audio_job(segment['id'], play=True)

    def _play(self, path):
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        if not hasattr(self, 'player'):
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.errorOccurred.connect(lambda *_: self.status.setText('播放失败：' + self.player.errorString()))
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.player.play()

    def _stop_playback(self):
        if hasattr(self, 'player'):
            self.player.stop()

    def _export(self, visuals):
        self._sync()
        output = self.export_directory() if hasattr(self, 'export_directory') else QFileDialog.getExistingDirectory(self, '选择导出目录', self.settings.output_dir)
        if output:
            if visuals:
                self._start_job('export', lambda data, token: export_complete_video([data], output, token), deepcopy(self.draft))
            else:
                intro=self.export_intro() if hasattr(self,'export_intro') else None
                self._start_job('export', lambda data, token: export_package(data, output, token, visuals=False,intro=intro), deepcopy(self.draft))

    def _start_job(self, kind, action, payload):
        if self.worker is not None:
            raise ValueError('请先等待当前任务完成或取消')
        self._stop_playback()
        self._job_kind = kind
        self.worker = RoundupWorker(action, payload, self)
        self.worker.finished.connect(self._finished)
        self._busy_inputs(True)
        self.cancel.setEnabled(True)
        self.status.setText('正在' + {'refresh':'刷新资料','script':'生成综述','segment':'重写段落','materials':'核对资料',
            'polish':'润色口播','audio':'生成配音与对齐','align':'强制对齐','preview_audio':'准备音画预览','export':'导出','unload':'卸载语音模型'}.get(kind,'处理任务') + '，可取消。')
        self.worker.start()
        self.job_changed.emit()

    def _busy_inputs(self, busy):
        self.include_all.setEnabled(not busy)
        if not getattr(self, 'workflow_embedded', False):
            self.tabs.setEnabled(not busy)
            return
        for page in (self.data_page, self.script_page, self.voice_page, self.export_controls):
            for kind in (QPlainTextEdit, QLineEdit):
                for widget in page.findChildren(kind):
                    widget.setReadOnly(busy or widget in (self.names, self.reading_preview))
            for widget in page.findChildren(QPushButton):
                if widget.property('roundupAction') not in ('_task_sources', '_segment_sources', '_stop_playback'):
                    widget.setEnabled(not busy)
            for kind in (QDateEdit, QComboBox, QDoubleSpinBox, QSpinBox):
                for widget in page.findChildren(kind):
                    widget.setEnabled(not busy)
        loading, self._loading = self._loading, True
        for row in range(self.tasks.rowCount()):
            for column in (0, 1, 4):
                cell = self.tasks.item(row, column)
                flag = Qt.ItemFlag.ItemIsEditable if column == 4 else Qt.ItemFlag.ItemIsUserCheckable
                cell.setFlags(cell.flags() & ~flag if busy else cell.flags() | flag)
        self._loading = loading

    def _finished(self):
        worker, kind = self.worker, self._job_kind
        self.worker = None
        self._busy_inputs(False)
        self.cancel.setEnabled(False)
        try:
            if kind in ('audio', 'materials','align'):
                self.draft = worker.payload
                self._show_segments(max(0, self._current))
                if kind == 'materials':
                    self._show_tasks()
            if worker.error:
                self.status.setText('失败，已成功的分段/文件保留：' + worker.error)
            elif worker.cancelled:
                self.status.setText(('后续生成已取消；成功音频保留，空闲语音模型仍驻留，可手动卸载' if self.tts_provider.is_running
                                   else '语音已取消，进程退出已确认；成功音频保留，下次需加载模型。')
                                   if kind == 'audio' else '当前任务已取消；已有结果和驻留语音模型保留')
            elif kind == 'refresh':
                self._new_snapshot(worker.value)
                self.status.setText('刷新完成。' + (worker.value.get('reason') or '分页完整；未核对字段保留原文'))
            elif kind in ('script', 'segment'):
                for key in ('writing_input', 'writing_warnings', 'focus_plan'):
                    if key in worker.payload:
                        self.draft[key] = worker.payload[key]
                roundup.apply_segments(self.draft, worker.value, single_id=self._single_id if kind == 'segment' else None)
                if kind == 'script':
                    roundup.sync_names(self.draft)
                    self._show_tasks()
                self._show_segments()
                self.tabs.setCurrentIndex(1)
                self.status.setText(self.script_origin.text() + '；' + '；'.join(self.draft.get('writing_warnings', [])) + '；目标时长需在实际配音后复核')
            elif kind == 'materials':
                self.status.setText('资料准备完成；原稿和人工编辑保留，请查看每项资料状态后再生成新稿。')
            elif kind == 'polish':
                self._review_narration(worker.value, '润色口播')
            elif kind == 'audio':
                errors = worker.value or []
                self.status.setText('配音完成' if not errors else '部分段落失败：' + str(errors))
                if not errors and any(s.get('alignment_error') for s in self.draft['segments']):
                    self.status.setText('配音完成；部分段落待校准，可在“同步校准”中检查')
                if getattr(self, '_play_after', None):
                    segment = next(s for s in self.draft['segments'] if s['id'] == self._play_after)
                    audio = current_audio(self.draft, segment)
                    if audio:
                        self._play(audio['path'])
            elif kind == 'unload':
                self.status.setText('语音模型已卸载；下次生成时按需加载')
            elif kind == 'align':
                self.status.setText('对齐完成，请试听检查切换点' if not worker.value else '部分段落待校准；配音保留，请打开同步校准查看原因')
            elif kind == 'preview_audio':
                self.preview_ready.emit(worker.value)
            elif kind == 'export':
                self.last_export_path=str(worker.value)
                self.status.setText('已导出：' + str(worker.value))
        except Exception as error:
            self.status.setText(str(error))
        finally:
            self.job_changed.emit()
            worker.deleteLater()
            if self._close_after and not self.tts_provider.is_running:
                super().reject()
            elif self._close_after and not (kind == 'unload' and worker.error):
                self._unload_voice()
            elif self._close_after:
                self._close_after = False
                self.status.setText('模型卸载尚未确认，窗口保留；请重试卸载')

    def _cancel(self):
        if self.worker is not None:
            self.worker.token.cancel()
            self.status.setText('正在取消，等待当前阻塞调用返回')

    def reject(self):
        self._stop_playback()
        if self.worker is not None:
            self._close_after = True
            self._cancel()
        elif self.tts_provider.is_running:
            self._close_after = True
            self._unload_voice()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None or self.tts_provider.is_running:
            self.reject()
            event.ignore()
        else:
            self._stop_playback()
            event.accept()
