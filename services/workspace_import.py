"""Explicit, backed-up data import; no disk discovery and no source deletion."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4
import zipfile

from services.launch_data import write_json

DIRECTORIES=('database','output','cache','config','translator','prompts','news-images','video-downloads','logs','errors')
ROOT_FILES=('config.json','feeds.json','categories.json')


def data_files(source):
    files=[]
    for name in ROOT_FILES:
        if (source/name).is_file():files.append(source/name)
    for name in DIRECTORIES:
        folder=source/name
        for path in folder.rglob('*'):
            if path.is_file() and path.resolve().is_relative_to(source) and not path.is_symlink():
                if path.suffix.lower() in ('.safetensors','.whl','.pyc','.pyd','.exe','.dll'):continue
                files.append(path)
    return files


def import_data(source,destination,token):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not (source/'config.json').is_file():raise ValueError('请选择含 config.json 的旧工作区或便携版数据目录')
    if destination==source or destination.is_relative_to(source) or source.is_relative_to(destination):raise ValueError('导入目标须独立于旧目录')
    if destination.exists() and any(destination.iterdir()):raise ValueError('请选择空目录导入；不会覆盖现有工作区')
    files=data_files(source);needed=sum(p.stat().st_size for p in files)*2+64*1024*1024
    destination.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(destination.parent).free<needed:raise ValueError('导入与备份所需磁盘空间不足')
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]
    backup=destination.parent/('SpaceWeekly-import-backup-'+stamp+'.zip')
    with zipfile.ZipFile(backup,'x',zipfile.ZIP_DEFLATED) as archive:
        for path in files:token.check();archive.write(path,path.relative_to(source).as_posix())
    destination.mkdir(parents=True,exist_ok=True);report=dict(version=1,status='copying',source=str(source),destination=str(destination),backup=str(backup),files=len(files),external_paths=[],missing_paths=[])
    record=destination/'config'/('import-record-'+stamp+'.json');write_json(record,report)
    try:
        for path in files:
            token.check();target=destination/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():raise ValueError('目标文件已存在，停止导入以保护新文件：'+str(target.relative_to(destination)))
            temp=target.with_name('.import-'+uuid4().hex)
            try:
                with path.open('rb') as src,temp.open('xb') as dest:
                    while block:=src.read(1024*1024):token.check();dest.write(block)
                shutil.copystat(path,temp);temp.replace(target)
            finally:temp.unlink(missing_ok=True)
        def rewrite(value):
            if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
            if isinstance(value,list):return [rewrite(v) for v in value]
            if isinstance(value,str):
                try:
                    path=Path(value)
                    if path.is_absolute() and path.resolve().is_relative_to(source):
                        relative=path.resolve().relative_to(source);new=destination/relative
                        if new.exists():return str(new)
                        if path.exists():
                            # Models / interpreters stay at the untouched old location, outside installed versions.
                            report['external_paths'].append(str(path));return value
                        report['missing_paths'].append(str(relative))
                except (OSError,ValueError):pass
            return value
        for path in files:
            if path.suffix.lower()!='.json':continue
            target=destination/path.relative_to(source)
            try:data=json.loads(target.read_text(encoding='utf-8'));write_json(target,rewrite(data))
            except ValueError:
                if path.name in ('config.json','production-session.json','dictionary.json'):raise ValueError('核心数据格式损坏，已停止激活导入；备份和原目录保留') from None
        report.update(status='complete',external_paths=sorted(set(report['external_paths'])),missing_paths=sorted(set(report['missing_paths'])))
        write_json(record,report);return str(destination)
    except BaseException:
        report['status']='incomplete';write_json(record,report);raise


def prepare_release(workspace):
    """Only schema 1 is supported; backup metadata before recording a new release."""
    from config import app_version
    workspace=Path(workspace).resolve();marker=workspace/'config/workspace-format.json'
    value=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    if value.get('schema',1)!=1:raise ValueError('工作区来自不兼容的新格式，请使用匹配版本；未修改数据')
    if value.get('app_version')==app_version():return
    backup=workspace/'backups'/('before-'+datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.zip');backup.parent.mkdir(parents=True,exist_ok=True)
    files=[p for p in data_files(workspace) if p.relative_to(workspace).parts[0] in ('config','translator','database','prompts') or p.name in ROOT_FILES]
    files+=[workspace/'cache/production-recovery.json'] if (workspace/'cache/production-recovery.json').is_file() else []
    with zipfile.ZipFile(backup,'x',zipfile.ZIP_DEFLATED) as archive:
        for path in files:archive.write(path,path.relative_to(workspace).as_posix())
    write_json(marker,dict(schema=1,app_version=app_version(),previous=value.get('app_version'),backup=str(backup)))
