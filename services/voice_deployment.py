"""Versioned Windows voice deployment, driven by the app's background setup page."""
from copy import deepcopy
import ctypes
from datetime import datetime,timezone
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import wave
import zipfile

from resources import bundled_resource_path
from services.cancellation import TaskCancelledError
from services.launch_data import write_json
from services.launch_roundup import digest
from services.voice_storage import ensure_root,validate_root,VoiceLease,OWNER,remember_root,inside
from services.voice_download import download,verified,sha256,redact,link_file

TEST_TEXT='你好，这是航天周报语音检查。'
GIB=1024**3
STAGES=('detect','python','dependencies','models','verify')


def now():return datetime.now(timezone.utc).isoformat()


def pending_capabilities(result):
    return ('配音已可用' if result['tts']['ready'] else '配音需要检查')+'；'+(
        '对齐已可用' if result['align']['ready'] else '对齐需要检查')


def catalog():
    data=json.loads(bundled_resource_path('tools','voice_deployment','profiles.json').read_text(encoding='utf-8'))
    for model in data['models'].values():
        if not re.fullmatch(r'[a-f0-9]{40}',model['revision']):raise ValueError('模型快照清单无效')
        for file in model['files']:
            name=PurePosixPath(file['path'])
            if name.is_absolute() or '..' in name.parts or '\\' in str(name) or not re.fullmatch('[a-f0-9]{64}',file['sha256']):
                raise ValueError('模型文件清单无效')
    return data


def read_state(root):
    try:
        value=json.loads((Path(root)/'deployment.json').read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('owner')!=OWNER:raise ValueError('部署清单身份无效')
        if value.get('status')=='configuring':
            try:
                with VoiceLease(root):
                    value=dict(value,status='interrupted',message='上次配置已中断，请检查后继续')
                    if value.get('stage') in value.get('stages',{}):value['stages'][value['stage']]['status']='interrupted'
                    write_json(Path(root)/'deployment.json',value)
            except ValueError:pass
        active=value.get('active',{})
        if value.get('status')=='ready' and not active.get('external') and active.get('version')!=catalog()['version']:
            value=dict(value,status='needs_check',message='语音部署方案版本已变更，请检查兼容性；不会自动下载或替换已有环境')
        return value
    except FileNotFoundError:return dict(owner=OWNER,status='unconfigured',active={},stages={})


def detect(root):
    supported=sys.platform=='win32' and sys.maxsize>2**32 and platform.machine().upper() in ('AMD64','X86_64')
    memory={};cpu_name=platform.processor() or '处理器（CPU）'
    if sys.platform=='win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                cpu_name=winreg.QueryValueEx(key,'ProcessorNameString')[0].strip()
        except OSError:pass
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),*[(k,ctypes.c_ulonglong) for k in
                ('total','available','page','available_page','virtual','available_virtual','extended')]]
        info=Memory();info.length=ctypes.sizeof(info)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):
            memory=dict(total_bytes=info.total,available_bytes=info.available)
    path=Path(root).expanduser().resolve()
    while not path.exists() and path!=path.parent:path=path.parent
    gpus=[]
    executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvidia-smi.exe'
    if executable.is_file():
        try:
            output=subprocess.check_output([str(executable),'--query-gpu=name,driver_version,memory.total,memory.free','--format=csv,noheader,nounits'],
                timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stderr=subprocess.DEVNULL).decode('utf-8',errors='replace')
            for line in output.splitlines():
                fields=[f.strip() for f in line.split(',')]
                if len(fields)==4:gpus.append(dict(name=fields[0],driver=fields[1],memory_mib=int(fields[2]),free_mib=int(fields[3])))
        except (OSError,ValueError,subprocess.SubprocessError):pass
    environments=[]
    for marker in (Path(root)/'environments').glob('*/spaceweekly-environment.json'):
        try:
            entry=json.loads(marker.read_text(encoding='utf-8'))
            if entry.get('owner')==OWNER:environments.append(dict(path=str(marker.parent),role=entry.get('role'),status='needs_check'))
        except (OSError,ValueError):pass
    return dict(supported=supported,system=platform.platform(),architecture=platform.machine(),cpu_name=cpu_name,memory=memory,gpus=gpus,environments=environments,
        disk_free_bytes=shutil.disk_usage(path).free,gpu_note='检测到显卡不代表 CUDA 已可用，需目标环境实际推理检查')


def lock_path(role,device):return bundled_resource_path('tools','voice_deployment',f'{role}-{device}.lock')


def plan(root,device='cu128',quality='lite',source='modelscope'):
    if device not in ('cpu','cu128') or quality not in ('lite','standard') or source not in ('huggingface','modelscope'):
        raise ValueError('请选择支持的运行方案和下载源')
    root=validate_root(root);data=catalog()
    locks={role:sha256(lock_path(role,device)) for role in ('tts','align')}
    if any(value!=data['lock_hashes'][role+'-'+device] for role,value in locks.items()):
        raise ValueError('部署依赖清单损坏，请修复程序资源；未执行安装')
    models={role:data['models'][quality if role=='tts' else 'align'] for role in ('tts','align')}
    full=sum(m['download_bytes'] for m in models.values());remaining=0
    for model in models.values():
        for file in model['files']:
            blob=root/'downloads/models'/file['sha256']
            partial=blob.with_suffix('.part')
            if blob.is_file() and blob.stat().st_size==file['size']:continue
            remaining+=max(0,file['size']-(partial.stat().st_size if partial.exists() else 0))
    identity=digest(dict(version=data['version'],device=device,quality=quality,locks=locks,
        models={r:m['revision'] for r,m in models.items()}))
    state=read_state(root) if (root/'ownership.json').exists() else {}
    old=state.get('profiles',{}).get(identity,state.get('active',{}))
    existing=old.get('plan_id')==identity and all(old.get(r,{}).get('ready') for r in ('tts','align'))
    records=[state.get('active',{}),*state.get('profiles',{}).values()]
    environments_available=all(any(record.get('lock_hashes',{}).get(role)==locks[role]
        and record.get(role,{}).get('ready') for record in records) for role in ('tts','align'))
    if not environments_available and state.get('pending',{}).get('plan_id')==identity:
        from services.voice_storage import managed_root
        def dependencies_present(role):
            try:
                python=Path(state['pending'][role]['python'])
                marker=json.loads((python.parent.parent/'spaceweekly-environment.json').read_text(encoding='utf-8'))
                return python.is_file() and managed_root(python)==root and marker.get('dependencies_ready') and marker.get('lock_hash')==locks[role]
            except (OSError,ValueError,KeyError):return False
        environments_available=all(dependencies_present(role) for role in ('tts','align'))
    estimate=GIB if existing or environments_available else (7*GIB if device=='cpu' else 16*GIB)
    return dict(root=str(root),device=device,quality=quality,source=source,plan_id=identity,version=data['version'],
        locks=locks,model_bytes=full,remaining_model_bytes=remaining,environment_estimate_bytes=estimate,
        required_free_bytes=remaining+estimate+GIB,space_estimated=True,models=models,
        hardware=detect(root),python=data['python'],uv=data['uv'])


def environment_vars(root):
    base={k:v for k,v in os.environ.items() if not k.startswith(('UV_','PIP_'))}
    env=dict(base,UV_CACHE_DIR=str(root/'uv-cache'),UV_PYTHON_INSTALL_DIR=str(root/'python'),UV_NATIVE_TLS='1',
        UV_PYTHON_INSTALL_BIN='0',UV_PYTHON_INSTALL_REGISTRY='0',UV_LINK_MODE='hardlink',UV_PYTHON_DOWNLOADS='never',
        UV_INDEX_URL='https://pypi.org/simple',PIP_INDEX_URL='https://pypi.org/simple',UV_EXTRA_INDEX_URL='',
        UV_HTTP_TIMEOUT='300',UV_HTTP_RETRIES='3',UV_CONCURRENT_DOWNLOADS='4',
        PIP_EXTRA_INDEX_URL='',UV_NO_CONFIG='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8',
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(root/'downloads/hf'),
        XDG_CACHE_HOME=str(root/'downloads/runtime'),NUMBA_CACHE_DIR=str(root/'downloads/numba'))
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX','UV_PYTHON','UV_PROJECT_ENVIRONMENT'):
        env.pop(key,None)
    return env


def expected_packages(path):
    result={}
    for line in path.read_text(encoding='utf-8').splitlines():
        match=re.match(r'^([a-zA-Z0-9_.-]+)==([^\s;\\]+)',line)
        if match:result[re.sub('[-_.]+','-',match[1]).lower()]=match[2]
        match=re.match(r'^(torch|torchaudio) @ .*?-([0-9.]+)(?:%2B|\+)(cpu|cu128)-cp312',line)
        if match:result[match[1]]=match[2]+'+'+match[3]
    return result


class VoiceDeployment:
    def __init__(self,root,token,on_progress=None):
        self.root=Path(root).expanduser().resolve();self.token=token;self.on_progress=on_progress or (lambda event:None)
        self.state=None;self._last_write=0;self._last_progress=0

    def emit(self,stage,message,**fields):
        self.token.check()
        changed=self.state.get('stage')!=stage or self.state.get('message')!=message
        self.state['stage']=stage;self.state['message']=message;self.state['updated_at']=now()
        self.state['stages'][stage]=dict(status='running',message=message,**fields)
        stamp=time.monotonic()
        if changed or stamp-self._last_write>.7:
            write_json(self.state_file,self.state);self._last_write=stamp
        if stamp-self._last_progress>.08 or not fields.get('total_bytes'):
            self.on_progress(dict(stage=stage,message=message,status=self.state['status'],**fields));self._last_progress=stamp

    def log(self,message):
        message=redact(message)
        with (self.root/'deployment.log').open('a',encoding='utf-8') as handle:handle.write(now()+' '+message+'\n')
        self.on_progress(dict(activity=message))

    def command(self,args,*,timeout=1800):
        from services.video_download import _run_process
        lines=[]
        def output(line):
            line=redact(line);lines.append(line);self.log(line)
        _run_process([str(a) for a in args],self.token.check,output,timeout=timeout,env=environment_vars(self.root))
        return '\n'.join(lines)

    @property
    def state_file(self):return self.root/('base-deployment.json' if getattr(self,'base_only',False) else 'deployment.json')

    def finish_stage(self,stage):
        self.state['stages'][stage]['status']='complete';write_json(self.state_file,self.state)

    def prepare_base(self):
        self.root=ensure_root(self.root)
        self.base_only=True
        model=catalog()['models']['base']
        with VoiceLease(self.root):
            self.state=dict(owner=OWNER,status='configuring',stages={},started_at=now())
            try:
                from services.voice_profiles import base_model
                target=base_model(self.root)
                needed=sum(f['size'] for f in model['files']
                    if not (target/f['path']).is_file() and not (self.root/'downloads/models'/f['sha256']).is_file())
                if shutil.disk_usage(self.root).free<needed+128*1024**2:raise ValueError('磁盘空间不足，无法准备参考音色模型')
                self.emit('models','正在准备参考音色组件')
                path=self.models(dict(models={'base':model},model_bytes=model['download_bytes'],source='huggingface'))['base']
                self.state.update(status='downloaded',message='Base 组件已校验；请在我的音色中生成试听')
                return path
            except TaskCancelledError:
                self.state.update(status='cancelled');raise
            except BaseException:
                self.state.update(status='failed');raise
            finally:write_json(self.state_file,self.state)

    def bootstrap(self,request):
        info=request['uv'];archive=self.root/'downloads'/f"uv-{info['version']}.zip"
        target=self.root/'tools'/info['version']/'uv.exe'
        if target.is_file() and sha256(target)==info['exe_sha256']:return target
        self.emit('python','准备独立运行环境')
        download(info['url'],archive,info['size'],info['sha256'],self.token,
            lambda a,b:self.emit('python','下载配置工具',downloaded_bytes=a,total_bytes=b))
        target.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            names=[n for n in z.namelist() if PurePosixPath(n).name=='uv.exe']
            if len(names)!=1:raise ValueError('配置工具压缩包内容无效')
            data=z.read(names[0])
        import hashlib
        expected=hashlib.sha256(data).hexdigest()
        if not target.is_file() or sha256(target)!=expected:
            temporary=target.with_suffix('.tmp');temporary.write_bytes(data);temporary.replace(target)
        return target

    def python(self,uv,request):
        self.emit('python','准备应用专用 Python（不修改系统 PATH 或注册表）')
        env=environment_vars(self.root);env['UV_PYTHON_DOWNLOADS']='manual'
        # python install explicitly permits this one pinned download; all later operations prohibit implicit downloads.
        from services.video_download import _run_process
        _run_process([str(uv),'python','install',request['python'],'--no-bin','--no-registry','--no-progress'],self.token.check,self.log,
            timeout=1200,env=env)
        output=self.command([uv,'python','find','--managed-python',request['python'],'--no-progress'])
        candidates=[Path(line.strip()) for line in output.splitlines() if line.strip().lower().endswith('python.exe')]
        if len(candidates)!=1 or not inside(candidates[0],self.root/'python') or not candidates[0].is_file():
            raise ValueError('未找到应用管理的目标 Python，未使用系统解释器代替')
        self.finish_stage('python');return candidates[0]

    def probe(self,python,role,device,*,require_lock=True):
        directory=self.root/'checks';directory.mkdir(exist_ok=True)
        output=directory/(role+'-modules.json');output.unlink(missing_ok=True)
        try:self.command([python,'-I','-X','utf8',bundled_resource_path('tools','voice_environment_probe.py'),'--output',output,
            '--role',role,'--device','cpu' if device=='cpu' else 'cuda:0'],timeout=600)
        except TaskCancelledError:raise
        except (RuntimeError,ValueError):
            if not output.exists():raise
        data=json.loads(output.read_text(encoding='utf-8'))
        if not data.get('ready'):
            self.log('目标环境检查原因：'+str(data.get('error','缺少必要模块')))
            raise ValueError('目标环境未通过模块或设备检查，请展开详情查看原因')
        if require_lock:
            if data['python'].split()[0]!=catalog()['python']:raise ValueError('独立 Python 版本需要修复')
            wanted=expected_packages(lock_path(role,device));actual=data['packages']
            wrong=[name for name,version in wanted.items() if actual.get(name)!=version]
            if wrong:raise ValueError('依赖版本需要修复：'+'、'.join(wrong[:8]))
        return data

    def environment(self,uv,python,request,role):
        def checked(candidate):
            marker=Path(candidate).parent.parent/'spaceweekly-environment.json'
            value=json.loads(marker.read_text(encoding='utf-8'))
            receipt=dict(value,dependencies_ready=True,lock_hash=request['locks'][role])
            if value!=receipt:write_json(marker,receipt)
            return Path(candidate)
        previous=self.state.get('pending',{}).get(role,{})
        record=self.state.get('profiles',{}).get(request['plan_id'],self.state.get('active',{}))
        active=record.get(role,{}) if record.get('plan_id')==request['plan_id'] else {}
        if not active:
            compatible=next((r for r in [self.state.get('active',{}),*self.state.get('profiles',{}).values()]
                if r.get('lock_hashes',{}).get(role)==request['locks'][role] and r.get(role,{}).get('ready')),None)
            if compatible:active=compatible[role]
        candidate=previous.get('python') if self.state.get('pending',{}).get('plan_id')==request['plan_id'] else None
        if not candidate:candidate=active.get('python')
        if candidate:
            from services.voice_storage import managed_root
            try:
                if managed_root(candidate)!=self.root:candidate=None
            except (OSError,ValueError,KeyError):candidate=None
        if candidate and inside(candidate,self.root/'environments') and Path(candidate).is_file():
            try:
                self.probe(candidate,role,request['device']);return checked(candidate)
            except (OSError,ValueError,RuntimeError) as error:
                self.log(str(error))
                report=self.root/'checks'/(role+'-modules.json')
                try:installed=json.loads(report.read_text(encoding='utf-8')).get('packages',{})
                except (OSError,ValueError):installed={}
                if all(installed.get(k)==v for k,v in expected_packages(lock_path(role,request['device'])).items()):
                    self.log('依赖版本完整但运行失败，将保留旧环境并创建新的候选环境进行检查');candidate=None
        if candidate and candidate==active.get('python') and active.get('ready'):candidate=None
        if not candidate:
            env=self.root/'environments'/(request['device']+'-'+role+'-'+request['locks'][role][:10]+'-'+uuid4().hex[:8])
            candidate=str(env/'Scripts/python.exe')
        env=Path(candidate).parent.parent
        if not inside(env,self.root/'environments'):raise ValueError('不能修改应用目录以外的环境')
        self.state.setdefault('pending',{}).update(plan_id=request['plan_id'])
        self.state['pending'][role]=dict(python=str(candidate))
        write_json(self.root/'deployment.json',self.state)
        env.mkdir(parents=True,exist_ok=True)
        marker=json.loads((self.root/'ownership.json').read_text(encoding='utf-8'))
        write_json(env/'spaceweekly-environment.json',dict(owner=OWNER,root=str(self.root),root_id=marker['id'],role=role))
        self.command([uv,'venv',env,'--python',python,'--allow-existing','--no-project','--no-progress'])
        self.emit('dependencies','安装配音依赖' if role=='tts' else '安装对齐依赖')
        self.command([uv,'pip','sync',lock_path(role,request['device']),'--python',candidate,'--require-hashes',
            '--only-binary',':all:','--index-url','https://pypi.org/simple','--no-progress'],timeout=4*3600)
        self.probe(candidate,role,request['device'])
        return checked(candidate)

    def models(self,request,*,download_missing=True):
        paths={};total=request['model_bytes'];completed=0
        for role,model in request['models'].items():
            target=self.root/'models'/model['repo'].split('/')[-1]/model['revision'];paths[role]=str(target)
            changed=False
            for file in model['files']:
                self.token.check();blob=self.root/'downloads/models'/file['sha256']
                destination=target/file['path'];base=completed
                if not inside(destination,self.root) or not inside(blob,self.root):raise ValueError('模型目录含指向管理目录外的链接，未修改外部文件')
                self.emit('models','检查模型文件：'+file['path'],downloaded_bytes=completed,total_bytes=total)
                if not verified(destination,file['size'],file['sha256']):
                    if not download_missing:raise ValueError('模型文件缺失或损坏：'+file['path'])
                    if request['source']=='huggingface':
                        url=f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{file['path']}"
                    else:
                        from urllib.parse import urlencode
                        url=f"https://modelscope.cn/api/v1/models/{model['repo']}/repo?"+urlencode(dict(Revision=file['modelscope_revision'],FilePath=file['path']))
                    download(url,blob,file['size'],file['sha256'],self.token,
                        lambda a,b:self.emit('models','下载模型：'+file['path'],downloaded_bytes=base+a,total_bytes=total))
                    link_file(blob,destination);changed=True
                completed+=file['size']
            receipt=target/'spaceweekly-model.json'
            try:old=json.loads(receipt.read_text(encoding='utf-8'))
            except (OSError,ValueError):old={}
            identity=dict(owner=OWNER,repo=model['repo'],revision=model['revision'],files=model['files'],download_complete=True)
            # An unchanged receipt keeps existing narration/alignment cache identities stable after a check or source switch.
            if changed or any(old.get(k)!=v for k,v in identity.items()):
                write_json(receipt,dict(identity,source=request['source'],verified_at=now()))
        self.finish_stage('models');return paths

    def verify(self,settings,*,managed=True):
        from ai.qwen_tts import QwenTTSProvider,capabilities
        from ai.qwen_aligner import QwenForcedAligner
        from ai.speech import SpeechRequest,generate_speech
        from services.roundup_audio import wav_info
        from services.roundup_sync import map_tokens,RATE
        result={r:dict(settings[r],ready=False) for r in ('tts','align')}
        directory=self.root/'checks'/('run-'+uuid4().hex[:8]);directory.mkdir(parents=True,exist_ok=True)
        voice=settings['tts'];align=settings['align'];sound=None
        self.emit('verify','检查配音能力：加载模型并生成短中文')
        provider=QwenTTSProvider(voice,self.log,acquire_environment=False)
        try:
            if not voice.get('python') or not voice.get('model'):raise ValueError('尚未提供配音解释器或模型，配音能力待配置')
            probe=self.probe(voice['python'],'tts','cpu' if voice['device']=='cpu' else 'cu128',require_lock=managed)
            info=capabilities(voice['model']);speaker=next((v for v in info['speakers'] if v.casefold()=='vivian'),next(iter(info['speakers']),'Vivian'))
            voice=dict(voice,speaker=speaker)
            sound=generate_speech(SpeechRequest(TEST_TEXT,model=voice['model'],voice=speaker),directory,
                provider=provider,cancel_check=self.token.check)
            frames,rate=wav_info(sound.path)
            with wave.open(str(sound.path),'rb') as audio:raw=audio.readframes(frames)
            from array import array
            if not .3<=frames/rate<=90 or max(abs(v) for v in array('h',raw))<16:raise ValueError('测试音频为空、异常静音或时长不合理')
            result['tts']=dict(voice,ready=True,verified_at=now(),probe=probe,
                capabilities=provider.runtime_info or info,test_audio=str(sound.path),test_text=TEST_TEXT,audio_sha256=sha256(sound.path),duration_seconds=frames/rate)
        except TaskCancelledError:raise
        except (OSError,ValueError,RuntimeError) as error:
            result['tts']['error']=redact(str(error));self.log(str(error))
        finally:
            self.state['validation']=deepcopy(result);write_json(self.root/'deployment.json',self.state)
            provider.unload(force=self.token.is_cancelled)
        self.emit('verify','检查对齐能力：加载独立模型并对齐刚生成的音频')
        try:
            if not align.get('python') or not align.get('model'):raise ValueError('尚未提供对齐解释器或模型，对齐能力待配置')
            probe=self.probe(align['python'],'align','cpu' if align['device']=='cpu' else 'cu128',require_lock=managed)
            if not sound or not result['tts']['ready']:raise ValueError('配音检查未通过，尚无有效测试音频用于对齐联检')
            frames,rate=wav_info(sound.path)
            from services.roundup_sync import canonical_audio
            audio=canonical_audio(dict(path=str(sound.path),sha256=sha256(sound.path),sample_rate=rate,sample_count=frames),self.token)
            duration=wav_info(audio)[0]
            tokens=[];failures=[]
            def aligned(identifier,items,error=''):
                if error:failures.append(error)
                else:tokens.extend(items or [])
            QwenForcedAligner(align,self.log,acquire_environment=False).align_batch(
                [dict(id='voice-environment-check',audio=str(audio),text=TEST_TEXT)],self.token,aligned)
            if failures:raise ValueError('、'.join(failures))
            spans=map_tokens(tokens,[dict(id='check',reading_text=TEST_TEXT)],duration)
            write_json(directory/'alignment.json',dict(text=TEST_TEXT,tokens=tokens,spans=spans,sample_rate=RATE))
            result['align']=dict(align,ready=True,verified_at=now(),probe=probe,tokens=tokens,
                test_audio_sha256=sha256(audio),alignment_file=str(directory/'alignment.json'))
        except TaskCancelledError:raise
        except (OSError,ValueError,RuntimeError) as error:
            result['align']['error']=redact(str(error));self.log(str(error))
        self.finish_stage('verify');return result

    def run(self,device='cu128',quality='lite',source='modelscope',*,check_only=False):
        request=plan(self.root,device,quality,source)
        if not request['hardware']['supported']:raise ValueError('自动配置第一版仅支持 Windows x64')
        self.root=ensure_root(self.root)
        with VoiceLease(self.root):
            remember_root(self.root)
            self.state=read_state(self.root)
            self.state.update(owner=OWNER,status='configuring',request={k:request[k] for k in ('root','device','quality','source','plan_id','version')},
                stages={},started_at=now(),hardware=request['hardware'])
            self.state.pop('error_detail',None);self.state.pop('validation',None)
            self.state.setdefault('source_history',[]).append(dict(source=source,time=now()))
            if self.state.get('pending',{}).get('plan_id')!=request['plan_id']:
                self.state['pending']=dict(plan_id=request['plan_id'])
            try:
                self.emit('detect','检查设备、空间和已有环境')
                if not check_only and request['hardware']['disk_free_bytes']<request['required_free_bytes']:
                    raise ValueError('空间不足，请选择空间更充足的磁盘；未删除已有环境或模型')
                self.finish_stage('detect')
                if check_only:
                    current=self.state.get('profiles',{}).get(request['plan_id'],self.state.get('active',{}))
                    compatible=current.get('lock_hashes')==request['locks'] and all(
                        current.get('models',{}).get(r,{}).get('revision')==model['revision'] for r,model in request['models'].items())
                    if current.get('plan_id')!=request['plan_id'] and not compatible:raise ValueError('尚无兼容的应用管理环境，请先自动配置或检查修复')
                    interpreters={r:Path(current[r]['python']) for r in ('tts','align')}
                else:
                    uv=self.bootstrap(request);python=self.python(uv,request)
                    self.emit('dependencies','检查并准备相互独立的配音与对齐环境')
                    interpreters={r:self.environment(uv,python,request,r) for r in ('tts','align')}
                    self.finish_stage('dependencies')
                self.emit('models','检查模型快照与文件完整性')
                models=self.models(request,download_missing=not check_only)
                settings=dict(device=device,tts=dict(python=str(interpreters['tts']),model=models['tts'],speaker='Vivian',style='',
                    device='cpu' if device=='cpu' else 'cuda:0',timeout=1200),
                    align=dict(python=str(interpreters['align']),model=models['align'],device='cpu' if device=='cpu' else 'cuda:0',language='Chinese',timeout=600))
                results=self.verify(settings)
                previous=deepcopy(self.state.get('active',{}));active=deepcopy(results)
                active.update(plan_id=request['plan_id'],request=self.state['request'],version=request['version'],models=request['models'],lock_hashes=request['locks'])
                self.state.update(active=active,validation=results,status='ready' if all(v['ready'] for v in results.values()) else 'needs_check',
                    message='配音与对齐已通过真实运行检查' if all(v['ready'] for v in results.values()) else pending_capabilities(results)+'；原配置保留',finished_at=now())
                self.state['previous_active']=previous
                profiles=self.state.setdefault('profiles',{})
                if all(v['ready'] for v in results.values()) or request['plan_id'] not in profiles:
                    profiles[request['plan_id']]=deepcopy(active)
                remember_root(self.root)
                return self.state
            except TaskCancelledError:
                if self.state.get('stage') in self.state['stages']:self.state['stages'][self.state['stage']]['status']='cancelled'
                self.state.update(status='cancelled',message='配置已取消，已下载内容保留；下次重新检查后继续');raise
            except Exception as error:
                detail=redact(str(error));stage=self.state.get('stage')
                if stage in self.state['stages']:self.state['stages'][stage]['status']='failed'
                message=detail if isinstance(error,ValueError) and len(detail)<180 and re.search('[\u3400-\u9fff]',detail) else {
                    'python':'运行环境准备未完成，可重试；已有缓存保留',
                    'dependencies':'依赖安装未完成，可重试；详细原因见日志，已有缓存保留',
                    'models':'模型下载未完成，可重试或切换所选来源；已有缓存保留',
                    'verify':'运行检查未完成；保留已有配置，请查看检查与修复'}.get(stage,'配置未完成，请检查保存位置和日志')
                self.state.update(status='needs_check' if check_only else 'failed',message=message,error_detail=detail);self.log(detail)
                if message!=detail:raise RuntimeError(message) from error
                raise
            finally:
                write_json(self.root/'deployment.json',self.state)
                self.on_progress(dict(status=self.state['status'],message=self.state['message']))

    def run_existing(self,tts,align):
        from contextlib import ExitStack
        from services.voice_storage import managed_root,validate_voice_locations
        for settings in (tts,align):validate_voice_locations(settings)
        self.root=ensure_root(self.root)
        if not any(settings.get('python') and settings.get('model') for settings in (tts,align)):
            raise ValueError('请至少提供配音环境和模型；也可以返回自动配置')
        for role,settings in (('tts',tts),('align',align)):
            python=Path(settings.get('python') or '.')
            if settings.get('python') and (not python.is_file() or not python.name.lower().startswith('python')):
                raise ValueError('请选择已有'+('配音' if role=='tts' else '对齐')+'环境的解释器')
        roots={self.root}
        for settings in (tts,align):
            value=managed_root(settings['python']) if settings.get('python') else None
            if value:roots.add(value)
        with ExitStack() as stack:
            for root in sorted(roots):stack.enter_context(VoiceLease(root))
            self.state=read_state(self.root);previous=deepcopy(self.state.get('active',{}))
            self.state.update(status='configuring',stages={},message='检查用户提供的环境，不安装或修改依赖')
            self.state.pop('error_detail',None);self.state.pop('validation',None)
            try:
                settings=dict(device='existing',tts=dict(tts),align=dict(align))
                result=self.verify(settings,managed=False)
                self.state.update(status='ready' if all(v['ready'] for v in result.values()) else 'needs_check',
                    active=dict(result,external=True,version='existing-checked-v1'),validation=result,previous_active=previous,
                    message='已有环境检查通过' if all(v['ready'] for v in result.values()) else pending_capabilities(result)+'；未修改依赖或模型',finished_at=now())
                remember_root(self.root);return self.state
            except TaskCancelledError:
                self.state.update(status='cancelled',message='检查已取消，用户环境保持不变');raise
            except Exception as error:
                self.state.update(status='failed',message=redact(str(error)));raise
            finally:write_json(self.root/'deployment.json',self.state)


def diagnostics(root,target):
    root=ensure_root(root)
    state=read_state(root)
    state['diagnostic_export_version']=1
    log=Path(root)/'deployment.log'
    state['deployment_log']=log.read_text(encoding='utf-8',errors='replace') if log.exists() else ''
    state['module_checks']={}
    for role in ('tts','align'):
        path=Path(root)/'checks'/(role+'-modules.json')
        if path.exists():
            try:state['module_checks'][role]=json.loads(path.read_text(encoding='utf-8'))
            except ValueError:state['module_checks'][role]=dict(error='检查记录损坏')
    def clean(value):
        if isinstance(value,str):return redact(value)
        if isinstance(value,list):return [clean(v) for v in value]
        if isinstance(value,dict):return {k:'[已隐藏]' if re.fullmatch(r'(?i)authorization|password|token|api[_-]?key|secret|access_token|refresh_token',k) else clean(v) for k,v in value.items()}
        return value
    write_json(Path(target),clean(state))
