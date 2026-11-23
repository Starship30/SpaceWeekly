"""Scheme B: task list and current detail, shared by native preview and MOV export."""
import json
from pathlib import Path
import shutil
import subprocess
from bisect import bisect_right
from functools import lru_cache

from PIL import Image, ImageDraw
from tools import video_cards as cards
from utils.launch_status import stage_recovery
from utils.output_files import reserved_output


PROGRAM_BG=(14,21,48,255)
BG=PROGRAM_BG
PANEL=(22,32,70,255)
TEXT=(238,241,255,255)
MUTED=(142,153,196,255)
ACCENT=(108,151,255,255)
SUCCESS=(76,195,138,255)
PROGRAM_SELECTOR=(ACCENT,(37,51,111,255))
LAUNCH_ACCENT=ACCENT
LAUNCH_SUCCESS=SUCCESS
LAUNCH_ERROR='#ff6b6b'
LAUNCH_TILE='#101a3c'
LAUNCH_SELECTOR=PROGRAM_SELECTOR
LIST_RULE='#26336b'
BODY_FONT=cards._font_path('msyh.ttc','segoeui.ttf')
HOLD_SECONDS=6.0
TRANSITION_SECONDS=.6
FRAME_LEFT,FRAME_RIGHT=88,1832
# Descendant cqw uses the padded frame's content box, not the 1920px canvas.
CQW=(FRAME_RIGHT-FRAME_LEFT)/100
BODY_TOP,BODY_BOTTOM=202,962
LIST_LEFT,LIST_RIGHT=FRAME_LEFT,844
ROW_MAX,DETAIL_PADDING=124,46
GROUP_MARGIN,GROUP_HEADER,PAGE_TASKS=20,34,6
PAGER_TOP=BODY_BOTTOM-76
PAGE_BUTTONS=((LIST_RIGHT-126,PAGER_TOP+20,LIST_RIGHT-70,PAGER_TOP+64),
              (LIST_RIGHT-56,PAGER_TOP+20,LIST_RIGHT,PAGER_TOP+64))
LEFT_BOX=(LIST_LEFT,BODY_TOP,LIST_RIGHT+1,BODY_BOTTOM+1)
RIGHT_BOX=(882,BODY_TOP,FRAME_RIGHT+1,BODY_BOTTOM+1)
RIGHT_MASK=Image.new('L',(RIGHT_BOX[2]-RIGHT_BOX[0],RIGHT_BOX[3]-RIGHT_BOX[1]))
ImageDraw.Draw(RIGHT_MASK).rounded_rectangle((0,0,RIGHT_MASK.width-1,RIGHT_MASK.height-1),radius=29,fill=255)
DEFAULT_CARD_GAP=round(RIGHT_MASK.height/4)
SHOT_VERSION=2
RENDER_VERSION='launch-reference-layout-v5'
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
    included_groups=_launch_groups(draft)
    def view(segment,ids,display):
        scene=dict(scene_plan(draft,dict(segment,task_ids=ids),included_groups=included_groups)[0],active_ids=ids,
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
            candidates=scene_plan(draft,segment,included_groups=included_groups)
            step=max(HOLD_SECONDS,row['duration_samples']/rate/len(candidates))
            for n,scene in enumerate(candidates):
                at=origin+round(n*step*rate)
                if at>=row['speech_end_sample']:break
                emit(at,dict(scene,display='task'),segment)
        elif segment['task_ids']:
            ids=segment['task_ids']
            emit(origin,view(segment,ids,'task' if len(ids)==1 else 'summary'),segment)
    included={row['id'] for row in draft['selection'] if row['included']}
    covered=set()
    for n,shot in enumerate(shots):
        covered.update(included.intersection(shot['scene']['active_ids']))
        shot['scene']['progress_count']=len(covered)
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


@lru_cache(maxsize=32)
def _progress_label(text,width,height,size,color):
    layer=Image.new('RGBA',(width,height))
    _text(layer,text,(0,0,width,height),size,color=color,bold=True)
    return layer


def draw_progress(canvas,launch_count,news_count,chapter,number=0,*,fraction=None,large=False,pulse=1,divisor=1):
    entries=[(name,count) for name,count in (('发射',launch_count),('新闻',news_count)) if count]
    if not entries:return
    left,right,y,gap,height=(211,1709,655,16,19) if large else (58,1862,1009,15,10)
    left,right,y,gap,height=(n//divisor for n in (left,right,y,gap,height))
    width=(right-left-gap*(len(entries)-1))//len(entries)
    draw=ImageDraw.Draw(canvas)
    for position,(name,count) in enumerate(entries):
        done=chapter=='新闻' and name=='发射'
        active=name==chapter
        x=left+position*(width+gap)
        label=f'{name} {number}/{count}' if active and not large else name
        color=SUCCESS if done else '#b9c4f5' if active else '#5d6a9c'
        canvas.alpha_composite(_progress_label(label,width,(51 if large else 33)//divisor,
                                               (38 if large else 21)//divisor,color),(x,y))
        top=y+(63 if large else 34)//divisor
        draw.rounded_rectangle((x,top,x+width,top+height),height//2,fill='#26336b')
        progress=(fraction if large and fraction is not None else 1) if done else (
            (1 if large else number/count if fraction is None else fraction) if active else 0)
        if progress>0:
            fill=SUCCESS if done else tuple(round(a*pulse+b*(1-pulse)) for a,b in zip(ACCENT,(38,51,107,255)))
            draw.rounded_rectangle((x,top,x+round(width*min(1,progress)),top+height),height//2,fill=fill)


def rise(canvas,layer,position,progress,distance=31):
    if progress<=0:return
    x,y=position
    canvas.alpha_composite(layer if progress>=1 else _faded(layer,progress),(x,y+round(distance*(1-progress))))


def _faded(layer,progress):
    faded=layer.copy()
    faded.putalpha(layer.getchannel('A').point(lambda value:round(value*progress)))
    return faded


def _launch_groups(draft):
    from services.launch_display import effective_item
    from services.roundup_style import groups
    from utils.time import parse_datetime
    times={i['ll2_id']:parse_datetime(i['net']) for i in map(effective_item,draft['snapshot']['items'])}
    return sorted(groups(draft),key=lambda group:times[group['task_ids'][0]].timestamp()
                  if times[group['task_ids'][0]] is not None else float('inf'))


def scene_plan(draft,segment,*,included_groups=None):
    """Bounded list windows; multi-task paragraphs keep their shared task scope."""
    active=set(segment['task_ids'])
    if active-{item['ll2_id'] for item in draft['snapshot']['items']}:
        raise ValueError('段落关联任务不存在，请在稿件页检查关联资料')
    if included_groups is None or any(not row['included'] and row['id'] in active for row in draft['selection']):
        view=dict(draft,selection=[dict(s,included=s['included'] or s['id'] in active) for s in draft['selection']])
        included_groups=_launch_groups(view)
    flat=[(group,identifier) for group in included_groups for identifier in group['task_ids']]
    order=[identifier for _,identifier in flat]
    count=len(flat)
    pages=max(1,(count+PAGE_TASKS-1)//PAGE_TASKS)
    size=max(1,(count+pages-1)//pages)
    windows=[]
    for start in range(0,count,size):
        window=[]
        for group,identifier in flat[start:start+size]:
            if not window or window[-1]['label']!=group['label']:
                window.append(dict(label=group['label'],task_ids=[],total_count=len(group['task_ids'])))
            window[-1]['task_ids'].append(identifier)
        windows.append(window)
    page_starts=order[::size]
    result=[]
    for number,window in enumerate(windows,1):
        ids=[identifier for group in window for identifier in group['task_ids']]
        selected=[identifier for identifier in ids if identifier in active]
        if active and not selected:continue
        for identifier in selected or ids:
            result.append(dict(groups=window,active_ids=selected,focus_id=identifier,page=number,total=len(windows),
                               paragraph_task_count=len(active),order=order,page_starts=page_starts))
    return result or [dict(groups=[],active_ids=[],focus_id=None,page=1,total=1,paragraph_task_count=len(active),order=order)]


def scene_index(count,elapsed,duration):
    # A visual carousel has no per-task speech timestamps; never rush a dense list.
    elapsed=min(max(0,elapsed),max(0,duration-1/cards.FPS))
    return min(count-1,int(elapsed/max(HOLD_SECONDS,duration/count)))


def _result_color(text):
    return LAUNCH_ERROR if '失败' in text and '部分' not in text else LAUNCH_SUCCESS if text=='发射成功' else MUTED


def _selector(draw,bounds,divisor=1,*,colors=PROGRAM_SELECTOR):
    left,top,right,bottom=bounds
    draw.rounded_rectangle(bounds,radius=17//divisor,fill=colors[0])
    draw.rounded_rectangle((left+8//divisor,top,right,bottom),radius=17//divisor,fill=colors[1])


def render_scene(draft,scene):
    from services.launch_display import load_dictionary
    from services.launch_research import display_names
    from services.roundup_style import activity_context,event_context,event_result,timezone_label
    from services.launch_roundup import statistics
    from utils.time import parse_datetime,_timezone
    rows={i['ll2_id']:i for i in draft['snapshot']['items']}
    entries=load_dictionary()
    canvas=Image.new('RGBA',(cards.W,cards.H),BG);draw=ImageDraw.Draw(canvas)
    list_text=Image.new('RGBA',canvas.size);text_draw=ImageDraw.Draw(list_text);selector=None;selectors=[]
    stats=statistics(draft)
    _text(canvas,'每周发射综述',(FRAME_LEFT,67,880,69),54,bold=True)
    header=f"{timezone_label(draft)} {draft['start']} — {draft['end']} · 共 {stats['actual']} 次发射，{stats['results']['success']} 次成功"
    _text(canvas,header,(FRAME_LEFT,139,FRAME_RIGHT-FRAME_LEFT,35),25,color=MUTED)
    activity=activity_context(draft,scene['focus_id'])
    groups=scene['groups'];count=sum(len(group['task_ids']) for group in groups)
    paged=scene['total']>1
    list_bottom=PAGER_TOP-14 if paged else BODY_BOTTOM
    overhead=len(groups)*GROUP_HEADER+max(0,len(groups)-1)*GROUP_MARGIN
    row_height=min(ROW_MAX,(list_bottom-BODY_TOP-overhead)//max(1,count))
    row_font=33 if count<=4 else 30
    y=BODY_TOP;targets=[]
    for number,group in enumerate(scene['groups']):
        if number:y+=GROUP_MARGIN
        heading=f"{group['label']} · {group['total_count']}"
        _text(list_text,heading,(LIST_LEFT,y,LIST_RIGHT-LIST_LEFT,32),23,21,color=MUTED,bold=True)
        rule_x=LIST_LEFT+round(cards._load_font(cards.FONT,23).getlength(heading))+20
        if rule_x<LIST_RIGHT:text_draw.line((rule_x,y+15,LIST_RIGHT,y+15),fill=LIST_RULE)
        y+=GROUP_HEADER
        previous_selected=False
        for row_number,identifier in enumerate(group['task_ids']):
            item=rows[identifier];names=display_names(item,entries)
            selected=identifier==scene['focus_id'] or (scene.get('display') in ('group','summary') and identifier in scene['active_ids'])
            bounds=(LIST_LEFT,y,LIST_RIGHT,y+row_height-1)
            targets.append((bounds,identifier))
            if selected:selectors.append(bounds)
            if row_number and not selected and not previous_selected:
                text_draw.line((LIST_LEFT+24,y,LIST_RIGHT-24,y),fill='#1d2a5c')
            local=parse_datetime(names.get('net',''))
            date=local.astimezone(_timezone(draft.get('timezone','Asia/Shanghai'))).strftime('%m-%d') if local else ''
            text_top=y+(row_height-row_font)//2
            _text(list_text,date,(LIST_LEFT+24,text_top,106,row_font+6),row_font,26,color='#8fb0ff' if selected else '#5f7fd6',bold=True)
            _text(list_text,names['rocket'],(LIST_LEFT+140,text_top,LIST_RIGHT-LIST_LEFT-202,row_font+9),row_font,
                  color=TEXT if selected else '#98a5d6',bold=selected)
            center=y+row_height//2
            text_draw.ellipse((LIST_RIGHT-46,center-10,LIST_RIGHT-26,center+10),fill=_result_color(event_result(item,draft)))
            y+=row_height;previous_selected=selected
    if paged:
        text_draw.line((LIST_LEFT,PAGER_TOP,LIST_RIGHT,PAGER_TOP),fill=LIST_RULE)
        _text(list_text,f"第 {scene['page']} / {scene['total']} 页 · 共 {len(scene['order'])} 条",
              (LIST_LEFT,PAGER_TOP+28,500,34),21,color='#7480ae')
        for index,(bounds,arrow) in enumerate(zip(PAGE_BUTTONS,('‹','›'))):
            page=scene['page']+(-1 if index==0 else 1)
            enabled=1<=page<=scene['total']
            text_draw.rounded_rectangle(bounds,12,fill='#141d40' if enabled else BG,outline='#2c3a78' if enabled else '#1d2a5c')
            x,top,right,bottom=bounds
            _center_text(list_text,arrow,(x,top,right-x,bottom-top),29,'#b9c4f5' if enabled else '#4a5784')
            if enabled:targets.append((bounds,scene['page_starts'][page-1]))
    list_base=canvas.crop(LEFT_BOX)
    regions=[]
    for left,top,right,bottom in selectors:
        if regions and top-regions[-1][3]==1:
            regions[-1]=(regions[-1][0],regions[-1][1],right,bottom)
        else:regions.append((left,top,right,bottom))
    selectors=regions
    selector=selectors[0] if len(selectors)==1 else None
    for bounds in selectors:_selector(draw,bounds,colors=LAUNCH_SELECTOR)
    canvas.alpha_composite(list_text)
    draw.rounded_rectangle((RIGHT_BOX[0],BODY_TOP,FRAME_RIGHT,BODY_BOTTOM),radius=30,fill=PANEL)
    left,right=RIGHT_BOX[0]+DETAIL_PADDING,FRAME_RIGHT-DETAIL_PADDING
    detail_width=right-left
    tile_top=BODY_BOTTOM-312
    if scene['focus_id']:
        item=rows[scene['focus_id']];names=display_names(item,entries);event=event_context(item,draft)
        label=names['provider'].strip()
        x=left
        if label:
            width=min(350,round(cards._load_font(BODY_FONT,22).getlength(label)+40))
            draw.rounded_rectangle((left,BODY_TOP+38,left+width,BODY_TOP+74),radius=18,fill='#2a2e62')
            _text(canvas,label,(left+18,BODY_TOP+44,width-32,27),22,19,color='#bac4f4')
        local=parse_datetime(names.get('net',''))
        local=local.astimezone(_timezone(draft.get('timezone','Asia/Shanghai'))) if local else None
        when=f'{local.month}月{local.day}日 {local:%H:%M}' if local else ''
        _text(canvas,when,(x,BODY_TOP+96,right-x,42),31,25,color=LAUNCH_ACCENT,bold=True)
        if activity['ordinal'] is not None:
            x+=round(cards._load_font(cards.FONT,31).getlength(when))+18
            scope='本期' if stats['complete'] else '所选'
            if x<right-90:_text(canvas,f"{scope}第 {activity['ordinal']} 次",(x,BODY_TOP+101,right-x,34),25,21,color=MUTED)
        _text(canvas,names['title'],(left,BODY_TOP+149,detail_width,76),65,minimum=42,bold=True)
        _text(canvas,names['location'],(left,BODY_TOP+234,detail_width,35),29,22,color='#acb7e6')
        task_line=bool(names['name'] and names['name']!=names['title'])
        if task_line:_text(canvas,'任务 · '+names['name'],(left,BODY_TOP+268,detail_width,26),22,18,color=MUTED)
        divider=BODY_TOP+300
        draw.line((left,divider,right,divider),fill='#2d3167',width=2)
        facts=[(label,value) for label,value in (('载荷',names['payload']),('目标轨道',event['orbit'])) if value]
        fact_width=(detail_width-28*(len(facts)-1))//len(facts) if facts else detail_width
        for n,(label,value) in enumerate(facts):
            x=left+n*(fact_width+28);value_top=BODY_TOP+351
            _text(canvas,label,(x,divider+22,fact_width,30),22,color=MUTED)
            _text(canvas,value,(x,value_top,fact_width,tile_top-8-value_top),35,26,line_height=50)
        result=event['launch_result']
        short={'发射成功':'成功','发射失败':'失败','发射部分失败':'部分失败',
               '已发射，结果待确认':'待确认','状态待确认':'待确认'}.get(result,result)
        quantity=event['payload_count']
        recovery=stage_recovery(item,names['recovery'],edited=names['field_sources']['recovery']=='本期人工编辑')
        metrics=[('发射结果',short,_result_color(result))]
        for stage,label in ((1,'一级回收'),(2,'二级回收')):
            if state:=recovery.get(stage):
                metrics.append((label,{'success':'成功','failure':'失败','partial':'部分失败'}[state],
                                LAUNCH_SUCCESS if state=='success' else LAUNCH_ERROR))
        metrics.append(('卫星数量',str(quantity) if quantity is not None else '—',TEXT))
        tile_width=(detail_width-16*(len(metrics)-1))/len(metrics)
        for number,(label,value,color) in enumerate(metrics):
            x=round(left+number*(tile_width+16));edge=round(x+tile_width)
            draw.rounded_rectangle((x,tile_top,edge,tile_top+126),radius=18,fill=LAUNCH_TILE)
            _text(canvas,value,(x+25,tile_top+18,edge-x-50,58),44,33,color=color,bold=True)
            _text(canvas,label,(x+25,tile_top+81,edge-x-50,28),22,color=MUTED)
    elif scene.get('display') in ('group','summary','transition'):
        ids=scene['active_ids'];items=[rows[i] for i in ids]
        labels=list(dict.fromkeys(g['label'].removesuffix('方面').rstrip() for g in scene['groups'] if set(g['task_ids'])&set(ids)))
        label=' / '.join(labels) or '每周发射综述'
        _text(canvas,label,(left,BODY_TOP+40,detail_width,46),29,color=LAUNCH_ACCENT)
        title='发射分组' if scene['display']=='group' else '任务汇总' if ids else '本期概览'
        _text(canvas,title,(left,BODY_TOP+111,detail_width,90),64)
        draw.line((left,BODY_TOP+207,right,BODY_TOP+207),fill='#2d3167',width=2)
        for n,item in enumerate(items[:4]):
            top=BODY_TOP+233+n*82;names=display_names(item,entries);result=event_result(item,draft)
            _text(canvas,names['rocket'],(left,top,detail_width-210,42),32)
            _text(canvas,result,(right-200,top,200,42),25,color=_result_color(result))
            _text(canvas,names['payload'],(left,top+44,detail_width,35),25,color=MUTED)
        if len(items)>4:_text(canvas,f'另有 {len(items)-4} 项关联任务',(left,766,detail_width,30),23,color=MUTED)
    else:
        _text(canvas,'尚未选择发射任务',(left,389,detail_width,100),48,color=MUTED)
    caption='本周每日发射次数' if not activity['windowed'] and len(activity['days'])==7 else '每日发射次数'
    if activity['windowed']:caption+=' · '+activity['start'][5:]+'—'+activity['end'][5:]
    _text(canvas,caption,(left,BODY_BOTTOM-164,detail_width,28),21,color=MUTED)
    width=(detail_width-12*(len(activity['days'])-1))/len(activity['days'])
    top=BODY_BOTTOM-126
    for number,day in enumerate(activity['days']):
        x=round(left+number*(width+12));edge=round(x+width)
        draw.rounded_rectangle((x,top,edge,top+90),13,fill=LAUNCH_SELECTOR[1] if day['selected'] else LAUNCH_TILE,
                               outline=LAUNCH_ACCENT if day['selected'] else None,width=3)
        _center_text(canvas,day['label'],(x,top+7,edge-x,26),20,'#b9c4f5' if day['selected'] else '#7480ae')
        _center_text(canvas,str(day['count']) if day['count'] else '–',(x,top+32,edge-x,52),37,TEXT if day['count'] else '#4a5784')
    scope='收录范围 · 本期所选任务' if stats['complete'] else '当前收录 / 所选任务 · 非全球完整统计'
    _text(canvas,scope,(52,1033,800,30),19,color=MUTED)
    return dict(image=canvas,list_base=list_base,list_text=list_text.crop(LEFT_BOX),selector=selector,
                groups=scene['groups'],focus_id=scene['focus_id'],selectors=selectors,
                state_key=_visual_key(scene),page=scene['page'],targets=targets)


def pages(draft,segment,*,timing=None):
    return prepare_frames(draft,segment,timing=timing)['pictures']


def preview_frame(draft,segment,elapsed=0,*,card_gap=DEFAULT_CARD_GAP):
    return frame_at(prepare_frames(draft,segment,card_gap=card_gap),elapsed)


def prepare_frames(draft,segment,*,timing=None,card_gap=DEFAULT_CARD_GAP,timeline=None,shot_table=None,
                   entering=False,preview=False):
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
    table=shot_table if view is draft and shot_table is not None else shot_plan(view,timeline)
    all_shots=table['shots'];rate=table['sample_rate'];origin=schedule['start_sample']
    first=max(0,bisect_right([s['start_sample'] for s in all_shots],origin)-1)
    shots=[s for s in all_shots[first:] if s['start_sample']<schedule['end_sample']]
    timing=dict(audio=schedule['audio'],timing_source=schedule['timing_source'],
        duration_seconds=(schedule['end_sample']-origin)/rate)
    starts=[(s['start_sample']-origin)/rate for s in shots]
    ends=[(s['animation_end_sample']-origin)/rate for s in shots]
    previous=render_scene(view,all_shots[first-1]['scene']) if first else None
    scenes=[render_scene(view,s['scene']) for s in shots]
    right_mask=RIGHT_MASK
    if preview:
        for scene in scenes+([previous] if previous is not None else []):
            for layer in ('image','list_base','list_text'):
                image=scene[layer]
                scene[layer]=image.resize((image.width//2,image.height//2),Image.Resampling.LANCZOS)
            scene['selectors']=[tuple(n//2 for n in bounds) for bounds in scene['selectors']]
            scene['selector']=scene['selectors'][0] if len(scene['selectors'])==1 else None
        left,top,right,bottom=(n//2 for n in RIGHT_BOX)
        right_mask=RIGHT_MASK.resize((right-left,bottom-top),Image.Resampling.LANCZOS)
    return dict(pictures=[scene['image'] for scene in scenes],scenes=scenes,previous=previous,timing=timing,
                starts=starts,ends=ends,animations=[s['animation'] for s in shots],
                schedule=schedule,card_gap=card_gap,entering=entering,divisor=2 if preview else 1,right_mask=right_mask)


def _slide_right(canvas,before,after,progress,card_gap=DEFAULT_CARD_GAP,*,box=RIGHT_BOX,mask=RIGHT_MASK,background=BG):
    surface=Image.new('RGBA',mask.size,background)
    height=mask.height
    distance=height+card_gap
    offset=round(distance*cards.smooth(progress,0,1))
    surface.paste(before.crop(box),(0,-offset),mask)
    surface.paste(after.crop(box),(0,distance-offset),mask)
    canvas.paste(surface,box[:2])


def _slide_selector(canvas,before,after,progress,*,box=LEFT_BOX,divisor=1,colors=LAUNCH_SELECTOR):
    if before.get('page')!=after.get('page'):
        source=before if progress<.5 else after
        canvas.paste(source['image'].crop(box),box[:2])
        return
    list_left, body_top, _, bottom_edge=box
    old,new=before['selector'],after['selector']
    eased=cards.smooth(progress,0,1)
    same_list=before['groups']==after['groups']
    old_id,new_id=before.get('focus_id'),after.get('focus_id')
    crossing=old_id and new_id and any((old_id in g['task_ids'])!=(new_id in g['task_ids']) for g in after['groups'])
    expanding=old and new and new[1]<=old[1] and new[3]>=old[3]
    if old and new and same_list and not crossing and (new[1]>=old[1] or expanding):
        base=after['list_base'].copy()
        bounds=tuple(round(a+(b-a)*eased) for a,b in zip(old,new))
        text=Image.blend(before['list_text'],after['list_text'],eased)
    elif same_list and (not old or not new or crossing):
        # Disjoint selections fade locally; they must not use the pagination exit path.
        layers=[]
        for scene in (before,after):
            layer=scene['list_base'].copy()
            for left,top,right,bottom in scene['selectors']:
                _selector(ImageDraw.Draw(layer),(left-list_left,top-body_top,right-list_left,bottom-body_top),divisor,colors=colors)
            layer.alpha_composite(scene['list_text']);layers.append(layer)
        canvas.paste(Image.blend(*layers,eased),box[:2])
        return
    else:
        # On wrap/pagination the selector exits below, then enters the next list from above.
        scene=before if progress<.5 else after
        base=scene['list_base'].copy();text=scene['list_text'];bounds=scene['selector']
        if bounds:
            left,top,right,bottom=bounds
            if progress<.5:
                shift=round((bottom_edge-top)*cards.smooth(progress,0,.5))
            else:
                shift=-round((bottom-body_top)*(1-cards.smooth(progress,.5,.5)))
            bounds=(left,top+shift,right,bottom+shift)
    if bounds:
        _selector(ImageDraw.Draw(base),(bounds[0]-list_left,bounds[1]-body_top,bounds[2]-list_left,bounds[3]-body_top),divisor,colors=colors)
    elif len((before if progress<.5 else after).get('selectors',[]))>1:
        for left,top,right,bottom in (before if progress<.5 else after)['selectors']:
            _selector(ImageDraw.Draw(base),(left-list_left,top-body_top,right-list_left,bottom-body_top),divisor,colors=colors)
    base.alpha_composite(text)
    canvas.paste(base,box[:2])


def _shot_frame(prepared,elapsed):
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
    prepared['list_scene']=before if before is not None and progress<.5 else after
    if before is not None and progress<1 and before['state_key']!=after['state_key']:
        if progress<=0:return before['image']
        divisor=prepared['divisor']
        slide=prepared['animations'][index]=='card'
        canvas=(before['image'] if slide and progress<.5 else after['image']).copy()
        if slide:
            _slide_right(canvas,before['image'],after['image'],progress,prepared['card_gap']//divisor,
                         box=tuple(n//divisor for n in RIGHT_BOX),mask=prepared['right_mask'])
        _slide_selector(canvas,before,after,progress,box=tuple(n//divisor for n in LEFT_BOX),divisor=divisor)
        footer=(0,1010//divisor,canvas.width,canvas.height)
        canvas.paste(Image.blend(before['image'].crop(footer),after['image'].crop(footer),cards.smooth(progress,0,.67)),footer[:2])
        return canvas
    return pictures[index]


def frame_at(prepared,elapsed):
    picture=_shot_frame(prepared,elapsed)
    if not prepared.get('entering') or elapsed>=.95:
        return picture
    divisor=prepared['divisor']
    canvas=Image.new('RGBA',picture.size,BG)
    for box,start,duration in (((0,0,cards.W,BODY_TOP),0,.6),(LEFT_BOX,.15,.5),(RIGHT_BOX,.25,.7),
                               ((0,1010,cards.W,cards.H),0,.4)):
        box=tuple(n//divisor for n in box)
        rise(canvas,picture.crop(box),box[:2],cards.smooth(elapsed,start,duration),31/divisor)
    return canvas


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
