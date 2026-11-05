"""Semantic-paragraph WAV caching and measured timing, independent of the UI."""
from pathlib import Path
import hashlib
import json
import wave
import math

from ai.speech import SpeechRequest, generate_speech
from resources import resource_path, bundled_resource_path
from services.cancellation import TaskCancelledError
from services.launch_data import write_json
from services.launch_roundup import digest, narration_text
from services.roundup_reading import final_reading
from services.roundup_reading import VERSION as READING_VERSION


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def speech_text(text, rules):
    return final_reading(text, rules)


def content_namespace(draft):
    return 'news' if draft.get('content_type')=='news' else 'roundup'


def audio_key(draft, segment, *, legacy_rules=False):
    settings = dict(draft['tts'])
    from services.voice_profiles import PREFIX,load
    custom=settings.get('speaker','').startswith(PREFIX)
    if custom:
        profile=load(settings['speaker'])
        settings.update(model=profile['model'],style='',reference_hash=profile['sha256'],
                        reference_text=profile['text'])
    root = Path(settings.get('model') or '.')
    stamps = []
    if settings.get('model') and root.is_dir():
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            stamps.append((str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns))
    worker = bundled_resource_path('tools', 'qwen_tts_worker.py')
    # The preset synthesis path is unchanged. Retain the published preset cache identity.
    worker_hash = (file_hash(worker) if worker.is_file() else 'missing-worker') if custom else '506842d175efe027da2e223ab21f8795dac9c483a9bb3b068ee18eb0d7dde8e5'
    runtime = Path(settings.get('python') or '.')
    runtime_stamp = runtime.stat().st_mtime_ns if runtime.is_file() else None
    identity = dict(engine='qwen-customvoice-subprocess-v1', text=reading_text(draft, segment),
                    settings=settings, model_files=stamps, worker=worker_hash, runtime_stamp=runtime_stamp)
    if content_namespace(draft)=='news':identity.update(content_type='news',news_id=segment['news_id'])
    if legacy_rules:
        identity['rules'] = draft['pronunciations']
    return digest(identity)


def reading_text(draft, segment):
    """Exact TTS input; names, source facts and screen copy are never rewritten."""
    text=narration_text(segment)
    if content_namespace(draft)=='news':
        from translator.dictionary import dictionary_replace,load_dictionary
        text=dictionary_replace(text,load_dictionary())[0]
    return speech_text(text, draft['pronunciations'])


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
    if not isinstance(value, dict):
        return None
    try:
        key = audio_key(draft, segment)
        if value.get('key') != key and value.get('key') != audio_key(draft, segment, legacy_rules=True):
            return None
        path = Path(value.get('path', ''))
        frames, rate = wav_info(path)
        if file_hash(path) != value.get('sha256'):
            return None
        return dict(value, key=key, duration_seconds=frames / rate, sample_rate=rate, sample_count=frames)
    except (OSError, ValueError, wave.Error, EOFError):
        return None


def segment_timing(draft,segment):
    """One duration policy for preview and exported visuals, at paragraph precision."""
    audio=current_audio(draft,segment)
    duration=audio['duration_seconds'] if audio else float(segment.get('preview_seconds',12))
    if not math.isfinite(duration) or not .1 <= duration <= 600:
        raise ValueError('单段时长应为0.1至600秒')
    return dict(duration_seconds=duration,timing_source='audio' if audio else 'editable_preview',audio=audio)


def generate_audio(draft, provider, token, *, segment_id=None, force=False, on_progress=None):
    """Continue on per-segment errors, keep successes, propagate cancellation."""
    errors = []
    for segment in draft['segments']:
        token.check()
        if segment_id and segment['id'] != segment_id:
            continue
        try:
            key = audio_key(draft, segment)
            existing = current_audio(draft, segment)
            if existing:
                segment['audio'] = existing
            directory = resource_path('cache', content_namespace(draft)+'_audio', key)
            index = directory / 'audio.json'
            if not force and not current_audio(draft, segment) and index.exists():
                cached = json.loads(index.read_text(encoding='utf-8'))
                candidate = dict(segment, audio=cached)
                if current_audio(draft, candidate):
                    segment['audio'] = cached
            if force or not current_audio(draft, segment):
                text = reading_text(draft, segment)
                from services.roundup_sync import reading_units
                try:
                    segment['units'] = reading_units(draft, segment)
                except ValueError:
                    # Legacy or edited narration remains synthesizable, with synchronization explicitly pending.
                    pass
                result = generate_speech(SpeechRequest(text,
                    model=draft['tts']['model'] or 'local-reference', voice=draft['tts']['speaker'], style=draft['tts'].get('style', '')),
                    directory, provider=provider, cancel_check=token.check)
                frames, rate = wav_info(result.path)
                segment['audio'] = dict(key=key, path=str(result.path.resolve()), duration_seconds=frames / rate,
                                        sample_rate=rate, sample_count=frames, sha256=file_hash(result.path), reading_text=text,
                                        reading_rules_version=READING_VERSION)
                if content_namespace(draft)=='news':segment['audio'].update(content_type='news',news_id=segment['news_id'])
                write_json(index, segment['audio'])
            segment.pop('audio_error', None)
        except TaskCancelledError:
            raise
        except (OSError, ValueError, RuntimeError, wave.Error) as error:
            segment['audio_error'] = str(error)
            errors.append((segment['id'], str(error)))
        finally:
            # A cancelled batch can resume from every already successful paragraph.
            write_json(resource_path('output', content_namespace(draft)+'-drafts', draft['id'] + '.json'), draft)
        if on_progress:
            on_progress(segment['id'])
    return errors
