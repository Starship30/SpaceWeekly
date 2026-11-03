"""Conservative, file-only cleanup of known regenerable workspace caches."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
from threading import RLock
import time
import math

from resources import resource_path,workspace_path
from services.launch_data import write_json

LOCK=RLock()
ACTIVE=0
DAY=86400
LABELS={'preview':'临时音频预览','thumbnail':'图片缩略图','network':'可重新获取的网页副本','temporary':'下载临时文件'}
DEFAULTS=dict(version=1,enabled=True,interval_days=3,retention_days=3,last_check=0,last_reminder=0,last_clean=0,snooze_until=0,files={})


def load():
    path=resource_path('config','cache-maintenance.json')
    value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(value,dict) or not isinstance(value.get('files',{}),dict):raise ValueError('缓存维护记录无效，未覆盖原文件')
    data=dict(DEFAULTS,**value);data['files']=dict(value.get('files',{}))
    if data['version']!=1:raise ValueError('不支持的缓存维护记录版本')
    if type(data['enabled']) is not bool or any(type(data[k]) is not int or not 1<=data[k]<=365 for k in ('interval_days','retention_days')):
        raise ValueError('缓存维护设置无效')
    if any(not isinstance(data[k],(int,float)) or not math.isfinite(data[k]) or data[k]<0 for k in ('last_check','last_reminder','last_clean','snooze_until')):
        raise ValueError('缓存维护时间记录无效')
    if any(not isinstance(v,dict) or any(k not in ('last_used','first_seen') or not isinstance(t,(int,float)) or not math.isfinite(t) or t<0 for k,t in v.items()) for v in data['files'].values()):
        raise ValueError('缓存使用记录无效')
    last=data.get('last_result')
    if last is not None and (not isinstance(last,dict) or any(type(last.get(k)) is not int or last[k]<0 for k in ('deleted','bytes'))
            or not isinstance(last.get('failed'),list) or any(not isinstance(r,dict) or any(not isinstance(r.get(k),str) for k in ('path','reason')) for r in last['failed'])):
        raise ValueError('缓存清理结果记录无效')
    return data


def update(**values):
    with LOCK:
        data=load();data.update(values);write_json(resource_path('config','cache-maintenance.json'),data)
    return data


def root():return Path(workspace_path() or resource_path()).resolve()


def category(relative):
    parts=Path(relative).parts
    if len(parts)!=3 or parts[0]!='cache':return None
    directory,name=parts[1:]
    if directory in ('news_thumbnails',) and re.fullmatch(r'[a-f0-9]{64}\.png',name):return 'thumbnail'
    if directory=='news_http' and re.fullmatch(r'[a-f0-9]{64}\.html',name):return 'network'
    if directory=='news_image_downloads' and re.fullmatch(r'[a-f0-9]{32}\.part',name):return 'temporary'
    return None


def managed(path):
    path=Path(path);base=root()
    try:
        relative=path.absolute().relative_to(base)
        # Reject reparse/symlink paths, including directory junctions escaping the workspace.
        if path.resolve()!=path.absolute() or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents) if p!=base):return None
    except (ValueError,OSError):return None
    parts=relative.parts
    if (len(parts)==4 and parts[:1]==('cache',) and parts[1] in ('roundup_pcm','news_pcm','roundup_playback','news_playback')
            and re.fullmatch(r'[a-f0-9]{64}',parts[2]) and parts[3] in ('audio.wav','audio.json','narration.wav','narration.json')):
        return 'preview'
    return category(relative)


def touch(path,now=None):
    """Only consumers/producers call this; scanning never refreshes actual usage."""
    path=Path(path)
    if not managed(path):return
    try:
        with LOCK:
            data=load();stamp=time.time() if now is None else now
            data['files'][str(path.resolve().relative_to(root()))]=dict(last_used=stamp)
            if path.suffix=='.wav' and path.with_suffix('.json').is_file():
                data['files'][str(path.with_suffix('.json').resolve().relative_to(root()))]=dict(last_used=stamp)
            write_json(resource_path('config','cache-maintenance.json'),data)
    except (OSError,ValueError):
        # Maintenance metadata must never make a valid voice or preview fail.
        import logging
        logging.getLogger(__name__).warning('缓存使用记录未保存；清理时会保守处理')


@contextmanager
def using_media():
    global ACTIVE
    with LOCK:ACTIVE+=1
    try:yield
    finally:
        with LOCK:ACTIVE-=1


def references(extra=()):
    """Protect saved drafts, histories and packages; unknown/unreadable references fail closed."""
    base=root();paths=set();hashes=set();uncertain=False
    def visit(value):
        if isinstance(value,dict):
            for v in value.values():visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
        elif isinstance(value,str):
            if re.fullmatch(r'[a-f0-9]{64}',value):hashes.add(value)
            try:
                p=Path(value)
                if p.is_absolute():paths.add(p.resolve())
                elif value.startswith('cache/') or value.startswith('cache\\'):paths.add((base/p).resolve())
            except (OSError,ValueError):pass
    files=list((base/'config').glob('**/*.json'))+list((base/'output').glob('**/*.json'))+[base/'cache/production-recovery.json']
    for path in files:
        if path.name=='cache-maintenance.json':continue
        try:
            if path.stat().st_size>16*1024*1024:uncertain=True;continue
            visit(json.loads(path.read_text(encoding='utf-8')))
        except FileNotFoundError:continue
        except (OSError,ValueError):uncertain=True
    visit(extra)
    return paths,hashes,uncertain


def protected(path,refs,active=False):
    paths,hashes,uncertain=refs
    if active or ACTIVE or uncertain:return True
    if path.resolve() in paths or path.parent.name in hashes:return True
    if path.suffix=='.json':return protected(path.with_suffix('.wav'),refs,active)
    sidecar=path.with_suffix('.json')
    if sidecar.exists():
        try:
            data=json.loads(sidecar.read_text(encoding='utf-8'))
            if set(data.get('source_hashes',[])) & hashes:return True
        except (ValueError,OSError):return True
    return False


def scan(token,*,now=None,extra=(),active=False,record_check=True):
    now=time.time() if now is None else now;base=root();rows=[];refs=references(extra)
    with LOCK:data=load()
    discovered={}
    for directory in ('news_thumbnails','news_http','news_image_downloads','roundup_pcm','news_pcm','roundup_playback','news_playback'):
        folder=base/'cache'/directory
        for path in folder.glob('*/*' if directory.endswith(('_pcm','_playback')) else '*'):
            token.check();kind=managed(path)
            if not kind or not path.is_file():continue
            try:info=path.stat()
            except FileNotFoundError:continue
            relative=str(path.relative_to(base));record=data['files'].get(relative)
            if not record:
                # Legacy files have unknown use history: observe once, then wait a full retention period.
                record=dict(first_seen=now);discovered[relative]=record
            used=max(record.get('last_used',record.get('first_seen',now)),info.st_mtime)
            if now-used<data['retention_days']*DAY or protected(path,refs,active):continue
            rows.append(dict(path=relative,category=kind,size=info.st_size,mtime_ns=info.st_mtime_ns,last_used=used))
    with LOCK:
        current=load()
        for path,value in discovered.items():current['files'].setdefault(path,value)
        if record_check:current['last_check']=now;current['snooze_until']=0
        if record_check or discovered:write_json(resource_path('config','cache-maintenance.json'),current)
    return dict(files=rows,count=len(rows),bytes=sum(r['size'] for r in rows),reference_warning=refs[2])


def due(now=None):
    now=time.time() if now is None else now;data=load()
    return data['enabled'] and (now>=data['snooze_until'] if data['snooze_until'] else
        now-max(data['last_check'],data['last_reminder'])>=data['interval_days']*DAY)


def reminded(now=None):return update(last_reminder=time.time() if now is None else now,snooze_until=0)


def snooze(now=None):
    now=time.time() if now is None else now
    # A later reminder is tomorrow; preserve the independent normal check interval.
    return update(snooze_until=now+DAY)


def clean(rows,token,*,now=None,extra=(),active=lambda:False):
    now=time.time() if now is None else now;result=dict(deleted=0,bytes=0,failed=[],cancelled=False)
    from services.cancellation import TaskCancelledError
    for row in rows:
        try:token.check()
        except TaskCancelledError:result['cancelled']=True;break
        path=root()/row['path']
        try:
            refs=references(extra)
            with LOCK:
                data=load()
                if not managed(path) or protected(path,refs,active()):raise ValueError('文件受保护或有任务正在使用')
                info=path.stat();record=data['files'].get(str(path.relative_to(root())),{})
                used=max(record.get('last_used',record.get('first_seen',now)),info.st_mtime)
                if info.st_size!=row['size'] or info.st_mtime_ns!=row['mtime_ns'] or now-used<data['retention_days']*DAY:
                    raise ValueError('文件已变化或近期使用，请重新检查')
                path.unlink();result['deleted']+=1;result['bytes']+=info.st_size
                data['files'].pop(str(path.relative_to(root())),None);data['last_clean']=now
                write_json(resource_path('config','cache-maintenance.json'),data)
        except (OSError,ValueError,KeyError) as error:result['failed'].append(dict(path=row['path'],reason=str(error)))
    update(last_clean=now,last_result=result)
    return result
