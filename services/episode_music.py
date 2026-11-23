"""Mix an optional ordered music bed under the existing narration track."""
from pathlib import Path
import math
import shutil
from tempfile import TemporaryDirectory
import wave

from resources import resource_path
from services.cancellation import TaskCancelledError
from services.launch_roundup import digest
from services.media_process import run_media
from services.roundup_audio import file_hash

DEFAULTS=dict(enabled=True,music_volume=5,voice_volume=100,fade_in=.6,fade_out=.6,loop=True)
AUDIO_FILTER='音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff)'
BED_RATE=48000


def settings(episode):
    value=episode.get('music_settings',{})
    if not isinstance(value,dict):raise ValueError('背景配乐设置无效')
    result={**DEFAULTS,**value}
    legacy_ducking=result.pop('ducking',None)
    for key in ('enabled','loop'):
        if type(result[key]) is not bool:raise ValueError('背景配乐开关无效')
    for key in ('music_volume','voice_volume'):
        if type(result[key]) is not int or not 0<=result[key]<=100:raise ValueError('音量应为 0 至 100%')
    # Retune the former default when opening a session saved with speech muting.
    if legacy_ducking is True and result['music_volume']==18:
        result['music_volume']=DEFAULTS['music_volume']
    for key in ('fade_in','fade_out'):
        seconds=result[key]
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<=seconds<=30:
            raise ValueError('淡入和淡出时长应为 0 至 30 秒')
    return result


def tracks(episode):
    options=settings(episode)
    return episode.get('music',[]) if options['enabled'] else []


def identity(paths):
    result=[]
    for filename in paths:
        path=Path(filename)
        if not path.is_file():raise ValueError(f'背景音乐文件已移走或丢失：{path.name}')
        result.append(file_hash(path))
    return result


def render_bed(paths,directory,duration,intro_seconds,ffmpeg,token,*,options):
    remaining=duration-intro_seconds
    if not paths or remaining<=0:return None
    playlist=['ffconcat version 1.0']
    length=0
    for index,filename in enumerate(paths):
        entry=directory/f'music-{index}.wav'
        try:
            run_media([ffmpeg,'-v','error','-y','-protocol_whitelist','file','-i',filename,
                       '-map','0:a:0','-vn','-ac','2','-ar','48000','-c:a','pcm_s16le',
                       '-t',f'{remaining:.9f}',str(entry)],token)
            with wave.open(str(entry),'rb') as sound:
                frames=sound.getnframes()
                if not frames:raise ValueError('没有可播放的音频')
                length+=frames/BED_RATE
        except TaskCancelledError:raise
        except (OSError,RuntimeError,ValueError,wave.Error,EOFError) as error:
            raise ValueError(f'背景音乐无法读取：{Path(filename).name} · {error}') from error
        playlist.append(f"file '{entry.name}'")
    manifest=directory/'music.ffconcat';manifest.write_text('\n'.join(playlist)+'\n',encoding='ascii')
    target=directory/'music-bed.wav'
    audible=remaining if options['loop'] else min(remaining,length)
    fade_in,fade_out=options['fade_in'],options['fade_out']
    scale=audible/max(audible,fade_in+fade_out)
    fade_in*=scale;fade_out*=scale
    filters=['asetpts=PTS-STARTPTS']
    if fade_in:filters.append(f'afade=d={fade_in:.9f}')
    if fade_out:filters.append(f'afade=t=out:st={audible-fade_out:.9f}:d={fade_out:.9f}')
    filters.extend((f'atrim=duration={audible:.9f}',f'adelay={round(intro_seconds*BED_RATE)}S:all=1',
                    'apad',f'atrim=end_sample={round(duration*BED_RATE)}'))
    loop=['-stream_loop','-1'] if options['loop'] else []
    run_media([ffmpeg,'-v','error','-y',*loop,'-f','concat','-safe','1','-i',str(manifest),
               '-map','0:a:0','-af',','.join(filters),'-c:a','pcm_s16le',str(target)],token)
    return target


def mix(voice,paths,directory,duration,intro_seconds,ffmpeg,token,*,options=None):
    options=DEFAULTS if options is None else options
    voice_gain=options['voice_volume']/100
    if not ffmpeg and (paths or voice_gain!=1):raise ValueError('音频混合需要 FFmpeg')
    bed=render_bed(paths,directory,duration,intro_seconds,ffmpeg,token,options=options)
    if bed is None and voice_gain==1:return voice
    output=directory/'narration-with-music.wav'
    gain=f"volume='if(lt(t,{intro_seconds:.9f}),1,{voice_gain})':eval=frame"
    if bed is None:
        run_media([ffmpeg,'-v','error','-y','-i',str(voice),'-af',gain,'-c:a','pcm_s16le',str(output)],token)
    else:
        filters=(f'[0:a]aresample={BED_RATE},aformat=channel_layouts=stereo,{gain}[voice];'
                 f"[1:a]volume={options['music_volume']/100}[bed];"
                 '[voice][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97:level=false:latency=true')
        run_media([ffmpeg,'-v','error','-y','-i',str(voice),'-i',str(bed),'-filter_complex',filters,
                   '-c:a','pcm_s16le',str(output)],token)
    return output


def mix_timeline(audio,edition,timeline,directory,ffmpeg,token,*,intro_seconds=0):
    return mix(audio,tracks(edition),directory,timeline['duration_seconds'],intro_seconds,ffmpeg,token,
               options=settings(edition))


def preview_bed(episode,timeline,token):
    paths=tracks(episode)
    if not paths:return None
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise ValueError('配乐预览需要 FFmpeg')
    options=settings(episode)
    seconds=timeline['duration_seconds']
    key=digest(['music-bed-v2',identity(paths),seconds,[options[k] for k in ('fade_in','fade_out','loop')]])
    directory=resource_path('cache','roundup_playback',key)
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/'music-bed.wav'
    if not target.is_file():
        with TemporaryDirectory(dir=directory) as folder:
            bed=render_bed(paths,Path(folder),seconds,0,ffmpeg,token,options=options)
            token.check();bed.replace(target)
    from services.cache_maintenance import touch
    touch(target)
    return target
