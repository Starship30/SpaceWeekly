"""Explicit QR login and validation; credentials never enter diagnostic messages."""
from io import BytesIO
import json
import logging
import re
import time
from urllib.parse import urlsplit

import requests

from services.cancellation import CancellationToken
from services.video_download import VideoDownloadError, _anonymous_session, _get

PASSPORT = 'https://passport.bilibili.com/x/passport-login/web/qrcode/'


class _LoginLogFilter(logging.Filter):
    def filter(self, record):
        if '/passport-login/web/qrcode/' in record.getMessage():
            record.msg, record.args = 'Bilibili QR request (details redacted)', ()
        return True


logging.getLogger('urllib3.connectionpool').addFilter(_LoginLogFilter())


def _data(session, url, token, **kwargs):
    try:
        payload = json.loads(_get(session, url, token.check, allow_redirects=False, **kwargs))
        if not isinstance(payload, dict) or payload.get('code') != 0 or not isinstance(payload.get('data'), dict):
            raise VideoDownloadError('login')
        return payload['data']
    except (requests.RequestException, ValueError) as error:
        if isinstance(error, VideoDownloadError):
            raise
        # Poll responses and exception URLs may contain login keys; never expose them.
        raise VideoDownloadError('login', type(error).__name__) from None


def login_bilibili(token: CancellationToken, on_qr, on_status):
    try:
        import qrcode
        from qrcode.image.pil import PilImage
    except ImportError:
        raise VideoDownloadError('qr_dependency') from None
    with _anonymous_session() as session:
        try:
            data = _data(session, PASSPORT+'generate', token)
            url, key = data.get('url', ''), data.get('qrcode_key', '')
            if not isinstance(url, str) or not isinstance(key, str):
                raise VideoDownloadError('login')
            parts = urlsplit(url)
            official_url = (parts.netloc == 'passport.bilibili.com' and parts.path.startswith('/h5/qrcode/')
                or parts.netloc == 'account.bilibili.com' and parts.path == '/h5/account-h5/auth/scan-web')
            if (parts.scheme != 'https' or not official_url or len(url) > 4096
                    or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key)):
                raise VideoDownloadError('login')
            image = qrcode.make(url, image_factory=PilImage, box_size=5, border=4)
            with BytesIO() as output:
                image.save(output, format='PNG')
                on_qr(output.getvalue())
            deadline = time.monotonic()+180
            while time.monotonic() < deadline:
                token.check()
                data = _data(session, PASSPORT+'poll', token, params={'qrcode_key': key})
                code = data.get('code')
                if code == 0:
                    cookies = requests.cookies.RequestsCookieJar()
                    for cookie in session.cookies:
                        if cookie.domain.lstrip('.') == 'bilibili.com':
                            cookies.set_cookie(cookie)
                    if not any(c.name == 'SESSDATA' and c.value for c in cookies):
                        raise VideoDownloadError('login')
                    nav = _data(session, 'https://api.bilibili.com/x/web-interface/nav', token)
                    if nav.get('isLogin') is not True:
                        cookies.clear()
                        raise VideoDownloadError('login')
                    token.check()
                    return cookies
                if code == 86038:
                    raise VideoDownloadError('login_expired')
                if code not in (86101, 86090):
                    raise VideoDownloadError('login')
                on_status('login_scanned' if code == 86090 else 'login_waiting')
                token.wait(2)
            raise VideoDownloadError('login_expired')
        finally:
            session.cookies.clear()


def session_status(cookies, token):
    with _anonymous_session(cookies) as session:
        try:
            payload = json.loads(_get(session, 'https://api.bilibili.com/x/web-interface/nav', token.check))
            if not isinstance(payload, dict):
                raise ValueError()
            if payload.get('code') == -101:
                return False
            data = payload.get('data')
            if payload.get('code') != 0 or not isinstance(data, dict) or not isinstance(data.get('isLogin'), bool):
                raise ValueError()
            return data['isLogin']
        except (requests.RequestException, ValueError):
            # A network failure is not evidence that the stored session expired.
            return None
        finally:
            session.cookies.clear()
