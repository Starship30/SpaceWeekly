from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDateEdit


NEWS_DATE_RANGES = (
    ("today", "today"),
    ("last3", "last_3_days"),
    ("last7", "last_7_days"),
    ("last30", "last_30_days"),
    ("news.range.disabled", "disabled"),
)

LAUNCH_DATE_RANGES = (
    ("launch.range.past", "past_week"),
    ("launch.range.next", "next_week"),
    ("launch.range.past30", "past_30_days"),
    ("launch.range.next30", "next_30_days"),
    ("launch.range.disabled", "disabled"),
)


def date_edit(value: str, fallback_days: int) -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    parsed = QDate.fromString(value, "yyyy-MM-dd")
    edit.setDate(parsed if parsed.isValid() else QDate.currentDate().addDays(fallback_days))
    return edit
