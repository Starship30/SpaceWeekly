"""Run in the separate qwen-asr environment, never imported by the desktop app."""
import argparse
import json
import os
from pathlib import Path


def load_aligner_class():
    if os.name=='nt':
        import locale
        # DyNet opens UTF-8 filenames through the C runtime, independently of Python's UTF-8 mode.
        try:locale.setlocale(locale.LC_ALL,'.UTF8')
        except locale.Error:pass  # Older user-provided runtimes can still work with ASCII paths; inference remains mandatory.
    from qwen_asr import Qwen3ForcedAligner
    return Qwen3ForcedAligner


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    args=parser.parse_args()
    request=json.loads(Path(args.request).read_text(encoding='utf-8'))
    settings=request['settings'];status=Path(request['status'])
    def emit(state,**fields):
        with status.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(dict(state=state,**fields),ensure_ascii=False)+'\n');handle.flush()
    try:
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        import torch
        Qwen3ForcedAligner=load_aligner_class()
        model=Qwen3ForcedAligner.from_pretrained(settings['model'],
            dtype=torch.float32 if settings.get('device','cpu')=='cpu' else torch.bfloat16,
            device_map=settings.get('device','cpu'),attn_implementation='sdpa',local_files_only=True)
        for job in request['jobs']:
            emit('running',id=job['id'])
            try:
                result=model.align(audio=job['audio'],text=job['text'],language=settings.get('language','Chinese'))
                if len(result)!=1:raise ValueError('对齐结果数量不匹配')
                emit('done',id=job['id'],tokens=[dict(text=x.text,start_time=float(x.start_time),end_time=float(x.end_time)) for x in result[0]])
            except Exception as error:
                emit('error',id=job['id'],error=str(error))
    except ImportError as error:
        emit('fatal',error='独立对齐环境缺少 qwen-asr 或其依赖，请检查语音设置中的运行环境',diagnostic=str(error))
        raise
    except Exception as error:
        emit('fatal',error=str(error))
        raise


if __name__=='__main__':main()
