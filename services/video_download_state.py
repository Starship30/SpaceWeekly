"""Workspace preferences and Windows user-encrypted Bilibili sessions."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

import requests

from services.video_download import VideoDownloadError, _auth_options


def state_path(workspace, name):
    workspace = Path(workspace).resolve()
    path = workspace/'video-downloads'/name
    if not path.resolve().is_relative_to(workspace):
        raise VideoDownloadError('path')
    return path


def _crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise VideoDownloadError('session_store', 'Persistent login requires Windows DPAPI')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # Current user scope; no machine-wide key and no credential prompt.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise VideoDownloadError('session_store', f'DPAPI error {ctypes.get_last_error()}')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    if not temporary.resolve().is_relative_to(path.parent.resolve()):
        raise VideoDownloadError('path')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        if temporary.is_file():
            temporary.unlink()


def save_session(workspace, cookies):
    _auth_options(cookies)
    if not cookies or not any(c.name == 'SESSDATA' for c in cookies):
        raise VideoDownloadError('login')
    rows = [dict(name=c.name, value=c.value, domain=c.domain, path=c.path,
                 secure=c.secure, expires=c.expires) for c in cookies]
    # Encryption happens before any file is created; plaintext is never written.
    encrypted = _crypt(json.dumps(rows).encode('utf-8'))
    _write(state_path(workspace, '.bilibili-session.bin'), encrypted)


def load_session(workspace):
    path = state_path(workspace, '.bilibili-session.bin')
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        rows = json.loads(_crypt(path.read_bytes(), decrypt=True))
        if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
            raise ValueError()
        jar = requests.cookies.RequestsCookieJar()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                    or not isinstance(row.get('value'), str) or not isinstance(row.get('domain'), str)
                    or row.get('path') != '/' or row.get('expires') is not None
                    and not isinstance(row['expires'], int)):
                raise ValueError()
            jar.set(row['name'], row['value'], domain=row['domain'], path='/',
                    secure=bool(row.get('secure')), expires=row.get('expires'))
        jar.clear_expired_cookies()
        _auth_options(jar)
        return jar if any(c.name == 'SESSDATA' for c in jar) else None
    except (ValueError, TypeError, KeyError, OSError):
        raise VideoDownloadError('session_store') from None


def clear_session(workspace):
    state_path(workspace, '.bilibili-session.bin').unlink(missing_ok=True)


def load_preferences(workspace):
    path = state_path(workspace, '.search-preferences.json')
    try:
        if path.stat().st_size > 4096:
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(workspace, up_ids, order):
    _write(state_path(workspace, '.search-preferences.json'),
           json.dumps(dict(up_ids=up_ids, order=order)).encode('utf-8'))
