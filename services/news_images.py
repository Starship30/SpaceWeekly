"""Entry-scoped candidates and verified, content-addressed news assets."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4
import warnings
from functools import lru_cache

from bs4 import BeautifulSoup
from PIL import Image,ImageOps

from downloader.client import public_url,download_to
from models.news import news_id
from resources import resource_path
from services.cancellation import TaskCancelledError
from services.launch_data import write_json
from services import cache_maintenance as cache

LOCK=RLock()
FORMATS={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','GIF':'.gif'}


def candidates(rows,base):
    result=[];seen={}
    for row in rows:
        if not isinstance(row,dict):continue
        url=public_url(row.get('url',''),base)
        if not url:continue
        if url in seen:
            for key in ('credit','license'):
                if not seen[url][key] and row.get(key):seen[url][key]=row[key]
            continue
        value=dict(url=url,source_url=row.get('source_url') or base,via=str(row.get('via','unknown')),credit=row.get('credit') or None,license=row.get('license') or None)
        seen[url]=value;result.append(value)
    return result[:20]


def html_candidates(html,base,*,page=False):
    soup=BeautifulSoup(html[:2*1024*1024],'html.parser');rows=[]
    if page:
        for tag in soup.select('meta[property="og:image"],meta[name="twitter:image"]'):
            rows.append(dict(url=tag.get('content'),via='page_meta'))
        article=soup.select_one('article,main')
        if article is None:return candidates(rows,base)
    else:article=soup
    for image in article.select('img')[:20]:
        caption=image.find_parent('figure');caption=caption.find('figcaption') if caption else None
        rows.append(dict(url=public_url(image.get('src'),base) or public_url(image.get('data-src'),base),via='page_body' if page else 'entry_html',
            credit=caption.get_text(' ',strip=True) if caption else None))
    return candidates(rows,base)


def page_candidates(html,base):return html_candidates(html,base,page=True)


def feed_candidates(item,base):
    if not hasattr(item,'get'):item=vars(item)
    rows=[]
    for key in ('media_content','media_thumbnail'):
        for media in item.get(key,[]) or []:
            if not isinstance(media,dict):continue
            if key=='media_content' and media.get('type') and not str(media['type']).startswith('image/') and media.get('medium')!='image':continue
            credit=media.get('credit') or item.get('media_credit')
            if isinstance(credit,list):credit='; '.join(str(x.get('content','')) for x in credit if isinstance(x,dict))
            rows.append(dict(url=media.get('url'),via=key,credit=credit,license=media.get('license') or item.get('rights')))
    for link in list(item.get('enclosures',[]) or [])+list(item.get('links',[]) or []):
        if isinstance(link,dict) and str(link.get('type','')).startswith('image/') and link.get('rel','enclosure')=='enclosure':
            rows.append(dict(url=link.get('href') or link.get('url'),via='enclosure'))
    for content in [item.get('summary','')]+[v.get('value','') for v in item.get('content',[]) or [] if isinstance(v,dict)]:
        rows+=html_candidates(str(content),base)
    return candidates(rows,base)


def api_candidates(item,base):
    return candidates([dict(url=item.get('image_url'),via='api_image_url',credit=item.get('image_credit'),license=item.get('image_license'))],base)


def load():
    path=resource_path('news-images','manifest.json')
    # On Windows an open reader can block the worker's atomic replacement.
    with LOCK:
        raw=path.read_text(encoding='utf-8') if path.exists() else None
    data=json.loads(raw) if raw is not None else dict(version=1,news={},images={})
    if (not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('news'),dict) or not isinstance(data.get('images'),dict)
            or any(not isinstance(v,dict) for v in data['news'].values()) or any(not isinstance(v,dict) for v in data['images'].values())):
        raise ValueError('新闻图片清单无效，未覆盖已有文件')
    return data


def save(data):write_json(resource_path('news-images','manifest.json'),data)


@lru_cache(maxsize=64)
def _digest(path,size,mtime):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(article):
    with LOCK:
        data=load();identifier=news_id(article.news.key)
        row=data['news'].setdefault(identifier,dict(id=identifier,candidates=[],assets=[],selected=None,status='pending'))
        row.update(title=article.news.title,url=article.news.url)
        row['candidates']=candidates(row['candidates']+article.news.images,article.news.url);save(data)
        return deepcopy(row)


def set_enabled(article, enabled):
    if not isinstance(enabled,bool):raise ValueError('配图使用状态必须为是或否')
    with LOCK:
        row=record(article);data=load();data['news'][row['id']]['use_image']=enabled;save(data)


def selected(identifier, *, include_disabled=False):
    data=load();row=data['news'].get(identifier,{})
    if not include_disabled and not row.get('use_image',True):return None
    return _resolve(data['images'].get(row.get('selected')))


def _resolve(value):
    if not value:return None
    value=deepcopy(value);path=resource_path(value['path']) if '/' not in value['path'] else resource_path(*value['path'].split('/'))
    if path.resolve().parent!=resource_path('news-images').resolve() or path.is_symlink() or not path.is_file():return None
    stamp=path.stat()
    if _digest(str(path),stamp.st_size,stamp.st_mtime_ns)!=value['sha256']:return None
    value['absolute_path']=str(path.resolve());return value


def slideshow(identifier):
    data=load();row=data['news'].get(identifier,{})
    if not row.get('use_image', True):return []
    result=[]
    for sha in row.get('slideshow', []):
        asset=_resolve(data['images'].get(sha))
        if asset is None:raise ValueError('轮播图片已缺失或被修改，请重新选择本条图片')
        result.append(dict(path=asset['absolute_path'],sha256=sha))
    return result


def set_slideshow(article, keys):
    with LOCK:
        row=record(article);data=load()
        for sha in keys:
            if sha not in row['assets'] or _resolve(data['images'].get(sha)) is None:
                raise ValueError('轮播图片不可用，请重新导入')
        data['news'][row['id']].update(slideshow=list(dict.fromkeys(keys)), use_image=bool(keys))
        if keys:data['news'][row['id']]['selected']=keys[0]
        save(data)


def acquire_slideshow(article, files, token):
    keys=[]
    for path in files:
        token.check()
        value=acquire(article,token,local=path,select=False)
        if value is None:raise ValueError('无法导入图片：'+Path(path).name)
        keys.append(value['sha256'])
    token.check()
    set_slideshow(article,keys)


def inspect(path):
    if path.stat().st_size>24*1024*1024:raise ValueError('图片超过24MB限制')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width,height=image.size;fmt=image.format
                if fmt not in FORMATS or min(width,height)<32 or max(width,height)>12000 or width*height>40000000:
                    raise ValueError('图片格式或尺寸不适合预览（最大4000万像素）')
                image.verify()
            with Image.open(path) as image:image.load()
    except (Image.DecompressionBombWarning,Image.DecompressionBombError) as error:raise ValueError('图片像素过大，已停止解码') from error
    return dict(format=fmt,width=width,height=height,size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def commit(article,temporary,origin,token,*,select=True):
    info=inspect(temporary);token.check();relative='news-images/'+info['sha256']+FORMATS[info['format']]
    target=resource_path(*relative.split('/'))
    with LOCK:
        record(article);data=load();row=data['news'][news_id(article.news.key)]
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=info['sha256']:
            raise ValueError('同名正式图片已被修改，已保留；请检查图片文件夹或导入新图片')
        if not target.exists():temporary.replace(target)
        value=data['images'].setdefault(info['sha256'],dict(info,path=relative,origins=[]))
        if origin not in value['origins']:value['origins'].append(origin)
        if info['sha256'] not in row['assets']:row['assets'].append(info['sha256'])
        if select:
            row['selected']=info['sha256']
            row.pop('slideshow',None)
        row.update(status='ready',error=None);save(data)
    return _resolve(value)


def acquire(article,token,*,candidate_url=None,local=None,progress=None,select=True):
    row=record(article);identifier=row['id']
    if not candidate_url and not local and row['status']=='ready' and selected(identifier,include_disabled=True):
        return selected(identifier,include_disabled=True)
    if not candidate_url and row.get('last_attempt') and row['status'] in ('failed','cancelled','downloading'):
        candidate_url=row['last_attempt']
    if not row['candidates'] and not local:
        if not article.news.url:
            return failed(identifier,'missing','本条暂无原文链接，可添加本地图片')
        # At most one bounded original-page fetch, never JavaScript or login bypass.
        directory=resource_path('cache','news_http');directory.mkdir(parents=True,exist_ok=True)
        html=directory/(hashlib.sha256(article.news.url.encode()).hexdigest()+'.html')
        temporary=resource_path('cache','news_image_downloads',uuid4().hex+'.part');temporary.parent.mkdir(parents=True,exist_ok=True)
        try:
            if not html.exists():
                download_to(article.news.url,temporary,token,max_bytes=2*1024*1024)
                temporary.replace(html)
            cache.touch(html);article.news.images=page_candidates(html.read_text(encoding='utf-8',errors='replace'),article.news.url)
            row=record(article)
        except TaskCancelledError:
            failed(identifier,'cancelled','已取消原文配图读取，已有正式图片保留');raise
        except (OSError,ValueError,RuntimeError) as error:
            return failed(identifier,'failed','原文配图读取失败：'+str(error))
        finally:temporary.unlink(missing_ok=True)
    choices=[c for c in row['candidates'] if not candidate_url or c['url']==candidate_url]
    if not local and not choices:return failed(identifier,'missing','未找到条目配图，可导入本地图片')
    # One preferred image per attempt; retry/switch is explicit in the UI.
    choice=dict(url=None,via='local_import',name=Path(local).name,credit=None,license=None) if local else choices[0]
    with LOCK:
        data=load();data['news'][identifier].update(status='downloading',error=None,last_attempt=choice['url']);save(data)
    temporary=resource_path('cache','news_image_downloads',uuid4().hex+'.part');temporary.parent.mkdir(parents=True,exist_ok=True)
    try:
        if local:
            source=Path(local);inspect(source)
            with source.open('rb') as src,temporary.open('wb') as dest:
                while block:=src.read(65536):token.check();dest.write(block)
        else:download_to(choice['url'],temporary,token,progress=progress)
        return commit(article,temporary,choice,token,select=select)
    except TaskCancelledError:
        failed(identifier,'cancelled','已取消，已有正式图片保留');raise
    except (OSError,ValueError,RuntimeError) as error:return failed(identifier,'failed',str(error))
    finally:temporary.unlink(missing_ok=True)


def failed(identifier,status,error):
    with LOCK:
        data=load();data['news'][identifier].update(status=status,error=error);save(data)
    return None


def thumbnail(identifier, *, include_disabled=False):
    value=selected(identifier,include_disabled=include_disabled)
    if not value:return None
    target=resource_path('cache','news_thumbnails',value['sha256']+'.png')
    with cache.using_media():
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(uuid4().hex+'.part')
            try:
                with Image.open(value['absolute_path']) as image:
                    image=ImageOps.exif_transpose(image);image.thumbnail((640,360));image.convert('RGB').save(temp,format='PNG')
                temp.replace(target)
            finally:temp.unlink(missing_ok=True)
        cache.touch(target)
    return target


def collect(articles,token):
    for article in articles:
        token.check()
        try:
            if record(article).get('use_image',True):acquire(article,token)
        except TaskCancelledError:raise
        except (OSError,ValueError,RuntimeError):continue
