"""A fake inference backend running the REAL worker protocol; never voice-quality evidence."""
from pathlib import Path


def worker_source():
    worker = str(Path(__file__).resolve().parents[1] / 'tools/qwen_tts_worker.py')
    return f'''
import importlib.util, time, wave
spec=importlib.util.spec_from_file_location('qwen_worker_under_test', {worker!r})
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class Voice:
 def get_supported_speakers(self):return ['vivian','serena']
 def generate_custom_voice(self, text, **kwargs):
  if text=='fail':raise ValueError('mock inference failure')
  if text=='slow':time.sleep(60)
  return [b'\\0\\0'*12000],24000
class SoundFile:
 def write(self,path,pcm,rate,**kwargs):
  with wave.open(path,'wb') as target:
   target.setparams((1,2,rate,0,'NONE','NONE'));target.writeframes(pcm)
def fake_load(self,job):
 self.model=Voice();self.sf=SoundFile()
module.QwenRuntime._load=fake_load
module.serve()
'''
