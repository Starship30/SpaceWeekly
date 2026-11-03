"""Frozen subprocess route for cancellable framework network operations."""
import json
from pathlib import Path
from services.launch_data import write_json


def run(request):
    from services.updates import worker_action
    path=Path(request);directory=path.parent
    try:
        action=json.loads(path.read_text(encoding='utf-8'))['action']
        result=worker_action(action,lambda value:write_json(directory/'progress.json',dict(percent=max(0,min(100,int(value))))))
    except Exception as error:result=dict(error=str(error))
    write_json(directory/'result.json',result)
