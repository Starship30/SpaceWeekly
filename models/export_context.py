from dataclasses import dataclass


@dataclass
class ExportContext:
    show_summary: bool
    show_keywords: bool
    show_category: bool
    show_importance: bool
    body_mode: str
    translations: dict[str, str]
