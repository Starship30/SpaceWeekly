"""Use bundled media tools without implicit working-directory executables."""
import os
import sys

# Packaged builds use Python 3.12+, whose which() honors this process-only flag.
os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
directory = os.path.join(sys._MEIPASS, "ffmpeg")
if any(os.path.isfile(os.path.join(directory, name))
       for name in ("ffmpeg.exe", "ffprobe.exe")):
    existing = os.environ.get("PATH")
    os.environ["PATH"] = directory + os.pathsep + existing if existing else directory
