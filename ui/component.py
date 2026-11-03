"""Persistent widgets with a small standalone adapter for existing integrations."""
from PySide6.QtCore import Signal,Qt,QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QMessageBox,QLabel,QSizePolicy,
    QTabWidget,QTextBrowser,QFrame,QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication,QAbstractItemView)


class SegmentedTabs(QTabWidget):
    """Flat, keyboard-accessible navigation over the existing page instances."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('SegmentedTabs');self.setDocumentMode(True)
        self.setElideMode(Qt.TextElideMode.ElideRight);self.setUsesScrollButtons(True)
        self.tabBar().setObjectName('SegmentedNav');self.tabBar().setDrawBase(False)
        self.tabBar().setExpanding(False)

    def _nav_width(self):
        self.tabBar().setMaximumWidth(min(self.width(),self.tabBar().sizeHint().width()))

    def tabInserted(self,index):
        super().tabInserted(index);self._nav_width()

    def setTabText(self,index,text):
        super().setTabText(index,text);self._nav_width()

    def resizeEvent(self,event):
        super().resizeEvent(event);self._nav_width()


class ContentBrowser(QTextBrowser):
    """Size short details to their content; the surrounding pane owns scrolling."""
    def __init__(self):
        super().__init__();self.setObjectName('ContentBrowser');self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._height)

    def _height(self,*_):
        self.setFixedHeight(max(100,int(self.document().size().height())+8))

    def resizeEvent(self,event):
        super().resizeEvent(event);self._height()

    def setHtml(self,value):
        self.document().setDefaultStyleSheet('h2 { font-size:22px; margin:8px 0 12px; } p { margin:10px 0; }')
        super().setHtml(value.replace('<hr>','<br>'))


class TableDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        colors=QApplication.instance().property('spaceweeklyThemeColors') or {}
        painter.save();painter.setPen(QColor(colors.get('border','#e7eaf0')))
        painter.drawLine(option.rect.bottomLeft(),option.rect.bottomRight());painter.restore()


class StatusDelegate(TableDelegate):
    def paint(self,painter,option,index):
        background=QStyleOptionViewItem(option);self.initStyleOption(background,index)
        text=background.text;background.text=''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,background,painter,option.widget)
        colors=QApplication.instance().property('spaceweeklyThemeColors') or {}
        state='warning' if '部分' in text else 'error' if '失败' in text else 'success' if '成功' in text else 'muted'
        text={'发射成功':'成功','发射失败':'失败','发射部分失败':'部分失败',
              '已发射，结果待确认':'结果待确认','状态待确认':'待确认'}.get(text,text)
        color=QColor(colors.get('log_'+state,colors.get('text_muted','#687386')))
        fill=QColor(color);fill.setAlpha(24)
        width=min(option.rect.width()-12,option.fontMetrics.horizontalAdvance(text)+16)
        rect=QRectF(option.rect.center().x()-width/2,option.rect.center().y()-13,width,26)
        painter.save();painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(fill);painter.drawRoundedRect(rect,4,4)
        painter.setPen(color);painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text);painter.restore()


def configure_table(table):
    table.setShowGrid(False);table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(46);table.verticalHeader().setMinimumSectionSize(44)
    table.horizontalHeader().setHighlightSections(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setTextElideMode(Qt.TextElideMode.ElideRight);table.setWordWrap(False)
    table.setItemDelegate(TableDelegate(table))


class AspectPreview(QLabel):
    """Keep the existing rendered frame inside its resizable preview pane."""
    def __init__(self,text):
        super().__init__(text);self.image=None
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)

    def set_image(self,image):
        self.image=image;self._scale()

    def _scale(self):
        if self.image is not None:self.setPixmap(self.image.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event);self._scale()


class ProductionComponent(QWidget):
    accepted = Signal()
    rejected = Signal()

    def report_error(self,title,message):
        if hasattr(self,'inline_error'):
            self.inline_error(message)
        else:
            QMessageBox.warning(self,title,message)

    def accept(self):
        self.accepted.emit()
        if not getattr(self,'workflow_embedded',False):self.hide()

    def reject(self):
        self.rejected.emit()
        if not getattr(self,'workflow_embedded',False):self.hide()

    def exec(self):
        """Legacy standalone callers; the production workspace embeds widgets directly."""
        parent = self.parentWidget()
        component=self
        class Host(QDialog):
            def reject(self):component.reject()
            def closeEvent(self,event):
                event.ignore();self.reject()
        dialog = Host(parent)
        dialog.setWindowTitle(self.windowTitle())
        dialog.resize(self.size())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self)
        accept=lambda:dialog.done(QDialog.DialogCode.Accepted)
        reject=lambda:dialog.done(QDialog.DialogCode.Rejected)
        self.accepted.connect(accept)
        self.rejected.connect(reject)
        try:
            return dialog.exec()
        finally:
            self.accepted.disconnect(accept)
            self.rejected.disconnect(reject)
            self.setParent(parent)
            dialog.deleteLater()
