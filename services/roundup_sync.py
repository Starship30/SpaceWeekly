"""Persistent narration units and one sample-based clock for preview and export."""
from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import unicodedata
from uuid import uuid4
import wave

from resources import resource_path, bundled_resource_path
from services.launch_data import write_json
from services.launch_roundup import digest, narration_text
from services.roundup_audio import current_audio, reading_text, speech_text, wav_info, file_hash, segment_timing
from services.roundup_audio import content_namespace
from services.cancellation import TaskCancelledError

VERSION = 1
RATE = 24000
DISPLAYS = ('group', 'task', 'summary', 'transition', 'news')
DEFAULT_ALIGNER = dict(python='', model='', device='cpu', language='Chinese', timeout=600)


def validate_aligner(settings):
    if not isinstance(settings,dict) or any(not isinstance(settings.get(k,''),str) for k in ('python','model','device','language')):
        raise ValueError('强制对齐配置格式无效')
    timeout=settings.get('timeout',600)
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 10<=timeout<=3600:
        raise ValueError('强制对齐超时应为10至3600秒')


def sentence_spans(text):
    result=[]
    for m in re.finditer(r'.+?(?:[。！？!?；;\n]+|$)',text,re.S):
        if m.group().strip():result.append((m.start(),m.end()))
        elif result:result[-1]=(result[-1][0],m.end())
    return result


def set_units(segment, parts):
    """Parts carry explicit IDs from generation or editing, never name searches."""
    text = narration_text(segment)
    if ''.join(p['text'] for p in parts) != text:
        raise ValueError('讲解单元必须按顺序完整覆盖最终朗读稿')
    units, cursor = [], 0
    for part in parts:
        for start, end in sentence_spans(part['text']):
            units.append(dict(id=uuid4().hex, segment_id=segment['id'], start=cursor+start, end=cursor+end,
                text=part['text'][start:end], screen=part.get('screen', part['text'][start:end]),
                task_ids=list(part['task_ids']), display=part['display'],
                association_status=part.get('association_status', 'confirmed')))
            if segment.get('news_id'):units[-1]['news_id']=segment['news_id']
        cursor += len(part['text'])
    segment.update(units=units, units_basis=digest(text))
    return units


def rebuild_units(segment):
    if segment.get('news_id'):
        return set_units(segment,[dict(text=narration_text(segment),task_ids=[],display='news')])
    ids = segment['task_ids'] if len(segment['task_ids']) == 1 else []
    return set_units(segment, [dict(text=narration_text(segment), task_ids=ids,
        display='task' if ids else 'transition', association_status='needs_review')])


def validate_unit_structure(segment):
    units = segment.get('units')
    if units is None:
        return
    if not isinstance(units, list) or len(units) > 500:
        raise ValueError('讲解单元应为最多500项的数组')
    seen = set()
    for u in units:
        if (not isinstance(u, dict) or not isinstance(u.get('id'), str) or not u['id'] or u['id'] in seen
                or u.get('segment_id') != segment['id'] or u.get('display') not in DISPLAYS
                or type(u.get('start')) is not int or type(u.get('end')) is not int
                or not 0 <= u['start'] < u['end'] or not isinstance(u.get('text'), str)
                or not isinstance(u.get('screen'), str) or not isinstance(u.get('task_ids'), list)
                or any(not isinstance(i, str) for i in u['task_ids'])):
            raise ValueError('讲解单元的身份、位置或关联格式无效')
        seen.add(u['id'])
        if u['display']=='news' and (not segment.get('news_id') or u.get('news_id')!=segment['news_id'] or u['task_ids']):
            raise ValueError('新闻讲解单元关联无效')


def reading_units(draft, segment):
    validate_unit_structure(segment)
    text = narration_text(segment)
    saved = segment.get('units')
    if not saved:
        raise ValueError('缺少句子与任务关联，请建立并检查讲解单元')
    news=content_namespace(draft)=='news'
    if segment.get('units_basis') != digest(text):
        spans = sentence_spans(text)
        # A saved association survives punctuation edits or deletion of an aside
        # when each original sentence still matches in place, without guessing IDs.
        normalized = lambda value: value.strip().rstrip('。！？!?；;').strip()
        if (news or len(spans)!=len(saved) or any(
                normalized(unit['text'])!=normalized(text[start:end]) and
                normalized(re.sub(r'（[^（）]*）|\([^()]*\)', '', unit['text']))!=normalized(text[start:end])
                for unit,(start,end) in zip(saved,spans))):
            raise ValueError('朗读稿已修改，旧讲解单元待复查；请重建并检查关联')
        saved = [dict(unit,start=start,end=end,text=text[start:end],
                      screen=text[start:end] if '[[' in unit['screen'] else unit['screen'])
                 for unit,(start,end) in zip(saved,spans)]
    known = set(draft.get('news_items',{})) if news else {i['ll2_id'] for i in draft['snapshot']['items']}
    included=known if news else {s['id'] for s in draft['selection'] if s['included']}
    result, cursor, reading_cursor = [], 0, 0
    for unit in saved:
        start, end = unit['start'], unit['end']
        ids = unit['task_ids']
        if start != cursor or text[start:end] != unit['text']:
            raise ValueError('讲解单元位置已失效，未自动匹配其他句子')
        if news:
            if (unit['display']!='news' or unit.get('news_id')!=segment.get('news_id') or unit['news_id'] not in known
                    or ids or unit.get('association_status')!='confirmed'):
                raise ValueError('新闻讲解单元关联待检查')
        elif (unit.get('association_status') != 'confirmed' or not set(ids) <= known & included & set(segment['task_ids'])
                or len(set(ids)) != len(ids) or unit['display'] == 'task' and len(ids) != 1
                or unit['display'] in ('group', 'summary') and not ids):
            raise ValueError('讲解单元的任务关联待检查，未自动指向其他任务')
        spoken = reading_text(draft,dict(spoken=unit['text']))
        result.append(dict(unit, reading_text=spoken, reading_start=reading_cursor, reading_end=reading_cursor+len(spoken)))
        reading_cursor += len(spoken)
        cursor = end
    if cursor != len(text) or ''.join(u['reading_text'] for u in result) != reading_text(draft, segment):
        raise ValueError('发音替换跨越讲解单元边界，请调整单元分界后重新检查')
    return result


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if unicodedata.category(c)[0] in 'LN')


def map_tokens(tokens, units, duration_samples):
    """Match the complete known transcript in order; never distribute duration."""
    if not isinstance(tokens, list) or not tokens:
        raise ValueError('对齐结果为空')
    text, spans, cursor, previous, zero = '', [], 0, 0, 0
    for token in tokens:
        if not isinstance(token, dict) or not isinstance(token.get('text'), str):
            raise ValueError('对齐结果文本格式无效')
        first, last = token.get('start_time'), token.get('end_time')
        if (type(first) not in (float, int) or type(last) not in (float, int)
                or not math.isfinite(first) or not math.isfinite(last)
                or first<0 or last<first or last>duration_samples/RATE):
            raise ValueError('对齐结果含无效时间')
        start, end = round(first*RATE), round(last*RATE)
        if not 0 <= previous <= start <= end <= duration_samples:
            raise ValueError('对齐结果时间乱序、重叠或越界')
        previous = end
        zero += start == end
        value = normalized(token['text'])
        if value:
            spans.append((cursor, cursor+len(value), start, end))
            cursor += len(value)
            text += value
    expected = ''.join(normalized(u['reading_text']) for u in units)
    if not expected or text != expected:
        raise ValueError('对齐结果与实际 TTS 输入不一致，未猜测句子时间')
    if zero > len(tokens)*.2:
        raise ValueError('对齐结果存在异常零时长，请试听并人工校准')
    result, cursor = [], 0
    for u in units:
        end = cursor + len(normalized(u['reading_text']))
        matched = [s for s in spans if s[0] >= cursor and s[1] <= end]
        if not matched or matched[0][0] != cursor or matched[-1][1] != end or matched[0][2] >= matched[-1][3]:
            raise ValueError('句子边界或零时长异常，不能生成可靠的单元时间')
        result.append(dict(unit_id=u['id'], start_sample=matched[0][2], end_sample=matched[-1][3]))
        cursor = end
    validate_spans(result, units, duration_samples)
    return result


def validate_spans(spans, units, duration):
    if not isinstance(spans, list) or len(spans) != len(units):
        raise ValueError('校准时间必须覆盖全部讲解单元')
    previous = 0
    for row, unit in zip(spans, units):
        if (not isinstance(row, dict) or row.get('unit_id') != unit['id']
                or type(row.get('start_sample')) is not int or type(row.get('end_sample')) is not int
                or not previous <= row['start_sample'] < row['end_sample'] <= duration):
            raise ValueError('校准时间须有序、不重叠、非零且不超过实际音频范围')
        previous = row['end_sample']


def sound_samples(sound):
    frames = sound.get('sample_count')
    if frames is None:
        frames = wav_info(sound['path'])[0]
    return round(Fraction(frames*RATE, sound['sample_rate']))


def sync_basis(sound, units):
    return digest(dict(audio=sound['sha256'], units=[{k:u[k] for k in ('id','reading_text','reading_start','reading_end')} for u in units]))


def aligner_identity(settings):
    model = Path(settings.get('model') or '.')
    files = [(str(p.relative_to(model)), p.stat().st_size, p.stat().st_mtime_ns)
             for p in sorted(model.rglob('*')) if p.is_file()] if settings.get('model') and model.is_dir() else []
    worker = bundled_resource_path('tools', 'qwen_align_worker.py')
    python = Path(settings.get('python') or '.')
    libraries=[]
    for base in (python.parent/'Lib/site-packages',python.parent.parent/'Lib/site-packages'):
        for package in ('qwen_asr','torch','transformers'):
            libraries += [(str(p),file_hash(p)) for p in base.glob(package+'-*.dist-info/METADATA')]
    return digest(dict(version=VERSION, settings=settings, model_files=files,
        worker=file_hash(worker) if worker.is_file() else '', libraries=libraries,
        runtime=python.stat().st_mtime_ns if python.is_file() else None))


def alignment_key(draft, segment, sound):
    value=dict(audio=sound['sha256'], text=reading_text(draft, segment), rate=RATE,
        model=aligner_identity(draft.get('aligner', DEFAULT_ALIGNER)))
    if content_namespace(draft)=='news':value['news_id']=segment['news_id']
    return digest(value)


def segment_schedule(draft, segment, timing=None):
    timing = timing or segment_timing(draft, segment)
    sound = timing['audio']
    duration = sound_samples(sound) if sound else round(timing['duration_seconds']*RATE)
    value = dict(segment_id=segment.get('id'), duration_samples=duration, rate=RATE,
        timing_source=timing['timing_source'], audio=sound, precision='paragraph' if sound else 'preview',
        status='pending' if sound else 'preview', units=[], message='尚无当前音频，使用预览时长' if not sound else '待校准，使用段落级展示')
    if not sound and segment.get('sync_manual'):
        value.update(manual_review_required=True,message='尚无当前版本音频，旧人工校准待复查；使用预览时长')
    try:
        units = reading_units(draft, segment)
        value['reading_units'] = units
        if not sound:
            return value
        basis = sync_basis(sound, units)
        manual = segment.get('sync_manual')
        if manual:
            if manual.get('basis') != basis:
                value['message'] = '音频或朗读已改变，旧人工校准待复查；未套用旧时间'
                value['manual_review_required']=True
                manual=None
        if manual:
            spans = manual['spans']
            validate_spans(spans, units, duration)
            status = 'manual'
        else:
            alignment = segment.get('alignment') or {}
            if alignment.get('key') != alignment_key(draft, segment, sound):
                if not value.get('manual_review_required'):
                    value['message'] = segment.get('alignment_error') or '音频、文本或对齐配置已变化，待校准'
                return value
            spans = map_tokens(alignment.get('tokens'), units, duration)
            status = 'automatic'
        value.update(precision='unit', status=status, message='人工校准' if status=='manual' else '自动对齐（需试听核对）',
            units=[dict(u, **span, status=status) for u, span in zip(units, spans)])
        if value.get('manual_review_required'):value['message']+='；旧人工校准待复查，当前使用新自动结果'
    except (ValueError, KeyError, TypeError) as error:
        value['message'] = str(error)
        if segment.get('sync_manual'):value.update(manual_review_required=True,message=value['message']+'；旧人工校准待复查')
    return value


def closing_hold(draft):
    value = draft.get('closing_hold_seconds', 1.5)
    if type(value) not in (int, float) or not 1 <= value <= 2:
        raise ValueError('发射收尾停留应为1至2秒')
    return value


def build_timeline(draft):
    rows, cursor = [], 0
    news=content_namespace(draft)=='news'
    before,after=0,0
    if news:
        intervals=draft.get('animation_gaps',{})
        before,after=intervals.get('before_voice',.2),intervals.get('after_voice',.2)
        if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=2 for v in (before,after)):
            raise ValueError('新闻配音前后间隔应为0至2秒')
        cursor=round((1.95+before)*RATE)
    lead=cursor
    pause = float(draft.get('pause_seconds', .3))
    if not math.isfinite(pause) or not 0 <= pause <= 3:
        raise ValueError('段间停顿应为0至3秒')
    for index, segment in enumerate(draft['segments']):
        row = segment_schedule(draft, segment)
        duration = row['duration_samples']
        final_pause = closing_hold(draft) if not news else after+1.5
        silence = round((pause if index+1 < len(draft['segments']) else final_pause)*RATE)
        row.update(start_sample=cursor, speech_end_sample=cursor+duration, end_sample=cursor+duration+silence,
            pause_samples=silence, start_seconds=cursor/RATE, speech_end_seconds=(cursor+duration)/RATE,
            end_seconds=(cursor+duration+silence)/RATE)
        for unit in row['units']:
            unit.update(global_start_sample=cursor+unit['start_sample'], global_end_sample=cursor+unit['end_sample'],
                start_seconds=unit['start_sample']/RATE, end_seconds=unit['end_sample']/RATE,
                global_start_seconds=(cursor+unit['start_sample'])/RATE, global_end_seconds=(cursor+unit['end_sample'])/RATE)
        cursor += duration+silence
        rows.append(row)
    result=dict(version=VERSION, sample_rate=RATE, total_samples=cursor, duration_seconds=cursor/RATE,lead_samples=lead,
        normalization_policy='PCM16-mono-24000-round-sample-count-v1',
        all_audio_current=bool(rows) and all(r['audio'] is not None for r in rows), segments=rows,
        precision='unit' if rows and all(r['precision']=='unit' for r in rows) else
                  'mixed' if any(r['precision']=='unit' for r in rows) else 'paragraph' if rows and all(r['audio'] for r in rows) else 'preview',
        gap_rule='hold_previous_until_next_start', animation_rule='within_existing_time', subtitles='unit_or_paragraph')
    if news and rows:
        from services.news_timing import animation
        result['news_animation']=animation(draft,result)
    return result


def join_timelines(timelines,*,leading_samples=0):
    """Offset each section in samples, keeping local alignment positions intact."""
    from copy import deepcopy
    result = deepcopy(timelines[0])
    result.update(segments=[],total_samples=leading_samples,all_audio_current=True,
                  lead_samples=leading_samples+result.get('lead_samples',0))
    for timeline in timelines:
        offset = result['total_samples']
        if result['segments'] and timeline.get('lead_samples',0):
            previous=result['segments'][-1];gap=timeline['lead_samples']
            previous['pause_samples']+=gap;previous['end_sample']+=gap;previous['end_seconds']=previous['end_sample']/RATE
        for original in timeline['segments']:
            row = deepcopy(original)
            for prefix in ('start', 'speech_end', 'end'):
                row[prefix+'_sample'] += offset
                row[prefix+'_seconds'] = row[prefix+'_sample']/RATE
            for unit in row['units']:
                for prefix in ('global_start', 'global_end'):
                    unit[prefix+'_sample'] += offset
                    unit[prefix+'_seconds'] = unit[prefix+'_sample']/RATE
            result['segments'].append(row)
        result['total_samples'] += timeline['total_samples']
        result['all_audio_current'] &= timeline['all_audio_current']
    result['duration_seconds'] = result['total_samples']/RATE
    precisions = {timeline['precision'] for timeline in timelines}
    result['precision'] = precisions.pop() if len(precisions)==1 else 'mixed'
    return result


def row_at(timeline, seconds):
    sample = min(max(0, math.floor(seconds*RATE+1e-7)), max(0, timeline['total_samples']-1))
    return next((r for r in timeline['segments'] if sample < r['end_sample']), timeline['segments'][-1])


def save_manual(draft, segment, seconds):
    sound = current_audio(draft, segment)
    if not sound:
        raise ValueError('人工校准需要当前版本的实际音频')
    units = reading_units(draft, segment)
    if any(type(v) not in (int,float) or not math.isfinite(v) for pair in seconds for v in pair):
        raise ValueError('校准时间必须为有效秒数')
    spans = [dict(unit_id=u['id'], start_sample=round(a*RATE), end_sample=round(b*RATE)) for u,(a,b) in zip(units, seconds)]
    if len(seconds) != len(units):
        raise ValueError('校准时间必须覆盖全部讲解单元')
    validate_spans(spans, units, sound_samples(sound))
    segment['sync_manual'] = dict(version=VERSION, basis=sync_basis(sound, units), spans=spans)


def canonical_audio(sound, token):
    """Resample before alignment; identical PCM sample counts also drive concatenation."""
    if sound['sample_rate'] == RATE:
        return Path(sound['path'])
    from services.media_process import run_media
    frames = sound_samples(sound)
    directory = resource_path('cache', sound.get('content_type','roundup')+'_pcm', sound['sha256'])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'audio.wav'
    if not _valid_pcm(target,(frames,RATE)):
        from utils.output_files import reserved_output
        with reserved_output(directory, '.wav') as (output, temporary):
            run_media(['ffmpeg','-v','error','-y','-i',sound['path'],'-af',
                f'aresample={RATE},apad=whole_len={frames},atrim=end_sample={frames}',
                '-ac','1','-ar',str(RATE),'-c:a','pcm_s16le',str(temporary)], token)
        output.replace(target)
        write_json(target.with_suffix('.json'),dict(sha256=file_hash(target)))
    from services.cache_maintenance import touch
    touch(target)
    return target


def _valid_pcm(path,expected):
    try:
        info=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        return wav_info(path)==expected and file_hash(path)==info['sha256']
    except (OSError,ValueError,KeyError,TypeError,wave.Error,EOFError):
        return False


def timeline_audio(draft, timeline, token, *, leading_audio=None):
    if not timeline['all_audio_current']:
        raise ValueError('整期音频预览需要各段当前配音；可继续使用无音频预览')
    identity=[timeline.get('lead_samples',0),[(r['audio']['sha256'],r['duration_samples'],r['pause_samples']) for r in timeline['segments']]]
    if leading_audio:identity.append(file_hash(leading_audio))
    key = digest(identity)
    directory = resource_path('cache',content_namespace(draft)+'_playback',key)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'narration.wav'
    if _valid_pcm(target,(timeline['total_samples'],RATE)):
        from services.cache_maintenance import touch
        touch(target)
        return target
    from utils.output_files import reserved_output
    with reserved_output(directory,'.wav') as (output, temporary):
        with wave.open(str(temporary),'wb') as dest:
            dest.setparams((1,2,RATE,0,'NONE','not compressed'))
            remaining=timeline.get('lead_samples',0)
            if leading_audio:
                with wave.open(str(leading_audio),'rb') as source:
                    if source.getframerate()!=RATE or source.getnframes()>remaining:
                        raise ValueError('片头音轨与整期时间表不一致')
                    remaining-=source.getnframes()
                    while chunk:=source.readframes(65536):
                        token.check();dest.writeframesraw(chunk)
            dest.writeframesraw(b'\0\0'*remaining)
            for row in timeline['segments']:
                token.check()
                path = canonical_audio(row['audio'],token)
                with wave.open(str(path),'rb') as src:
                    while True:
                        token.check()
                        chunk=src.readframes(65536)
                        if not chunk:break
                        dest.writeframesraw(chunk)
                dest.writeframesraw(b'\0\0'*row['pause_samples'])
        if wav_info(temporary) != (timeline['total_samples'],RATE):
            raise ValueError('拼接音频采样数与统一时间表不一致')
    output.replace(target)
    write_json(target.with_suffix('.json'),dict(sha256=file_hash(target),source_hashes=[r['audio']['sha256'] for r in timeline['segments']]))
    from services.cache_maintenance import touch
    touch(target)
    return target


def align_draft(draft, provider, token, *, segment_id=None, on_progress=None):
    return align_drafts([draft],provider,token,segment_id=segment_id,on_progress=on_progress)[0]


def align_drafts(drafts, provider, token, *, segment_id=None, on_progress=None):
    """Align cache misses for drafts sharing one model, with errors in draft order."""
    jobs, by_id, completed = [], {}, set()
    errors=[[] for _ in drafts]
    segments=((index,draft,segment) for index,draft in enumerate(drafts) for segment in draft['segments'])
    for index,draft,segment in segments:
        if segment_id and segment['id'] != segment_id:continue
        token.check()
        try:
            units = reading_units(draft,segment)
            sound = current_audio(draft,segment)
            if not sound:raise ValueError('尚无当前配音，保留旧音频并等待重新生成')
            if sound.get('reading_text',reading_text(draft,segment))!=reading_text(draft,segment):
                raise ValueError('音频记录的实际 TTS 输入与当前最终文本不一致')
            key = alignment_key(draft,segment,sound)
            cache = resource_path('cache',content_namespace(draft)+'_alignment',key,'alignment.json')
            cached=segment.get('alignment')
            if cached and cached.get('key')==key or cache.exists():
                if not cached or cached.get('key')!=key:cached = json.loads(cache.read_text(encoding='utf-8'))
                if not isinstance(cached,dict):raise ValueError('对齐缓存格式无效')
                if cached.get('key') != key:raise ValueError('对齐缓存身份不匹配')
                automatic=map_tokens(cached.get('tokens'),units,sound_samples(sound))
                segment['alignment'] = dict(cached,automatic=automatic)
                segment.pop('alignment_error',None)
                continue
            if sound_samples(sound) > 300*RATE:raise ValueError('强制对齐单段最多五分钟；请按自然语义段编辑')
            path = canonical_audio(sound,token)
            identifier=segment['id'] if len(drafts)==1 else f"{index}:{segment['id']}"
            jobs.append(dict(id=identifier,audio=str(path.resolve()),text=reading_text(draft,segment)))
            by_id[identifier] = (draft,segment,key,cache,units,sound,errors[index])
        except (OSError,ValueError,TypeError,KeyError) as error:
            segment['alignment_error']=str(error);errors[index].append((segment['id'],str(error)))
    def complete(identifier,tokens,error=''):
        completed.add(identifier)
        draft,segment,key,cache,units,sound,draft_errors=by_id[identifier]
        try:
            if error:raise ValueError(error)
            automatic=map_tokens(tokens,units,sound_samples(sound))
            result=dict(version=VERSION,key=key,audio_sha256=sound['sha256'],reading_text=reading_text(draft,segment),
                aligned_audio_sha256=file_hash(canonical_audio(sound,token)),
                model=deepcopy(draft.get('aligner',DEFAULT_ALIGNER)),tokens=tokens,automatic=automatic,
                duration_samples=sound_samples(sound),sample_rate=RATE)
            write_json(cache,result);segment['alignment']=result;segment.pop('alignment_error',None)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            segment['alignment_error']=str(exc);draft_errors.append((segment['id'],str(exc)))
        finally:
            write_json(resource_path('cache',content_namespace(draft)+'-drafts',draft['id']+'.json'),draft)
            if on_progress:on_progress(segment['id'])
    if jobs:
        try:
            provider.align_batch(jobs,token,complete)
        except TaskCancelledError:
            raise
        except (OSError,ValueError,RuntimeError) as error:
            for job in jobs:
                if job['id'] not in completed:
                    complete(job['id'],None,str(error))
    return errors
