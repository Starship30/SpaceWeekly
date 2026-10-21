from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from config import OUTPUT_DIR
from i18n import tr
from exporters.launches import launch_fields, launch_section_title
from utils.time import BEIJING_TIMEZONE, format_datetime
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from models.news_assessment import NewsAssessment
from utils.output_files import reserved_output
from utils.text import clean_ai_text


def export_markdown(
    articles: list[Article],
    ai_summaries: dict[str, AISummary] | None = None,
    context: ExportContext | None = None,
    assessments: dict[str, NewsAssessment] | None = None,
    cancel_check=None,
    launches: list[Article] | None = None,
    launch_range: str = "next_week",
    launch_timezone: str = BEIJING_TIMEZONE,
) -> Path:
    """Export the compact two-stage result to UTF-8 Markdown."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    check_cancel = cancel_check or (lambda: None)
    check_cancel()
    content = _build_markdown(
        articles,
        ai_summaries or {},
        context,
        assessments or {},
        launches=launches, launch_range=launch_range, launch_timezone=launch_timezone,
    )

    with reserved_output(OUTPUT_DIR, ".md") as (output_path, temp_path):
        check_cancel()
        temp_path.write_text(content, encoding="utf-8")
        check_cancel()

    return output_path


def _build_markdown(
    articles: list[Article],
    summaries: dict[str, AISummary],
    context: ExportContext | None,
    assessments: dict[str, NewsAssessment],
    *,
    launches: list[Article] | None = None,
    launch_range: str = "next_week",
    launch_timezone: str = BEIJING_TIMEZONE,
) -> str:
    sorted_articles = _sort_articles(articles)
    separator = tr("label_separator")
    lines = [
        f"# {_report_title(context)}",
        "",
        f"{tr('report_generated_at')}{separator}{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"{tr('report_release_date')}{separator}{datetime.now().date().isoformat()}",
        "",
        f"{tr('report_news_count')}{separator}{len(sorted_articles)}",
        "",
        f"{tr('report_source_stats')}{separator}",
    ]

    for source, count in _source_stats(sorted_articles).items():
        lines.append(f"{source}{separator}{count}")

    lines.extend(["", "---", ""])
    if launches is not None and launch_range != "disabled":
        lines.extend([f"# {launch_section_title(launch_range)}", ""])
        if not launches:
            lines.extend([tr("launch_none"), ""])
        for launch in launches:
            fields = launch_fields(launch)
            fields["time"] = format_datetime(fields["time"], launch_timezone)
            lines.extend([f"## {clean_ai_text(fields['name'])}", ""])
            for label, key in [("launch_time", "time"), ("rocket", "rocket"),
                               ("launch_operator", "provider"), ("launch_site", "pad"),
                               ("orbit", "orbit"), ("launch_status", "status"),
                               ("recovery_status", "recovery"), ("mission_description", "mission_description")]:
                lines.extend([f"{tr(label)}{separator}{clean_ai_text(fields[key])}", ""])
            lines.extend([f"{tr('report_link')}{separator}{launch.news.url}", "", "---", ""])

    for section, section_articles in _group_by_importance(
        sorted_articles,
        assessments,
    ).items():
        lines.extend([f"# {section}", ""])

        for article in section_articles:
            lines.extend(
                _article_lines(
                    article,
                    summaries.get(article.news.url),
                    assessments.get(article.news.url),
                    context,
                )
            )

    return "\n".join(lines)


def _article_lines(
    article: Article,
    summary: AISummary | None,
    assessment: NewsAssessment | None,
    context: ExportContext | None,
) -> list[str]:
    news = article.news
    separator = tr("label_separator")
    title = (
        context.localized_titles.get(news.url, news.title)
        if context is not None
        else news.title
    )
    lines: list[str] = []

    if context is None or context.include_title:
        lines.extend([f"## {clean_ai_text(title)}", ""])

    if context is None or context.include_published:
        lines.extend(
            [f"{tr('report_published')}{separator}{clean_ai_text(news.published)}", ""]
        )

    if context is None or context.include_source:
        lines.extend(
            [f"{tr('report_source')}{separator}{clean_ai_text(_source_name(article))}", ""]
        )

    if assessment is not None and (context is None or context.include_score):
        lines.extend(
            [
                f"{tr('report_importance')}{separator}{_importance_value_label(assessment.importance)}",
                f"{tr('report_reason')}{separator}{clean_ai_text(assessment.reason)}",
                "",
            ]
        )

    if summary is not None:
        if summary.categories:
            categories = tr("list_separator").join(
                clean_ai_text(category)
                for category in summary.categories
                if clean_ai_text(category)
            )
            lines.extend(
                [f"{tr('report_category')}{separator}{categories}", ""]
            )

        if context is None or (context.show_summary and context.include_summary):
            lines.extend(
                [
                    f"{tr('report_ai_summary')}{separator}{clean_ai_text(summary.summary)}",
                    "",
                ]
            )

        if context is None or (context.show_keywords and context.include_keywords):
            keywords = tr("list_separator").join(
                clean_ai_text(keyword)
                for keyword in summary.keywords
                if clean_ai_text(keyword)
            )
            lines.extend([f"{tr('report_keywords')}{separator}{keywords}", ""])
    elif context is None or context.include_summary:
        lines.extend(
            [f"{tr('report_summary')}{separator}{clean_ai_text(news.summary)}", ""]
        )

    if context is None or context.include_link:
        lines.extend([f"{tr('report_link')}{separator}{news.url}", ""])

    if (
        (context is None or context.include_body)
        and (context is None or context.body_mode != "none")
        and (context is None or context.include_original)
    ):
        lines.extend(
            [
                f"{tr('report_body')}{separator}",
                "",
                clean_ai_text(article.body),
                "",
            ]
        )

    lines.extend(["---", ""])
    return lines


def _sort_articles(articles: list[Article]) -> list[Article]:
    dated_articles = []

    for article in articles:
        try:
            published = parsedate_to_datetime(article.news.published)
        except (TypeError, ValueError):
            return articles

        dated_articles.append((published, article))

    dated_articles.sort(key=lambda item: item[0], reverse=True)
    return [article for _, article in dated_articles]


def _group_by_importance(
    articles: list[Article],
    assessments: dict[str, NewsAssessment],
) -> dict[str, list[Article]]:
    grouped: dict[str, list[Article]] = {}

    for article in articles:
        assessment = assessments.get(article.news.url)
        level = assessment.importance if assessment is not None else "Medium"
        grouped.setdefault(_importance_section(level), []).append(article)

    return grouped


def _importance_section(level: str) -> str:
    labels = {
        "High": f"🔥 {tr('report_section_high')}",
        "Medium": f"📰 {tr('report_section_medium')}",
        "Low": f"📌 {tr('report_section_low')}",
    }
    return labels.get(level, labels["Medium"])


def _importance_value_label(level: str) -> str:
    labels = {
        "High": tr("importance_high"),
        "Medium": tr("importance_medium"),
        "Low": tr("importance_low"),
    }
    return labels.get(level, tr("importance_medium"))


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


def _report_title(context: ExportContext | None) -> str:
    if context is None:
        return "SpaceWeekly"

    return clean_ai_text(context.report_title) or "SpaceWeekly"
