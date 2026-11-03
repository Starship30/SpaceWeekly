"""Executed only by the target independent Python; never imports ML into the desktop."""
import argparse
import importlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import re
import sys


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--device',required=True)
    parser.add_argument('--role',choices=['tts','align'],required=True);args=parser.parse_args()
    result=dict(python=sys.version,executable=sys.executable,architecture=platform.machine(),ready=False)
    try:
        result['packages']={re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in distributions() if d.metadata['Name']}
        import torch
        result['torch']=torch.__version__;result['cuda_build']=torch.version.cuda
        if args.device.startswith('cuda'):
            if not torch.cuda.is_available():raise RuntimeError('目标环境没有可用的 CUDA 设备')
            value=torch.ones((32,32),device=args.device);output=value@value;torch.cuda.synchronize()
            if output[0,0].item()!=32:raise RuntimeError('GPU 运算检查失败')
            prop=torch.cuda.get_device_properties(args.device)
            result['gpu']=dict(name=prop.name,memory_bytes=prop.total_memory,capability=[prop.major,prop.minor])
        else:
            if (torch.ones(4)@torch.ones(4)).item()!=4:raise RuntimeError('CPU 运算检查失败')
        if args.role=='align':
            import runpy
            runpy.run_path(str(Path(__file__).with_name('qwen_align_worker.py')))['load_aligner_class']()
        else:importlib.import_module('qwen_tts')
        result['ready']=True
    except Exception as error:
        result['error']=str(error)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if not result['ready']:sys.exit(1)


if __name__=='__main__':main()
