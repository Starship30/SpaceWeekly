"""Contextual reading forms; formal names and screen text never pass through here."""
from datetime import date
import re

VERSION = 2
DIGITS = '零一二三四五六七八九'


def chinese_number(value):
    number = int(value)
    if number < 10:
        return DIGITS[number]
    if number >= 10000:
        high, low = divmod(number, 10000)
        return chinese_number(high) + '万' + (('零' if low < 1000 else '') + chinese_number(low) if low else '')
    result, zero = '', False
    for divisor, unit in ((1000, '千'), (100, '百'), (10, '十'), (1, '')):
        digit, number = divmod(number, divisor)
        if digit:
            result += ('零' if zero else '') + DIGITS[digit] + unit
            zero = False
        elif result and number:
            zero = True
    return result[1:] if result.startswith('一十') else result


def number_id(value):
    return ''.join(DIGITS[int(c)] for c in value) if value.startswith('0') else chinese_number(value)


def decimal_reading(value):
    sign='负' if value.startswith('-') else ''
    integer,_,fraction=value.lstrip('-').partition('.')
    return sign+chinese_number(integer)+('点'+''.join(DIGITS[int(c)] for c in fraction) if fraction else '')


def normalize_reading(text):
    def calendar(match):
        year, month, day = match.group('year', 'month', 'day')
        try:
            date(int(year) if year else 2000, int(month), int(day))
        except ValueError:
            return match[0]
        return (''.join(DIGITS[int(c)] for c in year) + '年' if year else '') + chinese_number(month) + '月' + chinese_number(day) + '日'

    text = re.sub(r'(?<![A-Za-z0-9_.])(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日', calendar, text)
    text = re.sub(r'(?<![A-Za-z0-9_.])(?:19|20)\d{2}(?=年)',lambda m:''.join(DIGITS[int(c)] for c in m[0]),text)
    def clock(match):
        hour,minute,second=match.group('hour','minute','second')
        if int(hour)>23 or int(minute)>59 or second and int(second)>59:return match[0]
        return chinese_number(hour)+'点'+(number_id(minute)+'分' if int(minute) or second else '整')+(
            number_id(second)+'秒' if second else '')
    text=re.sub(r'(?<![A-Za-z0-9_.])(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?![\d.])',clock,text)
    # Only numbered satellites and pads: versions, decimals and vehicle models stay intact.
    text = re.sub(r'(?<![A-Za-z0-9_.])(?P<a>\d{1,3})\s*[—–－至-]\s*(?P<b>\d{1,3})(?=星)',
                  lambda m: number_id(m['a']) + '至' + number_id(m['b']), text)
    text = re.sub(r'(?<![A-Za-z0-9_.])\d{1,3}(?=星)', lambda m: number_id(m[0]), text)
    text = re.sub(r'(?<![A-Za-z0-9_.\-])(?P<n>\d{1,3})(?P<letter>[A-Z])?\s*(?:号)?工位',
                  lambda m: chinese_number(m['n']) + (' ' + m['letter'] + ' ' if m['letter'] else '号') + '工位', text)
    text=re.sub(r'(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)\s*[%％]',lambda m:'百分之'+decimal_reading(m[1]),text)
    units=r'(?:公里|千米|厘米|毫米|微米|纳米|千克|公斤|毫秒|分钟|小时|摄氏度|颗|枚|个|次|吨|克|米|秒|万|亿|年)'
    text=re.sub(r'(?<![A-Za-z0-9_.\-])(?P<a>\d+(?:\.\d+)?)\s*[—–至]\s*(?P<b>\d+(?:\.\d+)?)(?='+units+r')',
        lambda m:decimal_reading(m['a'])+'至'+decimal_reading(m['b']),text)
    text=re.sub(r'(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)(?='+units+r')',lambda m:decimal_reading(m[1]),text)
    return text


def final_reading(text, rules):
    if not isinstance(rules, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in rules.items()):
        raise ValueError('发音规则应为非空原文到读法的 JSON 对象')
    # User readings win, in one pass; normalize only the untouched pieces.
    if rules:
        pattern = '(' + '|'.join(re.escape(k) for k in sorted(rules, key=len, reverse=True)) + ')'
        result = ''.join(rules[part] if n % 2 else normalize_reading(part)
                         for n, part in enumerate(re.split(pattern, text)))
    else:
        result = normalize_reading(text)
    if re.search(r'https?://|www\.|\[\[|\]\]|```|<\s*/?\s*(?:speak|phoneme|prosody)\b|'
                 r'(?:^|\n)\s*(?:来源|出处|分类|编辑说明|新闻ID|任务ID|段落ID|同步标记)\s*[:：]', result, re.I):
        raise ValueError('朗读文本不能包含来源地址、同步标记或语音标签')
    return result
