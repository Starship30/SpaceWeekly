from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from models.article import Article


def export_markdown(articles: list[Article]) -> Path:
    """Export articles to a UTF-8 Markdown weekly report."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _output_path()
    content = _build_markdown(articles)

    output_path.write_text(content, encoding="utf-8")

    return output_path


def _output_path() -> Path:
    today = datetime.now().date().isoformat()

    return OUTPUT_DIR / f"SpaceWeekly_{today}.md"


def _build_markdown(articles: list[Article]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    published_date = datetime.now().date().isoformat()
    lines = [
        "# SpaceWeekly",
        "",
        f"生成时间：{now}",
        "",
        f"发布日期：{published_date}",
        "",
        "--------------------------------",
        "",
    ]

    for article in articles:
        lines.extend(_article_lines(article))

    return "\n".join(lines)


def _article_lines(article: Article) -> list[str]:
    news = article.news

    return [
        f"## {news.title}",
        "",
        f"来源：{news.source}",
        "",
        f"发布时间：{news.published}",
        "",
        f"链接：{news.url}",
        "",
        f"摘要：{news.summary}",
        "",
        "正文：",
        "",
        article.body,
        "",
        "--------------------------------",
        "",
    ]
