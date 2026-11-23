"""The v4 news layout at 1920×1080, shared by preview and export."""
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps

from services.news_video import SIZE as VIDEO_SIZE, VideoFrames, frame_range, validate_asset
from services.news_voice import load_screen_image, screen_subject
from tools import video_cards
from tools.roundup_visuals import (PROGRAM_BG as BG, PROGRAM_SELECTOR, BODY_FONT, MUTED, TEXT, TRANSITION_SECONDS, _selector,
                                   _slide_right, _slide_selector, _text, draw_progress, rise)
from utils.time import format_datetime

PANEL = (710, 192)
SIZE = (VIDEO_SIZE[0], 722)
LIST_WIDTH = 614
ROWS_PER_PAGE = 6
RENDER_VERSION = 'news-v4.6'
LEFT_BOX = (58, PANEL[1], 58 + LIST_WIDTH, PANEL[1] + SIZE[1])
RIGHT_BOX = (*PANEL, PANEL[0] + SIZE[0], PANEL[1] + SIZE[1])


@lru_cache(maxsize=64)
def _row_label(title,number,active,row_height):
    label=Image.new('RGBA',(LIST_WIDTH,row_height))
    _text(label,f'{number + 1:02d}',(28,max(0,(row_height-34)//2),65,34),28,
          color='#8fb0ff' if active else '#5f7fd6',bold=active)
    words=Image.new('RGBA',(486,row_height))
    height=_text(words,title,(0,0,words.width,min(row_height-7,68)),29,23,
                 color=TEXT if active else '#98a5d6',bold=active)
    label.alpha_composite(words,(100,(row_height-height)//2))
    return label


def list_layers(titles,index):
    first = index // ROWS_PER_PAGE * ROWS_PER_PAGE
    visible = min(ROWS_PER_PAGE, len(titles) - first)
    row_height = (SIZE[1] - 10 * (ROWS_PER_PAGE - 1)) // ROWS_PER_PAGE
    rows = []
    base=Image.new('RGBA',(LIST_WIDTH,SIZE[1]),BG)
    text=Image.new('RGBA',base.size)
    selector=None
    for number in range(first, first + visible):
        active = number == index
        y=(number-first)*(row_height+10)
        bounds=(0,y,LIST_WIDTH-1,y+row_height-1)
        ImageDraw.Draw(base).rounded_rectangle(bounds,17,fill='#141d40')
        if active:selector=(58,y+PANEL[1],58+LIST_WIDTH-1,y+PANEL[1]+row_height-1)
        label=_row_label(titles[number],number,active,row_height)
        text.alpha_composite(label,(0,y))
        row=base.crop((0,y,LIST_WIDTH,y+row_height))
        if active:_selector(ImageDraw.Draw(row),(0,0,LIST_WIDTH-1,row_height-1))
        row.alpha_composite(label)
        rows.append((row,y+PANEL[1]))
    state=dict(groups=(first,visible),list_base=base,list_text=text,selector=selector,selectors=[selector])
    return rows,state


def prepare_frames(draft, titles, index, *, timeline, launch_count=0, entering=True, preview=False):
    item = draft['screen_item']
    header = Image.new('RGBA', (620, 108))
    _text(header, '本周新闻', (0, 0, 620, 66), 54, bold=True)
    _text(header, f'共 {len(titles)} 条', (0, 66, 620, 40), 27, color=MUTED)
    rows,selector=list_layers(titles,index)
    previous=list_layers(titles,index-1)[1] if index else None

    width, height = SIZE
    video = item.get('video')
    if item.get('image_error'):raise ValueError(item['image_error'])
    slides=[]
    for image_asset in item.get('slideshow', []):
        slide=load_screen_image(dict(image=image_asset))
        if slide is None:raise ValueError('轮播图片已缺失或被修改，请重新选择本条图片')
        slides.append(ImageOps.fit(slide, SIZE, Image.Resampling.LANCZOS))
    reader = VideoFrames(validate_asset(video), video['frames'],fps=video.get('fps',video_cards.FPS),
                         preview_size=tuple(n//2 for n in VIDEO_SIZE) if preview else None) if video else None
    start,end=frame_range(video) if video else (0,0)
    video_end=(end-start)/reader.fps if reader else 0
    duration=timeline.get('duration_seconds',0)
    if reader and slides:
        video_end=min(video_end,duration-min(duration/2,1.5*len(slides)))
    image = None if reader or slides else load_screen_image(item)
    picture = ImageOps.fit(image, SIZE, Image.Resampling.LANCZOS) if image else None
    placeholder = Image.linear_gradient('L').rotate(45, expand=True).resize(SIZE)
    placeholder = ImageOps.colorize(placeholder, '#101a3c', '#1d2a5e').convert('RGBA')
    slides=[Image.alpha_composite(placeholder,slide) if slide.getextrema()[3][0]<255 else slide for slide in slides]
    if picture and picture.getextrema()[3][0]<255:picture=Image.alpha_composite(placeholder,picture)
    if not picture and not reader and not slides:
        from tools.roundup_visuals import _center_text
        _center_text(placeholder,'素材位 · 16:9',(0,155,width,48),27,'#8e99c4')

    metric = item.get('metric','').strip()
    text_width = width - 92 - (294 if metric else 0)
    detail = Image.new('RGBA', (text_width, 280))
    published = format_datetime(item.get('published'), output_format='%m-%d')
    subject = ' · '.join(filter(None,(item.get('subject',screen_subject(item['title'],item['summary'])),published)))
    y = 0
    if subject:
        label_width = min(text_width-32, round(video_cards._load_font(BODY_FONT,23).getlength(subject))+10)
        ImageDraw.Draw(detail).rounded_rectangle((0,0,label_width+36,37),18,fill='#232f66')
        _text(detail,subject,(18,5,label_width,29),23,19,color='#b9c4f5')
        y = 52
    y += _text(detail,item['title'],(0,y,text_width,111),50,34,bold=True) + 9
    y += _text(detail,item['summary'],(0,y,text_width,104),29,25,color='#c3ccf0')
    detail = detail.crop((0,0,text_width,y))
    overlay_height = min(height,max(y+135,180))
    overlay = Image.new('RGBA',(width,overlay_height),'#0b1228')
    overlay.putalpha(Image.linear_gradient('L').resize(overlay.size).point(lambda value: min(242,round(value*1.27))))
    overlay.alpha_composite(detail,(46,overlay_height-42-y))
    if metric:
        tile_x = width-46-269
        ImageDraw.Draw(overlay).rounded_rectangle((tile_x,overlay_height-158,width-46,overlay_height-42),
                                                 19,fill=(16,26,60,230))
        _text(overlay,metric,(tile_x+25,overlay_height-142,219,53),50,33,bold=True)
        _text(overlay,item.get('metric_label',''),(tile_x+25,overlay_height-82,219,35),23,19,color=MUTED)
    mask = Image.new('L', SIZE)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,width-1,height-1),30,fill=255)

    captions = []
    if draft.get('caption_overlay'):
        row = timeline['segments'][0]
        segment = draft['segments'][0]
        cues = row['units'] or [dict(global_start_seconds=row['start_seconds'],
                                   global_end_seconds=row['speech_end_seconds'],
                                   screen=segment.get('caption', segment['spoken']))]
        for cue in cues:
            if cue['screen'].strip():
                words,_,_ = video_cards.fit_payload_text(cue['screen'],1480,90,36,30)
                captions.append((cue['global_start_seconds'],cue['global_end_seconds'],words))
    if preview:
        # Rasterize labels once at full size, then compose the interactive preview at half size.
        def smaller(layer):
            return layer.resize((layer.width//2,layer.height//2),Image.Resampling.LANCZOS)
        header,overlay,placeholder,mask=map(smaller,(header,overlay,placeholder,mask))
        rows=[(smaller(row),top//2) for row,top in rows]
        picture=smaller(picture) if picture is not None else None
        slides=[smaller(slide) for slide in slides]
        captions=[(start,end,smaller(words)) for start,end,words in captions]
        for state in (selector,previous) if previous else (selector,):
            state['list_base']=smaller(state['list_base']);state['list_text']=smaller(state['list_text'])
            state['selector']=tuple(n//2 for n in state['selector']);state['selectors']=[state['selector']]
    return dict(header=header,rows=rows,reader=reader,picture=picture.convert('RGB') if picture else None,
                placeholder=placeholder,slides=slides,
                in_frame=start,out_frame=end,video_end=video_end,duration=duration,
                selector=selector,previous=previous,
                overlay=overlay,mask=mask,captions=captions,index=index,total=len(titles),launch=launch_count,
                entering=entering,divisor=2 if preview else 1)


def close_frames(prepared):
    if reader := prepared.get('reader'):
        reader.close()


def slide_from(prepared,before,after,seconds,*,duration=TRANSITION_SECONDS):
    progress=seconds/duration
    if progress<=0:return before
    if progress>=1:return after
    canvas=after.copy()
    divisor=prepared['divisor']
    _slide_right(canvas,before,after,progress,round(SIZE[1]/(4*divisor)),
                 box=tuple(n//divisor for n in RIGHT_BOX),mask=prepared['mask'],background=BG)
    _slide_selector(canvas,prepared['previous'],prepared['selector'],progress,
                    box=tuple(n//divisor for n in LEFT_BOX),divisor=divisor,colors=PROGRAM_SELECTOR)
    footer=(0,1009//divisor,canvas.width,canvas.height)
    canvas.paste(Image.blend(before.crop(footer),after.crop(footer),video_cards.smooth(progress,0,1)),footer[:2])
    return canvas


def video_frame(prepared,seconds):
    reader=prepared['reader']
    index=min(prepared['out_frame']-1,prepared['in_frame']+int(seconds*reader.fps+1e-7))
    original=reader.frame(index/reader.fps)
    if prepared.get('video_original') is not original:
        prepared['video_original']=original
        prepared['video_scaled']=ImageOps.fit(original,prepared['mask'].size,Image.Resampling.BILINEAR)
    return prepared['video_scaled']


def frame_at(prepared, seconds):
    smooth = video_cards.smooth
    reader,picture = prepared['reader'],prepared['picture']
    slides=prepared['slides'];video_end=prepared['video_end']
    steady = not slides and not prepared['captions'] and seconds >= (max(.95,video_end) if reader else 14 if picture else .95)
    if steady and 'steady' in prepared:return prepared['steady']
    divisor=prepared['divisor']
    panel_size=prepared['mask'].size
    canvas = Image.new('RGBA',(video_cards.W//divisor,video_cards.H//divisor),BG)
    entering = prepared['entering']
    rise(canvas,prepared['header'],(58//divisor,74//divisor),smooth(seconds,0,.6) if entering else 1,31/divisor)
    for n,(row,top) in enumerate(prepared['rows']):
        rise(canvas,row,(58//divisor,top),smooth(seconds,.15+n*.06,.5) if entering else 1,31/divisor)
    if slides and seconds>=video_end:
        elapsed=seconds-video_end
        step=max(1/video_cards.FPS,(prepared['duration']-video_end)/len(slides))
        index=min(len(slides)-1,int(elapsed/step))
        blend=smooth(elapsed-index*step,0,min(.5,step/2))
        before=slides[index-1] if index else video_frame(prepared,max(0,video_end-1/reader.fps)) if reader else slides[0]
        media=Image.blend(before,slides[index],blend) if blend<1 else slides[index].copy()
    elif reader:
        media = video_frame(prepared,seconds).copy()
    elif picture:
        zoom = 1 + .07 * min(1,max(0,seconds)/14)
        if prepared.get('zoom')!=zoom:
            width,height=panel_size
            # Fractional crop bounds keep the zoom centered between frames.
            inset=(1-1/zoom)/2
            left,top=width*inset,height*inset
            prepared['zoomed']=picture.resize(panel_size,Image.Resampling.BILINEAR,
                box=(left,top,width-left,height-top)).convert('RGBA')
            prepared['zoom']=zoom
        media=prepared['zoomed'].copy()
    else:
        media = prepared['placeholder'].copy()
    overlay = prepared['overlay']
    rise(media,overlay,(0,media.height-overlay.height),smooth(seconds,0,.4),15/divisor)
    progress = smooth(seconds,.25,.7) if entering else 1
    if progress>0:
        mask = prepared['mask']
        if progress<1:mask=mask.point(lambda value:round(value*progress))
        canvas.paste(media,(PANEL[0]//divisor,PANEL[1]//divisor+round(31*(1-progress)/divisor)),mask)
    for start,end,words in prepared['captions']:
        if start<=seconds<end:
            canvas.alpha_composite(words,(220//divisor,920//divisor))
            break
    index,total = prepared['index'],prepared['total']
    draw_progress(canvas,prepared['launch'],total,'新闻',index+1,
                  fraction=(index+smooth(seconds,0,.4))/total,divisor=divisor)
    if steady:prepared['steady']=canvas
    return canvas
