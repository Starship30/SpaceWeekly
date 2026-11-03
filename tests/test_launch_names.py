"""Name resolution is local, preserves identity, and never certifies background."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resources
from services import launch_roundup as roundup
from services.launch_research import display_names, fingerprint
from translator.dictionary import load_dictionary, save_dictionary, dictionary_replace
from services.roundup_audio import audio_key


class LaunchNamesTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir='build')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(resources, '_WORKSPACE_PATH', self.root)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.item = dict(ll2_id='identity', english_name='Unknown mission', rocket='Falcon 9',
                         site='Unknown site', net='2026-10-24T00:00:00Z', status_id=3)

    def test_defaults_legacy_and_user_priority_without_creating_files(self):
        (self.root/'config').mkdir()
        (self.root/'config/aerospace_terms.json').write_text(json.dumps({
            'legacy': '旧修改', 'Starship':'旧用户用词', 'falcon 9': dict(translation='已确认旧名', human_status='confirmed')}), encoding='utf-8')
        save_dictionary({'Falcon 9': dict(translation='未确认新名', type='rocket'), 'Starship':dict(translation='星舰')})
        entries = load_dictionary()
        self.assertEqual(dictionary_replace('Falcon 9', entries)[0], '已确认旧名')
        self.assertEqual(entries['legacy']['translation'], '旧修改')
        self.assertEqual(entries['Starship']['translation'], '旧用户用词')
        self.assertIn('Long March 4B', entries)
        self.assertFalse((self.root/'translator/pending_terms.json').exists())
        save_dictionary({'Falcon 9': dict(translation='用户确认名', human_status='confirmed')})
        self.assertEqual(dictionary_replace('Falcon 9', load_dictionary())[0], '用户确认名')
        self.assertTrue(list((self.root/'translator').glob('dictionary.json.backup-*')))

    def test_damaged_dictionary_is_not_overwritten(self):
        path=self.root/'translator/dictionary.json';path.parent.mkdir();path.write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError):load_dictionary()
        self.assertEqual(path.read_text(encoding='utf-8'),'{bad')

    def test_vandenberg_resource_name_is_sourced_and_user_override_wins(self):
        self.item['site'] = 'Vandenberg SFB, CA, USA'
        self.assertEqual(display_names(self.item)['site'], '范登堡太空军基地')
        self.assertIn('news.cctv.com', load_dictionary()[self.item['site']]['source_url'])
        self.assertFalse(roundup.material_status(self.item)['independent_support'])
        save_dictionary({self.item['site']: dict(translation='用户基地名称', human_status='confirmed')})
        self.assertEqual(display_names(self.item)['site'], '用户基地名称')
        self.assertEqual(dictionary_replace('Starlink Group 15-15', load_dictionary())[0], 'Starlink Group 15-15')

    def test_names_do_not_promote_unverified_cache_or_background(self):
        original=deepcopy(self.item)
        save_dictionary({'Unknown mission':dict(translation='用户名称',human_status='confirmed')})
        cache=self.root/'translator/translation_cache.json'
        cache.write_text(json.dumps({'Unknown site':dict(translation='无出处猜测',status='candidate')}),encoding='utf-8')
        names=display_names(self.item)
        self.assertEqual(names['name'],'用户名称');self.assertEqual(names['site'],'Unknown site')
        self.assertEqual(self.item,original)
        self.assertFalse(roundup.material_status(self.item)['independent_support'])
        self.item['research']=dict(fingerprint=fingerprint(self.item),human_status='confirmed',
            human_fingerprint=fingerprint(self.item),manual=dict(chinese_name='任务人工名称'))
        self.assertEqual(display_names(self.item)['name'],'任务人工名称')
        self.item['research']['input']=deepcopy(original)
        self.item['description']='Background updated without changing task identity'
        self.assertEqual(display_names(self.item)['name'],'任务人工名称')
        self.assertEqual(roundup.confirmed_values(self.item['research'],self.item),{})

    def test_replace_once_and_keep_model_numbers(self):
        entries={'Falcon 9':dict(translation='Rocket'), 'Rocket':dict(translation='再次替换')}
        self.assertEqual(dictionary_replace('Falcon 9 Block 5',entries)[0],'Rocket Block 5')
        self.assertEqual(dictionary_replace('Falcon 90',entries)[0],'Falcon 90')

    def test_generated_and_edited_scripts_refresh_differently_and_audio_expires(self):
        draft=roundup.create_draft(dict(items=[self.item],complete=True,start='2026-10-24',end='2026-10-24'),
                                  '2026-10-24','2026-10-24')
        segment=dict(id='paragraph',kind='highlight',spoken='Falcon 9',screen='Falcon 9',task_ids=['identity'],source_refs=[])
        draft['segments']=[segment];roundup.sync_names(draft)
        old=audio_key(draft,segment)
        save_dictionary({'Falcon 9':dict(translation='猎鹰九号',human_status='confirmed')})
        roundup.sync_names(draft)
        self.assertEqual(segment['spoken'],'猎鹰九号');self.assertNotEqual(audio_key(draft,segment),old)
        segment['edited']=True
        save_dictionary({'Falcon 9':dict(translation='猎鹰9号火箭',human_status='confirmed')})
        self.assertEqual(roundup.sync_names(draft),['paragraph']);self.assertEqual(segment['spoken'],'猎鹰九号')
        roundup.sync_names(draft,update_manual=True)
        self.assertEqual(segment['spoken'],'猎鹰9号火箭')

    def test_recorded_generated_script_waits_for_explicit_name_adoption(self):
        draft=roundup.create_draft(dict(items=[self.item],complete=True,start='2026-10-24',end='2026-10-24'),
                                  '2026-10-24','2026-10-24')
        segment=dict(id='recorded',kind='highlight',spoken='Falcon 9',screen='Falcon 9',
            task_ids=['identity'],source_refs=[],audio=dict(path='existing-recording.wav'))
        draft['segments']=[segment];before=audio_key(draft,segment)
        self.assertEqual(roundup.sync_names(draft),['recorded'])
        self.assertEqual(segment['spoken'],'Falcon 9');self.assertEqual(audio_key(draft,segment),before)
        roundup.sync_names(draft,update_manual=True)
        self.assertEqual(segment['spoken'],'猎鹰9号');self.assertNotEqual(audio_key(draft,segment),before)


if __name__=='__main__':unittest.main()
