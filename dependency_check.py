import importlib.util

REQUIRED_PACKAGES = {
    "PySide6": "PySide6",
    "python-docx": "docx",
    "requests": "requests",
    "feedparser": "feedparser",
    "beautifulsoup4": "bs4",
}


def missing_dependencies() -> list[str]:
    """Return missing runtime dependencies."""
    missing = []

    for package_name, import_name in REQUIRED_PACKAGES.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(package_name)

    return missing


def dependency_message(missing: list[str]) -> str:
    """Build a user-friendly dependency installation message."""
    packages = " ".join(missing)

    return (
        "缺少依赖："
        + ", ".join(missing)
        + "\n\n请执行：\n"
        + f"pip install {packages}"
    )
