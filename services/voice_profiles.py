"""User-scoped reference recordings, independent of workspaces and model downloads."""
from array import array
import json
import math
from pathlib import Path
import re
import shutil
from uuid import uuid4
import wave

from resources import app_data_directory
from services.launch_data import write_json
from services.media_process import run_media

PREFIX='my:'
REFERENCE_TEXT='这里是每周航天新闻。北京时间十月二十四日，火箭从发射场升空，将卫星送入预定轨道。接下来，我们来看本周的其他航天进展。'


def directory():
    return app_data_directory()/'voices'


def folder(identifier):
    if not isinstance(identifier,str) or not re.fullmatch(r'my:[a-f0-9]{32}',identifier):
        raise ValueError('音色编号无效')
    root=directory().resolve()
    target=root/identifier.removeprefix(PREFIX)
    if target.resolve()!=target:raise ValueError('音色目录不能是链接')
    return target


def load(identifier):
    target=folder(identifier)
    try:record=json.loads((target/'voice.json').read_text(encoding='utf-8'))
    except FileNotFoundError:raise ValueError('此参考音色已移除，请选择其他音色') from None
    if not isinstance(record,dict) or record.get('id')!=identifier or any(not isinstance(record.get(k),str) or not record[k]
        for k in ('name','text','model','reference','original','sha256')):
        raise ValueError('音色记录无效')
    for key in ('reference','original'):
        filename=record[key]
        path=target/filename
        if not re.fullmatch(r'[a-f0-9]{32}-(?:reference\.wav|original\.[a-z0-9]+)',filename) or path.resolve()!=path:
            raise ValueError('音色资料路径无效')
        record[key]=str(path)
    return record


def configured(settings):
    try:
        speaker=settings.get('speaker','')
        model=load(speaker)['model'] if speaker.startswith(PREFIX) else settings.get('model')
        return bool(settings.get('python') and Path(settings['python']).is_file() and model and Path(model).is_dir())
    except (OSError,ValueError):
        return False


def list_profiles():
    profiles=[]
    for path in directory().glob('*/voice.json'):
        try:profiles.append(load(PREFIX+path.parent.name))
        except (OSError,ValueError):continue
    return sorted(profiles,key=lambda row:(row['name'].casefold(),row['id']))


def decode(source,target,token):
    source=Path(source)
    if not source.is_file() or not 0<source.stat().st_size<=64*1024*1024:
        raise ValueError('请选择小于 64 MB 的非空参考录音')
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    run_media(['ffmpeg','-v','error','-y','-i',str(source.resolve()),'-map','0:a:0','-t','31',
               '-ac','1','-ar','24000','-c:a','pcm_s16le',str(target)],token)
    with wave.open(str(target),'rb') as sound:
        seconds=sound.getnframes()/sound.getframerate()
        if not 3<=seconds<=30:raise ValueError('参考录音需为 3 至 30 秒')
        samples=array('h',sound.readframes(sound.getnframes()))
    if not samples or math.sqrt(sum(s*s for s in samples)/len(samples))<100:
        raise ValueError('参考录音为空或音量过低，请重新录制')
    if sum(abs(s)>=32760 for s in samples)>len(samples)*.01:
        raise ValueError('参考录音削波明显，请降低录音音量后重试')
    return target


def save(name,text,model,original,reference,identifier=None):
    from services.roundup_audio import file_hash
    name,text=name.strip(),text.strip()
    if not 1<=len(name)<=48 or not 1<=len(text)<=2000:raise ValueError('请填写名称和与录音相符的文字')
    identifier=identifier or PREFIX+uuid4().hex
    target=folder(identifier);target.mkdir(parents=True,exist_ok=True)
    stamp=uuid4().hex
    suffix=Path(original).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}',suffix):suffix='.audio'
    names=(stamp+'-original'+suffix,stamp+'-reference.wav')
    try:
        shutil.copyfile(original,target/names[0]);shutil.copyfile(reference,target/names[1])
        write_json(target/'voice.json',dict(id=identifier,name=name,text=text,model=str(model),original=names[0],
            reference=names[1],sha256=file_hash(target/names[1]),format=1))
    except BaseException:
        for filename in names:(target/filename).unlink(missing_ok=True)
        raise
    return identifier


def rename(identifier,name):
    name=name.strip()
    if not 1<=len(name)<=48:raise ValueError('音色名称需为 1 至 48 个字符')
    target=folder(identifier);record=load(identifier)
    record.update(name=name,reference=Path(record['reference']).name,original=Path(record['original']).name)
    write_json(target/'voice.json',record)


def delete(identifier):
    target=folder(identifier);load(identifier)
    (target/'voice.json').unlink()
    for path in target.iterdir():
        if path.is_file() and not path.is_symlink() and re.fullmatch(r'[a-f0-9]{32}-(?:reference\.wav|original\.[a-z0-9]+)',path.name):
            path.unlink()


def base_model(root):
    from services.voice_deployment import catalog
    model=catalog()['models']['base']
    return Path(root)/'models'/model['repo'].split('/')[-1]/model['revision']
