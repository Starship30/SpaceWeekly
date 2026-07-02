import logging
from dataclasses import replace
from datetime import date
from datetime import datetime
from datetime import timedelta
from email.utils import parsedate_to_datetime

from ai.deepseek import summarize
from ai.deepseek import translate
from database.sqlite import get_articles
from database.sqlite import get_articles_by_date
from database.sqlite import initialize_database
from database.sqlite import save_article
from downloader.client import download
from exporters.markdown import export_markdown
from exporters.word import export_word
from logging_config import setup_logging
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from models.news import News
from services.feed_manager import FeedSource
from services.feed_manager import enabled_feeds
from services.settings import AppSettings
from services.settings import apply_settings
from services.settings import load_settings

logger = logging.getLogger(__name__)


def generate_weekly(
    feeds: list[FeedSource] | None = None,
    export_sqlite: bool = True,
    export_markdown_enabled: bool | None = None,
    export_word_enabled: bool | None = None,
) -> list[Article]:
    """Generate the weekly report from configured RSS feeds."""
    setup_logging()
    settings = load_settings()
    apply_settings(settings)
    selected_feeds = feeds if feeds is not None else enabled_feeds()
    markdown_enabled = _enabled(export_markdown_enabled, settings.export_markdown)
    word_enabled = _enabled(export_word_enabled, settings.export_word)
    ai_summaries: dict[str, AISummary] = {}
    translations: dict[str, str] = {}
    articles: list[Article] = []
    api_calls = 0
    date_range = _date_range(settings)

    if export_sqlite:
        initialize_database()

    logger.info("✔ 抓取 RSS")
    news_list = _filter_news(_fetch_news(selected_feeds), date_range)
    logger.info("预计文章数量：%s", len(news_list))
    logger.info("预计 Token：%s", _estimate_tokens(news_list, settings))
    logger.info("预计耗时：约 %s 秒", _estimate_seconds(news_list, settings))

    for index, news in enumerate(news_list, start=1):
        logger.info("正在处理：%s %s / %s", news.source, index, len(news_list))
        article = _parse_article(news)

        if article is None:
            continue

        article_for_ai = _limit_article_tokens(article, settings.max_article_tokens)
        api_calls = _run_ai(article_for_ai, settings, ai_summaries, translations, api_calls)
        articles.append(article)

        if export_sqlite:
            save_status = "SAVE" if save_article(article) else "SKIP"
            logger.info("✔ 保存 SQLite %s", save_status)

    export_articles = _export_articles(export_sqlite, articles, date_range)
    context = _export_context(settings, translations)
    _export_reports(export_articles, ai_summaries, context, markdown_enabled, word_enabled)

    return articles


def estimate_generation(feeds: list[FeedSource], settings: AppSettings) -> tuple[int, int, int]:
    """Return rough article, token, and seconds estimates for the GUI."""
    article_count = max(len([feed for feed in feeds if feed.enabled]) * 10, 0)
    token_count = article_count * settings.max_article_tokens
    api_factor = int(settings.ai_summary_enabled) + int(settings.ai_translation_enabled)
    seconds = article_count * max(api_factor, 1) * 8

    return article_count, token_count, seconds


def _enabled(value: bool | None, fallback: bool) -> bool:
    return fallback if value is None else value


def _fetch_news(feeds: list[FeedSource]) -> list[News]:
    news_list: list[News] = []
    import feedparser

    for feed in feeds:
        parsed_feed = feedparser.parse(feed.rss)

        for item in parsed_feed.entries:
            url = str(getattr(item, "link", ""))

            if not url or url.endswith(".js"):
                continue

            news_list.append(_news_from_feed_item(feed, item, url))

    return news_list


def _news_from_feed_item(feed: FeedSource, item, url: str) -> News:
    from bs4 import BeautifulSoup

    summary = BeautifulSoup(
        str(getattr(item, "summary", "")),
        "html.parser",
    ).get_text(" ", strip=True)

    return News(
        source=feed.name,
        title=str(getattr(item, "title", "")),
        published=str(getattr(item, "published", "")),
        summary=summary,
        url=url,
    )


def _filter_news(
    news_list: list[News],
    date_range: tuple[str, str] | None,
) -> list[News]:
    if date_range is None:
        return news_list

    start_date, end_date = date_range
    filtered = []

    for news in news_list:
        published_date = _published_date(news.published)

        if published_date is None:
            continue

        if start_date <= published_date.isoformat() <= end_date:
            filtered.append(news)

    return filtered


def _parse_article(news: News) -> Article | None:
    from parsers.registry import get_parser

    parser = get_parser(news.url)

    if parser is None:
        logger.warning("⚠ 未找到 Parser %s", news.url)
        return None

    html = download(news.url)
    article = parser(news, html)
    logger.info("✔ 解析 %s", article.parser)

    return article


def _run_ai(
    article: Article,
    settings: AppSettings,
    ai_summaries: dict[str, AISummary],
    translations: dict[str, str],
    api_calls: int,
) -> int:
    if _ai_limit_reached(settings, api_calls):
        _handle_ai_limit(settings)
        return api_calls

    if _summary_enabled(settings):
        api_calls += 1
        _summarize_article(article, settings, ai_summaries)

    if settings.ai_translation_enabled and not _ai_limit_reached(settings, api_calls):
        api_calls += 1
        _translate_article(article, translations)

    return api_calls


def _summary_enabled(settings: AppSettings) -> bool:
    return any(
        [
            settings.ai_summary_enabled,
            settings.ai_keywords_enabled,
            settings.ai_category_enabled,
            settings.ai_importance_enabled,
        ]
    )


def _ai_limit_reached(settings: AppSettings, api_calls: int) -> bool:
    return api_calls >= settings.max_daily_api_calls


def _handle_ai_limit(settings: AppSettings) -> None:
    message = "已达到每日 API 调用上限"

    if settings.ai_limit_action == "stop":
        raise RuntimeError(message)

    logger.warning("⚠ %s，跳过后续 AI 调用", message)


def _summarize_article(
    article: Article,
    settings: AppSettings,
    ai_summaries: dict[str, AISummary],
) -> None:
    try:
        ai_summary = summarize(article)
    except Exception as exc:
        logger.warning("⚠ AI 摘要失败 %s", exc)
        return

    ai_summaries[article.news.url] = _apply_summary_options(ai_summary, settings)
    logger.info("✔ AI 摘要完成")


def _apply_summary_options(summary: AISummary, settings: AppSettings) -> AISummary:
    return AISummary(
        summary=summary.summary if settings.ai_summary_enabled else "",
        keywords=summary.keywords if settings.ai_keywords_enabled else [],
        category=summary.category if settings.ai_category_enabled else "",
        importance=summary.importance if settings.ai_importance_enabled else "",
    )


def _translate_article(article: Article, translations: dict[str, str]) -> None:
    try:
        translations[article.news.url] = translate(article)
    except Exception as exc:
        logger.warning("⚠ AI 翻译失败 %s", exc)
        return

    logger.info("✔ AI 翻译完成")


def _export_articles(
    export_sqlite: bool,
    articles: list[Article],
    date_range: tuple[str, str] | None,
) -> list[Article]:
    if not export_sqlite:
        return articles

    if date_range is not None:
        return get_articles_by_date(date_range[0], date_range[1])

    return get_articles()


def _export_context(
    settings: AppSettings,
    translations: dict[str, str],
) -> ExportContext:
    return ExportContext(
        show_summary=settings.ai_summary_enabled,
        show_keywords=settings.ai_keywords_enabled,
        show_category=settings.ai_category_enabled,
        show_importance=settings.ai_importance_enabled,
        body_mode=settings.body_mode,
        translations=translations,
    )


def _export_reports(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    context: ExportContext,
    markdown_enabled: bool,
    word_enabled: bool,
) -> None:
    if markdown_enabled:
        export_markdown(articles, ai_summaries, context)
        logger.info("✔ 导出 Markdown")

    if word_enabled:
        export_word(articles, ai_summaries, context)
        logger.info("✔ 导出 Word")


def _date_range(settings: AppSettings) -> tuple[str, str] | None:
    today = date.today()

    if settings.date_mode == "today":
        return today.isoformat(), today.isoformat()

    if settings.date_mode == "last_3_days":
        return (today - timedelta(days=2)).isoformat(), today.isoformat()

    if settings.date_mode == "last_7_days":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()

    if settings.date_mode == "last_30_days":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()

    if settings.date_mode == "custom" and settings.start_date and settings.end_date:
        return settings.start_date, settings.end_date

    return None


def _published_date(published: str) -> date | None:
    try:
        return parsedate_to_datetime(published).date()
    except (TypeError, ValueError):
        return None


def _estimate_tokens(news_list: list[News], settings: AppSettings) -> int:
    return len(news_list) * settings.max_article_tokens


def _estimate_seconds(news_list: list[News], settings: AppSettings) -> int:
    api_factor = int(_summary_enabled(settings)) + int(settings.ai_translation_enabled)
    return len(news_list) * max(api_factor, 1) * 8


def _limit_article_tokens(article: Article, max_tokens: int) -> Article:
    max_chars = max_tokens * 4

    if len(article.body) <= max_chars:
        return article

    return replace(article, body=article.body[:max_chars])
