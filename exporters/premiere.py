"""Offline content snapshot; the selected Article order is the timeline order."""

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from config import OUTPUT_DIR
from models.ai_summary import AISummary
from models.article import Article
from models.export_context import ExportContext
from services.cancellation import CancelCheck, no_cancel
from utils.output_files import reserved_output

PROTOCOL_VERSION = "1.0"


def export_premiere(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    context: ExportContext,
    language: str,
    cancel_check: CancelCheck = no_cancel,
    launches: list[dict] | None = None,
) -> Path:
    """Export this run's in-memory result, without fetching or invoking AI."""
    cancel_check()
    items = []
    seen = set()
    for article in articles:
        cancel_check()
        news = article.news
        for field in ("title", "source", "published", "summary", "url"):
            value = getattr(news, field)
            if not isinstance(value, str):
                raise ValueError(f"Premiere: {field} must be a string")
        if not news.url.strip() or not news.source.strip():
            raise ValueError("Premiere: URL and source are required")
        if not re.fullmatch(r"https?://[^\s/]+(?:/[^\s]*)?", news.url, re.IGNORECASE):
            raise ValueError("Premiere: URL must be an HTTP(S) URL without whitespace")
        # News/Article carry no stable ID; SQLite's row ID is not in this run.
        item_id = "url-sha256:" + hashlib.sha256(news.url.encode("utf-8")).hexdigest()
        if item_id in seen:
            raise ValueError(f"Premiere: duplicate news URL: {news.url}")
        seen.add(item_id)
        localized = context.localized_titles.get(news.url)
        title = localized if isinstance(localized, str) and localized.strip() else news.title
        if not title.strip():
            raise ValueError(f"Premiere: title is required: {news.url}")
        ai = ai_summaries.get(news.url)
        if ai is not None and not isinstance(ai.summary, str):
            raise ValueError("Premiere: AI summary must be a string")
        use_ai = ai is not None and bool(ai.summary.strip())
        if ai is not None and not isinstance(ai.categories, list):
            raise ValueError("Premiere: categories must be a list")
        categories = list(ai.categories) if ai is not None else []
        if any(not isinstance(value, str) or not value.strip() for value in categories):
            raise ValueError("Premiere: categories must contain nonempty strings")
        items.append({
            "id": item_id,
            "title": title,
            "title_origin": "localized" if title == localized else "original",
            "summary": ai.summary if use_ai else news.summary,
            "summary_origin": "ai" if use_ai else "original",
            "summary_fallback": None if use_ai else (
                "missing_ai_summary" if ai is None else "empty_ai_summary"
            ),
            "source": news.source,
            "published": news.published,
            "url": news.url,
            "categories": categories,
        })
    if not isinstance(language, str) or not language.strip():
        raise ValueError("Premiere: language is required")
    if not isinstance(context.report_title, str):
        raise ValueError("Premiere: report_title must be a string")
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": str(uuid4()),
        "report_title": context.report_title if context.report_title.strip() else "SpaceWeekly",
        "language": language,
        "order_policy": "selected_articles_input_order",
        "items": items,
    }
    if launches is not None:
        if not isinstance(launches, list):
            raise ValueError('Premiere: launches must be a list')
        payload['launches'] = []
        for launch in launches:
            cancel_check()
            keys = ('id', 'title', 'summary', 'source', 'published', 'url')
            if not isinstance(launch, dict) or any(not isinstance(launch.get(key), str) for key in keys):
                raise ValueError('Premiere: invalid launch fields')
            if not launch['id'].strip() or launch['id'] in seen or not launch['title'].strip() or not launch['source'].strip():
                raise ValueError('Premiere: empty or duplicate launch identity/title/source')
            seen.add(launch['id'])
            if not re.fullmatch(r'https?://[^\s/]+(?:/[^\s]*)?', launch['url'], re.IGNORECASE):
                raise ValueError('Premiere: invalid launch URL')
            fields = launch.get('launch')
            if not isinstance(fields, dict) or any(not isinstance(fields.get(key), str) for key in ('orbit', 'payload', 'site', 'time')):
                raise ValueError('Premiere: invalid launch display fields')
            record = {key: launch[key] for key in keys}
            record.update(categories=[], video_type='launch',
                          launch={key: fields[key] for key in ('orbit', 'payload', 'site', 'time')})
            if isinstance(fields.get('display_timezone'), str):
                record['launch']['display_timezone'] = fields['display_timezone']
            payload['launches'].append(record)
    with reserved_output(OUTPUT_DIR, ".premiere.json") as (output_path, temp_path):
        cancel_check()
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        cancel_check()
    return output_path
