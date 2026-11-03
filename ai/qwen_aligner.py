"""Offline, isolated Qwen3-ForcedAligner process; loaded only for an alignment batch."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from resources import bundled_resource_path


class QwenForcedAligner:
    def __init__(self, settings, on_state=None, *, acquire_environment=True):
        self.settings = dict(settings)
        self.on_state = on_state or (lambda message: None)
        self.acquire_environment=acquire_environment

    def align_batch(self, jobs, token, on_result):
        from contextlib import nullcontext
        from services.voice_storage import environment_guard
        guard=environment_guard(self.settings.get('python','')) if self.acquire_environment else nullcontext()
        with guard:self._align_batch(jobs,token,on_result)

    def _align_batch(self, jobs, token, on_result):
        settings = self.settings
        from services.voice_storage import validate_voice_locations
        validate_voice_locations(settings)
        from services.roundup_sync import validate_aligner
        validate_aligner(settings)
        python, model = Path(settings.get('python') or '.'), Path(settings.get('model') or '.')
        if not settings.get('python') or not python.is_file() or not python.name.lower().startswith('python'):
            raise ValueError('尚未配置强制对齐运行环境，请在语音设置中配置')
        if not settings.get('model') or not model.is_dir() or not (model/'config.json').is_file():
            raise ValueError('尚未配置本地 Qwen3-ForcedAligner 模型，请在语音设置中配置')
        timeout = float(settings.get('timeout',600))
        if not 10 <= timeout <= 3600:raise ValueError('对齐超时应为10至3600秒')
        worker = bundled_resource_path('tools','qwen_align_worker.py')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory);request=root/'request.json';status=root/'status.jsonl'
            request.write_text(json.dumps(dict(settings=dict(settings,model=str(model.resolve())),jobs=jobs,
                status=str(status)),ensure_ascii=False),encoding='utf-8')
            with (root/'worker.log').open('w+b') as log:
                token.check()
                process = subprocess.Popen([str(python.resolve()),'-I','-X','utf8',str(worker),'--request',str(request)],
                    stdout=subprocess.DEVNULL,stderr=log,
                    env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1'),
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                from services.video_download import _WindowsJob,_stop_process
                process_job=_WindowsJob(process) if os.name=='nt' else None
                processed, completed = 0, set()
                deadline=time.monotonic()+timeout
                self.on_state('正在加载强制对齐模型（配音模型已卸载）')
                try:
                    while True:
                        token.check()
                        records=status.read_bytes().split(b'\n')[:-1] if status.exists() else []
                        for raw in records[processed:]:
                            event=json.loads(raw.decode('utf-8'));state=event.get('state');identifier=event.get('id')
                            if state=='fatal':raise RuntimeError('强制对齐失败：'+str(event.get('error','模型加载失败')))
                            if state=='running':
                                deadline=time.monotonic()+timeout
                                self.on_state('正在对齐实际音频与最终朗读文本')
                            elif state in ('done','error'):
                                if identifier not in {j['id'] for j in jobs} or identifier in completed:
                                    raise RuntimeError('强制对齐响应身份不匹配')
                                on_result(identifier,event.get('tokens'),event.get('error',''))
                                completed.add(identifier)
                        processed=len(records)
                        if process.poll() is not None:
                            # Read a final time after exit so the last append cannot be missed.
                            final=status.read_bytes().split(b'\n')[:-1] if status.exists() else []
                            if len(final)>processed:continue
                            if process.returncode or len(completed)!=len(jobs):
                                log.seek(0,2);log.seek(max(0,log.tell()-1600))
                                raise RuntimeError('对齐进程未完整完成：'+log.read().decode('utf-8',errors='replace'))
                            break
                        if time.monotonic()>deadline:raise RuntimeError('强制对齐超时，进程将终止')
                        time.sleep(.05)
                finally:
                    if process.poll() is None:
                        _stop_process(process,process_job)
                    if process_job is not None:process_job.close()
                    self.on_state('强制对齐进程已退出；已完成结果保留')
