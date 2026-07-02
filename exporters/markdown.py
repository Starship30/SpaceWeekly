from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from config import OUTPUT_DIR
from models.ai_summary import AISummary
from models.article import Article


def export_markdown(
    articles: list[Article],
    ai_summaries: dict[str, AISummary] | None = None,
) -> Path:
    """Export articles to a UTF-8 Markdown weekly report."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _output_path()
    content = _build_markdown(articles, ai_summaries or {})

    output_path.write_text(content, encoding="utf-8")

    return output_path


def _output_path() -> Path:
    today = datetime.now().date().isoformat()

    return OUTPUT_DIR / f"SpaceWeekly_{today}.md"


def _build_markdown(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
) -> str:
    sorted_articles = _sort_articles(articles)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    published_date = datetime.now().date().isoformat()
    lines = [
        "# SpaceWeekly",
        "",
        f"生成时间：{now}",
        "",
        f"发布日期：{published_date}",
        "",
        *_stats_lines(sorted_articles, ai_summaries),
    ]

    for source, source_articles in _group_by_source(sorted_articles).items():
        lines.extend(_source_lines(source, source_articles, ai_summaries))

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
    lines = [
        f"新闻总数：{len(articles)}",
        "",
        "来源统计：",
    ]

    for source, count in _source_stats(articles).items():
        lines.append(f"{source}：{count}")

    lines.extend(["", "分类统计："])

    for category, count in _category_stats(articles, ai_summaries).items():
        lines.append(f"{category}：{count}")

    lines.extend(["", "---", ""])

    return lines


def _group_by_source(articles: list[Article]) -> dict[str, list[Article]]:
    grouped: dict[str, list[Article]] = {}

    for article in articles:
        source = _source_name(article)
        grouped.setdefault(source, []).append(article)

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

        if ai_summary is None:
            continue

        category = ai_summary.category
        stats[category] = stats.get(category, 0) + 1

    return stats


def _source_lines(
    source: str,
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
) -> list[str]:
    lines = [
        f"# {source}",
        "",
    ]

    for article in articles:
        lines.extend(_article_lines(article, ai_summaries.get(article.news.url)))

    return lines


def _article_lines(
    article: Article,
    ai_summary: AISummary | None,
) -> list[str]:
    news = article.news
    lines = [
        f"## {news.title}",
        "",
        f"来源：{_source_name(article)}",
        "",
        f"发布时间：{news.published}",
        "",
        f"链接：{news.url}",
        "",
    ]

    if ai_summary is None:
        lines.extend([f"摘要：{news.summary}", ""])
    else:
        lines.extend(_ai_summary_lines(ai_summary))

    lines.extend(["正文：", "", article.body, "", "---", ""])

    return lines


def _ai_summary_lines(ai_summary: AISummary) -> list[str]:
    keywords = "、".join(ai_summary.keywords)

    return [
        f"AI 摘要：{ai_summary.summary}",
        "",
        f"关键词：{keywords}",
        "",
        f"分类：{ai_summary.category}",
        "",
        f"重要程度：{ai_summary.importance}",
        "",
    ]
