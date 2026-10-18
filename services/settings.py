import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import config
from resources import resource_path

CONFIG_PATH = resource_path("config.json")
NEWS_FILTER_LEVELS = {"High_only", "High_Medium", "All"}


@dataclass
class AppSettings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    model_candidates: list[str]
    temperature: float
    user_agent: str
    request_timeout: int
    sqlite_path: str
    output_dir: str
    export_markdown: bool
    export_word: bool
    export_sqlite: bool
    body_mode: str
    date_mode: str
    start_date: str
    end_date: str
    rss_limit: int
    news_filter_level: str
    report_title: str
    ai_provider: str
    summary_prompt: str
    filter_prompt: str
    prompt_preset: str
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
    include_keywords: bool
    include_body: bool
    include_original: bool
    release_mode: bool
    theme_mode: str
    language: str
    launch_range: str
    launch_start_date: str
    launch_end_date: str
    aerospace_translation_enabled: bool
    aerospace_translation_mode: str
    display_timezone: str
    web_search_enabled: bool
    web_search_provider: str
    web_search_api_key: str
    web_search_timeout: int
    web_search_max_results: int
    export_premiere: bool = False
    news_dedup_enabled: bool = True
    news_event_date_check_enabled: bool = True
    title_prompt: str = ""


def default_settings() -> AppSettings:
    """Return compact v2.4.3 settings based on code defaults."""

    return AppSettings(
        deepseek_api_key=config.DEEPSEEK_API_KEY,
        deepseek_base_url=config.DEEPSEEK_BASE_URL,
        deepseek_model=config.DEEPSEEK_MODEL,
        model_candidates=list(config.DEFAULT_MODEL_CANDIDATES),
        temperature=0.2,
        user_agent=config.USER_AGENT,
        request_timeout=config.REQUEST_TIMEOUT,
        sqlite_path=str(config.DATABASE_PATH),
        output_dir=str(config.OUTPUT_DIR),
        export_markdown=True,
        export_word=False,
        export_sqlite=True,
        body_mode="none",
        date_mode="last_7_days",
        start_date="",
        end_date="",
        rss_limit=20,
        news_filter_level="High_Medium",
        report_title="SpaceWeekly",
        ai_provider="DeepSeek",
        summary_prompt="",
        filter_prompt="",
        prompt_preset="Default",
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
        include_keywords=True,
        include_body=False,
        include_original=True,
        release_mode=True,
        theme_mode="system",
        language="zh_CN",
        launch_range="next_week",
        launch_start_date="",
        launch_end_date="",
        aerospace_translation_enabled=True,
        aerospace_translation_mode=config.TRANSLATION_MODE,
        display_timezone="Asia/Shanghai",
        web_search_enabled=config.WEB_SEARCH_ENABLED,
        web_search_provider=config.WEB_SEARCH_PROVIDER,
        web_search_api_key=config.WEB_SEARCH_API_KEY,
        web_search_timeout=config.WEB_SEARCH_TIMEOUT,
        web_search_max_results=config.WEB_SEARCH_MAX_RESULTS,
    )


def load_settings() -> AppSettings:
    """Load v2.4.3 settings and migrate v2.3/v2.4 keys in memory."""

    defaults = default_settings()

    if not CONFIG_PATH.exists():
        save_settings(defaults)
        return defaults

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    configured_model = str(
        data.get("model")
        or data.get("deepseek_model")
        or defaults.deepseek_model
    )

    return AppSettings(
        deepseek_api_key=str(data.get("deepseek_api_key") or defaults.deepseek_api_key),
        deepseek_base_url=str(data.get("deepseek_base_url") or defaults.deepseek_base_url),
        deepseek_model=configured_model,
        model_candidates=_list_setting(
            data.get("models", data.get("model_candidates")),
            defaults.model_candidates,
        ),
        temperature=float(data.get("temperature") or defaults.temperature),
        user_agent=str(data.get("user_agent") or defaults.user_agent),
        request_timeout=int(data.get("request_timeout") or defaults.request_timeout),
        sqlite_path=str(data.get("sqlite_path") or defaults.sqlite_path),
        output_dir=str(data.get("output_dir") or defaults.output_dir),
        export_markdown=bool(data.get("export_markdown", defaults.export_markdown)),
        export_word=bool(data.get("export_word", defaults.export_word)),
        export_premiere=bool(data.get("export_premiere", defaults.export_premiere)),
        export_sqlite=bool(data.get("export_sqlite", defaults.export_sqlite)),
        body_mode=_body_mode_setting(data.get("body_mode"), defaults.body_mode),
        date_mode=str(data.get("date_mode") or defaults.date_mode),
        start_date=str(data.get("start_date") or defaults.start_date),
        end_date=str(data.get("end_date") or defaults.end_date),
        rss_limit=_news_limit_setting(data.get("rss_limit", defaults.rss_limit), defaults.rss_limit),
        news_dedup_enabled=bool(data.get("news_dedup_enabled", defaults.news_dedup_enabled)),
        news_event_date_check_enabled=bool(data.get("news_event_date_check_enabled", defaults.news_event_date_check_enabled)),
        news_filter_level=_news_filter_level(data, defaults.news_filter_level),
        report_title=str(data.get("report_title") or defaults.report_title),
        ai_provider=str(data.get("ai_provider") or defaults.ai_provider),
        summary_prompt=str(data.get("summary_prompt") or defaults.summary_prompt),
        title_prompt=str(data.get("title_prompt") or defaults.title_prompt),
        filter_prompt=str(data.get("filter_prompt") or defaults.filter_prompt),
        prompt_preset=str(data.get("prompt_preset") or defaults.prompt_preset),
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
        include_keywords=bool(data.get("include_keywords", defaults.include_keywords)),
        include_body=bool(data.get("include_body", defaults.include_body)),
        include_original=bool(data.get("include_original", defaults.include_original)),
        release_mode=bool(data.get("release_mode", defaults.release_mode)),
        theme_mode=str(data.get("theme_mode") or defaults.theme_mode),
        language=_choice_setting(
            data.get("language"),
            defaults.language,
            {"zh_CN", "en_US"},
        ),
        launch_range=_choice_setting(
            data.get("launch_range"),
            defaults.launch_range,
            {"past_week", "next_week", "past_30_days", "next_30_days", "custom", "disabled"},
        ),
        launch_start_date=str(data.get("launch_start_date") or defaults.launch_start_date),
        launch_end_date=str(data.get("launch_end_date") or defaults.launch_end_date),
        aerospace_translation_enabled=bool(
            data.get(
                "aerospace_translation_enabled",
                defaults.aerospace_translation_enabled,
            )
        ),
        aerospace_translation_mode=_translation_mode_setting(
            data.get("aerospace_translation_mode"),
            defaults.aerospace_translation_mode,
        ),
        display_timezone=_choice_setting(
            data.get("display_timezone"),
            defaults.display_timezone,
            {
                "system",
                "UTC",
                "Asia/Shanghai",
                "Asia/Tokyo",
                "Europe/London",
                "America/New_York",
            },
        ),
        web_search_enabled=bool(
            data.get("web_search_enabled", defaults.web_search_enabled)
        ),
        web_search_provider=str(
            data.get("web_search_provider") or defaults.web_search_provider
        ),
        web_search_api_key=str(
            data.get("web_search_api_key") or defaults.web_search_api_key
        ),
        web_search_timeout=max(
            1,
            int(data.get("web_search_timeout") or defaults.web_search_timeout),
        ),
        web_search_max_results=max(
            1,
            min(
                10,
                int(
                    data.get("web_search_max_results")
                    or defaults.web_search_max_results
                ),
            ),
        ),
    )


def save_settings(settings: AppSettings) -> None:
    """Save canonical v2.4.3 settings while retaining the v2.3 model alias."""

    data = asdict(settings)
    data.pop("model_candidates", None)
    data["model"] = settings.deepseek_model
    data["models"] = list(settings.model_candidates)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _news_limit_setting(value, fallback: int) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return fallback
        limit = int(value)
        return limit if 0 <= limit <= 2_147_483_647 else fallback
    except ValueError:
        return fallback


def _news_filter_level(data: dict[str, object], fallback: str) -> str:
    configured = str(data.get("news_filter_level") or "")

    if configured in NEWS_FILTER_LEVELS:
        return configured

    # Migrate the v2.3/v2.4 report-style selector to the new screening selector.
    style = str(data.get("report_style") or "")

    if style == "curated":
        return "High_only"

    if style == "complete":
        return "All"

    if style == "custom":
        if bool(data.get("report_style_custom_low", False)):
            return "All"

        if bool(data.get("report_style_custom_medium", True)):
            return "High_Medium"

        return "High_only"

    return fallback


def _list_setting(value, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]

    if isinstance(value, str):
        items = [item.strip() for item in value.replace("，", ",").split(",")]
        return [item for item in items if item]

    return list(fallback)


def _body_mode_setting(value, fallback: str) -> str:
    mode = str(value or fallback).strip().lower()

    if mode == "none":
        return "none"

    # Removed full-body AI translation modes fall back to the original body.
    return "original"


def _choice_setting(value, fallback: str, choices: set[str]) -> str:
    selected = str(value or fallback)
    return selected if selected in choices else fallback


def _translation_mode_setting(value, fallback: str) -> str:
    selected = str(value or fallback)

    if selected == "ai_confirm":
        return "hybrid"

    return selected if selected in {"dictionary", "ai", "hybrid"} else fallback


def apply_settings(settings: AppSettings) -> None:
    """Apply user settings to imported I/O modules."""

    import ai.providers as providers
    import database.sqlite as sqlite
    import downloader.client as client
    import exporters.markdown as markdown
    import exporters.word as word
    import exporters.premiere as premiere

    providers.CONNECT_TIMEOUT = settings.connect_timeout
    providers.READ_TIMEOUT = settings.read_timeout
    providers.RETRY_COUNT = settings.retry_count
    sqlite.DATABASE_PATH = Path(settings.sqlite_path)
    markdown.OUTPUT_DIR = Path(settings.output_dir)
    word.OUTPUT_DIR = Path(settings.output_dir)
    premiere.OUTPUT_DIR = Path(settings.output_dir)
    client.headers["User-Agent"] = settings.user_agent
    client.REQUEST_TIMEOUT = settings.request_timeout
    client.CONNECT_TIMEOUT = settings.connect_timeout
    client.READ_TIMEOUT = settings.read_timeout
    client.RETRY_COUNT = max(settings.retry_count, 3)
