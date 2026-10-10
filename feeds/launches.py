import json
import hashlib
import logging
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from utils.time import _timezone
from models.article import Article
from models.news import News

API_URL = "https://ll.thespacedevs.com/2.3.0/launches/"
SOURCE = "Launch Library 2"
DEFAULT_TIMEOUT = 10
_LAST_ERROR = ""
_LAST_FIRST_LAUNCH: dict | None = None

logger = logging.getLogger(__name__)


def get_launches(
    limit: int = 10,
    timeout: int | None = None,
    launch_range: str = "next_week",
    start_date: str = "",
    end_date: str = "",
    display_timezone: str = "Asia/Shanghai",
    video_items: list[dict] | None = None,
) -> list[Article]:
    """Fetch launches in the configured date range as Article objects."""
    global _LAST_ERROR, _LAST_FIRST_LAUNCH
    _LAST_ERROR = ""
    _LAST_FIRST_LAUNCH = None

    if launch_range == "disabled":
        return []

    params = _query_params(limit, launch_range, start_date=start_date, end_date=end_date, display_timezone=display_timezone)

    try:
        import requests
    except ImportError as exc:
        _LAST_ERROR = str(exc)
        logger.warning("Launch Library 2 request skipped: %s", exc)
        return []

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        _LAST_ERROR = str(exc)
        logger.warning("Launch Library 2 request failed: %s", exc)
        return []

    results = payload.get("results", [])

    if not isinstance(results, list):
        _LAST_ERROR = "Unexpected Launch Library 2 response"
        logger.warning(_LAST_ERROR)
        return []

    _LAST_FIRST_LAUNCH = next(
        (item for item in results if isinstance(item, dict)),
        None,
    )
    _debug_launch_payload(results)

    if video_items is not None:
        video_items.extend(_launch_video_item(item, display_timezone) for item in results if isinstance(item, dict))
    return [_launch_to_article(item) for item in results if isinstance(item, dict)]


def _launch_video_item(item, display_timezone):
    url = _launch_url(item)
    identifier = _text(item.get('id'))
    net = _text(item.get('net'))
    display_time = net
    try:
        parsed = datetime.fromisoformat(net.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            local = parsed.astimezone(_timezone(display_timezone))
            display_time = local.strftime('%Y/%m/%d %H:%M')
    except ValueError:
        pass
    return dict(id='ll2:'+identifier if identifier else 'launch-url-sha256:'+hashlib.sha256(url.encode()).hexdigest(),
        title=_nested_text(item, 'rocket', 'configuration', 'full_name') or _text(item.get('name')) or 'Upcoming Launch',
        summary=_nested_text(item, 'mission', 'name') or _text(item.get('name')),
        source=SOURCE, published=net, url=url, categories=[], video_type='launch',
        launch=dict(orbit=_nested_text(item, 'mission', 'orbit', 'name'),
                    payload=_nested_text(item, 'mission', 'name'), site=_nested_text(item, 'pad', 'name'),
                    time=display_time, display_timezone=display_timezone))


def last_error() -> str:
    """Return the most recent Launch Library 2 request error."""
    return _LAST_ERROR


def last_first_launch_json() -> dict | None:
    """Return the first raw launch object from the most recent request."""
    return _LAST_FIRST_LAUNCH


def _query_params(
    limit: int,
    launch_range: str,
    now: datetime | None = None,
    *,
    start_date: str = "",
    end_date: str = "",
    display_timezone: str = "Asia/Shanghai",
) -> dict[str, object]:
    current = now or datetime.now(timezone.utc)

    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if launch_range == "custom":
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if first > last:
            raise ValueError("Start date must not be after end date")
        tz = _timezone(display_timezone)
        start = datetime.combine(first, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        return {"limit": limit, "net__gte": _api_datetime(start), "net__lt": _api_datetime(end), "ordering": "net"}

    if launch_range == "past_30_days":
        start, end, ordering = current - timedelta(days=30), current, "-net"
    elif launch_range == "next_30_days":
        start, end, ordering = current, current + timedelta(days=30), "net"
    elif launch_range == "past_week":
        start, end, ordering = current - timedelta(days=7), current, "-net"
    else:
        start, end, ordering = current, current + timedelta(days=7), "net"

    return {
        "limit": limit,
        "net__gte": _api_datetime(start),
        "net__lte": _api_datetime(end),
        "ordering": ordering,
    }


def _api_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _launch_to_article(item: dict) -> Article:
    name = _text(item.get("name")) or "Upcoming Launch"
    net = _text(item.get("net"))
    rocket = _nested_text(item, "rocket", "configuration", "full_name")
    provider = _nested_text(item, "launch_service_provider", "name")
    pad = _nested_text(item, "pad", "name")
    mission_name = _nested_text(item, "mission", "name")
    mission_description = _nested_text(item, "mission", "description")
    orbit = _nested_text(item, "mission", "orbit", "name")
    status = _nested_text(item, "status", "name")
    url = _launch_url(item)
    _debug_launch_fields(
        item=item,
        name=name,
        net=net,
        rocket=rocket,
        provider=provider,
        pad=pad,
        mission_name=mission_name,
        mission_description=mission_description,
        orbit=orbit,
        status=status,
        url=url,
    )
    summary = _summary(
        net=net,
        rocket=rocket,
        pad=pad,
        mission_description=mission_description,
        orbit=orbit,
    )
    body = _body(
        name=name,
        net=net,
        rocket=rocket,
        provider=provider,
        pad=pad,
        mission_name=mission_name,
        mission_description=mission_description,
        orbit=orbit,
        status=status,
        url=url,
    )

    return Article(
        news=News(
            source=SOURCE,
            title=name,
            published=net,
            summary=summary,
            url=url,
        ),
        body=body,
        parser=SOURCE,
    )


def _launch_url(item: dict) -> str:
    for key in ["vidURLs", "vid_urls", "infoURLs", "info_urls"]:
        value = _first_url(item.get(key))

        if value:
            return value

    for key in ["url", "slug", "id"]:
        value = _text(item.get(key))

        if value:
            if key == "url":
                return value

            return f"{API_URL}{value}/"

    return API_URL


def _first_url(value) -> str:
    if not isinstance(value, list):
        return ""

    for item in value:
        if isinstance(item, dict):
            url = _text(item.get("url"))
        else:
            url = _text(item)

        if url:
            return url

    return ""


def _nested_text(data: dict, *keys: str) -> str:
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return ""

        current = current.get(key)

    return _text(current)


def _debug_launch_payload(results: list) -> None:
    if not logger.isEnabledFor(logging.DEBUG) or not results:
        return

    first = results[0]

    if not isinstance(first, dict):
        logger.debug("Launch Library 2 first result is not an object: %r", first)
        return

    logger.debug(
        "Launch Library 2 first launch raw JSON:\n%s",
        json.dumps(first, ensure_ascii=False, indent=2, default=str),
    )
    logger.debug(
        "Launch Library 2 first launch field presence: pad=%s mission=%s orbit=%s",
        isinstance(first.get("pad"), dict),
        isinstance(first.get("mission"), dict),
        isinstance(_nested_value(first, "mission", "orbit"), dict),
    )


def _debug_launch_fields(
    item: dict,
    name: str,
    net: str,
    rocket: str,
    provider: str,
    pad: str,
    mission_name: str,
    mission_description: str,
    orbit: str,
    status: str,
    url: str,
) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return

    logger.debug(
        "Launch Library 2 extracted fields before Article: "
        "name=%r net=%r rocket=%r provider=%r pad=%r mission_name=%r "
        "mission_description_empty=%s orbit=%r status=%r url=%r "
        "raw_has_pad=%s raw_has_mission=%s raw_has_orbit=%s",
        name,
        net,
        rocket,
        provider,
        pad,
        mission_name,
        not bool(mission_description),
        orbit,
        status,
        url,
        isinstance(item.get("pad"), dict),
        isinstance(item.get("mission"), dict),
        isinstance(_nested_value(item, "mission", "orbit"), dict),
    )


def _nested_value(data: dict, *keys: str):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _summary(
    net: str,
    rocket: str,
    pad: str,
    mission_description: str,
    orbit: str,
) -> str:
    parts = [
        f"Launch time: {net}" if net else "",
        f"Rocket: {rocket}" if rocket else "",
        f"Launch pad: {pad}" if pad else "",
        f"Mission: {mission_description}" if mission_description else "",
        f"Orbit: {orbit}" if orbit else "",
    ]

    return "\n".join(part for part in parts if part) or "Upcoming launch information"


def _body(
    name: str,
    net: str,
    rocket: str,
    provider: str,
    pad: str,
    mission_name: str,
    mission_description: str,
    orbit: str,
    status: str,
    url: str,
) -> str:
    lines = [
        f"Launch name: {name}",
        f"Launch time: {net}" if net else "",
        f"Rocket: {rocket}" if rocket else "",
        f"Launch service provider: {provider}" if provider else "",
        f"Launch pad: {pad}" if pad else "",
        f"Mission name: {mission_name}" if mission_name else "",
        f"Orbit: {orbit}" if orbit else "",
        f"Status: {status}" if status else "",
        "",
        "Mission description:",
        mission_description or "No mission description available.",
        "",
        f"Official or video link: {url}" if url else "",
    ]

    return "\n".join(line for line in lines if line != "").strip()
