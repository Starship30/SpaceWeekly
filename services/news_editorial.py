"""Conservative event grouping and source checks before weekly narration."""
from copy import deepcopy
from datetime import date, datetime, time
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from models.article import Article
from models.news import News, is_news_url
from utils.time import parse_datetime, _timezone


_FLIGHT = re.compile(
    r'(?:星舰|starship)[\s\w’\x27-]{0,28}?(?:flight(?:[\s-]+test)?|ift|第)[\s#-]*(\d+|[一二三四五六七八九十百]+)(?!\d)'
    r'|(?:第\s*)?(\d+)(?:th|st|nd|rd|\s*次)[\s-]*(?:starship[\s-]+(?:test[\s-]+)?flight|星舰试飞)', re.I)
_PREVIEW = re.compile(r'\b(?:how to watch|when to watch|what to expect|everything to know|preview|upcoming|set to launch|to attempt|to launch|prepares? to)\b|前瞻|预告|直播指南|如何观看', re.I)
_FORECAST = re.compile(r'\b(?:will|plans? to|scheduled|expected to|set to|can it)\b|将于|将(?:发射|升空|试飞|尝试)|计划[在于试发执]|预计|即将', re.I)
_LIVE = re.compile(r'\blive(?: updates| coverage)?\b|\blift(?:ed)? off\b|\blaunch(?:ed|es)\b|直播|已起飞|已发射|飞行中|升空', re.I)
_ANALYSIS = re.compile(r'\b(?:analysis|what we learned|what[’\x27]s next|investigat\w+|why)\b|分析|解读|调查|复盘', re.I)
_TERMINAL = re.compile(r'\b(?:landed|splashed down|(?:caught|catches|catching) (?:the |its |Starship )?(?:upper stage|ship|booster|rocket)|returned to (?:Earth|its launch site)|was caught|was lost|broke apart|exploded|completed (?:its |the )?(?:test flight|flight|mission)|successfully (?:completed|landed|returned|deployed)|failed to (?:land|return|deploy))\b|(?:试飞|飞行|任务)(?:已经?|已|正式|现)?(?:完成|成功|失败)|完成(?:了)?(?:试飞|飞行)|成功(?:着陆|返回|回收|部署|捕获)|(?:溅落|着陆|解体|失联|回收失败|被(?:机械臂|塔架)捕获)', re.I)
_ORBIT = re.compile(r'\b(?:reached|entered|powers to|is in) (?:the |a )?(?:target )?orbit\b|入轨',re.I)
_ENDED = re.compile(r'\b(?:flight|mission|test) (?:has )?(?:ended|concluded|is over)\b|(?:试飞|飞行|任务)(?:已经?|已|正式)?(?:结束|告终)',re.I)
_REPORT = re.compile(r'\b(?:report|study|investigation|paper)\b|报告|论文|调查|研究', re.I)
_DISCLOSURE = re.compile(r'\b(?:releases?|publishes?|issues?|announces?)\b|发布|公布|发表|出炉|开展|启动|通报',re.I)
_CONCLUSION = re.compile(r'\b(?:found|concluded|determined|showed|revealed|identified|attributed|due to|caused by|still under investigation|cause remains unknown)\b|发现|认定|结论(?:是|为)|显示|证实|由于|导致|故障原因(?:是|为)|原因(?:仍在调查|尚未确定|尚不明确)', re.I)
_PLACEHOLDER = re.compile(r'待补资料|飞后结果待补|正文(?:未获取|缺失)|抓取失败|资料(?:不足|缺失|待补)|暂无(?:结论|正文|资料)|此处填|待补充|\[.{0,30}(?:待补|占位).{0,20}\]', re.I)
_DANGLING = re.compile(r'(?:报告(?:中)?|研究(?:人员|团队)?|调查|论文)(?:提到|指出|称|发现|显示|表明|表示)(?:了)?\s*[，,:：]*(?=$|[。！？；…]|\n)', re.M)
_VOYAGER = re.compile(r'(?:旅行者|航海家|voyager)\s*[- ]?\s*([12一二])(?:号|\b)?',re.I)
_LIGHT_DAY = re.compile(r'(?:一|1)\s*光[日天]|\b(?:one|1|a)\s+light[-‐– ]day\b',re.I)
_REACHED = re.compile(r'\b(?:reaches?|reached|has passed|passed|crossed|is now|became|becomes)\b|(?:已|现已)?(?:达到|抵达|跨过|跨越|突破)',re.I)
_CALENDAR_DATE = re.compile(r'\d{1,2}月\d{1,2}日|\d{4}年|\d{4}-\d{2}-\d{2}')
_METRICS = {
    '距地距离':re.compile(r'(\d+(?:\.\d+)?)\s*((?:(?:billion|million)\s+)?(?:km\b|kilomet(?:er|re)s?\b)|亿公里|万公里|公里)',re.I),
    '信号单程时间':re.compile(r'(?:单程|one[- ]way)[^\d,，。！？；]{0,28}(\d+(?:\.\d+)?)\s*(?:小时|hours?\b)|(\d+(?:\.\d+)?)\s*(?:小时|hours?\b)[^\d,，。！？；]{0,12}(?:单程|one[- ]way|to reach)',re.I),
    '信号往返时间':re.compile(r'(?:往返|round[- ]trip|two[- ]way)[^\d,，。！？；]{0,28}(\d+(?:\.\d+)?)\s*(?:小时|hours?\b)|(\d+(?:\.\d+)?)\s*(?:小时|hours?\b)[^\d,，。！？；]{0,12}(?:往返|round[- ]trip|two[- ]way)',re.I),
}


class MissingFacts(ValueError):
    def __init__(self, missing, reason, *, status='待补资料'):
        super().__init__(reason)
        self.missing, self.status = missing, status


def cutoff_for(date_range, timezone):
    zone=_timezone(timezone)
    now=datetime.now(zone)
    if not date_range:return now
    return min(now,datetime.combine(date.fromisoformat(date_range[1]),time.max,zone))


def before_cutoff(stamp, cutoff):
    published=parse_datetime(stamp)
    return published is not None and published<=cutoff


def _flight_numbers(text):
    from services.launch_roundup import _numbers
    numbers=set()
    for first,second in _FLIGHT.findall(unicodedata.normalize('NFKC',text)):
        value=first or second
        numbers.add(str(int(value)) if value.isdecimal() else next(iter(_numbers(value+'次')),value))
    return numbers


def event_key(article):
    data=article.launch_data or {}
    markers={str(row['event_id']).strip().casefold() for row in [data,*article.evidence]
             if isinstance(row,dict) and isinstance(row.get('event_id'),str) and row['event_id'].strip()}
    if len(markers)==1:return 'event:'+markers.pop()
    if markers:return None
    title=article.news.title
    link=unquote(urlsplit(article.news.url).path) if is_news_url(article.news.url) else ''
    ids=_flight_numbers(title+' '+link)
    if not ids:
        ids={number for row in article.evidence if isinstance(row,dict) and is_news_url(row.get('url'))
             for number in _flight_numbers(str(row.get('quote') or ''))}
    if len(ids)==1:return 'starship:'+ids.pop()
    if ids:return None
    if voyager:=voyager_fact(article):
        return f'voyager:{voyager[0]}:light-day:{voyager[1]}'
    if match:=re.search(r'\bUSSF[\s-]+(\d+)\b',title,re.I):return 'ussf:'+match[1]
    if not article.news.manual_id and data.get('ll2_id'):return 'll2:'+str(data['ll2_id'])
    return None


def voyager_fact(article):
    title=article.news.title
    subjects=_voyager_subjects(title)
    lines=[title,*sentences(article.news.summary),*sentences(article.body[:3000])]
    known=None
    for line in lines:
        ids=_voyager_subjects(line) or subjects
        if len(ids)!=1 or not _LIGHT_DAY.search(line):continue
        if _FORECAST.search(line):state='planned'
        elif _REACHED.search(line):state='reached'
        else:continue
        current=(ids.pop(),state,line)
        if known is None:known=current
        if current[:2]==known[:2] and _CALENDAR_DATE.search(line):return current
    return known


def _voyager_subjects(text):
    return {match.translate(str.maketrans('一二','12')) for match in _VOYAGER.findall(text)}


def measurements(line):
    for label,pattern in _METRICS.items():
        if label=='距地距离' and not re.search(r'距(?:离)?地球|距地|离地球|与地球的距离|from earth',line,re.I):continue
        for match in pattern.finditer(line):
            if label=='距地距离':
                unit=match[2].lower()
                scale=dict(亿公里=1e8,万公里=1e4).get(unit,1e9 if 'billion' in unit else 1e6 if 'million' in unit else 1)
                value=float(match[1])*scale
            else:value=float(match[1] or match[2])
            yield label,value,match[0]


def conflicts(articles):
    by_scope={}
    for article in articles:
        milestone=voyager_fact(article)
        for line in (article.news.title,*facts(article)):
            if milestone and (subjects:=_voyager_subjects(line)) and subjects!={milestone[0]}:continue
            event_date=_CALENDAR_DATE.search(line)
            for label,value,wording in measurements(line):
                scope=label,bool(_FORECAST.search(line)),event_date[0] if event_date else ''
                by_scope.setdefault(scope,[]).append((article.news.key,value,wording))
    conflicts={}
    for (label,_planned,_date),readings in by_scope.items():
        if any(a!=b and abs(x-y)>max(x,y)*.02 for a,x,_ in readings for b,y,_ in readings):
            conflicts[label]=' / '.join(dict.fromkeys(text for _,_,text in readings))
    return conflicts


def without_conflicts(text, labels):
    return ' '.join(line for line in sentences(text) if not any(_METRICS[label].search(line) for label in labels))


def merge_preview(articles, events):
    """Build an editable spoken preview using complete source facts."""
    groups={};warnings={};cited=[];blocks=[];missing=[];missing_sources=[]
    for article in articles:
        groups.setdefault(event_key(article) or article.news.key,[]).append(article)
    groups=sorted(groups.values(),key=lambda group:bool(
        (milestone:=voyager_fact(group[0])) and milestone[1]=='planned' or _FORECAST.search(group[0].news.title)))
    for group in groups:
        labels=conflicts(group) if len(group)>1 else {}
        warnings.update(labels)
        best=max(group,key=lambda a:len(list(facts(a))))
        metadata=events.get(best.news.key,{})
        labels={**metadata.get('conflicts',{}),**labels}
        warnings.update(labels)
        clean=writing_article(best,{**metadata,'conflicts':labels})
        milestone=voyager_fact(clean)
        try:
            if metadata.get('phase')=='pending':
                raise MissingFacts('实际飞后结果','已知试飞结束，缺少可信结果',status='飞后结果待补')
            require_material(clean)
            if voyager_fact(best) and not milestone:
                raise MissingFacts('旅行者探测器的里程碑及实际状态','可用来源存在数字冲突，删去争议内容后无法确认核心事实')
            line=milestone[2] if milestone else next(iter(facts(clean)),clean.news.title)
            check_spoken(line)
        except (MissingFacts,ValueError) as error:
            missing.append(f'{best.news.title} · {getattr(error,"missing",str(error))}')
            missing_sources.extend(a.news.key for a in group);continue
        title=clean.news.title.rstrip('。！？')
        if milestone:
            probe,state,_=milestone
            subject=f'旅行者{probe}号'
            title=f'{subject}'+('已达到距地球一光日的里程碑' if state=='reached' else '预计达到距地球一光日的里程碑')
            if not _VOYAGER.search(line):line=f'{subject}{line}'
            if not _CALENDAR_DATE.search(line):
                line=title
        if not _CALENDAR_DATE.search(line) and (stamp:=parse_datetime(best.news.published)):
            lead=f'{stamp.month}月{stamp.day}日的报道显示，'
        else:lead=''
        blocks.append((title,lead+line.rstrip('。！？.!?')+'。'))
        for source in group:
            if not is_news_url(source.news.url):continue
            quote=(voyager_fact(source) or (None,None,next(iter(facts(source)),source.news.title)))[2]
            cited.append(dict(url=source.news.url,title=source.news.title,published=source.news.published,quote=quote))
    title='；'.join(head for head,_ in blocks) or '相关新闻合稿'
    spoken=''.join(line if index==0 else '另外，'+line for index,(_,line) in enumerate(blocks))
    return dict(title=title,summary=spoken,spoken='' if missing else spoken,evidence=cited,
                conflicts=warnings,missing=missing,missing_sources=missing_sources)


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[。！？；])|(?<=[.!?;])\s+|\n+',text) if s.strip()]


def facts(article):
    seen={' '.join(article.news.title.split()).casefold().rstrip('。.!?:：')}
    for text in (article.body,article.news.summary,*(r.get('quote','') for r in article.evidence if isinstance(r,dict))):
        for sentence in sentences(text[:24000]) if isinstance(text,str) else ():
            key=' '.join(sentence.split()).casefold().rstrip('。.!?:：')
            if (len(key)>=8 or outcome(sentence)) and key not in seen and not _PLACEHOLDER.search(sentence) and not _DANGLING.search(sentence):
                seen.add(key)
                yield sentence


def outcome(text, *, terminal=False):
    return bool((_TERMINAL.search(text) or not terminal and _ORBIT.search(text)) and not _FORECAST.search(text)
                and not re.search(r'尚[未无]|还未|据传|未经证实|\b(?:could|might|would|unconfirmed|rumou?r)\b',text,re.I))


def _terminal_facts(article):
    key=event_key(article) or ''
    for line in (article.news.title,*list(facts(article))[:5]):
        if not outcome(line,terminal=True):continue
        numbers=_flight_numbers(line)
        if not numbers or not key.startswith('starship:') or numbers=={key.partition(':')[2]}:yield line


def phase(article):
    title=article.news.title
    lead=title.split('—',1)[0]
    if _PREVIEW.search(title) or _FORECAST.search(lead) and not _LIVE.search(lead):return 'preview'
    if any(_terminal_facts(article)):
        return 'analysis' if _ANALYSIS.search(title) else 'result'
    if _LIVE.search(title) or outcome(lead):return 'live'
    if _FORECAST.search(title):return 'preview'
    if _ANALYSIS.search(title):return 'analysis'
    return 'other'


def _terminal(article,cutoff):
    data=article.launch_data or {}
    return data.get('status_id') in (3,4,7,9) and before_cutoff(data.get('net') or article.news.published,cutoff)


def recommend(selected, candidates, launches, cutoff, *, protected=()):
    """Choose one anchor per unambiguous event without discarding its source articles."""
    grouped={};launch_keys=set()
    active=[a for a in selected if a.news.manual_id or before_cutoff(a.news.published,cutoff)]
    for article in candidates:
        if not article.news.manual_id and before_cutoff(article.news.published,cutoff) and (key:=event_key(article)):
            grouped.setdefault(key,[]).append(writing_article(article,{'cutoff':cutoff.isoformat()}))
    for article in launches:
        if isinstance(article,dict):
            article=Article(News('LL2',article.get('english_name') or article.get('launch_name',''),
                                 article.get('net',''),'',article.get('record_url','')),'','LL2',article)
        if _terminal(article,cutoff):launch_keys.add(event_key(article))
    output=[];events={};seen=set();protected=set(protected)
    for article in active:
        key=event_key(article) if not article.news.manual_id else None
        if not key:
            output.append(article);continue
        if key in seen:
            if article.news.key in protected and all(a.news.key!=article.news.key for a in output):output.append(article)
            continue
        seen.add(key);members=grouped.get(key,[article]);stages={a.news.key:phase(a) for a in members}
        milestone=key.startswith('voyager:')
        results=[a for a in members if stages[a.news.key]=='result' or stages[a.news.key]=='analysis' and any(_terminal_facts(a))]
        ranked=results or members
        stage_order={'preview':0,'other':1,'live':2,'analysis':3,'result':4}
        main=max(ranked,key=lambda a:(0 if milestone else stage_order[stages[a.news.key]],len(list(facts(a))),parse_datetime(a.news.published)))
        conflicts_found=conflicts(ranked) if len(ranked)>1 else {}
        sources=ranked if milestone or not results else results
        evidence=[];known=set(facts(main))|{main.news.title}
        for source in sources:
            if source.news.key==main.news.key or not is_news_url(source.news.url):continue
            quotes=list(facts(source))[:12]
            if milestone or outcome(source.news.title):quotes.insert(0,source.news.title)
            for quote in quotes:
                if quote not in known:
                    numbers=_flight_numbers(quote)
                    if numbers and key.startswith('starship:') and numbers!={key.partition(':')[2]}:continue
                    evidence.append(dict(url=source.news.url,title=source.news.title,published=source.news.published,quote=quote))
                    known.add(quote)
        pending=not results and (key in launch_keys or any(
            stages[a.news.key]!='preview' and any(_ENDED.search(s) and not _FORECAST.search(s) for s in (a.news.title,*facts(a)))
            for a in members))
        events[main.news.key]=dict(key=key,related=list(dict.fromkeys(a.news.key for a in members if a.news.key!=main.news.key)),
                                  phase='pending' if pending else stages[main.news.key],evidence=evidence[:8],cutoff=cutoff.isoformat())
        if conflicts_found:events[main.news.key]['conflicts']=conflicts_found
        output.append(main)
        if article.news.key!=main.news.key and article.news.key in protected:output.append(article)
    return list({a.news.key:a for a in output}.values()),events


def writing_article(article, event):
    value=deepcopy(article)
    cutoff=parse_datetime(event.get('cutoff'))
    if cutoff:
        value.evidence=[row for row in value.evidence if isinstance(row,dict) and
                        (row.get('manual') is True or before_cutoff(row.get('published'),cutoff))]
    value.evidence.extend(event.get('evidence',[]))
    if title:=event.get('result_title'):
        value.news.title=title
        value.body=''
        value.news.summary=''
    if labels:=event.get('conflicts'):
        clean=lambda text:without_conflicts(text,labels)
        safe_title=clean(value.news.title)
        value.body=clean(value.body);value.news.summary=clean(value.news.summary)
        value.evidence=[row for row in value.evidence if not any(_METRICS[label].search(row.get('quote','')) for label in labels)
                        or row.get('manual')]
        if not safe_title:
            milestone=voyager_fact(article)
            if milestone and not any(_METRICS[label].search(milestone[2]) for label in labels):
                safe_title=milestone[2]
        value.news.title=safe_title or '相关新闻资料待核对'
        if voyager_fact(article) and not voyager_fact(value):
            value.news.title='相关新闻资料待核对';value.body='';value.news.summary='';value.evidence=[]
    return value


def retry_material(article, sources, event, token, cutoff):
    from downloader.client import public_url
    from services.weekly_report import _parse_article

    refreshed={}
    for known in (article,*sources):
        token.check()
        if (known.news.manual_id or not public_url(known.news.url)
                or not before_cutoff(known.news.published,cutoff)):
            continue
        fetched=_parse_article(deepcopy(known.news),token,cutoff=cutoff)
        if fetched is None or fetched.parser.casefold().replace('_',' ')=='rss summary':continue
        fetched.evidence=known.evidence
        refreshed[known.news.key]=fetched
    if event.get('phase')=='pending':
        for source in [refreshed.get(known.news.key,known) for known in (article,*sources)]:
            if phase(source) not in ('result','analysis'):continue
            results=list(_terminal_facts(source))
            if not results or not is_news_url(source.news.url):continue
            event.update(phase='result',result_title=source.news.title,
                         evidence=[dict(url=source.news.url,title=source.news.title,published=source.news.published,quote=line)
                                   for line in results[:8]])
            break
    return refreshed


def require_material(article):
    source=list(facts(article))
    title=article.news.title
    if _REPORT.search(title) and (_DISCLOSURE.search(title) or not source) and not _CONCLUSION.search(title) and not any(
            _CONCLUSION.search(s) or _LIVE.search(s) or outcome(s) for s in source):
        raise MissingFacts('报告、研究或调查的主要结论', '来源只确认了报告或调查，未提供可核对的结论')
    if not source and not (outcome(title) or _CONCLUSION.search(title) or _FORECAST.search(title) or voyager_fact(article)):
        raise MissingFacts('事件的具体事实或已确认进展', '缺少可核对的正文、摘要或其他来源')


def check_spoken(text):
    if _PLACEHOLDER.search(text) or _DANGLING.search(text) or re.search(r'[：:，、…]\s*$',text):
        raise ValueError('口播包含未完成的引语或资料占位文字')
