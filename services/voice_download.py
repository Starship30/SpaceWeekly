"""Resumable, hash-verified deployment downloads with real byte progress."""
import hashlib
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import requests
from services.cancellation import TaskCancelledError


def sha256(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda:handle.read(1024*1024),b''):checksum.update(data)
    return checksum.hexdigest()


def verified(path,size,checksum):
    return path.is_file() and path.stat().st_size==size and sha256(path)==checksum


def redact(text):
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[凭据已隐藏]@',str(text))
    text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[参数已隐藏]',text)
    text=re.sub(r'''(?i)(["'](?:authorization|password|token|api[_-]?key|secret)["']\s*:\s*)(["']).*?\2''',r'\1"[已隐藏]"',text)
    text=re.sub(r'(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+',r'\1[已隐藏]',text)
    return re.sub(r'(?i)(authorization|password|token|api[_-]?key|secret)\s*[:=]\s*\S+',r'\1=[已隐藏]',text)


def download(url,target,size,checksum,token,on_bytes=None,*,session=None):
    """Partial files are never usable models; even resumed/cross-source bytes must pass the pinned hash."""
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if verified(target,size,checksum):
        if on_bytes:on_bytes(size,size)
        return target
    if urlsplit(url).scheme!='https':raise ValueError('部署下载只允许 HTTPS 官方来源')
    partial=target.with_suffix(target.suffix+'.part')
    if partial.exists() and partial.stat().st_size>size:partial.unlink()
    own=session is None;session=session or requests.Session()
    try:
        for attempt in range(3):
            token.check();offset=partial.stat().st_size if partial.exists() else 0
            if offset==size:
                if sha256(partial)==checksum:partial.replace(target);return target
                partial.unlink();offset=0
            if shutil.disk_usage(target.parent).free<size-offset+64*1024**2:
                raise ValueError('剩余空间不足，未继续下载；已有缓存保留')
            try:
                headers={'Accept-Encoding':'identity','User-Agent':'SpaceWeekly-VoiceSetup/1'}
                if offset:headers['Range']=f'bytes={offset}-'
                with session.get(url,headers=headers,stream=True,timeout=(15,30)) as response:
                    response.raise_for_status()
                    if response.status_code==206:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if not match or int(match[1])!=offset or not offset<=int(match[2])<size or int(match[3])!=size:
                            raise ValueError('下载源的续传范围与固定文件大小不一致')
                    elif response.status_code==200:offset=0
                    else:raise ValueError('下载源未返回有效文件')
                    if on_bytes:on_bytes(offset,size)
                    with partial.open('ab' if offset else 'wb') as handle:
                        for chunk in response.iter_content(1024*256):
                            token.check()
                            if not chunk:continue
                            handle.write(chunk);offset+=len(chunk)
                            if offset>size:raise ValueError('下载文件超过固定快照大小')
                            if on_bytes:on_bytes(offset,size)
                token.check()
                if not verified(partial,size,checksum):
                    if partial.exists() and partial.stat().st_size>=size:partial.unlink()
                    raise ValueError('文件完整性校验未通过，未标记为可用')
                partial.replace(target)
                return target
            except requests.RequestException as error:
                if attempt==2:raise RuntimeError('所选下载源连接失败，可重试或明确切换来源；缓存已保留（'+type(error).__name__+'：'+redact(error)+'）') from None
            except ValueError:
                if attempt==2:raise
            if token.wait(.5*(attempt+1)):token.check()
    finally:
        if own:session.close()


def link_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix(target.suffix+'.linking')
    temporary.unlink(missing_ok=True)
    try:
        try:os.link(source,temporary)
        except OSError:shutil.copyfile(source,temporary)
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
