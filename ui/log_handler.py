import logging
from html import escape

from PySide6.QtCore import QObject
from PySide6.QtCore import Signal

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    """Forward Python logging records to a Qt signal."""

    def __init__(self) -> None:
        super().__init__()
        self.emitter = LogEmitter()
        self.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                "%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        level_class = record.levelname.lower()
        message = escape(self.format(record))
        self.emitter.message.emit(
            f'<span class="{level_class}">{message}</span>'
        )
