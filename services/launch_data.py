"""Local launch collections and explicit, bounded LL2 pagination for roundups."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from resources import resource_path
from services.cancellation import TaskCancelledError
from utils.time import parse_datetime, _timezone


def write_json(path, data, *, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=None if compact else 2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_collection(items, start='', end='', complete=False, reason='', pages=1, *, display_timezone='Asia/Shanghai'):
    data = dict(version=1, items=deepcopy(items), start=start, end=end, timezone=display_timezone, complete=bool(complete),
                reason=reason, pages=pages, retrieved_at=datetime.now(timezone.utc).isoformat())
    archive_path = resource_path('cache', 'launch_archive.json')
    archive = json.loads(archive_path.read_text(encoding='utf-8')) if archive_path.exists() else {}
    if not isinstance(archive, dict) or any(not isinstance(i, dict) for i in archive.values()):
        raise ValueError('本地发射档案格式无效，旧档案未覆盖')
    if complete and start and end:
        first = datetime.fromisoformat(start).replace(tzinfo=_timezone(display_timezone))
        last = datetime.fromisoformat(end).replace(tzinfo=first.tzinfo) + timedelta(days=1)
        archive = {key: value for key, value in archive.items() if parse_datetime(value.get('net')) is None
                   or not first <= parse_datetime(value['net']) < last}
    for item in items:
        if item.get('ll2_id'):
            archive[item['ll2_id']] = {k: deepcopy(v) for k, v in item.items() if k not in ('research', 'display')}
    write_json(archive_path, archive)
    write_json(resource_path('cache', 'launch_collection.json'), data)
    return data


def load_existing(output_dir):
    """Read only: opening an editor must never fetch or refresh launch records."""
    path = resource_path('cache', 'launch_collection.json')
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                or any(not isinstance(i, dict) for i in data['items'])):
            raise ValueError('本地发射数据格式无效，请显式刷新')
        archive_path = resource_path('cache', 'launch_archive.json')
        archive = json.loads(archive_path.read_text(encoding='utf-8')) if archive_path.exists() else {}
        if not isinstance(archive, dict) or any(not isinstance(i, dict) for i in archive.values()):
            raise ValueError('本地发射档案格式无效')
        if data.get('complete') and data.get('start') and data.get('end'):
            first = datetime.fromisoformat(data['start']).replace(tzinfo=_timezone(data.get('timezone', 'Asia/Shanghai')))
            last = datetime.fromisoformat(data['end']).replace(tzinfo=first.tzinfo) + timedelta(days=1)
            # Completed refreshed ranges supersede old records that LL2 no longer returns in that range.
            archive = {key: value for key, value in archive.items() if parse_datetime(value.get('net')) is None
                       or not first <= parse_datetime(value['net']) < last}
        archive.update({i['ll2_id']: i for i in data['items'] if i.get('ll2_id')})
        data['items'] = list(archive.values())
        return data
    from services.launch_research import load_launch_research
    items = {}
    for record in load_launch_research().values():
        context = record.get('input', {})
        if context.get('ll2_id'):
            items[context['ll2_id']] = dict(context, research=record)
    # Older content packages provide useful records, but never prove complete coverage.
    for snapshot in sorted(output_dir.glob('*.premiere.json'), key=lambda p: p.stat().st_mtime) [-20:]:
        try:
            data = json.loads(snapshot.read_text(encoding='utf-8-sig'))
            for item in data.get('launches', []) + data.get('items', []):
                context = item.get('launch_context', {})
                if context.get('ll2_id'):
                    items[context['ll2_id']] = context
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return dict(version=1, items=list(items.values()), start='', end='', complete=False,
                reason='来自旧内容包/资料缓存，缺少分页完整性证明', pages=0)


def supplement_context(context, settings, token, *, get=None):
    """Explicit preparation only: recover this ID's original LL2 fields, never add events."""
    from feeds.launches import API_URL, launch_context
    identifier = context['ll2_id']
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', identifier):
        raise ValueError('缺少有效的 LL2 发射 ID，不能补取原始资料')
    token.check()
    response = (get or requests.get)(API_URL + identifier + '/', params={'mode': 'detailed'},
        headers={'User-Agent': settings.user_agent}, allow_redirects=False,
        timeout=(min(10, max(1, settings.connect_timeout)), min(20, max(1, settings.read_timeout))))
    token.check()
    response.raise_for_status()
    raw = response.json()
    if (not isinstance(raw, dict) or raw.get('id') != identifier
            or parse_datetime(raw.get('net')) != parse_datetime(context.get('net'))):
        raise ValueError('LL2 的 ID 或时间与草稿不同；保留快照，请先显式刷新发射数据')
    result = launch_context(raw)
    result['ll2_source'] = dict(url=API_URL + identifier + '/', retrieved_at=datetime.now(timezone.utc).isoformat(),
                               description=result['description'], mission_name=result['english_name'], net=result['net'],
                               identity={k: result[k] for k in ('english_name', 'net', 'rocket', 'site', 'provider')})
    return result


def refresh_collection(start, end, settings, token, *, get=None):
    """Explicit refresh only; retain partial results and explain incomplete pagination."""
    from feeds.launches import API_URL, _query_params, launch_context
    params = _query_params(100, 'custom', start_date=start, end_date=end, display_timezone=settings.display_timezone)
    params['mode'] = 'detailed'
    get = get or requests.get
    url, visited, rows, expected = API_URL, set(), {}, None
    complete, reason, pages = False, '', 0
    try:
        while url and pages < 20:
            token.check()
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.netloc != urlsplit(API_URL).netloc
                    or parsed.path != urlsplit(API_URL).path or url in visited):
                raise ValueError('LL2 分页地址无效或重复')
            visited.add(url)
            response = get(url, params=params if pages == 0 else None,
                           timeout=(min(10, max(1, settings.connect_timeout)), min(30, max(1, settings.read_timeout))),
                           headers={'User-Agent': settings.user_agent}, allow_redirects=False)
            token.check()
            if response.is_redirect:
                raise ValueError('LL2 分页发生重定向')
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise ValueError('LL2 分页响应无效')
            if pages == 0:
                expected = data.get('count')
            elif data.get('count') != expected:
                raise ValueError('LL2 分页期间总数发生变化，请重试')
            for raw in data['results']:
                token.check()
                context = launch_context(raw)
                identifier = context['ll2_id']
                if not identifier or identifier in rows:
                    raise ValueError('LL2 记录缺少 ID 或跨页重复')
                rows[identifier] = context
            pages += 1
            url = data.get('next')
            if url is not None and not isinstance(url, str):
                raise ValueError('LL2 next 不是地址')
        first, last = parse_datetime(params['net__gte']), parse_datetime(params['net__lt'])
        valid_dates = all(parse_datetime(row.get('net')) is not None
                          and first <= parse_datetime(row['net']) < last for row in rows.values())
        complete = not url and type(expected) is int and expected == len(rows) and valid_dates
        reason = '' if complete else '分页上限或总数/实际条数不符，不能视为全球完整统计'
    except TaskCancelledError:
        raise
    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        reason = f'采集未完成：{error}'
    token.check()
    # A total failure must not erase the user's last usable collection.
    if not rows and reason:
        raise RuntimeError(reason)
    return save_collection(list(rows.values()), start, end, complete, reason, pages, display_timezone=settings.display_timezone)
