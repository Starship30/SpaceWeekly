"""Two views of the same prepared news bundle; rendering/export stay in services."""
from copy import deepcopy
from html import escape

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtGui import QPixmap,QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QTextBrowser, QLineEdit, QPlainTextEdit, QLabel, QMessageBox,QFileDialog,QMenu,QInputDialog,
    QCheckBox,QToolButton,QDialog,QSizePolicy)

from i18n import tr
from models.ai_summary import AISummary
from services.production import serialize_news, restore_news
from services import news_voice
from services.roundup_audio import reading_text
from models.news import news_id
from ui.component import SegmentedTabs


class NewsWorkspace(QWidget):
    collect_requested = Signal()
    write_requested = Signal()
    changed = Signal()
    narration_requested=Signal()
    voice_requested=Signal(str)
    retry_requested=Signal()
    diagnostic_requested=Signal(object)
    article_changed=Signal()
    image_requested=Signal(object,object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bundle = None
        self.included = set()
        self.script_included = set()
        self.edited = set()
        self._running = False
        self.voices={};self.voice_settings=None;self.narration_failures={}
        self._loading = False
        self.data_page = QWidget()
        layout = QVBoxLayout(self.data_page)
        row = QHBoxLayout()
        self.collect = QPushButton(tr('production.collect_news'))
        self.collect.setObjectName('PrimaryButton')
        self.collect.clicked.connect(self.collect_requested.emit)
        row.addWidget(self.collect)
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
        split.addWidget(self.sources)
        detail=QWidget();detail_layout=QVBoxLayout(detail);self.image_preview=QLabel('暂无配图')
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.image_preview.setMinimumHeight(100);self.image_preview.setMaximumHeight(190)
        self.image_hint=QLabel();self.image_hint.setWordWrap(True);detail_layout.addWidget(self.image_preview);detail_layout.addWidget(self.image_hint)
        self.image_use=QCheckBox('使用本条配图');self.image_use.setToolTip('取消后画面与导出不使用配图，已保存的图片保留；可自行寻找并导入合适的图片。')
        self.image_use.toggled.connect(self._image_use_changed);detail_layout.addWidget(self.image_use)
        actions=QHBoxLayout();self.image_download=QPushButton('下载 / 重试配图');self.image_download.clicked.connect(lambda:self.request_image({}))
        self.image_change=QPushButton('更换图片');menu=QMenu(self.image_change)
        menu.addAction('选择候选图片',self.choose_candidate);menu.addAction('补充本地图片',self.import_image);self.image_change.setMenu(menu)
        folder=QPushButton('打开图片文件夹');folder.clicked.connect(self.open_images)
        for button in (self.image_download,self.image_change,folder):actions.addWidget(button)
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
        list_heading=QLabel('本期新闻');list_heading.setObjectName('SectionTitle');list_layout.addWidget(list_heading)
        controls=QHBoxLayout();self.script_actions=[]
        for text,action in (('全选',lambda:self._check_scripts(True)),('全不选',lambda:self._check_scripts(False)),
                            ('上移',lambda:self.move_article(-1)),('下移',lambda:self.move_article(1))):
            button=QToolButton();button.setText(text);button.clicked.connect(action);controls.addWidget(button);self.script_actions.append(button)
        self.script_actions[2].setShortcut('Alt+Up');self.script_actions[3].setShortcut('Alt+Down')
        controls.addStretch();list_layout.addLayout(controls)
        self.articles = QListWidget()
        self.articles.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.articles.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.articles.itemChanged.connect(self._choose_script)
        self.articles.currentRowChanged.connect(self._article)
        list_layout.addWidget(self.articles,1)
        self.selection_hint=QLabel();self.selection_hint.setWordWrap(True);list_layout.addWidget(self.selection_hint)
        self.selection_hint.setToolTip('勾选决定本期导出和失败重试的范围，高亮行决定右侧正在编辑的新闻。')
        self.retry=QPushButton('重试失败项')
        self.retry.setToolTip('仅重试本期已勾选且口播生成失败的新闻。')
        self.retry.clicked.connect(self.retry_requested.emit)
        list_layout.addWidget(self.retry,0,Qt.AlignmentFlag.AlignLeft)
        list_page.setMinimumWidth(260);split.addWidget(list_page)
        right_page=QWidget();right_layout=QVBoxLayout(right_page);right_layout.setContentsMargins(0,0,0,0)
        self.current_title=QLabel();self.current_title.setObjectName('SectionTitle')
        self.current_title.setAccessibleName('当前新闻标题')
        self.current_title.setTextFormat(Qt.TextFormat.PlainText);self.current_title.setWordWrap(True)
        self.current_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        right_layout.addWidget(self.current_title)
        editor = QWidget()
        form = QVBoxLayout(editor)
        form.setContentsMargins(0,8,0,0)
        form.addWidget(QLabel(tr('production.title')))
        self.title = QLineEdit()
        form.addWidget(self.title)
        form.addWidget(QLabel(tr('production.summary')))
        self.summary = QPlainTextEdit()
        form.addWidget(self.summary, 1)
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
        self.editor_tabs.addTab(voice_page,'口播稿');self.editor_tabs.addTab(editor,'屏幕文字与摘要')
        right_layout.addWidget(self.editor_tabs,1);split.addWidget(right_page)
        split.setStretchFactor(1, 3)
        split.setSizes([290,680])
        layout.addWidget(split, 1)
        self.title.textChanged.connect(self._edit)
        self.summary.textChanged.connect(self._edit)
        self._article()

    def set_running(self,running):
        self._running=running
        self.collect.setEnabled(not running)
        self.proposal.setEnabled(not running and self.current_article() is not None)
        self._update_script_rows()
        self.title.setReadOnly(running);self.summary.setReadOnly(running);self.oral.setReadOnly(running)
        self._loading=True
        for index in range(self.sources.count()):
            item=self.sources.item(index);flag=Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(item.flags() & ~flag if running else item.flags() | flag)
        self._loading=False

    def _generate(self):
        if self.edited and QMessageBox.question(self, tr('production.script'), tr('production.overwrite_news')) != QMessageBox.StandardButton.Yes:
            return
        self.write_requested.emit()

    def adopt(self, bundle, *, collected=False):
        proposals=bundle.pop('narration_proposals',{})
        failures=bundle.pop('narration_failures',{})
        states=bundle.pop('narration_states',{})
        if collected:self.batch_hint.hide()
        previous=[a.news.url for a in self.bundle['articles']] if self.bundle and not collected else []
        if collected or not self.bundle:
            bundle['source_articles']=list(bundle['articles'])
        else:
            originals=self.bundle.get('source_articles',self.bundle['articles'])
            verified={a.news.url:a.evidence for a in bundle['articles'] if a.evidence}
            for article in originals:
                if article.news.url in verified:article.evidence=verified[article.news.url]
            by_url={a.news.url:a for a in originals}
            bundle['articles']=[by_url.get(a.news.url,a) for a in bundle['articles']]
            bundle['source_articles']=originals
            by_url={a.news.url:a for a in bundle['articles']}
            bundle['articles']=[by_url[url] for url in previous if url in by_url]+[a for a in bundle['articles'] if a.news.url not in previous]
        self.bundle = bundle
        self.included = {a.news.url for a in bundle['articles']}
        self.script_included=(self.script_included & self.included)|(self.included-set(previous))
        self.edited.clear()
        self.apply_narrations(proposals,failures,states)
        self.refresh()
        self.changed.emit()

    def apply_narrations(self,proposals,failures,states):
        self.narration_failures.update(failures)
        if proposals and self.voice_settings is not None:
            voice=self.voice_settings()
            for article in self.bundle['articles']:
                identifier=news_id(article.news.url)
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
        for article in (self.bundle or {}).get('articles',[]):
            identifier=news_id(article.news.url)
            draft=self.voices.get(identifier)
            if article.news.url in self.script_included and identifier in self.narration_failures:
                if not draft or not news_voice.has_existing_spoken(draft):failed.append(article)
        return failed

    def select_article(self,identifier):
        for index in range(self.articles.count()):
            if news_id(self.articles.item(index).data(Qt.ItemDataRole.UserRole))==identifier:
                self.articles.setCurrentRow(index)
                return True
        return False

    def _go_voice(self):
        article=self.current_article()
        if article:self.voice_requested.emit(news_id(article.news.url))

    def _show_failure(self):
        article=self.current_article()
        failure=self.narration_failures.get(news_id(article.news.url)) if article else None
        if failure:self.diagnostic_requested.emit(failure)

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

    def refresh(self):
        item=self.articles.currentItem()
        previous=item.data(Qt.ItemDataRole.UserRole) if item else None
        self._loading = True
        with QSignalBlocker(self.sources),QSignalBlocker(self.articles):
            self.sources.clear()
            self.articles.clear()
        if self.bundle:
            for article in self.bundle.get('source_articles',self.bundle['articles']):
                item = QListWidgetItem(article.news.title)
                item.setToolTip(article.news.title)
                item.setCheckState(Qt.CheckState.Checked if article.news.url in self.included else Qt.CheckState.Unchecked)
                self.sources.addItem(item)
            for article in self.bundle['articles']:
                item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,article.news.url)
                item.setCheckState(Qt.CheckState.Checked if article.news.url in self.script_included else Qt.CheckState.Unchecked)
                self.articles.addItem(item)
        count = self.sources.count()
        self.data_hint.setText(tr('production.news_count', count=count) if count else tr('production.news_empty'))
        self._loading = False
        self._update_script_rows()
        self.sources.setCurrentRow(0 if count else -1)
        if previous is None or not self.select_article(news_id(previous)):
            self.articles.setCurrentRow(0 if self.articles.count() else -1)
        if not self.articles.count():
            self._article()

    def _title(self, article):
        context = self.bundle.get('context')
        return context.localized_titles.get(article.news.url, article.news.title) if context else article.news.title

    def _choose(self, *_):
        if self._loading or not self.bundle:
            return
        self.included = {a.news.url for n, a in enumerate(self.bundle.get('source_articles',self.bundle['articles'])) if self.sources.item(n).checkState() == Qt.CheckState.Checked}
        self._update_batch_scope()
        self.changed.emit()

    def _update_batch_scope(self):
        bundle=self.bundle or {}
        sources=bundle.get('source_articles',bundle.get('articles',[]))
        count=sum(article.news.url in self.included for article in sources)
        launches=len(bundle.get('launch_articles',[]))
        self.generate.setText(f'生成新闻稿（{count} 条）')
        self.generate.setEnabled(not self._running and bool(count or launches))
        self.script_hint.setText('生成范围取自资料页勾选'+(f'，另含 {launches} 项发射资料' if launches else ''))

    def _update_script_rows(self):
        rows=self.bundle['articles'] if self.bundle else []
        number=0
        with QSignalBlocker(self.articles):
            for article,item in zip(rows,[self.articles.item(i) for i in range(self.articles.count())]):
                included=article.news.url in self.script_included
                if included:number+=1
                title=self._title(article);identifier=news_id(article.news.url);draft=self.voices.get(identifier)
                status=('失败' if identifier in self.narration_failures else '待生成'
                        if not draft or not draft['segments'][0]['spoken'].strip() else
                        {'generated':'已生成','manual':'人工修改'}.get(draft.get('oral_origin'),'已有口播'))
                item.setText((f'{number:02d}  ' if included else '')+status+' · '+title);item.setToolTip(status+' · '+title)
                flag=Qt.ItemFlag.ItemIsUserCheckable
                item.setFlags(item.flags() & ~flag if self._running else item.flags() | flag)
        self.selection_hint.setText(f'已勾选 {number} / {len(rows)} 条，素材按此顺序编号。')
        index=self.articles.currentRow()
        for button in self.script_actions[:2]:button.setEnabled(not self._running and bool(rows))
        self.script_actions[2].setEnabled(not self._running and index>0)
        self.script_actions[3].setEnabled(not self._running and 0<=index<len(rows)-1)
        failed=len(self.failed_articles())
        self.retry.setText(f'重试失败项（{failed} 条）')
        self.retry.setEnabled(not self._running and bool(failed))
        self._update_batch_scope()

    def _choose_script(self, *_):
        if self._loading or self._running:return
        self.script_included={self.articles.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.articles.count())
                              if self.articles.item(i).checkState()==Qt.CheckState.Checked}
        self._update_script_rows();self.changed.emit()

    def _check_scripts(self,checked):
        if self._running:return
        self.script_included={a.news.url for a in self.bundle['articles']} if checked and self.bundle else set()
        with QSignalBlocker(self.articles):
            for i in range(self.articles.count()):self.articles.item(i).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_script_rows();self.changed.emit()

    def move_article(self,offset):
        index=self.articles.currentRow();target=index+offset
        if self._running or not self.bundle or not 0<=index<len(self.bundle['articles']) or not 0<=target<len(self.bundle['articles']):return
        identifier=news_id(self.articles.item(index).data(Qt.ItemDataRole.UserRole))
        with QSignalBlocker(self.articles):
            self.bundle['articles'].insert(target,self.bundle['articles'].pop(index))
            item=self.articles.takeItem(index);self.articles.insertItem(target,item)
            self.select_article(identifier)
        self._article();self.changed.emit()

    def _source(self, index):
        if not self.bundle or index < 0:
            self.details.setPlainText(tr('production.news_empty'))
            self.refresh_image()
            return
        article = self.bundle.get('source_articles',self.bundle['articles'])[index]
        news = article.news
        self.details.setHtml('<h3>'+escape(news.title)+'</h3><p>'+escape(news.source)+' · '+escape(news.published)+'</p><p><a href="'+escape(news.url, quote=True)+'">'+escape(news.url)+'</a></p><p>'+escape(news.summary)+'</p><hr><pre style="white-space:pre-wrap">'+escape(article.body)+'</pre>')
        self.refresh_image()

    def source_article(self):
        n=self.sources.currentRow()
        rows=self.bundle.get('source_articles',self.bundle['articles']) if self.bundle else []
        return rows[n] if 0<=n<len(rows) else None

    def refresh_image(self):
        from services import news_images
        article=self.source_article();self.image_preview.clear()
        self.image_download.setEnabled(article is not None);self.image_change.setEnabled(article is not None)
        self.image_use.setEnabled(article is not None)
        if not article:self.image_preview.setText('暂无配图');self.image_hint.clear();return
        try:
            identifier=news_id(article.news.url);value=news_images.selected(identifier,include_disabled=True)
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
                self.image_hint.setText(messages.get(row.get('status')) or ('有候选图片，下载后可离线预览' if article.news.images else '可尝试读取原文配图，或导入本地图片'))
                self.image_hint.setToolTip(row.get('error') or '')
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

    def request_image(self,options):
        article=self.source_article()
        if article:self.image_requested.emit(article,options)

    def choose_candidate(self):
        from services.news_images import record
        article=self.source_article()
        if not article:return
        rows=record(article)['candidates']
        if not rows:self.image_hint.setText('尚无候选地址，请下载尝试原文配图，或补充本地图片');return
        url,ok=QInputDialog.getItem(self,'更换新闻配图','候选图片地址',[r['url'] for r in rows],0,False)
        if ok:self.request_image(dict(candidate_url=url))

    def import_image(self):
        path,_=QFileDialog.getOpenFileName(self,'补充新闻图片','','图片 (*.jpg *.jpeg *.png *.webp *.gif)')
        if path:self.request_image(dict(local=path))

    def open_images(self):
        from resources import resource_path
        path=resource_path('news-images');path.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _article(self, *_):
        article=self.current_article()
        self._loading = True
        if article:
            summary = self.bundle.get('summaries', {}).get(article.news.url)
            self.title.setText(self._title(article))
            self.summary.setPlainText(summary.summary if summary else article.news.summary)
            self.origin.setText(tr('production.manual') if article.news.url in self.edited else tr('production.ai_text') if summary else tr('production.original_text'))
            self.citation.setHtml(escape(article.news.source)+' · <a href="'+escape(article.news.url, quote=True)+'">'+escape(article.news.url)+'</a>')
        else:
            self.title.clear(); self.summary.clear(); self.citation.clear()
            self.origin.setText(tr('production.news_empty'))
        self.title.setEnabled(article is not None)
        self.summary.setEnabled(article is not None)
        self.oral.setEnabled(article is not None);self.go_voice.setEnabled(article is not None)
        self._loading = False
        self.proposal.setEnabled(article is not None and not self._running)
        self._update_script_rows()
        self.refresh_voice();self.article_changed.emit()

    def current_article(self):
        row=self.articles.currentItem()
        if not row or not self.bundle:return None
        url=row.data(Qt.ItemDataRole.UserRole)
        return next((article for article in self.bundle['articles'] if article.news.url==url),None)

    def voice_draft(self,article=None):
        article=article or self.current_article()
        if article is None or self.voice_settings is None:return None
        identifier=news_id(article.news.url);voice=self.voice_settings()
        if identifier not in self.voices:self.voices[identifier]=news_voice.create(article,voice)
        draft=self.voices[identifier];draft.update(deepcopy(voice))
        summary=self.bundle.get('summaries',{}).get(article.news.url)
        ordered=[a.news.url for a in self.bundle['articles'] if a.news.url in self.script_included]
        draft['screen_item'].update(title=self._title(article),summary=summary.summary if summary else article.news.summary,
                                   ordinal=ordered.index(article.news.url)+1 if article.news.url in ordered else 1)
        from services.news_images import selected
        try:image=selected(identifier)
        except (OSError,ValueError,KeyError,TypeError):image=None
        draft['screen_item']['image']=dict(path=image['absolute_path'],sha256=image['sha256']) if image else None
        return draft

    def refresh_voice(self):
        article=self.current_article()
        title=(self._title(article) or '未填写标题') if article else '请选择一条新闻'
        self.current_title.setText(title);self.current_title.setToolTip('<p>'+escape(title)+'</p>')
        failure=self.narration_failures.get(news_id(article.news.url)) if article else None
        self.failure_reason.setText(failure['原因'] if failure else '')
        self.failure_reason.setVisible(bool(failure));self.failure_details.setVisible(bool(failure))
        try:
            draft=self.voice_draft();text=draft['segments'][0]['spoken'] if draft else ''
            self.proposal.setText('重写本条口播' if text.strip() else '生成本条口播')
            self.spoken_versions.setVisible(bool(draft and draft.get('spoken_history')))
            self.oral.blockSignals(True)
            if self.oral.toPlainText()!=text:self.oral.setPlainText(text)
            self.oral.blockSignals(False)
            self.final_input.setPlainText(reading_text(draft,draft['segments'][0]) if draft else '')
            self.voice_hint.setText(news_voice.state(draft) if draft else '请选择一条新闻')
        except (ValueError,KeyError,TypeError) as error:
            self.oral.blockSignals(False);self.final_input.setPlainText('无法用于配音：'+str(error));self.voice_hint.setText('请检查口播或语音设置')

    def _edit_oral(self):
        if self._loading:return
        draft=self.voice_draft()
        if draft is None:return
        news_voice.set_spoken(draft,self.oral.toPlainText())
        if draft['segments'][0]['spoken'].strip():self.narration_failures.pop(draft['news_id'],None)
        self._update_script_rows();self.refresh_voice();self.changed.emit()

    def _edit(self):
        if self._loading:
            return
        article=self.current_article()
        if article is None:return
        url = article.news.url
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
        self.edited.add(url)
        self.origin.setText(tr('production.manual'))
        self._update_script_rows()
        self.refresh_voice()
        self.changed.emit()

    def selected_bundle(self,*,for_write=False):
        if not self.bundle:
            raise ValueError(tr('production.news_empty'))
        value = deepcopy(self.bundle)
        included=self.included if for_write else self.script_included
        value['articles'] = [a for a in (value.get('source_articles',value['articles']) if for_write else value['articles']) if a.news.url in included]
        if not value['articles'] and not value.get('launch_articles'):
            raise ValueError(tr('production.select_content'))
        return value

    def snapshot(self):
        return dict(bundle=serialize_news(self.bundle), included=sorted(self.included), script_included=sorted(self.script_included),
                    edited=sorted(self.edited),voices=deepcopy(self.voices),narration_failures=deepcopy(self.narration_failures))

    def restore(self, value):
        self.bundle = restore_news(value.get('bundle'))
        self.included = set(value.get('included', []))
        self.script_included=set(value.get('script_included',[a.news.url for a in self.bundle['articles']] if self.bundle else []))
        self.edited = set(value.get('edited', []))
        self.voices=deepcopy(value.get('voices',{}))
        self.narration_failures=deepcopy(value.get('narration_failures',{}))
        self.refresh()
