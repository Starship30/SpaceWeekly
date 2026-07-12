import logging
from dataclasses import replace
from datetime import date
from datetime import timedelta
from email.utils import parsedate_to_datetime

from ai.deepseek import analyze
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
from models.news_analysis import NewsAnalysis
from services.feed_manager import FeedSource
from services.feed_manager import enabled_feeds
from services.prompt_manager import find_preset
from services.prompt_manager import prompt_value
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
    date_range = _date_range(settings)
    ai_summaries: dict[str, AISummary] = {}
    analyses: dict[str, NewsAnalysis] = {}
    translations: dict[str, str] = {}
    articles: list[Article] = []
    api_calls = 0

    if export_sqlite:
        initialize_database()

    logger.info("抓取 RSS")
    news_list = _filter_news(_fetch_news(selected_feeds, settings.rss_limit), date_range)
    logger.info("预计文章数量：%s", len(news_list))
    logger.info("预计 Token：%s", _estimate_tokens(news_list, settings))
    logger.info("预计耗时：约 %s 秒", _estimate_seconds(news_list, settings))

    for index, news in enumerate(news_list, start=1):
        logger.info("正在处理：%s %s / %s", news.source, index, len(news_list))
        article = _parse_article(news)

        if article is None:
            continue

        article_for_ai = _limit_article_tokens(article, settings.max_article_tokens)
        api_calls = _run_ai(
            article_for_ai,
            settings,
            ai_summaries,
            translations,
            analyses,
            api_calls,
        )

        if _should_ignore(article, settings, analyses):
            continue

        articles.append(article)

        if export_sqlite:
            save_status = "SAVE" if save_article(article) else "SKIP"
            logger.info("保存 SQLite %s", save_status)

    export_articles = _export_articles(export_sqlite, articles, date_range)
    context = _export_context(settings, translations)
    _export_reports(
        export_articles,
        ai_summaries,
        analyses,
        context,
        markdown_enabled,
        word_enabled,
    )

    return articles


def estimate_generation(feeds: list[FeedSource], settings: AppSettings) -> tuple[int, int, int]:
    """Return rough article, token, and seconds estimates for the GUI."""
    per_feed = settings.rss_limit if settings.rss_limit > 0 else 100
    article_count = max(len([feed for feed in feeds if feed.enabled]) * per_feed, 0)
    token_count = article_count * settings.max_article_tokens
    api_factor = int(_pipeline_ai_enabled(settings))
    seconds = article_count * max(api_factor, 1) * 8

    return article_count, token_count, seconds


def _enabled(value: bool | None, fallback: bool) -> bool:
    return fallback if value is None else value


def _fetch_news(feeds: list[FeedSource], rss_limit: int = 0) -> list[News]:
    import feedparser

    news_list: list[News] = []

    for feed in feeds:
        parsed_feed = feedparser.parse(feed.rss)
        entries = parsed_feed.entries

        if rss_limit > 0:
            entries = entries[:rss_limit]

        for item in entries:
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

    try:
        html = download(news.url)
    except Exception as exc:
        logger.warning("HTML 获取失败 %s %s，使用 RSS Summary 回退", news.url, exc)
        return _rss_summary_article(news)

    try:
        article = parser(news, html)
    except Exception as exc:
        logger.warning("Parser 异常 %s %s，使用 RSS Summary 回退", news.url, exc)
        return _rss_summary_article(news)

    if article.parser == "Generic":
        logger.info("Using Generic Parser")
    else:
        logger.info("解析 %s", article.parser)

    return article


def _rss_summary_article(news: News) -> Article:
    body = news.summary.strip() or news.title

    return Article(news=news, body=body, parser="RSS Summary")


def _run_ai(
    article: Article,
    settings: AppSettings,
    ai_summaries: dict[str, AISummary],
    translations: dict[str, str],
    analyses: dict[str, NewsAnalysis],
    api_calls: int,
) -> int:
    if not _pipeline_ai_enabled(settings):
        return api_calls

    if _ai_limit_reached(settings, api_calls):
        _handle_ai_limit(settings)
        return api_calls

    api_calls += 1
    _analyze_article(article, settings, ai_summaries, translations, analyses)

    return api_calls


def _pipeline_ai_enabled(settings: AppSettings) -> bool:
    return any(
        [
            settings.pipeline_score_enabled,
            settings.pipeline_filter_enabled,
            settings.pipeline_category_enabled,
            settings.pipeline_keywords_enabled,
            settings.pipeline_summary_enabled,
            settings.pipeline_translation_enabled,
            settings.ai_summary_enabled,
            settings.ai_translation_enabled,
            settings.ai_auto_filter_enabled,
        ]
    )


def _ai_limit_reached(settings: AppSettings, api_calls: int) -> bool:
    return api_calls >= settings.max_daily_api_calls


def _handle_ai_limit(settings: AppSettings) -> None:
    message = "已达到每日 API 调用上限"

    if settings.ai_limit_action == "stop":
        raise RuntimeError(message)

    logger.warning("%s，跳过后续 AI 调用", message)


def _analyze_article(
    article: Article,
    settings: AppSettings,
    ai_summaries: dict[str, AISummary],
    translations: dict[str, str],
    analyses: dict[str, NewsAnalysis],
) -> None:
    preset = find_preset(settings.prompt_preset)

    try:
        analysis = analyze(
            article,
            provider=settings.ai_provider,
            summary_prompt=prompt_value(settings.summary_prompt, preset, "summary"),
            translation_prompt=prompt_value(
                settings.translation_prompt,
                preset,
                "translation",
            ),
            category_prompt=_category_prompt(settings, preset),
            score_prompt=prompt_value(settings.score_prompt, preset, "score"),
            filter_prompt=prompt_value(settings.filter_prompt, preset, "filter"),
        )
    except Exception as exc:
        logger.warning("AI 分析失败 %s", exc)
        return

    analyses[article.news.url] = analysis

    if settings.pipeline_translation_enabled:
        translations[article.news.url] = analysis.translation

    ai_summaries[article.news.url] = _analysis_to_summary(analysis, settings)
    logger.info("AI 分析完成，重要程度：%s", _importance_level(analysis))


def _category_prompt(settings: AppSettings, preset) -> str:
    prompt = prompt_value(settings.category_prompt, preset, "category")
    labels = {
        "astronomy": "天文",
        "spaceflight": "航天",
        "humanities": "人文",
    }
    options = [
        labels.get(option, option)
        for option in settings.ai_category_options
        if option
    ]

    if not options:
        return prompt

    category_rule = "AI 分类方向仅从以下选项中选择，可多选：{}".format(
        "、".join(options)
    )

    if prompt:
        return f"{prompt}\n{category_rule}"

    return category_rule


def _analysis_to_summary(analysis: NewsAnalysis, settings: AppSettings) -> AISummary:
    return AISummary(
        summary=analysis.summary if settings.pipeline_summary_enabled else "",
        keywords=analysis.keywords if settings.pipeline_keywords_enabled else [],
        category="、".join(analysis.categories)
        if settings.pipeline_category_enabled
        else "",
        importance=_importance_level(analysis) if settings.ai_importance_enabled else "",
    )


def _should_ignore(
    article: Article,
    settings: AppSettings,
    analyses: dict[str, NewsAnalysis],
) -> bool:
    analysis = analyses.get(article.news.url)

    if analysis is None:
        return False

    if settings.pipeline_score_enabled and not _importance_allowed(analysis, settings):
        logger.info(
            "忽略非目标重要程度新闻：%s，%s",
            _importance_level(analysis),
            analysis.reason,
        )
        return True

    if (
        settings.pipeline_filter_enabled
        and settings.ai_auto_filter_enabled
        and not analysis.keep
    ):
        logger.info("AI 智能筛选忽略：%s", analysis.reason)
        return True

    return False


def _importance_allowed(analysis: NewsAnalysis, settings: AppSettings) -> bool:
    level = _importance_level(analysis)
    style = settings.report_style or "standard"

    if style == "complete":
        return True

    if style == "curated":
        return level == "High"

    if style == "custom":
        return (
            (level == "High" and settings.report_style_custom_high)
            or (level == "Medium" and settings.report_style_custom_medium)
            or (level == "Low" and settings.report_style_custom_low)
        )

    return level in {"High", "Medium"}


def _importance_level(analysis: NewsAnalysis) -> str:
    text = (analysis.importance or "").strip().lower()
    aliases = {
        "high": "High",
        "高": "High",
        "重点": "High",
        "本周重点": "High",
        "medium": "Medium",
        "中": "Medium",
        "推荐": "Medium",
        "推荐阅读": "Medium",
        "low": "Low",
        "低": "Low",
        "简讯": "Low",
    }

    if text in aliases:
        return aliases[text]

    if analysis.score >= 80:
        return "High"

    if analysis.score >= 50:
        return "Medium"

    return "Low"


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
        report_title=settings.report_title,
        show_summary=settings.pipeline_summary_enabled,
        show_keywords=settings.pipeline_keywords_enabled,
        show_category=settings.pipeline_category_enabled,
        show_importance=settings.ai_importance_enabled,
        body_mode=settings.body_mode,
        translations=translations,
        include_title=settings.include_title,
        include_source=settings.include_source,
        include_published=settings.include_published,
        include_link=settings.include_link,
        include_score=settings.include_score,
        include_summary=settings.include_summary,
        include_translation=settings.include_translation,
        include_categories=settings.include_categories,
        include_keywords=settings.include_keywords,
        include_body=settings.include_body,
        include_original=settings.include_original,
        include_chinese=settings.include_chinese,
    )


def _export_reports(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    analyses: dict[str, NewsAnalysis],
    context: ExportContext,
    markdown_enabled: bool,
    word_enabled: bool,
) -> None:
    if markdown_enabled:
        export_markdown(articles, ai_summaries, context, analyses)
        logger.info("导出 Markdown")

    if word_enabled:
        export_word(articles, ai_summaries, context, analyses)
        logger.info("导出 Word")


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
    return len(news_list) * max(int(_pipeline_ai_enabled(settings)), 1) * 8


def _limit_article_tokens(article: Article, max_tokens: int) -> Article:
    max_chars = max_tokens * 4

    if len(article.body) <= max_chars:
        return article

    return replace(article, body=article.body[:max_chars])
