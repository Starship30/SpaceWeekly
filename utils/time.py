from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

BEIJING_TIMEZONE = "Asia/Shanghai"
DEFAULT_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_datetime(value: str | datetime) -> datetime | None:
    """Parse ISO 8601 or RFC date text as a timezone-aware datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()

        if not text:
            return None

        normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def format_datetime(
    value: str | datetime,
    target_timezone: str = BEIJING_TIMEZONE,
    output_format: str = DEFAULT_FORMAT,
) -> str:
    """Convert a timestamp to the requested timezone and display format."""
    parsed = parse_datetime(value)

    if parsed is None:
        return str(value or "").strip()

    return parsed.astimezone(_timezone(target_timezone)).strftime(output_format)


def format_beijing_time(value: str | datetime) -> str:
    """Format a timestamp as Beijing time using the project display format."""
    return format_datetime(value, BEIJING_TIMEZONE)


def _timezone(name: str):
    if name == BEIJING_TIMEZONE:
        return timezone(timedelta(hours=8), name=BEIJING_TIMEZONE)

    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc
