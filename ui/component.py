"""Persistent widgets with a small standalone adapter for existing integrations."""
from PySide6.QtCore import Signal,Qt
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QMessageBox,QLabel,QSizePolicy


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
