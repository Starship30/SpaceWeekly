"""Bilibili search and cancellable yt-dlp downloads, independent of Qt."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import shlex
import signal
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone, timedelta
from threading import Thread
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup
import requests

from models.video_download import DownloadProgress, VideoFormat, VideoInfo, VideoSearchResults
from services.cancellation import CancelCheck, TaskCancelledError, no_cancel
from utils.time import parse_datetime

logger = logging.getLogger(__name__)
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
           'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
           'Referer': 'https://www.bilibili.com/'}
VIDEO_HOSTS = {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'}
SHORT_HOSTS = {'b23.tv', 'www.b23.tv'}
VIDEO_TIMEZONE = timezone(timedelta(hours=8))


class VideoDownloadError(ValueError):
    def __init__(self, code: str, detail: str = '') -> None:
        self.code, self.detail = code, safe_error(detail)
        super().__init__(self.detail or code)


def safe_error(value: object) -> str:
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(value))
    text = re.sub(r'https?://[^\s]+', '[URL]', text)
    text = re.sub(r'(?i)(cookie|authorization)\s*[:=].*', r'\1=[redacted]', text)
    text = re.sub(r'(?i)(sessdata|bili_jct|DedeUserID__ckMd5|sid|qrcode_key|refresh_token|token)\s*[=:]\s*[^\s;]+', r'\1=[redacted]', text)
    return text[-1800:]


def _checked_url(value: str):
    try:
        parts = urlsplit(value.strip())
        if (parts.scheme not in ('http', 'https') or parts.username or parts.password
                or parts.port not in (None, 80, 443) or not parts.hostname):
            raise ValueError()
        return parts
    except ValueError:
        raise VideoDownloadError('invalid_url') from None


def normalize_video_url(value: str) -> str:
    """Accept public BV/av video pages only, preserving a single selected part."""
    parts = _checked_url(value)
    if parts.hostname not in VIDEO_HOSTS:
        raise VideoDownloadError('invalid_url')
    match = re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av[1-9][0-9]*)/?', parts.path)
    if not match:
        raise VideoDownloadError('invalid_url')
    query = parse_qs(parts.query)
    suffix = ''
    if 'p' in query:
        if len(query['p']) != 1 or not re.fullmatch(r'[1-9][0-9]{0,3}', query['p'][0]):
            raise VideoDownloadError('invalid_url')
        suffix = '?p='+query['p'][0]
    return 'https://www.bilibili.com/video/'+match[1]+suffix


def _anonymous_session(cookies=None) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    # Reuse proxy/CA settings explicitly, never browser cookies or netrc credentials.
    session.proxies.update(requests.utils.get_environ_proxies('https://www.bilibili.com/'))
    session.verify = os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or True
    session.headers.update(HEADERS)
    if cookies is not None:
        session.cookies.update(cookies)
    return session


def resolve_video_url(value: str, cancel_check: CancelCheck = no_cancel) -> str:
    parts = _checked_url(value)
    if parts.hostname not in SHORT_HOSTS:
        return normalize_video_url(value)
    if not re.fullmatch(r'/[A-Za-z0-9]+/?', parts.path):
        raise VideoDownloadError('invalid_url')
    url = 'https://b23.tv'+parts.path
    with _anonymous_session() as session:
        for _ in range(5):
            cancel_check()
            with session.get(url, timeout=(5, 12), allow_redirects=False, stream=True) as response:
                cancel_check()
                if response.status_code not in (301, 302, 303, 307, 308):
                    raise VideoDownloadError('short_link', f'HTTP {response.status_code}')
                url = urljoin(url, response.headers.get('Location', ''))
                target = _checked_url(url)
                if target.hostname in VIDEO_HOSTS:
                    return normalize_video_url(url)
                if target.hostname not in SHORT_HOSTS:
                    raise VideoDownloadError('invalid_url')
    raise VideoDownloadError('short_link')


def thumbnail_url(value: str) -> str:
    if value.startswith('//'):
        value = 'https:'+value
    try:
        parts = _checked_url(value)
        if parts.hostname == 'hdslb.com' or parts.hostname.endswith('.hdslb.com'):
            return 'https://'+parts.netloc+parts.path
    except VideoDownloadError:
        pass
    return ''


def search_page_url(keyword: str, order='asc', date_range=None) -> str:
    params = {'keyword': keyword, 'order': 'totalrank' if order == 'relevance' else 'pubdate'}
    if date_range is not None:
        _validate_date_range(date_range)
        params.update(pubtime_begin_s=date_range[0], pubtime_end_s=date_range[1]-1)
    return 'https://search.bilibili.com/video?'+urlencode(params)


def mission_date_range(item):
    date = parse_datetime(item.get('published', ''))
    if date is None:
        raise VideoDownloadError('task_date')
    try:
        begin = date.astimezone(VIDEO_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        window = int(begin.timestamp()), int((begin+timedelta(days=2)).timestamp())
        _validate_date_range(window)
        return window
    except (ValueError, OverflowError):
        raise VideoDownloadError('task_date') from None


def _validate_date_range(date_range):
    if (not isinstance(date_range, (tuple, list)) or len(date_range) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in date_range)
            or not 0 < date_range[0] < date_range[1] < 32503680000):
        raise VideoDownloadError('task_date')


def video_timestamp(info):
    if 0 < info.published_timestamp < 32503680000:
        return info.published_timestamp
    text = info.published
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{8}', text):
        try:
            return datetime.strptime(text, '%Y-%m-%d' if '-' in text else '%Y%m%d').replace(
                tzinfo=VIDEO_TIMEZONE).timestamp()
        except ValueError:
            return 0
    date = parse_datetime(text)
    return date.timestamp() if date else 0


def _date_items(items, date_range):
    if date_range is None:
        return items
    return [item for item in items if date_range[0] <= video_timestamp(item) < date_range[1]]



def parse_up_ids(value: str) -> list[str]:
    ids = []
    for part in re.split(r'[\s,，;；]+', value.strip()):
        if not part:
            continue
        if part.startswith('https://'):
            parsed = _checked_url(part)
            if parsed.netloc != 'space.bilibili.com':
                raise VideoDownloadError('up_ids')
            part = parsed.path.strip('/')
        if not re.fullmatch(r'[1-9][0-9]{0,18}', part):
            raise VideoDownloadError('up_ids')
        if part not in ids:
            ids.append(part)
    if len(ids) > 10:
        raise VideoDownloadError('up_ids')
    return ids


def sort_search_items(items, order='asc'):
    if order == 'relevance':
        return items
    def key(item):
        date = parse_datetime(item.published)
        stamp = item.published_timestamp or (date.timestamp() if date else 0)
        return (not bool(stamp), -stamp if order == 'desc' else stamp)
    return sorted(items, key=key)



def _get(session, url: str, cancel_check: CancelCheck, **kwargs) -> str:
    cancel_check()
    with session.get(url, timeout=(5, 12), stream=True, **kwargs) as response:
        response.raise_for_status()
        chunks, length = [], 0
        for chunk in response.iter_content(65536):
            cancel_check()
            length += len(chunk)
            if length > 6*1024*1024:
                raise VideoDownloadError('search', 'Response exceeds 6 MB')
            chunks.append(chunk)
    cancel_check()
    return b''.join(chunks).decode('utf-8')


def _search_items(rows: list, limit=20) -> list[VideoInfo]:
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get('type', 'video') != 'video':
            continue
        bvid = str(row.get('bvid', ''))
        if not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid) or bvid in seen:
            continue
        title = BeautifulSoup(str(row.get('title', '')), 'html.parser').get_text(' ', strip=True)
        if not title:
            continue
        seen.add(bvid)
        published = row.get('pubdate', '')
        stamp = published if isinstance(published, (int, float)) and 0 < published < 32503680000 else 0
        if stamp:
            published = datetime.fromtimestamp(stamp, VIDEO_TIMEZONE).strftime('%Y-%m-%d')
        mid = str(row.get('mid', ''))
        result.append(VideoInfo(bvid, title, 'https://www.bilibili.com/video/'+bvid,
            str(row.get('author', '')), str(row.get('duration', '')),
            thumbnail_url(str(row.get('pic', ''))), str(published),
            author_id=mid if re.fullmatch(r'[1-9][0-9]{0,18}', mid) else '',
            published_timestamp=stamp))
    return result[:limit]


def extract_search_page(html: str) -> list[VideoInfo]:
    """Extract actual SSR cards; an empty JS/challenge shell is an error."""
    # shortcut: SSR only; add opt-in browser rendering if anonymous API failures become frequent.
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for card in soup.select('[data-testid="bili-video-card"], .bili-video-card, .video-item, .video-list-item'):
        anchor = card.select_one('a[href*="/video/"]')
        if not anchor:
            continue
        try:
            url = normalize_video_url(urljoin('https://www.bilibili.com/', anchor.get('href', '')))
        except VideoDownloadError:
            continue
        title_node = card.select_one('.bili-video-card__info--tit, .video-title, .title, h3')
        image = card.select_one('img')
        author = card.select_one('.bili-video-card__info--author, .up-name')
        up = card.select_one('a[href*="space.bilibili.com/"]')
        mid = urlsplit(urljoin('https:', up.get('href', ''))).path.strip('/') if up else ''
        duration = card.select_one('.bili-video-card__stats__duration, .duration, .so-imgTag_rb')
        date = card.select_one('.bili-video-card__info--date, .pubdate, .time')
        rows.append(dict(bvid=urlsplit(url).path.split('/')[-1],
            title=(title_node.get('title') or title_node.get_text(' ', strip=True)) if title_node
                  else anchor.get('title', ''), author=author.get_text(strip=True) if author else '', mid=mid,
            pic=(image.get('data-src') or image.get('src', '')) if image else '',
            duration=duration.get_text(strip=True) if duration else '',
            pubdate=date.get_text(strip=True) if date else ''))
    return _search_items(rows)


def _api_payload(session, url, cancel_check, **kwargs):
    payload = json.loads(_get(session, url, cancel_check, **kwargs))
    if not isinstance(payload, dict):
        raise VideoDownloadError('search', 'Invalid API response')
    if payload.get('code') != 0:
        raise VideoDownloadError('search', f"API {payload.get('code')}: {payload.get('message', '')}")
    if not isinstance(payload.get('data'), dict):
        raise VideoDownloadError('search', 'API returned a challenge or missing data')
    return payload['data']


def _global_rows(session, keyword, order, cancel_check, page=1, date_range=None):
    params = {'search_type': 'video', 'keyword': keyword, 'page': page,
              'order': 'totalrank' if order == 'relevance' else 'pubdate'}
    if date_range is not None:
        params.update(pubtime_begin_s=date_range[0], pubtime_end_s=date_range[1]-1)
    data = _api_payload(session, 'https://api.bilibili.com/x/web-interface/search/type',
                        cancel_check, params=params)
    rows = data.get('result')
    # Bilibili omits result entirely for an explicit zero-result response.
    if (rows is None and type(data.get('numResults')) is int and data['numResults'] == 0
            and type(data.get('numPages')) is int and data['numPages'] == 0 and not data.get('v_voucher')):
        rows = []
    if not isinstance(rows, list):
        raise VideoDownloadError('search', 'API returned missing result data')
    if rows and not _search_items(rows):
        raise VideoDownloadError('search', 'API returned no valid video records')
    return rows


def _wbi_params(params, images):
    keys = []
    for name in ('img_url', 'sub_url'):
        value = images.get(name, '')
        key = Path(urlsplit(value).path).stem if isinstance(value, str) else ''
        if not re.fullmatch(r'[a-fA-F0-9]{32}', key):
            raise VideoDownloadError('up_search', 'Missing WBI signing keys')
        keys.append(key)
    indices = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,
               29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
               22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)
    mixed = ''.join((keys[0]+keys[1])[index] for index in indices)[:32]
    signed = {key: re.sub(r"[!'()*]", '', str(value))
              for key, value in sorted(dict(params, wts=int(time.time())).items())}
    signed['w_rid'] = hashlib.md5((urlencode(signed)+mixed).encode()).hexdigest()
    return signed


def _up_search(session, keyword, up_ids, order, cancel_check, date_range=None):
    items, failures, coverage, successful = [], [], [], 0
    try:
        nav = json.loads(_get(session, 'https://api.bilibili.com/x/web-interface/nav', cancel_check))
        images = nav.get('data', {}).get('wbi_img', {})
        if not isinstance(images, dict):
            raise ValueError('Missing WBI signing keys')
    except (requests.RequestException, ValueError, AttributeError) as error:
        images = {}
        failures.append(safe_error(error))
    failed_ids = []
    for uid in up_ids:
        try:
            matches = []
            for page in range(1, 6 if date_range is not None else 2):
                params = _wbi_params(dict(mid=uid, keyword=keyword, pn=page, ps=30, order='pubdate'), images)
                data = _api_payload(session, 'https://api.bilibili.com/x/space/wbi/arc/search',
                                    cancel_check, params=params)
                rows = data.get('list', {}).get('vlist')
                if not isinstance(rows, list):
                    raise VideoDownloadError('up_search', 'Missing UP video list')
                mapped = [dict(row, mid=row.get('mid', uid), pubdate=row.get('created', ''),
                               duration=row.get('length', '')) for row in rows if isinstance(row, dict)]
                videos = _search_items(mapped, limit=30 if date_range is not None else 20)
                if rows and not videos or any(v.author_id != uid for v in videos):
                    raise VideoDownloadError('up_search', 'Invalid UP video records')
                matches.extend(_date_items(videos, date_range))
                stamps = [video_timestamp(v) for v in videos]
                if (date_range is None or len(matches) >= 20 or len(rows) < 30
                        or stamps and all(stamps) and max(stamps) < date_range[0]):
                    break
            else:
                coverage.append(f'UID {uid}: date scan reached the 5-page limit')
            items.extend(matches[:20])
            successful += 1
        except (requests.RequestException, ValueError, AttributeError) as error:
            failed_ids.append(uid)
            failures.append(f'UID {uid}: {safe_error(error)}')
    if failed_ids:
        # shortcut: UP fallback covers three public pages; expand if real coverage is insufficient.
        public_success = False
        try:
            for page in range(1, 4):
                rows = _global_rows(session, keyword, order, cancel_check, page, date_range)
                public_success = True
                items.extend(_date_items(_search_items([r for r in rows if isinstance(r, dict)
                                            and str(r.get('mid', '')) in failed_ids]), date_range))
                if not rows:
                    break
        except (requests.RequestException, ValueError) as error:
            failures.append('Public search: '+safe_error(error))
        if not successful and not items and (date_range is None or not public_success):
            raise VideoDownloadError('up_search', '; '.join(failures))
    unique = {item.id: item for item in items}
    return VideoSearchResults(sort_search_items(list(unique.values()), order), 'up',
                              '; '.join((failures if failed_ids else [])+coverage))


def search_videos(keyword: str, cancel_check: CancelCheck = no_cancel, cookies=None,
                  up_ids=(), order='asc', date_range=None) -> VideoSearchResults:
    keyword = keyword.strip()
    if not keyword or len(keyword) > 200:
        raise VideoDownloadError('keyword')
    if order not in ('asc', 'desc', 'relevance'):
        raise VideoDownloadError('operation', 'Invalid search order')
    up_ids = parse_up_ids(' '.join(up_ids))
    if date_range is not None:
        _validate_date_range(date_range)
    with _anonymous_session(cookies) as session:
        try:
            _get(session, 'https://www.bilibili.com/', cancel_check)
        except (requests.RequestException, ValueError):
            pass  # The API can still work without visitor cookies.
        if up_ids:
            return _up_search(session, keyword, up_ids, order, cancel_check, date_range)
        try:
            items, warning = [], ''
            # shortcut: five date-filtered pages; expand if mission searches need more coverage.
            for page in range(1, 6 if date_range is not None else 2):
                rows = _global_rows(session, keyword, order, cancel_check, page, date_range)
                items.extend(_date_items(_search_items(rows), date_range))
                if date_range is None or len(rows) < 20:
                    break
            else:
                warning = 'Date search reached the 5-page limit'
            unique = {item.id: item for item in items}
            return VideoSearchResults(sort_search_items(list(unique.values()), order), warning=warning)
        except (requests.RequestException, ValueError, AttributeError) as error:
            api_error = safe_error(error)
        try:
            items = extract_search_page(_get(session, search_page_url(keyword, order, date_range), cancel_check))
            if not items:
                raise VideoDownloadError('search', 'Search page has no readable video cards '
                                         '(dynamic page, verification or no results)')
            return VideoSearchResults(sort_search_items(_date_items(items, date_range), order), 'page',
                'Search-page dates can be incomplete' if date_range is not None else '')
        except (requests.RequestException, ValueError) as error:
            raise VideoDownloadError('search', f'{api_error}; page: {safe_error(error)}') from None


def backend_command() -> list[str]:
    """Keep yt-dlp optional. Frozen builds use a separately updatable executable."""
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).parent/'yt-dlp.exe'
        if bundled.is_file():
            return [str(bundled)]
    elif importlib.util.find_spec('yt_dlp') is not None:
        return [sys.executable, '-m', 'yt_dlp']
    executable = shutil.which('yt-dlp')
    if executable:
        return [executable]
    raise VideoDownloadError('dependency')


class _WindowsJob:
    """Own the child tree through a Windows handle, including in restricted jobs."""
    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if self.handle and not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.api.CloseHandle(self.handle)
            self.handle = None

    def terminate(self) -> None:
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self.terminate()
            self.api.CloseHandle(self.handle)
            self.handle = None


def _stop_process(process: subprocess.Popen, job=None) -> None:
    if process.poll() is not None:
        return
    try:
        if job is not None and job.handle:
            job.terminate()
        elif os.name == 'nt':
            # Kill FFmpeg children as well, so cancellation releases open media files.
            subprocess.run([str(Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/taskkill.exe'),
                '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _run_process(command: list[str], cancel_check: CancelCheck,
                 on_line: Callable[[str], None], timeout: float | None = None,
                 input_text: str | None = None, secret_values=()) -> None:
    cancel_check()
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        text=True, encoding='utf-8', errors='replace', **options)
    job = _WindowsJob(process) if os.name == 'nt' else None
    lines: Queue = Queue()
    errors = deque(maxlen=8)

    def read_output() -> None:
        try:
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    def write_input() -> None:
        try:
            process.stdin.write(input_text)
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    writer = Thread(target=write_input, daemon=True) if input_text is not None else None
    reader = Thread(target=read_output, daemon=True)
    reader.start()
    if writer is not None:
        writer.start()
    started = time.monotonic()
    try:
        while True:
            cancel_check()
            if timeout and time.monotonic()-started > timeout:
                raise VideoDownloadError('timeout')
            try:
                line = lines.get(timeout=.1)
            except Empty:
                continue
            if line is None:
                break
            if not line.startswith(('SW_PROGRESS:', 'SW_FILE:', '{')):
                message = line
                for secret in secret_values:
                    message = message.replace(secret, '[redacted]')
                errors.append(safe_error(message))
            on_line(line)
        while process.poll() is None:
            cancel_check()
            if timeout and time.monotonic()-started > timeout:
                raise VideoDownloadError('timeout')
            time.sleep(.05)
        cancel_check()
        if process.returncode:
            raise VideoDownloadError('backend', '\n'.join(errors) or f'Exit {process.returncode}')
    finally:
        try:
            _stop_process(process, job)
        finally:
            reader.join(timeout=2)
            if writer is not None:
                writer.join(timeout=2)
            process.stdout.close()
            if job is not None:
                job.close()


def _common_args() -> list[str]:
    return ['--ignore-config', '--no-playlist', '--no-cache-dir', '--no-colors',
            '--encoding', 'utf-8', '--user-agent', HEADERS['User-Agent'],
            '--referer', HEADERS['Referer'],
            '--socket-timeout', '12', '--retries', '1', '--fragment-retries', '1']


def _check_saving_allowed(url: str, cancel_check: CancelCheck, cookies=None) -> None:
    identifier = urlsplit(url).path.split('/')[-1]
    query = {'bvid': identifier} if identifier.startswith('BV') else {'aid': identifier[2:]}
    with _anonymous_session(cookies) as session:
        payload = json.loads(_get(session, 'https://api.bilibili.com/x/web-interface/view',
                                  cancel_check, params=query))
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get('code') != 0 or not isinstance(data, dict):
        raise VideoDownloadError('permission', 'Unable to confirm platform saving permission')
    rights = data.get('rights') or {}
    if (not isinstance(rights, dict) or rights.get('download') != 1
            or any(rights.get(key) for key in ('pay', 'ugc_pay', 'ugc_pay_preview', 'arc_pay'))):
        raise VideoDownloadError('permission', 'Platform saving is disabled or the video is paid/preview content')


def _formats(rows: list) -> list[VideoFormat]:
    playable = [row for row in rows if isinstance(row, dict) and not row.get('has_drm')
                and re.fullmatch(r'[A-Za-z0-9_.-]+', str(row.get('format_id', '')))
                and row.get('url')]
    audio = [row for row in playable if row.get('vcodec') == 'none'
             and row.get('acodec') not in ('none', None) and row.get('ext') == 'm4a']
    best_audio = max(audio, key=lambda row: row.get('abr') or row.get('tbr') or 0, default=None)
    result = []
    for row in playable:
        if row.get('vcodec') in ('none', None) or row.get('ext') != 'mp4':
            continue
        merge = row.get('acodec') == 'none' and best_audio is not None
        selector = str(row['format_id'])+('+'+str(best_audio['format_id']) if merge else '')
        result.append(VideoFormat(selector, int(row.get('height') or 0), str(row['vcodec']), merge))
    return sorted(result, key=lambda fmt: (fmt.height, fmt.codec.startswith('avc')), reverse=True)


def preferred_format(formats: list[VideoFormat]) -> VideoFormat | None:
    ffmpeg = shutil.which('ffmpeg')
    return max((fmt for fmt in formats if not fmt.requires_merge or ffmpeg),
               key=lambda fmt: (fmt.height, fmt.codec.startswith(('avc', 'h264'))),
               default=None)


def _auth_options(cookies):
    if not cookies:
        return [], {}
    values = []
    for cookie in cookies:
        if (cookie.domain.lstrip('.') != 'bilibili.com'
                or not re.fullmatch(r'[A-Za-z0-9_]+', cookie.name)
                or not cookie.value or len(cookie.value) > 4096
                or re.search(r'[;\x00-\x20\x7f]', cookie.value)):
            raise VideoDownloadError('login')
        values.append(cookie.name+'='+cookie.value)
    # stdin avoids exposing credentials in process arguments or a cookie file.
    config = '--add-header '+shlex.quote('Cookie: '+'; '.join(values))+'\n'
    return ['--config-locations', '-'], dict(input_text=config,
        secret_values=tuple(cookie.value for cookie in cookies))


def _backend_url(url, cookies):
    # yt-dlp scopes header cookies to its input host; the root scopes them to Bili APIs too.
    return url.replace('https://www.bilibili.com/', 'https://bilibili.com/') if cookies else url


def parse_video(url: str, cancel_check: CancelCheck = no_cancel, cookies=None) -> VideoInfo:
    url = resolve_video_url(url, cancel_check)
    payloads = []

    def collect(line: str) -> None:
        if line.startswith('{'):
            payloads.append(json.loads(line))

    auth_args, auth_input = _auth_options(cookies)
    _run_process(backend_command()+_common_args()+auth_args+
                 ['--dump-single-json', '--skip-download', _backend_url(url, cookies)],
                 cancel_check, collect, timeout=90, **auth_input)
    if len(payloads) != 1 or not isinstance(payloads[0], dict):
        raise VideoDownloadError('backend', 'Missing video metadata')
    data = payloads[0]
    _check_saving_allowed(url, cancel_check, cookies)
    if data.get('_type', 'video') != 'video' or data.get('is_live') or data.get('has_drm'):
        raise VideoDownloadError('unavailable')
    formats = _formats(data.get('formats') or [])
    if not formats:
        raise VideoDownloadError('unavailable')
    stamp = data.get('timestamp')
    stamp = stamp if isinstance(stamp, (int, float)) and 0 < stamp < 32503680000 else 0
    published = datetime.fromtimestamp(stamp, VIDEO_TIMEZONE).strftime('%Y-%m-%d') if stamp else str(data.get('upload_date') or '')
    return VideoInfo(str(data.get('id', '')), str(data.get('title') or data.get('id', '')),
        url, str(data.get('uploader') or ''), str(data.get('duration') or ''),
        thumbnail_url(str(data.get('thumbnail') or '')), published, formats=formats,
        author_id=str(data.get('uploader_id') or ''), published_timestamp=stamp)


def safe_filename(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:160].rstrip(' .')
    if not name or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name):
        name = 'video_'+name
    return name


def _save_metadata(directory: Path, data: dict) -> None:
    temporary = directory/'metadata.tmp'
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(directory/'metadata.json')


def _verify_file(path: Path, directory: Path, cancel_check: CancelCheck) -> None:
    if (not path.resolve().is_relative_to(directory.resolve()) or path.suffix.lower() != '.mp4'
            or not path.is_file() or path.stat().st_size < 32):
        raise VideoDownloadError('output')
    with path.open('rb') as stream:
        if b'ftyp' not in stream.read(4096):
            raise VideoDownloadError('output')
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        output = []
        _run_process([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
            'stream=codec_type', '-of', 'csv=p=0', str(path)], cancel_check, output.append, timeout=30)
        if 'video' not in output:
            raise VideoDownloadError('output')


def _move_no_replace(source: Path, target: Path) -> None:
    # Windows rename fails on existing targets; hard links provide the same guarantee elsewhere.
    if os.name == 'nt':
        source.rename(target)
    else:
        os.link(source, target)
        source.unlink()


def _publish_video(source: Path, root: Path, title: str) -> Path:
    name = safe_filename(title)
    index = 1
    while True:
        target = root/(name+('' if index == 1 else f' ({index})')+'.mp4')
        try:
            _move_no_replace(source, target)
            return target.resolve()
        except FileExistsError:
            index += 1


def _download_root(workspace: Path) -> Path:
    workspace = Path(workspace).resolve()
    root = workspace/'video-downloads'
    if not root.resolve().is_relative_to(workspace):
        raise VideoDownloadError('path')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _new_download_directory(root: Path) -> Path:
    name = datetime.now(VIDEO_TIMEZONE).strftime('%Y-%m-%d')
    index = 1
    while True:
        directory = root/(name+('' if index == 1 else f' ({index})'))
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            index += 1


def _completed_download(root: Path, url: str, selected: VideoFormat, cancel_check, preferred_directory=None):
    fallback = None
    for record in root.glob('*/metadata.json'):
        cancel_check()
        if not record.resolve().is_relative_to(root.resolve()):
            continue
        try:
            if record.stat().st_size > 65536:
                continue
            data = json.loads(record.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('status') != 'completed'
                    or data.get('original_url') != url or data.get('format_id') != selected.selector):
                continue
            path = Path(data['file_path']) if data.get('file_path') else record.parent/'media.mp4'
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                continue
            _verify_file(path, root, cancel_check)
            if preferred_directory is None or path.resolve().parent == preferred_directory:
                return path.resolve()
            fallback = fallback or path.resolve()
        except (OSError, ValueError, TypeError):
            continue
    return fallback


def download_video(info: VideoInfo, selected: VideoFormat, workspace: Path,
                   cancel_check: CancelCheck = no_cancel,
                   on_progress: Callable[[DownloadProgress], None] = lambda event: None,
                   cookies=None, output_directory: Path | None = None) -> Path:
    url = normalize_video_url(info.url)
    if selected not in info.formats or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\+[A-Za-z0-9_.-]+)?', selected.selector):
        raise VideoDownloadError('format')
    auth_args, auth_input = _auth_options(cookies)
    command = backend_command()+_common_args()+auth_args
    _check_saving_allowed(url, cancel_check, cookies)
    ffmpeg = shutil.which('ffmpeg')
    if selected.requires_merge and not ffmpeg:
        raise VideoDownloadError('ffmpeg')
    root = _download_root(workspace)
    cancel_check()
    destination = Path(output_directory).resolve() if output_directory is not None else None
    if destination is not None and (not destination.is_relative_to(root.resolve()) or not destination.is_dir()):
        raise VideoDownloadError('path')
    existing = _completed_download(root, url, selected, cancel_check, destination)
    if existing is not None and ((destination is None and existing.name != 'media.mp4')
                                 or existing.parent == destination):
        on_progress(DownloadProgress('completed', 100))
        return existing
    destination = destination or _new_download_directory(root)
    directory = root/('job-'+uuid4().hex[:12])
    directory.mkdir(exist_ok=False)
    metadata = dict(title=info.title, original_url=url, platform=info.platform,
        video_id=info.id, format=selected.ext, format_id=selected.selector, file_path=None,
        started_at=datetime.now().astimezone().isoformat(), downloaded_at=None, status='downloading')
    _save_metadata(directory, metadata)
    files = []
    published = None
    finished_streams = set()

    def receive(line: str) -> None:
        if line.startswith('SW_FILE:'):
            files.append(Path(json.loads(line[len('SW_FILE:'):])))
        elif line.startswith('SW_PROGRESS:'):
            progress = json.loads(line[len('SW_PROGRESS:'):])
            total = progress.get('total_bytes') or progress.get('total_bytes_estimate')
            percent = min(99, 100*(progress.get('downloaded_bytes') or 0)/total) if total else None
            on_progress(DownloadProgress('downloading', percent, progress.get('speed'), progress.get('eta')))
            if selected.requires_merge and progress.get('status') == 'finished':
                finished_streams.add(progress.get('filename'))
                if len(finished_streams) >= 2:
                    on_progress(DownloadProgress('merging'))
        elif line.startswith('[Merger]'):
            on_progress(DownloadProgress('merging'))

    command += ['--no-simulate', '--no-overwrites', '--no-continue', '--newline', '--progress',
        '--progress-delta', '0.25', '--progress-template', 'download:SW_PROGRESS:%(progress)j',
        '--print', 'after_move:SW_FILE:%(filepath)j', '-f', selected.selector,
        '--merge-output-format', 'mp4', '-o', str(directory/'media.%(ext)s')]
    if ffmpeg:
        command += ['--ffmpeg-location', str(Path(ffmpeg).parent)]
    try:
        if existing is None:
            _run_process(command+[_backend_url(url, cookies)], cancel_check, receive, **auth_input)
        else:
            target = directory/'media.mp4'
            with existing.open('rb') as source, target.open('xb') as output:
                while chunk := source.read(1024*1024):
                    cancel_check()
                    output.write(chunk)
            files.append(target)
        cancel_check()
        if len(files) != 1:
            raise VideoDownloadError('output')
        on_progress(DownloadProgress('verifying'))
        _verify_file(files[0], directory, cancel_check)
        cancel_check()
        published = _publish_video(files[0], destination, info.title)
        metadata.update(status='completed', file_path=str(published),
                        downloaded_at=datetime.now().astimezone().isoformat())
        _save_metadata(directory, metadata)
    except Exception as error:
        metadata.update(status='cancelled' if isinstance(error, TaskCancelledError) else 'failed',
                        error=safe_error(error))
        try:
            _save_metadata(directory, metadata)
        except OSError as metadata_error:
            logger.warning('Cannot write video download status: %s', safe_error(metadata_error))
        finally:
            if published is not None:
                published.unlink(missing_ok=True)
            # Clean this job even when a full disk prevents saving its failure record.
            for partial in directory.glob('media*'):
                if partial.is_file() and partial.resolve().is_relative_to(directory.resolve()):
                    partial.unlink()
        raise
    logger.info('Video download completed: %s', directory.name)
    on_progress(DownloadProgress('completed', 100))
    return published


def demo_download(keyword, date_range, workspace, up_ids=(), order='asc', cookies=None,
                  cancel_check=no_cancel, on_progress=lambda event: None,
                  on_search=lambda value: None, on_video=lambda value: None,
                  output_directory: Path | None = None):
    """Try at most three dated candidates; save one permitted MP4 through the normal backend."""
    _validate_date_range(date_range)
    cancel_check()
    on_progress(DownloadProgress('searching'))
    results = search_videos(keyword, cancel_check, cookies, up_ids, order, date_range)
    on_search(results)
    if not results.items:
        raise VideoDownloadError('date_no_match', results.warning)
    errors = []
    for candidate in results.items[:3]:
        cancel_check()
        on_progress(DownloadProgress('parsing'))
        try:
            info = parse_video(candidate.url, cancel_check, cookies)
            if not _date_items([info], date_range):
                raise VideoDownloadError('date_no_match', 'Parsed publication date does not match')
            if up_ids and info.author_id and info.author_id not in up_ids:
                raise VideoDownloadError('up_missing', 'Parsed uploader does not match')
            selected = preferred_format(info.formats)
            if selected is None:
                raise VideoDownloadError('ffmpeg')
        except VideoDownloadError as error:
            if error.code == 'dependency':
                raise
            errors.append(f'{candidate.title[:80]}: {error.code}: {error.detail}')
            continue
        cancel_check()
        on_video((info, selected))
        return download_video(info, selected, workspace, cancel_check, on_progress, cookies,
                              output_directory=output_directory)
    raise VideoDownloadError('demo_candidates', '; '.join(errors))


def demo_download_tasks(items, workspace, up_ids=(), order='asc', cookies=None,
                        cancel_check=no_cancel, on_progress=lambda event: None,
                        on_search=lambda value: None, on_video=lambda value: None,
                        on_task_started=lambda value: None, on_task_finished=lambda value: None):
    """Run one dated download per launch; retain completed jobs when another fails or is cancelled."""
    tasks = [item for item in items if item.get('video_type') == 'launch']
    if not tasks:
        raise VideoDownloadError('demo_task')
    if order != 'relevance':
        def date_key(item):
            try:
                stamp = mission_date_range(item)[0]
                return (False, -stamp if order == 'desc' else stamp)
            except VideoDownloadError:
                return (True, 0)
        tasks.sort(key=date_key)
    cancel_check()
    destination = _new_download_directory(_download_root(workspace))
    outcomes = []
    for index, item in enumerate(tasks, 1):
        cancel_check()
        keyword = str(item.get('summary') or item.get('title') or '').strip()
        on_task_started((index, len(tasks), item, keyword, destination))
        outcome = dict(index=index, total=len(tasks), keyword=keyword, directory=destination, path=None, error=None)
        try:
            window = mission_date_range(item)
            outcome['path'] = demo_download(keyword, window, workspace, up_ids, order, cookies,
                                           cancel_check, on_progress, on_search, on_video, output_directory=destination)
        except TaskCancelledError:
            raise
        except Exception as error:
            outcome['error'] = error if isinstance(error, VideoDownloadError) else VideoDownloadError('operation', str(error))
        outcomes.append(outcome)
        on_task_finished(outcome)
    return outcomes
