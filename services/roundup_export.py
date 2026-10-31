"""Non-overwriting Premiere handoff packages, measured WAV timeline and paragraph SRT."""
from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path
import shutil
from uuid import uuid4
import wave

from services.launch_data import write_json
from services.launch_roundup import (validate_draft, statistics, material_status, task_names,
                                    narration_text, current_narration, narration_sources)
from services.roundup_audio import current_audio, wav_info, reading_text
from services.roundup_style import timezone_label
from utils.output_files import reserved_output


def _srt_time(value):
    milliseconds = round(value * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def _merge(rows, directory, pause, token):
    formats = [wav_info(directory / row['audio_file'])[1] for row in rows]
    if len(set(formats)) != 1:
        raise ValueError('段落采样率不同，请使用同一语音设置重新生成')
    rate = formats[0]
    with reserved_output(directory, '.narration.wav') as (final, temporary):
        with wave.open(str(temporary), 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            for index, row in enumerate(rows):
                with wave.open(str(directory / row['audio_file']), 'rb') as audio:
                    while True:
                        token.check()
                        chunk = audio.readframes(65536)
                        if not chunk:
                            break
                        target.writeframesraw(chunk)
                if index + 1 < len(rows):
                    target.writeframesraw(b'\x00\x00' * round(pause * rate))
        token.check()
    return final


def export_package(draft, output_dir, token, *, visuals=True, renderer=None):
    validate_draft(draft)
    if not draft['segments']:
        raise ValueError('请先生成或编辑段落')
    if any(not s['spoken'].strip() or not s['screen'].strip() for s in draft['segments']):
        raise ValueError('请补全每段口播与屏幕文字；未完成内容仍可保存草稿')
    token.check()
    directory = Path(output_dir) / ('SpaceWeekly_roundup_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    manifest = dict(version=1, state='exporting', subtitle_precision='paragraph', start=draft['start'], end=draft['end'],
                    timezone=draft.get('timezone', 'Asia/Shanghai'), statistics=statistics(draft), segments=[], all_audio_current=False)
    states = {i['ll2_id']: material_status(i) for i in draft['snapshot']['items']}
    manifest['material_states'] = states
    write_json(directory / 'material_states.json', states)
    write_json(directory / 'draft.json', draft)
    write_json(directory / 'snapshot.json', draft['snapshot'])
    if draft.get('writing_input'):
        write_json(directory / 'writing_input.json', draft['writing_input'])
    if draft.get('polish_history'):
        write_json(directory / 'polish_history.json', draft['polish_history'])
    lines = ['# 每周发射综述', f"{timezone_label(draft)} {draft['start']} 至 {draft['end']}", '字幕精度：段落级；不提供逐字对齐。', '']
    for segment in draft['segments']:
        voice = current_narration(segment)
        reading_status = ('AI口播润色（机器复核，非人工确认）' if voice and voice.get('origin') == 'ai_polish' else
                          '人工朗读编辑（未经事实复核）' if voice else
                          '润色已过期，使用事实原稿' if segment.get('narration') else '使用事实原稿（无口播润色）')
        lines += [f"## {segment['id']} · {segment['kind']}", narration_text(segment), '', '原始事实稿：' + segment['spoken'],
                  '屏幕文字：' + segment['screen'], '最终TTS输入：' + reading_text(draft, segment),
                  '朗读状态：' + reading_status,
                  '关联任务：' + ', '.join(segment['task_ids']), '人工编辑：' + str(segment.get('edited', False)),
                  '稿件来源：' + segment.get('origin', 'unknown'), '回退原因：' + segment.get('generation_reason', '')]
        lines += ['资料状态：' + i + ' · ' + ' / '.join(states[i][key] for key in
                  ('preparation_label', 'source_label', 'independent_label', 'human_label')) for i in segment['task_ids']]
        lines += ['出处：' + s['url'] + '\n> ' + s['quote'] for s in narration_sources(segment)]
        lines += ['']
    (directory / 'script.md').write_text('\n'.join(lines), encoding='utf-8')
    audio = [current_audio(draft, s) for s in draft['segments']]
    manifest['all_audio_current'] = all(a is not None for a in audio)
    cursor, srt = 0., []
    try:
        reading_rows = [dict(id=s['id'], task_ids=s['task_ids'], formal_names=task_names(draft, s['task_ids']),
            fact_text=s['spoken'], screen_text=s['screen'], narration_text=narration_text(s), reading_text=reading_text(draft, s),
            narration_status='current' if current_narration(s) else 'stale' if s.get('narration') else 'original',
            narration_origin=(current_narration(s) or {}).get('origin', 'original'),
            narration_review=(current_narration(s) or {}).get('review'),
            source_refs=narration_sources(s), material_states={i: states[i] for i in s['task_ids']}) for s in draft['segments']]
        write_json(directory / 'reading_map.json', dict(version=1, alignment_status='not_performed',
            measured_audio=manifest['all_audio_current'], pronunciation_rules=draft['pronunciations'], segments=reading_rows))
        (directory / 'reading.txt').write_text('\n\n'.join(r['reading_text'] for r in reading_rows) + '\n', encoding='utf-8')
        (directory / 'reading_script.md').write_text('# 最终朗读稿\n\n' +
            f"{timezone_label(draft)} {draft['start']} 至 {draft['end']}\n\n" +
            ('实际音频时长见清单。' if manifest['all_audio_current'] else '实际口播时长未测量；目标时长不等于实际时长。') + '\n\n' +
            '\n\n'.join(r['narration_text'] for r in reading_rows) + '\n', encoding='utf-8')
        if renderer is None and visuals:
            from tools.roundup_visuals import render_segment
            renderer = render_segment
        for index, (segment, sound) in enumerate(zip(draft['segments'], audio)):
            token.check()
            duration = sound['duration_seconds'] if sound else float(segment.get('preview_seconds', 12))
            if not math.isfinite(duration) or not .1 <= duration <= 600:
                raise ValueError('单段时长应为0.1至600秒')
            pause = float(draft['pause_seconds']) if index + 1 < len(audio) else 0.
            if sound:
                # Match the silence that will actually be written to the WAV, down to one sample.
                pause = round(pause * sound['sample_rate']) / sound['sample_rate']
            row = dict(id=segment['id'], kind=segment['kind'], task_ids=segment['task_ids'], start_seconds=cursor,
                       script_origin=segment.get('origin', 'unknown'), edited=segment.get('edited', False),
                       generation_reason=segment.get('generation_reason', ''),
                       speech_end_seconds=cursor+duration, end_seconds=cursor+duration+pause,
                       duration_seconds=duration, pause_seconds=pause, timing_source='audio' if sound else 'editable_preview',
                       audio_file=None, visual_file=None, source_refs=narration_sources(segment),
                       fact_source_refs=segment.get('source_refs', []), reading_text=reading_text(draft, segment),
                       narration_origin=(current_narration(segment) or {}).get('origin', 'original'),
                       narration_review=(current_narration(segment) or {}).get('review'),
                       screen_text=segment['screen'], formal_names=task_names(draft, segment['task_ids']),
                       assets=[asset for selection in draft['selection'] if selection['id'] in segment['task_ids']
                               for asset in selection.get('assets', [])])
            if sound:
                target = f'{index+1:02d}.wav'
                shutil.copyfile(sound['path'], directory / target)
                row['audio_file'] = target
            if visuals:
                frames = max(1, math.ceil(row['end_seconds'] * 30) - math.ceil(cursor * 30))
                path = renderer(draft, segment, directory, frames, token)
                row.update(visual_file=path.relative_to(directory).as_posix(), visual_frames=frames, visual_fps=30)
            manifest['segments'].append(row)
            write_json(directory / 'manifest.json', manifest)
            if manifest['all_audio_current']:
                srt += [str(index+1), _srt_time(cursor) + ' --> ' + _srt_time(cursor+duration), narration_text(segment), '']
            cursor += duration + pause
        if manifest['all_audio_current']:
            path = _merge(manifest['segments'], directory, float(draft['pause_seconds']), token)
            manifest['narration_file'] = path.name
            measured_frames, rate = wav_info(path)
            manifest['actual_narration_seconds'] = measured_frames / rate
            (directory / 'paragraphs.srt').write_text('\n'.join(srt), encoding='utf-8')
            manifest['subtitle_file'] = 'paragraphs.srt'
        else:
            manifest['warning'] = '部分段落没有当前版本音频；混用预览时长，不生成整段旁白或SRT。'
        portable = deepcopy(draft)
        for segment, row in zip(portable['segments'], manifest['segments']):
            if row['audio_file']:
                segment['audio']['path'] = row['audio_file']
        write_json(directory / 'draft.json', portable)
        manifest.update(state='complete', timeline_seconds=cursor)
        write_json(directory / 'manifest.json', manifest)
        return directory
    except BaseException as error:
        manifest.update(state='incomplete', error=str(error))
        write_json(directory / 'manifest.json', manifest)
        raise
