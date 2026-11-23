import json
import re
import shutil
from copy import deepcopy
from functools import lru_cache
from uuid import uuid4
from pathlib import Path

from resources import bundled_resource_path
from resources import resource_path

_CASC_NAMES = "https://m.spacechina.com/n142/n152/n657792/c3556658/content.html"
_ROCKET_LAB_SITE = "https://rocketlabcorp.com/updates/rocket-lab-launch-complex-1-ready-for-launches/"
_CAS_SPACE_NAMES = "https://www.cas-space.com/article/610.html"

DEFAULT_DICTIONARY = {
    "Starlink Group": {"translation": "Starlink Group", "type": "mission"},
    "SpaceX": {"translation": "SpaceX", "type": "organization"},
    "Falcon 9": {"translation": "猎鹰9号", "type": "rocket"},
    "Starship": {"translation": "星舰", "type": "rocket"},
    "Soyuz": {"translation": "联盟号", "type": "rocket"},
    "Gravity-1": {"translation": "引力-1", "type": "rocket", "aliases": ["引力一号"],
                  "source_url": "https://orienspace.com/productPage"},
    "Orienspace": {"translation": "东方空间", "type": "organization",
                   "source_url": "https://www.orienspace.com/aboutPage"},
    "Jiuquan": {"translation": "酒泉", "type": "location"},
    "Taiyuan": {"translation": "太原", "type": "location"},
    "Xichang": {"translation": "西昌", "type": "location"},
    "Wenchang": {"translation": "文昌", "type": "location"},
    "Haiyang": {"translation": "海阳", "type": "location"},
    "Low Earth Orbit": {"translation": "近地轨道", "type": "orbit"},
    "Sun-Synchronous Orbit": {"translation": "太阳同步轨道", "type": "orbit"},
    "Geostationary Transfer Orbit": {
        "translation": "地球同步转移轨道",
        "type": "orbit"
    },
    "Launch Successful": {"translation": "发射成功", "type": "status"},
    "Long March 4B": {"translation": "长征四号乙", "type": "rocket", "source_url": _CASC_NAMES},
    "Long March 2C": {"translation":"长征二号丙","type":"rocket","source_url":"https://www.cnsa.gov.cn/n6758824/n6759008/n6759011/c6794035/content.html"},
    "Long March 2D": {"translation":"长征二号丁","type":"rocket","source_url":"https://microsate.cas.cn/xwzx/yw/202611/t20261112_8308339.html"},
    "Long March 6A": {"translation": "长征六号改", "type": "rocket", "source_url": _CASC_NAMES},
    "Long March 8A": {"translation": "长征八号甲", "type": "rocket", "source_url": _CASC_NAMES},
    "Yaogan 50-03": {"translation": "遥感五十号03星", "type": "mission", "source_url": _CASC_NAMES},
    "Yaogan 53-07 to 09/56-19 to 21": {"translation": "遥感五十三号07—09星、遥感五十六号19—21星",
                                      "type": "mission", "source_url": _CASC_NAMES},
    "SatNet LEO Group 28": {"translation": "卫星互联网低轨28组卫星", "type": "mission", "source_url": _CASC_NAMES},
    "Jiuquan Satellite Launch Center, People's Republic of China": {
        "translation": "酒泉卫星发射中心", "type": "location", "source_url": _CASC_NAMES},
    "Taiyuan Satellite Launch Center, People's Republic of China": {
        "translation": "太原卫星发射中心", "type": "location", "source_url": _CASC_NAMES},
    "Xichang Satellite Launch Center, People's Republic of China": {
        "translation":"西昌卫星发射中心","type":"location","source_url":"https://calt.spacechina.com/n482/n739/index.html"},
    "Plesetsk Cosmodrome, Russian Federation": {
        "translation":"普列谢茨克航天发射场","type":"location","source_url":"https://www.cmse.gov.cn/art/2008/9/17/art_311_3595.html"},
    "Russian Space Forces": {
        "translation":"俄罗斯航天兵","type":"organization","source_url":"https://www.cnsa.gov.cn/n6758823/n6759010/c6777054/content.html"},
    "Wenchang Space Launch Site, People's Republic of China": {
        "translation": "文昌航天发射场", "type": "location", "source_url": _CASC_NAMES},
    "Vandenberg SFB, CA, USA": {"translation": "范登堡太空军基地", "type": "location",
                               "source_url": "https://news.cctv.com/2025/02/20/ARTIoFDPdyWmuuvE7ZfRDRg5250220.shtml"},
    "Space Launch Complex 4E": {"translation": "SLC-4E 工位", "type": "location",
                                "source_url": "https://www.spacex.com/launches/sl-15-18"},
    "Rocket Lab Launch Complex 1, Mahia Peninsula, New Zealand": {
        "translation": "Rocket Lab 1号发射场（新西兰马希亚半岛）", "type": "location", "source_url": _ROCKET_LAB_SITE},
    "Rocket Lab Launch Complex 1A": {"translation": "1A 工位", "type": "location",
                                   "source_url": "https://rocketlabcorp.com/assets/Uploads/F64-iQPS-Presskit-The-Sea-God-Sees.pdf"},
    "Electron": {"translation": "电子号", "type": "rocket", "exact_only": True,
                 "source_url": "https://news.cctv.com/2020/07/06/ARTIEb3uOFqrfc7Y1S6bmxdG200706.shtml"},
    "Kinetica 2": {"translation": "力箭二号", "type": "rocket", "source_url": _CAS_SPACE_NAMES},
    "Kinetica-2": {"translation": "力箭二号", "type": "rocket", "source_url": _CAS_SPACE_NAMES},
    "CAS Space": {"translation": "中科宇航", "type": "organization", "source_url": _CAS_SPACE_NAMES},
    "GSLV Mk. II": {"translation": "地球同步卫星运载火箭 Mk II", "type": "rocket",
                    "source_url": "https://www.isro.gov.in/GSLV_Launchers.html"},
    "Satish Dhawan Space Centre, India": {"translation": "印度萨蒂什达万航天中心", "type": "location",
                                         "source_url": "https://news.cctv.com/2019/09/08/ARTIPxOq8ZpkbA6zkGiSPkXc190908.shtml"},
    "Satish Dhawan Space Centre Second Launch Pad": {"translation": "2号工位", "type": "location",
                                                    "source_url": "https://www.isro.gov.in/Mission_GSLVF18.html"},
    "IRNSS-1L (NVS-03)": {"translation": "印度导航卫星 NVS-03", "type": "mission",
                          "source_url": "https://www.isro.gov.in/Mission_GSLVF18.html"},
    "Indian Space Research Organization": {"translation": "印度空间研究组织", "type": "organization",
                                           "source_url": "https://www.isro.gov.in/LaunchMissions.html"},
}


def load_dictionary() -> dict[str, dict[str, str]]:
    sources=(bundled_resource_path("translator","dictionary.json"),
             resource_path("config","aerospace_terms.json"),_dictionary_path())
    fingerprints=[]
    for path in sources:
        try:
            stat=path.stat()
            fingerprints.append((path,stat.st_mtime_ns,stat.st_size))
        except FileNotFoundError:
            fingerprints.append((path,None,None))
    return deepcopy(_load_dictionary(tuple(fingerprints)))


@lru_cache(maxsize=8)
def _load_dictionary(fingerprints):
    # Resource defaults are a baseline; workspace edits and confirmed legacy terms win.
    entries = {key: dict(value) for key, value in DEFAULT_DICTIONARY.items()}
    (bundled,_,_),*sources=fingerprints
    entries.update(_read_entries(bundled, {}))
    defaults = {key.casefold(): value['translation'] for key, value in entries.items()}
    for source,modified,_ in sources:
        if modified is None:
            continue
        # Never turn a damaged user dictionary into a new empty one.
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("术语词典格式无效，请从备份恢复")
        for key, entry in _normalize_entries(raw).items():
            previous = next((k for k in entries if k.casefold() == key.casefold()), key)
            if (entries.get(previous, {}).get('human_status') == 'confirmed'
                    and entry.get('human_status') != 'confirmed'):
                continue
            if (entry.get('human_status') != 'confirmed' and previous in entries
                    and entry['translation'] == defaults.get(key.casefold())
                    and entries[previous]['translation'] != entry['translation']):
                continue
            if previous != key:
                entries.pop(previous)
            entries[key] = entry
    return entries


def save_dictionary(entries: dict[str, object]) -> None:
    _write_entries(_dictionary_path(), _normalize_entries(entries))


def load_pending_terms() -> dict[str, dict[str, str]]:
    path = _pending_path()

    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('待确认术语格式无效，请从备份恢复')
        return _normalize_entries(data, allow_empty=True)

    return {}


def save_pending_terms(entries: dict[str, object]) -> None:
    _write_entries(
        _pending_path(),
        _normalize_entries(entries, allow_empty=True),
    )


def add_pending_term(
    term: str,
    term_type: str,
    translation: str = "",
) -> bool:
    source = str(term or "").strip()

    if not source:
        return False

    pending = load_pending_terms()
    is_new = source not in pending
    current = pending.get(source, {})
    pending[source] = {
        "translation": str(translation or current.get("translation", "")).strip(),
        "type": str(term_type or current.get("type", "term")).strip() or "term",
    }
    save_pending_terms(pending)
    return is_new


def confirm_pending_term(
    term: str,
    translation: str | None = None,
    term_type: str | None = None,
) -> bool:
    pending = load_pending_terms()
    entry = pending.get(term)

    if entry is None:
        return False

    confirmed_translation = str(
        translation if translation is not None else entry.get("translation", "")
    ).strip()

    if not confirmed_translation:
        return False

    dictionary = load_dictionary()
    dictionary[term] = {
        "translation": confirmed_translation,
        "type": str(term_type or entry.get("type", "term")).strip() or "term",
        "human_status": "confirmed",
    }
    save_dictionary(dictionary)
    pending.pop(term, None)
    save_pending_terms(pending)
    return True


def remove_pending_term(term: str) -> None:
    pending = load_pending_terms()

    if term in pending:
        pending.pop(term)
        save_pending_terms(pending)


def dictionary_replace(
    text: str,
    entries: dict[str, dict[str, str]] | None = None,
) -> tuple[str, bool]:
    original = str(text or "").strip()

    if not original:
        return "", False

    dictionary = entries if entries is not None else load_dictionary()

    for source, entry in dictionary.items():
        if source.casefold() == original.casefold():
            return entry["translation"], True

    phrases = {k: v for k, v in dictionary.items() if not v.get('exact_only')}
    if not phrases:
        return original, False
    folded = {k.casefold(): v['translation'] for k, v in phrases.items()}
    pattern = '|'.join(re.escape(k) for k in sorted(phrases, key=len, reverse=True))
    # One pass, at Latin word boundaries, preserves model numbers and replacement text.
    translated, count = re.subn(r'(?<![A-Za-z0-9])(?:' + pattern + r')(?![A-Za-z0-9])',
                               lambda m: folded.get(m.group().casefold(), m.group()), original, flags=re.I)
    return translated, bool(count)


def exact_dictionary_translation(
    term: str,
    entries: dict[str, dict[str, str]] | None = None,
) -> str:
    """Return only a complete glossary match, never a substring replacement."""

    original = str(term or "").strip()

    if not original:
        return ""

    dictionary = entries if entries is not None else load_dictionary()

    for source, entry in dictionary.items():
        if source.casefold() == original.casefold():
            return str(entry.get("translation", "")).strip()

    return ""


def pending_translation(term: str) -> str:
    entry = load_pending_terms().get(term, {})

    return str(entry.get("translation", "")).strip()


def _dictionary_path() -> Path:
    return resource_path("translator", "dictionary.json")


def _pending_path() -> Path:
    return resource_path("translator", "pending_terms.json")


def _read_entries(
    path: Path,
    fallback: dict[str, dict[str, str]],
    allow_empty: bool = False,
) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {key: dict(value) for key, value in fallback.items()}

    if not isinstance(data, dict):
        return {key: dict(value) for key, value in fallback.items()}

    return _normalize_entries(data, allow_empty=allow_empty)


def _normalize_entries(
    entries: dict[str, object],
    allow_empty: bool = False,
) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}

    for source, raw_entry in entries.items():
        term = str(source).strip()

        if isinstance(raw_entry, dict):
            translation = str(raw_entry.get("translation", "")).strip()
            term_type = str(raw_entry.get("type", "term")).strip() or "term"
        else:
            translation = str(raw_entry).strip()
            term_type = "term"

        if term and (translation or allow_empty):
            normalized[term] = dict(raw_entry) if isinstance(raw_entry, dict) else {}
            normalized[term].update(translation=translation, type=term_type)

    return normalized


def _write_entries(path: Path, entries: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.name == 'dictionary.json':
        shutil.copy2(path, path.with_name(path.name + '.backup-' + uuid4().hex[:8]))
    temporary = path.with_name('.terms-' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
