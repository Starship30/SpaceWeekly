"""Scheme B: task list and current detail, shared by native preview and MOV export."""
import json
from pathlib import Path
import shutil
import subprocess
from bisect import bisect_right

from PIL import Image, ImageDraw
from tools import video_cards as cards
from utils.launch_status import recovery_states
from utils.output_files import reserved_output


BG=(14,21,48,255)
PANEL=(22,32,70,255)
TEXT=(238,241,255,255)
MUTED=(142,153,196,255)
ACCENT=(108,151,255,255)
SUCCESS=(76,195,138,255)
ERROR=(255,107,107,255)
BODY_FONT=cards._font_path('msyh.ttc','segoeui.ttf')
HOLD_SECONDS=6.0
TRANSITION_SECONDS=.6
FRAME_LEFT,FRAME_RIGHT=60,1860
# Descendant cqw uses the padded frame's content box, not the 1920px canvas.
CQW=(FRAME_RIGHT-FRAME_LEFT)/100
BODY_TOP,BODY_BOTTOM=184,1000
LIST_LEFT,LIST_RIGHT=60,840
ROW_MIN,ROW_MAX=round(4.2*CQW),round(7*CQW)
GROUP_HEIGHT,GROUP_MARGIN,LIST_GAP=round(1.8*CQW),round(1.1*CQW),round(.5*CQW)
LEFT_BOX=(LIST_LEFT,BODY_TOP,LIST_RIGHT+1,BODY_BOTTOM+1)
RIGHT_BOX=(880,BODY_TOP,1861,BODY_BOTTOM+1)
RIGHT_MASK=Image.new('L',(RIGHT_BOX[2]-RIGHT_BOX[0],RIGHT_BOX[3]-RIGHT_BOX[1]))
ImageDraw.Draw(RIGHT_MASK).rounded_rectangle((0,0,RIGHT_MASK.width-1,RIGHT_MASK.height-1),radius=29,fill=255)
DEFAULT_CARD_GAP=round(RIGHT_MASK.height/4)
SHOT_VERSION=2
SHORT_SHOT_SECONDS=3.0


def visual_settings(draft):
    value=draft.get('visuals',{})
    if (not isinstance(value,dict) or type(value.get('group_cards',False)) is not bool
            or not isinstance(value.get('removed_groups',[]),list)
            or any(not isinstance(key,str) for key in value.get('removed_groups',[]))):
        raise ValueError('分组画面设置无效')
    return dict(group_cards=value.get('group_cards',False),removed_groups=list(value.get('removed_groups',[])))


def group_cues(draft,timeline):
    from services.roundup_style import affiliation
    items={i['ll2_id']:i for i in draft['snapshot']['items']}
    return [dict(key=row['segment_id']+'/'+unit['id'],label=' / '.join(dict.fromkeys(
        affiliation(items[i])['label'].removesuffix('方面').rstrip() for i in unit['task_ids'])),
        start_seconds=unit['global_start_seconds'],end_seconds=unit['global_end_seconds'])
        for row in timeline['segments'] for unit in row['units'] if unit['display']=='group']


def _visual_key(scene):
    return (scene.get('display','task'),scene['focus_id'],tuple(sorted(scene['active_ids'])),scene['page'])


def shot_plan(draft,timeline=None):
    """Visual states on the existing sample clock; sentence IDs never define a shot."""
    from services.roundup_sync import build_timeline
    timeline=timeline or build_timeline(draft)
    options=visual_settings(draft);rate=timeline['sample_rate'];shots=[]
    def view(segment,ids,display):
        scene=dict(scene_plan(draft,dict(segment,task_ids=ids))[0],active_ids=ids,
            focus_id=ids[0] if display=='task' and len(ids)==1 else None,display=display,
            paragraph_task_count=0)
        return scene
    def emit(at,scene,segment,unit=None):
        ref=dict(segment_id=segment.get('id'),unit_id=unit.get('id') if unit else None)
        if shots and _visual_key(shots[-1]['scene'])==_visual_key(scene):
            shots[-1]['sources'].append(ref)
            return
        value=dict(start_sample=at,scene=scene,sources=[ref])
        if shots and shots[-1]['start_sample']==at:shots[-1]=value
        else:shots.append(value)
    for segment,row in zip(draft['segments'],timeline['segments']):
        origin=row['start_sample']
        if not shots:emit(0,view(segment,[],'summary'),segment)
        if row['units']:
            for unit in row['units']:
                ids=unit['task_ids'];display=unit['display']
                if not ids or display=='transition':continue
                if display=='group':
                    if not options['group_cards'] or row['segment_id']+'/'+unit['id'] in options['removed_groups']:
                        # The opening covers the first group; later introductions move to their own summary.
                        if not shots[-1]['scene']['active_ids']:continue
                        display='summary'
                else:display='task' if len(ids)==1 else 'summary'
                emit(origin+unit['start_sample'],view(segment,ids,display),segment,unit)
        elif not row['audio'] and segment['task_ids']:
            # Explicitly unmeasured preview retains the existing slow task carousel.
            candidates=scene_plan(draft,segment)
            step=max(HOLD_SECONDS,row['duration_samples']/rate/len(candidates))
            for n,scene in enumerate(candidates):
                at=origin+round(n*step*rate)
                if at>=row['speech_end_sample']:break
                emit(at,dict(scene,display='task'),segment)
        elif segment['task_ids']:
            ids=segment['task_ids']
            emit(origin,view(segment,ids,'task' if len(ids)==1 else 'summary'),segment)
    for n,shot in enumerate(shots):
        end=shots[n+1]['start_sample'] if n+1<len(shots) else timeline['total_samples']
        animation_end=min(end,timeline['segments'][-1]['speech_end_sample'])
        shot.update(end_sample=end,start_seconds=shot['start_sample']/rate,end_seconds=end/rate,
            duration_seconds=(end-shot['start_sample'])/rate,animation_end_sample=animation_end)
        before=shots[n-1]['scene'] if n else None
        shot['animation']=('none' if before is None else 'update' if
            (animation_end-shot['start_sample'])/rate<SHORT_SHOT_SECONDS or shot['scene']['display']=='summary' else 'card')
    return dict(version=SHOT_VERSION,sample_rate=rate,total_samples=timeline['total_samples'],
        settings=options,short_threshold_seconds=SHORT_SHOT_SECONDS,shots=shots,
        short_shots=[n for n,s in enumerate(shots) if s['duration_seconds']<SHORT_SHOT_SECONDS],
        transition_count=max(0,len(shots)-1),precision=timeline['precision'],
        hold_rule='group_intro_updates_summary_hold_transitions_and_pause')


def _text(canvas,text,box,size=36,minimum=None,color=TEXT,bold=False,line_height=None):
    x,y,width,height=box
    font=cards.FONT if bold or size>=40 else BODY_FONT
    fit_height=height
    if line_height:
        count=max(1,1+(height-size-4)//line_height)
        fit_height=min(height,count*(size+10))
    try:
        picture,actual,lines=cards.fit_text(str(text),width,fit_height,size,minimum or size,font_path=font)
    except ValueError:
        picture,actual,lines=cards.fit_payload_text(str(text),width,fit_height,size,minimum or size,font_path=font)
    if line_height:
        last=0
        for number,line in enumerate(lines):
            last=_text(canvas,line,(x,y+number*line_height,width,actual+10),actual,color=color,bold=bold)
        return (len(lines)-1)*line_height+last
    colored=Image.new('RGBA',picture.size,color);colored.putalpha(picture.getchannel('A'))
    canvas.alpha_composite(colored,(x,y))
    return picture.height


def _center_text(canvas,text,box,size,color):
    x,y,width,height=box
    font=cards._load_font(cards.FONT if size>=40 else BODY_FONT,size)
    used=min(width,round(font.getlength(str(text)))+6)
    bounds=font.getbbox(str(text));used_height=bounds[3]-bounds[1]+4
    _text(canvas,text,(round(x+(width-used)/2),y+max(0,(height-used_height)//2),used,height),size,color=color)


def _row_height(groups):
    count=sum(len(group['task_ids']) for group in groups)
    overhead=len(groups)*GROUP_HEIGHT+max(0,len(groups)-1)*GROUP_MARGIN+(count+len(groups)-1)*LIST_GAP
    return min(ROW_MAX,(BODY_BOTTOM-BODY_TOP-overhead)/count) if count else ROW_MAX


def scene_plan(draft,segment):
    """Bounded list windows; multi-task paragraphs keep their shared task scope."""
    from services.roundup_style import groups
    active=set(segment['task_ids'])
    if active-{item['ll2_id'] for item in draft['snapshot']['items']}:
        raise ValueError('段落关联任务不存在，请在稿件页检查关联资料')
    view=dict(draft,selection=[dict(s,included=s['included'] or s['id'] in active) for s in draft['selection']])
    windows=[];window=[]
    for group in groups(view):
        window.append(dict(label=group['label'],task_ids=[],total_count=len(group['task_ids'])))
        for identifier in group['task_ids']:
            window[-1]['task_ids'].append(identifier)
            if _row_height(window)<ROW_MIN:
                window[-1]['task_ids'].pop()
                if not window[-1]['task_ids']:window.pop()
                windows.append(window)
                window=[dict(label=group['label'],task_ids=[identifier],total_count=len(group['task_ids']))]
    if window:windows.append(window)
    windows=windows or [[]]
    result=[]
    for number,window in enumerate(windows,1):
        ids=[identifier for group in window for identifier in group['task_ids']]
        selected=[identifier for identifier in ids if identifier in active]
        if active and not selected:continue
        for identifier in selected or ids:
            result.append(dict(groups=window,active_ids=selected,focus_id=identifier,page=number,total=len(windows),
                               paragraph_task_count=len(active)))
    return result or [dict(groups=[],active_ids=[],focus_id=None,page=1,total=1,paragraph_task_count=len(active))]


def scene_index(count,elapsed,duration):
    # A visual carousel has no per-task speech timestamps; never rush a dense list.
    elapsed=min(max(0,elapsed),max(0,duration-1/cards.FPS))
    return min(count-1,int(elapsed/max(HOLD_SECONDS,duration/count)))


def _result_color(text):
    return ERROR if '失败' in text and '部分' not in text else SUCCESS if text=='发射成功' else MUTED


def _selector(draw,bounds):
    left,top,right,bottom=bounds
    draw.rounded_rectangle(bounds,radius=17,fill=ACCENT)
    draw.rounded_rectangle((left+8,top,right,bottom),radius=17,fill=(37,51,111,255))


def _scene(draft,segment,scene,timing):
    from services.launch_research import display_names
    from services.roundup_style import activity_context,event_context,payload_display,rocket_recovery,timezone_label
    from services.launch_roundup import statistics
    from utils.time import parse_datetime,_timezone
    rows={i['ll2_id']:i for i in draft['snapshot']['items']}
    canvas=Image.new('RGBA',(cards.W,cards.H),BG);draw=ImageDraw.Draw(canvas)
    list_text=Image.new('RGBA',canvas.size);text_draw=ImageDraw.Draw(list_text);selector=None;selectors=[]
    stats=statistics(draft)
    _text(canvas,'每周发射综述',(60,54,1800,72),54)
    summary=f"已选{stats['total']}项，已发射{stats['actual']}项，成功{stats['results']['success']}项"
    _text(canvas,f"{timezone_label(draft)} {draft['start']} — {draft['end']} · {summary}",(60,128,1800,38),25,color=MUTED)
    y=BODY_TOP;row_height=_row_height(scene['groups'])
    for number,group in enumerate(scene['groups']):
        if number:y+=GROUP_MARGIN
        group_label=group['label'].removesuffix('方面').rstrip()
        _text(list_text,group_label,(LIST_LEFT,round(y),LIST_RIGHT-LIST_LEFT-90,GROUP_HEIGHT),23,color=(185,196,245,255),bold=True)
        width=cards._load_font(cards.FONT,23).getlength(group_label)
        _text(list_text,f"· {group['total_count']}",(min(LIST_RIGHT-80,round(LIST_LEFT+width+14)),round(y),80,GROUP_HEIGHT),23,color=(111,123,171,255))
        y+=GROUP_HEIGHT+LIST_GAP
        for identifier in group['task_ids']:
            item=rows[identifier];names=display_names(item);event=event_context(item,draft)
            selected=identifier==scene['focus_id'] or (scene.get('display') in ('group','summary') and identifier in scene['active_ids'])
            related=identifier in scene['active_ids']
            top,bottom=round(y),round(y+row_height)
            draw.rounded_rectangle((LIST_LEFT,top,LIST_RIGHT,bottom),radius=17,fill=(20,29,64,255))
            if selected:selectors.append((LIST_LEFT,top,LIST_RIGHT,bottom))
            local=parse_datetime(item.get('net',''))
            date=local.astimezone(_timezone(draft.get('timezone','Asia/Shanghai'))).strftime('%m-%d') if local else ''
            text_top=round(y+(row_height-40)/2)
            _text(list_text,date,(87,text_top,110,42),29,color=(143,176,255,255) if selected else (95,127,214,255),bold=selected)
            _text(list_text,names['rocket'],(208,text_top,570,42),29,color=TEXT if selected or related else (152,165,214,255),bold=selected)
            center=round(y+row_height/2)
            text_draw.ellipse((800,center-10,820,center+10),fill=_result_color(event['launch_result']))
            y+=row_height+LIST_GAP
    list_base=canvas.crop(LEFT_BOX)
    regions=[]
    for left,top,right,bottom in selectors:
        if regions and top-regions[-1][3]<=LIST_GAP+1:
            regions[-1]=(regions[-1][0],regions[-1][1],right,bottom)
        else:regions.append((left,top,right,bottom))
    selectors=regions
    selector=selectors[0] if len(selectors)==1 else None
    for bounds in selectors:_selector(draw,bounds)
    canvas.alpha_composite(list_text)
    draw.rounded_rectangle((880,BODY_TOP,1860,BODY_BOTTOM),radius=29,fill=PANEL)
    if scene['focus_id']:
        item=rows[scene['focus_id']];names=display_names(item);event=event_context(item,draft)
        label=names['provider'].strip() or '机构待确认'
        width=min(886,round(cards._load_font(BODY_FONT,22).getlength(label)+44))
        draw.rounded_rectangle((927,224,927+width,260),radius=18,fill=(35,47,102,255))
        _text(canvas,label,(945,228,width-36,32),22,color=(185,196,245,255))
        local=parse_datetime(item.get('net',''))
        when=local.astimezone(_timezone(draft.get('timezone','Asia/Shanghai'))).strftime('%m月%d日 %H:%M') if local else ''
        _text(canvas,when,(927,285,886,42),31,color=ACCENT,bold=True)
        activity=activity_context(draft,scene['focus_id'])
        if activity['ordinal'] is not None:
            x=927+round(cards._load_font(cards.FONT,31).getlength(when))+18
            scope='本期' if stats['complete'] else '所选'
            _text(canvas,f"{scope}第 {activity['ordinal']} 次",(x,291,1813-x,34),25,color=MUTED)
        _text(canvas,names['rocket'],(927,344,886,84),68)
        place=names['site_name'] or names['site']
        pad=event['pad_code'] or names['pad_name']
        if pad and pad!=place:place+=' · '+pad+(' 工位' if event['pad_code'] else '')
        place_height=_text(canvas,place,(927,430,886,72),29,color=(169,180,222,255),line_height=35)
        divider=430+max(35,place_height)+25
        draw.line((927,divider,1813,divider),fill=(38,51,107,255),width=2)
        _text(canvas,'载荷',(927,divider+24,476,28),22,color=MUTED)
        payload_top=divider+62
        _text(canvas,payload_display(item),(927,payload_top,476,662-payload_top-14),36,line_height=54)
        _text(canvas,'目标轨道',(1443,divider+24,370,28),22,color=MUTED)
        _text(canvas,event['orbit'] or '待补充',(1443,payload_top,370,662-payload_top-14),36,
              line_height=54,color=TEXT if event['orbit'] else MUTED)
        result=event['launch_result']
        short={'发射成功':'成功','发射失败':'失败','发射部分失败':'部分失败',
               '已发射，结果待确认':'待确认','状态待确认':'待确认'}.get(result,result)
        recovery=rocket_recovery(item)
        metrics=[('发射结果',short,_result_color(result))]
        if recovery:
            states=recovery_states(recovery)
            state='成功' if states and all(s=='success' for s in states) else '部分失败' if 'partial' in states else '失败' if 'failure' in states else '未尝试' if 'none' in states else '待确认'
            metrics.append(('火箭回收',state,SUCCESS if state=='成功' else ERROR if state=='失败' else MUTED))
        metrics.append(('卫星数量',str(event['payload_count']) if event['payload_count'] is not None else '待确认',
                        SUCCESS if event['payload_count'] is not None else MUTED))
        tile_width=(886-16*(len(metrics)-1))/len(metrics)
        for number,(label,value,color) in enumerate(metrics):
            x=round(927+number*(tile_width+16));right=round(x+tile_width)
            draw.rounded_rectangle((x,662,right,804),radius=18,fill=(16,26,60,255))
            _text(canvas,value,(x+25,684,right-x-50,56),47,color=color)
            _text(canvas,label,(x+25,758,right-x-50,28),22,color=MUTED)
        caption='所选每日发射次数'
        if activity['windowed']:caption+=' · '+activity['start'][5:].replace('-','/')+'—'+activity['end'][5:].replace('-','/')
        _text(canvas,caption,(927,834,886,28),22,color=MUTED)
        width=(886-11*(len(activity['days'])-1))/len(activity['days'])
        for number,day in enumerate(activity['days']):
            x=round(927+number*(width+11));right=round(x+width)
            draw.rounded_rectangle((x,868,right,960),radius=13,fill=(37,51,111,255) if day['selected'] else (16,26,60,255),
                                   outline=ACCENT if day['selected'] else None,width=3)
            _center_text(canvas,day['label'],(x,880,right-x,26),20,(185,196,245,255) if day['selected'] else (116,128,174,255))
            _center_text(canvas,str(day['count']) if day['count'] else '–',(x,904,right-x,48),40,TEXT if day['count'] else (74,87,132,255))
    elif scene.get('display') in ('group','summary','transition'):
        ids=scene['active_ids'];items=[rows[i] for i in ids]
        labels=list(dict.fromkeys(g['label'].removesuffix('方面').rstrip() for g in scene['groups'] if set(g['task_ids'])&set(ids)))
        label=' / '.join(labels) or '每周发射综述'
        _text(canvas,label,(927,242,886,46),29,color=ACCENT)
        title='发射分组' if scene['display']=='group' else '任务汇总' if ids else '本期概览'
        _text(canvas,title,(927,330,886,90),64)
        draw.line((927,440,1813,440),fill=(38,51,107,255),width=2)
        for n,item in enumerate(items[:4]):
            top=476+n*108;names=display_names(item);event=event_context(item,draft)
            _text(canvas,names['rocket'],(927,top,650,42),32)
            _text(canvas,event['launch_result'],(1580,top,233,42),25,color=_result_color(event['launch_result']))
            _text(canvas,payload_display(item),(927,top+46,886,40),25,color=MUTED)
        if len(items)>4:_text(canvas,f'另有 {len(items)-4} 项关联任务',(927,934,886,36),25,color=MUTED)
        if not ids:_text(canvas,'按当前收录与所选任务统计',(927,490,886,60),32,color=MUTED)
    else:
        _text(canvas,'尚未选择发射任务',(932,345,880,100),48,color=MUTED)
    scope='所选任务' if stats['complete'] else '当前收录 / 所选任务 · 非全球完整统计'
    if scene['paragraph_task_count']>1:
        scope+=f" · 本段关联 {scene['paragraph_task_count']} 项"+('，预览轮播' if not timing.get('audio') else '，按讲解范围展示')
    if scene['total']>1:
        scope+=f" · 列表 {scene['page']} / {scene['total']}"
    _text(canvas,scope,(60,1038,1800,30),20,color=(116,128,174,255))
    return dict(image=canvas,list_base=list_base,list_text=list_text.crop(LEFT_BOX),selector=selector,
                groups=scene['groups'],focus_id=scene['focus_id'],selectors=selectors,
                state_key=_visual_key(scene))


def pages(draft,segment,*,timing=None):
    return prepare_frames(draft,segment,timing=timing)['pictures']


def preview_frame(draft,segment,elapsed=0,*,card_gap=DEFAULT_CARD_GAP):
    return frame_at(prepare_frames(draft,segment,card_gap=card_gap),elapsed)


def prepare_frames(draft,segment,*,timing=None,card_gap=DEFAULT_CARD_GAP,timeline=None):
    """Cache only images; preview and export use the same clock and transition."""
    if type(card_gap) is not int or not 0<=card_gap<=RIGHT_MASK.height*2:
        raise ValueError('卡片间距须为不超过两张卡片高度的非负整数')
    from services.roundup_sync import build_timeline
    view=draft
    if not segment.get('id') or not any(s.get('id')==segment['id'] for s in draft['segments']):
        segment=dict(segment,id='preview')
        view=dict(draft,segments=[segment])
    if timing is not None:
        segments=[dict(s) for s in view['segments']]
        selected=next(s for s in segments if s['id']==segment['id'])
        selected.update(audio=timing.get('audio'),preview_seconds=timing['duration_seconds'])
        view=dict(view,segments=segments)
    timeline=timeline if view is draft and timeline is not None else build_timeline(view)
    schedule=next(r for r in timeline['segments'] if r['segment_id']==segment['id'])
    table=shot_plan(view,timeline);all_shots=table['shots'];rate=table['sample_rate'];origin=schedule['start_sample']
    first=max(0,bisect_right([s['start_sample'] for s in all_shots],origin)-1)
    shots=[s for s in all_shots[first:] if s['start_sample']<schedule['end_sample']]
    timing=dict(audio=schedule['audio'],timing_source=schedule['timing_source'],
        duration_seconds=(schedule['end_sample']-origin)/rate)
    starts=[(s['start_sample']-origin)/rate for s in shots]
    ends=[(s['animation_end_sample']-origin)/rate for s in shots]
    previous=_scene(view,segment,all_shots[first-1]['scene'],timing) if first else None
    scenes=[_scene(view,segment,s['scene'],timing) for s in shots]
    return dict(pictures=[scene['image'] for scene in scenes],scenes=scenes,previous=previous,timing=timing,
                starts=starts,ends=ends,animations=[s['animation'] for s in shots],
                schedule=schedule,card_gap=card_gap)


def _slide_right(canvas,before,after,progress,card_gap=DEFAULT_CARD_GAP):
    surface=Image.new('RGBA',RIGHT_MASK.size,BG)
    height=RIGHT_MASK.height
    distance=height+card_gap
    offset=round(distance*cards.smooth(progress,0,1))
    surface.paste(before.crop(RIGHT_BOX),(0,-offset),RIGHT_MASK)
    surface.paste(after.crop(RIGHT_BOX),(0,distance-offset),RIGHT_MASK)
    canvas.paste(surface,(RIGHT_BOX[0],RIGHT_BOX[1]))


def _slide_selector(canvas,before,after,progress):
    old,new=before['selector'],after['selector']
    eased=cards.smooth(progress,0,1)
    same_list=before['groups']==after['groups']
    expanding=old and new and new[1]<=old[1] and new[3]>=old[3]
    if old and new and same_list and (new[1]>=old[1] or expanding):
        base=after['list_base'].copy()
        bounds=tuple(round(a+(b-a)*eased) for a,b in zip(old,new))
        text=Image.blend(before['list_text'],after['list_text'],eased)
    elif same_list and (not old or not new):
        # Disjoint selections fade locally; they must not use the pagination exit path.
        layers=[]
        for scene in (before,after):
            layer=scene['list_base'].copy()
            for left,top,right,bottom in scene['selectors']:
                _selector(ImageDraw.Draw(layer),(left-LIST_LEFT,top-BODY_TOP,right-LIST_LEFT,bottom-BODY_TOP))
            layer.alpha_composite(scene['list_text']);layers.append(layer)
        canvas.paste(Image.blend(*layers,eased),(LIST_LEFT,BODY_TOP))
        return
    else:
        # On wrap/pagination the selector exits below, then enters the next list from above.
        scene=before if progress<.5 else after
        base=scene['list_base'].copy();text=scene['list_text'];bounds=scene['selector']
        if bounds:
            left,top,right,bottom=bounds
            if progress<.5:
                shift=round((BODY_BOTTOM+1-top)*cards.smooth(progress,0,.5))
            else:
                shift=-round((bottom-BODY_TOP)*(1-cards.smooth(progress,.5,.5)))
            bounds=(left,top+shift,right,bottom+shift)
    if bounds:
        _selector(ImageDraw.Draw(base),(bounds[0]-LIST_LEFT,bounds[1]-BODY_TOP,bounds[2]-LIST_LEFT,bounds[3]-BODY_TOP))
    elif len((before if progress<.5 else after).get('selectors',[]))>1:
        for left,top,right,bottom in (before if progress<.5 else after)['selectors']:
            _selector(ImageDraw.Draw(base),(left-LIST_LEFT,top-BODY_TOP,right-LIST_LEFT,bottom-BODY_TOP))
    base.alpha_composite(text)
    canvas.paste(base,(LIST_LEFT,BODY_TOP))


def frame_at(prepared,elapsed):
    pictures=prepared['pictures'];duration=prepared['timing']['duration_seconds']
    rate=prepared['schedule']['rate']
    sample=min(max(0,round(elapsed*rate)),max(0,round(duration*rate)-1))
    starts=[round(value*rate) for value in prepared['starts']]
    index=max(0,min(len(pictures)-1,bisect_right(starts,sample)-1))
    start=starts[index]
    before=prepared['scenes'][index-1] if index else prepared['previous']
    after=prepared['scenes'][index]
    until=round(prepared['ends'][index]*rate)
    # Identical sample times must take the same midpoint branch after seeking or export arithmetic.
    progress=min(1,max(0,(sample-start)/max(1,min(round(TRANSITION_SECONDS*rate),(until-start)/2))))
    if before is not None and progress<1 and before['state_key']!=after['state_key']:
        if progress<=0:return before['image']
        canvas=(before['image'] if progress<.5 else after['image']).copy()
        if prepared['animations'][index]=='card':
            _slide_right(canvas,before['image'],after['image'],progress,prepared['card_gap'])
        else:canvas=after['image'].copy()
        _slide_selector(canvas,before,after,progress)
        return canvas
    return pictures[index]


def render_segment(draft, segment, directory, frames, token,*,card_gap=DEFAULT_CARD_GAP):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('画面导出需要 FFmpeg/ffprobe；稿件仍可单独导出')
    prepared=prepare_frames(draft,segment,card_gap=card_gap)
    from services.roundup_sync import build_timeline
    row=next((r for r in build_timeline(draft)['segments'] if r['segment_id']==segment.get('id')),None)
    origin=((row['start_sample']*cards.FPS+row['rate']-1)//row['rate'])/cards.FPS-row['start_seconds'] if row else 0
    with reserved_output(Path(directory), '.roundup.mov') as (final, temporary):
        process = None
        try:
            with temporary.with_suffix('.log').open('wb') as log:
                process = subprocess.Popen([ffmpeg, '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgba',
                    '-video_size', '1920x1080', '-framerate', str(cards.FPS), '-i', 'pipe:0', '-an',
                    '-c:v', 'png', '-pix_fmt', 'rgba', '-pred', 'mixed', '-threads', '4', str(temporary)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for frame in range(frames):
                    token.check()
                    image=frame_at(prepared,origin+frame/cards.FPS)
                    process.stdin.write(image.tobytes())
                process.stdin.close()
                while process.poll() is None:
                    token.check()
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    raise RuntimeError('综述 MOV 编码失败')
            token.check()
            info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(temporary)],
                timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))['streams'][0]
            if (info.get('codec_name') != 'png' or int(info.get('nb_frames', 0)) != frames
                    or (info.get('width'), info.get('height')) != (1920, 1080)):
                raise RuntimeError('综述 MOV 格式或帧数验证失败')
            token.check()
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if process is not None and process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            temporary.with_suffix('.log').unlink(missing_ok=True)
    return final
