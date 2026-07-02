from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from config import OUTPUT_DIR
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from models.news_analysis import NewsAnalysis


def export_markdown(
    articles: list[Article],
    ai_summaries: dict[str, AISummary] | None = None,
    context: ExportContext | None = None,
    analyses: dict[str, NewsAnalysis] | None = None,
) -> Path:
    """Export articles to a UTF-8 Markdown weekly report."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _output_path()
    content = _build_markdown(articles, ai_summaries or {}, context, analyses or {})
    output_path.write_text(content, encoding="utf-8")

    return output_path


def _output_path() -> Path:
    today = datetime.now().date().isoformat()

    return OUTPUT_DIR / f"SpaceWeekly_{today}.md"


def _build_markdown(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    context: ExportContext | None,
    analyses: dict[str, NewsAnalysis],
) -> str:
    sorted_articles = _sort_articles(articles)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {_report_title(context)}",
        "",
        f"生成时间：{now}",
        "",
        f"发布日期：{datetime.now().date().isoformat()}",
        "",
        *_stats_lines(sorted_articles, ai_summaries),
    ]

    for category, category_articles in _group_by_category(sorted_articles, analyses).items():
        lines.extend(_category_lines(category, category_articles, ai_summaries, context, analyses))

    return "\n".join(lines)


def _sort_articles(articles: list[Article]) -> list[Article]:
    dated_articles = []

    for article in articles:
        published = _parse_published(article.news.published)

        if published is None:
            return articles

        dated_articles.append((published, article))

    dated_articles.sort(key=lambda item: item[0], reverse=True)

    return [article for _, article in dated_articles]


def _parse_published(published: str) -> datetime | None:
    try:
        return parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return None


def _stats_lines(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
) -> list[str]:
    lines = [f"新闻总数：{len(articles)}", "", "来源统计："]

    for source, count in _source_stats(articles).items():
        lines.append(f"{source}：{count}")

    lines.extend(["", "分类统计："])

    for category, count in _category_stats(articles, ai_summaries).items():
        lines.append(f"{category}：{count}")

    lines.extend(["", "---", ""])

    return lines


def _report_title(context: ExportContext | None) -> str:
    if context is None:
        return "SpaceWeekly"

    return context.report_title or "SpaceWeekly"


def _group_by_category(
    articles: list[Article],
    analyses: dict[str, NewsAnalysis],
) -> dict[str, list[Article]]:
    grouped: dict[str, list[Article]] = {}

    for article in articles:
        analysis = analyses.get(article.news.url)
        categories = analysis.categories if analysis and analysis.categories else ["其它"]

        for category in categories:
            grouped.setdefault(category, []).append(article)

    return grouped


def _source_stats(articles: list[Article]) -> dict[str, int]:
    stats: dict[str, int] = {}

    for article in articles:
        source = _source_name(article)
        stats[source] = stats.get(source, 0) + 1

    return stats


def _source_name(article: Article) -> str:
    url = article.news.url

    if "cneos.jpl.nasa.gov" in url:
        return "CNEOS"

    if "science.nasa.gov" in url:
        return "NASA Science"

    if "jpl.nasa.gov" in url:
        return "JPL"

    return article.news.source


def _category_stats(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
) -> dict[str, int]:
    stats: dict[str, int] = {}

    for article in articles:
        ai_summary = ai_summaries.get(article.news.url)

        if ai_summary is None or not ai_summary.category:
            continue

        stats[ai_summary.category] = stats.get(ai_summary.category, 0) + 1

    return stats


def _category_lines(
    category: str,
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    context: ExportContext | None,
    analyses: dict[str, NewsAnalysis],
) -> list[str]:
    lines = [f"# {category}", ""]

    for article in articles:
        lines.extend(
            _article_lines(
                article,
                ai_summaries.get(article.news.url),
                context,
                analyses.get(article.news.url),
            )
        )

    return lines


def _article_lines(
    article: Article,
    ai_summary: AISummary | None,
    context: ExportContext | None,
    analysis: NewsAnalysis | None,
) -> list[str]:
    news = article.news
    lines: list[str] = []

    if context is None or context.include_title:
        lines.extend([f"## {news.title}", ""])

    if context is None or context.include_source:
        lines.extend([f"来源：{_source_name(article)}", ""])

    if context is None or context.include_published:
        lines.extend([f"发布时间：{news.published}", ""])

    if analysis is not None and (context is None or context.include_score):
        lines.extend([f"新闻价值评分：{analysis.score} 分", f"筛选原因：{analysis.reason}", ""])

    if context is None or context.include_link:
        lines.extend([f"链接：{news.url}", ""])

    if ai_summary is None:
        lines.extend([f"摘要：{news.summary}", ""])
    else:
        lines.extend(_ai_summary_lines(ai_summary, context))

    if context is None or context.include_body:
        lines.extend(_body_lines(article, context))

    lines.extend(["---", ""])

    return lines


def _ai_summary_lines(
    ai_summary: AISummary,
    context: ExportContext | None,
) -> list[str]:
    show_summary = context is None or context.show_summary
    show_keywords = context is None or context.show_keywords
    show_category = context is None or context.show_category
    show_importance = context is None or context.show_importance
    lines: list[str] = []

    if show_summary:
        lines.extend([f"AI 摘要：{ai_summary.summary}", ""])

    if show_keywords:
        lines.extend([f"关键词：{'、'.join(ai_summary.keywords)}", ""])

    if show_category:
        lines.extend([f"分类：{ai_summary.category}", ""])

    if show_importance:
        lines.extend([f"重要程度：{ai_summary.importance}", ""])

    return lines


def _body_lines(article: Article, context: ExportContext | None) -> list[str]:
    translation = (context.translations.get(article.news.url) if context else None)

    if context is not None and context.body_mode == "translated" and translation:
        return ["正文：", "", translation, ""]

    if context is not None and context.body_mode == "bilingual" and translation:
        return ["正文：", "", _bilingual_body(article.body, translation), ""]

    if context is not None and not context.include_original:
        return []

    return ["正文：", "", article.body, ""]


def _bilingual_body(original: str, translation: str) -> str:
    original_lines = [line for line in original.splitlines() if line.strip()]
    translated_lines = [line for line in translation.splitlines() if line.strip()]
    blocks = []

    for index, original_line in enumerate(original_lines):
        blocks.append(original_line)

        if index < len(translated_lines):
            blocks.append("")
            blocks.append(translated_lines[index])

        blocks.append("")

    return "\n".join(blocks).strip()
