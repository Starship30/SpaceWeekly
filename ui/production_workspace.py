"""Persistent production pages sharing the existing service/controller state."""
from copy import deepcopy
from dataclasses import replace
from html import escape
import json
import re
import time
from math import ceil
from pathlib import Path

from PySide6.QtCore import Qt,QTimer,Signal,QUrl,QSize
from PySide6.QtGui import QDesktopServices,QPixmap,QImage
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,
 QStackedWidget,QTabWidget,QSplitter,QPlainTextEdit,QTextBrowser,QLineEdit,
 QScrollArea,QFormLayout,QFileDialog,QMessageBox,QDialogButtonBox,QMenu,QProgressBar,QCheckBox,
 QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QSlider,QSpinBox)
from i18n import tr,set_language
from resources import resource_path,workspace_path
from services import launch_roundup as roundup
from services.launch_data import write_json,load_existing
from services.production import prepare_news,export_news,validate_session
from services.settings import load_settings,save_settings,apply_settings
from ui.launch_roundup_dialog import LaunchRoundupDialog,RoundupWorker
from ui.video_cards_dialog import VideoCardsDialog
from ui.video_intro_dialog import VideoIntroDialog
from ui.video_download_dialog import VideoDownloadDialog
from ui.settings_dialog import SettingsDialog
from ui.prompt_studio_dialog import PromptStudioDialog
from ui.widgets.dashboard_panel import DashboardPanel
from ui.widgets.feed_panel import FeedPanel
from ui.widgets.log_panel import LogPanel
from ui.news_workspace import NewsWorkspace
from ui.component import AspectPreview,SegmentedTabs,ContentBrowser,configure_table,StatusDelegate
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from config import app_version
from ui.widgets.date_range import NEWS_DATE_RANGES,LAUNCH_DATE_RANGES

SOURCE_DATE_KEYS=('date_mode','start_date','end_date','launch_range','launch_start_date','launch_end_date')


class TaskSummary(QLabel):
    def setText(self,text):
        self.setToolTip(text)
        compact=text.splitlines()[0] if text else ''
        if compact.startswith('已导出：'):compact='导出完成，可在导出页打开成果。'
        elif re.search(r'Traceback|HTTPS?ConnectionPool|HTTPError|NameResolutionError|WinError|No such file',compact):
            compact='采集不完整，请重试联网刷新；详情见任务与日志。' if '采集' in compact or '刷新' in compact else '操作未完成，详情见任务与日志。'
        super().setText(compact if len(compact)<=150 else compact[:147]+'…')


class ProductionWorkspace(QWidget):
    close_ready=Signal()
    settings_applied=Signal()
    workspace_requested=Signal(str)
    update_ready=Signal()
    uninstall_ready=Signal()

    def __init__(self,settings,feeds,theme,parent=None):
        super().__init__(parent)
        self.settings,self.theme=settings,theme
        self.worker=None;self.worker_kind='';self.mode='news'
        self.image_worker=None
        self.aux_worker=None;self.aux_kind='';self._aux_config=None
        self._restoring=True;self._saved_hash='';self._observed_hash=''
        self._pending_leave=None;self._discard=False;self._recovery_blocked=False
        self.results=[];self.result_scopes={};self._cards_base=None
        self._last_page=0
        self.material_edits={};self._detail_id=None;self._detail_loading=False
        self.feed_panel=FeedPanel(feeds);self.dashboard=DashboardPanel(settings)
        self.news=NewsWorkspace(self);self.roundup=LaunchRoundupDialog(settings,self)
        self.news.voice_settings=self.roundup.voice_settings
        self.roundup.workflow_embedded=True
        self.cards=VideoCardsDialog(settings.output_dir)
        self.intro=VideoIntroDialog(Path(settings.output_dir)/'video-cards')
        self.downloads=VideoDownloadDialog(workspace_path() or Path(settings.output_dir).parent,[])
        self.preferences=SettingsDialog(settings);self.preferences._model_timer.stop()
        self.rules=PromptStudioDialog(settings)
        for component in (self.cards,self.intro,self.downloads,self.preferences,self.rules):component.workflow_embedded=True
        self._build();self._connect();self._restore();self._restore_source_dates()
        self._restoring=False
        self._observed_hash=roundup.digest(self.content_state())
        self._saved_preferences=self.preference_signature();self._saved_rules=self.rules_signature();self._pending_preferences=False;self._pending_rules=False
        self._last_statuses={key:c.status.text() for key,c in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads))}
        if not self._saved_hash:self._saved_hash=self._observed_hash
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self._poll)
        self.timer.start();self._mode();self._poll();self.retranslate()

    def button(self,key,action,layout,primary=False):
        button=QPushButton(tr(key));button.setProperty('productionKey',key);button.setAutoDefault(False)
        if primary:button.setObjectName('PrimaryButton')
        button.clicked.connect(lambda _=False:self.guard(action));layout.addWidget(button)
        return button

    def guard(self,action):
        try:action()
        except (OSError,ValueError,KeyError,RuntimeError,TypeError) as error:
            self.task_text.setText(str(error))
            import logging
            logging.getLogger(__name__).warning('%s',error)

    def scroll(self,widget):
        widget.setMinimumSize(0,0)
        area=QScrollArea();area.setWidgetResizable(True);area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(widget);return area

    def _build(self):
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,8);outer.setSpacing(16)
        top=QWidget();top.setObjectName('ProductionHeader');header=QHBoxLayout(top);header.setContentsMargins(24,12,24,12);header.setSpacing(12)
        self.workspace_label=QLabel()
        self.workspace_label.setToolTip(str(workspace_path() or Path(self.settings.output_dir).parent))
        self._detach_widget(self.dashboard,self.dashboard.report_title_line)
        self.edition_button=QPushButton();self.edition_button.setFlat(True)
        self.edition_button.setObjectName('EditionTitle');self.edition_button.setMaximumWidth(220)
        self.edition_button.clicked.connect(self._edit_title);header.addWidget(self.edition_button)
        header.addWidget(self.dashboard.report_title_line);self.dashboard.report_title_line.hide()
        self.dashboard.report_title_line.editingFinished.connect(self._finish_title)
        self.content=QComboBox();self.content.addItem(tr('production.news'),'news');self.content.addItem(tr('production.roundup'),'roundup')
        header.addWidget(self.content)
        self.period=QLabel();header.addWidget(self.period,1)
        self.saved=QLabel();header.addWidget(self.saved)
        self.save_button=self.button('production.save',self.save,header)
        self.button('settings',self.show_settings,header)
        more=QPushButton(tr('production.more'));menu=QMenu(more)
        menu.addAction('术语词典',lambda:self.guard(self.open_terms))
        menu.addAction(tr('production.workspace'),self.choose_workspace)
        menu.addAction('工作区详情',lambda:QMessageBox.information(self,'当前工作区',self.workspace_label.toolTip()))
        menu.addAction(tr('open.output'),self.open_output)
        menu.addAction(tr('production.open_edition'),lambda:self.guard(self.open_edition))
        menu.addAction(tr('production.diagnostics'),lambda:self.inspect(dict(version=app_version(),tasks=[r[:3] for r in self.task_rows()])))
        menu.addAction(tr('launch_test_title'),lambda:self.guard(lambda:self.start_diagnostic('launch')))
        menu.addAction(tr('about'),self.show_about)
        more.setMenu(menu);header.addWidget(more);outer.addWidget(top)
        body=QHBoxLayout();body.setContentsMargins(12,0,24,0);body.setSpacing(16)
        self.navigation=QListWidget();self.navigation.setObjectName('ProductionNav');self.navigation.setFixedWidth(132);self.navigation.setSpacing(4)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key in ('materials','script','media','export'):
            self.navigation.addItem(tr('production.'+key));self.navigation.item(self.navigation.count()-1).setSizeHint(QSize(110,42))
        body.addWidget(self.navigation);content=QVBoxLayout();content.setSpacing(16)
        self.page_title=QLabel(tr('production.materials'));self.page_title.setObjectName('PageTitle');content.addWidget(self.page_title)
        self.pages=QStackedWidget();content.addWidget(self.pages,1);body.addLayout(content,1);outer.addLayout(body,1)
        self.materials=SegmentedTabs();self.materials.addTab(self.news.data_page,tr('production.news_short'))
        self._launch_materials();self.materials.addTab(self.roundup.data_page,tr('production.launches_short'))
        self._wrap_actions(self.roundup.script_page,3,3)
        self._wrap_actions(self.roundup.script_page,0,4)
        self._compact_roundup_editor()
        setup=QSplitter();self.feed_panel.setMaximumWidth(16777215);setup.addWidget(self.feed_panel);setup.addWidget(self.dashboard);setup.setStretchFactor(1,1)
        self.dashboard.setMinimumSize(0,0)
        for area in self.dashboard.findChildren(QScrollArea):area.setMinimumWidth(0)
        self.dashboard.start_button.setText(tr('production.collect_news'))
        self.materials.addTab(setup,tr('production.sources_dates'));self.pages.addWidget(self.materials)
        self.scripts=QStackedWidget();self.scripts.addWidget(self.news.script_page);self.scripts.addWidget(self.roundup.script_page);self.pages.addWidget(self.scripts)
        self.media=SegmentedTabs();self._voice_page();self._cards_page()
        self.intro.setMinimumSize(0,0);self.intro.preview.setMinimumSize(320,180)
        self.media.addTab(self.scroll(self.intro),tr('video.intro'));self.media.addTab(self.scroll(self.downloads),tr('video_download.title'));self.pages.addWidget(self.media)
        self._export_page();self._settings_page()
        self.roundup.hide();self.roundup.tabs.hide()
        self.roundup.inspect_data=self.inspect;self.roundup.open_rules=self.open_rules
        self.roundup.export_directory=lambda:self.preferences.output_edit.text().strip()
        self.roundup.export_intro=self.prepared_intro
        self.cards.edition_scope=self.intro.edition_scope=self.export_scope
        self.preferences.open_rules=self.open_rules;self.preferences.open_workspace=self.choose_workspace
        self.preferences.discover_models=lambda:self.guard(lambda:self.start_diagnostic('models'))
        self.preferences.check_connection=lambda:self.guard(lambda:self.start_diagnostic('connection'))
        for component in (self.roundup,self.cards,self.intro,self.downloads,self.preferences,self.rules):
            component.inline_error=lambda message:self.task_text.setText(str(message))
        for component in (self.cards,self.intro,self.downloads):
            component.setMinimumSize(0,0)
            for button in component.findChildren(QPushButton):
                if button.text() in (tr('video.close'),tr('video_download.close')):button.hide()
        self._build_inspector(outer)
        strip=QHBoxLayout();strip.setContentsMargins(24,0,24,0)
        self.task_text=TaskSummary(tr('ready'));self.task_text.setObjectName('MutedText');self.task_text.setWordWrap(True);strip.addWidget(self.task_text,1)
        self.task_text.setMaximumHeight(65)
        self.task_progress=QProgressBar();self.task_progress.setMaximumWidth(150);self.task_progress.hide();strip.addWidget(self.task_progress)
        self.cancel_tasks=self.button('production.cancel_tasks',self.cancel_all,strip)
        self.cancel_tasks.setObjectName('TextButton')
        self.last_diagnostic_button=self.button('查看诊断',lambda:self.inspect(self._last_diagnostic),strip)
        self.last_diagnostic_button.setObjectName('TextButton');self.last_diagnostic_button.hide()
        self.log_toggle=QPushButton(tr('production.show_log'));self.log_toggle.setCheckable(True);strip.addWidget(self.log_toggle);outer.addLayout(strip)
        self.log_toggle.setObjectName('TextButton')
        self.log=LogPanel();self.log.setMaximumHeight(180);self.log.hide();self.log.apply_theme(self.theme);outer.addWidget(self.log)
        self.log_toggle.toggled.connect(self.log.setVisible);self.navigation.setCurrentRow(0);self.pages.setCurrentIndex(0)

    def _detach_widget(self,root,control):
        for form in root.findChildren(QFormLayout):
            row,_=form.getWidgetPosition(control)
            if row>=0:
                taken=form.takeRow(row)
                if taken.labelItem and taken.labelItem.widget():taken.labelItem.widget().hide()
                break
        control.setParent(None)

    def _wrap_actions(self,page,index,columns):
        item=page.layout().takeAt(index)
        if item is None or item.layout() is None:return
        old=item.layout();rows=QVBoxLayout()
        while old.count():
            row=QHBoxLayout()
            for _ in range(min(columns,old.count())):row.addItem(old.takeAt(0))
            rows.addLayout(row)
        old.setParent(None);page.layout().insertLayout(index,rows)

    def _move_form_control(self,root,control,target,label=None):
        for form in root.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                item=form.itemAt(row,QFormLayout.ItemRole.FieldRole)
                if item is None:continue
                layout=item.layout()
                if item.widget() is control or layout is not None and layout.indexOf(control)>=0:
                    taken=form.takeRow(row)
                    text=label or (taken.labelItem.widget().text() if taken.labelItem and taken.labelItem.widget() else '')
                    if taken.labelItem and taken.labelItem.widget():taken.labelItem.widget().hide()
                    if layout is not None:layout.setParent(None);target.addRow(text,layout)
                    else:target.addRow(text,control)
                    return
        target.addRow(label or '',control)

    def _launch_materials(self):
        r=self.roundup;self.launch_details=ContentBrowser();self.launch_details.setOpenExternalLinks(True)
        detail=QWidget();detail.setObjectName('DetailPanel');layout=QVBoxLayout(detail);layout.setContentsMargins(24,20,24,20);layout.setSpacing(16)
        caption=QLabel('任务详情');caption.setObjectName('MutedText');layout.addWidget(caption);layout.addWidget(self.launch_details)
        actions=QHBoxLayout()
        edit=QPushButton('编辑资料');edit.setCheckable(True);edit.setObjectName('TextButton');actions.addWidget(edit)
        fix=QPushButton('修正名称');fix.setObjectName('TextButton');actions.addWidget(fix)
        evidence=self.button('production.show_evidence',self._show_task_evidence,actions);evidence.setObjectName('TextButton')
        self.launch_detail_actions=(edit,fix,evidence)
        actions.addStretch(1);layout.addLayout(actions)
        self.material_editor=QWidget();editor=QVBoxLayout(self.material_editor)
        self.chinese_name=QLineEdit();self.chinese_name.setPlaceholderText(tr('production.confirmed_name'))
        self.manual_summary=QPlainTextEdit();self.manual_summary.setPlaceholderText(tr('production.manual_summary'));self.manual_summary.setMaximumHeight(75)
        editor.addWidget(self.chinese_name);editor.addWidget(self.manual_summary)
        self.add_to_dictionary=QCheckBox('同时加入术语词典');editor.addWidget(self.add_to_dictionary)
        row=QHBoxLayout();self.confirm_name=self.button('production.confirm_name',self.confirm_launch,row)
        editor.addLayout(row);layout.addWidget(self.material_editor);self.material_editor.hide();layout.addStretch(1)
        edit.toggled.connect(self.material_editor.setVisible)
        fix.clicked.connect(lambda:(edit.setChecked(True),self.chinese_name.setFocus()))
        split=QSplitter();r.data_page.layout().removeWidget(r.tasks);split.addWidget(r.tasks);split.addWidget(self.scroll(detail));split.setStretchFactor(0,3);split.setStretchFactor(1,2)
        split.setSizes([680,420]);split.setChildrenCollapsible(False)
        r.data_page.layout().insertWidget(2,split,1)
        for column in (1,3,4,6,7,8,9,10,11):r.tasks.setColumnHidden(column,True)
        header=r.tasks.horizontalHeader()
        for column in (0,2,12,5,13):header.moveSection(header.visualIndex(column),list((0,2,12,5,13)).index(column))
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(36)
        r.task_column_sizes=self._task_column_sizes
        self._task_column_sizes()
        r.tasks.horizontalHeaderItem(2).setText('任务名称')
        r.tasks.currentCellChanged.connect(lambda *_:self._launch_detail())
        # Keep secondary operations reachable without a permanent bank of buttons.
        while r.data_page.layout().count()>3:
            item=r.data_page.layout().takeAt(3)
            if item.widget():item.widget().hide()
            if item.layout():
                while item.layout().count():
                    child=item.layout().takeAt(0)
                    if child.widget():child.widget().hide()
        more=QPushButton('更多');menu=QMenu(more)
        for label,action in [('补充缺失资料（联网）',r._supplement_materials),('重新核对本任务（联网）',r._recheck_materials),
          ('同步名称与资料',self.refresh_names),('统计与资料缺口',lambda:self.inspect(dict(统计=roundup.statistics(r.draft),采集=r.draft['snapshot'].get('reason',''))))]:
            menu.addAction(label,lambda _=False,fn=action:self.guard(fn))
        more.setMenu(menu);r.data_page.layout().itemAt(0).layout().addWidget(more)
        self.chinese_name.textChanged.connect(self._stage_name);self.manual_summary.textChanged.connect(self._stage_name)

    def _show_task_evidence(self):
        if not self._detail_id:return
        item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==self._detail_id)
        names=roundup.display_names(item)
        from translator.dictionary import load_dictionary
        terms={k:v for k,v in load_dictionary().items() if k in [item.get(f) for f in ('english_name','rocket','site_name','pad_name')]}
        self.inspect(dict(原始名称=item['english_name'],名称对应=names,词典条目=terms,原始资料=item))

    def _launch_detail(self):
        row=self.roundup.tasks.currentRow()
        if row<0 or row>=len(self.roundup.draft['selection']):
            self._detail_id=None;self.launch_details.setPlainText('请选择列表中的任务查看详情。列表勾选用于纳入本期。')
            for button in self.launch_detail_actions:button.setEnabled(False)
            self.launch_detail_actions[0].setChecked(False);self.confirm_name.setEnabled(False);return
        for button in self.launch_detail_actions:button.setEnabled(True)
        identifier=self.roundup.draft['selection'][row]['id'];item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
        state=roundup.material_status(item)
        names=roundup.display_names(item)
        from utils.time import format_datetime
        self._detail_id=identifier
        values=roundup.confirmed_values(item.get('research',{}),item)
        summary=values.get('summary') or next((f['quote'] for f in roundup.background_facts(item) if f['evidence_level']=='primary_announcement' and any('\u4e00'<=c<='\u9fff' for c in f['quote'])),'')
        from services.roundup_style import payload_display
        payload=payload_display(item) if not summary and item.get('payload') and not state['evidence_conflict'] else ''
        if payload==item.get('payload') and not re.search(r'[\u3400-\u9fff]',payload):payload=''
        orbit=roundup.event_context(item,self.roundup.draft)['orbit']
        lines=['<h2>'+escape(names['name'])+'</h2><p>',escape(format_datetime(item.get('net',''),'Asia/Shanghai')[:16])+' · '+escape(names['rocket'])+'</p><hr>',
         '<p>发射地点</p><p>'+escape(names['site_name'] or names['site'])+'<br>'+escape(names['pad_name'])+'</p><hr>',
         '<p>目标轨道</p><p>'+escape(orbit or '待补充')+'</p><hr>',
         '<p>'+('载荷资料（LL2）' if payload else '任务简介')+'</p><p>'+escape((summary or payload)[:240] or '暂无已核实的中文简介。')+'</p><hr>',
         '<p>名称来源<br>'+escape(names['name_source'])+'</p><p>背景资料<br>'+escape(state['label'])+'</p>']
        if state['evidence_conflict']:lines.append('<p>来源存在冲突，请查看出处核对。</p>')
        self.launch_details.setHtml(''.join(lines))
        values=self.material_edits.get(identifier,values);self._detail_loading=True
        self.chinese_name.setText(values.get('chinese_name') or names['name']);self.manual_summary.setPlainText(values.get('summary',''))
        self._detail_loading=False
        self.confirm_name.setEnabled(self.roundup.worker is None)

    def _task_column_sizes(self):
        table=self.roundup.tasks
        small=self.width()<1100
        for column,width in ((0,44),(12,116 if small else 148),(5,110 if small else 154),(13,92)):table.setColumnWidth(column,width)
        blocked=table.blockSignals(True)
        for row in range(table.rowCount()):
            cell=table.item(row,5)
            if cell:cell.setText(cell.toolTip()[5:16] if small else cell.toolTip()[:16])
        table.blockSignals(blocked)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'roundup'):self._task_column_sizes()

    def _stage_name(self,*_):
        if self._detail_id and not self._detail_loading:
            self.material_edits[self._detail_id]=dict(chinese_name=self.chinese_name.text(),summary=self.manual_summary.toPlainText())

    def confirm_launch(self):
        if self.roundup.worker:raise ValueError(tr('production.conflict'))
        row=self.roundup.tasks.currentRow()
        if row<0:raise ValueError(tr('production.select_content'))
        if not self.chinese_name.text().strip():raise ValueError(tr('production.name_required'))
        from services.launch_research import confirm_launch,load_launch_research,fingerprint,_store
        identifier=self.roundup.draft['selection'][row]['id']
        if identifier not in load_launch_research():
            item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
            record=deepcopy(item.get('research',{}))
            conflict=record.get('evidence_status')=='conflict' or (record.get('previous_evidence') or {}).get('evidence_status')=='conflict'
            record.update(version=1,input={k:deepcopy(v) for k,v in item.items() if k!='research'},fingerprint=fingerprint(item),
                evidence_status='conflict' if conflict else 'pending',matches=[],reason='从打开的草稿人工确认；自动背景仍须核对')
            _store(identifier,record)
        confirm_launch(identifier,self.chinese_name.text(),self.manual_summary.toPlainText())
        if self.add_to_dictionary.isChecked():
            from translator.dictionary import load_dictionary,save_dictionary
            item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
            entries=load_dictionary();entries[item['english_name']]=dict(translation=self.chinese_name.text().strip(),type='mission',human_status='confirmed')
            save_dictionary(entries)
        self.material_edits.pop(self.roundup.draft['selection'][row]['id'],None)
        self.refresh_names();self.material_editor.hide()

    def refresh_names(self,update_manual=False):
        r=self.roundup
        if r.worker:raise ValueError(tr('production.conflict'))
        r._store_segment();roundup.sync_materials(r.draft)
        pending=roundup.sync_names(r.draft,update_manual=update_manual)
        selected=r.segment_list.currentRow();r._show_tasks();r._show_segments(selected)
        self._frame_key=self._timeline_key=None
        self._launch_detail();self._voice_refresh()
        self.name_update_button.setVisible(bool(pending))
        self.task_text.setText('名称已刷新；已有配音或人工编辑稿保留，请点击“更新稿件名称”采用新名称。' if pending else '名称已刷新；朗读有变化的旧音频需重新生成。')

    def update_script_names(self):
        if QMessageBox.question(self,'更新稿件名称','仅替换稿件中的名称，保留其他人工文字。旧音频可能需重新生成。继续？')==QMessageBox.StandardButton.Yes:
            self.checkpoint();self.refresh_names(update_manual=True)

    def _voice_page(self):
        r=self.roundup;page=QWidget();layout=QVBoxLayout(page)
        self.voice_segments=QComboBox();self.voice_segments.currentIndexChanged.connect(self._voice_selected)
        row=QHBoxLayout();row.addWidget(self.voice_segments,1)
        self.listen_button=self.button('production.listen',self.listen_roundup,row,True)
        self.speech_button=self.button('production.synthesize',self.synthesize_media,row)
        r._button(row,'停止播放',self._stop_preview)
        layout.addLayout(row)
        voice=QHBoxLayout();voice.addWidget(QLabel('音色'))
        self._detach_widget(r.voice_page,r.speaker);voice.addWidget(r.speaker)
        self.voice_status=QLabel();voice.addWidget(self.voice_status,1)
        self.voice_image_use=QCheckBox('使用本条配图');self.voice_image_use.setToolTip('取消后预览和导出不使用配图；可在资料页更换或导入自行找到的图片。')
        self.voice_image_use.toggled.connect(lambda enabled:self.news.set_image_use(self.news.current_article(),enabled))
        voice.addWidget(self.voice_image_use)
        configure=QPushButton('配置语音');configure.clicked.connect(self.show_voice_settings);voice.addWidget(configure);layout.addLayout(voice)
        split=QSplitter();text=QWidget();left=QVBoxLayout(text);left.addWidget(QLabel('口播文本'))
        self.voice_text=QPlainTextEdit();left.addWidget(self.voice_text,1)
        self.voice_text.setToolTip('这里编辑口播；实际 TTS 输入在稿件页“最终朗读”下方预览。')
        self.voice_text.textChanged.connect(self._edit_voice_text)
        self.play_state=QLabel();self.play_state.setWordWrap(True);left.addWidget(self.play_state)
        split.addWidget(text);view=QWidget();right=QVBoxLayout(view)
        self.frame=AspectPreview(tr('production.preview_hint'));self.frame.setAlignment(Qt.AlignmentFlag.AlignCenter);self.frame.setMinimumSize(240,135)
        right.addWidget(self.frame,1)
        self.preview_slider=QSlider(Qt.Orientation.Horizontal);self.preview_slider.setObjectName('VideoPreviewSlider')
        self.preview_slider.setAccessibleName('整期音画预览位置');self.preview_slider.setEnabled(False)
        self.preview_slider.valueChanged.connect(lambda *_:self.guard(self._seek_preview))
        self.preview_slider.sliderPressed.connect(self._begin_preview_seek)
        self.preview_slider.sliderReleased.connect(lambda:self.guard(self._end_preview_seek))
        right.addWidget(self.preview_slider);self.preview_clock=QLabel();self.preview_clock.setObjectName('MutedText');right.addWidget(self.preview_clock)
        actions=QHBoxLayout();self.preview_button=self.button('production.preview',self.preview_roundup,actions)
        self.preview_play=QPushButton('播放画面');self.preview_play.clicked.connect(lambda:self.guard(self._toggle_preview));actions.addWidget(self.preview_play)
        self.sync_button=QPushButton('同步与画面');sync_menu=QMenu(self.sync_button)
        sync_menu.addAction('同步校准',lambda:self.guard(self.show_sync))
        sync_menu.addAction('镜头编排（高级）',lambda:self.guard(self.show_shot_settings))
        sync_menu.addAction('重新生成本条配音',lambda:self.guard(lambda:self.news_audio(force=True) if self.mode=='news' else r._audio_one()))
        self.sync_button.setMenu(sync_menu);actions.addWidget(self.sync_button)
        self.preview_tasks=QComboBox();self.preview_tasks.setMinimumWidth(100);self.preview_tasks.setMaximumWidth(220)
        self.preview_tasks.activated.connect(lambda index:self.guard(lambda:self.jump_task(self.preview_tasks.itemData(index))))
        actions.addWidget(self.preview_tasks)
        actions.addStretch()
        right.addLayout(actions)
        self.preview_timer=QTimer(self);self.preview_timer.setInterval(33)
        self.preview_timer.timeout.connect(lambda:self.guard(self._advance_preview))
        split.addWidget(view);split.setStretchFactor(0,2);split.setStretchFactor(1,3);split.setSizes([320,600]);layout.addWidget(split,1)
        self.media.addTab(page,tr('production.roundup_voice'))
        r.preview_ready.connect(self._audio_preview_ready)

    def _edit_voice_text(self):
        r=self.roundup
        if self.mode=='news':
            if getattr(self,'_voice_loading',False) or self.worker or r.worker:return
            draft=self.news.voice_draft()
            if draft:
                self._stop_preview();self.news.oral.setPlainText(self.voice_text.toPlainText())
            return
        if getattr(self,'_voice_loading',False) or r.worker or r.segment_list.currentRow()<0:return
        self._stop_preview()
        # Edit the existing narration field so its provenance and audio invalidation stay intact.
        r.narration_edit.setPlainText(self.voice_text.toPlainText())

    def show_voice_settings(self):
        self.show_settings();self.settings_tabs.setCurrentIndex(2)

    def _compact_roundup_editor(self):
        r=self.roundup
        # Replace the inherited action banks with two short context toolbars.
        for button in r.script_page.findChildren(QPushButton):button.hide()
        r.script_page.layout().removeWidget(r.target);r.target.setParent(None)
        for index in (3,0):
            item=r.script_page.layout().takeAt(index)
            if item and item.layout():
                def clear_layout(layout):
                    while layout.count():
                        child=layout.takeAt(0)
                        if child.widget():child.widget().hide()
                        elif child.layout():clear_layout(child.layout())
                clear_layout(item.layout())
        top=QHBoxLayout();top.addWidget(QLabel('目标时长'));top.addWidget(r.target);r.target.show()
        generate=r._button(top,'生成综述',r._generate_all);generate.setObjectName('PrimaryButton')
        more=QPushButton('稿件操作');menu=QMenu(more)
        for label,fn in [('只重写选中段',r._generate_one),('打开草稿',r._open),('保存草稿',r._save),('写作规则',r._writing_rules)]:
            menu.addAction(label,lambda _=False,action=fn:r._guard(action))
        more.setMenu(menu);top.addWidget(more);top.addStretch(1);r.script_page.layout().insertLayout(0,top)
        bottom=QHBoxLayout()
        for label,fn in [('上移',lambda:r._move_segment(-1)),('下移',lambda:r._move_segment(1)),('添加段落',r._add_segment),('口播整理',r._organize_selected)]:
            r._button(bottom,label,fn)
        options=QPushButton('段落操作');menu=QMenu(options)
        menu.addAction('AI 润色口播',lambda:r._guard(r._polish_selected))
        menu.addAction('删除选中段',lambda:r._guard(r._remove_segment));menu.addAction('查看段落出处',r._segment_sources)
        options.setMenu(menu);bottom.addWidget(options);r.script_page.layout().addLayout(bottom)
        bottom.addStretch(1)
        for label in r.script_page.findChildren(QLabel):label.setWordWrap(True)
        r.reading_input_label.setText('最终 TTS 输入（日期、编号和发音规则处理后）')
        r.reading_preview.show();r.reading_input_label.show()
        details=QWidget();form=QFormLayout(details)
        self._move_form_control(r.script_page,r.names,form,tr('production.formal_names'))
        self._move_form_control(r.script_page,r.ids,form,tr('production.task_ids'))
        self._move_form_control(r.script_page,r.preview_seconds,r.voice_form,tr('production.preview_duration'))
        split=r.segment_list.parentWidget()
        form=r.text_tabs.parentWidget().layout();taken=form.takeRow(r.text_tabs)
        if taken.labelItem:taken.labelItem.widget().hide()
        form.addRow(r.text_tabs)
        r.script_page.layout().setStretch(r.script_page.layout().indexOf(split),1)
        r.script_origin.setMaximumHeight(45)
        r.script_page.layout().addWidget(details);details.hide()
        toggle=QPushButton(tr('production.paragraph_details'));toggle.setCheckable(True);toggle.toggled.connect(details.setVisible)
        toggle.setObjectName('TextButton');toggle.setMaximumWidth(200)
        r.script_page.layout().addWidget(toggle)
        actions=top
        plan=QPushButton('任务编排');plan.setObjectName('TextButton');plan.clicked.connect(self.plan_tasks);actions.addWidget(plan)
        self.name_update_button=QPushButton('更新稿件名称');self.name_update_button.clicked.connect(self.update_script_names)
        actions.addWidget(self.name_update_button);self.name_update_button.hide()

    def plan_tasks(self):
        r=self.roundup
        if r.worker:return
        dialog=QDialog(self);dialog.setWindowTitle('重点、分组与顺序');dialog.resize(780,480)
        layout=QVBoxLayout(dialog);table=QTableWidget(0,4);configure_table(table);table.setHorizontalHeaderLabels(['纳入','重点','任务名称','分组'])
        table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);layout.addWidget(table)
        def render():
            table.setRowCount(len(r.draft['selection']))
            items={i['ll2_id']:i for i in r.draft['snapshot']['items']}
            for row,s in enumerate(r.draft['selection']):
                for col,value in enumerate(['','',roundup.display_names(items[s['id']])['name'],s['group']]):
                    cell=QTableWidgetItem(value)
                    if col<2:cell.setCheckState(Qt.CheckState.Checked if s['included' if col==0 else 'featured'] else Qt.CheckState.Unchecked)
                    if col==2:cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row,col,cell)
        def store():
            for row,s in enumerate(r.draft['selection']):
                s.update(included=table.item(row,0).checkState()==Qt.CheckState.Checked,
                         featured=table.item(row,1).checkState()==Qt.CheckState.Checked,group=table.item(row,3).text())
            r.draft['focus_mode']='manual'
        def move(direction):
            row=table.currentRow()
            if 0<=row+direction<table.rowCount() and row>=0:
                store();rows=r.draft['selection'];rows[row],rows[row+direction]=rows[row+direction],rows[row]
                render();table.setCurrentCell(row+direction,2)
        render();actions=QHBoxLayout()
        for label,fn in [('上移',lambda:move(-1)),('下移',lambda:move(1)),('按资料推荐重点',lambda:(store(),roundup.recommend(r.draft),render())),
                         ('关联本地素材',lambda:(r.tasks.setCurrentCell(table.currentRow(),2),r._associate()))]:
            button=QPushButton(label);button.clicked.connect(fn);actions.addWidget(button)
        layout.addLayout(actions);done=QPushButton('完成');done.clicked.connect(dialog.accept);layout.addWidget(done)
        dialog.exec();store();r._show_tasks()

    def _cards_page(self):
        cards=self.cards;cards.setMinimumSize(0,0);cards.preview.setMinimumSize(280,158)
        cards.layout().setContentsMargins(8,8,8,8);cards.log.hide()
        self.cards_mode_note=QLabel(tr('production.cards_backup_note'));self.cards_mode_note.setWordWrap(True)
        cards.inputs.layout().addWidget(self.cards_mode_note)
        cards.more_menu.addAction('使用当前期次',lambda:self.guard(self.cards_from_script))
        cards.more_menu.addAction('查看素材处理日志',lambda:self.inspect(dict(日志=cards.log.toPlainText())))
        tabs=SegmentedTabs();tabs.addTab(cards.editor_box,tr('production.edit'));tabs.addTab(cards.preview_box,tr('video.preview'))
        cards.editor_splitter.addWidget(tabs);cards.editor_splitter.setSizes([230,650])
        self.media.addTab(self.scroll(cards),'画面素材')

    def _export_page(self):
        page=QWidget();layout=QVBoxLayout(page)
        self.export_hint=QLabel();self.export_hint.setWordWrap(True);layout.addWidget(self.export_hint)
        path_row=QHBoxLayout();path_row.addWidget(QLabel(tr('video.output')))
        self._detach_widget(self.preferences,self.preferences.output_edit);path_row.addWidget(self.preferences.output_edit,1)
        self.button('production.choose_output',self.choose_output,path_row);layout.addLayout(path_row)
        news_actions=QHBoxLayout()
        self.complete_export_button=self.button('导出带配音的 MP4',self.export_complete,news_actions,True)
        news_actions.addStretch(1);layout.addLayout(news_actions)
        self.news_formats=QWidget();formats=QVBoxLayout(self.news_formats);formats.setContentsMargins(0,0,0,0);layout.addWidget(self.news_formats)
        advanced=QPushButton('文稿与资料格式');advanced.setCheckable(True);formats.addWidget(advanced,0,Qt.AlignmentFlag.AlignLeft)
        documents=QWidget();form=QFormLayout(documents)
        self._move_form_control(self.preferences,self.preferences.word_check,form,'Word 文档')
        self._move_form_control(self.preferences,self.preferences.markdown_check,form,'Markdown 文稿')
        self._move_form_control(self.preferences,self.preferences.premiere_check,form,'Premiere 素材快照（JSON）')
        self._move_form_control(self.preferences,self.preferences.sqlite_check,form,'新闻数据库（SQLite）')
        doc_actions=QHBoxLayout();self.export_news_button=self.button('导出所选文稿与资料',self.start_news_export,doc_actions)
        doc_actions.addStretch(1);form.addRow(doc_actions)
        documents.hide();formats.addWidget(documents);advanced.toggled.connect(documents.setVisible)
        self.append_news=QCheckBox('发射综述后接本期已勾选新闻');layout.addWidget(self.append_news)
        export_layout=self.roundup.export_controls.layout()
        script=export_layout.takeAt(0).widget();visuals=export_layout.takeAt(0).widget()
        primary=QHBoxLayout();primary.addWidget(script);primary.addStretch(1)
        visuals.hide();visuals.deleteLater();script.setObjectName('TextButton');export_layout.insertLayout(0,primary)
        layout.addWidget(self.roundup.export_controls)
        actions=QHBoxLayout();self.cards_export_button=self.button('production.render_cards',self.render_cards,actions)
        self.button('production.open_output',self.open_output,actions).setObjectName('TextButton');actions.addStretch(1);layout.addLayout(actions)
        self.include_intro=QCheckBox('随视频素材导出已制作的片头（独立 MP4）')
        layout.addWidget(self.include_intro)
        results_bar=QHBoxLayout();self.result_title=QLabel('本期成果');results_bar.addWidget(self.result_title,1)
        self.history_toggle=QPushButton('查看历史成果');self.history_toggle.setCheckable(True)
        self.history_toggle.toggled.connect(self._refresh_files);results_bar.addWidget(self.history_toggle);layout.addLayout(results_bar)
        self.output_files=QListWidget();self.output_files.itemDoubleClicked.connect(lambda item:QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))))
        self.output_hint=QLabel();layout.addWidget(self.output_hint)
        layout.addWidget(self.output_files,1);self.pages.addWidget(page)

    def _settings_page(self):
        page=QWidget();layout=QVBoxLayout(page);self.settings_tabs=SegmentedTabs();layout.addWidget(self.settings_tabs,1)
        self.preferences.layout().setContentsMargins(0,0,0,0);self.preferences.nav.setFixedWidth(140)
        self.preferences.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for i in range(self.preferences.nav.count()):self.preferences.nav.item(i).setSizeHint(QSize(120,40))
        header=self.preferences.layout().itemAt(0).layout()
        for i in range(header.count()):
            if header.itemAt(i).widget():header.itemAt(i).widget().hide()
        self.settings_tabs.addTab(self.preferences,tr('settings'));self.settings_tabs.addTab(self.scroll(self.rules),tr('prompt.studio'))
        self.voice_settings=self.roundup.voice_page;voice_form=self.roundup.voice_form
        self._move_form_control(self.preferences,self.preferences.display_timezone_combo,voice_form,tr('display.timezone'))
        self._move_form_control(self.preferences,self.preferences.aerospace_translation_check,voice_form,tr('production.translate_launches'))
        self._move_form_control(self.preferences,self.preferences.aerospace_translation_mode_combo,voice_form,tr('production.translation_mode'))
        r=self.roundup
        r.align_python=QLineEdit();r.align_model=QLineEdit();r.align_device=QLineEdit('cpu')
        r.align_language=QComboBox();r.align_language.addItems(['Chinese','English','Cantonese','Japanese','Korean','French','German','Italian','Portuguese','Russian','Spanish'])
        r.align_timeout=QSpinBox();r.align_timeout.setRange(10,3600);r.align_timeout.setValue(600)
        for label,widget in [('强制对齐环境',r.align_python),('对齐模型目录',r.align_model),('对齐设备',r.align_device),('对齐语言',r.align_language),('对齐超时（秒）',r.align_timeout)]:
            voice_form.addRow(label,widget)
            signal=widget.textChanged if isinstance(widget,QLineEdit) else widget.currentTextChanged if isinstance(widget,QComboBox) else widget.valueChanged
            signal.connect(self._alignment_settings_changed)
        align=QPushButton('对齐整稿');align.clicked.connect(lambda:self.guard(self.align_roundup));voice_form.addRow('',align)
        self.roundup._show_draft()
        self._pronunciation_editor(voice_form)
        from ui.voice_setup_panel import VoiceSetupPanel
        self.voice_setup=VoiceSetupPanel(self,self.voice_settings)
        self.settings_tabs.addTab(self.scroll(self.voice_setup),tr('production.voice_settings'))
        from ui.cache_maintenance_panel import CacheMaintenancePanel
        self.maintenance=CacheMaintenancePanel(self);self.settings_tabs.addTab(self.maintenance,'素材与缓存')
        output_options=QWidget();output_form=QFormLayout(output_options);self.video_workflow=QComboBox()
        self.video_workflow.addItem('完整视频 · 带配音 MP4','complete')
        self.video_workflow.addItem('画面素材 · 无声透明 MOV','materials')
        self.video_workflow.setCurrentIndex(max(0,self.video_workflow.findData(self.settings.video_workflow)))
        output_form.addRow('视频输出方式',self.video_workflow)
        self.settings_tabs.addTab(output_options,'视频输出')
        from ui.update_panel import UpdatePanel
        self.updates=UpdatePanel(self)
        self.preferences.about_layout.addRow(self.updates)
        self.roundup.request_voice_setup=self.show_voice_settings
        self.roundup.voice_setup_busy=lambda:self.voice_setup.worker is not None
        for index in (4,5):self.preferences.nav.item(index).setHidden(True)
        for component in (self.preferences,self.rules):
            component.setMinimumSize(0,0)
            for box in component.findChildren(QDialogButtonBox):box.hide()
        actions=QHBoxLayout();self.apply_button=self.button('production.apply_settings',self.apply_preferences,actions,True)
        self.button('术语词典',self.open_terms,actions)
        self.button('production.apply_rules',self.apply_rules,actions);self.button('production.save_voice',self.roundup._save_voice,actions)
        actions.insertStretch(0,1)
        layout.addLayout(actions);self.pages.addWidget(page)

    def open_terms(self):
        if self.roundup.worker:raise ValueError(tr('production.conflict'))
        from ui.term_editor_dialog import TermEditorDialog
        editor=TermEditorDialog(self.settings,self)
        if editor.exec()==QDialog.DialogCode.Accepted:self.refresh_names()

    def _pronunciation_editor(self,form):
        r=self.roundup
        self._detach_widget(r.voice_page,r.pronunciations)
        advanced=QWidget();advanced_form=QFormLayout(advanced)
        advanced_form.addRow('高级 JSON 导入 / 导出',r.pronunciations)
        for control,label in ((r.pause,'段间停顿（秒）'),(r.preview_seconds,'无音频预览时长')):
            self._move_form_control(r.voice_page,control,advanced_form,label)
        toggle=QPushButton('高级选项');toggle.setCheckable(True);toggle.toggled.connect(advanced.setVisible)
        toggle.setObjectName('TextButton');toggle.setMaximumWidth(120)
        self.pronunciation_table=QTableWidget(0,2);configure_table(self.pronunciation_table);self.pronunciation_table.setHorizontalHeaderLabels(['原文','读法'])
        self.pronunciation_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pronunciation_table.setMinimumHeight(130)
        form.addRow('发音替换',self.pronunciation_table)
        add=QPushButton('添加读法');add.clicked.connect(lambda:self.pronunciation_table.insertRow(self.pronunciation_table.rowCount()))
        remove=QPushButton('删除选中读法');actions=QHBoxLayout();actions.addWidget(add);actions.addWidget(remove);actions.addStretch(1)
        form.addRow(actions);form.addRow(toggle);form.addRow(advanced);advanced.hide()
        def read():
            try:rules=json.loads(r.pronunciations.toPlainText())
            except ValueError:return
            if not isinstance(rules,dict):return
            self.pronunciation_table.blockSignals(True);self.pronunciation_table.setRowCount(len(rules))
            for row,(source,value) in enumerate(rules.items()):
                self.pronunciation_table.setItem(row,0,QTableWidgetItem(source));self.pronunciation_table.setItem(row,1,QTableWidgetItem(value))
            self.pronunciation_table.blockSignals(False)
        def store():
            rules={}
            for row in range(self.pronunciation_table.rowCount()):
                source=self.pronunciation_table.item(row,0);value=self.pronunciation_table.item(row,1)
                if source and source.text().strip() and value:rules[source.text().strip()]=value.text()
            r.pronunciations.blockSignals(True);r.pronunciations.setPlainText(json.dumps(rules,ensure_ascii=False,indent=2));r.pronunciations.blockSignals(False)
            r._voice_changed();self._voice_refresh()
        r.pronunciations.textChanged.connect(read);self.pronunciation_table.itemChanged.connect(store);read()
        def remove_rule():
            row=self.pronunciation_table.currentRow()
            if row>=0:self.pronunciation_table.removeRow(row);store()
        remove.clicked.connect(remove_rule)

    def _build_inspector(self,layout):
        self.inspector=QWidget();box=QVBoxLayout(self.inspector);row=QHBoxLayout()
        row.addWidget(QLabel('详细资料与诊断'),1)
        self.button('复制诊断',lambda:QApplication.clipboard().setText(self.inspector_text.toPlainText()),row)
        self.button('关闭',self.inspector.hide,row);box.addLayout(row)
        self.inspector_text=QPlainTextEdit();self.inspector_text.setReadOnly(True);box.addWidget(self.inspector_text)
        self.inspector.setMaximumHeight(230);self.inspector.hide();layout.addWidget(self.inspector)

    def _connect(self):
        self.theme.theme_changed.connect(self._launch_detail)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex);self.content.currentIndexChanged.connect(self._mode)
        self.navigation.currentRowChanged.connect(self._navigated)
        self.materials.currentChanged.connect(self._materials_changed)
        self.news.collect_requested.connect(lambda:self.guard(self.start_news_collect));self.news.write_requested.connect(lambda:self.guard(self.start_news_write))
        self.dashboard.start_requested.connect(lambda:self.guard(self.start_news_collect));self.roundup.job_changed.connect(self._poll)
        self.roundup.draft_changed.connect(self._header)
        self.news.changed.connect(self._changed);self.dashboard.settings_changed.connect(self._changed);self.feed_panel.feeds_changed.connect(self._changed)
        d=self.dashboard
        for control in (d.date_mode_combo,d.launch_range_combo):control.currentIndexChanged.connect(self._remember_source_dates)
        for control in (d.start_date_edit,d.end_date_edit,d.launch_start_date_edit,d.launch_end_date_edit):
            control.dateChanged.connect(self._remember_source_dates)
        self.news.changed.connect(lambda:self._voice_refresh() if self.mode=='news' else None)
        self.news.article_changed.connect(lambda:self._voice_refresh() if self.mode=='news' else None)
        self.news.narration_requested.connect(lambda:self.guard(self.propose_news_voice))
        self.news.retry_requested.connect(lambda:self.guard(self.retry_news_narrations))
        self.news.voice_requested.connect(self.open_news_voice)
        self.news.diagnostic_requested.connect(self.inspect_news_failure)
        self.news.image_requested.connect(lambda article,options:self.guard(lambda:self.start_news_image(article,options)))
        self.roundup.tts_state_changed.connect(lambda message:self.task_text.setText(message) if self.worker and self.worker_kind.startswith('voice_') else None)
        self.preferences.output_edit.editingFinished.connect(self._changed)
        self.roundup.segment_list.currentRowChanged.connect(lambda *_:self._voice_refresh())
        self.roundup.job_changed.connect(lambda:self._launch_detail() if self.roundup.worker is None else None)
        self.roundup.job_changed.connect(lambda:self._stop_preview() if self.roundup.worker is not None else None)
        self.voice_setup.changed.connect(self._poll)
        self.preferences.accepted.connect(lambda:self.guard(self.apply_preferences));self.rules.accepted.connect(lambda:self.guard(self.apply_rules))
        self.media.currentChanged.connect(self._media_changed)

    def _mode(self):
        self.mode=self.content.currentData();self.scripts.setCurrentIndex(1 if self.mode=='roundup' else 0)
        if getattr(self,'_media_mode',None)!=self.mode:
            self._release_preview();self._frame_key=None;self._media_mode=self.mode
            self._preview_news_id=self._roundup_focus_id=None
        self.media.setTabText(0,'新闻配音' if self.mode=='news' else tr('production.roundup_voice'))
        self.media.setTabText(1,'画面素材')
        self.cards_mode_note.setVisible(self.mode=='roundup')
        materials_only=self.settings.video_workflow=='materials'
        if not materials_only and self.media.currentIndex()==1:self.media.setCurrentIndex(0)
        self.media.setTabVisible(1,materials_only)
        self.cards_export_button.setText('导出无声画面素材（MOV）')
        self.cards_export_button.setVisible(materials_only);self.include_intro.setVisible(materials_only)
        self.complete_export_button.setVisible(not materials_only)
        self.append_news.setVisible(not materials_only and self.mode=='roundup')
        self.speech_button.setText('确认并生成本条配音' if self.mode=='news' else tr('production.synthesize'))
        self.listen_button.setText('试听本条' if self.mode=='news' else tr('production.listen'))
        self.preview_button.setText('预览新闻画面' if self.mode=='news' else tr('production.preview'))
        self.preview_tasks.setVisible(self.mode=='roundup')
        self.voice_image_use.setVisible(self.mode=='news')
        self.roundup.export_controls.setVisible(materials_only and self.mode=='roundup')
        self.export_news_button.setVisible(self.mode=='news');self.news_formats.setVisible(self.mode=='news')
        self.export_hint.setText('画面素材导出无声透明 MOV，可另行导出稿件。' if materials_only else
            '本次成片只在所选目录新增一个带配音的 MP4。')
        if self.navigation.currentRow()==2 and self.media.currentIndex()==1 and not self._restoring:self.guard(self.sync_current_cards)
        if self.navigation.currentRow()==3 and not self._restoring:self._refresh_files()
        if self.navigation.currentRow()==2 and self.media.currentIndex()==0 and not self._restoring:self._voice_refresh()
        self._header(check_saved=False)

    def _materials_changed(self,index):
        self.inspector.hide()
        if index in (0,1):self.content.setCurrentIndex(index)

    def _media_changed(self,index):
        self.inspector.hide()
        if index==0 and not self._restoring:self._voice_refresh()
        if index!=0:self._release_preview()
        if index!=1:self.cards._stop_preview()
        if index==1 and not self._restoring:self.guard(self.sync_current_cards)
        if index==3:self.sync_download_tasks()

    def media_route(self,index):
        if index==1 and self.settings.video_workflow!='materials':index=0
        self.navigation.setCurrentRow(2);self.media.setCurrentIndex(index)
        if index==0:self._voice_refresh()
        if index==3:self.sync_download_tasks()

    def open_rules(self):
        self.show_settings();self.settings_tabs.setCurrentIndex(1)
        from services.prompt_manager import PROMPT_TYPES
        self.rules.type_list.setCurrentRow(PROMPT_TYPES.index('roundup') if self.mode=='roundup' else 0)

    def show_settings(self):
        self.inspector.hide()
        self.navigation.blockSignals(True);self.navigation.setCurrentRow(-1);self.navigation.blockSignals(False)
        self.pages.setCurrentIndex(4)
        self.page_title.setText(tr('settings'))

    def show_about(self):
        self.show_settings()
        self.settings_tabs.setCurrentIndex(0)
        self.preferences.nav.setCurrentRow(self.preferences.about_index)

    def _navigated(self,index):
        self.inspector.hide()
        if not self.busy():self.task_text.setText(tr('ready'))
        if index!=2:
            self._stop_preview();self.cards._stop_preview();self.intro._stop_preview()
        if index>=0:
            self._last_page=index;self.page_title.setText(self.navigation.item(index).text())
        if index==2:
            if self.media.currentIndex()==1 and not self._restoring:self.guard(self.sync_current_cards)
            else:self._voice_refresh()
        if index==3:self._refresh_files()

    def inspect(self,value):
        self.inspector_text.setPlainText(json.dumps(value,ensure_ascii=False,indent=2,default=str));self.inspector.show()

    def record_diagnostic(self,value):
        self._last_diagnostic=value
        self.last_diagnostic_button.show()
        self.log.append_html(escape(json.dumps(value,ensure_ascii=False,default=str)))

    def inspect_news_failure(self,failure):
        self.inspect(failure)
        if not failure.get('诊断'):return
        path=Path(failure['诊断']).resolve()
        if not path.is_relative_to(resource_path('logs','ai_raw').resolve()):return
        try:
            with path.open(encoding='utf-8') as stream:
                self.inspector_text.appendPlainText('\n'+stream.read(100000))
        except (OSError,ValueError):pass

    def open_news_voice(self,identifier):
        self.content.setCurrentIndex(self.content.findData('news'))
        if self.news.select_article(identifier):self.media_route(0)

    def _voice_selected(self,index):
        self._stop_preview()
        if self.mode=='news':
            if index>=0:self.news.select_article(self.voice_segments.itemData(index))
            return
        if index>=0 and index!=self.roundup.segment_list.currentRow():self.roundup.segment_list.setCurrentRow(index)

    def media_draft(self):
        if self.mode=='news':
            draft=self.news.voice_draft()
            if draft is None:raise ValueError('请先在稿件页选择一条新闻')
            return draft
        return self.roundup.draft

    def _media_basis(self):
        if self.mode=='news':return roundup.digest([self.news.voice_draft(),False])
        return roundup.digest([self.roundup.draft,self.roundup._voice_dirty])

    def synthesize_media(self):
        return self.news_audio() if self.mode=='news' else self.roundup._audio_all()

    def _news_voice_refresh(self):
        from services import news_voice
        draft=self.news.voice_draft();articles=self.news.bundle['articles'] if self.news.bundle else []
        from models.news import news_id
        self.voice_segments.blockSignals(True);self.voice_segments.clear()
        for article in articles:self.voice_segments.addItem(self.news._title(article),news_id(article.news.url))
        self.voice_segments.setCurrentIndex(self.voice_segments.findData(draft['news_id']) if draft else -1)
        self.voice_segments.blockSignals(False)
        self._voice_loading=True
        text=draft['segments'][0]['spoken'] if draft else ''
        if self.voice_text.toPlainText()!=text:self.voice_text.setPlainText(text)
        self._voice_loading=False;self.voice_text.setPlaceholderText('先在稿件页生成或填写新闻口播稿，摘要不会自动配音')
        try:self.play_state.setText(news_voice.state(draft) if draft else '请选择新闻')
        except ValueError as error:self.play_state.setText('最终朗读待检查：'+str(error))
        self.news.refresh_voice();self.play_state.setToolTip(self.news.final_input.toPlainText())
        from services.voice_profiles import configured
        voice=(draft or {}).get('tts',{});self.voice_status.setText('语音已配置' if configured(voice) else '尚未配置语音模型')
        from services import news_images
        try:
            row=news_images.load()['news'].get(draft['news_id'],{}) if draft else {}
            self.voice_image_use.setEnabled(draft is not None)
            self.voice_image_use.blockSignals(True);self.voice_image_use.setChecked(row.get('use_image',True));self.voice_image_use.blockSignals(False)
        except (OSError,ValueError,KeyError,TypeError):self.voice_image_use.setEnabled(False)
        if not getattr(self,'_preview_syncing',False):
            identifier=draft['news_id'] if draft else None
            if identifier!=getattr(self,'_preview_news_id',None):
                self._preview_news_id=identifier;self._preview_select_pending=True
            if not draft or self._media_basis()!=getattr(self,'_frame_key',None):self._release_preview()
        if draft and self.frame.isVisible():self._preview_news()
        elif not draft:self.frame.setText('请选择新闻；没有音频时仍可预览条卡画面')

    def _preview_news(self):
        from services import news_voice
        draft=self.media_draft();key=self._media_basis()
        if getattr(self,'_frame_key',None)!=key:
            self._news_frames=news_voice.prepare_frames(draft);self._preview_timeline=self._news_frames['timeline']
            self._timeline_key=key;self._frame_key=key
            self.preview_slider.blockSignals(True);self.preview_slider.setRange(0,round(self._preview_timeline['duration_seconds']*1000));self.preview_slider.blockSignals(False)
        if getattr(self,'_preview_select_pending',False):
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(0);self.preview_slider.blockSignals(False);self._preview_select_pending=False
        time=self.preview_slider.value()/1000;image=news_voice.frame_at(self._news_frames,time)
        qimage=QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888).copy();self.frame.set_image(QPixmap.fromImage(qimage))
        self.preview_slider.setEnabled(True);row=self._preview_timeline['segments'][0]
        cue=next((u['screen'] for u in row['units'] if u['global_start_seconds']<=time<u['global_end_seconds']),'')
        mode='播放器时间' if getattr(self,'_audio_playback',False) else '无声预览时间'
        message=row['message'] if draft['segments'][0]['spoken'].strip() else '尚未生成口播稿；可先预览画面，再到稿件页生成或填写口播'
        self.preview_clock.setWordWrap(True);self.preview_clock.setText(mode+f" · {time:.1f} / {self._preview_timeline['duration_seconds']:.1f} 秒 · "+message+('\n字幕：'+cue if cue else ''))

    def propose_news_voice(self):
        from services import news_voice
        draft=self.media_draft();article=deepcopy(self.news.current_article());settings=self.current_settings()
        self._news_proposal_basis=draft['segments'][0]['spoken']
        self._news_job('voice_propose',lambda _data,token:news_voice.propose_missing([article],settings,token,set()),deepcopy(draft))

    def retry_news_narrations(self):
        from services import news_voice
        articles=deepcopy(self.news.failed_articles())
        if not articles:return
        settings=self.current_settings()
        self.checkpoint()
        self._news_job('narration_retry',lambda data,token:news_voice.propose_missing(data,settings,token,set()),articles)

    def news_audio(self,*,force=False,play=False):
        from services import news_voice
        from services.roundup_audio import current_audio,reading_text
        draft=self.media_draft();segment=draft['segments'][0]
        if not segment['spoken'].strip():raise ValueError('请先在稿件页生成或编辑新闻口播稿，摘要不会自动用于配音')
        if current_audio(draft,segment) and not force and play:
            self._preview_select_pending=True;self._preview_news();self._toggle_preview();return
        voice=draft['tts']
        if not voice.get('python') or not Path(voice['python']).is_file() or not (voice.get('speaker','').startswith('my:') or voice.get('model') and Path(voice['model']).is_dir()):
            self.show_voice_settings();self.voice_setup.status.setText('请先自动配置语音，或检查已有环境；不需要重新下载可用模型');return
        text=reading_text(draft,segment)
        if draft.get('approved_text')!=roundup.digest(text):
            if QMessageBox.question(self,'确认本条最终朗读文本',text+'\n\n按当前口播稿生成配音？来源和状态不会朗读。')!=QMessageBox.StandardButton.Yes:return
        news_voice.approve(draft);self.checkpoint();self._news_play_after=play
        provider=self.roundup.tts_provider
        def action(data,token):
            from ai.qwen_aligner import QwenForcedAligner
            provider.configure(data['tts']);aligner=data.get('aligner',{})
            return news_voice.synthesize(data,provider,QwenForcedAligner(aligner,self.roundup.tts_state_changed.emit) if aligner.get('model') and aligner.get('python') else None,token,force=force)
        self._news_job('voice_audio',action,deepcopy(draft))

    def align_news_voice(self):
        draft=self.media_draft();aligner=draft.get('aligner',{})
        if not aligner.get('model') or not aligner.get('python'):
            self.show_voice_settings();self.voice_setup.status.setText('请先配置或检查现有对齐环境');return
        def action(data,token):
            from ai.qwen_aligner import QwenForcedAligner
            from services.roundup_sync import align_draft
            self.roundup.tts_provider.unload()
            return align_draft(data,QwenForcedAligner(data['aligner'],self.roundup.tts_state_changed.emit),token)
        self._news_job('voice_align',action,deepcopy(draft))

    def export_complete(self):
        from services.roundup_export import export_complete_video
        if self.settings.video_workflow!='complete':return
        drafts=[]
        if self.mode=='roundup':
            self.roundup._sync()
            if not self.roundup.draft['segments']:raise ValueError('请先生成发射综述稿并配音')
            drafts.append(self.roundup.draft)
        if self.mode=='news' or self.append_news.isChecked():
            articles=self.news.selected_bundle()['articles']
            if not articles:raise ValueError('请先在稿件页勾选要导出的新闻')
            drafts.extend(self.news.voice_draft(article) for article in articles)
        output=self.preferences.output_edit.text().strip()
        if not output:raise ValueError('请先选择输出目录')
        mode=self.mode
        self._complete_export_scope=self.export_scope(mode)
        self._news_job('complete_video',lambda data,token:export_complete_video(data,output,token),deepcopy(drafts))

    def _news_voice_finished(self,worker,kind):
        from services import news_voice
        payload=worker.payload;identifier=payload['news_id'];current=self.news.voices.get(identifier)
        if kind!='voice_propose' and current and current['segments'][0]['spoken']==payload['segments'][0]['spoken']:
            self.news.voices[identifier]=payload
        if worker.error:
            self.record_diagnostic(dict(操作=kind,原因=worker.error))
            page=1 if kind=='voice_propose' else 2
            if self.mode=='news' and self.navigation.currentRow()==page:
                self.task_text.setText('新闻操作失败，已有稿件和音频保留')
            else:self.task_text.setText(tr('ready'))
        elif worker.cancelled:self.task_text.setText('已取消；成功音频及历史版本保留')
        elif kind=='voice_propose':
            proposals,failures,states=worker.value
            proposal=proposals.get(identifier)
            self.task_text.setText(tr('ready'))
            if not proposal:
                self.news.apply_narrations({},failures,states)
                if failures:self.record_diagnostic(dict(口播失败=failures))
                elif states[identifier]=='skipped':self.task_text.setText('请先在设置中配置文本模型')
            elif current and current['segments'][0]['spoken']==self._news_proposal_basis:
                old=current['segments'][0]['spoken'];proposed=proposal['spoken']
                preview='当前口播：\n'+(old or '（尚未填写）')+'\n\n生成提案（待审稿）：\n'+proposed
                if QMessageBox.question(self,'采用新闻口播提案',preview+'\n\n采用此稿？原始新闻资料不会改变。')==QMessageBox.StandardButton.Yes:
                    self.checkpoint();news_voice.apply_proposal(current,proposal)
                    self.news.narration_failures.pop(identifier,None)
                    self.news.editor_tabs.setCurrentIndex(0)
                    self.task_text.setText('已采用本条口播稿')
            else:self.task_text.setText('口播已修改，生成提案未覆盖当前稿件')
        else:self.task_text.setText(news_voice.state(self.news.voices[identifier]))
        worker.deleteLater();self.news._update_script_rows();self.news.refresh_voice();self._voice_refresh();self._changed();self._poll()
        if kind=='voice_audio' and not worker.error and not worker.cancelled and getattr(self,'_news_play_after',False):
            if self.mode=='news' and self.news.voice_draft().get('news_id')==identifier:
                from services.roundup_audio import current_audio
                if current_audio(self.news.voices[identifier],self.news.voices[identifier]['segments'][0]):
                    self._preview_select_pending=True;self._preview_news();self._toggle_preview()

    def _voice_refresh(self):
        if not hasattr(self,'voice_segments'):return
        if self.mode=='news':return self._news_voice_refresh()
        r=self.roundup;index=r.segment_list.currentRow();titles=[roundup.segment_title(r.draft,s) for s in r.draft['segments']]
        if [self.voice_segments.itemText(i) for i in range(self.voice_segments.count())]!=titles:
            self.voice_segments.blockSignals(True);self.voice_segments.clear();self.voice_segments.addItems(titles);self.voice_segments.blockSignals(False)
        self.voice_segments.blockSignals(True);self.voice_segments.setCurrentIndex(index);self.voice_segments.blockSignals(False)
        text=roundup.narration_text(r.draft['segments'][index]) if index>=0 else ''
        self._voice_loading=True
        if self.voice_text.toPlainText()!=text:self.voice_text.setPlainText(text)
        self._voice_loading=False
        self.voice_text.setPlaceholderText(tr('production.script_empty'));self.voice_text.setReadOnly(r.worker is not None)
        from services.voice_profiles import configured
        ready=configured(dict(python=r.python.text().strip(),model=r.model.text().strip(),speaker=r.speaker_id()))
        self.voice_status.setText('语音已配置' if ready else '尚未配置语音模型')
        self.play_state.setText(r.audio_state.text() if index>=0 else '请选择段落')
        self.play_state.setToolTip(r.reading_preview.toPlainText())
        self.name_update_button.setVisible(bool(r.draft.get('name_updates_pending')))
        if not getattr(self,'_preview_syncing',False):
            identifier=r.draft['segments'][index]['id'] if index>=0 else None
            if identifier!=getattr(self,'_roundup_focus_id',None):
                self._roundup_focus_id=identifier;self._preview_select_pending=True
            self._stop_preview()
        task_values=[(roundup.display_names(i)['name'],i['ll2_id']) for i in r.draft['snapshot']['items']]
        if getattr(self,'_preview_task_values',None)!=task_values:
            self.preview_tasks.clear();self.preview_tasks.addItem('跳转到任务…',None)
            for title,identifier in task_values:self.preview_tasks.addItem(title,identifier)
            self._preview_task_values=task_values
        key=self._preview_key() if index>=0 else None
        if getattr(self,'_preview_syncing',False):return
        if getattr(self,'_frame_key',None)!=key:
            if self.frame.image is not None and index>=0:self.preview_roundup()
            else:self.frame.setText(tr('production.preview_hint'))

    def preview_roundup(self):
        if self.mode=='news':return self._preview_news()
        r=self.roundup;index=r.segment_list.currentRow()
        if index<0:raise ValueError(tr('production.script_empty'))
        from tools.roundup_visuals import prepare_frames,frame_at
        from services.roundup_sync import build_timeline,row_at
        from services.roundup_audio import segment_timing
        basis=roundup.digest([r.draft,r._voice_dirty])
        if getattr(self,'_timeline_key',None)!=basis:
            view=deepcopy(r.draft)
            if r._voice_dirty:
                for s in view['segments']:s['audio']=None
            self._preview_view=view;self._preview_timeline=build_timeline(view);self._timeline_key=basis
            self.preview_slider.blockSignals(True);self.preview_slider.setRange(0,round(self._preview_timeline['duration_seconds']*1000));self.preview_slider.blockSignals(False)
        if getattr(self,'_preview_select_pending',False):
            clock=self._preview_timeline['segments'][index]
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(ceil(clock['start_seconds']*1000));self.preview_slider.blockSignals(False)
            self._preview_select_pending=False
        global_time=self.preview_slider.value()/1000
        clock=row_at(self._preview_timeline,global_time)
        index=next(n for n,s in enumerate(r.draft['segments']) if s['id']==clock['segment_id'])
        if index!=r.segment_list.currentRow():
            self._preview_syncing=True
            try:r.segment_list.setCurrentRow(index)
            finally:self._preview_syncing=False
        self._roundup_focus_id=clock['segment_id']
        key=self._preview_key()
        if getattr(self,'_frame_key',None)!=key:
            segment=self._preview_view['segments'][index]
            self._preview_timing=segment_timing(self._preview_view,segment)
            self._preview_frames=prepare_frames(self._preview_view,segment,timing=self._preview_timing)
        elapsed=max(0,global_time-clock['start_seconds']);duration=self._preview_timeline['duration_seconds']
        image=frame_at(self._preview_frames,elapsed).convert('RGBA')
        qimage=QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888).copy()
        self.frame.set_image(QPixmap.fromImage(qimage))
        self.preview_slider.setEnabled(True)
        mode='播放器时间' if getattr(self,'_audio_playback',False) else '无声预览时间'
        self.preview_clock.setText(mode+f' · {global_time:.1f} / {duration:.1f} 秒 · '+clock['message'])
        self._frame_key=key

    def _stop_preview(self):
        was_playing=self.preview_timer.isActive()
        self.preview_timer.stop();self.preview_play.setText('播放画面')
        self._audio_wants_play=False;self._preview_request_active=False
        if hasattr(self,'preview_player'):
            self.preview_player.pause()
            if was_playing:self._audio_position_changed(self.preview_player.position())
        self.roundup._stop_playback()

    def _audio_position_changed(self, _position):
        if (getattr(self,'_audio_playback',False) and getattr(self,'_audio_ready',False)
                and not getattr(self,'_audio_finished',False)
                and not self.preview_timer.isActive() and not self.preview_slider.isSliderDown() and self.frame.isVisible()
                and getattr(self,'_timeline_key',None)==self._media_basis()):
            self._advance_preview()

    def _release_preview(self):
        self._stop_preview();self._audio_playback=False;self._audio_ready=False;self._audio_pending_position=None
        if hasattr(self,'preview_player'):
            self.preview_player.stop();self.preview_player.setSource(QUrl())

    def _toggle_preview(self):
        if self.preview_timer.isActive():self._stop_preview();return
        if self.mode!='news':self.roundup._sync()
        self._audition_end=None
        self.preview_roundup()
        if self.preview_slider.value()>=self.preview_slider.maximum():self.preview_slider.setValue(0)
        from services.roundup_sync import row_at
        clock=row_at(self._preview_timeline,self.preview_slider.value()/1000)
        if clock['audio']:
            self._prepare_audio_preview()
            return
        self._audio_playback=False
        self._preview_started=time.monotonic()-self.preview_slider.value()/1000
        self.preview_timer.start();self.preview_play.setText('暂停画面')

    def _advance_preview(self):
        if not self.frame.isVisible():self._stop_preview();return
        if getattr(self,'_audio_playback',False):
            if not getattr(self,'_audio_ready',False):return
            elapsed=self.preview_player.position()/1000+self._audio_offset
            if getattr(self,'_audition_end',None) is not None and elapsed>=self._audition_end:
                self._stop_preview();self._audition_end=None
        else:elapsed=time.monotonic()-self._preview_started
        self.preview_slider.blockSignals(True);self.preview_slider.setValue(round(elapsed*1000));self.preview_slider.blockSignals(False)
        self.preview_roundup()
        if elapsed>=self._preview_timeline['duration_seconds']:
            self._audio_finished=True;self._stop_preview()

    def _begin_preview_seek(self):
        self._seek_was_playing=self.preview_timer.isActive();self._stop_preview()

    def _end_preview_seek(self):
        if getattr(self,'_seek_was_playing',False):self._toggle_preview()

    def _seek_preview(self):
        self._preview_select_pending=False
        if getattr(self,'_audio_playback',False):
            target=self.preview_slider.value()/1000
            if self._audio_offset<=target<=self._audio_end:
                self._audio_finished=False
                self.preview_player.setPosition(round((target-self._audio_offset)*1000))
            else:
                self._stop_preview();self._audio_playback=False
        self.preview_roundup()

    def _prepare_audio_preview(self):
        r=self.roundup
        if self.mode!='news':r._sync()
        target=self.preview_slider.value()/1000
        self._audio_target=target
        def prepare(data,token):
            from services.roundup_sync import build_timeline,timeline_audio,row_at,canonical_audio
            timeline=build_timeline(data);clock=row_at(timeline,target)
            if timeline['all_audio_current']:
                path=timeline_audio(data,timeline,token);offset=0.;end=timeline['duration_seconds']
            else:
                if not clock['audio']:raise ValueError('本段尚无当前配音，可使用无音频预览')
                path=canonical_audio(clock['audio'],token);offset=clock['start_seconds'];end=clock['speech_end_seconds']
            return dict(path=str(path),offset=offset,end=end,basis=roundup.digest(data))
        r._start_job('preview_audio',prepare,deepcopy(self.media_draft()))
        self._preview_request_active=True

    def _audio_preview_ready(self,result):
        from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
        if not getattr(self,'_preview_request_active',False):return
        if result['basis']!=roundup.digest(self.media_draft()):
            self.task_text.setText('准备播放期间稿件已变化，请重新播放');return
        if not hasattr(self,'preview_player'):
            self.preview_player=QMediaPlayer(self);self.preview_audio_output=QAudioOutput(self)
            self.preview_player.setAudioOutput(self.preview_audio_output)
            self.preview_player.mediaStatusChanged.connect(self._audio_media_status)
            self.preview_player.positionChanged.connect(self._audio_position_changed)
            self.preview_player.errorOccurred.connect(lambda *_:self._audio_preview_error())
        self._audio_offset=result['offset'];self._audio_end=result['end'];self._audio_playback=True;self._audio_ready=False
        self._audio_finished=False
        self._audio_wants_play=True
        self._audio_pending_position=round(max(0,self._audio_target-result['offset'])*1000)
        url=QUrl.fromLocalFile(result['path'])
        if self.preview_player.source()==url:
            self._audio_ready=True;self.preview_player.setPosition(self._audio_pending_position);self._audio_pending_position=None;self.preview_player.play()
        else:self.preview_player.setSource(url)
        self.preview_timer.start();self.preview_play.setText('暂停播放')

    def _audio_media_status(self,status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self._audio_pending_position is not None:
            self.preview_player.setPosition(self._audio_pending_position);self._audio_pending_position=None;self._audio_ready=True
            if self._audio_wants_play:self.preview_player.play()
        elif status==QMediaPlayer.MediaStatus.EndOfMedia:
            self._audio_finished=True
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(round(self._audio_end*1000));self.preview_slider.blockSignals(False)
            self.preview_roundup();self._stop_preview()

    def _audio_preview_error(self):
        self._stop_preview();self._audio_playback=False
        self.task_text.setText('音频播放失败：'+self.preview_player.errorString())

    def listen_roundup(self):
        if self.mode=='news':return self.news_audio(play=True)
        self.roundup._sync();index=self.roundup.segment_list.currentRow()
        if index<0:raise ValueError('请选择段落')
        from services.roundup_audio import current_audio
        segment=self.roundup.draft['segments'][index]
        if not current_audio(self.roundup.draft,segment):
            self.roundup._audio_job(segment['id']);return
        self._preview_select_pending=True;self.preview_roundup();self._toggle_preview()
        from services.roundup_sync import row_at
        self._audition_end=row_at(self._preview_timeline,self.preview_slider.value()/1000)['speech_end_seconds']

    def jump_task(self,identifier):
        if not identifier:return
        self.preview_roundup()
        rows=self._preview_timeline['segments']
        units=[u for row in rows for u in row['units'] if identifier in u['task_ids']]
        precise=next((u for u in units if u['display']=='task'),next(iter(units),None))
        if precise:target=precise['global_start_seconds']
        else:
            row=next((row for row,s in zip(rows,self.roundup.draft['segments']) if identifier in s['task_ids']),None)
            if not row:raise ValueError('该任务没有讲解关联，请在稿件页检查')
            target=row['start_seconds'];self.task_text.setText('尚无有效句子对齐，已定位到关联段落')
        self._stop_preview();self._preview_select_pending=False;self.preview_slider.setValue(ceil(target*1000));self.preview_roundup()

    def audition_sync(self,identifier,start,end):
        from services.roundup_sync import build_timeline
        timeline=build_timeline(self.media_draft());row=next(r for r in timeline['segments'] if r['segment_id']==identifier)
        self.preview_roundup()
        self._stop_preview();self._preview_select_pending=False
        self.preview_slider.setValue(ceil((row['start_seconds']+start)*1000));self.preview_roundup()
        self._audition_end=min(row['speech_end_seconds'],row['start_seconds']+end);self._prepare_audio_preview()

    def show_sync(self):
        r=self.roundup
        if self.mode=='news':
            if self.busy():raise ValueError('请先等待或取消当前任务')
            self.media_draft()
            from ui.roundup_sync_dialog import RoundupSyncDialog
            RoundupSyncDialog(self).exec();return
        if r.worker:raise ValueError('请先等待或取消当前任务')
        r._sync()
        if r.segment_list.currentRow()<0:raise ValueError('请选择段落')
        from ui.roundup_sync_dialog import RoundupSyncDialog
        RoundupSyncDialog(self).exec()

    def _alignment_settings_changed(self,*_):
        r=self.roundup
        if r._loading:return
        r.draft['aligner']=dict(python=r.align_python.text().strip(),model=r.align_model.text().strip(),
            device=r.align_device.text().strip() or 'cpu',language=r.align_language.currentText(),timeout=r.align_timeout.value())
        self._stop_preview();self._frame_key=None

    def show_shot_settings(self):
        if self.mode=='news':raise ValueError('新闻使用固定条卡画面，不套用发射镜头编排')
        if self.roundup.worker:raise ValueError(tr('production.conflict'))
        from ui.roundup_shots_dialog import RoundupShotsDialog
        RoundupShotsDialog(self).exec()

    def align_roundup(self,identifier=None):
        if self.mode=='news':return self.align_news_voice()
        r=self.roundup;r._sync()
        if self.voice_setup.worker:raise ValueError('语音环境正在配置，请等待完成或取消')
        if not r.draft.get('aligner',{}).get('model') or not r.draft.get('aligner',{}).get('python'):
            self.show_voice_settings();self.voice_setup.status.setText('请先自动配置语音，或检查已有的对齐环境');return
        def align(data,token):
            from services.roundup_sync import align_draft
            from ai.qwen_aligner import QwenForcedAligner
            r.tts_provider.unload()
            return align_draft(data,QwenForcedAligner(data.get('aligner',{}),r.tts_state_changed.emit),token,segment_id=identifier)
        r._start_job('align',align,deepcopy(r.draft))

    def _preview_key(self):
        if self.mode=='news':return self._media_basis()
        draft=self.roundup.draft;segment=draft['segments'][self.roundup.segment_list.currentRow()]
        from translator.dictionary import load_dictionary
        entries=load_dictionary()
        return roundup.digest([draft['id'],segment,draft['segments'],draft['snapshot'],draft['selection'],
                               draft['tts'],draft['pronunciations'],draft.get('aligner'),draft['pause_seconds'],
                               draft.get('visuals'),draft.get('closing_hold_seconds'),self.roundup._voice_dirty,[roundup.display_names(i,entries) for i in draft['snapshot']['items']]])

    def current_settings(self):
        return self.dashboard.settings_snapshot(replace(self.settings,output_dir=self.preferences.output_edit.text().strip(),
         export_markdown=self.preferences.markdown_check.isChecked(),export_word=self.preferences.word_check.isChecked(),
         export_premiere=self.preferences.premiere_check.isChecked(),export_sqlite=self.preferences.sqlite_check.isChecked()))

    def _save_runtime(self):
        if not self.dashboard.date_ranges_valid():raise ValueError(tr('date.range.invalid'))
        self.settings=self.current_settings();save_settings(self.settings);apply_settings(self.settings)
        self.feed_panel.save_state();self.roundup.settings=self.settings

    def _news_job(self,kind,action,payload):
        if any(k not in ('maintenance','news_image','update') for k,*_ in self.task_rows()):raise ValueError(tr('production.conflict'))
        self._stop_preview()
        self._news_export_scope=self.export_scope('news')
        self._save_runtime();self.worker_kind=kind;self.worker=RoundupWorker(action,payload,self)
        self.worker.finished.connect(self._news_finished)
        self.news.set_running(True)
        self.worker.start();self._poll()

    def start_news_image(self,article,options):
        if self.image_worker:raise ValueError('正在处理新闻图片，可等待或在底部取消')
        from services.news_images import acquire
        self.image_worker=RoundupWorker(lambda data,token:acquire(data,token,**options),deepcopy(article),self)
        self.image_worker.finished.connect(self._news_image_finished);self.image_worker.start();self._poll()

    def _news_image_finished(self):
        worker=self.image_worker;self.image_worker=None
        if self.news.bundle:
            for article in self.news.bundle.get('source_articles',[])+self.news.bundle['articles']:
                if article.news.url==worker.payload.news.url:article.news.images=worker.payload.news.images
        self.news.refresh_image();self._frame_key=None
        if self.mode=='news':self._voice_refresh()
        self.task_text.setText('图片处理失败：'+worker.error if worker.error else '已取消图片下载，正式图片保留' if worker.cancelled else '图片处理完成；未找到配图时可导入本地图片')
        worker.deleteLater();self._changed();self._poll()

    def start_news_collect(self):
        if self.news.bundle and QMessageBox.question(self,tr('production.materials'),tr('production.replace_collection'))!=QMessageBox.StandardButton.Yes:return
        self.checkpoint();feeds=self.feed_panel.enabled_feeds();self._news_job('collect',lambda data,token:prepare_news(feeds,token,collect_only=True),{})

    def start_news_write(self):
        from services.news_voice import has_existing_spoken
        bundle=self.news.selected_bundle(for_write=True);self.checkpoint();feeds=self.feed_panel.enabled_feeds()
        skip={identifier for identifier,draft in self.news.voices.items() if has_existing_spoken(draft)}
        self._news_job('write',lambda data,token:prepare_news(feeds,token,prepared=data,narration_skip=skip),bundle);self.navigation.setCurrentRow(1)

    def start_news_export(self):
        bundle=self.news.selected_bundle();settings=self.current_settings()
        self._news_job('export',lambda data,token:export_news(data,settings,token),bundle)

    def import_legacy(self):
        source=QFileDialog.getExistingDirectory(self,'选择旧工作区或便携版数据目录')
        if not source:return
        target=QFileDialog.getExistingDirectory(self,'选择空的导入目标目录')
        if not target:return
        from services.workspace_import import import_data
        self._news_job('import',lambda data,token:import_data(data['source'],data['target'],token),dict(source=source,target=target))

    def _news_finished(self):
        worker,kind=self.worker,self.worker_kind;self.worker=None
        self.news.set_running(False)
        if kind.startswith('voice_'):return self._news_voice_finished(worker,kind)
        if worker.error:
            self.record_diagnostic(dict(操作=kind,原因=worker.error))
            page={'write':1,'narration_retry':1,'collect':0,'export':3,'complete_video':3}.get(kind)
            if page is None or (self.mode=='news' or kind=='complete_video') and self.navigation.currentRow()==page:
                self.task_text.setText('操作失败，已有内容保留')
            else:self.task_text.setText(tr('ready'))
            if kind in ('write','narration_retry'):
                self.news.batch_hint.setText('生成失败，已有稿件保留。可在任务栏查看诊断。');self.news.batch_hint.show()
        elif worker.cancelled:
            self.task_text.setText(tr('production.cancelled'))
            if kind=='write':
                self.news.batch_hint.setText(f"口播阶段开始前已取消，共 {len(worker.payload['articles'])} 条，已有稿件保留。")
                self.news.batch_hint.show()
        elif kind=='narration_retry':
            proposals,failures,states=worker.value
            self.news.apply_narrations(proposals,failures,states);self.news._update_script_rows()
            if failures:self.record_diagnostic(dict(口播失败=failures))
            self.task_text.setText('口播重试结束' if self.mode=='news' and self.navigation.currentRow()==1 else tr('ready'))
        elif kind=='export':
            for path in worker.value:self.add_result(path,self._news_export_scope)
            self._refresh_files();self.task_text.setText(tr('production.export_done'))
        elif kind=='complete_video':
            self.add_result(worker.value,self._complete_export_scope)
            self._refresh_files();self.task_text.setText('已导出带配音的 MP4')
        elif kind=='import':
            self.task_text.setText('旧数据已备份并导入：'+worker.value+'；原目录保留。')
            if QMessageBox.question(self,'导入完成','是否打开导入后的工作区？原目录与备份均保留。')==QMessageBox.StandardButton.Yes:
                QTimer.singleShot(0,lambda path=worker.value:self.request_leave(path))
        else:
            failures=worker.value.get('narration_failures',{})
            self.news.adopt(worker.value,collected=kind=='collect')
            if failures:self.record_diagnostic(dict(口播失败=failures))
            self.task_text.setText('新闻稿件已处理' if kind=='write' else '新闻资料已采集')
            self.roundup.collection=load_existing(Path(self.settings.output_dir))
        worker.deleteLater();self._changed();self._poll()

    def start_diagnostic(self,kind):
        if self.aux_worker is not None:raise ValueError(tr('production.conflict'))
        if kind=='launch':
            from feeds.launches import get_launches,last_error
            settings=self.current_settings()
            if not self.dashboard.date_ranges_valid():raise ValueError(tr('date.range.invalid'))
            def action(_,token):
                token.check()
                rows=get_launches(limit=5,launch_range=settings.launch_range,start_date=settings.launch_start_date,
                  end_date=settings.launch_end_date,display_timezone=settings.display_timezone,timeout=5)
                token.check()
                if last_error():raise ValueError(last_error())
                return [dict(name=a.news.title,time=a.news.published,description=a.body) for a in rows]
            self._aux_config=None;self.aux_kind=kind
            self.aux_worker=RoundupWorker(action,{},self);self.aux_worker.finished.connect(self._diagnostic_finished)
            self.aux_worker.start();self._poll();return
        from ai.configuration import provider_config_from_settings
        from ai.providers import list_models,test_connection
        config=provider_config_from_settings(self.preferences.settings())
        if not config.api_key:raise ValueError(tr('missing.api.key'))
        self._aux_config=config;self.preferences._model_request=config;self.aux_kind=kind
        candidates=list(self.preferences.settings().model_candidates)
        action=(lambda _,token:list_models(config,cancel_token=token)) if kind=='models' else (lambda _,token:test_connection(config,candidates,cancel_token=token))
        self.aux_worker=RoundupWorker(action,{},self);self.aux_worker.finished.connect(self._diagnostic_finished)
        self.aux_worker.start();self._poll()

    def _diagnostic_finished(self):
        worker=self.aux_worker;self.aux_worker=None
        error=worker.error or ''
        if self._aux_config and self._aux_config.api_key:error=error.replace(self._aux_config.api_key,'[REDACTED]')
        if worker.cancelled:self.task_text.setText(tr('production.cancelled'))
        elif error:
            self.record_diagnostic(dict(操作=self.aux_kind,原因=error))
            self.task_text.setText('检查失败，可查看诊断')
        elif self.aux_kind=='launch':
            self.record_diagnostic(worker.value);self.task_text.setText(tr('production.finished',task=tr('launch_test_title')))
        elif self.aux_kind=='models':
            self.preferences._models_discovered(self._aux_config,worker.value,'');self.task_text.setText(tr('production.models_done'))
        else:
            result=worker.value
            self.record_diagnostic(dict(provider=self._aux_config.provider,model=result.model,elapsed_ms=result.elapsed_ms))
            self.task_text.setText(tr('production.connection_done'))
        worker.deleteLater();self._poll()

    def render_cards(self):
        if self.settings.video_workflow!='materials':raise ValueError('请先在设置中选择画面素材')
        self.sync_current_cards()
        if not self.cards.data:raise ValueError('请先在资料或稿件页选择本期内容')
        self.cards.output_root=Path(self.preferences.output_edit.text())/'video-cards'
        self.cards.output_edit.setText(str(self.cards.output_root));self.cards._start(intro=self.prepared_intro())

    def prepared_intro(self):
        if not self.include_intro.isChecked():return None
        path=self.intro.result_path
        if not path or not Path(path).is_file():raise ValueError('请先在“制作片头”页生成片头')
        return path

    def roundup_video_items(self):
        from services.roundup_style import payload_display
        from utils.time import format_datetime
        from utils.launch_status import launch_status_text
        selected={s['id'] for s in self.roundup.draft['selection'] if s['included']}
        items=[]
        for item in self.roundup.draft['snapshot']['items']:
            if item['ll2_id'] not in selected:continue
            names=roundup.display_names(item);name=names['name']
            items.append(dict(id='ll2:'+item['ll2_id'],title=names['rocket'],summary=name,source='Launch Library 2',
                published=item['net'],url=item.get('record_url') or item.get('ll2_source',{}).get('url') or 'https://ll.thespacedevs.com/2.3.0/launches/'+item['ll2_id']+'/',
                categories=[],video_type='launch',launch_context=item,
                launch=dict(orbit=names['orbit'],payload=payload_display(item) if item.get('payload') else '',site=names['site'],
                    time=format_datetime(item['net'],self.roundup.draft.get('timezone','Asia/Shanghai')),
                    status=launch_status_text(dict(id=item.get('status_id'),name=item.get('status_name'))),recovery=item.get('recovery',''))))
        return items

    def cards_from_script(self):
        if self.cards.worker is not None:raise ValueError(tr('production.conflict'))
        if self.cards.source:
            if QMessageBox.question(self,'使用当前期次','将切换回本期内容，当前快照编辑会先备份到期次历史。继续吗？')!=QMessageBox.StandardButton.Yes:return
            self.checkpoint()
            self.cards.source=None;self._cards_base=None
        self.sync_current_cards()
        self.sync_download_tasks()

    def sync_current_cards(self):
        cards=self.cards
        if cards.worker or cards.source:return
        from tools import video_cards
        from exporters.premiere import build_snapshot
        from services.weekly_report import _export_context
        job='edition:'+self.export_scope()
        if self.mode=='roundup':
            data=dict(protocol_version='1.0',job_id=job,report_title=self.dashboard.report_title_line.text() or 'SpaceWeekly',
                      language=self.settings.language,items=self.roundup_video_items())
        else:
            bundle=self.news.bundle or {}
            context=bundle.get('context') or _export_context(self.settings,{})
            context=replace(context,report_title=self.dashboard.report_title_line.text().strip() or 'SpaceWeekly')
            articles=[a for a in bundle.get('articles',[]) if a.news.url in self.news.script_included]
            data=build_snapshot(articles,bundle.get('summaries',{}),context,self.settings.language,
                                launches=bundle.get('launch_video_items',[]),job_id=job)
            data['items']+=data.pop('launches',[])
        if data['items']:data=video_cards.validate_snapshot(data)
        if data==self._cards_base and bool(cards.data)==bool(data['items']):return
        cards._store_editor()
        same_edition=self._cards_base and self._cards_base['job_id']==job
        previous={item['id']:item for item in (cards.data or {}).get('items',[])} if same_edition else {}
        previous_base={item['id']:item for item in (self._cards_base or {}).get('items',[])}
        selected=cards.selected_ids();row=cards.items.currentItem();frame=cards.slider.value()
        current=row.data(Qt.ItemDataRole.UserRole) if row and same_edition else None
        self._cards_base=deepcopy(data)
        for item in data['items']:
            old=previous.get(item['id'])
            base=previous_base.get(item['id'])
            if old and base:
                for key in ('title','summary'):
                    if old[key]!=base[key]:
                        item[key]=old[key]
                        item[key+'_origin']='user_edit'
                if old.get('video_type','news')!=base.get('video_type','news'):
                    item['video_type']=old.get('video_type','news')
                    if item['video_type']=='launch':item['launch']=deepcopy(old['launch'])
                for key,value in old.get('launch',{}).items():
                    baseline=base.get('launch',{}).get(key,100 if key=='background_opacity' else '')
                    if key=='time':baseline=video_cards.launch_time_text(baseline)
                    if value!=baseline:item.setdefault('launch',{})[key]=value
        data['items']+= [item for identifier,item in previous.items() if identifier.startswith('launch-manual:')]
        selected.update(item['id'] for item in data['items'] if item['id'] not in previous)
        cards._set_data(data,None,selected,current,sort_order=None if same_edition else 'input')
        if current:cards.slider.setValue(frame)

    def sync_download_tasks(self):
        if self.downloads.worker is not None:return
        if self.cards.data and (self.cards.source or self.cards.data['job_id']=='edition:'+self.export_scope()):
            items=list(self.cards.data['items'])
        elif self.mode=='roundup':items=self.roundup_video_items()
        else:items=(self.news.bundle or {}).get('launch_video_items',[])
        self.downloads.snapshot_items=items;self.downloads._populate_tasks()

    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,tr('production.choose_output'),self.preferences.output_edit.text())
        if path:self.preferences.output_edit.setText(path);self._changed()

    def open_output(self):
        path=Path(self.preferences.output_edit.text());path.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def apply_rules(self):
        if self.busy():raise ValueError(tr('production.conflict'))
        value=self.rules.settings();fields={k:getattr(value,k) for k in ('prompt_preset','summary_prompt','filter_prompt','title_prompt','roundup_prompt')}
        self.preferences.original_settings=replace(self.preferences.original_settings,**fields);self.apply_preferences()
        self._saved_rules=self.rules_signature()

    def apply_preferences(self):
        if self.busy():raise ValueError(tr('production.conflict'))
        self.settings=self.dashboard.settings_snapshot(replace(self.preferences.settings(),video_workflow=self.video_workflow.currentData()))
        save_settings(self.settings);apply_settings(self.settings);set_language(self.settings.language)
        self.theme.apply(self.settings.theme_mode);self.log.apply_theme(self.theme);self.roundup.settings=self.settings
        self._saved_preferences=self.preference_signature()
        self.settings_applied.emit();self._mode();self._header();self.task_text.setText(tr('production.settings_saved'))

    def _daily(self):
        s=self.current_settings()
        return {k:getattr(s,k) for k in ('report_title','rss_limit','news_dedup_enabled','news_event_date_check_enabled',
         'news_filter_level','date_mode','start_date','end_date','launch_range','launch_start_date','launch_end_date',
         'export_markdown','export_word','export_premiere','export_sqlite','output_dir')}

    def _remember_source_dates(self,*_):
        if self._restoring or not self.dashboard.date_ranges_valid():return
        settings=self.dashboard.settings_snapshot(self.settings)
        try:
            write_json(resource_path('config','source-date-ranges.json'),
                       {key:getattr(settings,key) for key in SOURCE_DATE_KEYS})
        except OSError as error:self.task_text.setText('无法保存来源日期选项：'+str(error))

    def _restore_source_dates(self):
        from datetime import date
        path=resource_path('config','source-date-ranges.json')
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            for index,choices in ((0,NEWS_DATE_RANGES),(3,LAUNCH_DATE_RANGES)):
                mode,start,end=(value[key] for key in SOURCE_DATE_KEYS[index:index+3])
                if mode not in {'custom',*(choice for _,choice in choices)}:
                    raise ValueError('未知日期预设')
                first,last=date.fromisoformat(start),date.fromisoformat(end)
                if mode=='custom' and first>last:raise ValueError('日期范围无效')
            self.settings=replace(self.settings,**{key:value[key] for key in SOURCE_DATE_KEYS})
            self.dashboard.apply_settings(self.settings)
        except FileNotFoundError:pass
        except (OSError,ValueError,TypeError,KeyError) as error:self.task_text.setText('无法恢复来源日期选项：'+str(error))

    def _roundup_controls(self):
        r=self.roundup
        return dict(start=r.start.date().toString('yyyy-MM-dd'),end=r.end.date().toString('yyyy-MM-dd'),
         target=r.target.currentText(),python=r.python.text(),model=r.model.text(),speaker=r.speaker_id(),
         style=r.style.text(),device=r.device.currentText(),timeout=r.timeout.value(),pause=r.pause.value(),pronunciations=r.pronunciations.toPlainText())

    def content_state(self):
        self.cards._store_editor()
        return dict(version=1,daily=self._daily(),roundup=self.roundup.draft,roundup_controls=self._roundup_controls(),news=self.news.snapshot(),material_edits=self.material_edits,
         cards=dict(data=self.cards.data,base=self._cards_base,source=str(self.cards.source) if self.cards.source else None,
          selected=sorted(self.cards.selected_ids()) if self.cards.data else [],seconds=self.cards.seconds.value(),encoding=self.cards.encoding.currentData(),
          sort_order=self.cards.sort_order.currentData()),
         intro=self.intro.options(),intro_file=str(self.intro.result_path) if self.intro.result_path else None,
         results=self.results,result_scopes=self.result_scopes)

    def snapshot(self):
        value=deepcopy(self.content_state())
        value['view']=dict(page=self.navigation.currentRow() if self.navigation.currentRow()>=0 else self._last_page,mode=self.mode,materials=self.materials.currentIndex(),media=self.media.currentIndex(),
         roundup_segment=self.roundup.segment_list.currentRow(),news_source=self.news.sources.currentRow(),news_article=self.news.articles.currentRow(),
         cards_row=self.cards.items.currentRow(),cards_frame=self.cards.slider.value(),
         spoken_scroll=self.roundup.spoken.verticalScrollBar().value(),screen_scroll=self.roundup.screen.verticalScrollBar().value(),
         narration_scroll=self.roundup.narration_edit.verticalScrollBar().value())
        article=self.news.current_article()
        if article:
            from models.news import news_id
            value['view']['news_article_id']=news_id(article.news.url)
        if self._detail_id:value['view']['launch_detail_id']=self._detail_id
        return value

    def _restore(self,path=None):
        manual=resource_path('config','production-session.json');recovery=resource_path('cache','production-recovery.json')
        paths=[p for p in (manual,recovery) if p.exists()]
        if not paths and path is None:return
        path=path or max(paths,key=lambda p:p.stat().st_mtime_ns)
        try:
            value=validate_session(json.loads(path.read_text(encoding='utf-8')))
            daily=value.get('daily',{});self.settings=replace(self.settings,**{k:v for k,v in daily.items() if k in self._daily()})
            self.material_edits=value.get('material_edits',{})
            self.dashboard.apply_settings(self.settings);self.preferences.output_edit.setText(self.settings.output_dir)
            for key,widget in (('export_markdown',self.preferences.markdown_check),('export_word',self.preferences.word_check),('export_premiere',self.preferences.premiere_check),('export_sqlite',self.preferences.sqlite_check)):
                widget.setChecked(getattr(self.settings,key))
            self.roundup.draft=value['roundup'];self.roundup._show_draft();self.roundup.settings=self.settings
            card=value.get('cards',{})
            self._cards_base=card.get('base')
            self.cards.manifest=self.cards.manifest_scope=None
            if card.get('data'):
                self.cards._set_data(card['data'],card.get('source'),selected_ids=set(card.get('selected',[])),sort_order=card.get('sort_order','asc'))
                self.cards.seconds.setValue(card.get('seconds',12));self.cards.encoding.setCurrentIndex(max(0,self.cards.encoding.findData(card.get('encoding'))))
            else:self.cards._set_data(dict(items=[]),None)
            intro=value.get('intro',{})
            self.intro.result_path=Path(value['intro_file']) if value.get('intro_file') else None
            self.intro.open.setEnabled(bool(self.intro.result_path and self.intro.result_path.is_file()))
            self.intro.title.setText(intro.get('title',self.intro.title.text()));self.intro.subtitle.setText(intro.get('subtitle',self.intro.subtitle.text()));self.intro.logo.setText(intro.get('logo_path') or '')
            from PySide6.QtCore import QDate
            for control,key in ((self.intro.date,'date_text'),(self.intro.end_date,'end_date')):
                date=QDate.fromString(intro.get(key) or intro.get('end_date_text',''),'yyyy-MM-dd')
                if date.isValid():control.setDate(date)
            self.intro.logo_motion.setCurrentIndex(max(0,self.intro.logo_motion.findData(intro.get('logo_motion'))))
            staged=value.get('roundup_controls',{});r=self.roundup;r._loading=True
            for key,control in (('start',r.start),('end',r.end)):
                date=QDate.fromString(staged.get(key,''),'yyyy-MM-dd')
                if date.isValid():control.setDate(date)
            for key,control in (('python',r.python),('model',r.model),('style',r.style)):
                if key in staged:control.setText(staged[key])
            for key,control in (('target',r.target),('device',r.device)):
                if key in staged:
                    if control.findText(staged[key])<0:control.addItem(staged[key])
                    control.setCurrentText(staged[key])
            if 'speaker' in staged:r.refresh_speakers(staged['speaker'])
            if 'timeout' in staged:r.timeout.setValue(staged['timeout'])
            if 'pause' in staged:r.pause.setValue(staged['pause'])
            if 'pronunciations' in staged:r.pronunciations.setPlainText(staged['pronunciations'])
            r._loading=False;r._voice_dirty=True
            self.news.restore(value.get('news',{}))
            self.results=value.get('results',[]);self.result_scopes=value.get('result_scopes',{})
            self.intro.result_scope=self.result_scopes.get(str(self.intro.result_path))
            view=value.get('view',{});self.materials.setCurrentIndex(view.get('materials',0))
            self.content.setCurrentIndex(1 if view.get('mode')=='roundup' else 0)
            self.navigation.setCurrentRow(max(0,min(3,view.get('page',0))));self.media.setCurrentIndex(view.get('media',0))
            r.segment_list.setCurrentRow(view.get('roundup_segment',0));self.news.sources.setCurrentRow(view.get('news_source',0))
            if not self.news.select_article(view.get('news_article_id')):self.news.articles.setCurrentRow(view.get('news_article',0))
            for index,row in enumerate(r.draft['selection']):
                if row['id']==view.get('launch_detail_id'):r.tasks.setCurrentCell(index,2);break
            if 'result_scopes' not in value:
                self.result_scopes={path:self.export_scope('roundup' if Path(path).name.startswith('SpaceWeekly_roundup_')
                    else 'news' if Path(path).name.startswith('SpaceWeekly_news_voice_') else self.mode) for path in self.results}
            self._refresh_files()
            for key,control in (('spoken_scroll',r.spoken),('screen_scroll',r.screen),('narration_scroll',r.narration_edit)):
                control.verticalScrollBar().setValue(view.get(key,0))
            if self.cards.data:self.cards.items.setCurrentRow(view.get('cards_row',0));self.cards.slider.setValue(view.get('cards_frame',0))
            if manual.exists():
                saved=json.loads(manual.read_text(encoding='utf-8'));saved.pop('view',None);self._saved_hash=roundup.digest(saved)
            else:self._saved_hash='no-manual-save'
            self.task_text.setText(tr('production.restored'))
        except (ValueError,TypeError,KeyError,OSError,AttributeError,IndexError) as error:
            self._recovery_blocked=True;self.task_text.setText(tr('production.restore_failed',error=str(error)))

    def save(self):
        self.roundup._store_segment();value=self.snapshot();path=resource_path('config','production-session.json')
        if self._recovery_blocked and path.exists():
            from uuid import uuid4
            backup=path.with_name(path.stem+'.unreadable-'+uuid4().hex[:8]+'.json');backup.write_bytes(path.read_bytes())
        write_json(path,value);write_json(resource_path('cache','production-recovery.json'),value)
        self._saved_hash=roundup.digest(self.content_state());self._recovery_blocked=False
        if not self.busy() and self.dashboard.date_ranges_valid():self._save_runtime()
        self._header()

    def preference_signature(self):
        return roundup.digest([self.video_workflow.currentData(),{k:v for k,v in vars(self.preferences.settings()).items() if k not in self._daily()}])

    def rules_signature(self):
        value=self.rules.settings()
        return roundup.digest({key:getattr(value,key) for key in ('summary_prompt','filter_prompt','title_prompt','roundup_prompt','prompt_preset')})

    def checkpoint(self):
        from uuid import uuid4
        from datetime import datetime
        path=resource_path('output','production-versions',datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.json')
        write_json(path,self.snapshot())

    def open_edition(self):
        if self.busy():raise ValueError(tr('production.conflict'))
        path,_=QFileDialog.getOpenFileName(self,tr('production.open_edition'),str(resource_path('output','production-versions')),'JSON (*.json)')
        if path:
            if roundup.digest(self.content_state())!=self._saved_hash:
                if QMessageBox.question(self,tr('production.unsaved'),tr('production.open_version_question'))!=QMessageBox.StandardButton.Yes:return
                self.checkpoint()
            self._restoring=True
            try:self._restore(Path(path))
            finally:self._restoring=False
            self._remember_source_dates();self._mode();self._header()

    def _changed(self,*_):
        if not self._restoring:self._header()

    def _header(self,*,check_saved=True):
        if not hasattr(self,'period'):return
        s=self.current_settings();news=s.start_date+' - '+s.end_date if s.date_mode=='custom' else self.dashboard.date_mode_combo.currentText()
        r=self.roundup.draft
        self.edition_button.setText(self.dashboard.report_title_line.text() or '点击填写期次名称')
        self.edition_button.setToolTip('点击修改期次名称\n'+self.workspace_label.toolTip())
        self.period.setText((r['start']+' — '+r['end']+' · '+roundup.timezone_label(r)) if self.mode=='roundup' else news)
        if check_saved and self._saved_hash:self.saved.setText(tr('production.saved' if roundup.digest(self.content_state())==self._saved_hash else 'production.unsaved'))

    def _edit_title(self):
        self.edition_button.hide();self.dashboard.report_title_line.show();self.dashboard.report_title_line.setFocus();self.dashboard.report_title_line.selectAll()

    def _finish_title(self):
        self.dashboard.report_title_line.hide();self.edition_button.show();self._header()

    def export_scope(self,mode=None):
        mode=mode or self.mode
        if mode=='roundup':
            draft=self.roundup.draft
            basis=[draft['start'],draft['end'],draft.get('timezone')]
        else:
            basis=(self.news.bundle or {}).get('source_scope') or {
                key:getattr(self.current_settings(),key) for key in SOURCE_DATE_KEYS}
        return mode+':'+roundup.digest(basis)

    def add_result(self,path,scope):
        path=str(Path(path).resolve())
        if path not in self.results:self.results.append(path)
        self.result_scopes[path]=scope

    def _refresh_files(self):
        from datetime import datetime
        self.output_files.clear()
        scope=self.export_scope()
        current={Path(path).resolve() for path in self.results if self.result_scopes.get(path)==scope}
        if self.mode=='roundup' and self.settings.video_workflow=='complete':
            current={path for path in current if path.suffix.lower()=='.mp4' and not path.name.lower().endswith('.intro.mp4')}
        history=self.history_toggle.isChecked()
        self.result_title.setText('历史成果' if history else '本期成果')
        self.history_toggle.setText('返回本期成果' if history else '查看历史成果')
        paths=current
        directory=self.preferences.output_edit.text().strip()
        if history:
            paths={Path(path).resolve() for path in self.results}-current
            if directory:
                root=Path(directory)
                existing=[p for p in root.glob('SpaceWeekly_*') if p.is_dir() or p.suffix in ('.json','.md','.docx','.wav','.srt','.mov','.mp4')]
                existing+=list((root/'video-cards').glob('*/manifest.json'))+list((root/'video-cards').glob('*.intro.mp4'))
                paths.update(p.resolve() for p in existing if p.resolve() not in current)
        dated=[]
        for path in paths:
            try:dated.append((path.stat().st_mtime,path))
            except OSError:continue
        for modified,p in sorted(dated,reverse=True):
            stamp=datetime.fromtimestamp(modified).strftime('%m-%d %H:%M')
            label=(('新闻配音素材包' if p.name.startswith('SpaceWeekly_news_voice_') else '发射综述素材包')
                   if p.is_dir() else '画面素材清单' if p.name=='manifest.json' else p.name)+' · '+stamp
            manifest=p/'manifest.json' if p.is_dir() else p
            if manifest.name=='manifest.json' and manifest.is_file():
                try:
                    if json.loads(manifest.read_text(encoding='utf-8')).get('state')=='incomplete':label=tr('production.incomplete')+' '+label
                except (OSError,ValueError):pass
            self.output_files.addItem(label);cell=self.output_files.item(self.output_files.count()-1)
            cell.setData(Qt.ItemDataRole.UserRole,str(p));cell.setToolTip(str(p))
        self.output_hint.setText(('暂无历史成果。' if history else '本期尚未导出文件。') if not dated else '')
        self.output_hint.setVisible(not dated)
        intro=self.intro.result_path
        self.include_intro.setEnabled(bool(intro and intro.is_file()))
        self.include_intro.setToolTip(intro.name if intro else '在制作片头页生成后可选')

    def task_rows(self):
        rows=[]
        if self.updates.worker:rows.append(('update','软件更新',self.updates.status.text(),self.updates.cancel_work))
        if self.image_worker:rows.append(('news_image','新闻配图','正在下载或导入图片',self.image_worker.token.cancel))
        if self.maintenance.worker:rows.append(('maintenance','缓存维护',self.maintenance.status.text(),self.maintenance.cancel))
        if self.voice_setup.worker:
            rows.append(('voice_setup','语音配置',self.voice_setup.status.text(),self.voice_setup.cancel_work))
        if self.aux_worker:rows.append(('diagnostic',tr('production.diagnostics'),tr('production.working',task=self.aux_kind),self.aux_worker.token.cancel))
        if self.worker:
            label={'voice_propose':'生成新闻口播','voice_audio':'新闻配音与对齐','voice_align':'新闻对齐',
                'collect':'采集新闻','write':'生成新闻稿','narration_retry':'重试失败口播',
                'export':'导出新闻','complete_video':'导出带配音的 MP4','import':'备份与导入旧工作区'}.get(self.worker_kind,'处理新闻')
            rows.append(('news:'+self.worker_kind,tr('production.news'),label,self.worker.token.cancel))
        for key,component in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads)):
            worker=getattr(component,'worker',None) or getattr(component,'preview_worker',None)
            if worker is not None:
                name=tr('production.'+key) if key in ('roundup','cards') else tr('video.intro') if key=='intro' else tr('video_download.title')
                rows.append((key,name,component.status.text(),component._cancel))
        return rows

    def busy(self):return bool(self.task_rows())

    def cancel_all(self):
        for _key,_name,_text,cancel in self.task_rows():cancel()
        self.task_text.setText(tr('production.cancelling'))

    def _poll(self):
        if self._restoring:return
        paths=[resource_path('translator',name) for name in ('dictionary.json','launch_research.json')]
        paths.append(resource_path('config','aerospace_terms.json'))
        signature=tuple((str(p),p.stat().st_mtime_ns if p.exists() else None) for p in paths)
        if getattr(self,'_terms_signature',signature)!=signature and self.roundup.worker is None:
            self.guard(self.refresh_names)
        if self.roundup.worker is None:self._terms_signature=signature
        rows=self.task_rows();self.cancel_tasks.setEnabled(bool(rows))
        self.task_progress.setVisible(bool(rows))
        self.task_progress.setRange(0,0)
        if rows:self.task_text.setText(' | '.join(name+': '+text for _,name,text,_ in rows))
        for key,component in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads)):
            text=component.status.text()
            if self._last_statuses.get(key)!=text:
                self._last_statuses[key]=text
                self.log.append_html(escape(key+': '+text))
                page=self.navigation.currentRow()
                if not rows and ((key=='roundup' and self.mode=='roundup' and page in (0,1,2,3))
                    or page==2 and self.media.currentIndex()=={'cards':1,'intro':2,'downloads':3}.get(key)):
                    self.task_text.setText(text)
        self.apply_button.setEnabled(not rows)
        voice_idle=self.worker is None and self.roundup.worker is None and self.voice_setup.worker is None
        self.voice_settings.setEnabled(voice_idle);self.roundup.speaker.setEnabled(voice_idle)
        has_text=self.mode!='news' or bool(self.voice_text.toPlainText().strip())
        self.listen_button.setEnabled(voice_idle and has_text);self.speech_button.setEnabled(voice_idle and has_text)
        self.voice_text.setReadOnly(not voice_idle)
        self.sync_button.setEnabled(voice_idle)
        self.confirm_name.setEnabled(self.roundup.worker is None and self.roundup.tasks.currentRow()>=0)
        self.export_news_button.setEnabled(self.worker is None)
        self.complete_export_button.setEnabled(not rows)
        self.cards_export_button.setEnabled(self.cards.worker is None)
        self.news.sources.setEnabled(True);self.dashboard.setEnabled(self.worker is None)
        value=self.content_state();key=roundup.digest(value)
        if key!=self._observed_hash:
            self._observed_hash=key
            if not self._recovery_blocked and not self._discard:
                try:write_json(resource_path('cache','production-recovery.json'),self.snapshot())
                except OSError as error:
                    self._recovery_blocked=True;self.task_text.setText(tr('production.failed',error=str(error)))
            self._voice_refresh();self._header()
        path=getattr(self.roundup,'last_export_path',None)
        if path and Path(path).exists() and str(Path(path).resolve()) not in self.results:
            self.add_result(path,self.export_scope('roundup'));self._refresh_files()
        for path,scope in ((self.cards.manifest,self.cards.manifest_scope),(self.intro.result_path,self.intro.result_scope)):
            if path and str(Path(path).resolve()) not in self.results:
                self.add_result(path,scope or self.export_scope())
                self._refresh_files()
        if self._pending_leave and not self.busy() and not self.roundup.tts_provider.is_running:
            target=self._pending_leave;self._pending_leave=None
            if not self._discard:
                if self._pending_rules:self.apply_rules()
                elif self._pending_preferences:self.apply_preferences()
                self.save()
            if target=='update':self.update_ready.emit()
            elif target=='uninstall':self.uninstall_ready.emit()
            elif target=='close':self.close_ready.emit()
            else:self.workspace_requested.emit(target)

    def choose_workspace(self):
        path=QFileDialog.getExistingDirectory(self,tr('production.workspace'),str(workspace_path() or Path.home()))
        if path:self.request_leave(path)

    def request_leave(self,target='close',*,cancel_running=False):
        if self._pending_leave:return False
        self._pending_preferences=self.preference_signature()!=self._saved_preferences
        self._pending_rules=self.rules_signature()!=self._saved_rules
        if roundup.digest(self.content_state())!=self._saved_hash or self._pending_preferences or self._pending_rules:
            answer=QMessageBox.question(self,tr('production.unsaved'),tr('production.leave_question'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
            if answer==QMessageBox.StandardButton.Cancel:return False
            self._discard=answer==QMessageBox.StandardButton.Discard
            if not self._discard:self.save()
        if self.busy() and not cancel_running and QMessageBox.question(self,tr('production.tasks'),tr('production.leave_tasks'))!=QMessageBox.StandardButton.Yes:
            self._discard=False;return False
        if self._discard:
            recovery=resource_path('cache','production-recovery.json');manual=resource_path('config','production-session.json')
            if manual.exists():write_json(recovery,json.loads(manual.read_text(encoding='utf-8')))
            else:recovery.unlink(missing_ok=True)
        self._pending_leave=target;self.cancel_all()
        self._release_preview()
        self.preferences._model_timer.stop()
        self.roundup.reject()
        self.cards._stop_preview();self.cards._refresh_timer.stop();self.cards._cancel()
        self.intro.refresh.stop();self.intro._stop_preview();self.intro._cancel()
        self.downloads._save_preferences();self.downloads._restore_pending=False;self.downloads._abort_thumbnails();self.downloads._cancel()
        self._poll()
        return False

    def retranslate(self):
        from ui.localize import retranslate,chrome
        retranslate(self)
        for listing in (self.navigation,self.preferences.nav,self.rules.type_list):
            for index in range(listing.count()):listing.item(index).setText(chrome(listing.item(index).text()))
        self._mode()
