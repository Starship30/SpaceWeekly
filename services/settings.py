import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import config
from resources import resource_path

CONFIG_PATH = resource_path("config.json")


@dataclass
class AppSettings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    temperature: float
    max_tokens: int
    user_agent: str
    request_timeout: int
    sqlite_path: str
    output_dir: str
    export_markdown: bool
    export_word: bool
    ai_summary_enabled: bool
    ai_translation_enabled: bool
    ai_keywords_enabled: bool
    ai_category_enabled: bool
    ai_importance_enabled: bool
    body_mode: str
    date_mode: str
    start_date: str
    end_date: str
    max_article_tokens: int
    max_daily_api_calls: int
    ai_limit_action: str


def default_settings() -> AppSettings:
    """Return settings based on code defaults."""
    return AppSettings(
        deepseek_api_key=config.DEEPSEEK_API_KEY,
        deepseek_base_url=config.DEEPSEEK_BASE_URL,
        deepseek_model=config.DEEPSEEK_MODEL,
        temperature=0.2,
        max_tokens=2000,
        user_agent=config.USER_AGENT,
        request_timeout=config.REQUEST_TIMEOUT,
        sqlite_path=str(config.DATABASE_PATH),
        output_dir=str(config.OUTPUT_DIR),
        export_markdown=True,
        export_word=False,
        ai_summary_enabled=True,
        ai_translation_enabled=False,
        ai_keywords_enabled=True,
        ai_category_enabled=True,
        ai_importance_enabled=True,
        body_mode="original",
        date_mode="last_7_days",
        start_date="",
        end_date="",
        max_article_tokens=2000,
        max_daily_api_calls=100,
        ai_limit_action="skip",
    )


def load_settings() -> AppSettings:
    """Load user settings from config.json."""
    defaults = default_settings()

    if not CONFIG_PATH.exists():
        save_settings(defaults)
        return defaults

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    return AppSettings(
        deepseek_api_key=str(data.get("deepseek_api_key") or defaults.deepseek_api_key),
        deepseek_base_url=str(data.get("deepseek_base_url") or defaults.deepseek_base_url),
        deepseek_model=str(data.get("deepseek_model") or defaults.deepseek_model),
        temperature=float(data.get("temperature") or defaults.temperature),
        max_tokens=int(data.get("max_tokens") or defaults.max_tokens),
        user_agent=str(data.get("user_agent") or defaults.user_agent),
        request_timeout=int(data.get("request_timeout") or defaults.request_timeout),
        sqlite_path=str(data.get("sqlite_path") or defaults.sqlite_path),
        output_dir=str(data.get("output_dir") or defaults.output_dir),
        export_markdown=bool(data.get("export_markdown", defaults.export_markdown)),
        export_word=bool(data.get("export_word", defaults.export_word)),
        ai_summary_enabled=bool(data.get("ai_summary_enabled", defaults.ai_summary_enabled)),
        ai_translation_enabled=bool(data.get("ai_translation_enabled", defaults.ai_translation_enabled)),
        ai_keywords_enabled=bool(data.get("ai_keywords_enabled", defaults.ai_keywords_enabled)),
        ai_category_enabled=bool(data.get("ai_category_enabled", defaults.ai_category_enabled)),
        ai_importance_enabled=bool(data.get("ai_importance_enabled", defaults.ai_importance_enabled)),
        body_mode=str(data.get("body_mode") or defaults.body_mode),
        date_mode=str(data.get("date_mode") or defaults.date_mode),
        start_date=str(data.get("start_date") or defaults.start_date),
        end_date=str(data.get("end_date") or defaults.end_date),
        max_article_tokens=int(data.get("max_article_tokens") or defaults.max_article_tokens),
        max_daily_api_calls=int(data.get("max_daily_api_calls") or defaults.max_daily_api_calls),
        ai_limit_action=str(data.get("ai_limit_action") or defaults.ai_limit_action),
    )


def save_settings(settings: AppSettings) -> None:
    """Save user settings to config.json."""
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_settings(settings: AppSettings) -> None:
    """Apply user settings to already imported business modules."""
    import ai.deepseek as deepseek
    import database.sqlite as sqlite
    import downloader.client as client
    import exporters.markdown as markdown
    import exporters.word as word

    deepseek.DEEPSEEK_API_KEY = settings.deepseek_api_key
    deepseek.DEEPSEEK_BASE_URL = settings.deepseek_base_url
    deepseek.DEEPSEEK_MODEL = settings.deepseek_model
    deepseek.DEEPSEEK_TEMPERATURE = settings.temperature
    deepseek.DEEPSEEK_MAX_TOKENS = settings.max_tokens
    sqlite.DATABASE_PATH = Path(settings.sqlite_path)
    markdown.OUTPUT_DIR = Path(settings.output_dir)
    word.OUTPUT_DIR = Path(settings.output_dir)
    client.headers["User-Agent"] = settings.user_agent
    client.REQUEST_TIMEOUT = settings.request_timeout
