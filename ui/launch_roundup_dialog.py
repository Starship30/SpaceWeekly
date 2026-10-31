from ui.component import ProductionComponent
"""Weekly launch roundup: explicit data, script editing, optional speech, handoff."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QDate, Qt, QThread, Signal, QUrl
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QDateEdit, QTableWidget, QTableWidgetItem, QListWidget, QPlainTextEdit,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QFileDialog, QMessageBox, QSplitter, QAbstractItemView)

from resources import resource_path
from services.cancellation import CancellationToken, TaskCancelledError
from services.launch_data import load_existing, refresh_collection, save_collection, write_json
from services import launch_roundup as roundup
from services.roundup_audio import current_audio, generate_audio, speech_text
from services.roundup_polish import polish_segments, apply_polish
from services.roundup_export import export_package
from ai.qwen_tts import QwenTTSProvider, capabilities


class RoundupWorker(QThread):
    def __init__(self, action, payload, parent=None):
        super().__init__(parent)
        self.action, self.payload = action, payload
        self.token = CancellationToken()
        self.value, self.error, self.cancelled = None, '', False

    def run(self):
        try:
            self.value = self.action(self.payload, self.token)
        except TaskCancelledError:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)


class LaunchRoundupDialog(ProductionComponent):
    tts_state_changed = Signal(str)
    job_changed = Signal()
    draft_changed = Signal()

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
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        data_page = QWidget()
        self.data_page = data_page
        layout = QVBoxLayout(data_page)
        dates = QHBoxLayout()
        self.start, self.end = QDateEdit(), QDateEdit()
        self.date_label = QLabel(roundup.timezone_label(self.draft) + '起日')
        for label, edit in [(self.date_label, self.start), (QLabel('止日（包含当天）'), self.end)]:
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            dates.addWidget(label)
            dates.addWidget(edit)
        self._button(dates, '最近七个完整自然日', self._default_dates)
        self._button(dates, '应用范围（本地）', self._apply_range)
        self._button(dates, '刷新 LL2 与资料（联网）', self._refresh)
        layout.addLayout(dates)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        layout.addWidget(self.stats)
        self.tasks = QTableWidget(0, 12)
        self.tasks.setHorizontalHeaderLabels(['纳入', '重点', '任务名（悬停查看ID）', '分类', '分组（可编辑）', '北京时间', '发射地国家', '机构',
                                              '资料准备', '资料来源', '独立来源支持', '人工确认（名称）'])
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
        self.segment_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.segment_list.setToolTip('Ctrl/Shift多选；口播润色只作用于选中的重点/补充段落')
        self.segment_list.currentRowChanged.connect(self._select_segment)
        split.addWidget(self.segment_list)
        editor = QWidget()
        form = QFormLayout(editor)
        self.spoken, self.screen, self.narration_edit = QPlainTextEdit(), QPlainTextEdit(), QPlainTextEdit()
        self.text_tabs = QTabWidget()
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
        self._button(export_layout, '导出配音与画面素材包', lambda: self._export(True))
        export_layout.addWidget(QLabel('完整当前配音齐备后才生成整段旁白和 SRT。字幕精度为段落级，不是逐字对齐。'))
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
        self._loading = True
        self.start.setDate(QDate.fromString(self.draft['start'], 'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(self.draft['end'], 'yyyy-MM-dd'))
        self.target.setCurrentText(str(self.draft['target_seconds']))
        voice = self.draft['tts']
        self.python.setText(voice['python'])
        self.model.setText(voice['model'])
        self.speaker.setCurrentText(voice['speaker'])
        self.style.setText(voice.get('style', ''))
        device=voice.get('device','cpu')
        if self.device.findText(device)<0:self.device.addItem(device)
        self.device.setCurrentText(device)
        self.timeout.setValue(int(voice.get('timeout', 600)))
        self.pause.setValue(float(self.draft['pause_seconds']))
        self.pronunciations.setPlainText(json.dumps(self.draft['pronunciations'], ensure_ascii=False, indent=2))
        self._loading = False
        self._capabilities()
        self._voice_dirty = False
        self._show_tasks()
        self._show_segments()
        self.draft_changed.emit()

    def _show_tasks(self):
        current=self.tasks.currentRow()
        selected_id=self.tasks.item(current,0).toolTip().split('\n')[0] if current>=0 and self.tasks.item(current,0) else None
        scroll=self.tasks.verticalScrollBar().value()
        self.date_label.setText(roundup.timezone_label(self.draft) + '起日')
        self.tasks.horizontalHeaderItem(5).setText(roundup.timezone_label(self.draft))
        self._loading = True
        self.tasks.setRowCount(0)
        items = {i['ll2_id']: i for i in self.draft['snapshot']['items']}
        from services.launch_research import confirmed_values
        from utils.time import format_datetime
        for row, selection in enumerate(self.draft['selection']):
            self.tasks.insertRow(row)
            item = items[selection['id']]
            name = confirmed_values(item.get('research', {}), item).get('chinese_name') or item['english_name']
            classification = {'actual': '实际发射', 'planned': '计划', 'delayed': '延期', 'cancelled': '取消', 'pending': '待确认'}
            state = roundup.material_status(item)
            values = ['', '', name, classification[roundup.category(item, roundup.parse_datetime(self.draft['as_of']))], selection['group'], format_datetime(item.get('net', ''), self.draft.get('timezone', 'Asia/Shanghai')),
                      item.get('site_country', ''), item.get('provider', ''),
                      state['preparation_label'], state['source_label'], state['independent_label'], state['human_label']]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(selection['id'] + '\n' + str(value))
                if column >= 8:
                    cell.setToolTip(json.dumps(state, ensure_ascii=False, indent=2) + '\n独立支持指已有外部原句与事件匹配；机器复核不等于官方或人工确认。')
                if column in (0, 1):
                    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                    cell.setCheckState(Qt.CheckState.Checked if selection['included' if column == 0 else 'featured'] else Qt.CheckState.Unchecked)
                elif column != 4:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tasks.setItem(row, column, cell)
        self.tasks.resizeColumnsToContents()
        self.tasks.setColumnWidth(2, min(320, self.tasks.columnWidth(2)))
        self._loading = False
        if selected_id:
            row=next((n for n,s in enumerate(self.draft['selection']) if s['id']==selected_id),-1)
            if row>=0:self.tasks.setCurrentCell(row,2)
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

    def _show_stats(self):
        stats = roundup.statistics(self.draft)
        self.stats.setText(roundup.overview(self.draft) + '\n'
            + ('完整分页覆盖所选范围。' if stats['complete'] else '资料不完整或只选择了部分任务：' + self.draft['snapshot'].get('reason', ''))
            + '\n实际发射地点国家：' + json.dumps(stats['site_countries'], ensure_ascii=False)
            + '；发射机构：' + json.dumps(stats['agencies'], ensure_ascii=False))

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
        self.audio_state.setText(self.audio_state.text() + (f" · 当前配音 {audio['duration_seconds']:.2f} 秒" if audio else ' · 无当前音频（未生成或已过期）'))
        self._refresh_reading()

    def _refresh_reading(self):
        if not 0 <= self._current < len(self.draft['segments']):
            return
        segment = self.draft['segments'][self._current]
        value = roundup.current_narration(segment)
        if segment.get('narration') and not value:
            self.audio_state.setText(self.audio_state.text() + ' · 朗读润色已过期，当前使用事实原稿')
        elif value:
            self.audio_state.setText(self.audio_state.text() + (' · AI口播润色（非人工确认）' if value.get('origin') == 'ai_polish' else ' · 人工朗读编辑（待复核）'))
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
            self.segment_list.addItem(segment['id'] + ' · ' + segment['kind'] + ' · ' + origin)
        origins = set(s.get('origin', 'unknown') for s in self.draft['segments'])
        reasons = list(dict.fromkeys(s.get('generation_reason', '') for s in self.draft['segments'] if s.get('generation_reason')))
        label = 'AI综述（资料约束生成）' if origins == {'ai'} else '本地模板稿（非AI综述）' if origins == {'local_template'} else '混合稿/人工稿，请查看各段来源'
        detail = (label + ('；' + '；'.join(reasons) if reasons else '')) if origins else '尚无稿件'
        if any(s.get('edited') or (s.get('narration') or {}).get('origin') == 'manual_edit' for s in self.draft['segments']):
            detail += '（含人工编辑，需复查）'
        count = sum(bool(roundup.current_narration(s)) for s in self.draft['segments'])
        if count:
            detail += f'；{count}段有独立朗读文本'
        self.script_origin.setText(detail[:360])
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
        self.draft['tts'] = dict(python=self.python.text().strip(), model=self.model.text().strip(),
            speaker=self.speaker.currentText().strip(), style=self.style.text() if not self.style.isHidden() else '',
            device=self.device.currentText(), timeout=self.timeout.value())
        rules = json.loads(self.pronunciations.toPlainText())
        speech_text('', rules)
        self.draft['pronunciations'] = rules
        self._voice_dirty = False
        self._store_segment()
        if validate:
            roundup.validate_draft(self.draft)

    def _default_dates(self):
        start, end = roundup.default_range(display_timezone=self.settings.display_timezone)
        self.start.setDate(QDate.fromString(start, 'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(end, 'yyyy-MM-dd'))

    def _allow_new_snapshot(self):
        return not self.draft['segments'] or QMessageBox.question(self, '新建快照草稿',
            '应用新日期/数据会新建草稿。当前稿件不会自动迁移，请先保存。继续？') == QMessageBox.StandardButton.Yes

    def _apply_range(self):
        if self._allow_new_snapshot():
            self._new_snapshot(self.collection)

    def _new_snapshot(self, collection):
        voice, rules = deepcopy(self.draft['tts']), deepcopy(self.draft['pronunciations'])
        self.collection = collection
        self.draft = roundup.create_draft(collection, self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd'), self.settings.display_timezone)
        self.draft.update(tts=voice, pronunciations=rules)
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
        previous = self.speaker.currentText() or 'Vivian'
        try:
            info = capabilities(self.model.text().strip())
            self.style.setVisible(info['style'])
            self.style_label.setVisible(info['style'])
            self.speaker.clear()
            self.speaker.addItems(info['speakers'])
            self.speaker.setCurrentText(previous)
            self.voice_info.setText('已读取本地配置（尚未加载权重）。模型大小：' + info['size'])
        except (OSError, ValueError, KeyError) as error:
            self.style.hide()
            self.style_label.hide()
            self.voice_info.setText(str(error))

    def _save_voice(self):
        self._sync(validate=False)
        write_json(resource_path('config', 'roundup_voice.json'), dict(tts=self.draft['tts'], pronunciations=self.draft['pronunciations']))
        self.status.setText('语音配置已独立保存，未修改文本模型配置')

    def _voice_changed(self, *_):
        if not self._loading:
            self._voice_dirty = True
            self.audio_state.setText('语音设置已变更，原音频需要重新核验；试听/导出时按新设置检查')
            self._refresh_reading()

    def _audio_job(self, identifier=None, force=False, play=False):
        self._sync()
        if not self.draft['segments']:
            raise ValueError('请先生成稿件或添加人工段落')
        self._play_after = identifier if play else None
        settings = deepcopy(self.draft['tts'])
        needed = [s for s in self.draft['segments'] if (not identifier or s['id'] == identifier)
                  and (force or not current_audio(self.draft, s))]
        if needed:
            capabilities(settings['model'])
            if not settings['python'] or not Path(settings['python']).is_file():
                raise ValueError('请在语音页配置独立 TTS 环境的 python.exe；参见 tools/ROUNDUP.md。稿件可直接导出。')
        def synthesize(data, token):
            self.tts_provider.configure(settings)
            return generate_audio(data, self.tts_provider, token, segment_id=identifier, force=force)
        self._start_job('audio', synthesize, deepcopy(self.draft))

    def _tts_state(self, message):
        self.voice_info.setText(message)
        if self.worker is not None and self._job_kind in ('audio', 'unload'):
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
        output = self.export_directory() if hasattr(self, 'export_directory') else QFileDialog.getExistingDirectory(self, '导出到目录（自动新建独立成果文件夹）', self.settings.output_dir)
        if output:
            self._start_job('export', lambda data, token: export_package(data, output, token, visuals=visuals), deepcopy(self.draft))

    def _start_job(self, kind, action, payload):
        if self.worker is not None:
            raise ValueError('请先等待当前任务完成或取消')
        self._stop_playback()
        self._job_kind = kind
        self.worker = RoundupWorker(action, payload, self)
        self.worker.finished.connect(self._finished)
        self._busy_inputs(True)
        self.cancel.setEnabled(True)
        self.status.setText('处理中：' + kind + '；可取消。语音加载/编码可能需要较长时间。')
        self.worker.start()
        self.job_changed.emit()

    def _busy_inputs(self, busy):
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
            if kind in ('audio', 'materials'):
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
                    self._show_tasks()
                self._show_segments()
                self.tabs.setCurrentIndex(1)
                self.status.setText(self.script_origin.text() + '；' + '；'.join(self.draft.get('writing_warnings', [])) + '；目标时长需在实际配音后复核')
            elif kind == 'materials':
                self.status.setText('资料准备完成；原稿和人工编辑保留，请查看每项资料状态后再生成新稿。')
            elif kind == 'polish':
                apply_polish(self.draft, worker.value)
                selected_id = worker.value['updates'][0]['id']
                self._show_segments(next(i for i, s in enumerate(self.draft['segments']) if s['id'] == selected_id))
                self.text_tabs.setCurrentIndex(2)
                self.status.setText('已润色所选朗读文字并保留前后版本；事实、屏幕文字和其他段落未改。机器复核不等于人工确认，实际口播时长未测量。')
            elif kind == 'audio':
                errors = worker.value or []
                self.status.setText('配音完成' if not errors else '部分段落失败：' + str(errors))
                if getattr(self, '_play_after', None):
                    segment = next(s for s in self.draft['segments'] if s['id'] == self._play_after)
                    audio = current_audio(self.draft, segment)
                    if audio:
                        self._play(audio['path'])
            elif kind == 'unload':
                self.status.setText('语音模型已卸载；下次生成时按需加载')
            elif kind == 'export':
                self.last_export_path=str(worker.value)
                self.status.setText('已导出：' + str(worker.value) + '（无完整配音时不生成整段WAV/SRT）')
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
