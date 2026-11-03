"""Velopack owns package selection, verification and replacement; no executable copying here."""
import json
import hashlib
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from resources import app_data_directory,bundled_resource_path
from services.launch_data import write_json

REPOSITORY='https://github.com/Starship30/SpaceWeekly'
SDK_VERSION='1.2.161'
ASSET_KEYS=('PackageId','Version','Type','FileName','SHA1','SHA256','Size','NotesMarkdown','NotesHtml')


def profile():
    path=bundled_resource_path('release.json')
    return json.loads(path.read_text(encoding='utf-8')) if getattr(sys,'frozen',False) and path.exists() else {}


def installed():
    return bool(profile() and (Path(sys.executable).resolve().parent.parent/'Update.exe').is_file())


def state_path():return app_data_directory()/'updates.json'


def load():
    data=json.loads(state_path().read_text(encoding='utf-8')) if state_path().exists() else {}
    if not isinstance(data,dict) or data.get('version',1)!=1 or type(data.get('automatic',True)) is not bool:raise ValueError('更新设置无效，原文件保留')
    return dict({'version':1,'automatic':True},**data)


def save(**values):
    data=load();data.update(values);write_json(state_path(),data);return data


def source():
    data=load();local=data.get('local_source')
    if local and profile().get('qa'):return str(Path(local).resolve())
    return REPOSITORY


def manager():
    import velopack
    src=source()
    backend=velopack.GithubSource(REPOSITORY,access_token=None,prerelease=False) if src==REPOSITORY else src
    return velopack.UpdateManager(backend,velopack.UpdateOptions(False,10,profile()['channel']))


def asset_dict(asset):return {key:getattr(asset,key) for key in ASSET_KEYS}


def asset(value):
    import velopack
    if not isinstance(value,dict) or any(key not in value for key in ASSET_KEYS):raise ValueError('更新元数据不完整')
    if value['PackageId']!=profile()['package_id'] or Path(value['FileName']).name!=value['FileName'] or not value['FileName'].endswith('.nupkg'):
        raise ValueError('更新包身份或文件名不匹配')
    if not re.fullmatch('[a-fA-F0-9]{64}',value['SHA256']) or type(value['Size']) is not int or value['Size']<=0:raise ValueError('更新包缺少有效完整性信息')
    if not profile().get('qa') and '-' in value['Version'].split('+')[0]:raise ValueError('稳定版不接收预发布更新')
    return velopack.VelopackAsset(**{key:value[key] for key in ASSET_KEYS})


def info_dict(info):
    return dict(TargetFullRelease=asset_dict(info.TargetFullRelease),DeltasToTarget=[asset_dict(a) for a in info.DeltasToTarget],
        BaseRelease=asset_dict(info.BaseRelease) if info.BaseRelease else None,IsDowngrade=info.IsDowngrade)


def info(value):
    import velopack
    if value.get('IsDowngrade'):raise ValueError('不允许自动降级')
    base=value.get('BaseRelease');deltas=value.get('DeltasToTarget',[])
    # SDK may report an installed base without checksums; use the verified full target in that case.
    if base and not re.fullmatch('[a-fA-F0-9]{64}',base.get('SHA256','')):base=None;deltas=[]
    return velopack.UpdateInfo(asset(value['TargetFullRelease']),[asset(a) for a in deltas],False,asset(base) if base else None)


def run_job(action,token,progress=lambda value:None):
    if not installed():raise ValueError('源码运行或便携开发构建不提供在线更新，请使用安装版本')
    directory=app_data_directory()/'update-jobs'/uuid4().hex;directory.mkdir(parents=True)
    request=directory/'request.json';write_json(request,dict(action=action))
    command=[sys.executable,'--spaceweekly-update-worker',str(request)]
    deadline=time.monotonic()+(60 if action in ('check','status') else 1800)
    with (directory/'worker.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            last=None
            while process.poll() is None:
                token.check()
                event=directory/'progress.json'
                if event.exists():
                    try:
                        value=json.loads(event.read_text(encoding='utf-8'))['percent']
                        if value!=last:progress(value);last=value
                    except (OSError,ValueError,KeyError):pass
                if time.monotonic()>deadline:raise RuntimeError('更新请求超时，可稍后重试')
                token.wait(.1)
            token.check()
            result_path=directory/'result.json'
            if not result_path.exists():raise RuntimeError('更新进程异常结束；当前版本仍可使用')
            result=json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('error'):raise RuntimeError(result['error'])
            if process.returncode:raise RuntimeError('更新进程失败')
            return result
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)


def worker_action(action,progress):
    mgr=manager();current=mgr.get_current_version()
    if mgr.get_app_id()!=profile()['package_id']:raise ValueError('安装身份不匹配')
    if action=='status':
        pending=checked_pending(mgr)
        return dict(current=current,pending=asset_dict(pending) if pending else None)
    if action=='check':
        found=mgr.check_for_updates()
        if found:asset(asset_dict(found.TargetFullRelease))
        return dict(current=current,available=info_dict(found) if found else None)
    if action in ('download','verify'):
        state=load();value=state.get('available')
        if action=='verify':
            pending=checked_pending(mgr)
            if pending is None:raise ValueError('待更新包不存在，请重新检查并下载')
            value=dict(TargetFullRelease=asset_dict(pending),DeltasToTarget=[],IsDowngrade=False)
        if not value:raise ValueError('请先检查更新')
        target=info(value)
        # Reserve room for package, extraction and replacement; models are stored separately.
        if shutil.disk_usage(Path(sys.executable).resolve().parent.parent).free<target.TargetFullRelease.Size*3+128*1024*1024:
            raise ValueError('安装磁盘空间不足，请释放空间后重试')
        mgr.download_updates(target,progress_callback=progress)
        pending=checked_pending(mgr)
        if pending is None or pending.Version!=target.TargetFullRelease.Version:raise ValueError('未确认可安装的更新包')
        return dict(current=current,pending=asset_dict(pending))
    raise ValueError('未知更新操作')


def handoff():
    mgr=manager();pending=checked_pending(mgr);expected=load().get('pending')
    if not pending or not expected or pending.Version!=expected['Version']:raise ValueError('待更新状态已变化，请重新检查')
    asset(asset_dict(pending))
    # The native updater waits for this main process; production has already stopped its children.
    mgr.wait_exit_then_apply_updates(pending,silent=False,restart=True)


def handoff_uninstall():
    if not installed():raise ValueError('仅安装版本提供卸载入口')
    current=Path(sys.executable).resolve().parent
    launcher=current.parent/'Uninstall.exe'
    if not launcher.is_file():launcher=current/'Uninstall.exe'
    if not launcher.is_file():raise ValueError('独立卸载入口缺失，请用安装包修复')
    # This entry also requests a normal save/close from any other open application instances.
    subprocess.Popen([str(launcher),'--confirmed'],cwd=app_data_directory())


def checked_pending(mgr):
    pending=mgr.get_update_pending_restart()
    if pending is None:return None
    state=load();known=[state.get('pending'),(state.get('available') or {}).get('TargetFullRelease')]
    for expected in known:
        if expected and pending.Version==expected.get('Version') and pending.PackageId==expected.get('PackageId'):
            trusted=asset(expected)
            if pending.FileName!=trusted.FileName:continue
            path=Path(sys.executable).resolve().parent.parent/'packages'/trusted.FileName
            if not path.is_file() or path.stat().st_size!=trusted.Size:continue
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest().casefold()==trusted.SHA256.casefold():return trusted
    # SDK local discovery omits checksums; only the original feed hash can authorize the on-disk package.
    raise ValueError('本地待更新包与原始校验信息不符，请重新检查并下载；当前版本保留')
