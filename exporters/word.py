from pathlib import Path

from config import OUTPUT_DIR
from i18n import tr
from exporters.launches import launch_fields as _launch_fields, launch_section_title as _launch_section_title
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from models.news_assessment import NewsAssessment
from utils.time import BEIJING_TIMEZONE
from utils.time import format_datetime
from utils.output_files import reserved_output
from utils.text import clean_ai_text

IMPORTANCE_LEVELS = ["High", "Medium", "Low"]


def export_word(
    articles: list[Article],
    ai_summaries: dict[str, AISummary] | None = None,
    context: ExportContext | None = None,
    assessments: dict[str, NewsAssessment] | None = None,
    launches: list[Article] | None = None,
    launch_range: str = "next_week",
    launch_timezone: str = BEIJING_TIMEZONE,
    launch_term_translator=None,
    cancel_check=None,
) -> Path:
    """Export articles to a styled Word document."""
    from docx import Document

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    check_cancel = cancel_check or (lambda: None)
    check_cancel()
    document = Document()
    _setup_styles(document)
    document.add_heading(_report_title(context), level=0)

    if launch_range != "disabled":
        document.add_heading(_launch_section_title(launch_range), level=1)

        if launches:
            for launch in launches:
                check_cancel()
                _add_launch(
                    document,
                    launch,
                    launch_term_translator,
                    launch_timezone,
                )
        else:
            document.add_paragraph(tr("launch_none"))

    document.add_heading(tr("weekly_space_news"), level=1)

    for section, section_articles in _group_by_importance(
        articles,
        assessments or {},
    ).items():
        document.add_heading(section, level=2)

        for article in section_articles:
            check_cancel()
            _add_article(
                document,
                article,
                (ai_summaries or {}).get(article.news.url),
                context,
                (assessments or {}).get(article.news.url),
            )

    with reserved_output(OUTPUT_DIR, ".docx") as (output_path, temp_path):
        check_cancel()
        document.save(temp_path)
        check_cancel()

    return output_path


def _add_launch(
    document,
    launch: Article,
    term_translator,
    launch_timezone: str,
) -> None:
    fields = _launch_fields(launch)
    fields["time"] = format_datetime(fields["time"], launch_timezone)
    fields["name"] = _translated_term(fields["name"], "mission", term_translator)
    fields["rocket"] = _translated_term(fields["rocket"], "rocket", term_translator)
    fields["provider"] = _translated_term(
        fields["provider"],
        "organization",
        term_translator,
    )
    fields["pad"] = _translated_term(fields["pad"], "location", term_translator)
    fields["mission_name"] = _translated_term(
        fields["mission_name"],
        "mission",
        term_translator,
    )
    fields["orbit"] = _translated_term(fields["orbit"], "orbit", term_translator)
    fields["mission_description"] = _translated_description(
        fields["mission_description"],
        term_translator,
    )
    title = fields["mission_name"]

    if title == tr("not_available"):
        title = fields["name"]

    document.add_heading(f"🚀 {title}", level=2)
    _add_label(document, tr("launch_time"), fields["time"])
    _add_label(document, tr("rocket"), fields["rocket"])
    _add_label(document, tr("launch_operator"), fields["provider"])
    _add_label(document, tr("launch_site"), fields["pad"])
    _add_label(document, tr("orbit"), fields["orbit"])
    _add_label(document, tr("launch_status"), fields["status"])
    _add_label(document, tr("recovery_status"), fields["recovery"])
    _add_label(document, tr("mission_description"), fields["mission_description"])


def _translated_term(
    value: str,
    term_type: str,
    translator,
) -> str:
    if translator is None or value in {"Not available", tr("not_available")}:
        return value

    return translator.translate_term(value, term_type, allow_ai=True) or value


def _translated_description(value: str, translator) -> str:
    if (
        translator is None
        or value in {"Not available", tr("not_available")}
        or value == "No mission description available."
    ):
        return value

    return translator.translate_description(value) or value


def _setup_styles(document) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    for style_name in ["Normal", "Heading 1", "Heading 2"]:
        style = document.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    document.styles["Normal"].font.size = Pt(11)
    document.styles["Heading 1"].font.size = Pt(18)
    document.styles["Heading 2"].font.size = Pt(14)
    document.styles["Heading 1"].font.bold = True
    document.styles["Heading 2"].font.bold = True


def _report_title(context: ExportContext | None) -> str:
    if context is None:
        return "SpaceWeekly"

    return context.report_title or "SpaceWeekly"


def _add_article(
    document,
    article: Article,
    ai_summary: AISummary | None,
    context: ExportContext | None,
    assessment: NewsAssessment | None,
) -> None:
    news = article.news
    title = (
        context.localized_titles.get(news.url, news.title)
        if context is not None
        else news.title
    )

    if context is None or context.include_title:
        document.add_heading(clean_ai_text(title), level=2)

    if context is None or context.include_published:
        _add_label(document, tr("report_published"), clean_ai_text(news.published))

    if context is None or context.include_source:
        _add_label(document, tr("report_source"), clean_ai_text(news.source))

    if assessment is not None and (context is None or context.include_score):
        _add_label(
            document,
            tr("report_importance"),
            _importance_label(assessment.importance),
        )
        _add_label(document, tr("report_reason"), clean_ai_text(assessment.reason))

    if ai_summary is None:
        _add_label(document, tr("report_summary"), clean_ai_text(news.summary))
    else:
        _add_ai_summary(document, ai_summary, context)

    if context is None or context.include_link:
        paragraph = document.add_paragraph()
        paragraph.add_run(
            f"{tr('report_link')}{tr('label_separator')}"
        ).bold = True
        _add_hyperlink(paragraph, news.url, news.url)

    if context is None or context.include_body:
        body_text = _body_text(article, context)

        if body_text:
            document.add_heading(tr("report_body"), level=2)
            document.add_paragraph(body_text)

def _add_ai_summary(document, ai_summary: AISummary, context: ExportContext | None) -> None:
    if ai_summary.categories:
        _add_label(
            document,
            tr("report_category"),
            _join_values(ai_summary.categories),
        )

    if context is None or (context.show_summary and context.include_summary):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = 6
        run = paragraph.add_run(
            f"{tr('report_ai_summary')}{tr('label_separator')}{clean_ai_text(ai_summary.summary)}"
        )
        run.bold = True

    if context is None or (context.show_keywords and context.include_keywords):
        _add_label(document, tr("report_keywords"), _join_values(ai_summary.keywords))


def _body_text(article: Article, context: ExportContext | None) -> str:
    if context is not None and context.body_mode == "none":
        return ""

    if context is not None and not context.include_original:
        return ""

    return article.body


def _add_label(document, label: str, value: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(f"{label}{tr('label_separator')}")
    run.bold = True
    paragraph.add_run(clean_ai_text(value))


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE

    relationship_id = paragraph.part.relate_to(
        url,
        RELATIONSHIP_TYPE.HYPERLINK,
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    text_element = OxmlElement("w:t")
    text_element.text = text
    run.append(text_element)
    hyperlink.append(run)
    paragraph._element.append(hyperlink)


def _group_by_importance(
    articles: list[Article],
    assessments: dict[str, NewsAssessment],
) -> dict[str, list[Article]]:
    grouped = {_importance_label(level): [] for level in IMPORTANCE_LEVELS}

    for article in articles:
        assessment = assessments.get(article.news.url)
        level = assessment.importance if assessment is not None else "Medium"
        label = _importance_label(level)
        grouped.setdefault(label, []).append(article)

    return {label: items for label, items in grouped.items() if items}


def _importance_label(level: str) -> str:
    text = (level or "").strip()
    aliases = {
        "High": tr("importance_high"),
        "Medium": tr("importance_medium"),
        "Low": tr("importance_low"),
        "high": tr("importance_high"),
        "medium": tr("importance_medium"),
        "low": tr("importance_low"),
    }

    if text in aliases:
        return aliases[text]

    return text or tr("importance_medium")


def _join_values(values: list[str]) -> str:
    return tr("list_separator").join(
        clean_ai_text(value)
        for value in values
        if clean_ai_text(value)
    )
