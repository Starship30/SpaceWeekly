import os

from resources import resource_path

BASE_DIR = resource_path()
DATABASE_PATH = resource_path("database", "spaceweekly.db")
OUTPUT_DIR = resource_path("output")
APP_VERSION = "2.2"
APP_EDITION = "AI Workflow Edition"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "")
