"""Persistent production pages sharing the existing service/controller state."""
from copy import deepcopy
from dataclasses import replace
from html import escape
import json
from pathlib import Path

from PySide6.QtCore import Qt,QTimer,Signal,QUrl,QSize
from PySide6.QtGui import QDesktopServices,QPixmap,QImage
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,
 QStackedWidget,QTabWidget,QSplitter,QPlainTextEdit,QTextBrowser,QLineEdit,QComboBox,
 QScrollArea,QFormLayout,QFileDialog,QMessageBox,QDialogButtonBox,QMenu,QProgressBar)
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
from ui.component import AspectPreview


class TaskSummary(QLabel):
    def setText(self,text):
        self.setToolTip(text)
        compact=text.splitlines()[0] if text else ''
        super().setText(compact if len(compact)<=150 else compact[:147]+'…')


class ProductionWorkspace(QWidget):
    close_ready=Signal()
    settings_applied=Signal()
    workspace_requested=Signal(str)

    def __init__(self,settings,feeds,theme,parent=None):
        super().__init__(parent)
        self.settings,self.theme=settings,theme
        self.worker=None;self.worker_kind='';self.mode='news'
        self.aux_worker=None;self.aux_kind='';self._aux_config=None
        self._restoring=True;self._saved_hash='';self._observed_hash=''
        self._pending_leave=None;self._discard=False;self._recovery_blocked=False
        self.results=[]
        self._last_page=0
        self.material_edits={};self._detail_id=None;self._detail_loading=False
        self.feed_panel=FeedPanel(feeds);self.dashboard=DashboardPanel(settings)
        self.news=NewsWorkspace(self);self.roundup=LaunchRoundupDialog(settings,self)
        self.roundup.workflow_embedded=True
        self.cards=VideoCardsDialog(settings.output_dir)
        self.intro=VideoIntroDialog(Path(settings.output_dir)/'video-cards')
        self.downloads=VideoDownloadDialog(workspace_path() or Path(settings.output_dir).parent,[])
        self.preferences=SettingsDialog(settings);self.preferences._model_timer.stop()
        self.rules=PromptStudioDialog(settings)
        for component in (self.cards,self.intro,self.downloads,self.preferences,self.rules):component.workflow_embedded=True
        self._build();self._connect();self._restore()
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
        outer=QVBoxLayout(self);outer.setContentsMargins(14,12,14,10);outer.setSpacing(10)
        header=QHBoxLayout()
        self.workspace_label=QLabel(str(workspace_path() or Path(self.settings.output_dir).parent))
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.workspace_label.setWordWrap(True);header.addWidget(self.workspace_label,1)
        self.button('production.workspace',self.choose_workspace,header)
        self.save_button=self.button('production.save',self.save,header,True)
        self.button('settings',self.show_settings,header)
        more=QPushButton(tr('production.more'));menu=QMenu(more)
        menu.addAction(tr('open.output'),self.open_output)
        menu.addAction(tr('production.open_edition'),lambda:self.guard(self.open_edition))
        menu.addAction(tr('production.diagnostics'),lambda:self.inspect(dict(version='3.1.0demo',tasks=[r[:3] for r in self.task_rows()])))
        menu.addAction(tr('launch_test_title'),lambda:self.guard(lambda:self.start_diagnostic('launch')))
        menu.addAction(tr('about'),lambda:self.inspect(dict(application='SpaceWeekly',version='3.1.0demo',repository='https://github.com/Starship30/SpaceWeekly')))
        more.setMenu(menu);header.addWidget(more);outer.addLayout(header)
        title_row=QHBoxLayout();title_row.addWidget(QLabel(tr('production.edition_title')))
        self._detach_widget(self.dashboard,self.dashboard.report_title_line)
        title_row.addWidget(self.dashboard.report_title_line,1)
        self.content=QComboBox();self.content.addItem(tr('production.news'),'news');self.content.addItem(tr('production.roundup'),'roundup')
        title_row.addWidget(self.content);self.saved=QLabel();title_row.addWidget(self.saved);outer.addLayout(title_row)
        self.period=QLabel();self.period.setWordWrap(True);outer.addWidget(self.period)
        body=QHBoxLayout();self.navigation=QListWidget();self.navigation.setObjectName('SettingsNav');self.navigation.setFixedWidth(160);self.navigation.setSpacing(8)
        for key in ('materials','script','media','export'):
            self.navigation.addItem(tr('production.'+key));self.navigation.item(self.navigation.count()-1).setSizeHint(QSize(120,52))
        body.addWidget(self.navigation);self.pages=QStackedWidget();body.addWidget(self.pages,1);outer.addLayout(body,1)
        self.materials=QTabWidget();self.materials.addTab(self.news.data_page,tr('production.news'))
        self._launch_materials();self.materials.addTab(self.scroll(self.roundup.data_page),tr('production.launch_materials'))
        self._wrap_actions(self.roundup.data_page,0,4)
        self._wrap_actions(self.roundup.script_page,3,3)
        self._wrap_actions(self.roundup.script_page,0,4)
        self._compact_roundup_editor()
        setup=QSplitter();self.feed_panel.setMaximumWidth(16777215);setup.addWidget(self.feed_panel);setup.addWidget(self.dashboard);setup.setStretchFactor(1,1)
        self.dashboard.start_button.setText(tr('production.collect_news'))
        self.materials.addTab(setup,tr('production.sources_dates'));self.pages.addWidget(self.materials)
        self.scripts=QStackedWidget();self.scripts.addWidget(self.news.script_page);self.scripts.addWidget(self.scroll(self.roundup.script_page));self.pages.addWidget(self.scripts)
        self.media=QTabWidget();self._voice_page();self._cards_page()
        self.intro.setMinimumSize(0,0);self.intro.preview.setMinimumSize(320,180)
        self.media.addTab(self.scroll(self.intro),tr('video.intro'));self.media.addTab(self.scroll(self.downloads),tr('video_download.title'));self.pages.addWidget(self.media)
        self._export_page();self._settings_page()
        self.roundup.hide();self.roundup.tabs.hide()
        self.cards.open_intro=lambda:self.media_route(2);self.cards.open_download=lambda:self.media_route(3)
        self.roundup.inspect_data=self.inspect;self.roundup.open_rules=self.open_rules
        self.roundup.export_directory=lambda:self.preferences.output_edit.text().strip()
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
        strip=QHBoxLayout();self.task_text=TaskSummary(tr('ready'));self.task_text.setWordWrap(True);strip.addWidget(self.task_text,1)
        self.task_text.setMaximumHeight(65)
        self.task_progress=QProgressBar();self.task_progress.setMaximumWidth(150);self.task_progress.hide();strip.addWidget(self.task_progress)
        self.cancel_tasks=self.button('production.cancel_tasks',self.cancel_all,strip)
        self.log_toggle=QPushButton(tr('production.show_log'));self.log_toggle.setCheckable(True);strip.addWidget(self.log_toggle);outer.addLayout(strip)
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
        r=self.roundup;self.launch_details=QTextBrowser();self.launch_details.setOpenExternalLinks(True)
        detail=QWidget();layout=QVBoxLayout(detail);layout.addWidget(self.launch_details,1)
        self.chinese_name=QLineEdit();self.chinese_name.setPlaceholderText(tr('production.confirmed_name'))
        self.manual_summary=QPlainTextEdit();self.manual_summary.setPlaceholderText(tr('production.manual_summary'));self.manual_summary.setMaximumHeight(75)
        layout.addWidget(self.chinese_name);layout.addWidget(self.manual_summary)
        row=QHBoxLayout();self.confirm_name=self.button('production.confirm_name',self.confirm_launch,row)
        self.button('production.show_evidence',r._task_sources,row);layout.addLayout(row)
        split=QSplitter();r.data_page.layout().removeWidget(r.tasks);split.addWidget(r.tasks);split.addWidget(detail);split.setStretchFactor(1,1)
        r.data_page.layout().insertWidget(2,split,1)
        for column in (5,6,7,8,9,10,11):r.tasks.setColumnHidden(column,True)
        r.tasks.setColumnWidth(2,250);r.tasks.currentCellChanged.connect(lambda *_:self._launch_detail())
        self.chinese_name.textChanged.connect(self._stage_name);self.manual_summary.textChanged.connect(self._stage_name)

    def _launch_detail(self):
        row=self.roundup.tasks.currentRow()
        if row<0 or row>=len(self.roundup.draft['selection']):
            self._detail_id=None;self.launch_details.setPlainText(tr('production.launch_empty'));self.confirm_name.setEnabled(False);return
        identifier=self.roundup.draft['selection'][row]['id'];item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
        state=roundup.material_status(item)
        self._detail_id=identifier
        lines=['<h3>'+escape(item['english_name'])+'</h3>',escape(item.get('net',''))+' '+escape(item.get('rocket','')),
         '<p>'+escape(item.get('site_name') or item.get('site',''))+'</p>',
         '<p>'+escape(' / '.join(state[k] for k in ('preparation_label','source_label','independent_label','human_label')))+'</p>',
         '<p>'+escape(item.get('description',''))+'</p>']
        for source in roundup.background_facts(item):
            url=source['url'];lines.append('<p><a href="'+escape(url,quote=True)+'">'+escape(source.get('title') or url)+'</a></p><blockquote>'+escape(source['quote'])+'</blockquote>')
        if state['reason']:lines.append('<p>'+escape(state['reason'])+'</p>')
        self.launch_details.setHtml(''.join(lines))
        values=roundup.confirmed_values(item.get('research',{}),item)
        values=self.material_edits.get(identifier,values);self._detail_loading=True
        self.chinese_name.setText(values.get('chinese_name',''));self.manual_summary.setPlainText(values.get('summary',''))
        self._detail_loading=False
        self.confirm_name.setEnabled(self.roundup.worker is None)

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
        self.material_edits.pop(self.roundup.draft['selection'][row]['id'],None)
        self.roundup._sync_materials();self._launch_detail()

    def _voice_page(self):
        r=self.roundup;page=QWidget();layout=QVBoxLayout(page)
        self.voice_segments=QComboBox();self.voice_segments.currentIndexChanged.connect(self._voice_selected)
        row=QHBoxLayout();row.addWidget(self.voice_segments,1)
        self.listen_button=self.button('production.listen',r._listen,row,True)
        self.speech_button=self.button('production.synthesize',r._audio_all,row)
        layout.addLayout(row)
        split=QSplitter();split.addWidget(self.scroll(r.voice_page));view=QWidget();right=QVBoxLayout(view)
        self.voice_text=QPlainTextEdit();self.voice_text.setReadOnly(True);right.addWidget(self.voice_text,1)
        self.frame=AspectPreview(tr('production.preview_hint'));self.frame.setAlignment(Qt.AlignmentFlag.AlignCenter);self.frame.setMinimumSize(240,135)
        right.addWidget(self.frame,1);self.button('production.preview',self.preview_roundup,right)
        split.addWidget(view);split.setStretchFactor(0,1);split.setStretchFactor(1,1);layout.addWidget(split,1)
        self.media.addTab(page,tr('production.roundup_voice'))

    def _compact_roundup_editor(self):
        r=self.roundup
        for label in r.script_page.findChildren(QLabel):label.setWordWrap(True)
        r.reading_preview.hide();r.reading_input_label.hide()
        details=QWidget();form=QFormLayout(details)
        self._move_form_control(r.script_page,r.names,form,tr('production.formal_names'))
        self._move_form_control(r.script_page,r.ids,form,tr('production.task_ids'))
        self._move_form_control(r.script_page,r.preview_seconds,r.voice_form,tr('production.preview_duration'))
        r.script_page.layout().addWidget(details);details.hide()
        toggle=QPushButton(tr('production.paragraph_details'));toggle.setCheckable(True);toggle.toggled.connect(details.setVisible)
        r.script_page.layout().addWidget(toggle)
        self._wrap_actions(r.voice_page,3,2)

    def _cards_page(self):
        cards=self.cards;cards.setMinimumSize(0,0);cards.preview.setMinimumSize(280,158)
        self.button('production.cards_from_script',self.cards_from_script,cards.inputs.layout(),True)
        tabs=QTabWidget();tabs.addTab(cards.editor_box,tr('production.edit'));tabs.addTab(cards.preview_box,tr('video.preview'))
        cards.editor_splitter.addWidget(tabs);cards.editor_splitter.setSizes([230,650])
        self.media.addTab(self.scroll(cards),tr('production.cards'))

    def _export_page(self):
        page=QWidget();layout=QVBoxLayout(page)
        self.export_hint=QLabel();self.export_hint.setWordWrap(True);layout.addWidget(self.export_hint)
        form=QFormLayout();layout.addLayout(form)
        self._move_form_control(self.preferences,self.preferences.output_edit,form,tr('video.output'))
        self.news_formats=QWidget();form=QFormLayout(self.news_formats);layout.addWidget(self.news_formats)
        for widget,label in ((self.preferences.markdown_check,'Markdown'),
         (self.preferences.word_check,'Word'),(self.preferences.premiere_check,'Premiere JSON'),(self.preferences.sqlite_check,'SQLite')):
            self._move_form_control(self.preferences,widget,form,label)
        self.button('production.choose_output',self.choose_output,layout)
        self.export_news_button=self.button('production.export_news',self.start_news_export,layout,True)
        layout.addWidget(self.roundup.export_controls)
        actions=QHBoxLayout();self.cards_export_button=self.button('production.render_cards',self.render_cards,actions)
        self.intro_export_button=self.button('video.intro_generate',self.intro._start,actions);self.button('production.open_output',self.open_output,actions);layout.addLayout(actions)
        self.output_files=QListWidget();self.output_files.itemDoubleClicked.connect(lambda item:QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))))
        layout.addWidget(self.output_files,1);self.pages.addWidget(page)

    def _settings_page(self):
        page=QWidget();layout=QVBoxLayout(page);self.settings_tabs=QTabWidget();layout.addWidget(self.settings_tabs,1)
        self.settings_tabs.addTab(self.scroll(self.preferences),tr('settings'));self.settings_tabs.addTab(self.scroll(self.rules),tr('prompt.studio'))
        self.voice_settings=QWidget();voice_form=QFormLayout(self.voice_settings)
        for control,label in ((self.roundup.python,tr('production.tts_python')),(self.roundup.model,tr('production.tts_model')),
         (self.roundup.device,tr('production.device')),(self.roundup.timeout,tr('production.timeout'))):
            self._move_form_control(self.roundup.voice_page,control,voice_form,label)
        self._move_form_control(self.preferences,self.preferences.display_timezone_combo,voice_form,tr('display.timezone'))
        self._move_form_control(self.preferences,self.preferences.aerospace_translation_check,voice_form,tr('production.translate_launches'))
        self._move_form_control(self.preferences,self.preferences.aerospace_translation_mode_combo,voice_form,tr('production.translation_mode'))
        self.settings_tabs.addTab(self.scroll(self.voice_settings),tr('production.voice_settings'))
        for index in (4,5):self.preferences.nav.item(index).setHidden(True)
        for component in (self.preferences,self.rules):
            component.setMinimumSize(0,0)
            for box in component.findChildren(QDialogButtonBox):box.hide()
        actions=QHBoxLayout();self.apply_button=self.button('production.apply_settings',self.apply_preferences,actions,True)
        self.button('production.apply_rules',self.apply_rules,actions);self.button('production.save_voice',self.roundup._save_voice,actions)
        layout.addLayout(actions);self.pages.addWidget(page)

    def _build_inspector(self,layout):
        self.inspector=QWidget();box=QVBoxLayout(self.inspector);row=QHBoxLayout()
        row.addWidget(QLabel(tr('production.evidence')),1);self.button('close',self.inspector.hide,row);box.addLayout(row)
        self.inspector_text=QPlainTextEdit();self.inspector_text.setReadOnly(True);box.addWidget(self.inspector_text)
        self.inspector.setMaximumHeight(230);self.inspector.hide();layout.addWidget(self.inspector)

    def _connect(self):
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex);self.content.currentIndexChanged.connect(self._mode)
        self.navigation.currentRowChanged.connect(self._navigated)
        self.materials.currentChanged.connect(self._materials_changed)
        self.news.collect_requested.connect(lambda:self.guard(self.start_news_collect));self.news.write_requested.connect(lambda:self.guard(self.start_news_write))
        self.dashboard.start_requested.connect(lambda:self.guard(self.start_news_collect));self.roundup.job_changed.connect(self._poll)
        self.roundup.draft_changed.connect(self._header)
        self.news.changed.connect(self._changed);self.dashboard.settings_changed.connect(self._changed);self.feed_panel.feeds_changed.connect(self._changed)
        self.preferences.output_edit.editingFinished.connect(self._changed)
        self.roundup.segment_list.currentRowChanged.connect(lambda *_:self._voice_refresh())
        self.roundup.job_changed.connect(lambda:self._launch_detail() if self.roundup.worker is None else None)
        self.preferences.accepted.connect(lambda:self.guard(self.apply_preferences));self.rules.accepted.connect(lambda:self.guard(self.apply_rules))
        self.media.currentChanged.connect(lambda index:self.sync_download_tasks() if index==3 else None)

    def _mode(self):
        self.mode=self.content.currentData();self.scripts.setCurrentIndex(1 if self.mode=='roundup' else 0)
        self.roundup.export_controls.setVisible(self.mode=='roundup');self.export_news_button.setVisible(self.mode=='news');self.news_formats.setVisible(self.mode=='news')
        self.export_hint.setText(tr('production.export_roundup_hint' if self.mode=='roundup' else 'production.export_news_hint'))
        self._voice_refresh();self._header()

    def _materials_changed(self,index):
        if index in (0,1):self.content.setCurrentIndex(index)

    def media_route(self,index):
        self.navigation.setCurrentRow(2);self.media.setCurrentIndex(index)
        if index==3:self.sync_download_tasks()

    def open_rules(self):
        self.show_settings();self.settings_tabs.setCurrentIndex(1)
        from services.prompt_manager import PROMPT_TYPES
        self.rules.type_list.setCurrentRow(PROMPT_TYPES.index('roundup') if self.mode=='roundup' else 0)

    def show_settings(self):
        self.navigation.blockSignals(True);self.navigation.setCurrentRow(-1);self.navigation.blockSignals(False)
        self.pages.setCurrentIndex(4)

    def _navigated(self,index):
        if index>=0:self._last_page=index
        if index==3:self._refresh_files()

    def inspect(self,value):
        self.inspector_text.setPlainText(json.dumps(value,ensure_ascii=False,indent=2,default=str));self.inspector.show()

    def _voice_selected(self,index):
        if index>=0 and index!=self.roundup.segment_list.currentRow():self.roundup.segment_list.setCurrentRow(index)

    def _voice_refresh(self):
        if not hasattr(self,'voice_segments'):return
        r=self.roundup;index=r.segment_list.currentRow();ids=[s['id'] for s in r.draft['segments']]
        if [self.voice_segments.itemText(i) for i in range(self.voice_segments.count())]!=ids:
            self.voice_segments.blockSignals(True);self.voice_segments.clear();self.voice_segments.addItems(ids);self.voice_segments.blockSignals(False)
        self.voice_segments.blockSignals(True);self.voice_segments.setCurrentIndex(index);self.voice_segments.blockSignals(False)
        self.voice_text.setPlainText(r.reading_preview.toPlainText() if index>=0 else tr('production.script_empty'))
        key=roundup.digest([r.draft.get('id'),r.draft.get('segments',[])[index]]) if index>=0 else None
        if getattr(self,'_frame_key',None)!=key:
            self.frame.image=None;self.frame.setText(tr('production.preview_hint'))

    def preview_roundup(self):
        r=self.roundup;index=r.segment_list.currentRow()
        if index<0:raise ValueError(tr('production.script_empty'))
        from tools.roundup_visuals import pages
        image=pages(r.draft,r.draft['segments'][index])[0].convert('RGBA')
        qimage=QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888).copy()
        self.frame.set_image(QPixmap.fromImage(qimage))
        self._frame_key=roundup.digest([r.draft.get('id'),r.draft['segments'][index]])

    def current_settings(self):
        return self.dashboard.settings_snapshot(replace(self.settings,output_dir=self.preferences.output_edit.text().strip(),
         export_markdown=self.preferences.markdown_check.isChecked(),export_word=self.preferences.word_check.isChecked(),
         export_premiere=self.preferences.premiere_check.isChecked(),export_sqlite=self.preferences.sqlite_check.isChecked()))

    def _save_runtime(self):
        if not self.dashboard.date_ranges_valid():raise ValueError(tr('date.range.invalid'))
        self.settings=self.current_settings();save_settings(self.settings);apply_settings(self.settings)
        self.feed_panel.save_state();self.roundup.settings=self.settings

    def _news_job(self,kind,action,payload):
        if self.worker is not None:raise ValueError(tr('production.conflict'))
        self._save_runtime();self.worker_kind=kind;self.worker=RoundupWorker(action,payload,self)
        self.worker.finished.connect(self._news_finished)
        self.news.set_running(True)
        self.worker.start();self._poll()

    def start_news_collect(self):
        if self.news.bundle and QMessageBox.question(self,tr('production.materials'),tr('production.replace_collection'))!=QMessageBox.StandardButton.Yes:return
        self.checkpoint();feeds=self.feed_panel.enabled_feeds();self._news_job('collect',lambda data,token:prepare_news(feeds,token,collect_only=True),{})

    def start_news_write(self):
        bundle=self.news.selected_bundle(for_write=True);self.checkpoint();feeds=self.feed_panel.enabled_feeds()
        self._news_job('write',lambda data,token:prepare_news(feeds,token,prepared=data),bundle);self.navigation.setCurrentRow(1)

    def start_news_export(self):
        bundle=self.news.selected_bundle();settings=self.current_settings()
        self._news_job('export',lambda data,token:export_news(data,settings,token),bundle)

    def _news_finished(self):
        worker,kind=self.worker,self.worker_kind;self.worker=None
        self.news.set_running(False)
        if worker.error:self.task_text.setText(tr('production.failed',error=worker.error));self.inspect(dict(task=kind,error=worker.error))
        elif worker.cancelled:self.task_text.setText(tr('production.cancelled'))
        elif kind=='export':self.results+=worker.value;self._refresh_files();self.task_text.setText(tr('production.export_done'))
        else:
            self.news.adopt(worker.value,collected=kind=='collect');self.task_text.setText(tr('production.finished',task=kind))
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
        elif error:self.task_text.setText(error)
        elif self.aux_kind=='launch':
            self.inspect(worker.value);self.task_text.setText(tr('production.finished',task=tr('launch_test_title')))
        elif self.aux_kind=='models':
            self.preferences._models_discovered(self._aux_config,worker.value,'');self.task_text.setText(tr('production.models_done'))
        else:
            result=worker.value
            self.inspect(dict(provider=self._aux_config.provider,model=result.model,elapsed_ms=result.elapsed_ms))
            self.task_text.setText(tr('production.connection_done'))
        worker.deleteLater();self._poll()

    def render_cards(self):
        self.cards.output_root=Path(self.preferences.output_edit.text())/'video-cards';self.cards.output_edit.setText(str(self.cards.output_root));self.cards._start()

    def roundup_video_items(self):
        from utils.time import format_datetime
        from utils.launch_status import launch_status_text
        selected={s['id'] for s in self.roundup.draft['selection'] if s['included']}
        items=[]
        for item in self.roundup.draft['snapshot']['items']:
            if item['ll2_id'] not in selected:continue
            name=roundup.confirmed_values(item.get('research',{}),item).get('chinese_name') or item['english_name']
            items.append(dict(id='ll2:'+item['ll2_id'],title=item.get('rocket',''),summary=name,source='Launch Library 2',
                published=item['net'],url=item.get('record_url') or item.get('ll2_source',{}).get('url') or 'https://ll.thespacedevs.com/2.3.0/launches/'+item['ll2_id']+'/',
                categories=[],video_type='launch',launch_context=item,
                launch=dict(orbit=item.get('orbit',''),payload=item.get('payload',''),site=item.get('site',''),
                    time=format_datetime(item['net'],self.roundup.draft.get('timezone','Asia/Shanghai')),
                    status=launch_status_text(dict(id=item.get('status_id'),name=item.get('status_name'))),recovery=item.get('recovery',''))))
        return items

    def cards_from_script(self):
        if self.cards.worker is not None:raise ValueError(tr('production.conflict'))
        if self.cards.data and QMessageBox.question(self,tr('production.cards'),tr('production.replace_cards'))!=QMessageBox.StandardButton.Yes:return
        self.checkpoint()
        if self.mode=='roundup':
            from tools.video_cards import validate_snapshot
            data=validate_snapshot(dict(protocol_version='1.0',job_id='roundup:'+self.roundup.draft['id'],
                report_title=self.dashboard.report_title_line.text(),language=self.settings.language,items=deepcopy(self.roundup_video_items())))
            path=resource_path('cache','production-cards.json');write_json(path,data)
            self.cards._set_data(data,path)
        else:
            from exporters.premiere import export_premiere
            from services.weekly_report import _export_context
            self._save_runtime();bundle=self.news.selected_bundle()
            context=bundle.get('context') or _export_context(self.settings,{})
            path=export_premiere(bundle['articles'],bundle.get('summaries',{}),context,self.settings.language,launches=bundle.get('launch_video_items',[]))
            self.cards.load_snapshot(path);self.results.append(str(path))
        self.sync_download_tasks()

    def sync_download_tasks(self):
        if self.downloads.worker is not None:return
        items=list(self.cards.data['items']) if self.cards.data else []
        if not items and self.news.bundle:items=self.news.bundle.get('launch_video_items',[])
        if not items:items=self.roundup_video_items()
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
        self.settings=self.dashboard.settings_snapshot(self.preferences.settings());save_settings(self.settings);apply_settings(self.settings);set_language(self.settings.language)
        self.theme.apply(self.settings.theme_mode);self.log.apply_theme(self.theme);self.roundup.settings=self.settings
        self._saved_preferences=self.preference_signature()
        self.settings_applied.emit();self._header();self.task_text.setText(tr('production.settings_saved'))

    def _daily(self):
        s=self.current_settings()
        return {k:getattr(s,k) for k in ('report_title','rss_limit','news_dedup_enabled','news_event_date_check_enabled',
         'news_filter_level','date_mode','start_date','end_date','launch_range','launch_start_date','launch_end_date',
         'export_markdown','export_word','export_premiere','export_sqlite','output_dir')}

    def _roundup_controls(self):
        r=self.roundup
        return dict(start=r.start.date().toString('yyyy-MM-dd'),end=r.end.date().toString('yyyy-MM-dd'),
         target=r.target.currentText(),python=r.python.text(),model=r.model.text(),speaker=r.speaker.currentText(),
         style=r.style.text(),device=r.device.currentText(),timeout=r.timeout.value(),pause=r.pause.value(),pronunciations=r.pronunciations.toPlainText())

    def content_state(self):
        self.cards._store_editor()
        return dict(version=1,daily=self._daily(),roundup=self.roundup.draft,roundup_controls=self._roundup_controls(),news=self.news.snapshot(),material_edits=self.material_edits,
         cards=dict(data=self.cards.data,source=str(self.cards.source) if self.cards.source else None,
          selected=sorted(self.cards.selected_ids()) if self.cards.data else [],seconds=self.cards.seconds.value(),encoding=self.cards.encoding.currentData()),
         intro=self.intro.options(),results=self.results)

    def snapshot(self):
        value=deepcopy(self.content_state())
        value['view']=dict(page=self.navigation.currentRow() if self.navigation.currentRow()>=0 else self._last_page,mode=self.mode,materials=self.materials.currentIndex(),media=self.media.currentIndex(),
         roundup_segment=self.roundup.segment_list.currentRow(),news_source=self.news.sources.currentRow(),news_article=self.news.articles.currentRow(),
         cards_row=self.cards.items.currentRow(),cards_frame=self.cards.slider.value(),
         spoken_scroll=self.roundup.spoken.verticalScrollBar().value(),screen_scroll=self.roundup.screen.verticalScrollBar().value(),
         narration_scroll=self.roundup.narration_edit.verticalScrollBar().value())
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
            self.news.restore(value.get('news',{}))
            card=value.get('cards',{})
            if card.get('data'):
                self.cards._set_data(card['data'],Path(card.get('source') or self.settings.output_dir),selected_ids=set(card.get('selected',[])))
                self.cards.seconds.setValue(card.get('seconds',12));self.cards.encoding.setCurrentIndex(max(0,self.cards.encoding.findData(card.get('encoding'))))
            intro=value.get('intro',{})
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
            for key,control in (('target',r.target),('speaker',r.speaker),('device',r.device)):
                if key in staged:
                    if control.findText(staged[key])<0:control.addItem(staged[key])
                    control.setCurrentText(staged[key])
            if 'timeout' in staged:r.timeout.setValue(staged['timeout'])
            if 'pause' in staged:r.pause.setValue(staged['pause'])
            if 'pronunciations' in staged:r.pronunciations.setPlainText(staged['pronunciations'])
            r._loading=False;r._voice_dirty=True
            self.results=value.get('results',[]);self._refresh_files()
            view=value.get('view',{});self.materials.setCurrentIndex(view.get('materials',0))
            self.content.setCurrentIndex(1 if view.get('mode')=='roundup' else 0)
            self.navigation.setCurrentRow(max(0,min(3,view.get('page',0))));self.media.setCurrentIndex(view.get('media',0))
            r.segment_list.setCurrentRow(view.get('roundup_segment',0));self.news.sources.setCurrentRow(view.get('news_source',0));self.news.articles.setCurrentRow(view.get('news_article',0))
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
        return roundup.digest({k:v for k,v in vars(self.preferences.settings()).items() if k not in self._daily()})

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
            self._mode();self._header()

    def _changed(self,*_):
        if not self._restoring:self._header()

    def _header(self):
        if not hasattr(self,'period'):return
        s=self.current_settings();news=s.start_date+' - '+s.end_date if s.date_mode=='custom' else self.dashboard.date_mode_combo.currentText()
        launches=s.launch_start_date+' - '+s.launch_end_date if s.launch_range=='custom' else self.dashboard.launch_range_combo.currentText();r=self.roundup.draft
        self.period.setText(tr('production.period',news=news,launches=launches,roundup=r['start']+' - '+r['end'],timezone=roundup.timezone_label(r)))
        if self._saved_hash:self.saved.setText(tr('production.saved' if roundup.digest(self.content_state())==self._saved_hash else 'production.unsaved'))

    def _refresh_files(self):
        self.output_files.clear()
        root=Path(self.preferences.output_edit.text())
        existing=[p for p in root.glob('*') if p.is_file() and p.suffix in ('.json','.md','.docx','.wav','.srt','.mov')]
        existing+=list(root.glob('SpaceWeekly_roundup_*'))
        existing+=list((root/'video-cards').glob('*/manifest.json'))
        for path in dict.fromkeys(self.results+[str(p) for p in existing[-100:]]):
            p=Path(path)
            if p.exists():
                label=str(p)
                manifest=p/'manifest.json' if p.is_dir() else p
                if manifest.name=='manifest.json' and manifest.is_file():
                    try:
                        if json.loads(manifest.read_text(encoding='utf-8')).get('state')=='incomplete':label=tr('production.incomplete')+' '+label
                    except (OSError,ValueError):pass
                self.output_files.addItem(label);self.output_files.item(self.output_files.count()-1).setData(Qt.ItemDataRole.UserRole,str(p.resolve()))

    def task_rows(self):
        rows=[]
        if self.aux_worker:rows.append(('diagnostic',tr('production.diagnostics'),tr('production.working',task=self.aux_kind),self.aux_worker.token.cancel))
        if self.worker:rows.append(('news:'+self.worker_kind,tr('production.news'),tr('production.working',task=self.worker_kind),self.worker.token.cancel))
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
        rows=self.task_rows();self.cancel_tasks.setEnabled(bool(rows))
        self.task_progress.setVisible(bool(rows))
        self.task_progress.setRange(0,0)
        if rows:self.task_text.setText(' | '.join(name+': '+text for _,name,text,_ in rows))
        for key,component in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads)):
            text=component.status.text()
            if self._last_statuses.get(key)!=text:
                self._last_statuses[key]=text
                self.log.append_html(escape(key+': '+text))
                if not rows:self.task_text.setText(text)
        self.apply_button.setEnabled(not rows);self.voice_settings.setEnabled(self.roundup.worker is None)
        self.listen_button.setEnabled(self.roundup.worker is None);self.speech_button.setEnabled(self.roundup.worker is None)
        self.confirm_name.setEnabled(self.roundup.worker is None and self.roundup.tasks.currentRow()>=0)
        self.export_news_button.setEnabled(self.worker is None)
        self.cards_export_button.setEnabled(self.cards.worker is None)
        self.intro_export_button.setEnabled(self.intro.worker is None and self.intro.preview_worker is None)
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
        if path and Path(path).exists() and path not in self.results:self.results.append(path);self._refresh_files()
        for path in (self.cards.manifest,self.intro.result_path,self.downloads.downloaded_file):
            if path and str(path) not in self.results:self.results.append(str(path));self._refresh_files()
        if self._pending_leave and not self.busy() and not self.roundup.tts_provider.is_running:
            target=self._pending_leave;self._pending_leave=None
            if not self._discard:
                if self._pending_rules:self.apply_rules()
                elif self._pending_preferences:self.apply_preferences()
                self.save()
            if target=='close':self.close_ready.emit()
            else:self.workspace_requested.emit(target)

    def choose_workspace(self):
        path=QFileDialog.getExistingDirectory(self,tr('production.workspace'),str(workspace_path() or Path.home()))
        if path:self.request_leave(path)

    def request_leave(self,target='close'):
        if self._pending_leave:return False
        self._pending_preferences=self.preference_signature()!=self._saved_preferences
        self._pending_rules=self.rules_signature()!=self._saved_rules
        if roundup.digest(self.content_state())!=self._saved_hash or self._pending_preferences or self._pending_rules:
            answer=QMessageBox.question(self,tr('production.unsaved'),tr('production.leave_question'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
            if answer==QMessageBox.StandardButton.Cancel:return False
            self._discard=answer==QMessageBox.StandardButton.Discard
            if not self._discard:self.save()
        if self.busy() and QMessageBox.question(self,tr('production.tasks'),tr('production.leave_tasks'))!=QMessageBox.StandardButton.Yes:
            self._discard=False;return False
        if self._discard:
            recovery=resource_path('cache','production-recovery.json');manual=resource_path('config','production-session.json')
            if manual.exists():write_json(recovery,json.loads(manual.read_text(encoding='utf-8')))
            else:recovery.unlink(missing_ok=True)
        self._pending_leave=target;self.cancel_all()
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
