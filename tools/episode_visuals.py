"""Animated v4 introduction, news interlude and closing."""
from datetime import date
from math import cos, pi, sin

from PIL import Image, ImageDraw

from services.episode import episode_name, weekday
from tools import video_cards
from tools.roundup_visuals import ACCENT, PROGRAM_BG as BG, MUTED, SUCCESS, TEXT, _center_text, _text, draw_progress, rise


def prepare_frames(draft, *, launch_count=0, launch_stats=None, news_count=0):
    kind = draft.get('chapter','transition')
    base = Image.new('RGBA',(video_cards.W, video_cards.H),BG)
    layers = []
    def layer(text, box, size, start, *, color=TEXT, centered=False):
        x,y,width,height = box
        picture = Image.new('RGBA',(width,height))
        if centered:
            _center_text(picture,text,(0,0,width,height),size,color)
        else:
            _text(picture,text,(0,0,width,height),size,color=color,bold=True)
        layers.append((picture,(x,y),start))

    metrics=[]
    if kind=='opening':
        glow=Image.new('RGBA',(2020,1540),'#1b2a6b')
        glow.putalpha(Image.radial_gradient('L').resize(glow.size).point(lambda value: max(0,round(225*(1-value/178)))))
        base.alpha_composite(glow,(564,-573))
        draw=ImageDraw.Draw(base)
        draw.ellipse((1075,-152,2151,924),outline='#26336b',width=2)
        draw.ellipse((1248,19,1978,749),outline='#26336b',width=2)
        edition=draft['episode'];day=date.fromisoformat(edition['date'])
        layer('SPACEWEEKLY',(115,202,1450,54),31,.1,color=ACCENT)
        layer(f'{day.month}月{day.day}日 · {weekday(day)}',(115,510,1630,86),65,.45)
        if draft['review_range']:
            first,last=draft['review_range']
            span=(date.fromisoformat(last)-date.fromisoformat(first)).days+1
            layer(f'统计周期 {first[5:].replace("-",".")} — {last[5:].replace("-",".")}（北京时间）· 过去 {span} 天',
                  (115,603,1630,56),33,.6,color=MUTED)
        if launch_stats:
            metrics.extend(((launch_stats[0],'发射次数',TEXT),(launch_stats[1],'发射成功',SUCCESS)))
        if news_count:metrics.append((news_count,'条新闻',TEXT))
    elif kind=='closing':
        layer('SPACEWEEKLY',(60,285,1800,55),31,.1,color=ACCENT,centered=True)
        layer('下期再见',(60,365,1800,190),135,.25,centered=True)
        edition=draft['episode']
        layer(f"第 {edition['number']} 期{episode_name(edition)}",(60,578,1800,68),39,.45,color=MUTED,centered=True)
    else:
        layer('本周新闻',(60,323,1800,190),154,.25,centered=True)
        layer(f'共 {news_count} 条',(60,516,1800,56),33,.45,color=MUTED,centered=True)
    return dict(base=base,layers=layers,kind=kind,metrics=metrics,launch=launch_count,news=news_count,
                issue=draft['episode']['number'] if kind=='opening' else None)


def frame_at(prepared, seconds):
    smooth=video_cards.smooth
    canvas=prepared['base'].copy()
    for picture,position,start in prepared['layers']:
        rise(canvas,picture,position,smooth(seconds,start,.7))
    if prepared['kind']=='opening':
        angle=2*pi*seconds/40-pi/2
        x,y=1613+365*cos(angle),384+365*sin(angle)
        ImageDraw.Draw(canvas).ellipse((x-10,y-10,x+10,y+10),fill=ACCENT)
        count=1-(1-max(0,min(1,seconds/.9)))**3
        title=Image.new('RGBA',(1630,228))
        size=210
        prefix='第 '
        number=str(round(prepared['issue']*count))
        font=video_cards._load_font(video_cards.FONT,size)
        x=round(font.getlength(prefix))
        _text(title,prefix,(0,0,x+10,228),size,bold=True)
        _text(title,number,(x,0,1630-x,228),size,130,color='#8fb0ff',bold=True)
        x+=round(font.getlength(number+' '))
        _text(title,' 期',(x,0,1630-x,228),size,bold=True)
        rise(canvas,title,(115,273),smooth(seconds,.25,.7))
        for n,(amount,label,color) in enumerate(prepared['metrics']):
            tile=Image.new('RGBA',(340,114))
            ImageDraw.Draw(tile).rounded_rectangle((0,0,339,113),19,fill='#101a3c')
            _text(tile,str(round(amount*count)),(26,11,288,58),50,color=color,bold=True)
            _text(tile,label,(26,78,288,31),23,color=MUTED)
            rise(canvas,tile,(115+n*357,708),smooth(seconds,.8,.7))
    elif prepared['kind']=='transition':
        bars=Image.new('RGBA',canvas.size)
        pulse=.5-.3*cos(pi*seconds/1.2)
        draw_progress(bars,prepared['launch'],prepared['news'],'新闻',fraction=smooth(seconds,.8,1),
                      large=True,pulse=pulse)
        rise(canvas,bars,(0,0),smooth(seconds,.5,.6))
    return canvas
