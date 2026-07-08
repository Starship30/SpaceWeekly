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
    export_sqlite: bool
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
    rss_limit: int
    min_news_score: int
    report_style: str
    report_style_custom_high: bool
    report_style_custom_medium: bool
    report_style_custom_low: bool
    ai_auto_filter_enabled: bool
    report_title: str
    ai_provider: str
    summary_prompt: str
    translation_prompt: str
    category_prompt: str
    score_prompt: str
    filter_prompt: str
    prompt_preset: str
    pipeline_score_enabled: bool
    pipeline_filter_enabled: bool
    pipeline_category_enabled: bool
    pipeline_keywords_enabled: bool
    pipeline_summary_enabled: bool
    pipeline_translation_enabled: bool
    debug_ai_enabled: bool
    connect_timeout: int
    read_timeout: int
    retry_count: int
    include_title: bool
    include_source: bool
    include_published: bool
    include_link: bool
    include_score: bool
    include_summary: bool
    include_translation: bool
    include_categories: bool
    include_keywords: bool
    include_body: bool
    include_original: bool
    include_chinese: bool
    daily_token_limit: int
    token_limit_action: str
    release_mode: bool


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
        export_sqlite=True,
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
        rss_limit=20,
        min_news_score=70,
        report_style="standard",
        report_style_custom_high=True,
        report_style_custom_medium=True,
        report_style_custom_low=False,
        ai_auto_filter_enabled=True,
        report_title="SpaceWeekly",
        ai_provider="DeepSeek",
        summary_prompt="",
        translation_prompt="",
        category_prompt="",
        score_prompt="",
        filter_prompt="",
        prompt_preset="Default",
        pipeline_score_enabled=True,
        pipeline_filter_enabled=True,
        pipeline_category_enabled=True,
        pipeline_keywords_enabled=True,
        pipeline_summary_enabled=True,
        pipeline_translation_enabled=False,
        debug_ai_enabled=False,
        connect_timeout=10,
        read_timeout=60,
        retry_count=3,
        include_title=True,
        include_source=True,
        include_published=True,
        include_link=True,
        include_score=True,
        include_summary=True,
        include_translation=True,
        include_categories=True,
        include_keywords=True,
        include_body=True,
        include_original=True,
        include_chinese=True,
        daily_token_limit=100000,
        token_limit_action="ask",
        release_mode=True,
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
        export_sqlite=bool(data.get("export_sqlite", defaults.export_sqlite)),
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
        rss_limit=int(data.get("rss_limit") or defaults.rss_limit),
        min_news_score=int(data.get("min_news_score") or defaults.min_news_score),
        report_style=str(data.get("report_style") or defaults.report_style),
        report_style_custom_high=bool(
            data.get("report_style_custom_high", defaults.report_style_custom_high)
        ),
        report_style_custom_medium=bool(
            data.get("report_style_custom_medium", defaults.report_style_custom_medium)
        ),
        report_style_custom_low=bool(
            data.get("report_style_custom_low", defaults.report_style_custom_low)
        ),
        ai_auto_filter_enabled=bool(data.get("ai_auto_filter_enabled", defaults.ai_auto_filter_enabled)),
        report_title=str(data.get("report_title") or defaults.report_title),
        ai_provider=str(data.get("ai_provider") or defaults.ai_provider),
        summary_prompt=str(data.get("summary_prompt") or defaults.summary_prompt),
        translation_prompt=str(data.get("translation_prompt") or defaults.translation_prompt),
        category_prompt=str(data.get("category_prompt") or defaults.category_prompt),
        score_prompt=str(data.get("score_prompt") or defaults.score_prompt),
        filter_prompt=str(data.get("filter_prompt") or defaults.filter_prompt),
        prompt_preset=str(data.get("prompt_preset") or defaults.prompt_preset),
        pipeline_score_enabled=bool(
            data.get("pipeline_score_enabled", defaults.pipeline_score_enabled)
        ),
        pipeline_filter_enabled=bool(
            data.get("pipeline_filter_enabled", defaults.pipeline_filter_enabled)
        ),
        pipeline_category_enabled=bool(
            data.get("pipeline_category_enabled", defaults.pipeline_category_enabled)
        ),
        pipeline_keywords_enabled=bool(
            data.get("pipeline_keywords_enabled", defaults.pipeline_keywords_enabled)
        ),
        pipeline_summary_enabled=bool(
            data.get("pipeline_summary_enabled", defaults.pipeline_summary_enabled)
        ),
        pipeline_translation_enabled=bool(
            data.get(
                "pipeline_translation_enabled",
                defaults.pipeline_translation_enabled,
            )
        ),
        debug_ai_enabled=bool(data.get("debug_ai_enabled", defaults.debug_ai_enabled)),
        connect_timeout=int(data.get("connect_timeout") or defaults.connect_timeout),
        read_timeout=int(data.get("read_timeout") or defaults.read_timeout),
        retry_count=int(data.get("retry_count") or defaults.retry_count),
        include_title=bool(data.get("include_title", defaults.include_title)),
        include_source=bool(data.get("include_source", defaults.include_source)),
        include_published=bool(data.get("include_published", defaults.include_published)),
        include_link=bool(data.get("include_link", defaults.include_link)),
        include_score=bool(data.get("include_score", defaults.include_score)),
        include_summary=bool(data.get("include_summary", defaults.include_summary)),
        include_translation=bool(data.get("include_translation", defaults.include_translation)),
        include_categories=bool(data.get("include_categories", defaults.include_categories)),
        include_keywords=bool(data.get("include_keywords", defaults.include_keywords)),
        include_body=bool(data.get("include_body", defaults.include_body)),
        include_original=bool(data.get("include_original", defaults.include_original)),
        include_chinese=bool(data.get("include_chinese", defaults.include_chinese)),
        daily_token_limit=int(data.get("daily_token_limit") or defaults.daily_token_limit),
        token_limit_action=str(data.get("token_limit_action") or defaults.token_limit_action),
        release_mode=bool(data.get("release_mode", defaults.release_mode)),
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
    import ai.providers as providers
    import database.sqlite as sqlite
    import downloader.client as client
    import exporters.markdown as markdown
    import exporters.word as word

    deepseek.DEEPSEEK_API_KEY = settings.deepseek_api_key
    deepseek.DEEPSEEK_BASE_URL = settings.deepseek_base_url
    deepseek.DEEPSEEK_MODEL = settings.deepseek_model
    deepseek.DEEPSEEK_TEMPERATURE = settings.temperature
    deepseek.DEEPSEEK_MAX_TOKENS = settings.max_tokens
    providers.CONNECT_TIMEOUT = settings.connect_timeout
    providers.READ_TIMEOUT = settings.read_timeout
    providers.RETRY_COUNT = settings.retry_count
    sqlite.DATABASE_PATH = Path(settings.sqlite_path)
    markdown.OUTPUT_DIR = Path(settings.output_dir)
    word.OUTPUT_DIR = Path(settings.output_dir)
    client.headers["User-Agent"] = settings.user_agent
    client.REQUEST_TIMEOUT = settings.request_timeout
    client.CONNECT_TIMEOUT = settings.connect_timeout
    client.READ_TIMEOUT = settings.read_timeout
    client.RETRY_COUNT = max(settings.retry_count, 3)
