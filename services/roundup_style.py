"""Shared editorial policy and derived event context; never changes LL2 fields."""
import json
import re
from datetime import date

from ai.json_utils import AIResponseParseError
from utils.time import parse_datetime, _timezone


DEFAULT_ROUNDUP_RULE = '''使用自然、克制的中文航天解说口吻。开头使用程序提供的 opening，一周范围默认为“先来看一下过去一周的发射任务。”，其他范围明确起止日期。
按国家或国家加公司分组：航天科技集团任务为“中国方面”，不念集团全名；中国民营公司注明中国及公司；SpaceX 为“美国 SpaceX 方面”；Rocket Lab 来自美国，发射地点独立表述，不因在新西兰发射改变公司归属。其他机构按已知归属，未知时中性表达。
按 country_company_groups 组织，同组按 display_time 排序。每组首次明确日期；“当天”“第二天”等只指本组上一项任务，跨组重新交代日期。同公司同类任务可合并，保留各次日期、载荷数量和结果。
自然交代发射日期、适当时段、已知发射场、明确给出的具体工位、火箭、载荷名称/数量及发射结果。不要从火箭或历史任务猜工位，缺失字段直接省略，不解释缺失，不写成参数清单。
不使用“LL2记录显示”“根据LL2”等例行归因，不使用“先看重点任务”“再看另一任务”等空泛过渡。不反复总结同组数量和成功结果。
直接说本次发射的载荷，不要先念宣传任务全名再重复载荷。冗长编号通常留在屏幕，朗读只用已有载荷名或已核对简称。不要每次机械套用“执行某任务，搭载某卫星，发射成功”；可简洁地说成功发射相应载荷。回收不是必报字段，不扩写资料未给出的缩写全称。
背景只取有出处的一两项具体事实，不增加宣传评价、任务意义、趋势、首次或纪录。缺少背景就简短介绍，允许短于目标时长。可以省略整个不必要的背景断言；保留预测、用途、性质或争议断言时，必须保留其不确定性或来源差异，不能把未证实变成确定事实。
机构国别与发射地国家分别处理，发射结果与回收结果分别判断。部分收录或部分选择不能声称全球全部发射。结尾使用程序提供的 transition，自然转入其他航天新闻。
已核对中文名称优先；未核对名称保留原名，不根据本规则或示例自造专名。屏幕文字保留完整型号和工位编号。朗读可转换数字与编号读法，但对应的事实、任务ID、正式文字和出处不变。'''


def effective_rules(settings):
    from services.prompt_manager import find_preset, prompt_value
    return prompt_value(getattr(settings, 'roundup_prompt', ''), find_preset(settings.prompt_preset), 'roundup')


def timezone_label(draft):
    zone = draft.get('timezone', 'Asia/Shanghai')
    return '北京时间' if zone == 'Asia/Shanghai' else '系统时间' if zone == 'system' else zone


def opening(draft):
    first, last = date.fromisoformat(draft['start']), date.fromisoformat(draft['end'])
    as_of = parse_datetime(draft.get('as_of', ''))
    past = as_of is None or last <= as_of.astimezone(_timezone(draft.get('timezone', 'Asia/Shanghai'))).date()
    if (last - first).days == 6 and past:
        return '先来看一下过去一周的发射任务。'
    return f'先来看一下{first.year}年{first.month}月{first.day}日至{last.year}年{last.month}月{last.day}日的发射任务。'


def transition(draft, stats):
    summary = f"本期关注的{stats['actual']}次发射均取得成功。" if stats['total'] and stats['actual'] == stats['total'] == stats['results']['success'] else ''
    return summary + ('接下来，关注本周的其他航天进展。' if '过去一周' in opening(draft) else '接下来，关注其他航天进展。')


def affiliation(item):
    provider = item.get('provider', '').strip()
    known = {
        'china aerospace science and technology corporation': ('中国方面', 0),
        '中国航天科技集团': ('中国方面', 0), 'casc': ('中国方面', 0),
        'spacex': ('美国 SpaceX 方面', 1), 'rocket lab': ('美国 Rocket Lab 方面', 2),
        'galactic energy': ('中国星河动力方面', 0), '星河动力': ('中国星河动力方面', 0),
    }
    if provider.casefold() in known:
        label, rank = known[provider.casefold()]
        return dict(label=label, rank=rank, basis='用户确认的口播机构口径')
    country = item.get('operator_country', '').strip()
    country = {'China': '中国', 'United States of America': '美国', 'United States': '美国',
               'New Zealand': '新西兰', 'Japan': '日本', 'India': '印度', 'Russia': '俄罗斯'}.get(country, country)
    return dict(label=(country + ' ' + provider + '方面').strip() if provider else '其他任务',
                rank=3, basis='LL2机构国家字段' if country else '机构归属不足，未推断国家')


def pad_code(name):
    match = re.search(r'(Space Launch Complex|Launch Complex|Launch Area)\s+(\d+[A-Z]?)\b', name, re.I)
    if not match:
        return ''
    prefix = {'space launch complex': 'SLC-', 'launch complex': 'LC-', 'launch area': ''}[match[1].lower()]
    return prefix + match[2].upper()


def payload_count(item):
    from services.launch_research import background_facts
    facts = background_facts(item)
    if not facts:
        return None, ''
    name = item.get('english_name', '')
    match = re.search(r'(?:^|\()(\d+)\s*[x×]\s+\S', name, re.I)
    if match:
        return int(match[1]), name
    # shortcut: only explicit LL2 English count forms; other wording stays with sourced model review.
    for fact in (f for f in facts if f['evidence_level'] == 'll2_record'):
        quote = fact['quote'].strip()
        match = re.match(r'(?:A batch of\s+)?(\d+)\s+[^.\r\n]{0,65}?\bsatellites?\b', quote, re.I)
        if match:
            return int(match[1]), quote
        if re.match(r'The [^.\r\n]{1,80} is (?:a|an) [^.\r\n]{0,80}\bsatellite\b', quote, re.I):
            return 1, quote
    return None, ''


def spoken_quantities(text):
    from services.launch_roundup import _numbers
    return {n for phrase in re.findall(r'(?:\d+|[零一二三四五六七八九十百千两]+)\s*(?:颗|枚|星|satellites?\b)', text, re.I)
            for n in _numbers(phrase)}


def event_context(item, draft):
    local = parse_datetime(item.get('net', ''))
    local = local.astimezone(_timezone(draft.get('timezone', 'Asia/Shanghai'))) if local else None
    period = ('凌晨' if local.hour < 6 else '早上' if local.hour < 9 else '上午' if local.hour < 12 else
              '中午' if local.hour < 13 else '下午' if local.hour < 18 else '傍晚' if local.hour < 20 else '晚上') if local else ''
    pad = item.get('pad_name', '')
    code = pad_code(pad)
    result = {3: '发射成功', 4: '发射失败', 7: '发射部分失败'}.get(item.get('status_id'), '')
    if not result:
        from services.launch_roundup import category
        result = {'planned': '计划发射', 'delayed': '延期', 'cancelled': '取消', 'actual': '已发射，结果待确认', 'pending': '状态待确认'}[category(item, parse_datetime(draft.get('as_of', '')))]
    count, basis = payload_count(item)
    return dict(task_id=item['ll2_id'], group=affiliation(item), payload_count=count, payload_count_basis=basis,
        display_time=local.isoformat() if local else '',
        date=local.date().isoformat() if local else '',
        date_reading=f'{local.month}月{local.day}日{period}' if local else '',
        timezone=timezone_label(draft), rocket=item.get('rocket', ''), name=item.get('english_name', ''),
        site_name=item.get('site_name', ''), pad_name=pad if code else '', pad_code=code,
        legacy_site=item.get('site', '') if not item.get('site_name') else '',
        site_country=item.get('site_country', ''), launch_result=result,
        timing_basis='reported_launch' if item.get('status_id') in (3, 4, 6, 7, 9) else 'scheduled_time',
        recovery=item.get('recovery', ''))


def groups(draft):
    included = {s['id'] for s in draft['selection'] if s['included']}
    ordered = sorted((i for i in draft['snapshot']['items'] if i['ll2_id'] in included),
                     key=lambda i: (affiliation(i)['rank'], affiliation(i)['label'], parse_datetime(i['net'])))
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
        if re.search(r'(?:根据|据|按(?:照)?)\s*LL2|LL2\s*(?:记录|显示|指出|称)|先看重点任务|再看另一[项个]?任务', text, re.I):
            raise AIResponseParseError('口播请删除例行来源归因或空泛过渡；出处保留在资料中')
        if context and not context['complete_scope'] and re.search(r'全球.*(?:共|全部|完成|发射).*?(?:\d|[一二三四五六七八九十])|全球(?:同期)?全部发射', text):
            raise AIResponseParseError('只收录或选择部分任务，不能声称全球完整统计')
        counts = set()
        for identifier in segment.get('task_ids', []):
            if not any(s.get('task_id') == identifier and s.get('evidence_level') == 'll2_event' for s in segment.get('source_refs', [])):
                raise AIResponseParseError('每次任务须引用本次event事实，不能只串联名称')
            event = next(e for e in context['events'] if e['task_id'] == identifier)
            if event['payload_count'] is not None:
                counts.add(str(event['payload_count']))
            screen = segment['screen']
            if event['rocket'] and event['rocket'] not in screen:
                raise AIResponseParseError('屏幕文字须保留完整火箭型号：' + event['rocket'])
            if event['pad_code'] and event['pad_code'] not in screen and event['pad_name'] not in screen:
                raise AIResponseParseError('屏幕文字须保留工位编号：' + event['pad_code'])
        if not counts <= spoken_quantities(text):
            raise AIResponseParseError('口播须明确给出已有证据的载荷数量（如N颗），不能只藏在任务名/编号中：' + ','.join(sorted(counts)))
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
