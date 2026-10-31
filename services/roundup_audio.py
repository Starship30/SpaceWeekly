"""Semantic-paragraph WAV caching and measured timing, independent of the UI."""
from pathlib import Path
import hashlib
import json
import re
import wave

from ai.speech import SpeechRequest, generate_speech
from resources import resource_path, bundled_resource_path
from services.cancellation import TaskCancelledError
from services.launch_data import write_json
from services.launch_roundup import digest, narration_text


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def speech_text(text, rules):
    if not isinstance(rules, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in rules.items()):
        raise ValueError('发音规则应为非空原文到读法的 JSON 对象')
    if not rules:
        result = text
    else:
        # One pass prevents replacement rules from rewriting each other's output.
        result = re.sub('|'.join(re.escape(k) for k in sorted(rules, key=len, reverse=True)),
                        lambda m: rules[m.group()], text)
    if re.search(r'https?://|www\.', result, re.I):
        raise ValueError('发音替换不能把来源地址带入朗读文本')
    return result


def audio_key(draft, segment):
    settings = dict(draft['tts'])
    root = Path(settings.get('model') or '.')
    stamps = []
    if settings.get('model') and root.is_dir():
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            stamps.append((str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns))
    worker = bundled_resource_path('tools', 'qwen_tts_worker.py')
    worker_hash = file_hash(worker) if worker.is_file() else 'missing-worker'
    runtime = Path(settings.get('python') or '.')
    runtime_stamp = runtime.stat().st_mtime_ns if runtime.is_file() else None
    return digest(dict(engine='qwen-customvoice-subprocess-v1', text=reading_text(draft, segment),
                       settings=settings, model_files=stamps, worker=worker_hash,
                       runtime_stamp=runtime_stamp, rules=draft['pronunciations']))


def reading_text(draft, segment):
    """Exact TTS input; names, source facts and screen copy are never rewritten."""
    return speech_text(narration_text(segment), draft['pronunciations'])


def wav_info(path):
    with wave.open(str(path), 'rb') as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        if frames <= 0 or rate <= 0 or audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise ValueError('配音必须为非空单声道 PCM16 WAV')
        audio.setpos(frames - 1)
        if len(audio.readframes(1)) != 2:
            raise ValueError('WAV 数据不完整')
        return frames, rate


def current_audio(draft, segment):
    value = segment.get('audio')
    if not isinstance(value, dict) or value.get('key') != audio_key(draft, segment):
        return None
    path = Path(value.get('path', ''))
    try:
        frames, rate = wav_info(path)
        if file_hash(path) != value.get('sha256'):
            return None
        return dict(value, duration_seconds=frames / rate, sample_rate=rate)
    except (OSError, ValueError, wave.Error, EOFError):
        return None


def generate_audio(draft, provider, token, *, segment_id=None, force=False, on_progress=None):
    """Continue on per-segment errors, keep successes, propagate cancellation."""
    errors = []
    for segment in draft['segments']:
        token.check()
        if segment_id and segment['id'] != segment_id:
            continue
        try:
            key = audio_key(draft, segment)
            directory = resource_path('cache', 'roundup_audio', key)
            index = directory / 'audio.json'
            if not force and not current_audio(draft, segment) and index.exists():
                cached = json.loads(index.read_text(encoding='utf-8'))
                candidate = dict(segment, audio=cached)
                if current_audio(draft, candidate):
                    segment['audio'] = cached
            if force or not current_audio(draft, segment):
                text = reading_text(draft, segment)
                result = generate_speech(SpeechRequest(text,
                    model=draft['tts']['model'], voice=draft['tts']['speaker'], style=draft['tts'].get('style', '')),
                    directory, provider=provider, cancel_check=token.check)
                frames, rate = wav_info(result.path)
                segment['audio'] = dict(key=key, path=str(result.path.resolve()), duration_seconds=frames / rate,
                                        sample_rate=rate, sha256=file_hash(result.path), reading_text=text)
                write_json(index, segment['audio'])
            segment.pop('audio_error', None)
        except TaskCancelledError:
            raise
        except (OSError, ValueError, RuntimeError, wave.Error) as error:
            segment['audio_error'] = str(error)
            errors.append((segment['id'], str(error)))
        finally:
            # A cancelled batch can resume from every already successful paragraph.
            write_json(resource_path('output', 'roundup-drafts', draft['id'] + '.json'), draft)
        if on_progress:
            on_progress(segment['id'])
    return errors
