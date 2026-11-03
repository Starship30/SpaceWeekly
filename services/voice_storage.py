"""Ownership and process locks for application-managed voice files, separate from workspaces."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from services.launch_data import write_json

OWNER = 'SpaceWeeklyVoice-v1'


def registry_path():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'SpaceWeekly'/'voice-location.json'


def default_root():
    try:
        value=json.loads(registry_path().read_text(encoding='utf-8'))
        return Path(value['root']).expanduser().resolve()
    except (OSError,ValueError,KeyError,TypeError):
        return registry_path().parent/'voice'


def inside(path,root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def program_directory():
    path=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    return path.parent if getattr(sys,'frozen',False) and path.name=='current' else path


def validate_voice_locations(settings):
    if getattr(sys,'frozen',False):
        for key in ('python','model'):
            if settings.get(key) and inside(settings[key],program_directory()):
                raise ValueError('语音环境与模型不能放在安装目录内，请先迁到独立目录再使用，避免更新或卸载时丢失')


def validate_root(path):
    from resources import workspace_path
    raw=Path(path).expanduser()
    if raw.is_symlink() or getattr(raw,'is_junction',lambda:False)():raise ValueError('语音目录不能是目录链接')
    root=raw.resolve()
    program=program_directory()
    workspace=workspace_path()
    if root==Path(root.anchor) or inside(root,program) or workspace and inside(root,workspace):
        raise ValueError('请选择程序目录和工作区以外的专用语音目录')
    return root


def ensure_root(path):
    root=validate_root(path);marker=root/'ownership.json'
    if root.exists() and not marker.exists() and any(root.iterdir()):
        raise ValueError('该目录已有其他文件，请选择空目录或已有的 SpaceWeekly 语音目录')
    root.mkdir(parents=True,exist_ok=True)
    if marker.exists():
        data=json.loads(marker.read_text(encoding='utf-8'))
        if data.get('owner')!=OWNER or not data.get('id'):raise ValueError('不是有效的应用管理语音目录')
    else:write_json(marker,dict(owner=OWNER,id=uuid4().hex))
    return root


class VoiceLease:
    """OS releases the lock on crashes; stale status cannot grant concurrent access."""
    def __init__(self,root):
        self.root=Path(root);self.handle=None

    def __enter__(self):
        self.root.mkdir(parents=True,exist_ok=True)
        self.handle=(self.root/'operation.lock').open('a+b')
        self.handle.seek(0,2)
        if self.handle.tell()==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close();self.handle=None
            raise ValueError('该语音环境正在配置或被配音进程使用，请先停止并卸载模型') from None
        return self

    def __exit__(self,*args):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
            self.handle.close();self.handle=None


def managed_root(python):
    environment=Path(python).resolve().parent.parent
    marker=environment/'spaceweekly-environment.json'
    if not marker.exists():return None
    data=json.loads(marker.read_text(encoding='utf-8'))
    root=Path(data['root']).resolve();owner=json.loads((root/'ownership.json').read_text(encoding='utf-8'))
    if (data.get('owner')!=OWNER or owner.get('owner')!=OWNER or data.get('root_id')!=owner.get('id')
            or not inside(environment,root/'environments') or not environment.is_dir()):
        raise ValueError('应用管理的环境身份无效，请检查与修复')
    return root


def environment_guard(python):
    root=managed_root(python)
    return VoiceLease(root) if root else nullcontext()


def remember_root(root):
    write_json(registry_path(),dict(root=str(Path(root).resolve()),owner=OWNER))


def remove_managed(root, relative):
    """Only explicit generated environment/cache paths; never accept an arbitrary delete target."""
    import shutil
    root=ensure_root(root)
    with VoiceLease(root):
        target=(root/relative).resolve()
        parts=Path(relative).parts
        allowed=(relative=='downloads' or relative=='uv-cache' or len(parts)==2 and parts[0]=='environments')
        if not allowed or target==root or not inside(target,root):raise ValueError('只能移除应用管理的环境或下载缓存')
        if target.exists():
            if Path(relative).parts[:1]==('environments',):
                marker=target/'spaceweekly-environment.json'
                data=json.loads(marker.read_text(encoding='utf-8'))
                owner=json.loads((root/'ownership.json').read_text(encoding='utf-8'))
                if data.get('owner')!=OWNER or data.get('root_id')!=owner['id'] or Path(data.get('root','')).resolve()!=root:
                    raise ValueError('不能移除用户提供的环境')
            # Target has been resolved and checked inside the owned root before recursive removal.
            shutil.rmtree(target)
            state_path=root/'deployment.json'
            if parts[0]=='environments' and state_path.exists():
                state=json.loads(state_path.read_text(encoding='utf-8'))
                def invalidate(value):
                    if isinstance(value,dict):
                        if value.get('python') and inside(value['python'],target):value.update(ready=False,error='应用环境已移除')
                        for child in value.values():invalidate(child)
                    elif isinstance(value,list):
                        for child in value:invalidate(child)
                invalidate(state);state.update(status='needs_check',message='所选应用环境已移除，模型与工作区保留')
                write_json(state_path,state)
