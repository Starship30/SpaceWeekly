import logging
import re
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from resources import resource_path

_LOCK = Lock()
_CONFIGURED = False


class _ErrorReportHandler(logging.Handler):
    def __init__(self, errors_dir: Path) -> None:
        super().__init__(logging.ERROR)
        self.errors_dir = errors_dir

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.errors_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = self.errors_dir / f"error_{timestamp}_{uuid4().hex[:6]}.log"
            exception = ""

            if record.exc_info:
                exception = "".join(traceback.format_exception(*record.exc_info))

            model = getattr(record, "model", "") or _current_model()
            body = (
                f"time: {datetime.now().astimezone().isoformat()}\n"
                f"module: {record.name}\n"
                f"error_type: {record.exc_info[0].__name__ if record.exc_info else record.levelname}\n"
                f"model: {model}\n"
                f"message: {_redact(record.getMessage())}\n\n"
                f"traceback:\n{_redact(exception) or '-'}"
            )
            path.write_text(body, encoding="utf-8")
        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Configure console, daily, latest, and per-error diagnostic logs."""

    global _CONFIGURED

    with _LOCK:
        if _CONFIGURED:
            return

        logs_dir = resource_path("logs")
        errors_dir = resource_path("errors")
        logs_dir.mkdir(parents=True, exist_ok=True)
        errors_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )

        if not root.handlers:
            console = logging.StreamHandler()
            console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            root.addHandler(console)

        for path, mode in [
            (logs_dir / "latest.log", "w"),
            (logs_dir / f"{datetime.now():%Y-%m-%d}.log", "a"),
        ]:
            handler = logging.FileHandler(path, mode=mode, encoding="utf-8")
            handler.setFormatter(formatter)
            root.addHandler(handler)

        root.addHandler(_ErrorReportHandler(errors_dir))
        _CONFIGURED = True


def _redact(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1***",
        value,
    )
    value = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+",
        r"\1***",
        value,
    )
    return value


def _current_model() -> str:
    try:
        from services.settings import load_settings

        return load_settings().deepseek_model or "-"
    except Exception:
        return "-"
