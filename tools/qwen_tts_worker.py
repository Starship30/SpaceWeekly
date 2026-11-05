"""Persistent offline Qwen worker; ML libraries load only on the first request."""
import gc
import json
from pathlib import Path
import sys


class QwenRuntime:
    def __init__(self):
        self.model = self.sf = self.torch = self.key = None
        self.load_count = 0
        self.capabilities={}
        self.reference_key=self.reference_prompt=None

    def _load(self, job):
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel

        self.torch, self.sf = torch, sf
        self.model = Qwen3TTSModel.from_pretrained(job['model'], device_map=job['device'],
            dtype=torch.float32 if job['device'] == 'cpu' else torch.bfloat16,
            attn_implementation='sdpa', local_files_only=True)

    def release(self):
        self.model, self.key = None, None
        self.capabilities={}
        self.reference_key=self.reference_prompt=None
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()

    def generate(self, job, notify):
        config = json.loads((Path(job['model']) / 'config.json').read_text(encoding='utf-8'))
        kind=config.get('tts_model_type')
        if kind not in ('custom_voice','base'):
            raise ValueError('Only local CustomVoice and Base models are supported')
        if job.get('style') and (kind!='custom_voice' or config.get('tts_model_size') != '1b7'):
            raise ValueError('This model does not support instruct')
        if job.get('device') not in ('cpu', 'cuda:0'):
            raise ValueError('Device must be cpu or cuda:0')
        key = (str(Path(job['model']).resolve()), job['device'])
        if key != self.key:
            self.release()
            notify('loading')
            try:
                self._load(job)
                self.key = key
                self.load_count += 1
            except BaseException:
                self.release()
                raise
        speakers = (self.model.get_supported_speakers() or []) if kind=='custom_voice' else []
        if speakers and job['voice'].casefold() not in {v.casefold() for v in speakers}:
            raise ValueError('Unsupported speaker: ' + job['voice'])
        self.capabilities=dict(speakers=speakers,style=kind=='custom_voice' and config.get('tts_model_size')=='1b7',kind=kind,device=job['device'])
        notify('ready')
        notify('generating')
        if kind=='base':
            key=(job['reference_audio'],job['reference_sha256'],job['reference_text'])
            if key!=self.reference_key:
                self.reference_prompt=self.model.create_voice_clone_prompt(ref_audio=key[0],ref_text=key[2],x_vector_only_mode=False)
                self.reference_key=key
            wavs,rate=self.model.generate_voice_clone(text=job['text'],language='Chinese',voice_clone_prompt=self.reference_prompt)
        else:
            wavs, rate = self.model.generate_custom_voice(text=job['text'], language='Chinese', speaker=job['voice'],
                                                        **({'instruct': job['style']} if job.get('style') else {}))
        self.sf.write(job['output'], wavs[0], rate, subtype='PCM_16')


def serve():
    sys.stdin.reconfigure(encoding='utf-8')
    runtime = QwenRuntime()
    try:
        for line in sys.stdin:
            job = json.loads(line)
            status = Path(job['status'])
            def notify(state, error=''):
                # Append-only records avoid Windows replace/read sharing violations.
                event=dict(id=job['id'],state=state,error=error,load_count=runtime.load_count,
                    model_loaded=runtime.model is not None,capabilities=runtime.capabilities)
                with status.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(event,ensure_ascii=False)+'\n')
            try:
                runtime.generate(job, notify)
                notify('done')
            except Exception as error:
                notify('error', str(error))
    finally:
        runtime.release()


if __name__ == '__main__':
    if sys.argv[1:] != ['--serve']:
        raise SystemExit('Run with --serve in the configured TTS Python environment')
    serve()
