"""Cancellable FFmpeg operations shared by audio preparation and final muxing."""
import subprocess
import tempfile
import time


def run_media(arguments, token):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            while process.poll() is None:
                token.check()
                time.sleep(.05)
            token.check()
            if process.returncode:
                log.seek(0,2);log.seek(max(0,log.tell()-1600))
                raise RuntimeError('媒体处理失败：'+log.read().decode('utf-8',errors='replace'))
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=10)
