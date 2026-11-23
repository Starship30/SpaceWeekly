"""Selected-scope production with persistent audio and resumable final outputs."""
from copy import deepcopy
import json
from pathlib import Path

from resources import resource_path
from services.cancellation import TaskCancelledError
from services.launch_data import write_json
from services.launch_roundup import digest, statistics,opening,transition,narration_text
from services.roundup_audio import current_audio,generate_audio,file_hash
from services.roundup_sync import align_drafts,build_timeline,reading_units


def roundup_scope(draft):
    """Resolve the active segments and conflicts for display without copying source evidence."""
    allowed={s['id'] for s in draft['selection'] if s['included']}
    segments=[s for s in draft['segments'] if s.get('included',True)
        and (not s['task_ids'] or set(s['task_ids']) & allowed)] if allowed else []
    covered={identifier for s in segments for identifier in s['task_ids']}
    view=dict(draft,segments=segments,selection=[dict(row,included=row['included'] and row['id'] in covered)
                                                for row in draft['selection']])
    stats=statistics(view)
    key=digest(stats)
    program_texts={opening(view),transition(view,stats),'接下来转入其他航天新闻。'}
    conflicts=[s for s in view['segments'] if set(s['task_ids'])-allowed or
        (s.get('statistics_key') and s['statistics_key']!=key and
         not (not s['task_ids'] and narration_text(s) in program_texts and s['screen'] in program_texts))]
    if conflicts:
        conflict_ids={s['id'] for s in conflicts}
        view['segments']=[s for s in view['segments'] if s['id'] not in conflict_ids]
        return view,'勾选范围已变化，请更新涉及已取消任务的合并口播或总览，再制作。原稿和音频已保留。'
    return view,''


def selected_roundup(draft, *, strict=True):
    view,issue=roundup_scope(draft)
    if strict and issue:raise ValueError(issue)
    return deepcopy(view)


def media_identity(draft):
    properties=('id','content_type','chapter','episode','review_range','transition_id','screen_item',
                'caption_overlay','snapshot','selection','visuals','start','end','timezone','as_of')
    schedule=build_timeline(draft)
    identity=[{key:draft[key] for key in properties if key in draft},schedule.get('news_animation'),
        [(segment['id'],segment['kind'],segment['task_ids'],segment['screen'],segment.get('caption'),
          narration_text(segment),row['audio']['sha256'],row['start_sample'],row['end_sample'],
          [(unit['screen'],unit['display'],unit['task_ids'],unit['global_start_sample'],unit['global_end_sample'])
           for unit in row['units']])
         for segment,row in zip(draft['segments'],schedule['segments'])]]
    if any(len(segment['task_ids'])>1 and row.get('reading_units') and not row['units']
           for segment,row in zip(draft['segments'],schedule['segments'])):
        # Existing calibrated exports can be reused; old estimated task cuts must be rendered again.
        identity.append('unaligned-multi-task-summary')
    if 'selection' in draft and draft.get('content_type')!='chapter':
        from tools.roundup_visuals import RENDER_VERSION
        identity.append(RENDER_VERSION)
    return identity


def produce(drafts,output_dir,provider,token,progress=lambda message,done,total:None,*,
            episode=None,intro=None,parts=False,preflight_errors=(),materials_mix=None):
    from ai.qwen_aligner import QwenForcedAligner
    from services import news_voice
    from services.roundup_export import export_complete_video
    result=dict(paths=[],failed=list(preflight_errors),cancelled=False,alignment_pending=[])
    count=sum(len(d['segments']) for d in drafts);total=count*2+(len(drafts) if parts else 1)
    done=0;voice_failure='';eligible=[];ready=[]
    try:
        for draft in drafts:
            token.check()
            try:
                if draft.get('content_type')=='news':news_voice.approve(draft)
                if not voice_failure and any(not current_audio(draft,s) for s in draft['segments']):provider.configure(draft['tts'])
            except TaskCancelledError:
                raise
            except (OSError,ValueError,RuntimeError) as error:
                result['failed'].append(dict(id=draft['id'],title=draft.get('screen_item',{}).get('title') or '发射视频',reason=str(error)))
                done+=len(draft['segments'])*2;continue
            eligible.append(draft)
            for segment in draft['segments']:
                progress('准备配音 · '+(draft.get('screen_item',{}).get('title') or segment.get('screen') or '发射段落'),done,total)
                if voice_failure and not current_audio(draft,segment):
                    segment['audio_error']='语音进程未就绪，请检查环境后重试：'+voice_failure
                else:
                    errors=generate_audio(draft,provider,token,segment_id=segment['id'])
                    if errors and not provider.is_running:voice_failure=errors[0][1]
                done+=1
        # Release TTS once after all synthesis, before loading the alignment model.
        provider.unload()
        prepared=[];alignment_groups={}
        for draft in eligible:
            token.check()
            title=draft.get('screen_item',{}).get('title') or '发射综述'
            missing=[s for s in draft['segments'] if not current_audio(draft,s)]
            done+=len(draft['segments'])
            if missing:
                result['failed'].append(dict(id=draft['id'],title=title,reason='部分配音未完成，请重试。'+
                    '；'.join(s.get('audio_error','缺少有效配音') for s in missing)))
                continue
            settings=draft.get('aligner',{})
            prepared.append(draft)
            if settings.get('python') and settings.get('model'):
                alignment_groups.setdefault(digest(settings),(settings,[]))[1].append(draft)
        progress('核对实际音频与字幕时间',done,total)
        aligned={}
        for settings,batch in alignment_groups.values():
            aligner=QwenForcedAligner(settings,lambda message:progress(message,done,total))
            for draft,errors in zip(batch,align_drafts(batch,aligner,token)):
                aligned[id(draft)]=errors
        for draft in prepared:
            if id(draft) in aligned:
                errors=aligned[id(draft)]
                result['alignment_pending'].extend(errors)
                by_id={s['id']:s for s in draft['segments']}
                blocking=[]
                for part,error in errors:
                    try:reading_units(draft,by_id[part])
                    except ValueError:
                        # Old edited paragraphs can retain sound and honest paragraph-level captions.
                        continue
                    blocking.append((part,error))
                if blocking and episode:
                    result['failed'].append(dict(id=draft['id'],title=draft.get('screen_item',{}).get('title') or '发射综述',
                        reason='字幕对齐未完成：'+'；'.join(f'{part} {error}' for part,error in blocking)))
                    continue
            else:
                result['alignment_pending'].append((draft['id'],'未配置对齐，采用段落级时间'))
            ready.append(draft)
        token.check()
        groups=([[draft] for draft in ready] if parts else [ready] if not result['failed'] and ready else [])
        for group in groups:
            token.check()
            strip_only=parts and group[0].get('content_type')=='news'
            title=(group[0].get('screen_item',{}).get('title') or '发射视频') if parts else '本期视频'
            progress('渲染 · '+title,done,total)
            identity=[media_identity(draft) for draft in group]
            from services.launch_display import resolve_launch
            displays=[resolve_launch(i) for draft in group for i in draft.get('snapshot',{}).get('items',[])]
            intro_key=None
            if intro and episode:
                try:intro_key=file_hash(intro)
                except OSError as error:
                    result['failed'].append(dict(id='intro',title='片头',reason=str(error)))
                    break
            try:
                from services.episode_music import identity as music_identity,settings,tracks
                mix_edition=episode or materials_mix
                music_key=music_identity(tracks(mix_edition)) if mix_edition else []
            except (OSError,ValueError) as error:
                result['failed'].append(dict(id='music',title='背景音乐',reason=str(error)))
                break
            basis=['episode-v4.7',identity,displays,strip_only,episode,intro_key,music_key,str(Path(output_dir).resolve())]
            if not strip_only and any(draft.get('content_type')=='news' for draft in group):
                from tools.news_visuals import RENDER_VERSION
                basis.append(RENDER_VERSION)
            if mix_edition and (music_key or settings(mix_edition)['voice_volume']!=100):
                basis.extend(('audio-mix-v2',settings(mix_edition)))
            key=digest(basis)
            receipt=resource_path('cache','production_exports',key+'.json')
            try:
                old=json.loads(receipt.read_text(encoding='utf-8'));path=Path(old['path'])
                valid=path.is_file() and file_hash(path)==old['sha256']
            except (OSError,ValueError,KeyError,TypeError):valid=False
            try:
                if not parts:
                    from services.news_video import validate_asset
                    for draft in group:
                        if video:=draft.get('screen_item',{}).get('video'):
                            validate_asset(video)
                if not valid:
                    on_frame=lambda fraction:progress('渲染 · '+title,done+fraction,total)
                    if strip_only:path=news_voice.export_strip(group[0],output_dir,token,on_frame=on_frame,mix_edition=materials_mix)
                    elif episode:
                        path=export_complete_video(group,output_dir,token,on_frame=on_frame,
                                                   episode=episode,intro=intro,intro_key=intro_key)
                    else:path=export_complete_video(group,output_dir,token,on_frame=on_frame,mix_edition=mix_edition)
                    write_json(receipt,dict(path=str(path),sha256=file_hash(path)))
                result['paths'].append(str(path))
            except TaskCancelledError:
                raise
            except (OSError,ValueError,RuntimeError) as error:
                result['failed'].append(dict(id=group[0]['id'] if parts else 'episode',title=title,reason=str(error)))
            done+=1
        progress('导出完成' if result['paths'] and not result['failed'] else '请检查失败项后重试',total,total)
    except TaskCancelledError:
        result['cancelled']=True
    finally:
        for draft in drafts:
            write_json(resource_path('cache','production_exports','drafts',draft['id']+'.json'),draft)
    return result
