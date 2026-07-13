import shutil
import sys
from dataclasses import replace
from pathlib import Path

from resources import bundled_resource_path
from resources import default_workspace_path
from resources import set_workspace_path
from resources import workspace_path

WORKSPACE_DIRS = ["database", "output", "cache", "logs"]


def default_workspace() -> Path:
    """Return the recommended default workspace location."""
    return default_workspace_path()


def configured_workspace() -> Path | None:
    """Return the configured workspace if it is valid."""
    path = workspace_path()

    if path is None or not is_valid_workspace(path):
        return None

    return path


def needs_first_run_wizard(release_mode: bool = True) -> bool:
    """Return True when the app should ask the user to choose a workspace."""
    return configured_workspace() is None


def is_valid_workspace(path: Path) -> bool:
    """Return True when path looks like an initialized writable workspace."""
    path = Path(path)

    if not path.exists() or not path.is_dir():
        return False

    required_paths = [path / name for name in WORKSPACE_DIRS]
    required_paths.extend([path / "config.json", path / "feeds.json"])

    if not all(item.exists() for item in required_paths):
        return False

    return _can_write(path)


def initialize_workspace(path: Path, language: str | None = None) -> Path:
    """Create or reuse a workspace and update settings paths."""
    workspace = Path(path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    if not _can_write(workspace):
        raise PermissionError(f"Cannot write to {workspace}")

    for directory in WORKSPACE_DIRS:
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    set_workspace_path(workspace)
    _refresh_module_paths(workspace)
    _initialize_feeds(workspace)
    _initialize_prompts(workspace)
    _initialize_settings(workspace, language)
    _initialize_database()

    return workspace


def _refresh_module_paths(workspace: Path) -> None:
    settings_module = sys.modules.get("services.settings")

    if settings_module is not None:
        settings_module.CONFIG_PATH = workspace / "config.json"

    feeds_module = sys.modules.get("services.feed_manager")

    if feeds_module is not None:
        feeds_module.FEEDS_PATH = workspace / "feeds.json"

    config_module = sys.modules.get("config")

    if config_module is not None:
        config_module.DATABASE_PATH = workspace / "database" / "spaceweekly.db"
        config_module.OUTPUT_DIR = workspace / "output"

    prompt_module = sys.modules.get("services.prompt_manager")

    if prompt_module is not None:
        prompt_module.PROMPTS_DIR = workspace / "prompts"


def _initialize_feeds(workspace: Path) -> None:
    target = workspace / "feeds.json"

    if target.exists():
        return

    bundled = bundled_resource_path("feeds.json")

    if bundled.exists():
        shutil.copyfile(bundled, target)
        return

    target.write_text("[]", encoding="utf-8")


def _initialize_settings(workspace: Path, language: str | None) -> None:
    from services.settings import default_settings
    from services.settings import load_settings
    from services.settings import save_settings

    target = workspace / "config.json"

    if not target.exists():
        bundled = bundled_resource_path("config.json")

        if bundled.exists():
            shutil.copyfile(bundled, target)
        else:
            save_settings(default_settings())

    settings = load_settings()
    save_settings(
        replace(
            settings,
            sqlite_path=str(workspace / "database" / "spaceweekly.db"),
            output_dir=str(workspace / "output"),
            release_mode=True,
            language=language or settings.language,
        )
    )


def _initialize_prompts(workspace: Path) -> None:
    target = workspace / "prompts"

    if any(target.glob("*.json")):
        return

    bundled = bundled_resource_path("prompts")

    if bundled.exists():
        shutil.copytree(bundled, target, dirs_exist_ok=True)


def _initialize_database() -> None:
    from services.settings import load_settings
    import database.sqlite as sqlite

    settings = load_settings()
    sqlite.DATABASE_PATH = Path(settings.sqlite_path)
    sqlite.initialize_database()


def _can_write(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".spaceweekly_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False

    return True
