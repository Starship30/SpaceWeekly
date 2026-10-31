"""Mock text/TTS providers and fixed LL2 records; not a voice-quality evaluation."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import resources
from ai.json_utils import AIResponseParseError
from ai.speech import SpeechRequest
from ai.qwen_tts import capabilities, QwenTTSProvider
from services import launch_roundup as r
from services.launch_data import refresh_collection, save_collection, load_existing
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from services.roundup_audio import generate_audio, current_audio, speech_text, wav_info
from services.roundup_export import export_package


def context(n=1, status=3, net=None):
    return dict(ll2_id=f'launch-{n}', english_name=f'Fixture mission {n}', net=net or f'2026-10-{n+20:02d}T12:00:00Z',
                rocket='Electron', site='Mahia', provider='Rocket Lab', site_country='New Zealand',
                operator_country='United States', description='Unverified background', payload='', orbit='',
                status_id=status, status_name='', recovery='Stage: Failed', info_urls=[], video_urls=[])


def generated():
    return dict(segments=[dict(id='intro', kind='overview', spoken='[[overview]]', screen='[[overview]]', task_ids=[]),
        dict(id='focus1', kind='highlight', spoken='先看重点任务，[[name:launch-1]]。', screen='[[name:launch-1]]', task_ids=['launch-1']),
        dict(id='focus2', kind='highlight', spoken='再看另一项任务，[[name:launch-2]]。', screen='[[name:launch-2]]', task_ids=['launch-2']),
        dict(id='rest', kind='remaining', spoken='其余任务中，[[name:launch-3]]。', screen='[[name:launch-3]]', task_ids=['launch-3']),
        dict(id='outro', kind='transition', spoken='接下来转入其他航天新闻。', screen='接下来转入其他航天新闻。', task_ids=[])])


def styled_generated():
    data = generated()
    for index, segment in enumerate(data['segments'][1:4], 1):
        segment.update(spoken=('[[group:0]]' if index == 1 else '') + f'[[event:launch-{index}]]',
                       screen=f'[[event:launch-{index}]]')
    data['segments'][-1].update(spoken='[[transition]]', screen='[[transition]]')
    return data


def write_pcm(path, rate=24000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x01\x00' * (rate//2))


class RoundupTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir='build')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        old = resources._WORKSPACE_PATH
        resources._WORKSPACE_PATH = self.root
        self.addCleanup(setattr, resources, '_WORKSPACE_PATH', old)
        self.settings = default_settings()
        self.token = CancellationToken()
        self.collection = dict(items=[context(1), context(2), context(3)], complete=True,
                               start='2026-10-20', end='2026-10-29', pages=2, retrieved_at='2026-10-30T00:00:00Z')
        self.draft = r.create_draft(self.collection, '2026-10-20', '2026-10-29')
        self.draft['tts']['model'] = 'mock-local-model-not-real-weights'
        self.draft['segments'] = r.validate_generated(generated(), self.draft)
        self.provider = Mock()
        self.provider.synthesize.side_effect = lambda req, path, cancel_check: write_pcm(path)

    def test_default_complete_natural_days_and_beijing_boundaries(self):
        self.assertEqual(r.default_range(datetime(2026, 10, 30, 1, tzinfo=timezone.utc)), ('2026-10-23', '2026-10-29'))
        collection = dict(self.collection, items=[context(1, net='2026-10-19T16:00:00Z'),
            context(2, net='2026-10-29T15:59:59Z'), context(3, net='2026-10-29T16:00:00Z')])
        draft = r.create_draft(collection, '2026-10-20', '2026-10-29')
        self.assertEqual([s['id'] for s in draft['selection']], ['launch-1', 'launch-2'])

    def test_unknown_results_and_landing_failure_do_not_change_launch_results(self):
        self.draft['snapshot']['items'][1]['status_id'] = 987
        stats = r.statistics(self.draft)
        self.assertEqual((stats['actual'], stats['pending']), (2, 1))
        self.assertEqual(stats['results']['success'], 2)
        self.assertEqual(stats['recoveries']['failure'], 3)
        self.assertEqual(stats['site_countries'], {'New Zealand': 2})
        self.assertEqual(stats['agencies'], {'Rocket Lab': 2})

    def test_planned_delayed_cancelled_and_pending_are_distinct(self):
        draft = deepcopy(self.draft)
        draft['as_of'] = '2026-01-01T00:00:00Z'
        draft['snapshot']['items'] = [context(1, 1), context(2, 5), dict(context(3, 10), status_name='Launch Cancelled')]
        stats = r.statistics(draft)
        self.assertEqual((stats['planned'], stats['delayed'], stats['cancelled'], stats['actual']), (1, 1, 1, 0))

    def test_incomplete_or_subset_statistics_are_scoped(self):
        self.draft['snapshot']['complete'] = False
        self.assertIn('当前收录', r.overview(self.draft))
        self.draft['segments'][0]['spoken'] = '全球共完成99次发射。'
        with self.assertRaises(ValueError):
            r.validate_draft(self.draft)
        self.draft['selection'][0]['included'] = False
        self.assertFalse(r.statistics(self.draft)['complete'])

    def test_changed_statistics_cannot_silently_keep_old_overview_audio(self):
        self.draft['selection'][2]['included'] = False
        self.draft['segments'] = [s for s in self.draft['segments'] if s['id'] != 'rest']
        with self.assertRaisesRegex(ValueError, '统计已变化'):
            r.validate_draft(self.draft)

    def test_ai_wrong_id_or_numeric_claim_is_rejected(self):
        for field, value in [('task_ids', ['nonexistent']), ('spoken', '全球发射99次。'),
                              ('spoken', '[[name:missing]]'), ('spoken', '创造世界纪录。')]:
            raw = generated()
            raw['segments'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(AIResponseParseError):
                r.validate_generated(raw, self.draft)

    def test_text_provider_reuses_configuration_and_validator(self):
        self.settings.deepseek_api_key = 'mock-credential'
        def response(prompt, config, **kw):
            self.assertEqual(config.provider, self.settings.ai_provider)
            self.assertIn('target_seconds', prompt)
            kw['validator'](styled_generated())
            return styled_generated()
        with patch.object(r, 'request_json', side_effect=response):
            self.assertEqual(len(r.generate_script(self.draft, self.settings, self.token)), 5)

    def test_single_regeneration_preserves_all_other_manual_edits(self):
        self.draft['segments'][1].update(spoken='人工编辑，保留本句。', edited=True)
        original = deepcopy(self.draft['segments'][1])
        changed = dict(self.draft['segments'][2], spoken='新稿。')
        r.apply_segments(self.draft, [changed], single_id='focus2')
        self.assertEqual(self.draft['segments'][1], original)
        with self.assertRaises(ValueError):
            r.apply_segments(self.draft, [changed], single_id='focus1')

    def test_background_facts_keep_their_exact_source_reference(self):
        from services.launch_research import fingerprint
        item = self.draft['snapshot']['items'][0]
        item['research'] = dict(version=1, fingerprint=fingerprint(item), evidence_status='supported',
            human_status='unreviewed', sources=[dict(source_id=0, url='https://example.invalid/official',
            title='Fixed mock announcement', text='这颗卫星用于技术验证。')],
            matches=[dict(source_id=0, summary='这颗卫星用于技术验证。')])
        raw = generated()
        raw['segments'][1]['spoken'] += '[[fact:launch-1:0]]'
        segment = r.validate_generated(raw, self.draft)[1]
        self.assertIn('技术验证', segment['spoken'])
        self.assertNotIn('https', segment['spoken'])
        self.assertEqual(segment['source_refs'][0]['quote'], '这颗卫星用于技术验证。')

    def test_model_file_change_and_corrupt_audio_invalidate_cache(self):
        model = self.root / 'model-cache-test'
        model.mkdir()
        config = model / 'config.json'
        config.write_text('{}')
        self.draft['tts']['model'] = str(model)
        generate_audio(self.draft, self.provider, self.token, segment_id='focus1')
        self.assertIsNotNone(current_audio(self.draft, self.draft['segments'][1]))
        config.write_text('{"changed":true}')
        self.assertIsNone(current_audio(self.draft, self.draft['segments'][1]))

    def test_pagination_cancel_and_unsafe_next_preserve_previous_data(self):
        save_collection(self.collection['items'], complete=False)
        response = Mock(is_redirect=False)
        response.json.return_value = dict(count=2, next='https://attacker.invalid/page', results=[dict(id='x', name='x')])
        get = Mock(return_value=response)
        result = refresh_collection('2026-10-20', '2026-10-29', self.settings, self.token, get=get)
        self.assertFalse(result['complete'])
        get.assert_called_once()
        self.token.cancel()
        previous = (self.root / 'cache/launch_collection.json').read_bytes()
        with self.assertRaises(TaskCancelledError):
            refresh_collection('2026-10-20', '2026-10-29', self.settings, self.token, get=get)
        self.assertEqual((self.root / 'cache/launch_collection.json').read_bytes(), previous)

    def test_export_cancel_retains_completed_files_and_marks_incomplete(self):
        def renderer(draft, segment, directory, frames, token):
            path = directory / 'mock.mov'
            path.write_bytes(b'mock only')
            token.cancel()
            return path
        with self.assertRaises(TaskCancelledError):
            export_package(self.draft, self.root, self.token, renderer=renderer)
        directory = next(self.root.glob('SpaceWeekly_roundup_*'))
        self.assertTrue((directory / 'mock.mov').exists())
        self.assertEqual(json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['state'], 'incomplete')

    def test_qwen_child_cancel_is_propagated_and_process_is_stopped(self):
        model = self.root / 'model'
        model.mkdir()
        (model/'config.json').write_text(json.dumps(dict(tts_model_type='custom_voice', tts_model_size='0b6')))
        fake = self.root/'slow_mock.py'
        fake.write_text('import time\ntime.sleep(60)\n')
        calls = []
        def cancel():
            calls.append(1)
            if len(calls) >= 3:
                raise TaskCancelledError('mock cancellation')
        with patch('ai.qwen_tts.bundled_resource_path', return_value=fake), self.assertRaises(TaskCancelledError):
            QwenTTSProvider(dict(python=sys.executable, model=str(model))).synthesize(
                SpeechRequest('cancel mock', model=str(model), voice='Vivian'), self.root/'never.wav', cancel)
        self.assertFalse((self.root/'never.wav').exists())

    def test_snapshot_saved_and_data_difference_reported_without_refresh(self):
        path = self.root / 'draft.json'
        r.save_draft(self.draft, path)
        reopened = r.load_draft(path)
        self.assertEqual(reopened, self.draft)
        newer = deepcopy(self.collection)
        newer['items'][0]['net'] = '2026-10-21T13:00:00Z'
        self.assertEqual(r.changed_tasks(reopened, newer), ['launch-1'])
        self.assertEqual(reopened['snapshot']['items'][0]['net'], context(1)['net'])

    def test_audio_uses_measured_duration_and_reuses_cache(self):
        self.assertEqual(generate_audio(self.draft, self.provider, self.token), [])
        self.assertEqual(self.provider.synthesize.call_count, 5)
        for segment in self.draft['segments']:
            self.assertEqual(current_audio(self.draft, segment)['duration_seconds'], .5)
        generate_audio(self.draft, self.provider, self.token)
        self.assertEqual(self.provider.synthesize.call_count, 5)

    def test_text_voice_style_rules_and_model_changes_invalidate_audio(self):
        generate_audio(self.draft, self.provider, self.token, segment_id='focus1')
        for field, value in [('spoken', '另一个完整段落。'), ('speaker', 'different'), ('style', '平静'), ('model', 'changed-model')]:
            draft = deepcopy(self.draft)
            if field == 'spoken':
                draft['segments'][1][field] = value
            else:
                draft['tts'][field] = value
            self.assertIsNone(current_audio(draft, draft['segments'][1]))
        draft = deepcopy(self.draft)
        draft['pronunciations']['Fixture'] = '测试'
        self.assertIsNone(current_audio(draft, draft['segments'][1]))
        draft['segments'][1]['screen'] = '仅修改屏幕'
        self.assertIsNotNone(current_audio(self.draft, dict(self.draft['segments'][1], screen='仅改画面')))

    def test_pronunciation_rules_are_one_pass_and_leave_screen_unchanged(self):
        self.assertEqual(speech_text('ABC ABCD', {'ABCD': 'AB', 'AB': 'C'}), 'CC AB')
        self.draft['pronunciations'] = {'Fixture': '测试'}
        original = deepcopy(self.draft['segments'][1])
        generate_audio(self.draft, self.provider, self.token, segment_id='focus1')
        self.assertIn('测试', self.provider.synthesize.call_args.args[0].text)
        self.assertEqual(self.draft['segments'][1]['spoken'], original['spoken'])
        self.assertEqual(self.draft['segments'][1]['screen'], original['screen'])

    def test_single_failure_keeps_other_successes_and_retry(self):
        def fail(req, path, cancel_check):
            if 'Fixture mission 2' in req.text:
                raise RuntimeError('mock failure')
            write_pcm(path)
        self.provider.synthesize.side_effect = fail
        errors = generate_audio(self.draft, self.provider, self.token)
        self.assertEqual(errors[0][0], 'focus2')
        self.assertIsNotNone(current_audio(self.draft, self.draft['segments'][1]))
        self.assertIsNone(current_audio(self.draft, self.draft['segments'][2]))
        self.provider.synthesize.side_effect = lambda req, path, cancel_check: write_pcm(path)
        generate_audio(self.draft, self.provider, self.token, segment_id='focus2')
        self.assertIsNotNone(current_audio(self.draft, self.draft['segments'][2]))

    def test_cancel_keeps_successful_segment_and_cleans_current_temporary_file(self):
        calls = []
        def cancel(req, path, cancel_check):
            calls.append(path)
            write_pcm(path)
            if len(calls) == 2:
                self.token.cancel()
        self.provider.synthesize.side_effect = cancel
        with self.assertRaises(TaskCancelledError):
            generate_audio(self.draft, self.provider, self.token)
        self.assertIsNotNone(current_audio(self.draft, self.draft['segments'][0]))
        self.assertFalse(calls[1].exists())
        self.assertFalse(list(self.root.rglob('*.tmp*')))

    def test_export_wav_srt_manifest_measured_timeline_and_no_overwrite(self):
        generate_audio(self.draft, self.provider, self.token)
        def renderer(draft, segment, directory, frames, token):
            path = directory / (segment['id'] + '.mov')
            path.write_bytes(b'MOCK-VIDEO-NOT-A-REAL-RENDER')
            return path
        first = export_package(self.draft, self.root, self.token, renderer=renderer)
        data = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(data['subtitle_precision'], 'paragraph')
        self.assertEqual(data['segments'][0]['duration_seconds'], .5)
        self.assertAlmostEqual(data['segments'][1]['start_seconds'], .8)
        self.assertAlmostEqual(wav_info(first / data['narration_file'])[0]/24000, 3.7)
        self.assertIn('00:00:00,000 --> 00:00:00,500', (first / 'paragraphs.srt').read_text(encoding='utf-8'))
        second = export_package(self.draft, self.root, self.token, visuals=False)
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'manifest.json').exists())
        reopened = r.load_draft(first / 'draft.json')
        self.assertEqual(Path(reopened['segments'][0]['audio']['path']), first / '01.wav')

    def test_no_tts_exports_script_without_fake_audio_or_precise_subtitles(self):
        directory = export_package(self.draft, self.root, self.token, visuals=False)
        self.assertTrue((directory / 'script.md').exists())
        self.assertTrue((directory / 'snapshot.json').exists())
        self.assertFalse(list(directory.glob('*.wav')))
        self.assertFalse(list(directory.glob('*.srt')))
        self.assertEqual(json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['segments'][0]['timing_source'], 'editable_preview')

    def test_pagination_completion_and_partial_failure(self):
        def page(n, more):
            response = Mock(is_redirect=False)
            response.json.return_value = dict(count=2, next=more, results=[dict(id=f'id-{n}', name='Fixture', net='2026-10-25T00:00:00Z')])
            return response
        next_url = 'https://ll.thespacedevs.com/2.3.0/launches/?offset=100'
        get = Mock(side_effect=[page(1, next_url), page(2, None)])
        result = refresh_collection('2026-10-20', '2026-10-29', self.settings, self.token, get=get)
        self.assertTrue(result['complete'])
        self.assertEqual(result['pages'], 2)
        get.side_effect = [page(1, next_url), ValueError('broken page')]
        partial = refresh_collection('2026-10-20', '2026-10-29', self.settings, self.token, get=get)
        self.assertFalse(partial['complete'])
        self.assertEqual(len(partial['items']), 1)
        self.assertIn('broken page', partial['reason'])

    def test_open_local_collection_does_not_network(self):
        save_collection(self.collection['items'], '2026-10-20', '2026-10-29', True)
        with patch('requests.get', side_effect=AssertionError('implicit network')):
            self.assertEqual(len(load_existing(self.root)['items']), 3)

    def test_new_collection_keeps_other_dates_but_complete_range_replaces_stale_rows(self):
        save_collection(self.collection['items'], '2026-10-20', '2026-10-29', True)
        save_collection([context(4, net='2026-11-01T00:00:00Z')], '2026-11-01', '2026-11-01', True)
        self.assertEqual(len(load_existing(self.root)['items']), 4)
        save_collection([context(1)], '2026-10-20', '2026-10-29', True)
        self.assertEqual({i['ll2_id'] for i in load_existing(self.root)['items']}, {'launch-1', 'launch-4'})
        save_collection([context(5, net='2026-12-01T00:00:00Z')], complete=False)
        self.assertNotIn('launch-3', {i['ll2_id'] for i in load_existing(self.root)['items']})

    def test_qwen_capabilities_and_isolated_real_subprocess_with_mock_engine(self):
        model = self.root / 'model'
        model.mkdir()
        config = dict(tts_model_type='custom_voice', tts_model_size='0b6', talker_config=dict(spk_id={'vivian': 1}))
        (model / 'config.json').write_text(json.dumps(config))
        self.assertFalse(capabilities(str(model))['style'])
        fake = self.root / 'mock_worker.py'
        from tests.qwen_fixture import worker_source
        fake.write_text(worker_source(), encoding='utf-8')
        settings = dict(python=sys.executable, model=str(model), device='cpu', timeout=10)
        target = self.root / 'mock-process.wav'
        provider = QwenTTSProvider(settings)
        self.addCleanup(provider.unload)
        with patch('ai.qwen_tts.bundled_resource_path', return_value=fake):
            provider.synthesize(SpeechRequest('模拟进程通信检查', model=str(model), voice='vivian'), target)
        self.assertEqual(wav_info(target), (12000, 24000))
        with self.assertRaises(ValueError):
            provider.synthesize(SpeechRequest('测试', model=str(model), voice='vivian', style='平静'), target)
        config['tts_model_size'] = '1b7'
        (model / 'config.json').write_text(json.dumps(config))
        self.assertTrue(capabilities(str(model))['style'])

    def test_ui_entry_and_manual_edit_survive_paragraph_reorder(self):
        from PySide6.QtWidgets import QApplication
        from ui.launch_roundup_dialog import LaunchRoundupDialog
        from ui.widgets.app_toolbar import AppToolBar
        app = QApplication.instance() or QApplication([])
        toolbar = AppToolBar()
        self.assertIn('每周发射综述', [a.text() for a in toolbar.actions()])
        with patch('ui.launch_roundup_dialog.load_existing', return_value=self.collection), patch('requests.get', side_effect=AssertionError('implicit request')):
            dialog = LaunchRoundupDialog(self.settings)
        self.addCleanup(dialog.close)
        dialog.draft = deepcopy(self.draft)
        dialog._show_draft()
        dialog.segment_list.setCurrentRow(1)
        dialog.spoken.setPlainText('人工编辑的完整短句。')
        dialog._move_segment(1)
        self.assertEqual(dialog.draft['segments'][2]['spoken'], '人工编辑的完整短句。')
        self.assertTrue(dialog.draft['segments'][2]['edited'])
        self.assertFalse(dialog.style.isVisible())


if __name__ == '__main__':
    unittest.main()
