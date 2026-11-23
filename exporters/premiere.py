"""Offline content snapshot; the selected Article order is the timeline order."""

import json
import re
from pathlib import Path
from uuid import uuid4

from config import OUTPUT_DIR
from models.ai_summary import AISummary
from models.article import Article
from models.news import is_manual_id, is_news_url, news_id
from models.export_context import ExportContext
from services.cancellation import CancelCheck, no_cancel
from utils.output_files import reserved_output
from utils.text import mission_display_name

PROTOCOL_VERSION = "1.0"


def build_snapshot(
    articles: list[Article],
    ai_summaries: dict[str, AISummary],
    context: ExportContext,
    language: str,
    cancel_check: CancelCheck = no_cancel,
    launches: list[dict] | None = None,
    *, job_id: str | None = None,
) -> dict:
    """Build the same content for direct editing or the portable JSON export."""
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
        if not news.source.strip():
            raise ValueError("Premiere: URL and source are required")
        if not is_news_url(news.url) and not (is_manual_id(news.manual_id) and news.url==''):
            raise ValueError("Premiere: URL must be an HTTP(S) URL without whitespace")
        item_id = news_id(news.key)
        if item_id in seen:
            raise ValueError(f"Premiere: duplicate news URL: {news.url}")
        seen.add(item_id)
        localized = context.localized_titles.get(news.key)
        title = localized if isinstance(localized, str) and localized.strip() else news.title
        if not title.strip():
            raise ValueError(f"Premiere: title is required: {news.url}")
        ai = ai_summaries.get(news.key)
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
        "job_id": job_id or str(uuid4()),
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
            for key in ('status', 'recovery'):
                if key in fields:
                    if not isinstance(fields[key], str):
                        raise ValueError(f'Premiere: invalid launch {key}')
                    record['launch'][key] = fields[key]
            if isinstance(fields.get('description_zh'), str):
                record['launch']['description_zh'] = fields['description_zh']
            record['summary'] = mission_display_name(record['summary'])
            if isinstance(launch.get('launch_context'), dict):
                from copy import deepcopy
                record['launch_context'] = deepcopy(launch['launch_context'])
                from services.launch_display import resolve_launch
                record['display_record']=resolve_launch(launch['launch_context'])
            payload['launches'].append(record)
    return payload


def export_premiere(articles,ai_summaries,context,language,cancel_check=no_cancel,launches=None) -> Path:
    payload = build_snapshot(articles,ai_summaries,context,language,cancel_check,launches)
    with reserved_output(OUTPUT_DIR, ".premiere.json") as (output_path, temp_path):
        cancel_check()
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        cancel_check()
    return output_path
