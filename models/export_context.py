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
    include_summary: bool = True
    include_translation: bool = True
    include_categories: bool = True
    include_keywords: bool = True
    include_body: bool = True
    include_original: bool = True
    include_chinese: bool = True
