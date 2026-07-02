from dataclasses import dataclass


@dataclass
class ExportContext:
    report_title: str
    show_summary: bool
    show_keywords: bool
    show_category: bool
    show_importance: bool
    body_mode: str
    translations: dict[str, str]
    include_title: bool
    include_source: bool
    include_published: bool
    include_link: bool
    include_score: bool
    include_body: bool
    include_original: bool
    include_chinese: bool
