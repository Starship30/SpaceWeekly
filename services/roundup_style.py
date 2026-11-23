"""Shared editorial policy and derived event context; never changes LL2 fields."""
import json
import re
from collections import Counter
from datetime import date, timedelta

from ai.json_utils import AIResponseParseError
from utils.time import parse_datetime, _timezone


DEFAULT_ROUNDUP_RULE = '''使用自然、克制的中文航天解说口吻。开头使用程序提供的 opening，一周范围默认为“先来看一下过去一周的发射任务。”，其他范围明确起止日期。
按国家或国家加公司分组：航天科技集团任务为“中国方面”，不念集团全名；中国民营公司注明中国及公司；SpaceX 为“美国 SpaceX 方面”；Rocket Lab 来自美国，发射地点独立表述，不因在新西兰发射改变公司归属。其他机构按已知归属，未知时中性表达。
按 country_company_groups 组织，同组按 display_time 排序。每组首次明确日期；“当天”“第二天”等只指本组上一项任务，跨组重新交代日期。同公司同类任务可合并，保留各次日期、载荷数量和结果。
自然交代发射日期、适当时段、已知发射场、明确给出的具体工位、火箭、载荷名称/数量、有资料支持的目标轨道及发射结果。不要从火箭或历史任务猜工位或轨道，缺失字段直接省略，不解释缺失，不写成参数清单。
不使用“LL2记录显示”“根据LL2”等例行归因，不使用“先看重点任务”“再看另一任务”等空泛过渡。不反复总结同组数量和成功结果。
直接说本次发射的载荷，不要先念宣传任务全名再重复载荷。冗长编号通常留在屏幕，朗读只用已有载荷名或已核对简称。不要每次机械套用“执行某任务，搭载某卫星，发射成功”；可简洁地说成功发射相应载荷。回收不是必报字段，不扩写资料未给出的缩写全称。
背景只取有出处的一两项具体事实，不增加宣传评价、任务意义、趋势、首次或纪录。缺少背景就简短介绍，允许短于目标时长。可以省略整个不必要的背景断言；保留预测、用途、性质或争议断言时，必须保留其不确定性或来源差异，不能把未证实变成确定事实。
机构国别与发射地国家分别处理，发射结果与回收结果分别判断。部分收录或部分选择不能声称全球全部发射。结尾使用程序提供的 transition，自然转入其他航天新闻。
已核对中文名称优先；未核对名称保留原名，不根据本规则或示例自造专名。屏幕文字保留完整型号和工位编号。朗读可转换数字与编号读法，但对应的事实、任务ID、正式文字和出处不变。'''

NARRATION_RULE = '''正式资料、屏幕文字、口播和最终TTS输入分别保存，不能拼接屏幕字段写口播。
不要重复单位（如“03星卫星”），载荷已清楚时不再补“本次搭载一颗卫星”；单颗且身份明确可以不重复数量，多颗数量只自然交代一次。
同组全部成功可在组末统一说明，不能逐项成功后再重复总结；混合结果逐项准确交代，不从成功推断入轨。跨国家或公司重新建立日期，同组相邻日期可用当天、第二天加准确时段。
完整任务编号留在屏幕；优先使用events提供的spoken_payload。有出处的两组编号范围可简化为两组卫星，但与本期其他任务混淆时保留必要编号。不得改写未确认品牌或型号。
工位只使用events.pad_spoken短名称，不能朗读原始字段的历史别名和括号。没有可靠短名称时省略，不猜测。
有轨道资料时自然交代events.orbit，保持与载荷、任务ID的对应；同组目标轨道相同可在组末合并说明，不逐项重复。mission_target是任务目标，不是已核实的实际入轨结果；不能仅因发射成功就写成“已送入该轨道”。近地轨道不能推成太阳同步轨道，不从载荷用途猜轨道、高度或倾角；缺失或未知时省略，不补造。
不强加背景、意义或固定套话；保留自然完整语义段，units登记句子与任务的明确关联，不能按逗号拆成TTS请求。数字、日期和发音替换由最终TTS层统一处理。'''


def effective_rules(settings):
    from services.prompt_manager import find_preset, prompt_value
    return prompt_value(getattr(settings, 'roundup_prompt', ''), find_preset(settings.prompt_preset), 'roundup') + '\n本轮共同口播约束：\n' + NARRATION_RULE


def timezone_label(draft):
    zone = draft.get('timezone', 'Asia/Shanghai')
    return '北京时间' if zone == 'Asia/Shanghai' else '系统时间' if zone == 'system' else zone


def activity_context(draft, task_id):
    """Selected, actually launched events only; dates use the production timezone."""
    from services.launch_roundup import category
    zone=_timezone(draft.get('timezone','Asia/Shanghai'))
    first,last=date.fromisoformat(draft['start']),date.fromisoformat(draft['end'])
    included={s['id'] for s in draft['selection'] if s['included']}
    events=[];current=None
    from services.launch_display import effective_item
    for item in map(effective_item,draft['snapshot']['items']):
        when=parse_datetime(item.get('net',''))
        if when is None:continue
        when=when.astimezone(zone)
        if item['ll2_id']==task_id:current=when.date()
        if item['ll2_id'] in included and first<=when.date()<=last and category(item)=='actual':
            events.append((when,item['ll2_id']))
    events.sort()
    ordinal=next((n for n,(_,identifier) in enumerate(events,1) if identifier==task_id),None)
    counts=Counter(when.date() for when,_ in events)
    offset=max(0,min((last-first).days,(current-first).days if current else 0))
    start=first+timedelta(days=offset//7*7)
    start=min(start,max(first,last-timedelta(days=6)))
    end=min(last,start+timedelta(days=6))
    days=[start+timedelta(days=n) for n in range((end-start).days+1)]
    return dict(ordinal=ordinal,start=start.isoformat(),end=end.isoformat(),
        windowed=(last-first).days>=7,
        days=[dict(date=day.isoformat(),label=day.strftime('%m/%d') if start.month!=end.month else f'{day.day}日',
                   count=counts[day],selected=day==current) for day in days])


def opening(draft):
    first, last = date.fromisoformat(draft['start']), date.fromisoformat(draft['end'])
    as_of = parse_datetime(draft.get('as_of', ''))
    past = as_of is None or last <= as_of.astimezone(_timezone(draft.get('timezone', 'Asia/Shanghai'))).date()
    if (last - first).days == 6 and past:
        return '先来看一下过去一周的发射任务。'
    return f'先来看一下{first.year}年{first.month}月{first.day}日至{last.year}年{last.month}月{last.day}日的发射任务。'


def transition(draft, stats):
    return '接下来，关注本周的其他航天进展。' if '过去一周' in opening(draft) else '接下来，关注其他航天进展。'


def affiliation(item):
    from services.launch_display import resolve_launch,overrides
    view=resolve_launch(item);edits=overrides(item)
    provider = item.get('provider', '').strip()
    known = {
        'china aerospace science and technology corporation': ('中国方面', 0),
        '中国航天科技集团': ('中国方面', 0), 'casc': ('中国方面', 0),
        '中国航天科技集团公司': ('中国方面', 0), '中国航天科技集团有限公司': ('中国方面', 0),
        'spacex': ('美国 SpaceX 方面', 1), 'rocket lab': ('美国 Rocket Lab 方面', 2),
        'galactic energy': ('中国星河动力方面', 0), '星河动力': ('中国星河动力方面', 0),
    }
    if any(k in edits for k in ('provider','group_label','operator_country')):
        country='' if view['provider'].startswith(view['operator_country']) else view['operator_country']
        return dict(label=edits.get('group_label',(' '.join(filter(None,[country,view['provider']]))+'方面')),
                    rank=known.get(provider.casefold(),('',3))[1],basis='本期人工编辑')
    if provider.casefold() in known:
        label, rank = known[provider.casefold()]
        return dict(label=label, rank=rank, basis='用户确认的口播机构口径')
    country = view['operator_country'].strip()
    country = {'China': '中国', 'United States of America': '美国', 'United States': '美国',
               'New Zealand': '新西兰', 'Japan': '日本', 'India': '印度', 'Russia': '俄罗斯'}.get(country, country)
    provider = view['provider']
    if provider.startswith(country):country=''
    return dict(label=(country + ' ' + provider + '方面').strip() if provider else '其他任务',
                rank=3, basis='LL2机构国家字段' if country else '机构归属不足，未推断国家')


def pad_code(name):
    short=re.fullmatch(r'(SLC|LC)-(\d+[A-Z]?)',name,re.I)
    if short:return short[0].upper()
    match = re.search(r'(Space Launch Complex|Launch Complex|Launch Area)\s+(\d+[A-Z]?)\b', name, re.I)
    if not match:
        return ''
    prefix = {'space launch complex': 'SLC-', 'launch complex': 'LC-', 'launch area': ''}[match[1].lower()]
    return prefix + match[2].upper()


def payload_count(item):
    from services.launch_display import resolve_launch
    view=resolve_launch(item)
    return view['payload_count'],view['quantity_source']


def spoken_quantities(text):
    from services.launch_roundup import _numbers
    return {n for phrase in re.findall(r'(?:\d+|[零一二三四五六七八九十百千两]+)\s*(?:颗|枚|星|satellites?\b)', text, re.I)
            for n in _numbers(phrase)}


def payload_display(item):
    from services.launch_display import resolve_launch
    return resolve_launch(item)['payload']


def rocket_recovery(item):
    """Hide expendable/unknown vehicles; legacy explicit booster landings remain usable."""
    from utils.launch_status import recovery_states
    if item.get('rocket_reusable') is False:
        return ''
    from services.launch_display import resolve_launch
    text = resolve_launch(item)['recovery'] or ''
    # Legacy combined recovery text may also contain capsule landings.
    text = '; '.join(part.strip() for part in re.split(r'[;；\n]+', text)
                     if not re.match(r'\s*(?:飞船|航天器|Spacecraft)', part, re.I))
    states = recovery_states(text)
    if not states or any(word in text.casefold() for word in ('暂无','待确认','未知','unknown','pending','tbd')):
        return ''
    if set(states)=={'none'} and item.get('rocket_reusable') is not True:return ''
    if set(states)=={'none'}:return '不尝试回收'
    return text if set(states) - {'unknown','pending'} else ''


def event_context(item, draft):
    from services.launch_research import display_names
    from services.launch_display import effective_item
    item=effective_item(item)
    names = display_names(item)
    orbit = names['orbit'].strip()
    if orbit.casefold() in ('unknown', 'unknown orbit', 'n/a', 'tbd', '-', '未知', '待确认'):
        orbit = ''
    local = parse_datetime(names.get('net', ''))
    local = local.astimezone(_timezone(draft.get('timezone', 'Asia/Shanghai'))) if local else None
    period = ('凌晨' if local.hour < 6 else '早上' if local.hour < 9 else '上午' if local.hour < 12 else
              '中午' if local.hour < 13 else '下午' if local.hour < 18 else '傍晚' if local.hour < 20 else '晚上') if local else ''
    pad = names['pad_name']
    code = pad_code(pad)
    result = event_result(item,draft)
    count, basis = payload_count(item)
    return dict(task_id=item['ll2_id'], group=affiliation(item), payload_count=count, payload_count_basis=basis,
        display_time=local.isoformat() if local else '',
        date=local.date().isoformat() if local else '',
        date_reading=f'{local.month}月{local.day}日{period}' if local else '',
        timezone=timezone_label(draft), rocket=names['rocket'], name=names['name'],
        site_name=names['site_name'], pad_name=names['pad_name'] if pad else '', pad_code=code,
        legacy_site='',
        site_country=item.get('site_country', ''), launch_result=result,
        timing_basis='reported_launch' if item.get('status_id') in (3, 4, 6, 7, 9) else 'scheduled_time',
        recovery=rocket_recovery(item), pad_spoken=spoken_pad(names['pad_name'], code),
        spoken_payload=spoken_payload(item, draft, names['name']), orbit=orbit,
        orbit_basis='mission_target' if orbit else '')


def event_result(item,draft):
    from services.launch_display import effective_item
    item=effective_item(item)
    result={3:'发射成功',4:'发射失败',7:'发射部分失败'}.get(item.get('status_id'))
    if result:return result
    from services.launch_roundup import category
    return {'planned':'计划发射','delayed':'延期','cancelled':'取消','actual':'已发射，结果待确认',
            'pending':'状态待确认'}[category(item,parse_datetime(draft.get('as_of','')))]


def spoken_pad(name, code):
    # The primary source code is distinct from historical aliases in parentheses.
    if code:
        return code + ('号工位' if code.isdigit() else '工位')
    return name if re.fullmatch(r'[\u3400-\u9fff\d A-Z-]{1,24}工位', name) else ''


def spoken_payload(item, draft, name):
    """Shorten only explicit numbered satellite ranges, checking the entire selection."""
    from services.launch_research import display_names
    from services.launch_research import background_facts
    if not any(f['quote'] == item.get('payload', '') and re.search(r'\bsatellites?\b', f['quote'], re.I)
               for f in background_facts(item)):
        return payload_display(item)
    parts = name.split('、')
    matches = [re.fullmatch(r'([^\d、]+号)(\d{1,3})[—–至-](\d{1,3})星', part) for part in parts]
    if len(parts) > 1 and all(matches):
        bases = [m[1] for m in matches]
        selected = {s['id'] for s in draft['selection'] if s['included']}
        ambiguous = any(any(base in display_names(other)['name'] for base in bases)
                        for other in draft['snapshot']['items'] if other['ll2_id'] in selected and other['ll2_id'] != item['ll2_id'])
        if not ambiguous:
            from services.roundup_reading import chinese_number
            return '和'.join(bases) + '的' + ('两' if len(parts) == 2 else chinese_number(len(parts))) + '组卫星'
    return name if re.fullmatch(r'[^。\n]+号\d+(?:[—–至-]\d+)?星(?:、[^。\n]+号\d+(?:[—–至-]\d+)?星)*', name) else payload_display(item)


def narration_parts(draft, task_ids):
    """A conservative event-only proposal, with explicit identity on every unit."""
    from services.roundup_reading import chinese_number
    items = {i['ll2_id']: i for i in draft['snapshot']['items']}
    parts = []
    for group in groups(draft):
        ids = [i for i in group['task_ids'] if i in task_ids]
        if not ids:
            continue
        parts.append(dict(text=group['label'] + '，', task_ids=ids, display='group'))
        events = [event_context(items[i], draft) for i in ids]
        uniform_success = all(e['launch_result'] == '发射成功' for e in events)
        shared_orbit = events[0]['orbit'] if len(events) > 1 and all(e['orbit'] == events[0]['orbit'] for e in events) else ''
        previous = None
        for event in events:
            when = event['date_reading']
            if previous and event['timing_basis'] == previous['timing_basis']:
                delta = (date.fromisoformat(event['date']) - date.fromisoformat(previous['date'])).days
                period = re.sub(r'^\d+月\d+日', '', when)
                if delta in (0, 1):
                    when = ('当天' if delta == 0 else '第二天') + period
            place = event['site_name'] or event['legacy_site']
            if event['pad_spoken'] and event['pad_spoken'] != place:
                place += ('的' if place else '') + event['pad_spoken']
            text = when + '，' + event['rocket'] + ('从' + place if place else '')
            payload, count = event['spoken_payload'], event['payload_count']
            destination = '，目标是' + event['orbit'] if event['orbit'] and not shared_orbit else ''
            actual = event['timing_basis'] == 'reported_launch'
            if payload and actual:
                if count and count > 1:
                    text += '起飞。这次是一箭' + chinese_number(count) + '星，搭载的是' + payload + destination + '。'
                else:
                    text += '起飞，发射' + payload + destination + '。'
            else:
                mission=event['name'] if event['name'].endswith('任务') else event['name']+'任务'
                if actual:
                    text += '起飞，执行' + mission + destination + '。'
                else:
                    plan = '计划' if event['launch_result'] == '计划发射' else '原定'
                    text = when + '，' + event['rocket'] + plan + ('从' + place if place else '') + '发射，执行' + mission + destination + '。'
                if count:
                    text += ('搭载' if actual else '计划搭载') + chinese_number(count) + '颗卫星。'
            if not uniform_success and event['launch_result'] != '计划发射':
                text += {'延期':'任务已延期', '取消':'任务已取消'}.get(event['launch_result'], event['launch_result']) + '。'
            parts.append(dict(text=text, task_ids=[event['task_id']], display='task'))
            previous = event
        if uniform_success or shared_orbit:
            text = '本次发射取得成功' if len(ids) == 1 else ('两' if len(ids) == 2 else chinese_number(len(ids))) + '次发射均取得成功'
            if not uniform_success:text = ''
            if shared_orbit:text += ('，任务目标都是' if text else '这些任务的目标都是') + shared_orbit
            text += '。'
            parts.append(dict(text=text, task_ids=ids, display='summary'))
    return parts


def groups(draft):
    from services.launch_display import resolve_launch
    included = {s['id'] for s in draft['selection'] if s['included']}
    def order(item):
        group=affiliation(item);when=parse_datetime(resolve_launch(item).get('net') or '')
        return group['rank'],group['label'],when.timestamp() if when else float('inf')
    ordered = sorted((i for i in draft['snapshot']['items'] if i['ll2_id'] in included),
                     key=order)
    grouped = {}
    for item in ordered:
        label = affiliation(item)['label']
        grouped.setdefault(label, []).append(item['ll2_id'])
    return [dict(label=label, task_ids=ids) for label, ids in grouped.items()]


def style_context(draft, settings):
    from services.launch_roundup import statistics
    return dict(rules=effective_rules(settings), opening=opening(draft), transition=transition(draft, statistics(draft)), complete_scope=statistics(draft)['complete'],
                timezone=timezone_label(draft), country_company_groups=groups(draft),
                events=[event_context(i, draft) for i in draft['snapshot']['items']
                        if any(s['id'] == i['ll2_id'] and s['included'] for s in draft['selection'])])


def event_fact(item, draft):
    return dict(id='event', quote=json.dumps(event_context(item, draft), ensure_ascii=False),
        url=item.get('record_url') or item.get('ll2_source', {}).get('url', ''), title='LL2结构化事件及程序时区换算',
        published='', retrieved_at=item.get('ll2_source', {}).get('retrieved_at', draft['snapshot'].get('retrieved_at', '')),
        evidence_level='ll2_event', citation_status='structured_fields', event_status='snapshot_id',
        task_id=item['ll2_id'])


def validate_style(segments, context, *, single=False):
    for segment in segments:
        text = segment['spoken']
        if re.search(r'(?:[\d零一二三四五六七八九十]+星\s*卫星|卫星\s*卫星)', text):
            raise AIResponseParseError('口播出现重复卫星单位，请保留一个自然称呼')
        if re.search(r'(?:根据|据|按(?:照)?)\s*LL2|LL2\s*(?:记录|显示|指出|称)|先看重点任务|再看另一[项个]?任务', text, re.I):
            raise AIResponseParseError('口播请删除例行来源归因或空泛过渡；出处保留在资料中')
        if context and not context['complete_scope'] and re.search(r'全球.*(?:共|全部|完成|发射).*?(?:\d|[一二三四五六七八九十])|全球(?:同期)?全部发射', text):
            raise AIResponseParseError('只收录或选择部分任务，不能声称全球完整统计')
        counts = set()
        for identifier in segment.get('task_ids', []):
            if not any(s.get('task_id') == identifier and s.get('evidence_level') == 'll2_event' for s in segment.get('source_refs', [])):
                raise AIResponseParseError('每次任务须引用本次event事实，不能只串联名称')
            event = next(e for e in context['events'] if e['task_id'] == identifier)
            if event['payload_count'] is not None and not (event['payload_count'] == 1 and event['spoken_payload'] and event['spoken_payload'] in text):
                counts.add(str(event['payload_count']))
            screen = segment['screen']
            if event['rocket'] and event['rocket'] not in screen:
                raise AIResponseParseError('屏幕文字须保留完整火箭型号：' + event['rocket'])
            if event['pad_code'] and event['pad_code'] not in screen and event['pad_name'] not in screen:
                raise AIResponseParseError('屏幕文字须保留工位编号：' + event['pad_code'])
        if not counts <= spoken_quantities(text):
            raise AIResponseParseError('口播须自然交代已有证据的载荷数量；单颗且身份明确可省重复数量：' + ','.join(sorted(counts)))
    if single:
        return
    expected = [i for g in context['country_company_groups'] for i in g['task_ids']]
    actual = [i for s in segments for i in s['task_ids']]
    if actual != expected:
        raise AIResponseParseError('按国家/公司分组，同组按时间排序，每次任务只关联一次')
    for segment in segments:
        ids = set(segment['task_ids'])
        if ids and not any(ids <= set(g['task_ids']) for g in context['country_company_groups']):
            raise AIResponseParseError('任务段不能混入其他国家或公司组')
    for group in context['country_company_groups']:
        text = ''.join(s['spoken'] for s in segments if set(s['task_ids']) & set(group['task_ids']))
        if re.sub(r'\s', '', group['label']) not in re.sub(r'\s', '', text):
            raise AIResponseParseError('本组首次须明确机构归属：' + group['label'])
