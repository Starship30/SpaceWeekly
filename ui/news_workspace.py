"""Two views of the same prepared news bundle; rendering/export stay in services."""
from copy import deepcopy
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QTextBrowser, QLineEdit, QPlainTextEdit, QLabel, QMessageBox)

from i18n import tr
from models.ai_summary import AISummary
from services.production import serialize_news, restore_news


class NewsWorkspace(QWidget):
    collect_requested = Signal()
    write_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bundle = None
        self.included = set()
        self.edited = set()
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
        self.sources.itemChanged.connect(self._choose)
        self.sources.currentRowChanged.connect(self._source)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        split.addWidget(self.sources)
        split.addWidget(self.details)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        self.script_page = QWidget()
        layout = QVBoxLayout(self.script_page)
        row = QHBoxLayout()
        self.generate = QPushButton(tr('production.write_news'))
        self.generate.setObjectName('PrimaryButton')
        self.generate.clicked.connect(self._generate)
        row.addWidget(self.generate)
        self.script_hint = QLabel(tr('production.news_empty'))
        self.script_hint.setWordWrap(True)
        row.addWidget(self.script_hint, 1)
        layout.addLayout(row)
        split = QSplitter()
        self.articles = QListWidget()
        self.articles.currentRowChanged.connect(self._article)
        split.addWidget(self.articles)
        editor = QWidget()
        form = QVBoxLayout(editor)
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
        split.addWidget(editor)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        self.title.textChanged.connect(self._edit)
        self.summary.textChanged.connect(self._edit)
        self._index = -1
        self._article(-1)

    def set_running(self,running):
        self.collect.setEnabled(not running);self.generate.setEnabled(not running)
        self.title.setReadOnly(running);self.summary.setReadOnly(running)
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
        if collected or not self.bundle:
            bundle['source_articles']=bundle['articles']
        else:
            originals=self.bundle.get('source_articles',self.bundle['articles'])
            by_url={a.news.url:a for a in originals}
            bundle['articles']=[by_url.get(a.news.url,a) for a in bundle['articles']]
            bundle['source_articles']=originals
        self.bundle = bundle
        self.included = {a.news.url for a in bundle['articles']}
        self.edited.clear()
        self.refresh()
        self.changed.emit()

    def refresh(self):
        self._loading = True
        self.sources.clear()
        self.articles.clear()
        if self.bundle:
            for article in self.bundle.get('source_articles',self.bundle['articles']):
                item = QListWidgetItem(article.news.title)
                item.setCheckState(Qt.CheckState.Checked if article.news.url in self.included else Qt.CheckState.Unchecked)
                self.sources.addItem(item)
            for article in self.bundle['articles']:self.articles.addItem(self._title(article))
        count = self.sources.count()
        self.data_hint.setText(tr('production.news_count', count=count) if count else tr('production.news_empty'))
        self.script_hint.setText(tr('production.news_prepared') if self.bundle and self.bundle.get('context') else tr('production.news_unwritten'))
        self._loading = False
        self.sources.setCurrentRow(0 if count else -1)
        self.articles.setCurrentRow(0 if count else -1)
        if not count:
            self._article(-1)

    def _title(self, article):
        context = self.bundle.get('context')
        return context.localized_titles.get(article.news.url, article.news.title) if context else article.news.title

    def _choose(self, *_):
        if self._loading or not self.bundle:
            return
        self.included = {a.news.url for n, a in enumerate(self.bundle.get('source_articles',self.bundle['articles'])) if self.sources.item(n).checkState() == Qt.CheckState.Checked}
        self.changed.emit()

    def _source(self, index):
        if not self.bundle or index < 0:
            self.details.setPlainText(tr('production.news_empty'))
            return
        article = self.bundle.get('source_articles',self.bundle['articles'])[index]
        news = article.news
        self.details.setHtml('<h3>'+escape(news.title)+'</h3><p>'+escape(news.source)+' · '+escape(news.published)+'</p><p><a href="'+escape(news.url, quote=True)+'">'+escape(news.url)+'</a></p><p>'+escape(news.summary)+'</p><hr><pre style="white-space:pre-wrap">'+escape(article.body)+'</pre>')

    def _article(self, index):
        self._index = index
        self._loading = True
        if self.bundle and index >= 0:
            article = self.bundle['articles'][index]
            summary = self.bundle.get('summaries', {}).get(article.news.url)
            self.title.setText(self._title(article))
            self.summary.setPlainText(summary.summary if summary else article.news.summary)
            self.origin.setText(tr('production.manual') if article.news.url in self.edited else tr('production.ai_text') if summary else tr('production.original_text'))
            self.citation.setHtml(escape(article.news.source)+' · <a href="'+escape(article.news.url, quote=True)+'">'+escape(article.news.url)+'</a>')
        else:
            self.title.clear(); self.summary.clear(); self.citation.clear()
            self.origin.setText(tr('production.news_empty'))
        self.title.setEnabled(index >= 0)
        self.summary.setEnabled(index >= 0)
        self._loading = False

    def _edit(self):
        if self._loading or self._index < 0 or not self.bundle:
            return
        article = self.bundle['articles'][self._index]
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
        self.articles.item(self._index).setText(self.title.text())
        self.changed.emit()

    def selected_bundle(self,*,for_write=False):
        if not self.bundle:
            raise ValueError(tr('production.news_empty'))
        value = deepcopy(self.bundle)
        value['articles'] = [a for a in (value.get('source_articles',value['articles']) if for_write else value['articles']) if a.news.url in self.included]
        if not value['articles'] and not value.get('launch_articles'):
            raise ValueError(tr('production.select_content'))
        return value

    def snapshot(self):
        return dict(bundle=serialize_news(self.bundle), included=sorted(self.included), edited=sorted(self.edited))

    def restore(self, value):
        self.bundle = restore_news(value.get('bundle'))
        self.included = set(value.get('included', []))
        self.edited = set(value.get('edited', []))
        self.refresh()
