"""Desktop handoff of existing report stages; JSON persistence and offline exports."""
from dataclasses import asdict, replace
from copy import deepcopy

from models.article import Article
from models.news import News
from models.ai_summary import AISummary
from models.news_assessment import NewsAssessment
from models.export_context import ExportContext


def validate_session(value):
    """Check the entire recovery boundary before changing live controllers."""
    from services.launch_roundup import KINDS
    from tools.video_cards import validate_snapshot
    if not isinstance(value,dict) or value.get('version')!=1:
        raise ValueError('Invalid production session version')
    draft=value.get('roundup',{})
    if not isinstance(draft,dict) or any(k not in draft for k in ('start','end','snapshot','selection','segments','tts','pronunciations')):
        raise ValueError('Incomplete launch draft')
    from datetime import date
    date.fromisoformat(draft['start']);date.fromisoformat(draft['end'])
    if not isinstance(draft['tts'],dict) or any(not isinstance(draft['tts'].get(k),str) for k in ('python','model','speaker','style','device')):
        raise ValueError('Invalid voice configuration')
    if not isinstance(draft['tts'].get('timeout',600),(int,float)) or not 1<=draft['tts'].get('timeout',600)<=7200:
        raise ValueError('Invalid voice timeout')
    from services.roundup_sync import validate_aligner
    validate_aligner(draft.get('aligner',{}))
    from tools.roundup_visuals import visual_settings
    visual_settings(draft)
    if not isinstance(draft['pronunciations'],dict) or draft.get('target_seconds') not in (60,90,120) or not 0<=float(draft.get('pause_seconds',.3))<=3:
        raise ValueError('Invalid production timing/voice rules')
    if not isinstance(draft['snapshot'].get('items'),list) or not isinstance(draft['selection'],list) or not isinstance(draft['segments'],list):
        raise ValueError('Invalid launch snapshot/selection')
    identifiers={i['ll2_id'] for i in draft['snapshot']['items']}
    if any(not isinstance(i.get(k),str) for i in draft['snapshot']['items'] for k in ('ll2_id','english_name','net')):
        raise ValueError('Invalid launch identity')
    if any(s['id'] not in identifiers or not isinstance(s['included'],bool) for s in draft['selection']):
        raise ValueError('Launch selection is outside snapshot')
    for segment in draft['segments']:
        if (segment.get('kind') not in KINDS or not isinstance(segment.get('id'),str)
            or not isinstance(segment.get('spoken'),str) or not isinstance(segment.get('screen'),str)
            or not isinstance(segment.get('task_ids'),list)):
            raise ValueError('Invalid paragraph')
        from services.roundup_sync import validate_unit_structure
        validate_unit_structure(segment)
    restored=restore_news(value.get('news',{}).get('bundle'))
    selected=value.get('news',{}).get('script_included')
    if selected is not None:
        urls={a.news.url for a in restored['articles']} if restored else set()
        if not isinstance(selected,list) or any(not isinstance(url,str) for url in selected) or not set(selected)<=urls:
            raise ValueError('新闻稿件选择无效')
    from services.news_voice import validate as validate_voice
    voices=value.get('news',{}).get('voices',{})
    if not isinstance(voices,dict):raise ValueError('新闻配音草稿列表无效')
    for identifier,draft_voice in voices.items():
        validate_voice(draft_voice)
        if identifier!=draft_voice['news_id']:raise ValueError('新闻配音身份无效')
    if restored and any(not isinstance(getattr(a.news,key),str) for a in restored['articles'] for key in ('title','summary','source','published','url')):
        raise ValueError('Invalid news fields')
    cards=value.get('cards',{}).get('data')
    if cards:validate_snapshot(deepcopy(cards))
    if value.get('cards',{}).get('sort_order','asc') not in ('input','asc','desc'):raise ValueError('素材排序无效')
    if not isinstance(value.get('daily',{}),dict) or not isinstance(value.get('view',{}),dict):
        raise ValueError('Invalid production controls')
    from services.settings import default_settings
    defaults=vars(default_settings())
    if any(k not in defaults or type(v) is not type(defaults[k]) for k,v in value.get('daily',{}).items()):
        raise ValueError('Invalid edition settings')
    for key,item in value.get('view',{}).items():
        if not isinstance(item,str if key=='mode' else int):raise ValueError('Invalid saved view')
    staged=value.get('roundup_controls',{})
    if not isinstance(staged,dict) or any(not isinstance(v,(int,float) if k in ('timeout','pause') else str) for k,v in staged.items()):
        raise ValueError('Invalid pending voice controls')
    if not isinstance(value.get('intro',{}),dict) or any(v is not None and not isinstance(v,str) for v in value.get('intro',{}).values()):
        raise ValueError('Invalid intro settings')
    if not isinstance(value.get('results',[]),list) or any(not isinstance(v,str) for v in value.get('results',[])):
        raise ValueError('Invalid generated files')
    if not isinstance(value.get('material_edits',{}),dict) or any(not isinstance(v,dict) or any(not isinstance(s,str) for s in v.values()) for v in value.get('material_edits',{}).values()):
        raise ValueError('Invalid pending research edits')
    return value


def serialize_news(bundle):
    if not bundle:
        return None
    return dict(articles=[asdict(a) for a in bundle['articles']],
                source_articles=[asdict(a) for a in bundle.get('source_articles',bundle['articles'])],source_scope=bundle.get('source_scope'),
                launch_articles=[asdict(a) for a in bundle.get('launch_articles', [])],
                launch_video_items=bundle.get('launch_video_items', []),
                summaries={k: asdict(v) for k, v in bundle.get('summaries', {}).items()},
                assessments={k: asdict(v) for k, v in bundle.get('assessments', {}).items()},
                context=asdict(bundle['context']) if bundle.get('context') else None)


def restore_news(value):
    if value is None:
        return None
    def articles(rows):
        return [Article(News(**r['news']), r['body'], r['parser'], r.get('launch_data')) for r in rows]
    sources=articles(value.get('source_articles',value['articles']))
    by_url={a.news.url:a for a in sources}
    return dict(articles=[by_url.get(a.news.url,a) for a in articles(value['articles'])], launch_articles=articles(value.get('launch_articles', [])),
                source_articles=sources,source_scope=value.get('source_scope'),
                launch_video_items=value.get('launch_video_items', []),
                summaries={k: AISummary(**v) for k, v in value.get('summaries', {}).items()},
                assessments={k: NewsAssessment(**v) for k, v in value.get('assessments', {}).items()},
                context=ExportContext(**value['context']) if value.get('context') else None)


def prepare_news(feeds, token, *, prepared=None, collect_only=False):
    from services.weekly_report import generate_weekly
    result = []
    generate_weekly(feeds=feeds, export_sqlite=False, export_markdown_enabled=False,
                   export_word_enabled=False, cancel_token=token, prepared=prepared,
                   collect_only=collect_only, defer_exports=True, result_callback=result.append)
    token.check()
    if not collect_only:
        from services.news_images import collect
        collect(result[0]['articles'],token)
    return result[0]


def export_news(bundle, settings, token):
    """Already prepared articles and launch evidence only; no translation/network here."""
    from exporters.markdown import export_markdown
    from exporters.word import export_word
    from exporters.premiere import export_premiere
    from services.settings import apply_settings
    from services.weekly_report import _export_context
    from services.weekly_report import prepared_scope_matches
    token.check()
    if not prepared_scope_matches(bundle.get('source_scope'), settings):
        raise ValueError('资料日期范围已变化；请恢复稿件范围或重新采集后生成，导出不会隐式联网')
    if not any((settings.export_markdown,settings.export_word,settings.export_premiere,settings.export_sqlite)):
        raise ValueError('Choose at least one output format')
    apply_settings(settings)
    context = replace(bundle['context'], report_title=settings.report_title) if bundle.get('context') else _export_context(settings, {})
    paths = []
    common = dict(cancel_check=token.check, launches=bundle.get('launch_articles', []),
                  launch_range=settings.launch_range, launch_timezone=settings.display_timezone)
    for enabled, export in ((settings.export_markdown, export_markdown), (settings.export_word, export_word)):
        if enabled:
            paths.append(str(export(bundle['articles'], bundle.get('summaries', {}), context,
                                    bundle.get('assessments', {}), **common)))
    if settings.export_premiere:
        paths.append(str(export_premiere(bundle['articles'], bundle.get('summaries', {}), context,
                                        settings.language, token.check, launches=bundle.get('launch_video_items', []))))
    if settings.export_sqlite:
        from database.sqlite import initialize_database, save_article
        initialize_database()
        for article in bundle['articles']:
            token.check()
            save_article(article)
        paths.append(settings.sqlite_path)
    return paths
