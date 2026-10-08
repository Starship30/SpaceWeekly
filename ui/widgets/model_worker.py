from PySide6.QtCore import QThread, Signal
from ai.providers import list_models


class ModelWorker(QThread):
    result_ready = Signal(object, object, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self):
        try:
            models = list_models(self.config)
        except Exception as exc:
            self.result_ready.emit(self.config, None, str(exc))
        else:
            self.result_ready.emit(self.config, models, "")
