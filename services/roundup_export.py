"""Non-overwriting handoff packages using the shared sample clock and calibrated subtitles."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
from uuid import uuid4

from services.launch_data import write_json
from services.launch_roundup import (digest,validate_draft, statistics, material_status, task_names,
                                    narration_text, current_narration, narration_sources)
from services.roundup_audio import wav_info, reading_text,file_hash
from services.roundup_reading import VERSION as READING_VERSION
from services.roundup_style import timezone_label
from services.roundup_sync import build_timeline, join_timelines, timeline_audio, VERSION as SYNC_VERSION
from utils.output_files import reserved_output


def _srt_time(value):
    milliseconds = round(value * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def _mux(directory, manifest, token, *, opaque=False):
    from services.media_process import run_media
    concat=directory/'visuals.ffconcat'
    concat.write_text(''.join("file '"+(directory/r['visual_file']).resolve().as_posix().replace("'","'\\''")+"'\n"
        for r in manifest['segments']),encoding='utf-8')
    with reserved_output(directory,'.mp4') as (final,temporary):
        video_map=['-map','0:v:0']
        if opaque:video_map=['-filter_complex','color=c=black:s=1920x1080:r=30[bg];[bg][0:v]overlay=shortest=1:format=auto[v]','-map','[v]']
        run_media(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(concat),'-i',str(directory/manifest['narration_file']),
            *video_map,'-map','1:a:0','-c:v','libx264','-crf','18','-preset','fast','-pix_fmt','yuv420p',
            '-c:a','aac','-t',str(manifest['timeline_seconds']),'-movflags','+faststart',str(temporary)],token)
    return final


def copy_prepared_intro(source,directory,cancel_check):
    """Include a prepared clip as a separate, non-overwriting export asset."""
    source=Path(source)
    if source.suffix.lower()!='.mp4' or not source.is_file() or not source.stat().st_size:
        raise ValueError('已制作的片头文件不可用，请在制作片头页检查')
    with reserved_output(Path(directory),'.intro.mp4') as (final,temporary):
        with source.open('rb') as reader,temporary.open('wb') as writer:
            while block:=reader.read(1024*1024):
                cancel_check();writer.write(block)
        cancel_check()
    return final.name


def export_complete_video(drafts, output_dir, token, *, on_frame=None,
                          episode=None,intro=None,intro_key=None,mix_edition=None):
    from bisect import bisect_right
    from tempfile import TemporaryDirectory
    from services import news_voice
    from services.roundup_audio import content_namespace
    from tools import episode_visuals, news_visuals, roundup_visuals, video_cards

    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('成片导出需要 FFmpeg 和 ffprobe')
    if not drafts:
        raise ValueError('请先勾选本期要导出的内容')
    schedules = []
    for draft in drafts:
        token.check()
        is_news = content_namespace(draft)=='news'
        if draft.get('content_type')!='chapter':
            (news_voice.validate if is_news else validate_draft)(draft)
        schedule = build_timeline(draft)
        if not schedule['all_audio_current']:
            title = draft.get('screen_item',{}).get('title','发射综述')
            raise ValueError(f'{title}缺少当前配音，请先生成或更新配音')
        schedules.append(schedule)
    lead=None;lead_samples=0
    if intro:
        from services.episode_video import FPS as episode_fps, prepared_intro
        lead=prepared_intro(intro,intro_key or file_hash(intro),ffmpeg,ffprobe,token)
        lead_samples=lead[1]*schedules[0]['sample_rate']//episode_fps
    clock = join_timelines(schedules,leading_samples=lead_samples)
    audio = timeline_audio(drafts[0],clock,token,leading_audio=lead[2] if lead else None)
    sections, starts = [], []
    titles = [draft['screen_item']['title'] for draft in drafts if content_namespace(draft)=='news']
    launch=next((d for d in drafts if 'selection' in d and d.get('content_type')!='chapter'),None)
    stats=statistics(launch) if launch else None
    launch_count=stats['total'] if stats else 0
    news_index = 0
    clock['sections']=[dict(id='intro',start_sample=0,duration_samples=lead_samples)] if lead else []
    offset = lead_samples
    for draft, schedule in zip(drafts,schedules):
        clock['sections'].append(dict(id=draft['id'],start_sample=offset,duration_samples=schedule['total_samples']))
        if draft.get('content_type')=='chapter':
            starts.append(offset)
            sections.append((episode_visuals,(draft,),dict(launch_count=launch_count,news_count=len(titles),
                launch_stats=(stats['actual'],stats['results']['success']) if stats else None)))
        elif content_namespace(draft)=='news':
            starts.append(offset)
            sections.append((news_visuals, (draft, titles, news_index),
                             dict(timeline=schedule,launch_count=launch_count,entering=not news_index)))
            news_index += 1
        else:
            shot_table=roundup_visuals.shot_plan(draft,schedule)
            for segment, row in zip(draft['segments'],schedule['segments']):
                starts.append(offset+row['start_sample'])
                if segment['id']==draft.get('transition_id'):
                    sections.append((episode_visuals,(draft,),dict(news_count=len(titles),launch_count=launch_count)))
                    clock['sections'].append(dict(id='news-transition',start_sample=starts[-1],
                                                  duration_samples=row['end_sample']-row['start_sample']))
                else:sections.append((roundup_visuals, (draft, segment), dict(timeline=schedule,shot_table=shot_table,
                    entering=bool(episode) and not row['start_sample'])))
        offset += schedule['total_samples']
    active, prepared = -1, None
    rate, fps = clock['sample_rate'], video_cards.FPS
    def frame_at(seconds):
        nonlocal active, prepared
        sample = round(seconds*rate)
        index = bisect_right(starts,sample)-1
        module,args,kwargs = sections[index]
        if index!=active:
            if active>=0 and sections[active][0] is news_visuals:news_visuals.close_frames(prepared)
            prepared = module.prepare_frames(*args,**kwargs)
            active = index
        return module.frame_at(prepared,(sample-starts[index])/rate)
    frames = (clock['total_samples']*fps+rate-1)//rate
    clock['playback_audio'] = dict(file=audio.name,sha256=file_hash(audio),sample_count=clock['total_samples'],sample_rate=rate)
    write_json(audio.parent/f"timeline-{digest(clock)}.json",clock)
    cues=[]
    segments=(segment for draft in drafts for segment in draft['segments'])
    for row,segment in zip(clock['segments'],segments):
        timed=row['units'] or [dict(global_start_seconds=row['start_seconds'],
            global_end_seconds=row['speech_end_seconds'],screen=segment.get('caption',narration_text(segment)))]
        for unit in timed:
            text=' '.join((unit.get('screen',unit.get('text','')).strip()).split())
            start,end=_srt_time(unit['global_start_seconds']),_srt_time(unit['global_end_seconds'])
            if text and start!=end:cues.append(f'{len(cues)+1}\n{start} --> {end}\n{text}')
    with TemporaryDirectory(dir=audio.parent) as folder:
        subtitles=None
        if cues:
            subtitles=Path(folder)/'captions.srt'
            subtitles.write_text('\n\n'.join(cues)+'\n',encoding='utf-8')
        if episode:
            from services.episode_video import mux_program
            return mux_program(sections,starts,clock,audio,subtitles,Path(folder),output_dir,episode,lead,
                               ffmpeg,ffprobe,token,on_frame)
        try:
            if mix_edition:
                from services.episode_music import mix_timeline
                audio=mix_timeline(audio,mix_edition,clock,Path(folder),ffmpeg,token)
            return video_cards.encode_card({},Path(output_dir),frames,ffmpeg,ffprobe,token.check,
                                           on_frame=on_frame,frame_provider=frame_at,audio=audio,subtitles=subtitles)
        finally:
            if active>=0 and sections[active][0] is news_visuals:news_visuals.close_frames(prepared)


def export_package(draft, output_dir, token, *, visuals=True, renderer=None,intro=None):
    native_renderer=renderer is None
    validate_draft(draft)
    if not draft['segments']:
        raise ValueError('请先生成或编辑段落')
    if any(not s['spoken'].strip() or not s['screen'].strip() for s in draft['segments']):
        raise ValueError('请补全每段口播与屏幕文字；未完成内容仍可保存草稿')
    token.check()
    directory = Path(output_dir) / ('SpaceWeekly_roundup_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    timeline = build_timeline(draft)
    manifest = dict(version=1, sync_version=SYNC_VERSION, state='exporting', subtitle_precision=timeline['precision'], start=draft['start'], end=draft['end'],
                    timezone=draft.get('timezone', 'Asia/Shanghai'), statistics=statistics(draft), segments=[], all_audio_current=False)
    states = {i['ll2_id']: material_status(i) for i in draft['snapshot']['items']}
    manifest['material_states'] = states
    write_json(directory / 'material_states.json', states)
    write_json(directory / 'draft.json', draft)
    write_json(directory / 'snapshot.json', draft['snapshot'])
    write_json(directory / 'timeline.json', timeline)
    manifest['timeline_file']='timeline.json'
    from tools.roundup_visuals import shot_plan
    shots=shot_plan(draft,timeline)
    write_json(directory/'shots.json',shots)
    manifest.update(visual_version=shots['version'],shots_file='shots.json',
        visual_transitions=shots['transition_count'],short_shots=len(shots['short_shots']))
    if draft.get('writing_input'):
        write_json(directory / 'writing_input.json', draft['writing_input'])
    if draft.get('polish_history'):
        write_json(directory / 'polish_history.json', draft['polish_history'])
    lines = ['# 每周发射综述', f"{timezone_label(draft)} {draft['start']} 至 {draft['end']}", '字幕与画面精度：'+timeline['precision']+'；不提供逐字动画。', '']
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
    timings = timeline['segments']
    audio = [timing['audio'] for timing in timings]
    manifest['all_audio_current'] = all(a is not None for a in audio)
    cursor, srt = 0., []
    try:
        reading_rows = [dict(id=s['id'], task_ids=s['task_ids'], formal_names=task_names(draft, s['task_ids']),
            fact_text=s['spoken'], screen_text=s['screen'], narration_text=narration_text(s), reading_text=reading_text(draft, s),
            narration_status='current' if current_narration(s) else 'stale' if s.get('narration') else 'original',
            narration_origin=(current_narration(s) or {}).get('origin', 'original'),
            narration_review=(current_narration(s) or {}).get('review'),
            source_refs=narration_sources(s), material_states={i: states[i] for i in s['task_ids']},
            units=clock.get('reading_units',s.get('units',[])),sync_status=clock['status']) for s,clock in zip(draft['segments'],timings)]
        write_json(directory / 'reading_map.json', dict(version=3, reading_rules_version=READING_VERSION,
            alignment_status=timeline['precision'] if any(r['units'] for r in timings) else 'not_performed',
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
            clock=timings[index]
            duration = clock['duration_samples']/timeline['sample_rate']
            pause = clock['pause_samples']/timeline['sample_rate']
            cursor = clock['start_seconds']
            row = dict(id=segment['id'], kind=segment['kind'], task_ids=segment['task_ids'], start_seconds=cursor,
                       script_origin=segment.get('origin', 'unknown'), edited=segment.get('edited', False),
                       generation_reason=segment.get('generation_reason', ''),
                       speech_end_seconds=cursor+duration, end_seconds=cursor+duration+pause,
                       duration_seconds=duration, pause_seconds=pause, timing_source=clock['timing_source'],
                       sync_precision=clock['precision'],sync_status=clock['status'],sync_message=clock['message'],
                       units=clock['units'],
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
                frames = max(1, (clock['end_sample']*30+timeline['sample_rate']-1)//timeline['sample_rate']
                    -(clock['start_sample']*30+timeline['sample_rate']-1)//timeline['sample_rate'])
                path = renderer(draft, segment, directory, frames, token)
                row.update(visual_file=path.relative_to(directory).as_posix(), visual_frames=frames, visual_fps=30)
            manifest['segments'].append(row)
            write_json(directory / 'manifest.json', manifest)
            if manifest['all_audio_current']:
                cues=clock['units'] or [dict(global_start_seconds=cursor,global_end_seconds=cursor+duration,
                    screen=segment.get('caption',narration_text(segment)))]
                for cue in cues:
                    number=len(srt)//4+1
                    srt += [str(number), _srt_time(cue['global_start_seconds'])+' --> '+_srt_time(cue['global_end_seconds']),cue['screen'], '']
            cursor += duration + pause
        if manifest['all_audio_current']:
            path = timeline_audio(draft,timeline,token)
            target=directory/'narration.wav';shutil.copyfile(path,target);path=target
            manifest['narration_file'] = path.name
            measured_frames, rate = wav_info(path)
            manifest['actual_narration_seconds'] = measured_frames / rate
            timeline['playback_audio']=dict(file=path.name,sha256=file_hash(path),sample_count=measured_frames,sample_rate=rate)
            manifest['narration_sha256']=timeline['playback_audio']['sha256']
            write_json(directory/'timeline.json',timeline)
            (directory / 'paragraphs.srt').write_text('\n'.join(srt), encoding='utf-8')
            manifest['subtitle_file'] = 'paragraphs.srt'
        else:
            manifest['warning'] = '部分段落没有当前版本音频；混用预览时长，不生成整段旁白或SRT。'
        portable = deepcopy(draft)
        for segment, row in zip(portable['segments'], manifest['segments']):
            if row['audio_file']:
                segment['audio']['path'] = row['audio_file']
        write_json(directory / 'draft.json', portable)
        manifest.update(timeline_seconds=timeline['duration_seconds'])
        if visuals and native_renderer and manifest['all_audio_current']:
            manifest['preview_video_file']=_mux(directory,manifest,token).name
        if intro:manifest['intro_file']=copy_prepared_intro(intro,directory,token.check)
        manifest['state']='complete'
        write_json(directory / 'manifest.json', manifest)
        return directory
    except BaseException as error:
        manifest.update(state='incomplete', error=str(error))
        write_json(directory / 'manifest.json', manifest)
        raise
