import sys
import shutil
from pathlib import Path

WRITABLE_ROOTS = {"config.json", "feeds.json", "output", "database"}


def resource_path(*parts: str) -> Path:
    """Return a resource path for development and PyInstaller builds."""
    if hasattr(sys, "_MEIPASS"):
        if parts and parts[0] in WRITABLE_ROOTS:
            return _writable_path(*parts)

        bundled_path = Path(sys._MEIPASS).joinpath(*parts)

        if bundled_path.exists():
            return bundled_path

        return Path(sys.executable).resolve().parent.joinpath(*parts)

    return Path(__file__).resolve().parent.joinpath(*parts)


def _writable_path(*parts: str) -> Path:
    target = Path(sys.executable).resolve().parent.joinpath(*parts)

    if target.exists():
        return target

    bundled = Path(sys._MEIPASS).joinpath(*parts)

    if bundled.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled, target)

    return target
