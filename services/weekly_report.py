import logging
import re
from dataclasses import replace
from datetime import date
from datetime import timedelta
from email.utils import parsedate_to_datetime

from ai.configuration import provider_config_from_settings
from ai.deepseek import assess_importance
from ai.deepseek import generate_content
from ai.deepseek import translate_titles
from ai.providers import test_connection
from config import APP_VERSION
from database.sqlite import initialize_database
from database.sqlite import save_article
from downloader.client import download
from exporters.markdown import export_markdown
from exporters.word import export_word
from feeds.launches import get_launches
from i18n import set_language
from logging_config import setup_logging
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from models.news import News
from models.news_assessment import NewsAssessment
from services.cancellation import CancellationToken
from services.cancellation import TaskCancelledError
from services.feed_manager import FeedSource
from services.feed_manager import enabled_feeds
from services.prompt_manager import find_preset
from services.prompt_manager import prompt_value
from services.settings import AppSettings
from services.settings import apply_settings
from services.settings import load_settings
from translator.translator import Translator
from utils.text import clean_ai_text

logger = logging.getLogger(__name__)


def generate_weekly(
    feeds: list[FeedSource] | None = None,
    export_sqlite: bool = True,
    export_markdown_enabled: bool | None = None,
    export_word_enabled: bool | None = None,
    cancel_token: CancellationToken | None = None,
) -> list[Article]:
    """Generate a report with separate AI screening and content stages."""

    setup_logging()
    settings = load_settings()
    token = cancel_token or CancellationToken()
    token.check()
    set_language(settings.language)
    logger.setLevel(logging.DEBUG if not settings.release_mode else logging.NOTSET)
    apply_settings(settings)
    logger.info("SpaceWeekly v%s", APP_VERSION)
    logger.info("AI Model: %s", settings.deepseek_model or "-")

    if _ai_configured(settings):
        settings = _resolve_runtime_model(settings, token)

    if export_sqlite:
        token.check()
        initialize_database()

    selected_feeds = feeds if feeds is not None else enabled_feeds()
    markdown_enabled = _enabled(export_markdown_enabled, settings.export_markdown)
    word_enabled = _enabled(export_word_enabled, settings.export_word)
    date_range = _date_range(settings)
    rss_news, launch_articles = get_news(
        selected_feeds,
        settings.rss_limit,
        settings.launch_range,
        token,
    )
    news_list = _filter_news(rss_news, date_range)
    logger.info("基础解析文章数量：%s", len(news_list))
    parsed_articles = _parse_articles(news_list, token)
    logger.info("基础解析完成：%s 篇", len(parsed_articles))

    preset = find_preset(settings.prompt_preset)
    assessments: dict[str, NewsAssessment] = {}
    ai_summaries: dict[str, AISummary] = {}
    api_calls = 0

    selected_articles, api_calls = _screen_articles(
        parsed_articles,
        settings,
        prompt_value(settings.filter_prompt, preset, "filter"),
        assessments,
        api_calls,
        token,
    )
    logger.info(
        "筛选完成：%s / %s 篇入选（等级：%s）",
        len(selected_articles),
        len(parsed_articles),
        settings.news_filter_level,
    )

    if export_sqlite:
        for article in selected_articles:
            token.check()
            save_status = "SAVE" if save_article(article) else "SKIP"
            logger.info("保存 SQLite %s", save_status)

    api_calls = _generate_selected_content(
        selected_articles,
        settings,
        prompt_value(settings.summary_prompt, preset, "summary"),
        ai_summaries,
        api_calls,
        token,
    )
    localized_titles, api_calls = _localize_selected_titles(
        selected_articles,
        settings,
        api_calls,
        token,
    )
    logger.info(
        "AI 工作流完成：筛选 %s 篇，内容生成 %s 篇，API 调用 %s 次",
        len(parsed_articles),
        len(ai_summaries),
        api_calls,
    )

    context = _export_context(settings, localized_titles)
    _export_reports(
        selected_articles,
        ai_summaries,
        assessments,
        context,
        launch_articles,
        settings,
        markdown_enabled,
        word_enabled,
        token,
    )
    return selected_articles


def get_news(
    feeds: list[FeedSource],
    rss_limit: int = 0,
    launch_range: str = "next_week",
    cancel_token: CancellationToken | None = None,
) -> tuple[list[News], list[Article]]:
    """Fetch RSS news and Launch Library 2 launch articles."""

    logger.info("抓取 RSS")
    token = cancel_token or CancellationToken()
    token.check()
    rss_news = _fetch_news(feeds, rss_limit, token)
    token.check()
    launch_articles = _fetch_launch_articles(launch_range, token)
    token.check()
    return rss_news, launch_articles


def _fetch_news(
    feeds: list[FeedSource],
    rss_limit: int,
    cancel_token: CancellationToken,
) -> list[News]:
    import feedparser

    news_list: list[News] = []

    for feed in feeds:
        cancel_token.check()
        parsed_feed = feedparser.parse(feed.rss)
        cancel_token.check()
        entries = parsed_feed.entries[:rss_limit] if rss_limit > 0 else parsed_feed.entries

        for item in entries:
            cancel_token.check()
            url = str(getattr(item, "link", ""))

            if not url or url.endswith(".js"):
                continue

            news_list.append(_news_from_feed_item(feed, item, url))

    return news_list


def _fetch_launch_articles(
    launch_range: str,
    cancel_token: CancellationToken,
) -> list[Article]:
    if launch_range == "disabled":
        return []

    logger.info("抓取 Launch Library 2")
    cancel_token.check()

    try:
        launches = get_launches(launch_range=launch_range)
        cancel_token.check()
        return launches
    except TaskCancelledError:
        raise
    except Exception as exc:
        logger.warning("Launch Library 2 获取失败：%s", exc)
        return []


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
    return [
        news
        for news in news_list
        if (
            (published_date := _published_date(news.published)) is not None
            and start_date <= published_date.isoformat() <= end_date
        )
    ]


def _parse_articles(
    news_list: list[News],
    cancel_token: CancellationToken,
) -> list[Article]:
    articles = []

    for index, news in enumerate(news_list, start=1):
        cancel_token.check()
        logger.info("基础解析：%s %s / %s", news.source, index, len(news_list))
        article = _parse_article(news, cancel_token)

        if article is not None:
            articles.append(article)

    return articles


def _parse_article(
    news: News,
    cancel_token: CancellationToken,
) -> Article | None:
    from parsers.registry import get_parser

    parser = get_parser(news.url)
    cancel_token.check()

    try:
        html = download(news.url)
        cancel_token.check()
    except TaskCancelledError:
        raise
    except Exception as exc:
        logger.warning("HTML 获取失败 %s %s，使用 RSS Summary 回退", news.url, exc)
        return _rss_summary_article(news)

    try:
        article = parser(news, html)
        cancel_token.check()
    except TaskCancelledError:
        raise
    except Exception as exc:
        logger.warning("Parser 异常 %s %s，使用 RSS Summary 回退", news.url, exc)
        return _rss_summary_article(news)

    logger.info("解析 %s", article.parser)
    return article


def _rss_summary_article(news: News) -> Article:
    return Article(
        news=news,
        body=news.summary.strip() or news.title,
        parser="RSS Summary",
    )


def _screen_articles(
    articles: list[Article],
    settings: AppSettings,
    custom_prompt: str,
    assessments: dict[str, NewsAssessment],
    api_calls: int,
    cancel_token: CancellationToken,
) -> tuple[list[Article], int]:
    selected = []

    for index, article in enumerate(articles, start=1):
        cancel_token.check()
        logger.info("AI 重要程度筛选：%s / %s", index, len(articles))

        if not _ai_configured(settings):
            assessment = _fallback_assessment(settings.language)
        else:
            api_calls += 1

            try:
                assessment = assess_importance(
                    article,
                    settings,
                    custom_prompt=custom_prompt,
                    cancel_token=cancel_token,
                )
            except TaskCancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "新闻筛选失败，按 Medium 回退：%s",
                    exc,
                    exc_info=True,
                    extra={"model": settings.deepseek_model},
                )
                assessment = _fallback_assessment(settings.language)

        assessments[article.news.url] = assessment
        logger.info(
            "筛选结果：%s；%s",
            assessment.importance,
            assessment.reason,
        )

        if _importance_allowed(assessment.importance, settings.news_filter_level):
            selected.append(article)

    return selected, api_calls


def _generate_selected_content(
    articles: list[Article],
    settings: AppSettings,
    custom_prompt: str,
    summaries: dict[str, AISummary],
    api_calls: int,
    cancel_token: CancellationToken,
) -> int:
    if not _ai_configured(settings):
        logger.warning("AI 配置不完整，跳过入选新闻摘要生成")
        return api_calls

    for index, article in enumerate(articles, start=1):
        cancel_token.check()

        logger.info("生成入选新闻内容：%s / %s", index, len(articles))
        api_calls += 1

        try:
            summaries[article.news.url] = generate_content(
                article,
                settings,
                custom_prompt=custom_prompt,
                cancel_token=cancel_token,
            )
        except TaskCancelledError:
            raise
        except Exception as exc:
            logger.error(
                "入选新闻内容生成失败，继续处理其他文章：%s",
                exc,
                exc_info=True,
                extra={"model": settings.deepseek_model},
            )

    return api_calls


def _localize_selected_titles(
    articles: list[Article],
    settings: AppSettings,
    api_calls: int,
    cancel_token: CancellationToken,
) -> tuple[dict[str, str], int]:
    localized = {
        article.news.url: clean_ai_text(article.news.title)
        for article in articles
    }

    if not _ai_configured(settings):
        return localized, api_calls

    targets = [
        article
        for article in articles
        if _title_language_mismatch(article.news.title, settings.language)
    ]

    for start in range(0, len(targets), 30):
        cancel_token.check()
        batch = targets[start:start + 30]
        ids = {f"item_{index:03d}": article.news.title for index, article in enumerate(batch)}
        urls = {f"item_{index:03d}": article.news.url for index, article in enumerate(batch)}
        api_calls += 1

        try:
            translated = translate_titles(ids, settings, cancel_token=cancel_token)
        except TaskCancelledError:
            raise
        except Exception as exc:
            logger.error(
                "RSS 标题批量翻译失败，保留原始标题：%s",
                exc,
                exc_info=True,
                extra={"model": settings.deepseek_model},
            )
            continue

        for item_id, title in translated.items():
            localized[urls[item_id]] = title

    return localized, api_calls


def _importance_allowed(importance: str, configured_level: str) -> bool:
    if configured_level == "High_only":
        return importance == "High"

    if configured_level == "All":
        return importance in {"High", "Medium", "Low"}

    return importance in {"High", "Medium"}


def _fallback_assessment(language: str) -> NewsAssessment:
    reason = (
        "AI unavailable; retained as Medium for compatibility."
        if language == "en_US"
        else "AI 不可用，按 Medium 保留以兼容非 AI 工作流。"
    )
    return NewsAssessment("Medium", reason)


def _ai_configured(settings: AppSettings) -> bool:
    return bool(
        settings.deepseek_api_key
        and settings.deepseek_base_url
        and (settings.deepseek_model or settings.model_candidates)
    )


def _title_language_mismatch(title: str, language: str) -> bool:
    text = str(title or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))

    if letters < 2:
        return False

    ratio = chinese / letters
    return ratio < 0.15 if language == "zh_CN" else ratio > 0.2


def _export_context(
    settings: AppSettings,
    localized_titles: dict[str, str],
) -> ExportContext:
    return ExportContext(
        report_title=settings.report_title,
        show_summary=True,
        show_keywords=True,
        body_mode=settings.body_mode,
        localized_titles=localized_titles,
        include_title=settings.include_title,
        include_source=settings.include_source,
        include_published=settings.include_published,
        include_link=settings.include_link,
        include_score=settings.include_score,
        include_summary=settings.include_summary,
        include_keywords=settings.include_keywords,
        include_body=settings.include_body,
        include_original=settings.include_original,
    )


def _export_reports(
    articles: list[Article],
    summaries: dict[str, AISummary],
    assessments: dict[str, NewsAssessment],
    context: ExportContext,
    launch_articles: list[Article],
    settings: AppSettings,
    markdown_enabled: bool,
    word_enabled: bool,
    cancel_token: CancellationToken,
) -> None:
    cancel_token.check()

    if markdown_enabled:
        export_markdown(
            articles,
            summaries,
            context,
            assessments,
            cancel_check=cancel_token.check,
        )
        logger.info("导出 Markdown")

    if word_enabled:
        cancel_token.check()
        export_word(
            articles,
            summaries,
            context,
            assessments,
            launches=launch_articles,
            launch_range=settings.launch_range,
            launch_timezone=settings.display_timezone,
            launch_term_translator=_launch_term_translator(settings, cancel_token),
            cancel_check=cancel_token.check,
        )
        logger.info("导出 Word")


def _launch_term_translator(
    settings: AppSettings,
    cancel_token: CancellationToken,
):
    if (
        not settings.aerospace_translation_enabled
        or settings.language != "zh_CN"
    ):
        return None

    return Translator(
        settings=settings,
        mode=settings.aerospace_translation_mode,
        cancel_token=cancel_token,
    )


def _resolve_runtime_model(
    settings: AppSettings,
    cancel_token: CancellationToken,
) -> AppSettings:
    provider = provider_config_from_settings(settings)
    result = test_connection(
        provider,
        settings.model_candidates,
        cancel_token=cancel_token,
    )
    selected = result.model

    if selected != settings.deepseek_model:
        logger.warning("配置模型不可用，已自动选择模型：%s", selected)

    logger.info("AI Model: %s", selected)
    return replace(settings, deepseek_model=selected)


def _enabled(value: bool | None, fallback: bool) -> bool:
    return fallback if value is None else value


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
