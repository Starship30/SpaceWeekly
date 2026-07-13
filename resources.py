import sys
import shutil
import os
import json
from pathlib import Path

WRITABLE_ROOTS = {
    "config.json",
    "feeds.json",
    "output",
    "database",
    "cache",
    "logs",
    "prompts",
}
_WORKSPACE_PATH: Path | None = None


def resource_path(*parts: str) -> Path:
    """Return a resource path for development and PyInstaller builds."""
    workspace = workspace_path()

    if parts and parts[0] in WRITABLE_ROOTS and workspace is not None:
        return workspace.joinpath(*parts)

    if hasattr(sys, "_MEIPASS"):
        if parts and parts[0] in WRITABLE_ROOTS:
            return _writable_path(*parts)

        bundled_path = bundled_resource_path(*parts)

        if bundled_path.exists():
            return bundled_path

        return Path(sys.executable).resolve().parent.joinpath(*parts)

    return Path(__file__).resolve().parent.joinpath(*parts)


def bundled_resource_path(*parts: str) -> Path:
    """Return the bundled application resource path, ignoring workspace routing."""
    if hasattr(sys, "_MEIPASS"):
        bundled_path = Path(sys._MEIPASS).joinpath(*parts)

        if bundled_path.exists():
            return bundled_path

        return Path(sys.executable).resolve().parent.joinpath(*parts)

    return Path(__file__).resolve().parent.joinpath(*parts)


def default_workspace_path() -> Path:
    """Return the default first-run workspace path."""
    if sys.platform.startswith("win"):
        return Path.home() / "Documents" / "SpaceWeekly"

    return Path.home() / "Documents" / "SpaceWeekly"


def workspace_pointer_path() -> Path:
    """Return the user-scoped file that remembers the selected workspace."""
    base = os.getenv("APPDATA") or os.getenv("XDG_CONFIG_HOME")

    if base:
        return Path(base) / "SpaceWeekly" / "workspace.json"

    return Path.home() / ".spaceweekly" / "workspace.json"


def workspace_path() -> Path | None:
    """Return the active workspace path if one is configured."""
    global _WORKSPACE_PATH

    if _WORKSPACE_PATH is not None:
        return _WORKSPACE_PATH

    pointer = workspace_pointer_path()

    if not pointer.exists():
        return None

    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    raw_path = str(data.get("workspace", "")).strip()

    if not raw_path:
        return None

    path = Path(raw_path).expanduser()
    _WORKSPACE_PATH = path
    return _WORKSPACE_PATH


def set_workspace_path(path: Path, persist: bool = True) -> None:
    """Set and optionally persist the active workspace path."""
    global _WORKSPACE_PATH
    _WORKSPACE_PATH = Path(path).expanduser().resolve()

    if not persist:
        return

    pointer = workspace_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"workspace": str(_WORKSPACE_PATH)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_workspace_path() -> None:
    """Forget the active workspace pointer."""
    global _WORKSPACE_PATH
    _WORKSPACE_PATH = None
    pointer = workspace_pointer_path()

    if pointer.exists():
        pointer.unlink()


def _writable_path(*parts: str) -> Path:
    target = Path(sys.executable).resolve().parent.joinpath(*parts)

    if target.exists():
        return target

    bundled = Path(sys._MEIPASS).joinpath(*parts)

    if bundled.is_dir():
        shutil.copytree(bundled, target, dirs_exist_ok=True)

    if bundled.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled, target)

    return target
