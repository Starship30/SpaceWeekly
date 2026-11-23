"""Persistent production pages sharing the existing service/controller state."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html import escape
import json
import re
import time
from math import ceil
from pathlib import Path

from PySide6.QtCore import QDate,Qt,QTimer,Signal,QUrl,QSize,QPointF
from PySide6.QtGui import QColor,QDesktopServices,QPixmap,QImage,QPainter,QPen,QPolygonF
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,
 QStackedWidget,QTabWidget,QSplitter,QPlainTextEdit,QTextBrowser,QLineEdit,
 QScrollArea,QFormLayout,QFileDialog,QMessageBox,QDialogButtonBox,QMenu,QProgressBar,QCheckBox,QSizePolicy,QFrame,
 QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QSlider,QSpinBox,QDoubleSpinBox)
from i18n import tr
from models.news import news_id
from resources import resource_path,workspace_path
from services.cancellation import TaskCancelledError
from services import launch_roundup as roundup
from services.episode import DEFAULT_EPISODE_NAME, EPISODE_NAME_LIMIT, episode_name, includes_news, new_episode, opening_text, validate_episode
from services.episode_music import DEFAULTS as MUSIC_DEFAULTS,settings as music_settings,tracks as music_tracks
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
from ui.news_workspace import NewsWorkspace, populate_voice_assets
from ui.localize import set_ui_language
from ui.music_panel import MusicPanel,MusicPlaylist
from ui.theme import WORKSPACE_GAP
from ui.component import AspectPreview,SegmentedTabs,ContentBrowser,configure_table,StatusDelegate,WorkflowNavDelegate,detail_icon,launch_badge
from ui.widgets.combo_box import RoundedComboBox as QComboBox
from config import app_version
from utils.time import parse_datetime
from ui.widgets.date_range import NEWS_DATE_RANGES,LAUNCH_DATE_RANGES

SOURCE_DATE_KEYS=('date_mode','start_date','end_date','launch_range','launch_start_date','launch_end_date')
WORKFLOW_PAGES=('materials','script','media','export')
_BACKGROUND=ThreadPoolExecutor(max_workers=3,thread_name_prefix='SpaceWeekly')


class TaskSummary(QLabel):
    def setText(self,text):
        self.setToolTip(text)
        compact=text.splitlines()[0] if text else ''
        if compact.startswith('已导出：'):compact='导出完成，可在导出页打开成果。'
        elif re.search(r'Traceback|HTTPS?ConnectionPool|HTTPError|NameResolutionError|WinError|No such file',compact):
            compact='采集不完整，请重试联网刷新；详情见任务与日志。' if '采集' in compact or '刷新' in compact else '操作未完成，详情见任务与日志。'
        super().setText(compact if len(compact)<=150 else compact[:147]+'…')


class EditionPeriodButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.full_text=self.short_text=''
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(140)

    def set_caption(self,full,short):
        self.full_text,self.short_text=full,short
        self.setMaximumWidth(max(150,self.fontMetrics().horizontalAdvance(full)+55))
        self._fit_caption()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._fit_caption()

    def _fit_caption(self):
        metrics=self.fontMetrics();width=max(0,self.width()-49)
        text=self.full_text if metrics.horizontalAdvance(self.full_text)<=width else self.short_text
        self.setText(metrics.elidedText(text,Qt.TextElideMode.ElideRight,width))

    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen=QPen(self.palette().buttonText().color(),1.4);pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);painter.setPen(pen)
        x,y=self.width()-25,self.height()/2
        painter.drawPolyline(QPolygonF([QPointF(x-4,y-2),QPointF(x,y+2),QPointF(x+4,y-2)]))


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
        self._writing_input_digest=(None,None)
        self._recovery_write=self._terms_refresh=None
        self._poster_future=self._poster_request=None
        self.episode=new_episode()
        if settings.date_mode=='last_7_days' and settings.launch_range=='next_week' and settings.display_timezone=='Asia/Shanghai':
            self.episode['review_range']=list(roundup.default_range())
        self._pending_leave=None;self._discard=False;self._recovery_blocked=False
        self.results=[];self.result_scopes={};self._cards_base=None
        self._last_page=0
        self.material_edits={};self._detail_id=None;self._detail_loading=False
        self.feed_panel=FeedPanel(feeds);self.dashboard=DashboardPanel(settings)
        self.news=NewsWorkspace(self);self.roundup=LaunchRoundupDialog(settings,self)
        self.news.voice_settings=self.roundup.voice_settings
        self.news.in_edition=lambda article:includes_news(self.episode,article)
        self.roundup.workflow_embedded=True
        self.cards=VideoCardsDialog(settings.output_dir)
        self.intro=VideoIntroDialog(Path(settings.output_dir)/'video-cards')
        self.downloads=VideoDownloadDialog(workspace_path() or Path(settings.output_dir).parent,[])
        self.preferences=SettingsDialog(settings);self.preferences._model_timer.stop()
        self.rules=PromptStudioDialog(settings)
        for component in (self.cards,self.intro,self.downloads,self.preferences,self.rules):component.workflow_embedded=True
        self._build();self._connect();self._restore();self._restore_source_dates();self._sync_edition_dates()
        self._restoring=False
        initial=self.content_state(for_poll=True)
        self._observed_hash=roundup.digest(initial)
        self._observed_voice_hash=self._voice_state_hash(initial)
        self._saved_preferences=self.preference_signature();self._saved_rules=self.rules_signature();self._pending_preferences=False;self._pending_rules=False
        self._last_statuses={key:c.status.text() for key,c in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads))}
        initial_hash=roundup.digest(self.content_state())
        if not self._saved_hash:self._saved_hash=initial_hash
        self._update_save_tooltip(initial_hash)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self._poll)
        self.timer.start();self._mode();self._poll(initialize=True);self.retranslate()

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
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        top=QWidget();top.setObjectName('ProductionHeader');header=QHBoxLayout(top);header.setContentsMargins(28,12,25,12);header.setSpacing(17)
        self.workspace_label=QLabel()
        self.workspace_label.setToolTip(str(workspace_path() or Path(self.settings.output_dir).parent))
        self._detach_widget(self.dashboard,self.dashboard.report_title_line)
        self.edition_button=QPushButton();self.edition_button.setFlat(True)
        self.edition_button.setObjectName('EditionTitle');self.edition_button.setMaximumWidth(220)
        self.edition_button.clicked.connect(self._edit_title);header.addWidget(self.edition_button);header.addSpacing(18)
        self.dashboard.report_title_line.setMaximumWidth(220)
        header.addWidget(self.dashboard.report_title_line);self.dashboard.report_title_line.hide()
        self.dashboard.report_title_line.editingFinished.connect(self._finish_title)
        self.content=QComboBox();self.content.addItem(tr('production.news'),'news');self.content.addItem(tr('production.roundup'),'roundup')
        self.period=EditionPeriodButton();self.period.setObjectName('EditionPeriod');self.period.setAutoDefault(False)
        self.period.clicked.connect(lambda:self.guard(self.edit_episode));header.addWidget(self.period,1);header.addStretch()
        self.save_button=self.button('production.save',self.save,header)
        settings_button=self.button('settings',self.show_settings,header)
        more=QPushButton(tr('production.more'));menu=QMenu(more)
        menu.addAction(tr('production.workspace'),self.choose_workspace)
        menu.addAction('工作区详情',lambda:QMessageBox.information(self,'当前工作区',self.workspace_label.toolTip()))
        menu.addAction(tr('production.open_edition'),lambda:self.guard(self.open_edition))
        menu.addAction(tr('production.diagnostics'),lambda:self.inspect(dict(version=app_version(),tasks=[r[:3] for r in self.task_rows()])))
        menu.addAction(tr('launch_test_title'),lambda:self.guard(lambda:self.start_diagnostic('launch')))
        more.setMenu(menu);header.addWidget(more)
        for action in (self.save_button,settings_button,more):action.setProperty('headerAction',True)
        outer.addWidget(top)
        body=QHBoxLayout();body.setContentsMargins(0,0,0,0);body.setSpacing(0)
        self.navigation=QListWidget();self.navigation.setObjectName('ProductionNav');self.navigation.setFixedWidth(208)
        self.navigation.setItemDelegate(WorkflowNavDelegate(self.navigation));self.navigation.setMouseTracking(True)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key in WORKFLOW_PAGES:
            item=QListWidgetItem(tr('production.'+key));item.setSizeHint(QSize(208,72));self.navigation.addItem(item)
        body.addWidget(self.navigation);content=QVBoxLayout();content.setContentsMargins(25,19,25,17);content.setSpacing(WORKSPACE_GAP)
        page_header=QHBoxLayout()
        self.page_title=QLabel(tr('production.materials'));self.page_title.setObjectName('PageTitle');page_header.addWidget(self.page_title,1)
        page_header.addWidget(self.content);content.addLayout(page_header)
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
        for page in (self.news.data_page,self.news.script_page,self.roundup.script_page,self.intro,self.downloads,self.cards):
            page.layout().setContentsMargins(0,0,0,0);page.layout().setSpacing(WORKSPACE_GAP)
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
        footer=QWidget();footer.setObjectName('ProductionFooter')
        strip=QHBoxLayout(footer);strip.setContentsMargins(26,10,26,10);strip.setSpacing(12)
        status_dot=QLabel();status_dot.setObjectName('WorkflowStatusDot');status_dot.setFixedSize(17,17);strip.addWidget(status_dot)
        self.task_text=TaskSummary(tr('ready'));self.task_text.setObjectName('MutedText');self.task_text.setWordWrap(True);strip.addWidget(self.task_text,1)
        self.task_text.setMaximumHeight(65)
        self.task_progress=QProgressBar();self.task_progress.setMaximumWidth(150);self.task_progress.hide();strip.addWidget(self.task_progress)
        self.cancel_tasks=self.button('production.cancel_tasks',self.cancel_all,strip)
        self.cancel_tasks.setObjectName('TextButton')
        self.last_diagnostic_button=self.button('查看诊断',lambda:self.inspect(self._last_diagnostic),strip)
        self.last_diagnostic_button.setObjectName('TextButton');self.last_diagnostic_button.hide()
        self.log_toggle=QPushButton(tr('production.show_log'));self.log_toggle.setCheckable(True);strip.addWidget(self.log_toggle)
        self.log_toggle.setObjectName('TextButton')
        self.previous_button=self.button('production.previous_step',self.previous_step,strip)
        self.next_button=self.button('production.next_step',self.next_step,strip,True)
        self.log=LogPanel();self.log.setMaximumHeight(180);self.log.hide();self.log.apply_theme(self.theme);outer.addWidget(self.log)
        outer.addWidget(footer)
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
        page_layout=r.data_page.layout();page_layout.setContentsMargins(0,0,0,0);page_layout.setSpacing(10)
        dates=page_layout.itemAt(0).layout();dates.setContentsMargins(5,0,0,0);dates.setSpacing(11)
        detail=QWidget();detail.setObjectName('DetailPanel');detail.setAccessibleName('任务详情')
        layout=QVBoxLayout(detail);layout.setContentsMargins(20,14,20,22);layout.setSpacing(10)
        header=QHBoxLayout()
        self.launch_title=QLabel();self.launch_title.setObjectName('LaunchDetailTitle');self.launch_title.setWordWrap(True)
        self.launch_title.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse|Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.launch_title.linkActivated.connect(self._edit_current_launch_name);header.addWidget(self.launch_title,1)
        self.launch_status=QLabel();self.launch_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.launch_status,0,Qt.AlignmentFlag.AlignTop);layout.addLayout(header)
        self.launch_name_controls=QWidget();name_row=QHBoxLayout(self.launch_name_controls);name_row.setContentsMargins(0,0,0,0)
        self.launch_name_edit=QLineEdit();self.launch_name_edit.setPlaceholderText('本期任务名称（可直接修改）')
        self.launch_name_edit.setObjectName('LaunchNameEdit');name_row.addWidget(self.launch_name_edit,1)
        self.save_launch_name_button=self.button('production.save_name',self.save_launch_name,name_row)
        self.save_launch_name_button.setObjectName('SaveLaunchName')
        self.launch_name_edit.returnPressed.connect(lambda:self.guard(self.save_launch_name))
        self.launch_name_controls.hide();layout.addWidget(self.launch_name_controls)
        self.launch_name_edit.textEdited.connect(self._stage_launch_name)
        body=QWidget();self.launch_detail_body=body;card=QVBoxLayout(body);card.setContentsMargins(0,0,0,0);card.setSpacing(10)
        self.launch_time=QLabel();self.launch_rocket=QLabel();self.launch_meta_icons=[]
        for field in (self.launch_time,self.launch_rocket):
            row=QHBoxLayout();row.setSpacing(10)
            icon=QLabel();icon.setFixedSize(24,24);self.launch_meta_icons.append(icon)
            row.addWidget(icon,0,Qt.AlignmentFlag.AlignTop)
            field.setObjectName('LaunchDetailHint');field.setTextFormat(Qt.TextFormat.PlainText);field.setWordWrap(True)
            row.addWidget(field,1);card.addLayout(row)
        card.addSpacing(4)
        self.launch_summary_title=QLabel('任务简介');self.launch_summary_title.setObjectName('SectionTitle');card.addWidget(self.launch_summary_title)
        self.launch_summary=QLabel();self.launch_summary.setObjectName('LaunchDetailHint');self.launch_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.launch_summary.setWordWrap(True);card.addWidget(self.launch_summary)
        r.before_task_edit=self.checkpoint;r.task_edited.connect(self._launch_name_saved)
        actions=QHBoxLayout()
        full=QPushButton('编辑本期内容');full.setObjectName('DetailAction');full.clicked.connect(lambda:self.guard(self.edit_launch_content));actions.addWidget(full)
        evidence=self.button('production.show_evidence',self._show_task_evidence,actions);evidence.setObjectName('TextButton')
        self.launch_detail_actions=(full,evidence)
        actions.addStretch(1)
        for child in (actions,self.launch_details):
            divider=QFrame();divider.setObjectName('LaunchDetailDivider');divider.setFixedHeight(1);card.addWidget(divider)
            if child is actions:card.addLayout(actions)
            else:card.addWidget(child)
        layout.addWidget(body);layout.addStretch(1)
        summary=page_layout.takeAt(1).widget();page_layout.removeWidget(r.tasks)
        list_area=QWidget();left=QVBoxLayout(list_area);left.setContentsMargins(0,0,0,0);left.setSpacing(8)
        left.addWidget(summary);left.addWidget(r.tasks,1)
        # The details start level with the table's selection summary.
        split=QSplitter();split.setHandleWidth(16);split.addWidget(list_area)
        detail_scroll=self.scroll(detail);detail_scroll.setObjectName('LaunchDetailScroll');detail_scroll.setMinimumWidth(270)
        details_area=QWidget();right=QVBoxLayout(details_area);right.setContentsMargins(0,15,0,0);right.addWidget(detail_scroll)
        split.addWidget(details_area);split.setStretchFactor(0,5);split.setStretchFactor(1,3)
        split.setSizes([790,488]);split.setChildrenCollapsible(False);page_layout.insertWidget(1,split,1)
        r.tasks.setObjectName('LaunchTasks')
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
        while page_layout.count()>2:
            item=page_layout.takeAt(2)
            if item.widget():item.widget().hide()
            if item.layout():
                while item.layout().count():
                    child=item.layout().takeAt(0)
                    if child.widget():child.widget().hide()
        more=QPushButton('更多');menu=QMenu(more)
        complete=r.refresh_button;complete.setText('补全中文资料');complete.clicked.disconnect()
        complete.setProperty('roundupAction','_supplement_materials')
        complete.setToolTip('先复用资料与译名；缺失时联网核对正式名称和背景，再补译中文显示。不会覆盖人工稿件。')
        complete.clicked.connect(lambda:self.guard(r._supplement_materials));self.complete_launch_materials_button=complete
        for label,action in [('刷新发射列表（联网）',r._refresh),('重新补全本任务',r._recheck_materials),
          ('同步名称与资料',self.refresh_names),('统计与资料缺口',lambda:self.inspect(dict(统计=roundup.statistics(r.draft),采集=r.draft['snapshot'].get('reason',''))))]:
            menu.addAction(label,lambda _=False,fn=action:self.guard(fn))
        more.setMenu(menu);page_layout.itemAt(0).layout().addWidget(more)
        for edit in (r.start,r.end):edit.setObjectName('LaunchDate')
        for button in (r.recent_dates,more):button.setObjectName('LaunchAction')
        r.recent_dates.setMinimumWidth(105);more.setMinimumWidth(86)

    def _show_task_evidence(self):
        if not self._detail_id:return
        item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==self._detail_id)
        names=roundup.display_names(item)
        from translator.dictionary import load_dictionary
        terms={k:v for k,v in load_dictionary().items() if k in [item.get(f) for f in ('english_name','rocket','site_name','pad_name')]}
        self.inspect(dict(原始名称=item['english_name'],名称对应=names,核对状态=roundup.material_status(item),词典条目=terms,原始资料=item))

    def edit_launch_content(self):
        if not self._detail_id:raise ValueError('请选择要编辑的任务')
        from ui.launch_edit_dialog import LaunchEditDialog
        item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==self._detail_id)
        LaunchEditDialog(self,item).exec()

    def _stage_launch_name(self,text):
        if self._detail_id:self.material_edits.setdefault(self._detail_id,{})['name']=text

    def _edit_current_launch_name(self,_link):
        if self.launch_name_edit.isEnabled():
            self.launch_name_controls.show();self.launch_name_edit.setFocus();self.launch_name_edit.selectAll()

    def save_launch_name(self):
        if not self._detail_id:raise ValueError('请选择要修改的任务')
        if not self.roundup.edit_task_field(self._detail_id,'name',self.launch_name_edit.text().strip()):
            self._launch_name_saved(self._detail_id)

    def _launch_name_saved(self,identifier):
        self.material_edits.pop(identifier,None)
        self.launch_name_controls.hide()
        self._frame_key=self._timeline_key=None;self._launch_detail();self._changed()

    def _launch_detail(self):
        row=self.roundup.tasks.currentRow()
        if row<0 or row>=len(self.roundup.draft['selection']):
            self._detail_id=None;self.launch_title.setText('请选择一项发射任务')
            self.launch_title.setToolTip('');self.launch_details.clear()
            self.launch_status.hide();self.launch_detail_body.hide();self.launch_name_controls.hide()
            self.launch_name_edit.clear();self.launch_name_edit.setEnabled(False);self.save_launch_name_button.setEnabled(False)
            for button in self.launch_detail_actions:button.setEnabled(False)
            return
        self.launch_status.show();self.launch_detail_body.show()
        for button in self.launch_detail_actions:button.setEnabled(True)
        identifier=self.roundup.draft['selection'][row]['id'];item=next(i for i in self.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
        names=roundup.display_names(item)
        if self._detail_id!=identifier:self.launch_name_controls.hide()
        self.launch_name_edit.setEnabled(self.roundup.worker is None);self.save_launch_name_button.setEnabled(self.roundup.worker is None)
        self.launch_name_edit.setText(self.material_edits.get(identifier,{}).get('name',names['name']))
        from utils.time import format_datetime
        from services.roundup_style import event_result
        self._detail_id=identifier
        colors=self.theme.theme.colors
        self.launch_title.setText(f'<a href="rename" style="color:{colors["text"]};text-decoration:none">{escape(names["name"])}</a>')
        self.launch_title.setToolTip('点击修改本期任务名称\n原文：'+item['english_name'])
        label,state=launch_badge(event_result(item,self.roundup.draft))
        tint=QColor(colors.get('log_'+state,colors['text_muted']))
        self.launch_status.setStyleSheet(f'color:{tint.name()}; background:rgba({tint.red()},{tint.green()},{tint.blue()},28); border-radius:13px; padding:4px 12px; font-weight:600;')
        self.launch_status.setText(label)
        self.launch_time.setText('发射时间\n'+(format_datetime(names.get('net',''),'Asia/Shanghai')[:16] or '待确认'))
        self.launch_rocket.setText('火箭  '+(names['rocket'] or '待确认'))
        for icon,key in zip(self.launch_meta_icons,('time','rocket')):
            icon.setPixmap(QPixmap.fromImage(detail_icon(key,colors['text_subtle'])))
        translated=names.get('description_translation') if not names['summary'] else ''
        self.launch_summary_title.setText('任务简介 · AI 参考译文' if translated else '任务简介')
        self.launch_summary.setText((names['summary'] or translated or '暂无已核实的中文简介。')[:400])
        self.launch_summary.setToolTip('双击表格名称或按 F2 修改。原始名称和资料可在完整出处中查看。')
        orbit=roundup.event_context(item,self.roundup.draft)['orbit']
        quantity=str(names['payload_count']) if names['payload_count'] is not None else names['quantity_source']
        facts=(('location','发射地点',names['location'],True),
               ('agency','机构',names['provider']+'（'+(names['organization_type'] or '类型待核对')+'）',False),
               ('payload','载荷',names['payload'] or '缺少对应载荷名称',True),
               ('count','数量',quantity,False),('orbit','目标轨道',orbit or '待补充',True))
        rows=[]
        for key,title,value,stacked in facts:
            self.launch_details.set_icon(key,detail_icon(key,colors['text_subtle']))
            separator='<br>' if stacked else ' · '
            rows.append(f'<tr><td width="34" valign="top"><img src="detail:{key}"></td>'
                        f'<td valign="middle"><b>{title}</b>{separator}{escape(value or "待补充")}</td></tr>')
        self.launch_details.setHtml(f'<table cellspacing="0" cellpadding="5" width="100%" style="color:{colors["text_muted"]}">'
                                    +''.join(rows)+'</table>')
    def _task_column_sizes(self):
        table=self.roundup.tasks
        small=self.width()<1100
        header=table.horizontalHeader()
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        for column,width in ((0,36 if small else 58),(12,82 if small else 154),
                             (5,92 if small else 164),(13,56 if small else 106)):
            table.setColumnWidth(column,width)
        table.horizontalHeaderItem(5).setText('时间' if small else roundup.timezone_label(self.roundup.draft))
        table.horizontalHeaderItem(13).setText('结果' if small else '发射结果')
        for date in (self.roundup.start,self.roundup.end):date.setFixedWidth(124 if small else 153)
        blocked=table.blockSignals(True)
        for row in range(table.rowCount()):
            cell=table.item(row,5)
            if cell:cell.setText(cell.toolTip()[5:16] if small else cell.toolTip()[:16])
        table.blockSignals(blocked)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        small=self.width()<1100
        if hasattr(self,'roundup') and small!=getattr(self,'_narrow_tasks',None):
            self._narrow_tasks=small;self._task_column_sizes()

    def refresh_names(self,update_manual=False):
        r=self.roundup
        if r.worker:raise ValueError(tr('production.conflict'))
        r._store_segment();roundup.sync_materials(r.draft)
        pending=roundup.sync_names(r.draft,update_manual=update_manual)
        selected=r.segment_list.currentRow();r._show_tasks();r._show_segments(selected)
        self._frame_key=self._timeline_key=None
        self._launch_detail();self._refresh_visible_voice()
        self.name_update_button.setVisible(bool(pending))
        self.task_text.setText('名称已刷新；已有配音或人工编辑稿保留，请点击“更新稿件名称”采用新名称。' if pending else '名称已刷新；朗读有变化的旧音频需重新生成。')

    @staticmethod
    def _load_local_terms(draft):
        roundup.sync_materials(draft)
        roundup.sync_names(draft)
        return draft

    def _poll_terms(self):
        paths=[resource_path('translator',name) for name in ('dictionary.json','launch_research.json','translation_cache.json')]
        paths.append(resource_path('config','aerospace_terms.json'))
        signature=tuple((str(p),p.stat().st_mtime_ns if p.exists() else None) for p in paths)
        r=self.roundup
        def basis(draft):
            return roundup.digest([draft['snapshot'],draft['segments'],draft['name_bindings'] if 'name_bindings' in draft else None])
        if self._terms_refresh:
            future,original,previous,revision,started=self._terms_refresh
            if not future.done() or r.worker:return
            self._terms_refresh=None
            if r.draft is original and revision==self._edition_revision and basis(original)==previous:
                self._terms_signature=started
                try:result=future.result()
                except (OSError,ValueError,TypeError,KeyError) as error:
                    self.task_text.setText('刷新本地发射资料失败：'+str(error))
                else:
                    if basis(result)!=previous:
                        for key in ('snapshot','segments','name_bindings','name_updates_pending'):
                            if key in result:original[key]=result[key]
                        selected=r.segment_list.currentRow();r._show_tasks();r._show_segments(selected)
                        self._launch_detail()
                        if self.mode=='roundup':
                            self._frame_key=self._timeline_key=None;self._refresh_visible_voice()
        if signature!=getattr(self,'_terms_signature',None) and not r.worker:
            draft=r.draft
            local=deepcopy({key:draft[key] for key in ('snapshot','segments','name_bindings','name_updates_pending') if key in draft})
            self._terms_refresh=(_BACKGROUND.submit(self._load_local_terms,local),draft,basis(draft),self._edition_revision,signature)

    def update_script_names(self):
        if QMessageBox.question(self,'更新稿件名称','仅替换稿件中的名称，保留其他人工文字。旧音频可能需重新生成。继续？')==QMessageBox.StandardButton.Yes:
            self.checkpoint();self.refresh_names(update_manual=True)

    def _voice_page(self):
        r=self.roundup;page=QWidget();layout=QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(WORKSPACE_GAP)
        setup_row=QHBoxLayout()
        self.voice_status=QLabel();self.voice_status.setObjectName('MutedText');self.voice_status.setWordWrap(True)
        setup_row.addWidget(self.voice_status)
        self.button('production.configure_voice',self.show_voice_settings,setup_row)
        setup_row.addWidget(QLabel('音色'))
        self._detach_widget(r.voice_page,r.speaker);r.speaker.setMaximumWidth(160);setup_row.addWidget(r.speaker)
        self.voice_sample_button=self.button('试听音色',self._voice_sample,setup_row)
        setup_row.addStretch(1)
        layout.addLayout(setup_row)
        self.voice_segments=QComboBox();self.voice_segments.currentIndexChanged.connect(self._voice_selected)
        self.voice_segments.setObjectName('VoiceSegments')
        self.voice_segments.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.voice_segments.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.voice_segments.setMinimumWidth(144)
        self.voice_scope=QLabel();self.voice_scope.setObjectName('MutedText');self.voice_scope.setBuddy(self.voice_segments)
        row=QHBoxLayout();row.addWidget(self.voice_scope);row.addWidget(self.voice_segments,1)
        self.listen_button=self.button('production.listen',self.listen_roundup,row)
        self.speech_button=self.button('production.synthesize',self.synthesize_media,row,True)
        layout.addLayout(row)
        row=QHBoxLayout()
        self.voice_image_use=QCheckBox('使用本条配图');self.voice_image_use.setToolTip('取消后预览和导出不使用配图；可在资料页更换或导入自行找到的图片。')
        self.voice_image_use.toggled.connect(lambda enabled:self.news.set_image_use(self.news.current_article(),enabled))
        row.addWidget(self.voice_image_use)
        self.voice_image_menu=QPushButton('管理画面素材');images=QMenu(self.voice_image_menu)
        images.addAction('读取或重试配图',lambda:self.news.request_image({},article=self.news.current_article()))
        images.addAction('选择候选图片',lambda:self.guard(lambda:self.news.choose_candidate(article=self.news.current_article())))
        images.addAction('添加本地图片（可多选）',lambda:self.news.import_image(article=self.news.current_article()))
        images.addAction('选择已保存图片和顺序',lambda:self.news.edit_gallery(article=self.news.current_article()))
        images.addAction('添加本地视频',lambda:self.news.import_video(article=self.news.current_article()))
        images.addAction('剪裁视频（I / O）',lambda:self.news.trim_video(article=self.news.current_article()))
        images.addAction('移除本条视频',lambda:self.news.request_image(dict(remove_video=True),article=self.news.current_article()))
        self.voice_image_menu.setMenu(images);row.addWidget(self.voice_image_menu)
        row.addStretch(1);layout.addLayout(row)
        self.music_panel=MusicPanel();self.music_panel.set_edition(self.episode);layout.addWidget(self.music_panel)
        self.music_panel.changed.connect(lambda changes:self.guard(lambda:self._update_music(changes)))
        self.music_panel.audition_started.connect(self._stop_preview)
        self.music_panel.mix_requested.connect(lambda:self.guard(self._listen_mix))
        self.music_panel.failed.connect(lambda message:self.task_text.setText(message))
        split=QSplitter();split.setChildrenCollapsible(False)
        text=QWidget();left=QVBoxLayout(text);left.setContentsMargins(0,0,0,0)
        text_label=QLabel('口播文本');text_label.setObjectName('SectionTitle');left.addWidget(text_label)
        self.voice_text=QPlainTextEdit();left.addWidget(self.voice_text,1)
        text_label.setBuddy(self.voice_text)
        self.voice_text.setToolTip('这里编辑口播；实际 TTS 输入在稿件页“最终朗读”下方预览。')
        self.voice_text.textChanged.connect(self._edit_voice_text)
        self.play_state=QLabel();self.play_state.setWordWrap(True);left.addWidget(self.play_state)
        split.addWidget(text);view=QWidget();right=QVBoxLayout(view);right.setContentsMargins(0,0,0,0)
        preview_label=QLabel('画面预览');preview_label.setObjectName('SectionTitle');right.addWidget(preview_label)
        self.frame=AspectPreview(tr('production.preview_hint'));self.frame.setAlignment(Qt.AlignmentFlag.AlignCenter);self.frame.setMinimumSize(240,135)
        self.frame.setObjectName('ProductionPreview');self.frame.setWordWrap(True)
        self.frame.image_clicked.connect(lambda point:self.guard(lambda:self._browse_launch_preview(point)))
        right.addWidget(self.frame,1)
        self.preview_slider=QSlider(Qt.Orientation.Horizontal);self.preview_slider.setObjectName('VideoPreviewSlider')
        self.preview_slider.setAccessibleName('整期音画预览位置');self.preview_slider.setEnabled(False)
        self.preview_slider.valueChanged.connect(lambda *_:self.guard(self._seek_preview))
        self.preview_slider.sliderPressed.connect(self._begin_preview_seek)
        self.preview_slider.sliderReleased.connect(lambda:self.guard(self._end_preview_seek))
        right.addWidget(self.preview_slider)
        time_row=QHBoxLayout();self.preview_clock=QLabel();self.preview_clock.setWordWrap(True)
        self.preview_clock.setObjectName('MutedText');time_row.addWidget(self.preview_clock,1)
        self.preview_tasks=QComboBox();self.preview_tasks.setMinimumWidth(100);self.preview_tasks.setMaximumWidth(220)
        self.preview_tasks.activated.connect(lambda index:self.guard(lambda:self.jump_task(self.preview_tasks.itemData(index))))
        time_row.addWidget(self.preview_tasks);right.addLayout(time_row)
        actions=QHBoxLayout();self.preview_button=self.button('production.preview',self._request_visible_preview,actions)
        self.preview_play=QPushButton('播放画面');self.preview_play.clicked.connect(lambda:self.guard(self._toggle_preview));actions.addWidget(self.preview_play)
        self.sync_button=QPushButton('同步与画面');sync_menu=QMenu(self.sync_button)
        sync_menu.addAction('同步校准',lambda:self.guard(self.show_sync))
        sync_menu.addAction('镜头编排（高级）',lambda:self.guard(self.show_shot_settings))
        sync_menu.addAction('重新生成本条配音',lambda:self.guard(lambda:self.news_audio(force=True) if self.mode=='news' else r._audio_one()))
        self.sync_button.setMenu(sync_menu);actions.addWidget(self.sync_button)
        actions.addStretch()
        right.addLayout(actions)
        self.preview_timer=QTimer(self);self.preview_timer.setInterval(33)
        self.preview_timer.timeout.connect(lambda:self.guard(self._advance_preview))
        self._poster_timer=QTimer(self);self._poster_timer.setInterval(24)
        self._poster_timer.timeout.connect(self._poll_poster)
        split.addWidget(view);split.setStretchFactor(0,2);split.setStretchFactor(1,3);split.setSizes([320,600]);layout.addWidget(split,1)
        self.media.addTab(self.scroll(page),tr('production.roundup_voice'))
        r.preview_ready.connect(self._audio_preview_ready)
        r.sample_ready.connect(self._voice_sample_ready)

    def _voice_sample(self):
        from ai.speech import SpeechRequest,generate_speech
        from services.voice_profiles import REFERENCE_TEXT
        from uuid import uuid4

        if not self._voice_configured():self.show_voice_settings();return
        r=self.roundup;configured=r.voice_settings()['tts']
        def generate(options,token):
            r.tts_provider.configure(options)
            request=SpeechRequest(REFERENCE_TEXT,model=options['model'] or 'local-reference',
                                  voice=options['speaker'],style=options.get('style',''))
            return generate_speech(request,resource_path('cache','voice_samples',uuid4().hex),
                                   provider=r.tts_provider,cancel_check=token.check).path
        r._start_job('voice_sample',generate,configured)

    def _voice_sample_ready(self,path):
        if self.navigation.currentRow()==2 and self.media.currentIndex()==0:self.roundup._play(path)

    def _update_music(self,changes):
        previous=music_settings(self.episode)
        track_change=changes.get('music',self.episode.get('music',[]))!=self.episode.get('music',[])
        updated={**self.episode,**changes};validate_episode(updated)
        options=music_settings(updated)
        reprepare=track_change or any(previous[key]!=options[key] for key in ('enabled','loop','fade_in','fade_out'))
        playing=self.preview_timer.isActive() and getattr(self,'_audio_playback',False)
        self.episode.update(changes)
        if track_change:self.music_panel.stop();self.music_panel.set_edition(self.episode)
        self._update_preview_levels()
        if reprepare and playing:self._stop_preview();self._prepare_audio_preview()
        if reprepare:self._changed()

    def _listen_mix(self):
        if self.roundup.worker is not None:raise ValueError('请先等待当前任务完成或取消')
        self._stop_preview();self._audition_end=None
        self.preview_roundup()
        from services.roundup_sync import row_at
        if self.preview_slider.value()>=self.preview_slider.maximum():self.preview_slider.setValue(0)
        clock=row_at(self._preview_timeline,self.preview_slider.value()/1000)
        if not clock['audio']:raise ValueError('请先生成当前段落的配音')
        self._prepare_audio_preview()

    def _edit_voice_text(self):
        r=self.roundup
        if self.mode=='news':
            if getattr(self,'_voice_loading',False) or self.worker or r.worker:return
            if self.news.is_selected(self.news.current_article()):
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
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(WORKSPACE_GAP)
        review=QWidget();review.setObjectName('WorkspacePanel');review_box=QVBoxLayout(review)
        review_box.setContentsMargins(16,12,16,12);review_box.setSpacing(8)
        self.export_hint=QLabel();self.export_hint.setObjectName('SectionTitle');self.export_hint.setWordWrap(True)
        review_box.addWidget(self.export_hint)
        scope_row=QHBoxLayout();self.export_contents=QLabel();self.export_contents.setWordWrap(True)
        scope_row.addWidget(self.export_contents,1)
        self.button('production.review_script',self._review_export_scope,scope_row).setObjectName('TextButton')
        review_box.addLayout(scope_row)
        self.export_voice_row=QWidget();self.export_voice_row.setProperty('formGroup',True)
        voice_row=QHBoxLayout(self.export_voice_row);voice_row.setContentsMargins(0,0,0,0)
        self.export_voice_state=QLabel();voice_row.addWidget(self.export_voice_state,1)
        self.button('production.configure_voice',self.show_voice_settings,voice_row).setObjectName('TextButton')
        review_box.addWidget(self.export_voice_row);layout.addWidget(review)
        output=QWidget();output.setObjectName('WorkspacePanel');output_box=QVBoxLayout(output)
        output_box.setContentsMargins(16,12,16,12);output_box.setSpacing(8)
        path_row=QHBoxLayout();path_label=QLabel(tr('video.output'));path_row.addWidget(path_label)
        self._detach_widget(self.preferences,self.preferences.output_edit);path_row.addWidget(self.preferences.output_edit,1)
        path_label.setBuddy(self.preferences.output_edit)
        self.button('production.choose_output',self.choose_output,path_row);output_box.addLayout(path_row)
        news_actions=QHBoxLayout()
        self.complete_export_button=self.button('导出本期视频',self.export_complete,news_actions,True)
        self.episode_preview_button=self.button('预览本期（含配音）',lambda:self.export_complete(preview=True),news_actions)
        news_actions.addStretch(1);output_box.addLayout(news_actions)
        self.news_formats=QWidget();formats=QVBoxLayout(self.news_formats);formats.setContentsMargins(0,0,0,0);output_box.addWidget(self.news_formats)
        self.news_formats.setProperty('formGroup',True)
        advanced=QPushButton('文稿与资料格式');advanced.setCheckable(True);formats.addWidget(advanced,0,Qt.AlignmentFlag.AlignLeft)
        documents=QWidget();documents.setProperty('formGroup',True);form=QFormLayout(documents)
        self._move_form_control(self.preferences,self.preferences.word_check,form,'Word 文档')
        self._move_form_control(self.preferences,self.preferences.markdown_check,form,'Markdown 文稿')
        self._move_form_control(self.preferences,self.preferences.premiere_check,form,'Premiere 素材快照（JSON）')
        self._move_form_control(self.preferences,self.preferences.sqlite_check,form,'新闻数据库（SQLite）')
        doc_actions=QHBoxLayout();self.export_news_button=self.button('导出所选文稿与资料',self.start_news_export,doc_actions)
        doc_actions.addStretch(1);form.addRow(doc_actions)
        documents.hide();formats.addWidget(documents);advanced.toggled.connect(documents.setVisible)
        export_layout=self.roundup.export_controls.layout()
        script=export_layout.takeAt(0).widget();visuals=export_layout.takeAt(0).widget()
        primary=QHBoxLayout();primary.addWidget(script);primary.addStretch(1)
        visuals.hide();visuals.deleteLater();script.setObjectName('TextButton');export_layout.insertLayout(0,primary)
        output_box.addWidget(self.roundup.export_controls)
        actions=QHBoxLayout();self.cards_export_button=self.button('production.render_cards',self.render_cards,actions)
        self.button('production.open_output',self.open_output,actions).setObjectName('TextButton');actions.addStretch(1);output_box.addLayout(actions)
        self.include_intro=QCheckBox('随视频素材导出已制作的片头（独立 MP4）')
        output_box.addWidget(self.include_intro);layout.addWidget(output)
        results=QWidget();results.setObjectName('WorkspacePanel');results_box=QVBoxLayout(results)
        results_box.setContentsMargins(16,10,16,12)
        results_bar=QHBoxLayout();self.result_title=QLabel('本期成果');self.result_title.setObjectName('SectionTitle');results_bar.addWidget(self.result_title,1)
        self.history_toggle=QPushButton('查看历史成果');self.history_toggle.setCheckable(True)
        self.history_toggle.toggled.connect(self._refresh_files);results_bar.addWidget(self.history_toggle);results_box.addLayout(results_bar)
        self.output_files=QListWidget();self.output_files.setProperty('flat',True)
        self.output_files.itemDoubleClicked.connect(lambda item:QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))))
        self.output_hint=QLabel();self.output_hint.setObjectName('MutedText');results_box.addWidget(self.output_hint)
        results_box.addWidget(self.output_files,1);layout.addWidget(results,1);self.pages.addWidget(self.scroll(page))

    def _export_checks(self):
        complete=self.settings.video_workflow=='complete'
        news=len(self.news.selected_articles()) if complete or self.mode=='news' else 0
        launches=self._launch_count() if complete or self.mode=='roundup' else 0
        key='production.export_selection' if news or launches else 'production.export_empty_selection'
        self.export_contents.setText(tr(key,news=news,launches=launches))
        self.export_voice_row.setVisible(complete)
        if complete:
            ready=self._voice_configured()
            self.export_voice_state.setText(tr('production.voice_ready' if ready else 'production.voice_missing'))

    def _review_export_scope(self):
        if self.settings.video_workflow=='complete':
            news=bool(self.news.selected_articles());launches=bool(self._launch_count())
            if news!=launches:self.content.setCurrentIndex(0 if news else 1)
        self.navigation.setCurrentRow(1)

    def _voice_configured(self):
        from services.voice_profiles import configured
        r=self.roundup
        return configured(dict(python=r.python.text().strip(),model=r.model.text().strip(),speaker=r.speaker_id()))

    def _settings_page(self):
        page=QWidget();layout=QVBoxLayout(page);self.settings_tabs=SegmentedTabs();layout.addWidget(self.settings_tabs,1)
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(WORKSPACE_GAP)
        self.preferences.layout().setContentsMargins(0,0,0,0);self.preferences.nav.setFixedWidth(140)
        self.preferences.workspace_button.hide()
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
        self.video_workflow.addItem('模式 1 · 整期视频','complete')
        self.video_workflow.addItem('模式 2 · 旧版分项素材','materials')
        self.video_workflow.setCurrentIndex(max(0,self.video_workflow.findData(self.settings.video_workflow)))
        output_form.addRow('视频输出方式',self.video_workflow)
        self.settings_tabs.addTab(output_options,'视频输出')
        from ui.update_panel import UpdatePanel
        self.updates=UpdatePanel(self)
        self.preferences.about_layout.addRow(self.updates)
        self.roundup.request_voice_setup=self.show_voice_settings
        self.roundup.voice_setup_busy=lambda:self.voice_setup.worker is not None
        for index in (2,4,5):self.preferences.nav.item(index).setHidden(True)
        for component in (self.preferences,self.rules):
            component.setMinimumSize(0,0)
            for box in component.findChildren(QDialogButtonBox):box.hide()
        actions=QHBoxLayout();self.apply_button=self.button('production.apply_settings',self.apply_preferences,actions,True)
        terms=self.button('术语词典',self.open_terms,actions)
        rules=self.button('production.apply_rules',self.apply_rules,actions,True)
        voice=self.button('production.save_voice',self.roundup._save_voice,actions,True)
        def show_actions(index):
            self.apply_button.setVisible(index in (0,4));terms.setVisible(index==0)
            rules.setVisible(index==1);voice.setVisible(index==2)
        self.settings_tabs.currentChanged.connect(show_actions);show_actions(self.settings_tabs.currentIndex())
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
        self.roundup.draft_changed.connect(self._changed)
        self.news.changed.connect(self._changed);self.dashboard.settings_changed.connect(self._changed);self.feed_panel.feeds_changed.connect(self._changed)
        d=self.dashboard
        for control in (d.date_mode_combo,d.launch_range_combo):control.currentIndexChanged.connect(self._remember_source_dates)
        for control in (d.start_date_edit,d.end_date_edit,d.launch_start_date_edit,d.launch_end_date_edit):
            control.dateChanged.connect(self._remember_source_dates)
        self.news.changed.connect(self._sync_news_voice)
        self.news.article_changed.connect(self._sync_news_voice)
        self.news.oral_modified.connect(self._sync_edited_news_oral)
        self.news.narration_requested.connect(lambda:self.guard(self.propose_news_voice))
        self.news.retry_requested.connect(lambda:self.guard(self.retry_news_narrations))
        self.news.voice_requested.connect(self.open_news_voice)
        self.news.diagnostic_requested.connect(self.inspect_news_failure)
        self.news.image_requested.connect(lambda article,options:self.guard(lambda:self.start_news_image(article,options)))
        self.roundup.tts_state_changed.connect(lambda message:self.task_text.setText(message) if self.worker and self.worker_kind.startswith('voice_') else None)
        self.preferences.output_edit.editingFinished.connect(self._changed)
        self.roundup.segment_list.currentRowChanged.connect(self._roundup_voice_selection_changed)
        self.roundup.job_changed.connect(self._launch_detail)
        self.roundup.job_changed.connect(lambda:self._stop_preview() if self.roundup.worker is not None else None)
        self.voice_setup.changed.connect(self._poll)
        self.preferences.accepted.connect(lambda:self.guard(self.apply_preferences));self.rules.accepted.connect(lambda:self.guard(self.apply_rules))
        self.media.currentChanged.connect(self._media_changed)

    def _mode(self):
        self.mode=self.content.currentData();self.scripts.setCurrentIndex(1 if self.mode=='roundup' else 0)
        if not self._restoring and self.navigation.currentRow()==0 and self.materials.currentIndex()<2:
            self.materials.setCurrentIndex(self.content.currentIndex())
        if getattr(self,'_media_mode',None)!=self.mode:
            self._release_preview();self._frame_key=None;self._media_mode=self.mode
            self._preview_news_id=self._roundup_focus_id=None
        self.media.setTabText(0,'新闻配音' if self.mode=='news' else tr('production.roundup_voice'))
        self.media.setTabText(1,'画面素材')
        self.cards_mode_note.setVisible(self.mode=='roundup')
        materials_only=self.settings.video_workflow=='materials'
        if hasattr(self,'preview_timer'):self.preview_timer.setInterval(33 if materials_only else 16)
        if not materials_only and self.media.currentIndex()==1:self.media.setCurrentIndex(0)
        self.media.setTabVisible(1,materials_only)
        self.cards_export_button.setText('导出旧版画面素材（MOV）')
        self.cards_export_button.setVisible(materials_only);self.include_intro.setVisible(materials_only)
        self.complete_export_button.setText(('导出发射 MP4' if self.mode=='roundup' else '导出新闻透明 MOV')
                                           if materials_only else '导出本期视频')
        self.episode_preview_button.setVisible(not materials_only)
        self.speech_button.setText('确认并生成本条配音' if self.mode=='news' else tr('production.synthesize'))
        self.listen_button.setText('试听本条' if self.mode=='news' else tr('production.listen'))
        self.preview_button.setText('预览新闻画面' if self.mode=='news' else tr('production.preview'))
        self.preview_tasks.setVisible(self.mode=='roundup')
        self.voice_scope.setVisible(self.mode=='news')
        self.voice_segments.setPlaceholderText(tr('production.choose_news_for_voice') if self.mode=='news' else '')
        self.voice_image_use.setVisible(self.mode=='news')
        self.voice_image_menu.setVisible(self.mode=='news')
        self.roundup.export_controls.setVisible(materials_only and self.mode=='roundup')
        self.export_news_button.setVisible(self.mode=='news');self.news_formats.setVisible(self.mode=='news')
        self.export_hint.setText('按稿件勾选范围导出带配音的分项素材。' if materials_only else
            f"第 {self.episode['number']} 期 · {self.episode['date']} · 导出为带配音和字幕的 MP4")
        if self.navigation.currentRow()==2 and self.media.currentIndex()==1 and not self._restoring:self.guard(self.sync_current_cards)
        if self.navigation.currentRow()==3 and not self._restoring:self._refresh_files()
        self._refresh_visible_voice()
        self._header(check_saved=False)

    def _materials_changed(self,index):
        self.inspector.hide()
        if index in (0,1):self.content.setCurrentIndex(index)

    def _media_changed(self,index):
        self.inspector.hide()
        if index==0:self._refresh_visible_voice()
        if index!=0:self._release_preview()
        if index!=1:self.cards._stop_preview()
        if index==1 and not self._restoring:self.guard(self.sync_current_cards)
        if index==3:self.sync_download_tasks()

    def media_route(self,index):
        if index==1 and self.settings.video_workflow!='materials':index=0
        unchanged=self.navigation.currentRow()==2 and self.media.currentIndex()==index
        self.navigation.setCurrentRow(2);self.media.setCurrentIndex(index)
        if unchanged and index==0:self._refresh_visible_voice()
        if index==3:self.sync_download_tasks()

    def open_rules(self):
        self.show_settings();self.settings_tabs.setCurrentIndex(1)
        from services.prompt_manager import PROMPT_TYPES
        self.rules.type_list.setCurrentRow(PROMPT_TYPES.index('roundup') if self.mode=='roundup' else 0)

    def show_settings(self):
        self.navigation.blockSignals(True);self.navigation.setCurrentRow(-1);self.navigation.blockSignals(False)
        self.pages.setCurrentIndex(4)
        self._navigated(-1)
        self.page_title.setText(tr('settings'))

    def show_about(self):
        self.show_settings()
        self.settings_tabs.setCurrentIndex(0)
        self.preferences.nav.setCurrentRow(self.preferences.about_index)

    def _navigated(self,index):
        self.inspector.hide()
        if index!=2:
            self._release_preview();self.cards._stop_preview();self.intro._stop_preview()
        if index>=0:
            self._last_page=index;self.page_title.setText(self.navigation.item(index).text())
        if index==0 and not self._restoring and self.materials.currentIndex()<2:
            self.materials.setCurrentIndex(self.content.currentIndex())
        if index==1 and self.mode=='news' and not self._restoring:self.news.refresh_voice()
        if index==2:
            if self.media.currentIndex()==1 and not self._restoring:self.guard(self.sync_current_cards)
            else:self._refresh_visible_voice()
        if index==3:self._refresh_files();self._export_checks()
        self._workflow_navigation()

    def previous_step(self):
        index=self.navigation.currentRow()
        if index<0:self.navigation.setCurrentRow(self._last_page)
        elif index>0:self.navigation.setCurrentRow(index-1)

    def next_step(self):
        index=self.navigation.currentRow()
        if index==1:self.media.setCurrentIndex(0)
        if 0<=index<len(WORKFLOW_PAGES)-1:self.navigation.setCurrentRow(index+1)

    def _workflow_navigation(self):
        index=self.navigation.currentRow()
        self.content.setVisible(index>0)
        self.content.setAccessibleName(tr('production.content_mode'))
        self.previous_button.setVisible(index!=0)
        self.previous_button.setText(tr('production.return_to_workflow' if index<0 else 'production.previous_step'))
        has_next=0<=index<len(WORKFLOW_PAGES)-1
        self.next_button.setVisible(has_next)
        if has_next:self.next_button.setText(tr('production.next_step')+'  ·  '+self.navigation.item(index+1).text())

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
        if self.mode=='news':
            if index>=0:self.news.select_article(self.voice_segments.itemData(index))
            return
        self._stop_preview()
        identifier=self.voice_segments.itemData(index)
        row=next((n for n,s in enumerate(self.roundup.draft['segments']) if s['id']==identifier),-1)
        if row>=0 and row!=self.roundup.segment_list.currentRow():self.roundup.segment_list.setCurrentRow(row)

    def media_draft(self):
        if self.mode=='news':
            article=self.news.current_article()
            if article is not None and not self.news.is_selected(article):
                raise ValueError('本条未纳入当前期次，请检查稿件勾选与本期统计日期')
            draft=self.news.voice_draft(article)
            if draft is None:raise ValueError('请先在稿件页选择一条新闻')
            return draft
        from services.production_export import selected_roundup
        return selected_roundup(self.roundup.draft)

    def _media_basis(self):
        if self.mode=='news':
            return self._news_media_basis(self.news.voice_draft(assets=False),self._news_titles())
        from services.production_export import selected_roundup
        return roundup.digest([selected_roundup(self.roundup.draft),self.roundup._voice_dirty])

    def _news_media_basis(self,draft,titles):
        try:asset_stamp=resource_path('news-images','manifest.json').stat().st_mtime_ns
        except OSError:asset_stamp=None
        segment=draft['segments'][0] if draft else {}
        screen={k:v for k,v in draft['screen_item'].items() if k not in ('image','image_error','slideshow','video')} if draft else {}
        return roundup.digest([segment,screen,titles,self.settings.video_workflow,self._launch_count(),asset_stamp,self._edition_revision,
            [draft.get(key) for key in ('tts','pronunciations','aligner','caption_overlay')] if draft else None])

    def synthesize_media(self):
        return self.news_audio() if self.mode=='news' else self.roundup._audio_all()

    def _news_titles(self):
        return [self.news._title(article) for article in self.news.selected_articles()]

    def _launch_count(self):
        return sum(row['included'] for row in self.roundup.draft['selection'])

    def _sync_news_voice(self):
        if self.mode!='news' or self._restoring:return
        if self.navigation.currentRow()==2 and self.media.currentIndex()==0:
            self._voice_refresh()
            return
        selected=self.news.is_selected(self.news.current_article())
        self._set_voice_text(self.news.oral.toPlainText() if selected else '')

    def _set_voice_text(self,text):
        self._voice_loading=True
        if self.voice_text.toPlainText()!=text:self.voice_text.setPlainText(text)
        self._voice_loading=False

    def _news_voice_refresh(self):
        from models.news import news_id
        from services import news_voice
        articles=self.news.selected_articles()
        selected=self.news.is_selected(self.news.current_article())
        if (articles and not selected and not self._restoring and
                self.navigation.currentRow()==2 and self.media.currentIndex()==0):
            selected=self.news.select_article(news_id(articles[0].news.key))
        draft=self.news.voice_draft(assets=False) if selected else None
        choices=[(self.news._title(article),news_id(article.news.key)) for article in articles]
        self.voice_scope.setText(tr('production.checked_news_for_voice',count=len(articles)))
        self.voice_segments.blockSignals(True)
        if getattr(self,'_news_voice_choices',None)!=choices:
            self.voice_segments.clear()
            for title,identifier in choices:self.voice_segments.addItem(title,identifier)
            self._news_voice_choices=choices
        self.voice_segments.setCurrentIndex(self.voice_segments.findData(draft['news_id']) if draft else -1)
        self.voice_segments.blockSignals(False)
        self._set_voice_text(draft['segments'][0]['spoken'] if draft else '')
        self.voice_text.setPlaceholderText('先在稿件页生成或填写新闻口播稿，摘要不会自动配音')
        self.play_state.setText(self.news.voice_hint.text() if draft else '请选择新闻')
        self.play_state.setToolTip(self.news.final_input.toPlainText())
        self.voice_status.setText(tr('production.voice_ready' if self._voice_configured() else 'production.voice_missing'))
        from services import news_images
        try:
            row=news_images.load()['news'].get(draft['news_id'],{}) if draft else {}
            self.voice_image_use.setEnabled(draft is not None)
            self.voice_image_use.blockSignals(True);self.voice_image_use.setChecked(row.get('use_image',True));self.voice_image_use.blockSignals(False)
        except (OSError,ValueError,KeyError,TypeError):self.voice_image_use.setEnabled(False)
        self.voice_image_menu.setEnabled(draft is not None and self.image_worker is None)
        titles=[title for title,_ in choices]
        key=self._news_media_basis(draft,titles)
        if not getattr(self,'_preview_syncing',False):
            identifier=draft['news_id'] if draft else None
            if identifier!=getattr(self,'_preview_news_id',None):
                self._preview_news_id=identifier;self._preview_select_pending=True
            if not draft or key!=getattr(self,'_frame_key',None):
                if not self._poster_request or self._poster_request[0]!=key:self._release_preview()
        if draft and self.frame.isVisible() and key!=getattr(self,'_frame_key',None):
            if not self._poster_request:
                index=draft['screen_item']['ordinal']-1
                previous=self.news.voice_draft(articles[index-1],assets=False) if index else None
                position=0 if self._preview_select_pending else self.preview_slider.value()/1000
                self._poster_request=(key,deepcopy(draft),deepcopy(previous),titles,self._launch_count(),
                                     self.settings.video_workflow,position)
                self.frame.set_image(None);self.frame.setText('正在准备本条新闻画面…')
                self.preview_clock.setText('正在加载画面素材')
                self.preview_slider.setEnabled(False)
                if not self._poster_timer.isActive():self._poster_timer.start()
        elif not draft:
            self.frame.set_image(None);self.frame.setText(tr('production.choose_news_for_voice'))
            self.preview_slider.setEnabled(False)
        self.preview_play.setEnabled(draft is not None and not self._poster_request)

    def _sync_edited_news_oral(self,text):
        if self.mode!='news' or not self.news.is_selected(self.news.current_article()):return
        self._set_voice_text(text)
        if self._audio_playback or self.preview_timer.isActive():self._release_preview()
        draft=self.news.voices.get(news_id(self.news.current_article().news.key))
        if draft and draft['segments'][0].get('audio'):self.play_state.setText('旧配音已过期，请重新生成')

    @staticmethod
    def _render_news_poster(request):
        from tools import news_visuals
        from services import news_voice
        _,draft,previous,titles,launch_count,workflow,position=request
        renderer=news_voice if workflow=='materials' else news_visuals
        populate_voice_assets(draft)
        if previous and renderer is news_visuals:populate_voice_assets(previous)
        timeline,frames=ProductionWorkspace._build_news_frames(draft,renderer,previous,titles,launch_count)
        position=min(position,round(timeline['duration_seconds']*1000)/1000)
        try:
            poster=renderer.frame_at(frames,position)
            if position>0 and 'previous_picture' in frames:
                poster=renderer.slide_from(frames,frames['previous_picture'],poster,position)
        finally:news_visuals.close_frames(frames)
        return timeline,frames,poster,position

    def _poll_poster(self):
        if self._poster_future is not None:
            key,future=self._poster_future
            if not future.done():return
            self._poster_future=None
            if self._poster_request and self._poster_request[0]==key:
                _,draft,*_=self._poster_request;self._poster_request=None
                try:timeline,frames,poster,position=future.result()
                except (ValueError,OSError,RuntimeError,KeyError,TypeError) as error:
                    self.frame.setText(str(error));self.preview_clock.clear()
                else:
                    self._adopt_news_frames(draft,key,timeline,frames)
                    self._show_preview_image(poster)
                    self._preview_select_pending=False
                    self.preview_slider.setEnabled(True)
                    self._update_news_clock(position)
                    row=timeline['segments'][0]
                    if draft['segments'][0].get('audio'):
                        self.play_state.setText('配音有效 · '+row['message'] if row['audio'] else '旧配音已过期，请重新生成')
                self.preview_play.setEnabled(True)
        if self._poster_request:
            self._poster_future=(self._poster_request[0],_BACKGROUND.submit(self._render_news_poster,self._poster_request))
        else:self._poster_timer.stop()

    def _preview_news(self, *, reuse=False):
        from services import news_voice
        from tools import news_visuals
        renderer=news_voice if self.settings.video_workflow=='materials' else news_visuals
        if not reuse or getattr(self,'_news_frames',None) is None:
            draft=self.news.voice_draft(assets=False);key=self._news_media_basis(draft,self._news_titles())
            if not draft or not self.news.is_selected(self.news.current_article()):
                raise ValueError('请在稿件页勾选本条新闻')
            if getattr(self,'_frame_key',None)!=key:
                if self._poster_request:self._poster_request=None
                populate_voice_assets(draft)
                self._prepare_news_frames(draft,key,renderer)
        if getattr(self,'_preview_select_pending',False):
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(0);self.preview_slider.blockSignals(False);self._preview_select_pending=False
        time=self.preview_slider.value()/1000;image=renderer.frame_at(self._news_frames,time)
        # A still preview at the start should identify the selected article.
        if renderer is news_visuals and 'previous_picture' in self._news_frames and (time>0 or self.preview_timer.isActive()):
            image=renderer.slide_from(self._news_frames,self._news_frames['previous_picture'],image,time)
        self._show_preview_image(image)
        self.preview_slider.setEnabled(True);self._update_news_clock(time)

    def _request_visible_preview(self):
        if self.mode=='news':self._news_voice_refresh()
        else:self.preview_roundup()

    def _update_news_clock(self,time):
        row=self._preview_timeline['segments'][0]
        cue=next((u['screen'] for u in row['units'] if u['global_start_seconds']<=time<u['global_end_seconds']),'')
        mode='播放器时间' if getattr(self,'_audio_playback',False) else '无声预览时间'
        message=row['message'] if self._news_has_spoken else '尚未生成口播稿；可先预览画面，再到稿件页生成或填写口播'
        self.preview_clock.setWordWrap(True);self.preview_clock.setText(mode+f" · {time:.1f} / {self._preview_timeline['duration_seconds']:.1f} 秒 · "+message+('\n字幕：'+cue if cue else ''))

    def _prepare_news_frames(self,draft,key,renderer):
        from services import news_voice
        self._close_news_frames()
        index=draft['screen_item']['ordinal']-1
        before=self.news.voice_draft(self.news.selected_articles()[index-1]) if index and renderer is not news_voice else None
        timeline,frames=self._build_news_frames(draft,renderer,before,self._news_titles(),self._launch_count())
        self._adopt_news_frames(draft,key,timeline,frames)

    @staticmethod
    def _build_news_frames(draft,renderer,before,titles,launch_count):
        from services import news_voice
        from services.roundup_sync import build_timeline
        timeline=build_timeline(draft)
        if renderer is news_voice:
            frames=renderer.prepare_frames(draft,timeline=timeline)
        else:
            index=draft['screen_item']['ordinal']-1
            frames=renderer.prepare_frames(draft,titles,index,timeline=timeline,launch_count=launch_count,entering=index==0,preview=True)
            if before:
                clock=build_timeline(before)
                previous=renderer.prepare_frames(before,titles,index-1,timeline=clock,
                    launch_count=launch_count,entering=index==1,preview=True)
                try:frames['previous_picture']=renderer.frame_at(previous,clock['duration_seconds']-1/60)
                finally:renderer.close_frames(previous)
        return timeline,frames

    def _adopt_news_frames(self,draft,key,timeline,frames):
        self._preview_timeline=timeline;self._news_frames=frames
        self._news_has_spoken=bool(draft['segments'][0]['spoken'].strip())
        self._timeline_key=key;self._frame_key=key
        self.preview_slider.blockSignals(True);self.preview_slider.setRange(0,round(self._preview_timeline['duration_seconds']*1000));self.preview_slider.blockSignals(False)
        if self._preview_select_pending:
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(0);self.preview_slider.blockSignals(False)

    def _show_preview_image(self,image):
        if self.frame.image is not None and image is getattr(self,'_preview_image',None):return
        qimage=QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888).copy()
        self.frame.set_image(QPixmap.fromImage(qimage))
        self._preview_image=image

    def propose_news_voice(self):
        from services import news_voice
        draft=self.news.voice_draft()
        if draft is None:raise ValueError('请先选择新闻')
        article=deepcopy(self.news.current_article());settings=self.current_settings()
        events={article.news.key:deepcopy(self.news.editorial_event(article))}
        self._news_proposal_basis=draft['segments'][0]['spoken']
        self._news_job('voice_propose',lambda _data,token:news_voice.propose_missing([article],settings,token,set(),events=events),deepcopy(draft))

    def retry_news_narrations(self):
        from services import news_voice
        from services import news_editorial
        from services.weekly_report import prepared_scope
        articles=deepcopy(self.news.failed_articles())
        if not articles:return
        settings=self.current_settings()
        events={a.news.key:deepcopy(self.news.editorial_event(a)) for a in articles}
        source_events=deepcopy((self.news.bundle or {}).get('news_events',{}))
        source_rows={a.news.key:deepcopy(a) for a in self.news._rows('sources')
                     if any(a.news.key in row.get('related',[]) for row in events.values())}
        fetch={a.news.key for a in articles if self.news.narration_failures[news_id(a.news.key)].get('状态') in ('待补资料','飞后结果待补')}
        cutoff=news_editorial.cutoff_for(prepared_scope(settings)['news'],settings.display_timezone)
        def retry(data,token):
            refreshed={}
            for article in data:
                if token.is_cancelled:break
                key=article.news.key;event=events[key]
                if key not in fetch or any(row.get('manual') for row in article.evidence):continue
                related=[source_rows[key] for key in event.get('related',[]) if key in source_rows]
                try:
                    if missing:=event.get('missing_sources'):
                        for row in related:
                            if row.news.key in missing:
                                refreshed.update(news_editorial.retry_material(row,[],source_events.get(row.news.key,{}),token,cutoff))
                        components=[refreshed.get(row.news.key,row) for row in related if row.news.key in event['merged_checks']]
                        preview=news_editorial.merge_preview(components,source_events)
                        event['missing_sources']=preview['missing_sources']
                        event['conflicts']=preview['conflicts']
                        if preview['spoken']:
                            article.body=preview['spoken'];article.evidence=preview['evidence']
                            refreshed[key]=article
                    else:refreshed.update(news_editorial.retry_material(article,related if event.get('phase')=='pending' else [],
                                                                   event,token,parse_datetime(event.get('cutoff')) or cutoff))
                except TaskCancelledError:break
            results=news_voice.propose_missing([refreshed.get(a.news.key,a) for a in data],settings,token,set(),events=events,cutoff=cutoff)
            return (*results,refreshed,{key:row for key,row in events.items() if row})
        self._news_job('narration_retry',retry,articles)

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
        news_voice.approve(draft);self._news_play_after=play
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

    def edit_episode(self):
        from services.roundup_audio import speech_text
        from ui.widgets.date_range import date_edit
        if self.busy():raise ValueError(tr('production.conflict'))
        dialog=QDialog(self);dialog.setWindowTitle('本期设置');dialog.resize(660,620)
        form=QFormLayout(dialog);number=QSpinBox();number.setRange(1,999999);number.setValue(self.episode['number'])
        number.setObjectName('EditionNumber')
        name=QLineEdit(episode_name(self.episode));name.setObjectName('EpisodeName')
        name.setMaxLength(EPISODE_NAME_LIMIT);name.setPlaceholderText(DEFAULT_EPISODE_NAME)
        published=QLineEdit(self.episode['date']);published.setPlaceholderText('YYYY-MM-DD')
        launch=self.roundup.draft;settings=self.current_settings()
        start,end=self.episode.get('review_range') or ([launch['start'],launch['end']] if launch['selection'] else
            [settings.start_date,settings.end_date])
        first,last=date_edit(start,-7),date_edit(end,-1)
        first.setObjectName('EditionRangeStart');last.setObjectName('EditionRangeEnd')
        dates=QHBoxLayout();dates.addWidget(first);dates.addWidget(QLabel('至'));dates.addWidget(last)
        opening=QPlainTextEdit(self.episode['opening']);opening.setMaximumHeight(120)
        final=QLabel();final.setWordWrap(True);final.setTextFormat(Qt.TextFormat.PlainText)
        error=QLabel();error.setWordWrap(True)
        for label,control in (('期号',number),('播报日期（北京时间）',published),('开场口播',opening),('最终朗读',final)):
            form.addRow(label,control)
        form.insertRow(2,'统计日期（北京时间）',dates)
        form.insertRow(0,'周报名称',name)
        music=MusicPlaylist(self.episode,dialog);form.addRow('背景音乐',music)
        next_edition=False
        def values():
            return validate_episode(dict(name=name.text().strip() or DEFAULT_EPISODE_NAME,
                number=number.value(),date=published.text().strip(),opening=opening.toPlainText().strip(),
                review_range=[edit.date().toString('yyyy-MM-dd') for edit in (first,last)],
                **music.values()))
        def refresh():
            try:
                value=values()
                default=opening_text(value,launch if any(row['included'] for row in launch['selection']) else None,
                                     any(includes_news(value,a) for a in self.news.selected_articles()))
                opening.setPlaceholderText(default)
                final.setText(speech_text(value['opening'] or default,self.roundup.voice_settings()['pronunciations']))
                error.clear()
            except ValueError as issue:error.setText(str(issue))
        for signal in (name.textChanged,number.valueChanged,published.textChanged,first.dateChanged,last.dateChanged,opening.textChanged):
            signal.connect(refresh)
        next_button=QPushButton('新建下一期')
        def advance():
            nonlocal next_edition
            next_edition=True
            new=new_episode(min(999999,self.episode['number']+1))
            number.setValue(new['number']);published.setText(new['date']);opening.clear()
            day=QDate.fromString(new['date'],'yyyy-MM-dd')
            first.setDate(day.addDays(-7));last.setDate(day.addDays(-1))
            next_button.setEnabled(False)
        next_button.clicked.connect(advance);form.addRow(next_button);form.addRow(error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        def save():
            try:value=values()
            except ValueError as issue:error.setText(str(issue));return
            changed=value['review_range']!=[launch['start'],launch['end']] or launch.get('timezone')!='Asia/Shanghai'
            if changed or next_edition:self.checkpoint()
            self.episode=value
            self._edition_revision+=1
            self.music_panel.set_edition(self.episode)
            self._sync_edition_dates()
            if changed:self.roundup._new_snapshot(self.roundup.collection)
            dialog.accept()
        buttons.accepted.connect(save);refresh()
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.news._update_script_rows()
            self._mode();self._changed();self._poll()

    def export_complete(self,*,preview=False):
        from services.episode import make_video
        from services.voice_profiles import configured
        from services.roundup_audio import current_audio
        parts=self.settings.video_workflow=='materials'
        if parts and preview:return
        launch=self.roundup.draft
        if (not parts or self.mode=='roundup') and any(row['included'] for row in launch['selection']):
            self.roundup._sync(validate=False)
        else:launch=None
        articles=[a for a in self.news.selected_articles() if not self.news.blocked(a)] if not parts or self.mode=='news' else []
        news=[self.news.voice_draft(article) for article in articles]
        if not launch and not news:raise ValueError('请先勾选可制作的内容，待补资料可在稿件页处理')
        voice=self.roundup.voice_settings()
        existing=([launch] if launch else [])+news
        if not configured(voice['tts']) and (not parts or any(not d['segments'] or any(
                not current_audio(d,s) for s in d['segments']) for d in existing)):
            self.show_voice_settings();self.voice_setup.status.setText('请配置语音环境后导出本期内容');return
        output=str(resource_path('cache','episode-previews')) if preview else self.preferences.output_edit.text().strip()
        if not output:raise ValueError('请先选择输出目录')
        linked=bool(self.episode.get('review_range'))
        payload=dict(episode=self.episode,launch=launch,articles=articles,news=news,voice=voice,parts=parts,
                     preview=preview,
                     intro=str(self.intro.result_path) if self.intro.result_path and not parts and not linked else None,
                     intro_options=self.intro.options() if not parts and (self.intro.template.is_file() or linked and self.intro.result_path) else None,
                     scope=self.export_scope(self.mode if parts else 'complete'))
        payload['news_basis']={d['news_id']:d['segments'][0]['spoken'] for d in news}
        payload['launch_basis']=roundup.digest(launch['segments']) if launch else None
        settings=self.current_settings()
        self._news_job('complete_video',lambda data,token:make_video(data,settings,output,self.roundup.tts_provider,token,
            lambda message,done,total:self.worker.progress.emit(message,done,total)),deepcopy(payload))
        self.worker.progress.connect(self._export_progress)

    def _export_progress(self,message,done,total):
        self._export_activity=message
        self.task_text.setText(message);self.task_progress.setRange(0,max(1,round(total*100)))
        self.task_progress.setValue(round(done*100))

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

    def _roundup_voice_selection_changed(self, _row):
        if self.mode!='roundup':return
        if not getattr(self,'_preview_syncing',False):self._preview_select_pending=True
        self._refresh_visible_voice()

    def _refresh_visible_voice(self):
        if not self._restoring and self.navigation.currentRow()==2 and self.media.currentIndex()==0:
            self._voice_refresh()

    def _voice_refresh(self):
        if not hasattr(self,'voice_segments'):return
        if self.mode=='news':return self._news_voice_refresh()
        self._news_voice_choices=None
        from services.production_export import roundup_scope
        r=self.roundup;index=r.segment_list.currentRow();view,scope_issue=roundup_scope(r.draft)
        segments=view['segments'];allowed={s['id'] for s in segments}
        name_ids={identifier for segment in segments for identifier in segment['task_ids']}
        name_ids.update(row['id'] for row in view['selection'] if row['included'])
        task_names=roundup.task_names(r.draft,name_ids)
        self.voice_segments.blockSignals(True);self.voice_segments.clear()
        for segment in segments:
            names=[name for name in task_names if name['task_id'] in segment['task_ids']]
            self.voice_segments.addItem(roundup.segment_title(view,segment,names=names),segment['id'])
        current=r.draft['segments'][index]['id'] if index>=0 else None
        self.voice_segments.setCurrentIndex(self.voice_segments.findData(current));self.voice_segments.blockSignals(False)
        if current not in allowed:index=-1
        text=roundup.narration_text(r.draft['segments'][index]) if index>=0 else ''
        self._set_voice_text(text)
        self.voice_text.setPlaceholderText(tr('production.script_empty'));self.voice_text.setReadOnly(r.worker is not None)
        self.voice_status.setText(tr('production.voice_ready' if self._voice_configured() else 'production.voice_missing'))
        self.play_state.setText(scope_issue or (r.audio_state.text() if index>=0 else '请在稿件页勾选制作内容'))
        self.play_state.setToolTip(r.reading_preview.toPlainText())
        if not getattr(self,'_preview_syncing',False):
            identifier=r.draft['segments'][index]['id'] if index>=0 else None
            if identifier!=getattr(self,'_roundup_focus_id',None):
                self._roundup_focus_id=identifier;self._preview_select_pending=True
            self._stop_preview()
        names_by_id={name['task_id']:name['display_name'] for name in task_names}
        task_values=[(names_by_id[s['id']],s['id']) for s in view['selection'] if s['included']]
        if getattr(self,'_preview_task_values',None)!=task_values:
            self.preview_tasks.clear();self.preview_tasks.addItem('跳转到任务…',None)
            for title,identifier in task_values:self.preview_tasks.addItem(title,identifier)
            self._preview_task_values=task_values
        self.preview_play.setEnabled(bool(segments) and not scope_issue)
        if not segments or scope_issue:self.preview_slider.setEnabled(False)
        if getattr(self,'_preview_syncing',False):return
        if self.frame.image is not None and index>=0 and not scope_issue:
            if self._frame_key!=self._preview_key():self.preview_roundup()
        else:
            self.frame.set_image(None);self.frame.setText(scope_issue or tr('production.preview_hint'))

    def preview_roundup(self, *, reuse=False):
        if self.mode=='news':return self._preview_news(reuse=reuse)
        r=self.roundup
        from tools.roundup_visuals import prepare_frames,frame_at,shot_plan
        from services.roundup_sync import build_timeline,row_at
        from services.roundup_audio import segment_timing
        if not reuse or not getattr(self,'_timeline_key',None):
            view=self.media_draft()
            if not view['segments']:raise ValueError('当前没有勾选的发射内容')
            basis=roundup.digest([view,r._voice_dirty])
            if getattr(self,'_timeline_key',None)!=basis:
                if r._voice_dirty:
                    for s in view['segments']:s['audio']=None
                self._preview_view=view;self._preview_timeline=build_timeline(view);self._timeline_key=basis
                self._preview_shots=None
                self.preview_slider.blockSignals(True);self.preview_slider.setRange(0,round(self._preview_timeline['duration_seconds']*1000));self.preview_slider.blockSignals(False)
        if getattr(self,'_preview_select_pending',False):
            current=r.segment_list.currentRow()
            identifier=r.draft['segments'][current]['id'] if current>=0 else None
            rows=self._preview_timeline['segments']
            clock=next((row for row in rows if row['segment_id']==identifier),rows[0])
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(ceil(clock['start_seconds']*1000));self.preview_slider.blockSignals(False)
            self._preview_select_pending=False
        global_time=self.preview_slider.value()/1000
        clock=row_at(self._preview_timeline,global_time)
        original_index=next(n for n,s in enumerate(r.draft['segments']) if s['id']==clock['segment_id'])
        if original_index!=r.segment_list.currentRow():
            self._preview_syncing=True
            try:r.segment_list.setCurrentRow(original_index)
            finally:self._preview_syncing=False
        self._roundup_focus_id=clock['segment_id']
        key=getattr(self,'_frame_key',None)
        if not (reuse and key and getattr(self,'_preview_frames_segment',None)==clock['segment_id']):
            key=self._preview_key()
        if getattr(self,'_frame_key',None)!=key:
            index=next(n for n,s in enumerate(self._preview_view['segments']) if s['id']==clock['segment_id'])
            segment=self._preview_view['segments'][index]
            self._preview_timing=segment_timing(self._preview_view,segment)
            if not reuse or getattr(self,'_preview_shots',None) is None:
                self._preview_shots=shot_plan(self._preview_view,self._preview_timeline)
            self._preview_frames=prepare_frames(self._preview_view,segment,timeline=self._preview_timeline,
                shot_table=self._preview_shots,
                entering=index==0 and self.settings.video_workflow=='complete',preview=True)
            self._preview_frames_segment=clock['segment_id']
        elapsed=max(0,global_time-clock['start_seconds']);duration=self._preview_timeline['duration_seconds']
        image=frame_at(self._preview_frames,elapsed)
        self._roundup_list_scene=self._preview_frames['list_scene']
        if elapsed==0 and self._preview_frames['entering'] and not self.preview_timer.isActive():
            image=self._roundup_list_scene['image']
        self._show_preview_image(image)
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
        if hasattr(self,'preview_bed_player'):self.preview_bed_player.pause()
        self.music_panel.stop()
        self.roundup._stop_playback()

    def _audio_position_changed(self, _position):
        if (getattr(self,'_audio_playback',False) and getattr(self,'_audio_ready',False)
                and not getattr(self,'_audio_finished',False)
                and not self.preview_timer.isActive() and not self.preview_slider.isSliderDown() and self.frame.isVisible()
                and getattr(self,'_timeline_key',None)==self._media_basis()):
            self._advance_preview()

    def _release_preview(self):
        self._poster_request=None
        self._stop_preview();self._audio_playback=False;self._audio_ready=False;self._audio_pending_position=None
        self._close_news_frames()
        if hasattr(self,'preview_player'):
            self.preview_player.stop();self.preview_player.setSource(QUrl())
        if hasattr(self,'preview_bed_player'):
            self.preview_bed_player.stop();self.preview_bed_player.setSource(QUrl())

    def _close_news_frames(self):
        if getattr(self,'_news_frames',None) is not None:
            from tools.news_visuals import close_frames
            close_frames(self._news_frames)
            self._news_frames=None
            self._frame_key=None

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
            self._sync_preview_bed()
            elapsed=self.preview_player.position()/1000+self._audio_offset
            if getattr(self,'_audition_end',None) is not None and elapsed>=self._audition_end:
                self._stop_preview();self._audition_end=None
        else:elapsed=time.monotonic()-self._preview_started
        self.preview_slider.blockSignals(True);self.preview_slider.setValue(round(elapsed*1000));self.preview_slider.blockSignals(False)
        self.preview_roundup(reuse=True)
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
                self._sync_preview_bed(round(target*1000))
            else:
                self._stop_preview();self._audio_playback=False
        self.preview_roundup()

    def _prepare_audio_preview(self):
        r=self.roundup
        if self.mode!='news':r._sync()
        target=self.preview_slider.value()/1000
        self._audio_target=target
        self.music_panel.stop()
        edition=deepcopy(self.episode)
        def prepare(data,token):
            from services.roundup_sync import build_timeline,timeline_audio,row_at,canonical_audio
            from services.episode_music import preview_bed
            timeline=build_timeline(data);clock=row_at(timeline,target)
            if timeline['all_audio_current']:
                path=timeline_audio(data,timeline,token);offset=0.;end=timeline['duration_seconds']
            else:
                if not clock['audio']:raise ValueError('本段尚无当前配音，可使用无音频预览')
                path=canonical_audio(clock['audio'],token);offset=clock['start_seconds'];end=clock['speech_end_seconds']
            bed=preview_bed(edition,timeline,token)
            return dict(path=str(path),bed=str(bed) if bed else '',offset=offset,end=end,basis=roundup.digest(data))
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
        self._voice_loaded=self.preview_player.source()==url
        self._bed_loaded=not result.get('bed')
        self._load_preview_bed(result.get('bed'))
        self._update_preview_levels()
        if not self._voice_loaded:self.preview_player.setSource(url)
        self._start_prepared_audio()
        self.preview_timer.start();self.preview_play.setText('暂停播放')

    def _load_preview_bed(self,path):
        from PySide6.QtMultimedia import QAudioOutput,QMediaPlayer
        if not path:
            if hasattr(self,'preview_bed_player'):self.preview_bed_player.setSource(QUrl())
            return
        if not hasattr(self,'preview_bed_player'):
            self.preview_bed_player=QMediaPlayer(self);self.preview_bed_output=QAudioOutput(self)
            self.preview_bed_player.setAudioOutput(self.preview_bed_output)
            self.preview_bed_player.mediaStatusChanged.connect(self._bed_media_status)
            self.preview_bed_player.errorOccurred.connect(lambda *_:self._audio_preview_error(self.preview_bed_player))
        bed=self.preview_bed_player;url=QUrl.fromLocalFile(path)
        if bed.source()==url:self._bed_loaded=True
        else:bed.setSource(url)

    def _update_preview_levels(self):
        options=music_settings(self.episode)
        if hasattr(self,'preview_audio_output'):self.preview_audio_output.setVolume(options['voice_volume']/100)
        if hasattr(self,'preview_bed_output'):self.preview_bed_output.setVolume(options['music_volume']/100)

    def _bed_media_status(self,status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia):
            self._bed_loaded=True;self._start_prepared_audio()

    def _start_prepared_audio(self):
        if self._audio_pending_position is None or not (getattr(self,'_voice_loaded',False) and getattr(self,'_bed_loaded',True)):
            return
        position=self._audio_pending_position;self._audio_pending_position=None
        self.preview_player.setPosition(position);self._audio_ready=True
        if self._audio_wants_play:self.preview_player.play()
        self._sync_preview_bed(position+round(self._audio_offset*1000))

    def _sync_preview_bed(self,position=None):
        from PySide6.QtMultimedia import QMediaPlayer
        bed=getattr(self,'preview_bed_player',None)
        if not bed or bed.source().isEmpty():return
        chosen=position if position is not None else self.preview_player.position()+round(self._audio_offset*1000)
        if position is not None or abs(bed.position()-chosen)>140:bed.setPosition(chosen)
        if self.preview_player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            if bed.playbackState()!=QMediaPlayer.PlaybackState.PlayingState:bed.play()
        elif bed.playbackState()==QMediaPlayer.PlaybackState.PlayingState:bed.pause()

    def _audio_media_status(self,status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self._audio_pending_position is not None:
            self._voice_loaded=True;self._start_prepared_audio()
        elif status==QMediaPlayer.MediaStatus.EndOfMedia:
            self._audio_finished=True
            self.preview_slider.blockSignals(True);self.preview_slider.setValue(round(self._audio_end*1000));self.preview_slider.blockSignals(False)
            self.preview_roundup();self._stop_preview()

    def _audio_preview_error(self,player=None):
        self._stop_preview();self._audio_playback=False
        self.task_text.setText('音频播放失败：'+(player or self.preview_player).errorString())

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

    def _browse_launch_preview(self,point):
        scene=getattr(self,'_roundup_list_scene',None)
        if self.mode!='roundup' or scene is None:return
        from tools import roundup_visuals,video_cards
        x,y=point.x()*video_cards.W,point.y()*video_cards.H
        identifier=next((task for (left,top,right,bottom),task in scene['targets']
                         if left<=x<=right and top<=y<=bottom),None)
        if identifier is None:return
        self._stop_preview()
        draft=self._preview_view
        selection=roundup_visuals.scene_plan(draft,dict(task_ids=[identifier]))[0]
        self._roundup_list_scene=roundup_visuals.render_scene(draft,selection)
        self._show_preview_image(self._roundup_list_scene['image'])

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
        if self.mode=='news':
            draft=self.news.voice_draft()
            if draft is None:raise ValueError('请先选择新闻')
            if self.busy():raise ValueError(tr('production.conflict'))
            legacy=self.settings.video_workflow=='materials'
            dialog=QDialog(self);dialog.setWindowTitle('新闻条时间' if legacy else '新闻段落时间');form=QFormLayout(dialog)
            fields={}
            options=[('before_voice','入场完成后等待（秒）'),('after_voice','配音结束后保持（秒）')]
            if legacy:options.append(('summary_close_before_end','摘要收起时长（秒，0为自动；读完时收好）'))
            for key,label in options:
                value=QDoubleSpinBox();value.setRange(0,60 if key=='summary_close_before_end' else 2);value.setSingleStep(.1)
                value.setValue(draft.get('animation_gaps',{}).get(key,0 if key=='summary_close_before_end' else .2));fields[key]=value;form.addRow(label,value)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
            buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                gaps=draft.setdefault('animation_gaps',{})
                for key,value in fields.items():
                    if key=='summary_close_before_end' and value.value()==0:gaps.pop(key,None)
                    else:gaps[key]=value.value()
                self._stop_preview();self._frame_key=None;self._changed()
            return
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
        r._start_job('align',align,self.media_draft())

    def _preview_key(self):
        if self.mode=='news':return self._media_basis()
        draft=self.roundup.draft;segment=draft['segments'][self.roundup.segment_list.currentRow()]
        from translator.dictionary import load_dictionary
        entries=load_dictionary()
        return roundup.digest([draft['id'],segment,draft['segments'],draft['snapshot'],draft['selection'],
                               draft['tts'],draft['pronunciations'],draft.get('aligner'),draft['pause_seconds'],
                               draft.get('visuals'),draft.get('closing_hold_seconds'),self.roundup._voice_dirty,
                               self.settings.video_workflow,
                               [roundup.display_names(i,entries) for i in draft['snapshot']['items']]])

    def current_settings(self):
        settings=self.dashboard.settings_snapshot(replace(self.settings,output_dir=self.preferences.output_edit.text().strip(),
         export_markdown=self.preferences.markdown_check.isChecked(),export_word=self.preferences.word_check.isChecked(),
         export_premiere=self.preferences.premiere_check.isChecked(),export_sqlite=self.preferences.sqlite_check.isChecked()))
        if span:=self.episode.get('review_range'):
            start,end=span
            return replace(settings,date_mode='custom',start_date=start,end_date=end,launch_range='custom',
                           launch_start_date=start,launch_end_date=end,display_timezone='Asia/Shanghai')
        return settings

    def _sync_edition_dates(self):
        span=self.episode.get('review_range')
        dashboard,r=self.dashboard,self.roundup
        combos=(dashboard.date_mode_combo,dashboard.launch_range_combo)
        fields=(dashboard.start_date_edit,dashboard.end_date_edit,dashboard.launch_start_date_edit,dashboard.launch_end_date_edit)
        dates=(*fields,r.start,r.end,self.intro.date,self.intro.end_date)
        for control in (*combos,*dates):
            control.setEnabled(not span)
            if span:control.setToolTip('在「本期设置」修改统一统计日期')
        r.recent_dates.setEnabled(not span)
        if not span:return
        was_restoring=self._restoring;self._restoring=True
        was_loading=r._loading;r._loading=True;r.range_timer.stop()
        try:
            for combo in combos:combo.setCurrentIndex(combo.findData('custom'))
            for control,value in zip(dates,span*4):
                control.setDate(QDate.fromString(value,'yyyy-MM-dd'))
            r.settings=replace(r.settings,display_timezone='Asia/Shanghai')
        finally:
            r._loading=was_loading;self._restoring=was_restoring

    def _save_runtime(self):
        if not self.dashboard.date_ranges_valid():raise ValueError(tr('date.range.invalid'))
        self.settings=self.current_settings();save_settings(self.settings);apply_settings(self.settings)
        self.feed_panel.save_state();self.roundup.settings=self.settings

    def _news_job(self,kind,action,payload):
        if any(k not in ('maintenance','news_image','update') for k,*_ in self.task_rows()):raise ValueError(tr('production.conflict'))
        self._stop_preview()
        self._news_export_scope=self.export_scope('news')
        self._save_runtime();self.worker_kind=kind
        self._news_phase=''
        if kind=='complete_video':self._export_activity='正在准备本期视频'
        if kind in ('collect','write','narration_retry','voice_audio','complete_video'):
            path=self._checkpoint_path()
            before=self.snapshot()
            output=Path(self.settings.output_dir)
            def run(data,token):
                write_json(path,before,compact=True)
                token.check()
                value=action(data,token)
                if kind in ('collect','write') and isinstance(value,dict) and not token.is_cancelled:
                    try:value['_launch_collection']=load_existing(output)
                    except (OSError,ValueError,TypeError):pass
                return value
        else:run=action
        self.worker=RoundupWorker(run,payload,self)
        self.worker.news_context=(workspace_path(),self._edition_revision,self.news.bundle,self.news.selection_revision)
        self.worker.finished.connect(self._news_finished)
        if kind in ('collect','write'):self.worker.progress.connect(self._show_news_phase)
        self.news.set_running(kind!='export')
        self.worker.start();self._poll()

    def _show_news_phase(self,message,*_):
        if self.sender() is self.worker and self.worker and not self.worker.token.is_cancelled:
            self._news_phase=message;self.task_text.setText(message)

    def _report_news_phase(self,message):
        self.worker.progress.emit(message,0,0)

    def start_news_image(self,article,options):
        if self.image_worker:raise ValueError('正在处理新闻素材，可等待或在底部取消')
        from models.news import news_id
        from services.news_images import acquire, acquire_slideshow, set_slideshow
        if options.get('video'):
            from services.news_video import acquire as acquire_video
            action=lambda data,token:acquire_video(data,options['video'],token)
        elif options.get('remove_video'):
            from services.news_video import remove
            action=lambda data,token:remove(data)
        elif options.get('trim'):
            from services.news_video import trim
            action=lambda data,token:trim(news_id(data.news.key),*options['trim'])
        elif 'files' in options:action=lambda data,token:acquire_slideshow(data,options['files'],token)
        elif 'slideshow' in options:action=lambda data,token:set_slideshow(data,options['slideshow'])
        else:action=lambda data,token:acquire(data,token,**options)
        self.image_worker=RoundupWorker(action,deepcopy(article),self)
        self.image_worker.video=bool(options.get('video') or options.get('remove_video') or options.get('trim'))
        self.image_worker.finished.connect(self._news_image_finished);self.image_worker.start();self._poll()

    def _news_image_finished(self):
        worker=self.image_worker;self.image_worker=None
        self.news.set_article_images(worker.payload);self._frame_key=None
        if self.mode=='news':self._refresh_visible_voice()
        if worker.video:
            self.task_text.setText('视频素材处理失败：'+worker.error if worker.error else
                '已取消新闻视频处理' if worker.cancelled else '新闻视频素材已更新')
        else:
            self.task_text.setText('图片处理失败：'+worker.error if worker.error else '已取消图片下载，正式图片保留' if worker.cancelled else '图片处理完成；未找到配图时可导入本地图片')
        worker.deleteLater();self._changed();self._poll()

    def start_news_collect(self):
        if self.news.bundle and QMessageBox.question(self,tr('production.materials'),tr('production.replace_collection'))!=QMessageBox.StandardButton.Yes:return
        feeds=self.feed_panel.enabled_feeds()
        self._news_job('collect',lambda data,token:prepare_news(feeds,token,collect_only=True,progress=self._report_news_phase),{})

    def start_news_write(self):
        from services.news_voice import has_existing_spoken
        bundle=self.news.selected_bundle(for_write=True);feeds=self.feed_panel.enabled_feeds()
        skip={identifier for identifier,draft in self.news.voices.items() if has_existing_spoken(draft)}
        skip.update(identifier for identifier,row in self.news.narration_failures.items() if row.get('已跳过'))
        skip.update(news_id(key) for key in self.news.merged_members())
        bundle['editorial_protected']=[key for key,checked in self.news.script_preferences.items() if checked]
        bundle['editorial_merges']={key:row for key,row in bundle.get('news_events',{}).items() if 'merged_checks' in row}
        bundle['editorial_launches']=deepcopy(self.roundup.draft['snapshot']['items'])
        self._news_job('write',lambda data,token:prepare_news(feeds,token,prepared=data,narration_skip=skip,
            progress=self._report_news_phase),bundle)
        self.navigation.setCurrentRow(1)

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
        workspace,edition,bundle,revision=worker.news_context
        changed=bundle is not self.news.bundle or revision!=self.news.selection_revision
        if workspace!=workspace_path() or edition!=self._edition_revision or (kind in ('collect','write','narration_retry','complete_video') and changed):
            worker.deleteLater();self._poll()
            self.task_text.setText('期次或工作区已切换，已忽略旧任务的界面结果')
            return
        if kind=='complete_video':
            data=worker.payload
            for draft in data['news']:
                identifier=draft['news_id'];previous=self.news.voices.get(identifier)
                if previous and previous['segments'][0]['spoken']==data['news_basis'][identifier]:
                    self.news.voices[identifier]=draft
                    if draft['segments'][0]['spoken'].strip():self.news.narration_failures.pop(identifier,None)
            launch=data['launch'];current=self.roundup.draft
            if launch and launch['id']==current['id']:
                produced=data.get('produced_launch') or {}
                by_id={s['id']:s for s in produced.get('segments',[])}
                new_segments=roundup.digest(current['segments'])==data['launch_basis']
                if new_segments:
                    current['segments']=launch['segments']
                for s in current['segments']:
                    generated=by_id.get(s['id'])
                    if generated and roundup.narration_text(generated)==roundup.narration_text(s):
                        for key in ('audio','audio_error','alignment','alignment_error','sync_manual','units'):
                            if key in generated:s[key]=generated[key]
                if new_segments:self.roundup._show_segments(max(0,self.roundup.segment_list.currentRow()))
            result=worker.value or dict(paths=[],failed=[],cancelled=worker.cancelled)
            if data['preview'] and result['paths']:
                QDesktopServices.openUrl(QUrl.fromLocalFile(result['paths'][0]))
            elif not data['preview']:
                for path in result['paths']:self.add_result(path,data['scope'])
            if result['failed'] or result.get('alignment_pending'):
                self.record_diagnostic(dict(制作失败=result['failed'],字幕对齐=result.get('alignment_pending',[])))
            self._refresh_files();self._voice_refresh();self._changed();self._poll()
            if result['failed']:
                issue=result['failed'][0]
                status=f"{issue['title']} · {issue['reason'].splitlines()[0]}" if issue['reason'] else issue['title']
                if len(result['failed'])>1:status+=f" · 共 {len(result['failed'])} 项失败，查看诊断后可重试"
            elif result['cancelled']:status='已取消，可再次导出'
            else:
                status='本期预览已打开' if data['preview'] else '本期视频已导出' if not data['parts'] else f"已导出 {len(result['paths'])} 项素材"
                if result.get('alignment_pending'):status+=' · 部分字幕采用段落级时间'
            self.task_text.setText(worker.error or status)
            worker.deleteLater();return
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
            proposals,failures,states,refreshed,events=worker.value
            self.news.apply_retry_material(refreshed,events)
            self.news.apply_narrations(proposals,failures,states)
            self.news.script_included.update(a.news.key for a in self.news._rows('articles') if news_id(a.news.key) in proposals
                                             and self.news.script_preferences.get(a.news.key) is not False)
            self.news._update_script_rows()
            if failures:self.record_diagnostic(dict(口播失败=failures))
            self.task_text.setText('口播重试结束' if self.mode=='news' and self.navigation.currentRow()==1 else tr('ready'))
        elif kind=='export':
            for path in worker.value:self.add_result(path,self._news_export_scope)
            self._refresh_files();self.task_text.setText(tr('production.export_done'))
        elif kind=='import':
            self.task_text.setText('旧数据已备份并导入：'+worker.value+'；原目录保留。')
            if QMessageBox.question(self,'导入完成','是否打开导入后的工作区？原目录与备份均保留。')==QMessageBox.StandardButton.Yes:
                QTimer.singleShot(0,lambda path=worker.value:self.request_leave(path))
        else:
            failures=worker.value.get('narration_failures',{})
            collection=worker.value.pop('_launch_collection',None)
            self.news.adopt(worker.value,collected=kind=='collect')
            if failures:self.record_diagnostic(dict(口播失败=failures))
            self.task_text.setText('新闻稿件已处理' if kind=='write' else '新闻资料已采集')
            if collection is not None:self.roundup.collection=collection
        worker.deleteLater();self._changed();self._poll()
        if kind in ('write','narration_retry') and not worker.error and not worker.cancelled and failures:self.news.show_failures()

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
            items.append(dict(id='ll2:'+item['ll2_id'],title=names['title'],summary=name,source='Launch Library 2',display_record=names,
                published=names['net'],url=item.get('record_url') or item.get('ll2_source',{}).get('url') or 'https://ll.thespacedevs.com/2.3.0/launches/'+item['ll2_id']+'/',
                categories=[],video_type='launch',launch_context=item,
                launch=dict(orbit=names['orbit'],payload=names['payload'],site=names['location'],
                    time=format_datetime(names['net'],self.roundup.draft.get('timezone','Asia/Shanghai')),
                    status=launch_status_text(dict(id=names['status_id'],name=names['status_name'])),recovery=roundup.event_context(item,self.roundup.draft)['recovery'])))
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
            articles=[a for a in bundle.get('articles',[]) if a.news.key in self.news.script_included]
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
        if self.episode.get('review_range'):self.settings=replace(self.settings,display_timezone='Asia/Shanghai')
        save_settings(self.settings);apply_settings(self.settings);set_ui_language(self.settings.language)
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
        settings=self.current_settings()
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

    def content_state(self, *, for_poll=False):
        self.cards._store_editor()
        draft=self.roundup.draft
        if for_poll:
            # Generation replaces this audit record as a whole.
            writing_input=draft.get('writing_input')
            if writing_input is not self._writing_input_digest[0]:
                self._writing_input_digest=(writing_input,roundup.digest(writing_input))
            if writing_input is not None:draft=dict(draft,writing_input=self._writing_input_digest[1])
        return dict(version=1,daily=self._daily(),roundup=draft,roundup_controls=self._roundup_controls(),news=self.news.snapshot(for_poll=for_poll),material_edits=self.material_edits,
         cards=dict(data=self.cards.data,base=self._cards_base,source=str(self.cards.source) if self.cards.source else None,
          selected=sorted(self.cards.selected_ids()) if self.cards.data else [],seconds=self.cards.seconds.value(),encoding=self.cards.encoding.currentData(),
          sort_order=self.cards.sort_order.currentData()),
         intro=self.intro.options(),intro_file=str(self.intro.result_path) if self.intro.result_path else None,
         results=self.results,result_scopes=self.result_scopes,episode=self.episode)

    @staticmethod
    def _voice_state_hash(state):
        episode={key:value for key,value in state['episode'].items() if key not in ('music','music_settings')}
        return roundup.digest({**state,'episode':episode})

    def snapshot(self):
        state=self.content_state()
        writing_input=state['roundup'].get('writing_input')
        # The writing audit is replaced as a whole when a new draft is generated.
        detached={id(state['news']):state['news']}
        if writing_input is not None:detached[id(writing_input)]=writing_input
        value=deepcopy(state,detached)
        value['view']=dict(page=self.navigation.currentRow() if self.navigation.currentRow()>=0 else self._last_page,mode=self.mode,materials=self.materials.currentIndex(),media=self.media.currentIndex(),
         roundup_segment=self.roundup.segment_list.currentRow(),news_source=self.news.sources.currentRow(),news_article=self.news.articles.currentRow(),
         cards_row=self.cards.items.currentRow(),cards_frame=self.cards.slider.value(),
         spoken_scroll=self.roundup.spoken.verticalScrollBar().value(),screen_scroll=self.roundup.screen.verticalScrollBar().value(),
         narration_scroll=self.roundup.narration_edit.verticalScrollBar().value())
        article=self.news.current_article()
        if article:
            from models.news import news_id
            value['view']['news_article_id']=news_id(article.news.key)
        value['view'].update(self.news.view_state())
        if self._detail_id:value['view']['launch_detail_id']=self._detail_id
        return value

    def _restore(self,path=None):
        self._finish_recovery_write(wait=True)
        self._poster_request=None
        self._edition_revision=getattr(self,'_edition_revision',0)+1
        manual=resource_path('config','production-session.json');recovery=resource_path('cache','production-recovery.json')
        paths=[p for p in (manual,recovery) if p.exists()]
        if not paths and path is None:return
        path=path or max(paths,key=lambda p:p.stat().st_mtime_ns)
        try:
            value=validate_session(json.loads(path.read_text(encoding='utf-8')))
            self.episode=value.get('episode') or new_episode()
            self.music_panel.set_edition(self.episode)
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
            self._sync_edition_dates()
            self.news.restore(value.get('news',{}))
            self.results=value.get('results',[]);self.result_scopes=value.get('result_scopes',{})
            self.intro.result_scope=self.result_scopes.get(str(self.intro.result_path))
            view=value.get('view',{});self.materials.setCurrentIndex(view.get('materials',0))
            self.content.setCurrentIndex(1 if view.get('mode')=='roundup' else 0)
            self.navigation.setCurrentRow(max(0,min(3,view.get('page',0))));self.media.setCurrentIndex(view.get('media',0))
            r.segment_list.setCurrentRow(view.get('roundup_segment',0))
            self.news.restore_view(view)
            if 'news_articles_focus' not in view:self.news.select_article(view.get('news_article_id'))
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
        self._finish_recovery_write(wait=True)
        self.roundup._store_segment();value=self.snapshot();path=resource_path('config','production-session.json')
        if self._recovery_blocked and path.exists():
            from uuid import uuid4
            backup=path.with_name(path.stem+'.unreadable-'+uuid4().hex[:8]+'.json');backup.write_bytes(path.read_bytes())
        write_json(path,value,compact=True);write_json(resource_path('cache','production-recovery.json'),value,compact=True)
        self._saved_hash=roundup.digest({key:state for key,state in value.items() if key!='view'});self._recovery_blocked=False
        if not self.busy() and self.dashboard.date_ranges_valid():self._save_runtime()
        self._observed_hash=roundup.digest(self.content_state(for_poll=True))
        self._header(check_saved=False);self._update_save_tooltip(self._saved_hash)

    def preference_signature(self):
        return roundup.digest([self.video_workflow.currentData(),{k:v for k,v in vars(self.preferences.settings()).items() if k not in self._daily()}])

    def rules_signature(self):
        value=self.rules.settings()
        return roundup.digest({key:getattr(value,key) for key in ('summary_prompt','filter_prompt','title_prompt','roundup_prompt','prompt_preset')})

    @staticmethod
    def _checkpoint_path():
        from uuid import uuid4
        from datetime import datetime
        return resource_path('cache','production-versions',datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.json')

    def checkpoint(self):
        write_json(self._checkpoint_path(),self.snapshot(),compact=True)

    def open_edition(self):
        if self.busy():raise ValueError(tr('production.conflict'))
        directory=resource_path('cache','production-versions')
        if not directory.exists():directory=resource_path('output','production-versions')
        path,_=QFileDialog.getOpenFileName(self,tr('production.open_edition'),str(directory),'JSON (*.json)')
        if path:
            if roundup.digest(self.content_state())!=self._saved_hash:
                if QMessageBox.question(self,tr('production.unsaved'),tr('production.open_version_question'))!=QMessageBox.StandardButton.Yes:return
                self.checkpoint()
            self._restoring=True
            try:self._restore(Path(path))
            finally:self._restoring=False
            self._remember_source_dates();self._mode();self._header()

    def _changed(self,*_):
        if not self._restoring:self._header(check_saved=False)

    def _header(self,*,check_saved=True):
        if not hasattr(self,'period'):return
        s=self.current_settings();news=s.start_date+' - '+s.end_date if s.date_mode=='custom' else self.dashboard.date_mode_combo.currentText()
        r=self.roundup.draft
        self.name_update_button.setVisible(bool(r.get('name_updates_pending')))
        self.edition_button.setText(self.dashboard.report_title_line.text() or '点击填写期次名称')
        self.edition_button.setToolTip('点击修改期次名称\n'+self.workspace_label.toolTip())
        span=self.episode.get('review_range')
        period=(' — '.join(span)+' · '+tr('production.beijing_time')) if span else (
            r['start']+' — '+r['end']+' · '+roundup.timezone_label(r) if self.mode=='roundup' else news)
        issue=tr('production.edition_number',number=self.episode['number'])
        display_span=span or ([r['start'],r['end']] if self.mode=='roundup' else
            [s.start_date,s.end_date] if s.date_mode=='custom' else [])
        if len(display_span)==2 and all(len(day)==10 for day in display_span) and display_span[0][:4]==display_span[1][:4]:
            visible_period=' — '.join(day[5:] for day in display_span)
        else:visible_period=period.split(' · ')[0]
        self.period.set_caption(issue+'  ·  '+visible_period,issue)
        self.period.setToolTip(tr('production.edition_settings')+'\n'+issue+' · '+period)
        self.period.setAccessibleName(tr('production.edition_settings'))
        self.period.setAccessibleDescription(issue+' · '+period)
        descriptions=(tr('production.materials_selection',news=len(self.news.included),launches=self._launch_count()),
                      tr('production.script_description'),tr('production.media_description'),tr('production.export_description'))
        for index,description in enumerate(descriptions):
            item=self.navigation.item(index)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,description);item.setToolTip(description)
        self._workflow_navigation()
        if self.navigation.currentRow()==3:self._export_checks()
        if check_saved and self._saved_hash:self._update_save_tooltip(roundup.digest(self.content_state()))

    def _update_save_tooltip(self, content_hash):
        self.save_button.setToolTip(tr('production.saved' if content_hash==self._saved_hash else 'production.unsaved'))

    def _edit_title(self):
        self.edition_button.hide();self.dashboard.report_title_line.show();self.dashboard.report_title_line.setFocus();self.dashboard.report_title_line.selectAll()

    def _finish_title(self):
        self.dashboard.report_title_line.hide();self.edition_button.show();self._header()

    def export_scope(self,mode=None):
        mode=mode or self.mode
        if mode=='complete':
            basis=[self.episode['number'],self.episode['date'],self.episode.get('music',[]),self.export_scope('roundup'),self.export_scope('news')]
            if self.episode.get('review_range'):basis.append(self.episode['review_range'])
            options=music_settings(self.episode)
            if options!=MUSIC_DEFAULTS:basis.append(options)
        elif mode=='roundup':
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
        scopes={self.export_scope()}
        if self.settings.video_workflow=='complete':scopes.add(self.export_scope('complete'))
        current={Path(path).resolve() for path in self.results if self.result_scopes.get(path) in scopes}
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
        if self.image_worker:rows.append(('news_image','新闻画面素材','正在更新视频素材' if self.image_worker.video
                                          else '正在下载或导入图片',self.image_worker.token.cancel))
        if self.maintenance.worker:rows.append(('maintenance','缓存维护',self.maintenance.status.text(),self.maintenance.cancel))
        if self.voice_setup.worker:
            rows.append(('voice_setup','语音配置',self.voice_setup.status.text(),self.voice_setup.cancel_work))
        if self.aux_worker:rows.append(('diagnostic',tr('production.diagnostics'),tr('production.working',task=self.aux_kind),self.aux_worker.token.cancel))
        if self.worker:
            label={'voice_propose':'生成新闻口播','voice_audio':'新闻配音与对齐','voice_align':'新闻对齐',
                'collect':'采集新闻','write':'生成新闻稿','narration_retry':'重试失败口播',
                'export':'导出新闻','complete_video':'导出带配音的 MP4','import':'备份与导入旧工作区'}.get(self.worker_kind,'处理新闻')
            if self.worker_kind in ('collect','write'):label=self._news_phase or label
            if self.worker.token.is_cancelled:label=tr('production.cancelling')
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

    def _finish_recovery_write(self,*,wait=False):
        future=self._recovery_write
        if future is not None and (wait or future.done()):
            self._recovery_write=None
            try:self._update_save_tooltip(future.result())
            except OSError as error:
                self._recovery_blocked=True;self.task_text.setText(tr('production.failed',error=str(error)))

    @staticmethod
    def _write_recovery(path,snapshot):
        write_json(path,snapshot,compact=True)
        return roundup.digest({key:value for key,value in snapshot.items() if key!='view'})

    def _poll(self,*,initialize=False):
        if self._restoring:return
        self._finish_recovery_write()
        if not initialize:self._poll_terms()
        rows=self.task_rows();self.cancel_tasks.setVisible(bool(rows))
        self.task_progress.setVisible(bool(rows))
        if self.worker_kind!='complete_video' or not self.worker:self.task_progress.setRange(0,0)
        if rows:self.task_text.setText(self._export_activity if self.worker and self.worker_kind=='complete_video'
                                      and not self.worker.token.is_cancelled else ' | '.join(name+': '+text for _,name,text,_ in rows))
        for key,component in (('roundup',self.roundup),('cards',self.cards),('intro',self.intro),('downloads',self.downloads)):
            text=component.status.text()
            if self._last_statuses.get(key)!=text:
                self._last_statuses[key]=text
                self.log.append_html(escape(key+': '+text))
                page=self.navigation.currentRow()
                if not rows and ((key=='roundup' and self.mode=='roundup' and page in (0,1,2,3))
                    or key=='roundup' and page==2 and self.roundup._job_kind in ('preview_audio','voice_sample')
                    or page==2 and self.media.currentIndex()=={'cards':1,'intro':2,'downloads':3}.get(key)):
                    self.task_text.setText(text)
        self.apply_button.setEnabled(not rows)
        voice_idle=self.worker is None and self.roundup.worker is None and self.voice_setup.worker is None
        self.voice_settings.setEnabled(voice_idle);self.roundup.speaker.setEnabled(voice_idle)
        self.voice_sample_button.setEnabled(voice_idle)
        self.music_panel.enabled.setEnabled(self.roundup.worker is None)
        self.music_panel.manage.setEnabled(self.roundup.worker is None)
        self.music_panel.mix_button.setEnabled(self.roundup.worker is None)
        has_text=self.mode!='news' or bool(self.voice_text.toPlainText().strip())
        self.listen_button.setEnabled(voice_idle and has_text);self.speech_button.setEnabled(voice_idle and has_text)
        self.voice_text.setReadOnly(not voice_idle)
        self.sync_button.setEnabled(voice_idle)
        self.period.setEnabled(not rows)
        self.launch_detail_actions[0].setEnabled(self.roundup.worker is None and self.roundup.tasks.currentRow()>=0)
        self.export_news_button.setEnabled(self.worker is None)
        self.complete_export_button.setEnabled(not rows)
        self.episode_preview_button.setEnabled(not rows)
        self.cards_export_button.setEnabled(self.cards.worker is None)
        self.news.sources.setEnabled(True);self.dashboard.setEnabled(self.worker is None)
        value=self.content_state(for_poll=True);key=roundup.digest(value)
        if key!=self._observed_hash and self._recovery_write is None:
            self._observed_hash=key
            snapshot=self.snapshot()
            if not self._recovery_blocked and not self._discard:
                self._recovery_write=_BACKGROUND.submit(self._write_recovery,resource_path('cache','production-recovery.json'),snapshot)
            voice_key=self._voice_state_hash(value)
            if voice_key!=self._observed_voice_hash:
                self._observed_voice_hash=voice_key
                self._refresh_visible_voice()
            self._header(check_saved=False)
            self.save_button.setToolTip(tr('production.unsaved'))
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
