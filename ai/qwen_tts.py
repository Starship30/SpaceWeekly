"""One lazy local CustomVoice worker shared by preview and batch synthesis."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import RLock
import time
from uuid import uuid4

from resources import bundled_resource_path
from services.cancellation import no_cancel, TaskCancelledError


def capabilities(model_path):
    root = Path(model_path).expanduser()
    if not model_path or not root.is_dir() or not (root / 'config.json').is_file():
        raise ValueError('请选择已下载的本地 Qwen3-TTS CustomVoice 模型目录（含 config.json）')
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    if not isinstance(config, dict) or config.get('tts_model_type') != 'custom_voice':
        raise ValueError('仅支持 CustomVoice，不支持 Base 克隆或 VoiceDesign')
    talker = config.get('talker_config') or {}
    speakers = (talker.get('spk_id') or {}) if isinstance(talker, dict) else {}
    if not isinstance(speakers, (dict, list)):
        speakers = {}
    return dict(style=config.get('tts_model_size') == '1b7', speakers=[s for s in speakers if isinstance(s, str)],
                size=str(config.get('tts_model_size') or 'unknown'), model=str(root.resolve()))


class QwenTTSProvider:
    def __init__(self, settings, on_state=None, *, acquire_environment=True):
        self.settings = dict(settings)
        self.on_state = on_state or (lambda message: None)
        self.process = None
        self._runtime = self._log = self._worker_key = None
        self._loaded = False
        self._lock = RLock()
        self.load_count = 0
        self.runtime_info={};self.acquire_environment=acquire_environment;self._environment_lease=None
        self._job=None

    @property
    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def configure(self, settings):
        from services.voice_storage import validate_voice_locations
        validate_voice_locations(settings)
        self.settings = dict(settings)

    def unload(self, *, force=False):
        """Return only after OS confirms worker exit; call from a background thread."""
        with self._lock:
            process = self.process
            if process is not None and process.poll() is None:
                self.on_state('正在终止当前生成并卸载模型' if force else '正在卸载语音模型')
                try:
                    if force:
                        from services.video_download import _stop_process
                        _stop_process(process,self._job)
                    elif process.stdin is not None:
                        process.stdin.close()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        from services.video_download import _stop_process
                        _stop_process(process,self._job)
                except (OSError, subprocess.TimeoutExpired) as error:
                    if process.poll() is None:
                        self.on_state('停止尚未确认，工作进程仍在运行，请重试卸载')
                        raise RuntimeError('未确认语音进程退出，不能声称已停止') from error
            if process is not None and process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            self.process, self._loaded, self._worker_key = None, False, None
            if self._job is not None:self._job.close();self._job=None
            if self._log is not None:
                self._log.close()
                self._log = None
            if self._runtime is not None:
                try:self._runtime.cleanup()
                except OSError:self.on_state('语音进程已退出，临时日志暂未清理；音频已保留')
                self._runtime = None
            if self._environment_lease is not None:
                self._environment_lease.__exit__(None,None,None);self._environment_lease=None
            self.on_state('语音模型已卸载；下次生成时按需加载')

    def _start(self, info, output_dir):
        python = Path(self.settings['python'])
        if not self.settings['python'] or not python.is_file() or not python.name.lower().startswith('python'):
            raise ValueError('请配置独立 TTS 环境的 python.exe；安装方法见 tools/ROUNDUP.md')
        worker = bundled_resource_path('tools', 'qwen_tts_worker.py')
        if not worker.is_file():
            raise ValueError('缺少本地 TTS 工作进程脚本')
        # Metadata detects replaced weights without hashing multi-GB files per paragraph.
        files = [(str(p.relative_to(info['model'])), p.stat().st_size, p.stat().st_mtime_ns)
                 for p in sorted(Path(info['model']).rglob('*')) if p.is_file()]
        key = (str(python.resolve()), info['model'], self.settings.get('device', 'cpu'), files,
               worker.stat().st_mtime_ns, python.stat().st_mtime_ns)
        if self.is_running and key == self._worker_key:
            return
        self.unload()
        if self.acquire_environment:
            from services.voice_storage import environment_guard
            self._environment_lease=environment_guard(self.settings['python']);self._environment_lease.__enter__()
        self._runtime = tempfile.TemporaryDirectory(dir=output_dir,ignore_cleanup_errors=True)
        self._log = (Path(self._runtime.name) / 'worker.log').open('w+b')
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUTF8='1')
        try:
            self.process = subprocess.Popen([str(python.resolve()), '-I', '-X', 'utf8', str(worker), '--serve'],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log, env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if os.name=='nt':
                from services.video_download import _WindowsJob
                self._job=_WindowsJob(self.process)
            self._worker_key, self.load_count = key, 0
        except BaseException:
            self.unload(force=True)
            raise

    def _failure_detail(self):
        self._log.flush()
        with (Path(self._runtime.name) / 'worker.log').open('rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 2500))
            return handle.read().decode('utf-8', errors='replace')

    def synthesize(self, request, output_path, cancel_check=no_cancel):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('语音工作进程正在处理另一项请求')
        inflight = False
        try:
            cancel_check()
            from services.voice_storage import validate_voice_locations
            validate_voice_locations(self.settings)
            info = capabilities(self.settings['model'])
            if request.style and not info['style']:
                raise ValueError('当前模型不支持风格指令；0.6B 请留空')
            if info['speakers'] and request.voice.casefold() not in {s.casefold() for s in info['speakers']}:
                raise ValueError('说话人不在当前模型支持列表中')
            self._start(info, Path(output_path).parent)
            with tempfile.TemporaryDirectory(dir=Path(output_path).parent,ignore_cleanup_errors=True) as directory:
                identifier = uuid4().hex
                status = Path(directory) / 'status.jsonl'
                payload = dict(asdict(request), id=identifier, model=info['model'],
                    device=self.settings.get('device', 'cpu'), output=str(Path(output_path).resolve()), status=str(status.resolve()))
                self.on_state('复用已加载模型，正在生成本段' if self._loaded else '首次按需加载语音模型')
                inflight = True
                self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
                self.process.stdin.flush()
                deadline = time.monotonic() + max(10, min(3600, float(self.settings.get('timeout', 600))))
                previous = ''
                while True:
                    cancel_check()
                    records = status.read_bytes().split(b'\n')[:-1] if status.exists() else []
                    if records:
                        event = json.loads(records[-1].decode('utf-8'))
                        if event.get('id') != identifier:
                            raise RuntimeError('语音工作进程响应 ID 不匹配')
                        self.load_count = event.get('load_count', self.load_count)
                        self._loaded = bool(event.get('model_loaded'))
                        if event.get('capabilities'):self.runtime_info=event['capabilities']
                        state = event.get('state')
                        if state != previous:
                            self.on_state({'loading': '正在加载语音模型', 'generating': '模型已就绪，正在生成音频',
                                'ready':'模型已加载，音色已读取',
                                'done': '本段生成完成；模型驻留，可继续复用', 'error': '本段生成失败'}.get(state, str(state)))
                            previous = state
                        if state == 'done':
                            inflight = False
                            return
                        if state == 'error':
                            inflight = False
                            if not self._loaded:
                                self.unload()
                            raise RuntimeError('Qwen3-TTS 请求失败：' + str(event.get('error', '未知错误')))
                    if not self.is_running:
                        raise RuntimeError('语音工作进程退出：' + self._failure_detail())
                    if time.monotonic() > deadline:
                        self.unload(force=True)
                        raise RuntimeError('TTS 超时，工作进程已终止；下次需重新加载')
                    time.sleep(.05)
        except TaskCancelledError:
            self.unload(force=True)
            raise
        except (OSError, ValueError, RuntimeError):
            if inflight and self.is_running:
                self.unload(force=True)
            self.on_state('语音失败；模型仍驻留' if self.is_running and self._loaded else '语音失败；模型未就绪')
            raise
        except BaseException:
            if inflight and self.is_running:
                self.unload(force=True)
            raise
        finally:
            self._lock.release()
