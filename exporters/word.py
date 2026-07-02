from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext


def export_word(
    articles: list[Article],
    ai_summaries: dict[str, AISummary] | None = None,
    context: ExportContext | None = None,
) -> Path:
    """Export articles to a styled Word document."""
    from docx import Document

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _output_path()
    document = Document()
    _setup_styles(document)
    document.add_heading("SpaceWeekly", level=0)

    for index, article in enumerate(articles):
        if index:
            document.add_page_break()

        _add_article(document, article, (ai_summaries or {}).get(article.news.url), context)

    document.save(output_path)

    return output_path


def _output_path() -> Path:
    today = datetime.now().date().isoformat()

    return OUTPUT_DIR / f"SpaceWeekly_{today}.docx"


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


def _add_article(
    document,
    article: Article,
    ai_summary: AISummary | None,
    context: ExportContext | None,
) -> None:
    news = article.news
    document.add_heading(news.title, level=1)
    _add_label(document, "发布时间", news.published)
    _add_label(document, "来源", news.source)

    if ai_summary is None:
        _add_label(document, "摘要", news.summary)
    else:
        _add_ai_summary(document, ai_summary, context)

    document.add_heading("正文", level=2)
    document.add_paragraph(_body_text(article, context))
    paragraph = document.add_paragraph()
    paragraph.add_run("链接：").bold = True
    _add_hyperlink(paragraph, news.url, news.url)


def _add_ai_summary(document, ai_summary: AISummary, context: ExportContext | None) -> None:
    show_summary = context is None or context.show_summary
    show_keywords = context is None or context.show_keywords
    show_category = context is None or context.show_category
    show_importance = context is None or context.show_importance

    if show_summary:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = 6
        run = paragraph.add_run(f"AI 摘要：{ai_summary.summary}")
        run.bold = True

    if show_keywords:
        _add_label(document, "关键词", "、".join(ai_summary.keywords))

    if show_category:
        _add_label(document, "分类", ai_summary.category)

    if show_importance:
        _add_label(document, "重要程度", ai_summary.importance)


def _body_text(article: Article, context: ExportContext | None) -> str:
    translation = (context.translations.get(article.news.url) if context else None)

    if context is not None and context.body_mode == "translated" and translation:
        return translation

    if context is not None and context.body_mode == "bilingual" and translation:
        return _bilingual_body(article.body, translation)

    return article.body


def _bilingual_body(original: str, translation: str) -> str:
    original_lines = [line for line in original.splitlines() if line.strip()]
    translated_lines = [line for line in translation.splitlines() if line.strip()]
    blocks = []

    for index, original_line in enumerate(original_lines):
        blocks.append(original_line)

        if index < len(translated_lines):
            blocks.append(translated_lines[index])

        blocks.append("")

    return "\n".join(blocks).strip()


def _add_label(document, label: str, value: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(f"{label}：")
    run.bold = True
    paragraph.add_run(value)


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
