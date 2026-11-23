"""Actual saved problematic records, plus explicit boundary cases; no invented confirmations."""
from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import resources
from services.launch_display import resolve_launch,place
from services import launch_roundup as roundup
from services.roundup_style import payload_count

class LaunchReworkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);p=patch.object(resources,'_WORKSPACE_PATH',self.root);p.start();self.addCleanup(p.stop)
        self.rows=json.loads(Path('tests/fixtures/launch_records_20261108.json').read_text(encoding='utf8'))
    def test_real_records_do_not_use_descriptions_or_unproven_one(self):
        for row in self.rows:
            before=deepcopy(row);view=resolve_launch(row)
            self.assertEqual(row,before)
            self.assertEqual(view['payload'],'');self.assertIsNone(view['payload_count'])
        russian=next(i for i in self.rows if i['provider']=='Russian Space Forces')
        view=resolve_launch(russian)
        self.assertEqual(view['provider'],'俄罗斯航天兵');self.assertEqual(view['organization_type'],'军方')
        self.assertEqual(view['orbit'],'中地球轨道');self.assertEqual(view['pad_name'],'')
        self.assertIn('上面级',view['rocket'])
    def test_cached_legacy_name_does_not_confirm_facts_and_user_terms_win(self):
        path=self.root/'translator/translation_cache.json';path.parent.mkdir()
        path.write_text(json.dumps({'Historical mission':dict(translation='已有中文任务',source='llm',confidence=.6)}))
        row=dict(ll2_id='stable',english_name='Historical mission')
        self.assertEqual(resolve_launch(row)['name'],'已有中文任务')
        self.assertFalse(roundup.material_status(row)['independent_support'])
        self.assertEqual(resolve_launch(row,{'Historical mission':dict(translation='用户名称')})['name'],'用户名称')
    def test_explicit_empty_and_edits_reach_all_resolved_fields(self):
        row=deepcopy(self.rows[0]);row.update(edition_version=2,edition_fields=dict(provider='本期机构',payload='载荷甲',orbit='',title='本期主标题',payload_count=0,pad_name=''))
        view=resolve_launch(row)
        self.assertEqual([view[k] for k in ('provider','payload','orbit','title','payload_count','pad_name')],['本期机构','载荷甲','','本期主标题',0,''])
        row['edition_fields']['site_name']='';self.assertEqual(resolve_launch(row)['location'],'')
    def test_official_payload_flight_amount_and_missing_amount_are_distinct(self):
        from feeds.launches import launch_context
        flight=json.loads(Path('tests/fixtures/ll2_payload_flight_20261108.json').read_text(encoding='utf-8'))
        raw=dict(rocket=dict(payloads=[flight]))
        view=resolve_launch(launch_context(raw));self.assertEqual(view['payload_count'],1)
        self.assertEqual(view['payload'],'Peregrine Mission One lander')
        for amount in (None,True,-1,'1'):
            flight['amount']=amount;self.assertIsNone(resolve_launch(launch_context(raw))['payload_count'])
        flight['amount']=3;raw['rocket']['payloads'].append(dict(payload=dict(name='Second payload')))
        self.assertIsNone(resolve_launch(launch_context(raw))['payload_count'])
    def test_participating_agencies_do_not_become_customer_or_manufacturer(self):
        from feeds.launches import launch_context
        raw=dict(launch_service_provider=dict(name='Launch operator'),rocket=dict(configuration=dict(manufacturer=dict(name='Rocket manufacturer'))),mission=dict(agencies=[dict(name='Participating agency')]))
        view=resolve_launch(launch_context(raw))
        self.assertEqual(view['provider'],'Launch operator');self.assertEqual(view['manufacturer'],'Rocket manufacturer');self.assertEqual(view['customer'],'')
    def test_edition_result_and_time_drive_event_and_daily_statistics(self):
        from services.roundup_style import event_context,activity_context
        draft=roundup.create_draft(dict(items=self.rows,complete=True,start='2026-11-01',end='2026-11-07'),'2026-11-01','2026-11-07')
        row=draft['snapshot']['items'][0]
        row.update(edition_version=2,edition_fields=dict(status_id=None,status_name='待确认',net='2026-11-04T00:00:00+08:00'))
        event=event_context(row,draft);self.assertEqual(event['launch_result'],'状态待确认');self.assertEqual(event['date'],'2026-11-04')
        self.assertEqual(roundup.statistics(draft)['actual'],4)
        self.assertEqual(sum(d['count'] for d in activity_context(draft,row['ll2_id'])['days']),4)
    def test_place_relations_and_selected_statistics(self):
        self.assertEqual(place('范登堡太空军基地','范登堡太空军基地 SLC-4E')[2],'范登堡太空军基地 · SLC-4E')
        self.assertEqual(place('保留完整名称','Unknown Pad')[2],'保留完整名称')
        draft=roundup.create_draft(dict(items=self.rows,complete=True,start='2026-11-01',end='2026-11-07'),'2026-11-01','2026-11-07')
        stats=roundup.statistics(draft);self.assertEqual((stats['total'],stats['actual'],stats['results']['success']),(6,5,5))
    def test_saved_localized_defaults_do_not_mask_new_terms_or_clear_manual_fields(self):
        row=dict(ll2_id='stable',english_name='M',rocket='旧自动名称',source_original=dict(rocket='Original'),edition_fields=dict(rocket='旧自动名称'))
        entries={'Original':dict(translation='旧自动名称')}
        self.assertEqual(resolve_launch(row,entries)['rocket'],'旧自动名称')
        partial=dict(row,rocket='西昌 Satellite Launch Center',source_original={'rocket':'Xichang Satellite Launch Center'},edition_fields={'rocket':'西昌 Satellite Launch Center'})
        terms={'Xichang':dict(translation='西昌'),'Xichang Satellite Launch Center':dict(translation='西昌卫星发射中心')}
        self.assertEqual(resolve_launch(partial,terms)['rocket'],'西昌卫星发射中心')
        row.update(edition_version=2,edition_fields=dict(rocket=''))
        self.assertEqual(resolve_launch(row,entries)['rocket'],'')
    def test_configuration_and_place_formatting_are_idempotent(self):
        from services.launch_display import text
        first=text('Soyuz 2.1b Fregat-M','rocket')
        self.assertEqual(text(first,'rocket'),first);self.assertEqual(first.count('上面级'),1)
        view=resolve_launch(dict(english_name='M',rocket='Soyuz 2.1b Fregat-M',edition_version=2,edition_fields={'upper_stage':'新确认上面级'}))
        self.assertIn('新确认上面级',view['rocket'].replace(' ',''));self.assertNotIn('Fregat',view['rocket'])
        self.assertEqual(view['rocket'].count('上面级'),1)
        self.assertEqual(place('火箭实验室1号发射场','火箭实验室1A工位','Rocket Lab Launch Complex 1, Mahia','Rocket Lab Launch Complex 1A')[2],'火箭实验室1号发射场 · LC-1A')
        from services.roundup_style import affiliation
        self.assertEqual(affiliation(dict(provider='Rocket Lab',edition_version=2,edition_fields={'provider':'火箭实验室'}))['rank'],2)
    def test_nested_old_sources_keep_original_relationships(self):
        row=dict(ll2_id='stable',english_name='M',site_name='旧显示',pad_name='旧工位',edition_version=2,edition_fields={},source_original=dict(ll2_id='stable',site_name='旧显示',source_original=dict(ll2_id='stable',site_name='发射场',pad_name='Unknown Pad')))
        self.assertEqual(resolve_launch(row)['location'],'发射场')
