"""Offline launch presentation; names, facts and edition overrides remain distinct."""
from functools import lru_cache
import json
import re
from resources import resource_path
from translator.dictionary import load_dictionary,dictionary_replace,exact_dictionary_translation

UNKNOWN={'','unknown','unknown pad','unknown orbit','n/a','tbd','tbc','-','未知','待确认','待补充','暂无回收信息'}
VOCABULARY={
 'organization_type':{'Military':'军方','Government':'政府机构','Commercial':'商业机构','Private':'商业机构','Multinational':'国际机构'},
 'orbit':{'Medium Earth Orbit':'中地球轨道','Low Earth Orbit':'近地轨道','Sun-Synchronous Orbit':'太阳同步轨道',
          'Geostationary Transfer Orbit':'地球同步转移轨道','Geostationary Orbit':'地球静止轨道','Polar Orbit':'极地轨道'},
 'operator_country':{'Russia':'俄罗斯','Russian Federation':'俄罗斯','China':'中国','United States of America':'美国',
                     'United States':'美国','New Zealand':'新西兰','India':'印度','Japan':'日本'},
}

TRANSLATION_FIELDS=dict(english_name='mission_label',rocket='rocket',upper_stage='rocket',site_name='location',pad_name='location',
 provider='organization',manufacturer='organization',customer='organization',orbit='orbit',description='description')


def mission_reference(original):
    """Translate standard group/flight syntax while retaining mission identifiers."""
    if re.fullmatch(r'[A-Z]{2,}-\d[A-Z0-9-]*',original):return original+'任务'
    group=re.fullmatch(r'(Starlink|SatNet)\s+(LEO\s+)?Group\s+(\d+(?:-\d+)?)(?:\s*\(Starship\s+Flight\s+(\d+)\))?',original,re.I)
    if group:
        family='星链' if group[1].casefold()=='starlink' else '卫星互联网'
        return family+('低轨' if group[2] else '')+'第'+group[3]+'组'+('（星舰第'+group[4]+'次飞行）' if group[4] else '')
    return ''


def valid_translation(entry,original,kind):
    """A reusable translation remains a candidate, never evidence of a launch fact."""
    if (not isinstance(entry,dict) or entry.get('validation_version')!=1 or entry.get('original')!=original
            or entry.get('type')!=kind or entry.get('status') not in ('candidate','translated','unchanged')):
        return False
    from translator.translator import _validate_translation_payload
    from ai.json_utils import AIResponseParseError
    try:
        _validate_translation_payload({'translation':entry.get('translation')},original=original,long_text=kind=='description')
        # Decimal vehicle variants such as 2.1b must retain their letter, too.
        codes=re.findall(r'\b\d+(?:\.\d+)*[A-Za-z]\b',original)+re.findall(r'\b[A-Za-z]+(-[A-Z]+)\b',original)
        if not all(code in entry['translation'] for code in codes):return False
        if kind=='mission_label':
            protected=[code for code in re.findall(r'\b[A-Z]{2,}[A-Z0-9]*(?:-\d+[A-Z0-9-]*)?\b|\bi[A-Z]{2,}\b',original)
                       if code not in ('LEO','GEO','GTO','SSO')]
            if any(code not in entry['translation'] for code in protected):return False
            if not re.search(r'[\u3400-\u9fff]',entry['translation']):return False
        if kind=='description':
            qualifiers=((r'\b(?:may|might|possibly|likely)\b','可能|或许|也许|据推测'),
                        (r'\b(?:expected|planned|scheduled|proposed)\b','预计|预期|计划|拟|预定'),
                        (r'\b(?:unknown|unconfirmed)\b','未知|未确认|尚不明确|待确认'))
            if any(re.search(source,original,re.I) and not re.search(target,entry['translation']) for source,target in qualifiers):return False
        return True
    except (AIResponseParseError,TypeError):return False


def translated_field(item,field,original):
    entry=item.get('field_translations',{}).get(field,{})
    return entry if valid_translation(entry,original,TRANSLATION_FIELDS.get(field)) else {}

@lru_cache(maxsize=4)
def _cached(path,modified,size):
    if modified is None:return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    return {k:v for k,v in data.items() if isinstance(v,dict) and isinstance(v.get('translation'),str)
            and v['translation'].strip() and v.get('status') not in ('rejected','conflict','failed')
            and (v.get('status') in ('confirmed','human_confirmed') or (v.get('source') in ('llm','web','dictionary','glossary') or v.get('source')=='web+llm' and v.get('source_url')) and
                 type(v.get('confidence')) in (float,int) and v['confidence']>=.5)}

def translations():
    path=resource_path('translator','translation_cache.json')
    try:s=path.stat();return _cached(path,s.st_mtime_ns,s.st_size)
    except FileNotFoundError:return {}

def text(value,field,entries=None):
    value=str(value or '').strip()
    if value.casefold() in UNKNOWN:return ''
    entries=load_dictionary() if entries is None else entries
    exact=exact_dictionary_translation(value,entries)
    if exact:return exact
    cached=next((v['translation'] for k,v in translations().items() if k.casefold()==value.casefold()),None)
    if cached:return cached
    result,matched=dictionary_replace(value,entries)
    for source,target in VOCABULARY.get(field,{}).items():
        if value.casefold()==source.casefold():return target
    if field=='rocket':
        # A configuration is a vehicle plus an upper stage, not a single unknown proper noun.
        result=re.sub(r'\s*[/ ]\s*(Fregat(?:-[A-Z]+)?)(?:\s*上面级)*\s*$',r' / \1 上面级',result)
    return result

def original_source(item):
    raw=item
    while isinstance(raw.get('source_original'),dict):
        candidate=raw['source_original']
        if candidate.get('ll2_id',item.get('ll2_id'))!=item.get('ll2_id'):raise ValueError('原始发射记录的任务身份不一致')
        raw=candidate
    return raw


def overrides(item,entries=None):
    edits=item.get('edition_fields',{})
    if not isinstance(edits,dict):raise ValueError('本期发射修改格式无效；原始资料保留')
    if item.get('edition_version',0)>=2:
        for key,value in edits.items():
            if key=='payload_count':
                if value is not None and (type(value) is not int or not 0<=value<=100000):raise ValueError('本期载荷数量无效')
            elif key=='status_id':
                if value is not None and type(value) is not int:raise ValueError('本期发射结果无效')
            elif not isinstance(value,str):raise ValueError('本期画面字段应为文字：'+key)
        return edits
    raw=original_source(item)
    if edits and entries is None:entries=load_dictionary()
    # Old forms saved untouched defaults too. Keep changed values, not copied descriptions/count guesses.
    result={}
    for key,value in edits.items():
        original=raw.get('english_name' if key=='name' else key)
        fragments={k:v for k,v in entries.items() if k.casefold()!=str(original or '').casefold()}
        copied=(original,text(original,key,entries),dictionary_replace(str(original or ''),entries)[0],dictionary_replace(str(original or ''),fragments)[0])
        if key!='payload_count' and value not in copied and not (key=='payload' and value==raw.get('description')):
            result[key]=value
    for key in ('status_id','status_name'):
        if key in raw and item.get(key)!=raw[key]:result[key]=item.get(key)
    return result

def effective_item(item):
    result=dict(item)
    for k,v in overrides(item).items():
        if k in ('net','status_id','status_name','provider','operator_country','organization_type'):result[k]=v
    return result


def edit_fields(item,fields):
    """The table, inline name form and full editor share edition-only persistence."""
    from copy import deepcopy
    if 'name' in fields and (not isinstance(fields['name'],str) or not fields['name'].strip() or len(fields['name'])>300):
        raise ValueError('任务名称不能为空，且最多300个字符')
    edits=dict(overrides(item),**fields)
    overrides(dict(item,edition_version=2,edition_fields=edits))
    item.setdefault('source_original',deepcopy(item))
    if item.get('edition_version',0)<2:item.setdefault('edition_legacy',deepcopy(item.get('edition_fields',{})))
    item.update(edition_version=2,edition_fields=edits)

def place(site,pad,source_site='',source_pad=''):
    """Only strip a known site prefix or a typed launch-complex code, not arbitrary substrings."""
    if pad.casefold() in UNKNOWN:pad=''
    match=re.search(r'\b(SLC|LC|Space Launch Complex|Launch Complex)[- ]*(\d+[A-Z]?)\b',pad,re.I)
    if match:pad=('SLC-' if match[1].casefold() in ('slc','space launch complex') else 'LC-')+match[2].upper()
    elif (area:=re.search(r'\bLaunch Area\s+(\d+[A-Z]?)\b',pad,re.I)):pad=area[1].upper()+' 工位'
    elif (related:=re.search(r'^(.*Launch Complex\s+)(\d+[A-Z]?)$',source_pad,re.I)) and source_site.startswith(related[1]):pad='LC-'+related[2].upper()
    elif site and pad.startswith(site):pad=pad[len(site):].lstrip(' ·,，-')
    if pad==site:pad=''
    return site,pad,' · '.join(v for v in (site,pad) if v)

def resolve_launch(item,entries=None):
    entries=load_dictionary() if entries is None else entries
    raw=original_source(item);edits=overrides(item,entries);record=item.get('research',{})
    original=item.get('english_name','');name=text(original,'name',entries);origin='原文 / 名称待核对'
    if name!=original:origin='词典或既有译文（名称对应）'
    from utils.text import mission_display_name
    base=mission_display_name(original)
    confirmed=exact_dictionary_translation(original,entries) or exact_dictionary_translation(base,entries)
    human_term=any(k.casefold() in (original.casefold(),base.casefold()) and v.get('human_status')=='confirmed' for k,v in entries.items())
    if confirmed:name=confirmed;origin='术语词典'
    from services.launch_research import confirmed_values
    supported=confirmed_values(record,item)
    reference=translated_field(item,'english_name',original)
    if not reference:
        from translator.cache import get_cached_translation
        cached=get_cached_translation(original)
        if valid_translation(cached,original,'mission_label'):reference=cached
    if supported.get('chinese_name') and not human_term:
        name=supported['chinese_name'];origin='有来源支持的正式名称'
    elif not confirmed and mission_reference(original):
        name=mission_reference(original);origin='任务代号与组次的中文显示（非正式名称）'
    elif not confirmed and reference:
        name=reference['translation'];origin='任务代号（保留原码）' if reference.get('source')=='code_label' else '任务名参考译文（非正式名称）'
    for candidate in (record,record.get('previous_evidence',{})):
        identity=candidate.get('input',{})
        if (identity.get('ll2_id')==item.get('ll2_id') and identity.get('english_name')==original and candidate.get('chinese_name')):
            if name==original:name=candidate['chinese_name'];origin='已有名称译文（背景另行核对）'
    manual=record.get('manual',{})
    if record.get('human_status')=='confirmed' and manual.get('chinese_name'):
        name=manual['chinese_name'];origin='任务人工确认'
    if 'name' in edits:name=edits['name'];origin='本期人工编辑'
    result=dict(name=name,name_source=origin)
    for field in ('rocket','site','site_name','pad_name','provider','orbit','operator_country','organization_type','upper_stage','manufacturer','customer'):
        original=str(raw.get(field,item.get(field,'')) or '')
        translated=translated_field(item,field,original)
        if translated.get('translation')==original:translated={}
        human=next((v['translation'] for k,v in translations().items() if k.casefold()==original.casefold() and v.get('status') in ('confirmed','human_confirmed')),'')
        result[field]=(edits[field] if field in edits else exact_dictionary_translation(original,entries)
                       or human or translated.get('translation') or text(original,field,entries))
    # Legacy edits with untranslated defaults still benefit from confirmed terms and cached translations.
    if item.get('edition_version',0)<2:
        for field in ('rocket','site_name','pad_name','orbit','provider'):result[field]=text(result[field],field,entries)
    stage=re.search(r'\s*/?\s*(Fregat(?:-[A-Z]+)?)(?:\s*上面级)*\s*$',result['rocket'])
    if stage:
        if 'upper_stage' not in edits:result['upper_stage']=stage[1]
        elif 'rocket' not in edits:
            result['rocket']=result['rocket'][:stage.start()].rstrip(' /')+(' / '+result['upper_stage'].removesuffix('上面级').rstrip()+' 上面级' if result['upper_stage'] else '')
    if raw.get('site')==raw.get('pad_name') and 'site' not in edits:result['site']=result['pad_name']
    site,pad,location=place(result['site_name'] if 'site_name' in edits else result['site_name'] or result['site'],result['pad_name'],
                          raw.get('site_name',''),raw.get('pad_name','') if 'pad_name' not in edits or item.get('edition_version',0)<2 else '')
    result.update(site_name=site,pad_name=pad,location=location)
    payloads=[p for p in item.get('payloads',[]) if isinstance(p,dict)]
    payload='; '.join(p['name'] for p in payloads if isinstance(p.get('name'),str) and p['name'].strip())
    result['payload']=edits['payload'] if 'payload' in edits else text(payload,'payload',entries)
    amounts=[p.get('amount') for p in payloads]
    count=sum(amounts) if amounts and all(type(n) is int and n>=0 for n in amounts) else None
    result['payload_count']=edits.get('payload_count',count)
    result['quantity_source']='本期人工确认' if 'payload_count' in edits else 'LL2 rocket.payloads[].amount' if count is not None else 'LL2未提供载荷明细或有效数量；未从介绍推断'
    result['summary']=edits.get('summary',manual.get('summary') or record.get('summary',''))
    result['description_translation']=translated_field(item,'description',str(raw.get('description','') or '')).get('translation','')
    result['title']=edits.get('title',result['rocket'])
    for field in ('net','status_id','status_name'):result[field]=edits.get(field,item.get(field))
    result['recovery']=edits.get('recovery',item.get('rocket_recovery',item.get('recovery','')))
    result['organization_type']=result['organization_type'] or ('军方' if raw.get('provider')=='Russian Space Forces' else '')
    sources={};cached={k.casefold() for k in translations()}
    human_cached={k.casefold() for k,v in translations().items() if v.get('status') in ('confirmed','human_confirmed')}
    for field in result:
        original=str(raw.get(field,'') or '')
        sources[field]=('本期人工编辑' if field in edits else '术语词典（名称对应）' if exact_dictionary_translation(original,entries) else
                        '人工确认的术语缓存' if original.casefold() in human_cached else
                        'AI参考译文（未核对）' if translated_field(item,field,original) else
                        '既有译文缓存（背景另行核对）' if original.casefold() in cached else '原始字段 / 格式规范化')
    sources.update(name=origin,payload_count=result['quantity_source'],location='发射场与工位结构化关系')
    if result['description_translation']:sources['description_translation']='LL2原始说明的AI参考译文（未独立核对）'
    result['field_sources']=sources
    return result
