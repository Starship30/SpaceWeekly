"""Inventory and file-only cleanup of known workspace products."""
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
LABELS={'preview':'临时预览与缩略音频','thumbnail':'图片缩略图','network':'可重新获取的网页副本','temporary':'下载与失败任务临时文件',
        'images':'新闻配图','downloads':'下载的视频素材','generated':'配音、字幕和画面素材','exports':'导出成果'}
FORMAL={'images','downloads','generated','exports'}
OUTPUT_NAME=r'SpaceWeekly_\d{4}-\d{2}-\d{2}_\d{3,}'
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
    if directory=='episode-previews' and re.fullmatch(OUTPUT_NAME+r'\.mp4',name):return 'preview'
    return None


def safe_relative(path):
    path=Path(path);base=root()
    try:
        relative=path.absolute().relative_to(base)
        # Reject reparse/symlink paths, including directory junctions escaping the workspace.
        if path.resolve()!=path.absolute() or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents) if p!=base):return None
    except (ValueError,OSError,RuntimeError):return None
    return relative


def managed(path,receipts=None):
    relative=safe_relative(path)
    if relative is None:return None
    parts=relative.parts
    if (len(parts)==4 and parts[:1]==('cache',) and parts[1] in ('roundup_pcm','news_pcm','roundup_playback','news_playback')
            and re.fullmatch(r'[a-f0-9]{64}',parts[2]) and (parts[3] in ('audio.wav','audio.json','narration.wav','narration.json','music-bed.wav','timeline.json','news-design.svg')
                 or re.fullmatch(r'timeline-[a-f0-9]{64}\.json',parts[3]))):
        return 'preview'
    if len(parts)==4 and parts[:2]==('cache','voice_samples') and re.fullmatch(r'[a-f0-9]{32}',parts[2]):
        if parts[3] in ('reference.wav','sample.wav') or re.fullmatch(r'original\.[a-z0-9]+|'+OUTPUT_NAME+r'\.wav',parts[3]):
            return 'preview'
    if len(parts)==4 and parts[:2]==('cache','episode-visuals') and re.fullmatch(r'[a-f0-9]{64}',parts[2]):
        if re.fullmatch(r'clip\.json|intro-audio\.wav|'+OUTPUT_NAME+r'(?:\.intro)?\.mp4',parts[3]):return 'preview'
    if len(parts)==4 and parts[0]=='cache' and re.fullmatch(r'(news|roundup)_(audio|alignment)',parts[1]) and re.fullmatch(r'[a-f0-9]{64}',parts[2]):
        if re.fullmatch(r'audio\.json|alignment\.json|'+OUTPUT_NAME+r'\.wav',parts[3]):return 'generated'
    if len(parts)==2 and parts[0]=='output' and re.fullmatch(OUTPUT_NAME+r'\.(?:premiere\.json|docx|md|mp4|mov|srt|wav)',parts[1]):
        if parts[1].endswith('.premiere.json'):
            try:
                candidate=root()/relative
                if candidate.stat().st_size>16*1024*1024:return None
                data=json.loads(candidate.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or data.get('protocol_version')!='1.0' or not isinstance(data.get('items'),list):return None
            except (OSError,ValueError):return None
        return 'exports'
    if len(parts)==3 and parts[:2] in (('output','video-intro'),('output','video-cards')) and re.fullmatch(OUTPUT_NAME+r'\.intro\.mp4',parts[2]):
        return 'generated'
    if len(parts)==2 and parts[0]=='output' and re.fullmatch(r'\.'+OUTPUT_NAME+r'(?:\.premiere)?\.[a-f0-9]{32}\.tmp\.(?:json|docx|md|mp4|mov|wav)',parts[1]):
        return 'temporary'
    if receipts and relative in receipts:return receipts[relative]
    return category(relative)


def receipts():
    base=root();owned={};image_refs=set();uncertain=False
    def read(path):
        if safe_relative(path) is None or path.stat().st_size>16*1024*1024:raise ValueError('无效清单路径')
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise ValueError('无效清单内容')
        return data
    def add(path,kind):
        relative=safe_relative(path)
        if relative is not None:owned[relative]=kind
    path=base/'news-images/manifest.json'
    try:
        images=read(path);linked={identifier for row in images['news'].values()
            for identifier in [row.get('selected'),*row.get('assets',[])]}
        for key,asset in images['images'].items():
            relative=Path(asset['path'])
            if len(relative.parts)==2 and relative.parts[0]=='news-images' and re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpe?g|webp|gif)',relative.name):
                add(base/relative,'images')
                if key in linked:image_refs.add((base/relative).resolve())
        videos={row['video']['sha256'] for row in images['news'].values() if row.get('video')}
        for key in images.get('videos',{}):
            if re.fullmatch(r'[a-f0-9]{64}',key):
                video=base/'news-images'/(key+'.mp4')
                add(video,'generated')
                if key in videos:image_refs.add(video.resolve())
    except FileNotFoundError:pass
    except (OSError,ValueError,TypeError,AttributeError,KeyError):uncertain=True
    for path in (base/'video-downloads').glob('*/metadata.json'):
        try:
            record=read(path)
            if not record.get('original_url'):continue
            if record.get('status')=='completed':
                video=Path(record.get('file_path') or path.parent/'media.mp4')
                if video.suffix.lower()=='.mp4' and video.parent in (base/'video-downloads',path.parent):add(video,'downloads')
            elif record.get('status') in ('failed','cancelled'):
                for file in path.parent.glob('media*'):
                    if re.fullmatch(r'media(?:\.[\w-]+){1,4}',file.name):add(file,'temporary')
        except (OSError,ValueError,TypeError):uncertain=True
    for path in (base/'output/video-cards').glob('*/manifest.json'):
        try:
            record=read(path)
            if record.get('audio_policy')!='silent_no_audio_stream':continue
            for row in record.get('cards',[]):
                name=Path(row.get('file',''))
                if len(name.parts)<=2 and re.fullmatch(OUTPUT_NAME+r'\.(?:launch|news)\.mov',name.name):
                    if len(name.parts)==1 or name.parts[0] in ('news','launches'):add(path.parent/name,'generated')
            add(path,'exports')
        except (OSError,ValueError,TypeError,AttributeError):uncertain=True
    for path in (base/'output').glob('SpaceWeekly_*/manifest.json'):
        if not re.fullmatch(r'SpaceWeekly_(?:roundup|news_voice)_\d{8}_\d{6}_[a-f0-9]{8}',path.parent.name):continue
        try:
            record=read(path)
            if record.get('state') not in ('incomplete','complete','exporting'):continue
            for file in path.parent.iterdir():
                if (re.fullmatch(OUTPUT_NAME+r'\.(?:(?:news|launch|roundup)\.mov|(?:intro\.)?mp4)',file.name)
                        or re.fullmatch(r'\d{2,}\.wav|(?:script|reading_script)\.md|reading\.txt|(?:paragraphs|subtitles)\.srt|(?:voice|narration)\.wav|(?:draft|snapshot|timeline|shots|reading_map|manifest|material_states|writing_input|polish_history)\.json',file.name)):
                    add(file,'exports')
        except (OSError,ValueError,TypeError):uncertain=True
    return owned,image_refs,uncertain


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


def references(extra=(),owned=None):
    """Protect saved drafts, histories and packages; unknown/unreadable references fail closed."""
    base=root();paths=set();hashes=set();uncertain=False
    known=owned or {}
    def visit(value,folder=base):
        if isinstance(value,dict):
            for v in value.values():visit(v,folder)
        elif isinstance(value,list):
            for v in value:visit(v,folder)
        elif isinstance(value,str):
            if re.fullmatch(r'[a-f0-9]{64}',value):hashes.add(value)
            if not value or len(value)>1024 or '\n' in value or '://' in value:return
            try:
                p=Path(value)
                if p.is_absolute():
                    if p.is_relative_to(base):paths.add(p)
                elif p.parts and p.parts[0] in ('cache','news-images','video-downloads','output'):paths.add((base/p).resolve())
                elif folder!=base and (folder/p).relative_to(base) in known:paths.add(folder/p)
            except (OSError,ValueError,RuntimeError):pass
    files=[base/'config/production-session.json',base/'cache/production-recovery.json']
    for directory in ('production-versions','roundup-drafts','news-drafts'):
        files.extend((base/'output'/directory).glob('*.json'))
        files.extend((base/'cache'/directory).glob('*.json'))
    files.extend((base/'cache/production_exports/drafts').glob('*.json'))
    files.extend(base/relative for relative in (owned or {}) if relative.parts[0]=='output' and relative.suffix=='.json')
    for path in files:
        if path.name=='cache-maintenance.json':continue
        try:
            if path.stat().st_size>16*1024*1024:uncertain=True;continue
            if safe_relative(path) is None:uncertain=True;continue
            visit(json.loads(path.read_text(encoding='utf-8')),path.parent)
        except FileNotFoundError:continue
        except (OSError,ValueError):uncertain=True
    visit(extra)
    return paths,hashes,uncertain


def protected(path,refs,active=False,*,ignore_active=False):
    paths,hashes,uncertain=refs
    if active or (ACTIVE and not ignore_active) or uncertain:return True
    if (path.resolve() in paths or path.parent.name in hashes
            or path.parent in paths and path.parent.name.startswith(('SpaceWeekly_roundup_','SpaceWeekly_news_voice_'))):return True
    if path.suffix=='.json':return protected(path.with_suffix('.wav'),refs,active,ignore_active=ignore_active)
    sidecar=path.with_suffix('.json')
    if sidecar.exists():
        try:
            data=json.loads(sidecar.read_text(encoding='utf-8'))
            if set(data.get('source_hashes',[])) & hashes:return True
        except (ValueError,OSError):return True
    return False


def scan(token,*,now=None,extra=(),active=False,in_use=(),record_check=True):
    now=time.time() if now is None else now;base=root();rows=[];inventory=[]
    owned,image_refs,uncertain=receipts();paths,hashes,warning=references(extra,owned)
    refs=(paths|image_refs,hashes,False);blocked=bool(active or ACTIVE or warning or uncertain)
    playback={Path(path).resolve() for path in in_use if path}
    with LOCK:data=load()
    discovered={}
    paths_to_check={base/path for path in owned}
    paths_to_check.update((base/'output').glob('*'))
    for name in ('video-intro','video-cards'):paths_to_check.update((base/'output'/name).glob('*.intro.mp4'))
    for directory in ('news_thumbnails','news_http','news_image_downloads','roundup_pcm','news_pcm','roundup_playback','news_playback',
                      'news_audio','roundup_audio','news_alignment','roundup_alignment','voice_samples','episode-visuals','episode-previews'):
        folder=base/'cache'/directory
        paths_to_check.update(folder.glob('*/*' if directory.endswith(('_pcm','_playback','_audio','_alignment','_samples','-visuals')) else '*'))
    for path in sorted(paths_to_check):
        token.check();kind=managed(path,owned)
        if not kind or not path.is_file():continue
        try:info=path.stat()
        except FileNotFoundError:continue
        relative=str(path.relative_to(base));record=data['files'].get(relative)
        if not record and kind not in FORMAL:
            # Legacy caches have unknown use history. Observe once before applying retention.
            record=dict(first_seen=now);discovered[relative]=record
        record=record or {}
        used=max(record.get('last_used',record.get('first_seen',info.st_mtime)),info.st_mtime)
        old=now-used>=data['retention_days']*DAY
        referenced=protected(path,refs,ignore_active=True)
        locked=blocked or path in playback or path.with_suffix('.wav') in playback
        default=old and not referenced and not locked and kind not in FORMAL
        row=dict(path=relative,category=kind,size=info.st_size,mtime_ns=info.st_mtime_ns,last_used=used,
                 referenced=referenced,blocked=locked,default=default,old=old)
        inventory.append(row)
        if default:rows.append(row)
    with LOCK:
        current=load()
        for path,value in discovered.items():current['files'].setdefault(path,value)
        if record_check:current['last_check']=now;current['snooze_until']=0
        if record_check or discovered:write_json(resource_path('config','cache-maintenance.json'),current)
    return dict(files=rows,inventory=inventory,count=len(rows),bytes=sum(r['size'] for r in rows),reference_warning=warning or uncertain)


def due(now=None):
    now=time.time() if now is None else now;data=load()
    return data['enabled'] and (now>=data['snooze_until'] if data['snooze_until'] else
        now-max(data['last_check'],data['last_reminder'])>=data['interval_days']*DAY)


def reminded(now=None):return update(last_reminder=time.time() if now is None else now,snooze_until=0)


def snooze(now=None):
    now=time.time() if now is None else now
    # A later reminder is tomorrow; preserve the independent normal check interval.
    return update(snooze_until=now+DAY)


def clean(rows,token,*,now=None,extra=(),active=lambda:False,in_use=(),allow_formal=False,allow_referenced=False):
    now=time.time() if now is None else now;result=dict(deleted=0,bytes=0,failed=[],cancelled=False)
    from services.cancellation import TaskCancelledError
    owned,image_refs,uncertain=receipts()
    paths,hashes,warning=references(extra,owned);refs=(paths|image_refs,hashes,False)
    playback={Path(path).resolve() for path in in_use if path}
    # Keep inventory receipts when a file in their package could not be removed.
    for row in sorted(rows,key=lambda r:Path(r['path']).name=='manifest.json'):
        try:token.check()
        except TaskCancelledError:result['cancelled']=True;break
        path=root()/row['path']
        try:
            with LOCK:
                data=load()
                kind=managed(path,owned);formal=kind in FORMAL
                if not kind or kind!=row['category'] or uncertain or warning or active() or ACTIVE or path in playback or path.with_suffix('.wav') in playback:
                    raise ValueError('文件受保护或有任务正在使用')
                if (formal and not allow_formal) or (protected(path,refs) and (not allow_referenced or not row.get('referenced'))):
                    raise ValueError('文件受引用保护或未明确选择')
                if path.name=='manifest.json' and any(Path(failure['path']).is_relative_to(path.parent.relative_to(root())) for failure in result['failed']):
                    raise ValueError('所属素材有未删除项，保留清单')
                info=path.stat();record=data['files'].get(str(path.relative_to(root())),{})
                used=max(record.get('last_used',record.get('first_seen',now)),info.st_mtime)
                if info.st_size!=row['size'] or info.st_mtime_ns!=row['mtime_ns'] or (not formal and now-used<data['retention_days']*DAY):
                    raise ValueError('文件已变化或近期使用，请重新检查')
                path.unlink();result['deleted']+=1;result['bytes']+=info.st_size
                data['files'].pop(str(path.relative_to(root())),None);data['last_clean']=now
                write_json(resource_path('config','cache-maintenance.json'),data)
        except (OSError,ValueError,KeyError) as error:result['failed'].append(dict(path=row['path'],reason=str(error)))
    update(last_clean=now,last_result=result)
    return result
