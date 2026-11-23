"""Two views of the same prepared news bundle; rendering/export stay in services."""
from copy import deepcopy
from datetime import date
from html import escape
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QIcon, QPixmap,QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QTextBrowser, QLineEdit, QPlainTextEdit, QLabel, QMessageBox,QFileDialog,QMenu,QInputDialog,
    QCheckBox,QToolButton,QDialog,QSizePolicy,QFormLayout,QDialogButtonBox,QAbstractItemView)

from i18n import tr
from models.ai_summary import AISummary
from services.production import serialize_news, restore_news
from services import news_voice
from services.launch_roundup import digest
from services.roundup_audio import reading_text
from models.article import Article
from models.news import News, is_news_url, news_id
from ui.component import SegmentedTabs
from ui.caption_editor import edit_news_captions
from ui.widgets.combo_box import RoundedComboBox


def populate_voice_assets(draft):
    from services.news_images import selected,slideshow
    from services.news_video import selected as selected_video
    identifier=draft['news_id'];item=draft['screen_item']
    try:image=selected(identifier)
    except (OSError,ValueError,KeyError,TypeError):image=None
    item['image']=dict(path=image['absolute_path'],sha256=image['sha256']) if image else None
    item.pop('image_error',None)
    try:item['slideshow']=slideshow(identifier)
    except (OSError,ValueError,KeyError,TypeError) as error:
        item['image_error']=str(error);item['slideshow']=[]
    try:item['video']=selected_video(identifier)
    except (OSError,ValueError,KeyError,TypeError):item['video']=None


class SelectVisibleNews(QCheckBox):
    def nextCheckState(self):
        self.setCheckState(Qt.CheckState.Unchecked if self.checkState()==Qt.CheckState.Checked else Qt.CheckState.Checked)


class NewsWorkspace(QWidget):
    PAGE_SIZE=20
    collect_requested = Signal()
    write_requested = Signal()
    changed = Signal()
    narration_requested=Signal()
    oral_modified=Signal(str)
    voice_requested=Signal(str)
    retry_requested=Signal()
    diagnostic_requested=Signal(object)
    article_changed=Signal()
    image_requested=Signal(object,object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.in_edition=lambda article:True
        self.bundle = None
        self._bundle_digest = ''
        self.included = set()
        self.script_included = set()
        self.script_preferences={}
        self.edited = set()
        self._running = False
        self.voices={};self.voice_settings=None;self.narration_failures={}
        self._loading = False
        self._pages={'sources':0,'articles':0}
        self.selection_revision=0
        self.data_page = QWidget()
        layout = QVBoxLayout(self.data_page)
        row = QHBoxLayout()
        self.collect = QPushButton(tr('production.collect_news'))
        self.collect.setObjectName('PrimaryButton')
        self.collect.clicked.connect(self.collect_requested.emit)
        row.addWidget(self.collect)
        self.new_button=QPushButton('新建新闻');self.new_button.clicked.connect(lambda:self.new_news())
        row.addWidget(self.new_button)
        self.data_hint = QLabel(tr('production.news_empty'))
        self.data_hint.setWordWrap(True)
        row.addWidget(self.data_hint, 1)
        layout.addLayout(row)
        split = QSplitter()
        self.sources = QListWidget()
        self.sources.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sources.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sources.itemChanged.connect(self._choose)
        self.sources.currentRowChanged.connect(self._source)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        source_list=QWidget();source_layout=QVBoxLayout(source_list)
        source_layout.setContentsMargins(0,0,0,0)
        self._list_controls(source_layout,'sources')
        source_layout.addWidget(self.sources,1);split.addWidget(source_list)
        detail=QWidget();detail.setObjectName('WorkspacePanel');detail_layout=QVBoxLayout(detail)
        detail_layout.setContentsMargins(14,12,14,12)
        self.source_title=QLabel();self.source_title.setObjectName('PanelTitle');self.source_title.setWordWrap(True)
        self.source_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.source_title.setTextFormat(Qt.TextFormat.PlainText);detail_layout.addWidget(self.source_title)
        source_row=QHBoxLayout();self.source_meta=QLabel();self.source_meta.setObjectName('MutedText')
        self.source_meta.setTextFormat(Qt.TextFormat.PlainText);self.source_meta.setWordWrap(True);source_row.addWidget(self.source_meta,1)
        self.open_source=QPushButton(tr('production.open_news_source'));self.open_source.setObjectName('TextButton')
        self.open_source.clicked.connect(self._open_source);source_row.addWidget(self.open_source);detail_layout.addLayout(source_row)
        self.image_preview=QLabel('暂无配图');self.image_preview.setObjectName('NewsImagePreview')
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.image_preview.setMinimumHeight(100);self.image_preview.setMaximumHeight(190)
        self.image_hint=QLabel();self.image_hint.setWordWrap(True);detail_layout.addWidget(self.image_preview);detail_layout.addWidget(self.image_hint)
        self.image_use=QCheckBox('使用本条配图');self.image_use.setToolTip('取消后画面与导出不使用配图，已保存的图片保留；可自行寻找并导入合适的图片。')
        self.image_use.toggled.connect(self._image_use_changed);detail_layout.addWidget(self.image_use)
        actions=QHBoxLayout();self.image_download=QPushButton('下载 / 重试配图');self.image_download.clicked.connect(lambda:self.request_image({}))
        self.image_change=QPushButton('管理画面素材');menu=QMenu(self.image_change)
        menu.addAction('选择候选图片',lambda:self.choose_candidate());menu.addAction('添加本地图片（可多选）',lambda:self.import_image())
        menu.addAction('选择已保存图片和顺序',lambda:self.edit_gallery())
        menu.addAction('添加本地视频',lambda:self.import_video())
        menu.addAction('剪裁视频（I / O）',lambda:self.trim_video())
        menu.addAction('移除本条视频',lambda:self.request_image(dict(remove_video=True)))
        self.image_change.setMenu(menu)
        self.edit_source=QPushButton('编辑人工新闻')
        self.edit_source.clicked.connect(lambda:self.new_news(self.source_article()))
        self.edit_source.hide()
        folder=QPushButton('打开图片文件夹');folder.clicked.connect(self.open_images)
        for button in (self.edit_source,self.image_download,self.image_change,folder):actions.addWidget(button)
        actions.addStretch();detail_layout.addLayout(actions);detail_layout.addWidget(self.details,1);split.addWidget(detail)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        self.script_page = QWidget()
        layout = QVBoxLayout(self.script_page)
        row = QHBoxLayout()
        self.generate = QPushButton('生成新闻稿')
        self.generate.setObjectName('PrimaryButton')
        self.generate.clicked.connect(self._generate)
        row.addWidget(self.generate)
        self.script_hint = QLabel('生成范围取自资料页勾选')
        self.script_hint.setWordWrap(True)
        row.addWidget(self.script_hint, 1)
        layout.addLayout(row)
        self.batch_hint=QLabel();self.batch_hint.setWordWrap(True);self.batch_hint.hide();layout.addWidget(self.batch_hint)
        split = QSplitter()
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)
        list_page=QWidget();list_layout=QVBoxLayout(list_page);list_layout.setContentsMargins(0,0,0,0)
        heading_row=QHBoxLayout()
        list_heading=QLabel('本期新闻');list_heading.setObjectName('SectionTitle');heading_row.addWidget(list_heading,1)
        controls=QHBoxLayout();self.script_actions=[]
        self.select_visible=SelectVisibleNews('全选');self.select_visible.setTristate(True)
        self.select_visible.setToolTip('选择当前筛选结果的所有分页。取消时只影响这批新闻。')
        self.select_visible.clicked.connect(self._check_scripts);controls.addWidget(self.select_visible)
        self.filtered_count=QLabel();self.filtered_count.setObjectName('MutedText');controls.addWidget(self.filtered_count)
        controls.addStretch()
        for text,action in (('上移',lambda:self.move_article(-1)),('下移',lambda:self.move_article(1))):
            button=QToolButton();button.setText(text);button.clicked.connect(action);controls.addWidget(button);self.script_actions.append(button)
        self.script_actions[0].setShortcut('Alt+Up');self.script_actions[1].setShortcut('Alt+Down')
        self.list_more=QPushButton('更多')
        menu=QMenu(self.list_more);self.merge_button=menu.addAction('合并成一条',self.show_merge)
        self.list_more.setMenu(menu);heading_row.addWidget(self.list_more)
        list_layout.addLayout(heading_row);list_layout.addLayout(controls)
        self.articles = QListWidget()
        self.articles.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.articles.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.articles.itemChanged.connect(self._choose_script)
        self.articles.currentRowChanged.connect(self._article)
        self._list_controls(list_layout,'articles')
        list_layout.addWidget(self.articles,1)
        self.selection_hint=QLabel();self.selection_hint.setWordWrap(True);list_layout.addWidget(self.selection_hint)
        self.selection_hint.setToolTip('勾选决定本期导出和失败重试的范围，高亮行决定右侧正在编辑的新闻。')
        self.retry=QPushButton('重试失败项')
        self.retry.setToolTip('仅重试本期尚未处理的失败项。')
        self.retry.clicked.connect(self.retry_requested.emit)
        list_layout.addWidget(self.retry,0,Qt.AlignmentFlag.AlignLeft)
        list_page.setMinimumWidth(260);split.addWidget(list_page)
        right_page=QWidget();right_layout=QVBoxLayout(right_page);right_layout.setContentsMargins(0,0,0,0)
        self.current_title=QLabel();self.current_title.setObjectName('SectionTitle')
        self.current_title.setAccessibleName('当前新闻标题')
        self.current_title.setTextFormat(Qt.TextFormat.PlainText);self.current_title.setWordWrap(True)
        self.current_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        right_layout.addWidget(self.current_title)
        editor_actions=QHBoxLayout()
        self.related_button=QPushButton('相关来源');self.related_button.clicked.connect(self.show_related)
        editor_actions.addWidget(self.related_button)
        self.unmerge_button=QPushButton('撤销合并');self.unmerge_button.clicked.connect(self.undo_merge)
        editor_actions.addWidget(self.unmerge_button);editor_actions.addStretch();right_layout.addLayout(editor_actions)
        self.conflict_hint=QLabel();self.conflict_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.conflict_hint.setWordWrap(True);right_layout.addWidget(self.conflict_hint)
        editor = QWidget()
        form = QVBoxLayout(editor)
        form.setContentsMargins(0,8,0,0)
        form.addWidget(QLabel(tr('production.title')))
        self.title = QLineEdit()
        form.addWidget(self.title)
        details=QHBoxLayout()
        self.screen_fields={}
        for key,label,limit in (('subject','新闻主体',80),('metric','关键数据',16),('metric_label','数据说明',40)):
            field=QLineEdit();field.setMaxLength(limit);field.setPlaceholderText(label);field.setAccessibleName(label)
            field.textEdited.connect(lambda text,k=key:self._edit_screen_field(k,text))
            details.addWidget(field,2 if key=='subject' else 1);self.screen_fields[key]=field
        form.addLayout(details)
        form.addWidget(QLabel(tr('production.summary')))
        self.summary = QPlainTextEdit()
        form.addWidget(self.summary, 1)
        self.captions=QPushButton('编辑本条字幕');self.captions.clicked.connect(lambda:edit_news_captions(self));form.addWidget(self.captions)
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        form.addWidget(self.origin)
        self.citation = QTextBrowser()
        self.citation.setOpenExternalLinks(True)
        self.citation.setMaximumHeight(130)
        form.addWidget(self.citation)
        self.editor_tabs=SegmentedTabs()
        voice_page=QWidget();voice_layout=QVBoxLayout(voice_page);actions=QHBoxLayout()
        voice_layout.setContentsMargins(0,8,0,0)
        self.proposal=QPushButton('生成本条口播');self.proposal.clicked.connect(self.narration_requested.emit);actions.addWidget(self.proposal)
        self.proposal.setToolTip('为列表当前打开的这条新闻生成口播提案。')
        self.go_voice=QPushButton('前往配音');self.go_voice.clicked.connect(self._go_voice);actions.addWidget(self.go_voice);actions.addStretch(1)
        voice_layout.addLayout(actions);voice_layout.addWidget(QLabel('新闻口播稿（配音时朗读）'))
        self.oral=QPlainTextEdit();voice_layout.addWidget(self.oral,1)
        reading_row=QHBoxLayout()
        self.reading_toggle=QPushButton('展开最终读法预览');self.reading_toggle.setCheckable(True);reading_row.addWidget(self.reading_toggle)
        self.reading_toggle.setToolTip('修改口播，或在语音设置中调整发音规则。')
        self.spoken_versions=QPushButton('查看口播修改记录');self.spoken_versions.clicked.connect(self._show_versions)
        self.spoken_versions.hide();reading_row.addWidget(self.spoken_versions);reading_row.addStretch(1)
        voice_layout.addLayout(reading_row)
        self.final_input=QPlainTextEdit();self.final_input.setReadOnly(True);self.final_input.setMaximumHeight(134)
        self.final_input.hide();voice_layout.addWidget(self.final_input)
        self.reading_toggle.toggled.connect(self.final_input.setVisible)
        self.reading_toggle.toggled.connect(lambda shown:self.reading_toggle.setText('收起最终读法预览' if shown else '展开最终读法预览'))
        detail_row=QHBoxLayout()
        self.voice_hint=QLabel();self.voice_hint.setWordWrap(True);voice_layout.addWidget(self.voice_hint)
        self.failure_reason=QLabel();self.failure_reason.setWordWrap(True);detail_row.addWidget(self.failure_reason,1)
        self.failure_details=QPushButton('查看失败详情');self.failure_details.clicked.connect(self._show_failure);detail_row.addWidget(self.failure_details)
        voice_layout.addLayout(detail_row)
        self.oral.textChanged.connect(self._edit_oral)
        self._edit_timer=QTimer(self);self._edit_timer.setSingleShot(True);self._edit_timer.setInterval(150)
        self._edit_timer.timeout.connect(self._paint_edit)
        self.editor_tabs.addTab(voice_page,'口播稿');self.editor_tabs.addTab(editor,'屏幕文字与摘要')
        right_layout.addWidget(self.editor_tabs,1);split.addWidget(right_page)
        split.setStretchFactor(1, 3)
        split.setSizes([290,680])
        layout.addWidget(split, 1)
        self.title.textChanged.connect(self._edit)
        self.summary.textChanged.connect(self._edit)
        self._source(-1)
        self._article()

    def set_running(self,running):
        self._running=running
        self.captions.setEnabled(not running)
        self.collect.setEnabled(not running)
        self.new_button.setEnabled(not running)
        self.edit_source.setEnabled(not running)
        self.proposal.setEnabled(not running and self.current_article() is not None)
        self._update_script_rows()
        self.title.setReadOnly(running);self.summary.setReadOnly(running);self.oral.setReadOnly(running)
        for field in self.screen_fields.values():field.setReadOnly(running)
        self._loading=True
        for index in range(self.sources.count()):
            item=self.sources.item(index);flag=Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(item.flags() & ~flag if running else item.flags() | flag)
        self._loading=False

    def _generate(self):
        self.write_requested.emit()

    def new_news(self,article=None):
        dialog=QDialog(self);dialog.setWindowTitle('编辑人工新闻' if article else '新建新闻');dialog.resize(600,420)
        form=QFormLayout(dialog)
        title=QLineEdit();body=QPlainTextEdit()
        published=QLineEdit();published.setPlaceholderText('YYYY-MM-DD')
        source=QLineEdit();url=QLineEdit()
        if article:
            news=article.news
            title.setText(news.title);body.setPlainText(article.body)
            published.setText(news.published);url.setText(news.url)
            if news.source!='人工录入':source.setText(news.source.removeprefix('人工录入 · '))
        for label,field in (('新闻标题',title),('正文',body),('日期（选填）',published),
                            ('来源（选填）',source),('原文链接（选填）',url)):
            form.addRow(label,field)
        error=QLabel();error.setWordWrap(True);form.addRow(error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons);buttons.rejected.connect(dialog.reject)
        def save():
            try:self.add_news(title.text(),body.toPlainText(),published=published.text(),source=source.text(),url=url.text(),existing=article)
            except ValueError as issue:error.setText(str(issue));return
            dialog.accept()
        buttons.accepted.connect(save);dialog.exec()

    def add_news(self,title,body,*,published='',source='',url='',existing=None):
        title,body,published,source,url=(value.strip() for value in (title,body,published,source,url))
        if not title or not body:raise ValueError('请填写新闻标题和正文')
        if published:
            try:published=date.fromisoformat(published).isoformat()
            except ValueError:raise ValueError('日期请填写为 YYYY-MM-DD') from None
        if url and not is_news_url(url):raise ValueError('原文链接应为有效的 HTTP 或 HTTPS 地址')
        provenance='人工录入'+(' · '+source if source else '')
        if existing:
            if not existing.news.manual_id:raise ValueError('只能在此补充人工新闻')
            existing.news.title=title;existing.news.published=published;existing.news.source=provenance
            existing.news.url=url;existing.body=body
            self._bundle_digest=''
            self.selection_revision+=1;self.refresh();self.changed.emit()
            return existing
        news=News(provenance,title,published,'',url,manual_id='manual:'+uuid4().hex)
        article=Article(news,body,'manual')
        if self.bundle is None:self.bundle=dict(articles=[],source_articles=[],summaries={},assessments={})
        self.bundle['articles'].append(article)
        self.bundle['source_articles'].append(article)
        self._bundle_digest=''
        self.included.add(news.key);self.script_included.add(news.key)
        self.script_preferences[news.key]=True
        self.selection_revision+=1
        was_hidden=self._lists['sources'][0].currentData()=='unselected'
        self._show_item('sources',news.key)
        self._show_item('articles',news.key)
        if was_hidden:self.data_hint.setText(self.data_hint.text()+' · 已切换到全部新闻并定位新条目')
        self.changed.emit()
        return article

    def adopt(self, bundle, *, collected=False):
        focus=self.current_article()
        proposals=bundle.pop('narration_proposals',{})
        failures=bundle.pop('narration_failures',{})
        states=bundle.pop('narration_states',{})
        if collected:self.batch_hint.hide()
        previous=[a.news.key for a in self.bundle['articles']] if self.bundle else []
        old_bundle=self.bundle or {}
        merged={key:row for key,row in old_bundle.get('news_events',{}).items() if 'merged_checks' in row}
        merged_sources={key for row in merged.values() for key in row['merged_checks']}
        saved=[a for a in old_bundle.get('articles',[]) if a.news.key in merged_sources or a.news.key in self.script_preferences or a.news.key in self.edited
               or (voice:=self.voices.get(news_id(a.news.key))) and news_voice.has_existing_spoken(voice)]
        originals=(self.bundle or {}).get('source_articles',[])
        before={a.news.key for a in originals}
        manual=[a for a in originals if a.news.manual_id]
        if collected or not self.bundle:
            bundle['source_articles']=list(bundle['articles'])
            if manual:
                bundle['source_articles'].extend(manual)
                bundle['articles'].extend(manual)
        else:
            originals=self.bundle.get('source_articles',self.bundle['articles'])
            verified={a.news.key:a.evidence for a in bundle['articles'] if a.evidence}
            for article in originals:
                if article.news.key in verified:article.evidence=verified[article.news.key]
            by_url={a.news.key:a for a in originals}
            bundle['articles']=[by_url.get(a.news.key,a) for a in bundle['articles']]
            bundle['source_articles']=originals
            by_url={a.news.key:a for a in bundle['articles']}
            bundle['articles']=[by_url[url] for url in previous if url in by_url]+[a for a in bundle['articles'] if a.news.key not in previous]
        keys={a.news.key for a in bundle['articles']}
        sources={a.news.key for a in bundle['source_articles']}
        bundle['articles'].extend(a for a in saved if a.news.key not in keys)
        bundle['source_articles'].extend(a for a in saved if a.news.key not in sources)
        for key in self.edited:
            summary=old_bundle.get('summaries',{}).get(key)
            if summary:bundle.setdefault('summaries',{})[key]=summary
        old_context=old_bundle.get('context')
        if old_context and self.edited:
            from dataclasses import replace
            context=bundle.get('context') or old_context
            bundle['context']=replace(context,localized_titles={**context.localized_titles,
                **{key:title for key,title in old_context.localized_titles.items() if key in self.edited}})
        if manual:
            keys={a.news.key for a in bundle['articles']}
            bundle['articles'].extend(a for a in manual if a.news.key not in keys)
            edited={a.news.key for a in manual if collected or a.news.key in self.edited}
            bundle.setdefault('summaries',{}).update({k:v for k,v in self.bundle.get('summaries',{}).items() if k in edited})
            if self.bundle.get('context') and edited:
                from dataclasses import replace
                context=bundle.get('context') or self.bundle['context']
                bundle['context']=replace(context,localized_titles=dict(context.localized_titles,
                    **{a.news.key:self._title(a) for a in manual if a.news.key in edited}))
        self.bundle = bundle
        self._bundle_digest=''
        self.selection_revision+=1
        keys={a.news.key for a in bundle['articles']}
        source_keys={a.news.key for a in bundle['source_articles']}
        self.included=(self.included & source_keys)|(source_keys-before)
        self.script_included=(self.script_included & keys)|(keys-set(previous))
        events=bundle.get('news_events',{})
        self.script_included.update(key for key,row in events.items() if row['phase']!='pending'
                                    and old_bundle.get('news_events',{}).get(key,{}).get('phase')=='pending')
        suppressed=({key for row in events.values() for key in row['related']}-set(events)) | {
            key for key,row in events.items() if row['phase']=='pending'}
        self.script_included.difference_update(suppressed)
        self.script_included.update(key for key in keys if self.script_preferences.get(key) is True)
        self.script_included.difference_update(key for key in keys if self.script_preferences.get(key) is False)
        if merged:bundle.setdefault('news_events',{}).update(merged)
        self.script_included.difference_update(merged_sources)
        self.edited.intersection_update(keys)
        self.apply_narrations(proposals,failures,states)
        if focus:
            position=next((n for n,a in enumerate(self._filtered('articles')) if a.news.key==focus.news.key),None)
            if position is not None:self._pages['articles']=position//self.PAGE_SIZE
        self.refresh()
        self.changed.emit()

    def apply_narrations(self,proposals,failures,states):
        self.narration_failures.update(failures)
        if proposals and self.voice_settings is not None:
            voice=self.voice_settings()
            for article in self.bundle['articles']:
                identifier=news_id(article.news.key)
                draft=self.voices.get(identifier)
                if identifier in proposals and (draft is None or not news_voice.has_existing_spoken(draft)):
                    if draft is None:draft=self.voices[identifier]=news_voice.create(article,voice)
                    news_voice.apply_proposal(draft,proposals[identifier])
                    self.narration_failures.pop(identifier,None)
        if states:
            message='，'.join(f'{label}{list(states.values()).count(key)}条' for key,label in (
                ('generated','已生成'),('existing','已有'),('skipped','跳过'),('failed','失败'),('cancelled','取消'))
                if key in states.values())
            self.batch_hint.setText(message);self.batch_hint.setVisible(bool(message))
        self.refresh_voice()

    def failed_articles(self):
        failed=[]
        merged=self.merged_members()
        for article in (self.bundle or {}).get('articles',[]):
            identifier=news_id(article.news.key)
            draft=self.voices.get(identifier)
            if (article.news.key not in merged and self.script_preferences.get(article.news.key) is not False and self.in_edition(article)
                    and identifier in self.narration_failures):
                if not draft or not news_voice.has_existing_spoken(draft):failed.append(article)
        return failed

    def select_article(self,identifier):
        article=next((a for a in self._rows('articles') if news_id(a.news.key)==identifier),None)
        if article is None:return False
        if self.current_article() is not article:
            for index in range(self.articles.count()):
                if self.articles.item(index).data(Qt.ItemDataRole.UserRole)==article.news.key:
                    self.articles.setCurrentRow(index);break
            else:self._show_item('articles',article.news.key)
        return True

    def _go_voice(self):
        article=self.current_article()
        if self.is_selected(article):self.voice_requested.emit(news_id(article.news.key))

    def _show_failure(self):
        article=self.current_article()
        failure=self.narration_failures.get(news_id(article.news.key)) if article else None
        if failure:self.show_failures([article])

    def editorial_event(self,article):
        return (self.bundle or {}).get('news_events',{}).get(article.news.key,{})

    def merged_members(self):
        return {key for row in (self.bundle or {}).get('news_events',{}).values()
                if 'merged_checks' in row for key in row['merged_checks']}

    def show_merge(self):
        from services.news_editorial import merge_preview
        members=self.merged_members()
        candidates=[a for a in self._rows('articles') if self.in_edition(a) and a.news.key not in members
                    and 'merged_checks' not in self.editorial_event(a)]
        if self._running or len(candidates)<2:return
        chooser=QDialog(self);chooser.setWindowTitle('选择要合并的新闻');chooser.resize(620,380)
        layout=QVBoxLayout(chooser);listing=QListWidget();layout.addWidget(listing)
        listing.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for row in candidates:
            item=QListWidgetItem(self._title(row),listing)
            item.setSelected(False)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
        preview=buttons.addButton('生成预览',QDialogButtonBox.ButtonRole.AcceptRole);preview.setEnabled(False)
        listing.itemSelectionChanged.connect(lambda:preview.setEnabled(len(listing.selectedItems())>=2))
        buttons.accepted.connect(chooser.accept);buttons.rejected.connect(chooser.reject)
        if not chooser.exec():return
        articles=[row for index,row in enumerate(candidates) if listing.item(index).isSelected()]
        data=merge_preview(articles,self.bundle.get('news_events',{}))
        dialog=QDialog(self);dialog.setWindowTitle('相关新闻合稿预览');dialog.resize(700,540)
        form=QFormLayout(dialog)
        title=QLineEdit(data['title']);summary=QPlainTextEdit(data['summary']);spoken=QPlainTextEdit(data['spoken'])
        for label,widget in (('标题',title),('摘要',summary),('口播',spoken)):form.addRow(label,widget)
        issue=QLabel();issue.setTextFormat(Qt.TextFormat.PlainText);issue.setWordWrap(True);form.addRow(issue)
        issue.setText('\n'.join([*(f'需核对 {label}（{values}）' for label,values in data['conflicts'].items()),
                                 *(f'待补资料 {line}' for line in data['missing'])]))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton('确认合稿',QDialogButtonBox.ButtonRole.AcceptRole);form.addRow(buttons)
        def confirm():
            try:self.confirm_merge(articles,{**data,'title':title.text(),'summary':summary.toPlainText(),'spoken':spoken.toPlainText()})
            except ValueError as error:issue.setText(str(error));return
            dialog.accept()
        buttons.accepted.connect(confirm);buttons.rejected.connect(dialog.reject);dialog.exec()

    def confirm_merge(self,articles,data):
        from services.news_editorial import check_spoken
        title=data['title'].strip();summary=data['summary'].strip();spoken=data['spoken'].strip()
        if self._running or len(articles)<2 or not title or self.voice_settings is None:
            raise ValueError('请选择至少两条新闻并填写合稿标题')
        if len(spoken)>8000:raise ValueError('合稿口播超过 8000 字，请缩短口播或减少合并条目')
        if not spoken and not data['missing']:raise ValueError('请填写完整口播')
        if spoken:check_spoken(spoken)
        keys=[a.news.key for a in articles]
        news=News('相关新闻合稿',title,articles[0].news.published,summary,'',manual_id='manual:'+uuid4().hex)
        merged=Article(news,spoken or summary,'manual',evidence=data['evidence'])
        draft=news_voice.create(merged,self.voice_settings())
        if spoken:
            news_voice.set_spoken(draft,spoken)
            draft['segments'][0]['source_refs']=deepcopy(data['evidence'])
        rows=self.bundle['articles'];rows.insert(min(rows.index(a) for a in articles),merged)
        self.bundle['source_articles'].append(merged)
        related=list(dict.fromkeys(key for a in articles for key in [a.news.key,*self.editorial_event(a).get('related',[])]))
        self.bundle.setdefault('news_events',{})[news.key]=dict(phase='other',related=related,evidence=[],
            conflicts=data['conflicts'],merged_checks={key:key in self.script_included for key in keys},
            missing_sources=data['missing_sources'] if not spoken else [])
        self.script_included.difference_update(keys);self.script_included.add(news.key)
        self.script_preferences[news.key]=True;self.edited.add(news.key)
        self.voices[draft['news_id']]=draft
        if not spoken:self.narration_failures[draft['news_id']]=dict(标题=title,出处='',状态='待补资料',
            缺少='；'.join(data['missing']),原因='合稿含无法核对的核心事实')
        self._bundle_digest='';self.selection_revision+=1
        self.refresh();self._show_item('articles',news.key);self.changed.emit()
        return merged

    def undo_merge(self):
        article=self.current_article()
        event=self.editorial_event(article) if article else {}
        if self._running or 'merged_checks' not in event:return
        self.bundle['news_events'].pop(article.news.key)
        for name in ('articles','source_articles'):self.bundle[name].remove(article)
        self.script_included.discard(article.news.key);self.included.discard(article.news.key)
        self.script_preferences.pop(article.news.key,None);self.edited.discard(article.news.key)
        self.narration_failures.pop(news_id(article.news.key),None)
        self.script_included.update(key for key,checked in event['merged_checks'].items() if checked)
        self._bundle_digest='';self.selection_revision+=1
        self.refresh();self.changed.emit()

    def restore_related(self,article):
        if article.news.key in self.merged_members():return
        if article not in self._rows('articles'):
            self.bundle['articles'].append(article)
            self._bundle_digest=''
        self.script_included.add(article.news.key)
        self.script_preferences[article.news.key]=True
        self.narration_failures.get(news_id(article.news.key),{}).pop('已跳过',None)
        self.selection_revision+=1
        self._show_item('articles',article.news.key);self.changed.emit()

    def show_related(self):
        from services.news_editorial import phase
        article=self.current_article()
        if article is None:return
        keys=set(self.editorial_event(article).get('related',[]))
        sources=[a for a in self._rows('sources') if a.news.key in keys]
        if not sources:return
        dialog=QDialog(self);dialog.setWindowTitle('相关来源');dialog.resize(690,340)
        layout=QVBoxLayout(dialog);listing=QListWidget();layout.addWidget(listing)
        labels=dict(preview='飞前预告',live='飞行进展',result='飞后结果',analysis='后续分析',other='相关报道')
        for source in sources:
            listing.addItem(f'{labels[phase(source)]} · {source.news.title}\n{source.news.source} · {source.news.published}')
        listing.setCurrentRow(0)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);layout.addWidget(buttons)
        open_link=buttons.addButton('查看原文',QDialogButtonBox.ButtonRole.ActionRole)
        restore=buttons.addButton('恢复为独立稿件',QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        def open_selected():
            url=sources[listing.currentRow()].news.url
            if is_news_url(url):QDesktopServices.openUrl(QUrl(url))
        def restore_selected():
            self.restore_related(sources[listing.currentRow()]);dialog.accept()
        open_link.clicked.connect(open_selected);restore.clicked.connect(restore_selected)
        restore.setEnabled(not self._running and 'merged_checks' not in self.editorial_event(article));dialog.exec()

    def show_failures(self,articles=None):
        articles=self.failed_articles() if articles is None else list(articles)
        if not articles:return
        if previous:=getattr(self,'failure_dialog',None):previous.close()
        dialog=QDialog(self);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('新闻口播处理结果');dialog.resize(720,430);self.failure_dialog=dialog
        dialog.finished.connect(lambda:self.__dict__.pop('failure_dialog',None))
        layout=QVBoxLayout(dialog);count=QLabel(f'{len(articles)} 条新闻需要处理');layout.addWidget(count)
        listing=QListWidget();layout.addWidget(listing,1)
        details=QPlainTextEdit();details.setReadOnly(True);layout.addWidget(details,1)
        rows=[self.narration_failures[news_id(a.news.key)] for a in articles]
        for article,row in zip(articles,rows):listing.addItem(f"{row.get('状态','失败')} · {self._title(article)}")
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);layout.addWidget(buttons)
        retry=buttons.addButton('重试获取',QDialogButtonBox.ButtonRole.ActionRole)
        supplement=buttons.addButton('手动补充',QDialogButtonBox.ButtonRole.ActionRole)
        skip=buttons.addButton('本期跳过',QDialogButtonBox.ButtonRole.ActionRole)
        diagnostic=buttons.addButton('查看诊断',QDialogButtonBox.ButtonRole.ActionRole)
        diagnostic.setVisible(any(row.get('诊断') for row in rows))
        def show_row(index):
            diagnostic.setEnabled(index>=0 and bool(rows[index].get('诊断')))
            if index<0:return
            row=rows[index]
            details.setPlainText(f"新闻标题：{self._title(articles[index])}\n"
                                 f"缺少信息：{row.get('缺少','可核对的完整口播稿')}\n"
                                 f"失败原因：{row['原因']}")
        listing.currentRowChanged.connect(show_row);listing.setCurrentRow(0)
        diagnostic.clicked.connect(lambda:self.diagnostic_requested.emit(rows[listing.currentRow()]))
        buttons.rejected.connect(dialog.reject)
        def retry_failed():
            dialog.accept();self.retry_requested.emit()
        def skip_current():
            index=listing.currentRow()
            article=articles.pop(index);rows.pop(index)
            self.script_included.discard(article.news.key);self.script_preferences[article.news.key]=False
            self.narration_failures[news_id(article.news.key)]['已跳过']=True
            self.selection_revision+=1;self.refresh();self.changed.emit()
            if articles:
                listing.takeItem(index);show_row(listing.currentRow());count.setText(f'{len(articles)} 条新闻需要处理')
            else:dialog.accept()
        def supplement_current():
            if self.supplement(articles[listing.currentRow()],dialog):dialog.accept()
        retry.clicked.connect(retry_failed);skip.clicked.connect(skip_current);supplement.clicked.connect(supplement_current)
        for button in (retry,skip,supplement):button.setEnabled(not self._running)
        dialog.show()

    def supplement(self,article,parent):
        from services.news_editorial import outcome, sentences
        text,ok=QInputDialog.getMultiLineText(parent,'手动补充资料','请输入已核对的关键事实或结论')
        if not ok or not text.strip():return False
        event=self.editorial_event(article)
        if event.get('phase')=='pending' and not any(outcome(line) for line in sentences(text)):
            QMessageBox.warning(parent,'飞后结果待补','请补充实际飞后结果，例如实际入轨、回收或损失情况。');return False
        url,ok=QInputDialog.getText(parent,'补充资料出处','来源链接（本期截稿前）',text=article.news.url)
        if not ok:return False
        url=url.strip()
        if not is_news_url(url) and not (article.news.manual_id and not url):
            QMessageBox.warning(parent,'补充资料出处','请填写有效的来源链接。');return False
        if url:
            article.evidence.append(dict(url=url,title='人工核对补充',quote=text.strip(),manual=True))
        else:article.body=article.body.rstrip()+'\n'+text.strip()
        if 'missing_sources' in event:event['missing_sources']=[]
        if event.get('phase')=='pending':
            event['phase']='result';event['result_title']=next(line for line in sentences(text) if outcome(line))[:150]
        self.script_preferences[article.news.key]=True;self.script_included.add(article.news.key)
        self.narration_failures[news_id(article.news.key)].pop('已跳过',None)
        self._bundle_digest='';self.selection_revision+=1;self.refresh();self.changed.emit()
        return True

    def _show_versions(self):
        draft=self.voice_draft()
        if not draft:return
        dialog=QDialog(self);dialog.setWindowTitle('口播修改记录');dialog.resize(620,360)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel('改写或开始人工修改前的版本'))
        text=QPlainTextEdit();text.setReadOnly(True)
        text.setPlainText('\n\n'.join(row['at']+'\n'+row['text'] for row in reversed(draft.get('spoken_history',[]))))
        layout.addWidget(text,1)
        close=QPushButton('关闭');close.clicked.connect(dialog.accept);layout.addWidget(close,0,Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _list_controls(self,layout,name):
        row=QHBoxLayout()
        display=RoundedComboBox()
        for label,key in (('全部新闻','all'),('已选','selected'),('未选','unselected')):
            display.addItem(label,key)
        previous=QPushButton('上一页');following=QPushButton('下一页');position=QLabel()
        if not hasattr(self,'_lists'):self._lists={}
        self._lists[name]=(display,previous,position,following)
        display.currentIndexChanged.connect(lambda:self._filter(name))
        previous.clicked.connect(lambda:self._page(name,-1))
        following.clicked.connect(lambda:self._page(name,1))
        layout.addWidget(display)
        for widget in (previous,position,following):row.addWidget(widget)
        layout.addLayout(row)

    def _rows(self,name):
        bundle=self.bundle or {}
        return bundle.get('source_articles',bundle.get('articles',[])) if name=='sources' else bundle.get('articles',[])

    def _checked(self,name):
        return self.included if name=='sources' else self.script_included

    def _filtered(self,name):
        mode=self._lists[name][0].currentData()
        checked=self._checked(name)
        return [a for a in self._rows(name) if mode=='all' or (a.news.key in checked)==(mode=='selected')]

    def _filter(self,name):
        self._pages[name]=0
        self._render_list(name)

    def _page(self,name,offset):
        self._pages[name]+=offset
        self._render_list(name, fallback=0, scroll=0)

    def _show_item(self,name,key):
        display=self._lists[name][0]
        rows=[a.news.key for a in self._filtered(name)]
        if key not in rows:
            with QSignalBlocker(display):display.setCurrentIndex(0)
            rows=[a.news.key for a in self._rows(name)]
        self._pages[name]=rows.index(key)//self.PAGE_SIZE
        self._render_list(name,focus=key)

    def _render_list(self,name,*,focus=None,fallback=None,scroll=None,update_detail=True):
        listing=getattr(self,name)
        current=listing.currentItem()
        rows=self._filtered(name)
        count=max(1,(len(rows)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
        requested=self._pages[name]
        page=self._pages[name]=max(0,min(requested,count-1))
        if focus is None and listing.currentItem():
            focus=listing.currentItem().data(Qt.ItemDataRole.UserRole)
        if fallback is None:fallback=max(0,listing.currentRow())
        if scroll is None:scroll=listing.verticalScrollBar().value()
        visible=rows[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]
        if requested>page:fallback=len(visible)-1
        checked=self._checked(name)
        keys=[a.news.key for a in visible];wanted=set(keys)
        with QSignalBlocker(listing):
            for index in range(listing.count()-1,-1,-1):
                if listing.item(index).data(Qt.ItemDataRole.UserRole) not in wanted:listing.takeItem(index)
            existing={listing.item(i).data(Qt.ItemDataRole.UserRole):listing.item(i) for i in range(listing.count())}
            for index,article in enumerate(visible):
                key=article.news.key;item=existing.get(key)
                if item is None:
                    item=QListWidgetItem(article.news.title);item.setToolTip(article.news.title)
                    item.setData(Qt.ItemDataRole.UserRole,key)
                    listing.insertItem(index,item)
                elif listing.row(item)!=index:
                    listing.insertItem(index,listing.takeItem(listing.row(item)))
                if name=='sources' and item.text()!=article.news.title:
                    item.setText(article.news.title);item.setToolTip(article.news.title)
                state=Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked
                if item.checkState()!=state or item.data(Qt.ItemDataRole.CheckStateRole) is None:
                    item.setCheckState(state)
                flag=Qt.ItemFlag.ItemIsUserCheckable
                if self._running:item.setFlags(item.flags() & ~flag)
            listing.setCurrentRow(keys.index(focus) if focus in keys else min(max(0,fallback),len(keys)-1))
            listing.verticalScrollBar().setValue(scroll)
        _,previous,position,following=self._lists[name]
        previous.setEnabled(page>0);following.setEnabled(page+1<count)
        position.setText(f'{page+1} / {count}')
        changed=listing.currentItem() is not current
        if name=='sources':
            total=len(self._rows(name))
            label=tr('production.news_count',count=total) if total else tr('production.news_empty')
            self.data_hint.setText(f'{label} · 已选 {len(checked)}' if total else label)
            if changed or update_detail:self._source(listing.currentRow())
        elif changed or update_detail:self._article()
        else:self._update_script_rows()

    def refresh(self):
        for name in self._pages:self._render_list(name)

    def view_state(self):
        value={}
        for name,page in self._pages.items():
            listing=getattr(self,name);item=listing.currentItem()
            value[f'news_{name}_filter']=self._lists[name][0].currentData()
            value[f'news_{name}_page']=page
            value[f'news_{name}_scroll']=listing.verticalScrollBar().value()
            if item:value[f'news_{name}_focus']=item.data(Qt.ItemDataRole.UserRole)
        return value

    def restore_view(self,view):
        for name in self._pages:
            display=self._lists[name][0]
            with QSignalBlocker(display):
                display.setCurrentIndex(max(0,display.findData(view.get(f'news_{name}_filter'))))
            self._pages[name]=view.get(f'news_{name}_page',0)
            self._render_list(name,focus=view.get(f'news_{name}_focus'),scroll=view.get(f'news_{name}_scroll',0),
                              fallback=view.get('news_source' if name=='sources' else 'news_article',0))

    def _title(self, article):
        context = self.bundle.get('context')
        updated=self.editorial_event(article).get('result_title')
        title=updated or article.news.title
        return context.localized_titles.get(article.news.key,title) if context and (not updated or article.news.key in self.edited) else title

    def _summary(self,article):
        summary=self.bundle.get('summaries',{}).get(article.news.key)
        if summary:return summary.summary
        if not article.news.summary and article.news.manual_id and article.body.strip():return news_voice.source_excerpt(article,120)
        return article.news.summary

    def _change_check(self,name,item):
        if self._loading or self._running or not self.bundle:return
        listing=getattr(self,name);key=item.data(Qt.ItemDataRole.UserRole)
        if name=='articles' and key in self.merged_members():return
        checked=self._checked(name)
        on=item.checkState()==Qt.CheckState.Checked
        if on==(key in checked):return
        if on:checked.add(key)
        else:checked.discard(key)
        if name=='articles':
            self.script_preferences[key]=on
            if on:self.narration_failures.get(news_id(key),{}).pop('已跳过',None)
        self.selection_revision+=1
        if self._lists[name][0].currentData()!='all':
            self._render_list(name,focus=key,fallback=listing.row(item),update_detail=False)
        elif name=='articles':self._update_script_rows()
        else:
            total=len(self._rows(name))
            self.data_hint.setText(tr('production.news_count',count=total)+f' · 已选 {len(checked)}')
        self._update_batch_scope();self.changed.emit()

    def _choose(self,item):
        self._change_check('sources',item)

    def _update_batch_scope(self):
        bundle=self.bundle or {}
        sources=bundle.get('source_articles',bundle.get('articles',[]))
        count=sum(article.news.key in self.included for article in sources)
        launches=len(bundle.get('launch_articles',[]))
        self.generate.setText(f'生成新闻稿（{count} 条）')
        self.generate.setEnabled(not self._running and bool(count or launches))
        self.script_hint.setText('生成范围取自资料页勾选'+(f'，另含 {launches} 项发射资料' if launches else ''))

    def _update_script_rows(self):
        rows=self.bundle['articles'] if self.bundle else []
        merged=self.merged_members()
        numbers={a.news.key:n for n,a in enumerate(self.selected_articles(),1)}
        by_key={a.news.key:a for a in rows}
        with QSignalBlocker(self.articles):
            for i in range(self.articles.count()):
                item=self.articles.item(i);key=item.data(Qt.ItemDataRole.UserRole)
                article=by_key.get(key)
                if article is None:continue
                number=numbers.get(key)
                title=self._title(article);identifier=news_id(article.news.key);draft=self.voices.get(identifier)
                status=(('本期跳过' if self.narration_failures[identifier].get('已跳过')
                         else self.narration_failures[identifier].get('状态','失败')) if identifier in self.narration_failures
                        and not (draft and news_voice.has_existing_spoken(draft)) else '待生成'
                        if not draft or not draft['segments'][0]['spoken'].strip() else
                        {'generated':'已生成','manual':'人工修改'}.get(draft.get('oral_origin'),'已有口播'))
                if key in merged:status='已并入合稿'
                item.setText((f'{number:02d}  ' if number else '')+status+' · '+title);item.setToolTip(status+' · '+title)
                flag=Qt.ItemFlag.ItemIsUserCheckable
                item.setFlags(item.flags() & ~flag if self._running or key in merged else item.flags() | flag)
        self.selection_hint.setText(f'已勾选 {len(numbers)} / {len(rows)} 条，按稿件顺序制作。')
        article=self.current_article();listed=self._filtered('articles')
        index=listed.index(article) if article in listed else -1
        keys={row.news.key for row in listed}-merged
        checked=len(keys & self.script_included)
        with QSignalBlocker(self.select_visible):
            self.select_visible.setCheckState(Qt.CheckState.Unchecked if not checked else
                Qt.CheckState.Checked if checked==len(keys) else Qt.CheckState.PartiallyChecked)
        self.filtered_count.setText(f'{checked}/{len(keys)}')
        self.select_visible.setEnabled(not self._running and bool(keys))
        self.script_actions[0].setEnabled(not self._running and index>0)
        self.script_actions[1].setEnabled(not self._running and 0<=index<len(listed)-1)
        eligible=sum(a.news.key not in merged and 'merged_checks' not in self.editorial_event(a) and self.in_edition(a) for a in rows)
        self.merge_button.setEnabled(not self._running and eligible>=2)
        self.unmerge_button.setEnabled(not self._running)
        can_voice=self.is_selected(article)
        self.go_voice.setEnabled(can_voice)
        self.go_voice.setToolTip('' if can_voice else tr('production.choose_news_for_voice'))
        failed=len(self.failed_articles())
        self.retry.setText(f'重试失败项（{failed} 条）')
        self.retry.setVisible(bool(failed));self.retry.setEnabled(not self._running)
        self._update_batch_scope()

    def _choose_script(self,item):
        self._change_check('articles',item)

    def _check_scripts(self,checked):
        if self._running:return
        keys={a.news.key for a in self._filtered('articles')}-self.merged_members()
        if checked:self.script_included.update(keys)
        else:self.script_included.difference_update(keys)
        self.script_preferences.update((key,checked) for key in keys)
        if checked:
            for key in keys:self.narration_failures.get(news_id(key),{}).pop('已跳过',None)
        self.selection_revision+=1
        self._render_list('articles',update_detail=False);self.changed.emit()

    def move_article(self,offset):
        article=self.current_article()
        if self._running or article is None:return
        visible=self._filtered('articles');target=visible.index(article)+offset
        if not 0<=target<len(visible):return
        rows=self.bundle['articles'];index=rows.index(article);other=rows.index(visible[target])
        rows[index],rows[other]=rows[other],rows[index]
        self._bundle_digest=''
        self.selection_revision+=1
        self._show_item('articles',article.news.key);self.changed.emit()

    def _source(self, index):
        article=self.source_article()
        self.edit_source.setVisible(bool(article and article.news.manual_id))
        self.open_source.setVisible(bool(article and is_news_url(article.news.url)))
        if article is None:
            self.source_title.clear();self.source_title.hide();self.source_meta.clear();self.source_meta.hide()
            self.details.setPlainText(tr('production.news_empty'))
            self.refresh_image()
            return
        news = article.news
        self.open_source.setToolTip(news.url)
        metadata=' · '.join(filter(None,(news.source,news.published)))
        self.source_title.setText(news.title);self.source_title.show()
        self.source_meta.setText(metadata);self.source_meta.setVisible(bool(metadata))
        summary=f'<p>{escape(news.summary)}</p><hr>' if news.summary else ''
        self.details.setHtml(summary+'<pre style="white-space:pre-wrap">'+escape(article.body)+'</pre>')
        self.refresh_image()

    def _open_source(self):
        article=self.source_article()
        if article and is_news_url(article.news.url):QDesktopServices.openUrl(QUrl(article.news.url))

    def source_article(self):
        item=self.sources.currentItem()
        if item:
            key=item.data(Qt.ItemDataRole.UserRole)
            return next((a for a in self._rows('sources') if a.news.key==key),None)
        return None

    def refresh_image(self):
        from services import news_images
        from services.news_video import selected as selected_video
        article=self.source_article();self.image_preview.clear()
        self.image_download.setEnabled(bool(article and (article.news.url or article.news.images)))
        self.image_change.setEnabled(article is not None)
        self.image_use.setEnabled(article is not None)
        if not article:self.image_preview.setText('暂无配图');self.image_hint.clear();return
        try:
            identifier=news_id(article.news.key);value=news_images.selected(identifier,include_disabled=True)
            row=news_images.load()['news'].get(identifier,{})
            with QSignalBlocker(self.image_use):self.image_use.setChecked(row.get('use_image',True))
            if value:
                path=news_images.thumbnail(identifier,include_disabled=True);pixmap=QPixmap(str(path))
                self.image_preview.setPixmap(pixmap.scaled(480,180,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
                self.image_hint.setText(('已保存配图' if row.get('use_image',True) else '本条不使用配图；图片已保留，可勾选恢复或导入其他图片')+
                    ('；上次操作失败，可重试' if row.get('status')=='failed' else '')+' · 署名和许可见图片详情提示')
                self.image_hint.setToolTip('\n'.join((x.get('url') or '本地导入')+'\n署名：'+str(x.get('credit') or '未知')+'\n许可：'+str(x.get('license') or '未知') for x in value['origins']))
            else:
                self.image_preview.setText('暂无本地配图')
                messages={'failed':'配图下载失败，可重试或导入本地图片','cancelled':'已取消，可继续下载','missing':'暂无可用配图，可导入本地图片','downloading':'上次下载尚未完成，可重试'}
                fallback='可尝试读取原文配图，或导入本地图片' if article.news.url else '可补充本地图片'
                self.image_hint.setText(messages.get(row.get('status')) or ('有候选图片，下载后可离线预览' if article.news.images else fallback))
                self.image_hint.setToolTip(row.get('error') or '')
            video=selected_video(identifier)
            media=[]
            if video:
                from services.news_video import frame_range
                start,end=frame_range(video);fps=video.get('fps',30)
                media.append(f"视频：{video['name']} · {start/fps:.2f}–{end/fps:.2f} 秒")
            if row.get('slideshow'):media.append(f"图片轮播：{len(row['slideshow'])} 张")
            if media:self.image_hint.setText('\n'.join(media+[self.image_hint.text()]))
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.image_preview.setText('配图暂不可用');self.image_hint.setText('请重试或检查图片清单，详情见提示');self.image_hint.setToolTip(str(error))

    def _image_use_changed(self,enabled):
        self.set_image_use(self.source_article(),enabled)

    def set_image_use(self,article,enabled):
        from services import news_images
        if not article:return
        try:
            news_images.set_enabled(article,enabled);self.refresh_image();self.changed.emit()
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.refresh_image();self.image_hint.setText('配图选择未保存，请重试：'+str(error))

    def request_image(self,options,*,article=None):
        article=article or self.source_article()
        if article:self.image_requested.emit(article,options)

    def choose_candidate(self,*,article=None):
        from services.news_images import record
        article=article or self.source_article()
        if not article:return
        rows=record(article)['candidates']
        if not rows:
            message='尚无候选图片，请读取原文配图或添加本地图片。'
            if self.image_hint.isVisible():self.image_hint.setText(message)
            else:QMessageBox.information(self,'新闻配图',message)
            return
        url,ok=QInputDialog.getItem(self,'更换新闻配图','候选图片地址',[r['url'] for r in rows],0,False)
        if ok:self.request_image(dict(candidate_url=url),article=article)

    def import_image(self,*,article=None):
        paths,_=QFileDialog.getOpenFileNames(self,'补充新闻图片','','图片 (*.jpg *.jpeg *.png *.webp *.gif)')
        if paths:self.request_image(dict(files=paths),article=article)

    def edit_gallery(self,*,article=None):
        from services import news_images
        article=article or self.source_article()
        if not article:return
        try:
            data=news_images.load();row=data['news'].get(news_id(article.news.key),{})
            chosen=row.get('slideshow') or ([row['selected']] if row.get('selected') else [])
            order=list(dict.fromkeys(chosen+row.get('assets',[])))
            if not order:raise ValueError('请先导入或下载图片')
            dialog=QDialog(self);dialog.setWindowTitle('选择新闻图片和播放顺序');dialog.resize(520,420)
            layout=QVBoxLayout(dialog);hint=QLabel('勾选图片并排序。多张图片按顺序交叉淡化。有视频时先播放选段，并留出轮播时间。')
            hint.setWordWrap(True);layout.addWidget(hint)
            listing=QListWidget();layout.addWidget(listing)
            for number,sha in enumerate(order,1):
                asset=news_images._resolve(data['images'].get(sha))
                if not asset:continue
                origin=next((o.get('name') or o.get('url') for o in asset['origins'] if o.get('name') or o.get('url')),None)
                item=QListWidgetItem(origin or f'配图 {number}');item.setData(Qt.ItemDataRole.UserRole,sha)
                item.setIcon(QIcon(asset['absolute_path']));item.setCheckState(Qt.CheckState.Checked if sha in chosen else Qt.CheckState.Unchecked)
                listing.addItem(item)
            listing.setIconSize(QSize(72,44))
            def move(by):
                old=listing.currentRow();new=old+by
                if old>=0 and 0<=new<listing.count():listing.insertItem(new,listing.takeItem(old));listing.setCurrentRow(new)
            buttons=QHBoxLayout()
            for label,by in (('上移',-1),('下移',1)):
                button=QPushButton(label);button.clicked.connect(lambda _checked,by=by:move(by));buttons.addWidget(button)
            layout.addLayout(buttons)
            actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
            actions.accepted.connect(dialog.accept);actions.rejected.connect(dialog.reject);layout.addWidget(actions)
            if dialog.exec():
                keys=[listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                      if listing.item(i).checkState()==Qt.CheckState.Checked]
                self.request_image(dict(slideshow=keys),article=article)
        except (OSError,ValueError,KeyError,TypeError) as error:QMessageBox.warning(self,'新闻配图',str(error))

    def import_video(self,*,article=None):
        from resources import resource_path
        path,_=QFileDialog.getOpenFileName(self,'添加新闻视频',str(resource_path('video-downloads')),
                                          '视频 (*.mp4 *.mov *.m4v *.mkv *.webm)')
        if path:self.request_image(dict(video=path),article=article)

    def trim_video(self,*,article=None):
        from services.news_video import selected
        from ui.news_trim_dialog import NewsTrimDialog
        article=article or self.source_article()
        if not article:return
        try:
            asset=selected(news_id(article.news.key))
            if not asset:raise ValueError('请先添加本地视频')
            dialog=NewsTrimDialog(asset,self)
            if dialog.exec():self.request_image(dict(trim=(asset,dialog.first,dialog.end)),article=article)
        except (OSError,ValueError,KeyError,TypeError) as error:QMessageBox.warning(self,'剪裁视频',str(error))

    def open_images(self):
        from resources import resource_path
        path=resource_path('news-images');path.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _article(self, *_):
        article=self.current_article()
        event=self.editorial_event(article) if article else {}
        self.related_button.setVisible(bool(event.get('related')))
        self.unmerge_button.setVisible('merged_checks' in event)
        self.conflict_hint.setText('\n'.join(f'需核对 {label}（{values}）' for label,values in event.get('conflicts',{}).items()))
        self.conflict_hint.setVisible(bool(event.get('conflicts')))
        self._loading = True
        if article:
            summary = self.bundle.get('summaries', {}).get(article.news.key)
            title,body=self._title(article),self._summary(article)
            if self.title.text()!=title:self.title.setText(title)
            if self.summary.toPlainText()!=body:self.summary.setPlainText(body)
            self.origin.setText(tr('production.manual') if article.news.key in self.edited else tr('production.ai_text') if summary else tr('production.original_text'))
            news=article.news
            link=f' · <a href="{escape(news.url,quote=True)}">{escape(news.url)}</a>' if news.url else ''
            self.citation.setHtml(escape(news.source)+link)
        else:
            self.title.clear(); self.summary.clear(); self.citation.clear()
            self.origin.setText(tr('production.news_empty'))
        self.title.setEnabled(article is not None)
        self.summary.setEnabled(article is not None)
        screen=self.voices.get(news_id(article.news.key),{}).get('screen_item',{}) if article else {}
        for key,field in self.screen_fields.items():
            field.setEnabled(article is not None)
            text=screen.get(key,news_voice.screen_subject(self.title.text(),self.summary.toPlainText()) if article and key=='subject' else '')
            field.setText(text)
        self.oral.setEnabled(article is not None)
        self._loading = False
        self.proposal.setEnabled(article is not None and not self._running)
        self._update_script_rows()
        self.refresh_voice();self.article_changed.emit()

    def current_article(self):
        row=self.articles.currentItem()
        if not row or not self.bundle:return None
        url=row.data(Qt.ItemDataRole.UserRole)
        return next((article for article in self.bundle['articles'] if article.news.key==url),None)

    def voice_draft(self,article=None,*,assets=True):
        article=article or self.current_article()
        if article is None or self.voice_settings is None:return None
        identifier=news_id(article.news.key);voice=self.voice_settings()
        if identifier not in self.voices:self.voices[identifier]=news_voice.create(article,voice)
        draft=self.voices[identifier];draft.update(deepcopy(voice))
        ordered=[a.news.key for a in self.selected_articles()]
        draft['screen_item'].update(title=self._title(article),summary=self._summary(article),
                                   source=article.news.source,published=article.news.published,url=article.news.url,
                                   ordinal=ordered.index(article.news.key)+1 if article.news.key in ordered else 1)
        if assets:populate_voice_assets(draft)
        return draft

    def refresh_voice(self):
        article=self.current_article()
        title=(self._title(article) or '未填写标题') if article else '请选择一条新闻'
        self.current_title.setText(title);self.current_title.setToolTip('<p>'+escape(title)+'</p>')
        failure=self.narration_failures.get(news_id(article.news.key)) if article else None
        self.failure_reason.setText(f"{failure.get('状态','失败')} · {failure['原因']}" if failure else '')
        self.failure_reason.setVisible(bool(failure));self.failure_details.setVisible(bool(failure))
        try:
            draft=self.voice_draft(assets=False);text=draft['segments'][0]['spoken'] if draft else ''
            self.proposal.setText('重写本条口播' if text.strip() else '生成本条口播')
            self.spoken_versions.setVisible(bool(draft and draft.get('spoken_history')))
            self.oral.blockSignals(True)
            if self.oral.toPlainText()!=text:self.oral.setPlainText(text)
            self.oral.blockSignals(False)
            self.final_input.setPlainText(reading_text(draft,draft['segments'][0]) if draft else '')
            if draft and (self.script_page.isVisible() or not draft['segments'][0].get('audio')):
                self.voice_hint.setText(news_voice.state(draft))
            else:self.voice_hint.setText('正在检查已有配音' if draft else '请选择一条新闻')
        except (ValueError,KeyError,TypeError) as error:
            self.oral.blockSignals(False);self.final_input.setPlainText('无法用于配音：'+str(error));self.voice_hint.setText('请检查口播或语音设置')

    def _edit_oral(self):
        if self._loading:return
        draft=self.voice_draft(assets=False)
        if draft is None:return
        news_voice.set_spoken(draft,self.oral.toPlainText())
        spoken=bool(draft['segments'][0]['spoken'].strip())
        if spoken:self.narration_failures.pop(draft['news_id'],None)
        self.proposal.setText('重写本条口播' if spoken else '生成本条口播')
        self.oral_modified.emit(draft['segments'][0]['spoken'])
        self._edit_timer.start()

    def _paint_edit(self):
        self._update_script_rows();self.refresh_voice();self.changed.emit()

    def _edit(self):
        if self._loading:
            return
        article=self.current_article()
        if article is None:return
        url = article.news.key
        context = self.bundle.get('context')
        if context:
            context.localized_titles[url] = self.title.text()
        else:
            # Keep the source title; create the normal export context on first manual edit.
            from services.settings import load_settings
            from services.weekly_report import _export_context
            self.bundle['context'] = _export_context(load_settings(), {url: self.title.text()})
        previous = self.bundle.get('summaries', {}).get(url)
        self.bundle.setdefault('summaries', {})[url] = AISummary(self.summary.toPlainText(), previous.keywords if previous else [], previous.categories if previous else [])
        self._bundle_digest=''
        self.edited.add(url)
        identifier=news_id(article.news.key)
        item=self.voices.get(identifier,{}).get('screen_item',{})
        if 'subject' not in item:self.screen_fields['subject'].setText(news_voice.screen_subject(self.title.text(),self.summary.toPlainText()))
        self.origin.setText(tr('production.manual'))
        self._edit_timer.start()

    def _edit_screen_field(self,key,text):
        draft=self.voice_draft(assets=False)
        if draft is not None:
            draft['screen_item'][key]=text.strip()
            self._edit_timer.start()

    def is_selected(self, article):
        return article is not None and article.news.key in self.script_included and self.in_edition(article)

    def selected_articles(self):
        return [a for a in self._rows('articles') if self.is_selected(a)]

    def selected_bundle(self,*,for_write=False):
        if not self.bundle:
            raise ValueError(tr('production.news_empty'))
        included=(self.included|{key for key,value in self.script_preferences.items() if value}) if for_write else self.script_included
        articles = [a for a in (self._rows('sources') if for_write else self._rows('articles'))
                             if a.news.key in included and self.in_edition(a)]
        if not articles and not self.bundle.get('launch_articles'):
            raise ValueError(tr('production.select_content'))
        return deepcopy({**{key:value for key,value in self.bundle.items() if key!='source_articles'},'articles':articles})

    def apply_retry_material(self,refreshed,events):
        for article in self._rows('sources')+self._rows('articles'):
            if fresh:=refreshed.get(article.news.key):
                article.body,article.parser,article.evidence=fresh.body,fresh.parser,fresh.evidence
        if events:self.bundle.setdefault('news_events',{}).update(events)
        if refreshed or events:self._bundle_digest=''

    def blocked(self,article):
        identifier=news_id(article.news.key)
        draft=self.voices.get(identifier)
        return (identifier in self.narration_failures or self.editorial_event(article).get('phase')=='pending') and not (
            draft and draft['segments'][0]['spoken'].strip())

    def set_article_images(self, updated):
        if self.bundle:
            for article in self.bundle.get('source_articles',[])+self.bundle['articles']:
                if article.news.key==updated.news.key:
                    article.news.images=updated.news.images
                    self._bundle_digest=''
        self.refresh_image()

    def snapshot(self, *, for_poll=False):
        if not self._bundle_digest:
            self._serialized_bundle=serialize_news(self.bundle)
            self._bundle_digest=digest(self._serialized_bundle)
        return dict(bundle=self._bundle_digest if for_poll else self._serialized_bundle,
                    included=sorted(self.included),script_included=sorted(self.script_included),edited=sorted(self.edited),
                    script_preferences=dict(self.script_preferences),
                    voices=self.voices if for_poll else deepcopy(self.voices),
                    narration_failures=self.narration_failures if for_poll else deepcopy(self.narration_failures))

    def restore(self, value):
        self.selection_revision+=1
        self.bundle = restore_news(value.get('bundle'))
        self._bundle_digest=''
        self.included = set(value.get('included', []))
        self.script_included=set(value.get('script_included',[a.news.key for a in self.bundle['articles']] if self.bundle else []))
        self.script_preferences=dict(value.get('script_preferences',{
            a.news.key:False for a in self._rows('articles') if a.news.key not in self.script_included}))
        self.edited = set(value.get('edited', []))
        self.voices=deepcopy(value.get('voices',{}))
        self.narration_failures=deepcopy(value.get('narration_failures',{}))
        self.refresh()
