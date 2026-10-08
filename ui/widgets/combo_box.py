from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QComboBox, QFrame, QListView, QSizePolicy


class RoundedComboBox(QComboBox):
    """Use a styled list popup instead of the platform menu frame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView()
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setSpacing(3)
        self.setView(view)
        self.setMaxVisibleItems(10)

    def showPopup(self):
        popup = self.view().window()
        popup.setObjectName("ComboPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)
        super().showPopup()
        # Qt's native menu margins surround the list with dark top/bottom strips.
        layout = popup.layout()
        for index in range(layout.count()):
            spacer = layout.itemAt(index).spacerItem()
            if spacer is not None:
                spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.invalidate()
        layout.activate()
        path = QPainterPath()
        path.addRoundedRect(QRectF(popup.rect()), 12, 12)
        popup.setMask(QRegion(path.toFillPolygon().toPolygon()))
