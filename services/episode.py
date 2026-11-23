"""Edition metadata and the spoken chapters surrounding the existing drafts."""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

from services import launch_roundup as roundup, news_voice
from services.cancellation import TaskCancelledError
from services.production_export import produce, selected_roundup
from utils.time import format_datetime, parse_datetime


DEFAULT_EPISODE_NAME = '航天周报'
EPISODE_NAME_LIMIT = 40


def new_episode(number=1):
    return dict(number=number,date=format_datetime(datetime.now(timezone.utc),output_format='%Y-%m-%d'),
                opening='',name=DEFAULT_EPISODE_NAME)


def episode_name(episode):
    return episode.get('name',DEFAULT_EPISODE_NAME).strip() or DEFAULT_EPISODE_NAME


def validate_episode(value):
    if (not isinstance(value,dict) or type(value.get('number')) is not int or not 1<=value['number']<=999999
            or not isinstance(value.get('date'),str) or not isinstance(value.get('opening'),str)
            or len(value['opening'])>2000):
        raise ValueError('期号、播报日期或开场文字无效')
    date.fromisoformat(value['date'])
    name=value.get('name',DEFAULT_EPISODE_NAME)
    if not isinstance(name,str) or len(name.strip())>EPISODE_NAME_LIMIT or '\n' in name or '\r' in name:
        raise ValueError(f'周报名称须在一行内，最多 {EPISODE_NAME_LIMIT} 个字符')
    span=value.get('review_range')
    if span is not None:
        if not isinstance(span,list) or len(span)!=2 or any(not isinstance(day,str) for day in span):
            raise ValueError('请填写本期统计起止日期')
        first,last=map(date.fromisoformat,span)
        if [first.isoformat(),last.isoformat()]!=span or not first<=last<date.max:
            raise ValueError('本期统计起止日期无效')
    music=value.get('music',[])
    if not isinstance(music,list) or any(not isinstance(path,str) or not Path(path).is_absolute() for path in music):
        raise ValueError('背景音乐必须是本地音频文件')
    from services.episode_music import settings as music_settings
    music_settings(value)
    return value


def includes_news(episode, article):
    span=episode.get('review_range')
    if not span or not article.news.published.strip() and article.news.manual_id:
        return True
    stamp=parse_datetime(article.news.published)
    return stamp is not None and span[0]<=format_datetime(stamp,output_format='%Y-%m-%d')<=span[1]


def weekday(day):
    return '星期'+'一二三四五六日'[day.weekday()]


def opening_text(episode, launch, has_news):
    day=date.fromisoformat(episode['date'])
    text=f"大家好，今天是{day.month}月{day.day}日，{weekday(day)}。欢迎收看第{episode['number']}期{episode_name(episode)}。"
    if launch:
        first,last=map(date.fromisoformat,episode.get('review_range') or [launch['start'],launch['end']])
        days=(last-first).days+1
        period={7:'一周',14:'两周'}.get(days,f'{days}天')
        span=f'过去{period}' if last<=day else f'{first.month}月{first.day}日至{last.month}月{last.day}日'
        return text+f'首先，来看{span}的发射任务。'
    return text+('接下来，来看本期航天新闻。' if has_news else '')


def chapter_segment(kind,text,*,name=None):
    from services.roundup_sync import set_units
    segment=dict(id='episode-'+(name or kind),kind=kind,task_ids=[],spoken=text,screen=text,preview_seconds=8.)
    for index,unit in enumerate(set_units(segment,[dict(text=text,task_ids=[],display='transition')])):
        unit['id']=roundup.digest([segment['id'],text,index])[:32]
    return segment


def chapter_draft(episode, voice, kind, text, *, launch=None):
    segment=chapter_segment(kind,text)
    return dict(voice,content_type='chapter',id=segment['id'],chapter=kind,episode=episode,segments=[segment],
                snapshot=dict(items=[]),selection=[],review_range=episode.get('review_range') or (
                    [launch['start'],launch['end']] if launch else []),screen_item={
                    'title':{'opening':'本期介绍','closing':'结尾'}[kind]})


def prepare_launch(draft,settings,token,*,has_news,parts=False):
    wanted={row['id'] for row in draft['selection'] if row['included']}
    if not wanted:
        return None
    if not draft['segments']:
        roundup.apply_segments(draft,roundup.generate_script(draft,settings,token))
    for segment in list(draft['segments']):
        if segment.get('included',True) and (not segment['task_ids'] or set(segment['task_ids'])<=wanted):
            if not roundup.narration_text(segment).strip():
                replacement=roundup.local_script(draft,'补齐本期勾选段落的口播',single_id=segment['id'])
                roundup.apply_segments(draft,replacement,single_id=segment['id'])
    excluded={i for segment in draft['segments'] if not segment.get('included',True) for i in segment['task_ids']}
    covered={i for segment in draft['segments'] if segment.get('included',True) for i in segment['task_ids']}
    missing=wanted-covered-excluded
    if missing:
        scope=deepcopy(draft)
        for row in scope['selection']:row['included']=row['id'] in missing
        supplement=roundup.local_script(scope,'补齐本期勾选但缺少稿件的任务')
        for segment in supplement:
            if segment['task_ids']:
                segment['id']='追加-'+roundup.digest(segment['task_ids'])[:16]
                for unit in segment.get('units',[]):unit['segment_id']=segment['id']
                draft['segments'].insert(next((i for i,s in enumerate(draft['segments']) if s['kind']=='transition'),
                                              len(draft['segments'])),segment)
    view=selected_roundup(draft)
    if not view['segments']:
        return None
    if not parts:
        if not has_news:
            view['segments']=[s for s in view['segments'] if s['kind']!='transition']
        elif view['segments'][-1]['kind']!='transition':
            text=roundup.transition(view,roundup.statistics(view))
            view['segments'].append(chapter_segment('transition',text,name='news-transition'))
        if has_news and view['segments'][-1]['kind']=='transition':
            view['transition_id']=view['segments'][-1]['id']
    return view if view['segments'] else None


def make_video(data, settings, output, provider, token, progress):
    """Prepare only the checked program and preserve successful work for retry."""
    episode=validate_episode(data['episode'])
    entries=[(draft,article) for draft,article in zip(data['news'],data['articles']) if includes_news(episode,article)]
    parts=data['parts']
    failed=[]
    result=dict(paths=[],failed=failed,cancelled=False,alignment_pending=[])
    try:
        intro=data.get('intro')
        if intro and not Path(intro).is_file():
            failed.append(dict(id='intro',title='片头',reason='已配置的片头文件不可用，请在制作片头页检查'))
            return result
        launch=None
        if data['launch']:
            try:
                progress('检查发射稿件',0,1)
                draft=data['launch']
                if episode.get('review_range') and (episode['review_range']!=[draft['start'],draft['end']]
                        or draft.get('timezone','Asia/Shanghai')!='Asia/Shanghai'):
                    raise ValueError('发射资料与本期统计日期不一致，请在资料页刷新发射列表')
                launch=prepare_launch(draft,settings,token,has_news=bool(entries),parts=parts)
            except TaskCancelledError:
                raise
            except (OSError,ValueError,RuntimeError) as error:
                failed.append(dict(id='launch',title='发射综述',reason=str(error)))
        news=[]
        for draft,article in entries:
            token.check()
            title=draft['screen_item']['title']
            try:
                if not title.strip():raise ValueError('请填写新闻画面标题')
                progress('检查新闻稿件 · '+title,0,1)
                news_voice.prepare_spoken(draft,article,settings,token)
                news.append(draft)
            except TaskCancelledError:
                raise
            except (OSError,ValueError,RuntimeError) as error:
                failed.append(dict(id=draft['id'],title=title or article.news.title,reason=str(error)))
        drafts=([launch] if launch else [])+news
        if not drafts and not failed:
            failed.append(dict(id='edition',title='本期视频',reason='请在稿件页勾选本期内容'))
        if drafts and not parts and not failed:
            opening=episode['opening'].strip() or opening_text(episode,launch,bool(news))
            drafts.insert(0,chapter_draft(episode,data['voice'],'opening',opening,launch=launch))
            drafts.append(chapter_draft(episode,data['voice'],'closing',f'以上就是本期{episode_name(episode)}，感谢收看。我们下期再见。'))
        data['produced_launch']=launch
        if drafts:
            if not intro and not parts and not failed and data.get('intro_options'):
                from services.episode_video import cached_clip
                from services.roundup_audio import file_hash
                from tools import video_intro
                options=data['intro_options']
                try:
                    logo=file_hash(options['logo_path']) if options.get('logo_path') else None
                    progress('制作已配置的片头',0,1)
                    intro=cached_clip(['prepared-intro',options,logo],lambda directory:
                        video_intro.generate(directory,**options,cancel_check=token.check))
                except TaskCancelledError:
                    raise
                except (OSError,ValueError,RuntimeError) as error:
                    failed.append(dict(id='intro',title='片头',reason=str(error)))
            result=produce(drafts,output,provider,token,progress,episode=episode if not parts else None,
                           intro=intro,parts=parts,preflight_errors=failed,materials_mix=episode if parts else None)
    except TaskCancelledError:
        result['cancelled']=True
    return result
