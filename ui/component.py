"""Persistent widgets with a small standalone adapter for existing integrations."""
from PySide6.QtCore import Signal,Qt,QPointF,QRect,QRectF,QUrl
from PySide6.QtGui import QColor,QFont,QFontMetrics,QImage,QPainter,QPainterPath,QPen,QPolygonF,QTextDocument
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QMessageBox,QLabel,QSizePolicy,
    QTabWidget,QTextBrowser,QFrame,QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication,QAbstractItemView)
from ui.theme import TYPE_SIZES


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
        if self.tabText(index)==text:return
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

    def set_icon(self,name,image):
        self.document().addResource(QTextDocument.ResourceType.ImageResource,QUrl('detail:'+name),image)


def detail_icon(name,color):
    image=QImage(24,24,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    tint=QColor(color);tint.setAlpha(21)
    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(tint);painter.drawRoundedRect(QRectF(0,0,24,24),6,6)
    pen=QPen(QColor(color),1.45);pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(pen)
    if name=='location':
        path=QPainterPath();path.moveTo(12,20);path.cubicTo(9,16,6,13,6,10);path.cubicTo(6,2,18,2,18,10);path.cubicTo(18,13,15,16,12,20)
        painter.drawPath(path);painter.drawEllipse(QPointF(12,10),2,2)
    elif name=='orbit':
        painter.drawEllipse(QRectF(4,8,16,8));painter.drawEllipse(QPointF(12,12),3,3)
    else:
        strokes={
            'time': (((5,8),(5,19),(19,19),(19,8),(5,8)),((8,5),(8,10)),((16,5),(16,10)),((5,12),(19,12))),
            'rocket': (((12,4),(16,10),(16,17),(8,17),(8,10),(12,4)),((8,12),(5,17),(8,17)),((16,12),(19,17),(16,17)),((12,17),(12,20))),
            'agency': (((5,19),(5,10),(12,5),(19,10),(19,19),(5,19)),((9,12),(9,17)),((15,12),(15,17))),
            'payload': (((5,9),(12,5),(19,9),(19,17),(12,21),(5,17),(5,9),(12,13),(19,9)),((12,13),(12,21))),
            'count': (((10,5),(8,19)),((16,5),(14,19)),((5,10),(20,10)),((4,15),(19,15))),
        }[name]
        for stroke in strokes:painter.drawPolyline(QPolygonF([QPointF(x,y) for x,y in stroke]))
    painter.end()
    return image


class TableDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        colors=QApplication.instance().property('spaceweeklyThemeColors') or {}
        painter.save();painter.setPen(QColor(colors.get('border','#e7eaf0')))
        painter.drawLine(option.rect.bottomLeft(),option.rect.bottomRight());painter.restore()


class WorkflowNavDelegate(QStyledItemDelegate):
    ICON_STROKES=(
        (((2,22),(2,5),(3,4),(10,4),(14,8),(23,8),(24,9),(24,22),(23,23),(3,23),(2,22)),),
        (((12,4),(4,4),(3,5),(3,23),(4,24),(22,24),(23,23),(23,15)),
         ((10,19),(11,14),(21,3),(24,6),(14,17),(10,19))),
        (((4,4),(23,4),(24,5),(24,22),(23,23),(4,23),(3,22),(3,5),(4,4)),
         ((3,20),(10,13),(14,17),(18,12),(24,18)),((17,8),(18,8))),
        (((3,13),(3,23),(4,24),(22,24),(23,23),(23,13)),
         ((13,20),(13,3)),((7,9),(13,3),(19,9))),
    )

    def paint(self,painter,option,index):
        colors=QApplication.instance().property('spaceweeklyThemeColors') or {}
        active=bool(option.state & QStyle.StateFlag.State_Selected)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        keyboard_focus=bool(option.state & QStyle.StateFlag.State_HasFocus and option.state & QStyle.StateFlag.State_KeyboardFocusChange)
        area=option.rect.adjusted(74,11,-15,-8)
        accent=QColor(colors.get('accent','#4468e8'))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if active or hovered or keyboard_focus:
            painter.setPen(QPen(accent,1) if keyboard_focus else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.get('selection_bg' if active else 'panel_bg','#edf2ff')))
            painter.drawRoundedRect(QRectF(option.rect.adjusted(10,0,-10,0)),9,9)
        if active:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(option.rect.left()-2,option.rect.center().y()-21,12,42),3,3)
        painter.save()
        painter.translate(option.rect.left()+30,option.rect.top()+(option.rect.height()-27)/2)
        pen=QPen(accent if active else QColor(colors.get('text_muted','#46546e')),2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);painter.setPen(pen)
        for stroke in self.ICON_STROKES[index.row()]:
            painter.drawPolyline(QPolygonF([QPointF(x,y) for x,y in stroke]))
        painter.restore()
        for y,height,size,weight,value,color in (
            (0,24,TYPE_SIZES['section_type'],QFont.Weight.DemiBold,index.data(Qt.ItemDataRole.DisplayRole),'text'),
            (25,19,TYPE_SIZES['muted_type'],QFont.Weight.Normal,index.data(Qt.ItemDataRole.AccessibleDescriptionRole) or '','text_muted'),
        ):
            font=painter.font();font.setPixelSize(size);font.setWeight(weight);painter.setFont(font)
            painter.setPen(QColor(colors.get(color,'#46546e')))
            rect=QRect(area.left(),area.top()+y,area.width(),height)
            text=QFontMetrics(font).elidedText(value,Qt.TextElideMode.ElideRight,area.width())
            painter.drawText(rect,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,text)
        painter.restore()


def launch_badge(text):
    state='warning' if '部分' in text else 'error' if '失败' in text else 'success' if '成功' in text else 'muted'
    label={'发射成功':'成功','发射失败':'失败','发射部分失败':'部分失败',
           '已发射，结果待确认':'结果待确认','状态待确认':'待确认'}.get(text,text)
    return label,state


class StatusDelegate(TableDelegate):
    def paint(self,painter,option,index):
        background=QStyleOptionViewItem(option);self.initStyleOption(background,index)
        text=background.text;background.text=''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,background,painter,option.widget)
        colors=QApplication.instance().property('spaceweeklyThemeColors') or {}
        text,state=launch_badge(text)
        color=QColor(colors.get('log_'+state,colors.get('text_muted','#687386')))
        fill=QColor(color);fill.setAlpha(24)
        width=min(option.rect.width()-12,option.fontMetrics.horizontalAdvance(text)+16)
        rect=QRectF(option.rect.center().x()-width/2,option.rect.center().y()-13,width,26)
        painter.save();painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(fill);painter.drawRoundedRect(rect,4,4)
        painter.setPen(color);painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text);painter.restore()


def configure_table(table):
    table.setShowGrid(False);table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(48);table.verticalHeader().setMinimumSectionSize(42)
    table.horizontalHeader().setHighlightSections(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setTextElideMode(Qt.TextElideMode.ElideRight);table.setWordWrap(False)
    table.setItemDelegate(TableDelegate(table))


class AspectPreview(QLabel):
    """Keep the existing rendered frame inside its resizable preview pane."""
    image_clicked=Signal(QPointF)

    def __init__(self,text):
        super().__init__(text);self.image=None
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)

    def set_image(self,image):
        self.image=image;self._scale()

    def _scale(self):
        if self.image is not None:self.setPixmap(self.image.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event);self._scale()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.image is not None:
            size=self.pixmap().deviceIndependentSize().toSize()
            area=self.contentsRect().adjusted(self.margin(),self.margin(),-self.margin(),-self.margin())
            rect=QStyle.alignedRect(self.layoutDirection(),self.alignment(),size,area)
            point=event.position()
            if rect.contains(point.toPoint()):
                self.image_clicked.emit(QPointF((point.x()-rect.x())/rect.width(),(point.y()-rect.y())/rect.height()))
        super().mouseReleaseEvent(event)


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
