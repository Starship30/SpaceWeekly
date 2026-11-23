"""Encode changed picture sections and join them on the shared program clock."""
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image

from resources import resource_path
from services.cache_maintenance import touch
from services.launch_data import write_json
from services.launch_roundup import digest
from services.media_process import run_media
from services.roundup_audio import file_hash
from tools import video_cards
from utils.output_files import reserved_output

FPS=60

def probe(path,ffprobe):
    return json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-of','json',str(path)],
        timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))['streams']


def cached_clip(basis,render):
    directory=resource_path('cache','episode-visuals',digest([1,basis]))
    receipt=directory/'clip.json'
    try:
        index=json.loads(receipt.read_text(encoding='utf-8'))
        path=directory/Path(index['file']).name
        if file_hash(path)==index['sha256']:
            touch(path)
            return path
    except (OSError,ValueError,KeyError,TypeError):
        pass
    path=render(directory)
    write_json(receipt,dict(file=path.name,sha256=file_hash(path)))
    touch(path)
    return path


def prepared_intro(source,checksum,ffmpeg,ffprobe,token):
    from fractions import Fraction
    streams=probe(source,ffprobe)
    video=next((s for s in streams if s['codec_type']=='video'),None)
    if not video or not video.get('duration'):
        raise ValueError('已配置的片头没有可读取的视频时长')
    frames=max(1,round(Fraction(video['duration'])*FPS))
    duration=frames/FPS
    def render(directory):
        with reserved_output(directory,'.mp4') as (final,temporary):
            run_media([ffmpeg,'-v','error','-y','-i',str(source),'-map','0:v:0','-an','-vf',
                f'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}',
                '-frames:v',str(frames),*video_cards.H264_CLIP_ARGS,'-threads','4',str(temporary)],token)
            video_cards.verify_h264_clip(temporary,frames,ffprobe,fps=FPS)
        return final
    clip=cached_clip(['intro',checksum,frames,FPS],render)
    audio=None
    if any(s['codec_type']=='audio' for s in streams):
        audio=clip.parent/'intro-audio.wav'
        if not audio.exists():
            with reserved_output(clip.parent,'.wav') as (output,temporary):
                run_media([ffmpeg,'-v','error','-y','-i',str(source),'-map','0:a:0','-ac','1','-ar','24000',
                           '-c:a','pcm_s16le','-t',str(duration),str(temporary)],token)
            output.replace(audio)
        touch(audio)
    return clip,frames,audio


def section_basis(module,args,kwargs,prepared):
    from tools import news_visuals, roundup_visuals
    if module is news_visuals:
        draft,titles,index=args
        return [news_visuals.RENDER_VERSION,draft['screen_item'],titles,index,prepared['launch'],prepared['entering'],
                [(start,end,hashlib.sha256(image.tobytes()).hexdigest()) for start,end,image in prepared['captions']]]
    if module is roundup_visuals:
        scenes=prepared['scenes']+([prepared['previous']] if prepared['previous'] else [])
        return ['launch',prepared['starts'],prepared['ends'],prepared['animations'],prepared['entering'],
                [(s['state_key'],s['selectors'],hashlib.sha256(s['image'].tobytes()).hexdigest()) for s in scenes]]
    draft=args[0]
    metadata={k:v for k,v in draft.get('episode',{}).items() if k not in ('music','music_settings')}
    return ['chapter-v4.3',draft.get('chapter'),metadata if draft.get('chapter') else None,
            draft.get('review_range'),kwargs]


def mux_program(sections,starts,clock,audio,subtitles,directory,output_dir,episode,lead,ffmpeg,ffprobe,token,on_frame):
    from services.episode_music import mix_timeline
    from services.news_video import VideoFrames
    from tools import news_visuals, roundup_visuals

    rate=clock['sample_rate'];fps=FPS
    audio=mix_timeline(audio,episode,clock,directory,ffmpeg,token,intro_seconds=lead[1]/fps if lead else 0)
    frame_sample=rate//fps
    frames=(clock['total_samples']+frame_sample-1)//frame_sample
    files=[lead[:2]] if lead else []
    previous=None
    if lead:
        reader=VideoFrames(lead[0],lead[1],(video_cards.W,video_cards.H),fps=fps)
        try:previous=reader.frame((lead[1]-1)/fps)
        finally:reader.close()
    for index,((module,args,kwargs),start,end) in enumerate(zip(sections,starts,starts[1:]+[clock['total_samples']])):
        token.check()
        first=(start+frame_sample-1)//frame_sample
        last=(end+frame_sample-1)//frame_sample
        count=last-first
        if not count:continue
        phase=first*frame_sample-start
        prepared=module.prepare_frames(*args,**kwargs)
        try:
            incoming=previous
            previous=None
            slide=module is news_visuals and index>0 and sections[index-1][0] is module
            basis=['v4-layout',fps,section_basis(module,args,kwargs,prepared),count,phase,
                   hashlib.sha256(incoming.tobytes()).hexdigest() if incoming is not None else None]
            def frame_at(at):
                image=module.frame_at(prepared,at+phase/rate)
                if incoming is not None and slide:
                    return news_visuals.slide_from(prepared,incoming,image,at+phase/rate)
                if incoming is not None and at<.5:
                    return Image.blend(incoming,image,video_cards.smooth(at,0,.5))
                return image
            def render(directory):
                return video_cards.encode_card({},directory,count,ffmpeg,ffprobe,token.check,encoding='h264',
                    frame_provider=frame_at,fps=fps,
                    on_frame=(lambda fraction:on_frame((first+fraction*count)/frames)) if on_frame else None)
            files.append((cached_clip(basis,render),count))
            # Existing launch shots already animate on their calibrated paragraph clock.
            if index+1<len(sections) and not (module is roundup_visuals and sections[index+1][0] is module):
                previous=frame_at((count-1)/fps)
        finally:
            if module is news_visuals:news_visuals.close_frames(prepared)
        if on_frame:on_frame(last/frames)
    if sum(count for _,count in files)!=frames:
        raise ValueError('整期各章节帧数与时间表不一致')
    concat=directory/'episode.ffconcat'
    lines=['ffconcat version 1.0']
    for path,count in files:
        name=path.resolve().as_posix().replace("'","'\\''")
        lines.extend((f"file '{name}'",f'duration {count/fps:.9f}'))
    concat.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    subtitle_input=['-i',str(subtitles)] if subtitles else []
    subtitle_map=['-map','2:s:0','-c:s','mov_text','-metadata:s:s:0','language=chi','-disposition:s:0','default'] if subtitles else []
    with reserved_output(Path(output_dir),'.mp4',date.fromisoformat(episode['date'])) as (final,temporary):
        run_media([ffmpeg,'-v','error','-y','-f','concat','-safe','0','-i',str(concat),'-i',str(audio),
            *subtitle_input,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac',*subtitle_map,
            '-movflags','+faststart',str(temporary)],token)
        streams=probe(temporary,ffprobe)
        expected=['h264','aac']+(['mov_text'] if subtitles else [])
        if ([s['codec_name'] for s in streams]!=expected or int(streams[0].get('nb_frames',0))!=frames or
                streams[0]['r_frame_rate']!=f'{fps}/1' or
                abs(float(streams[1]['duration'])-clock['duration_seconds'])>1/fps+.002):
            raise RuntimeError('整期成片的帧数、音轨或字幕轨与时间表不一致')
    return final
