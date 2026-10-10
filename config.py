import os

from resources import resource_path

BASE_DIR = resource_path()
DATABASE_PATH = resource_path("database", "spaceweekly.db")
OUTPUT_DIR = resource_path("output")


def app_version() -> str:
    """Single source for the application version."""
    return "3.0"


APP_EDITION = "AI Workflow Edition"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10
TRANSLATION_MODE = "hybrid"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "")
DEFAULT_MODEL_CANDIDATES: list[str] = []

WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "duckduckgo")
WEB_SEARCH_API_KEY = os.getenv("WEB_SEARCH_API_KEY", "")
WEB_SEARCH_TIMEOUT = int(os.getenv("WEB_SEARCH_TIMEOUT", "10"))
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
