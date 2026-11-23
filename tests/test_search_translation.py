"""DeepSeek search, source-backed names, and concise mission display."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from ai.json_utils import AIResponseParseError
from exporters import premiere, word, markdown
from exporters.launches import launch_fields
from feeds.launches import _launch_to_article, _launch_video_item
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from services.video_download import mission_search_keywords
from services.weekly_report import _export_context, _localize_launch_video_items
from translator import search
from translator import dictionary
from translator.translator import Translator, _validate_translation_payload, _validate_payload_summary
from utils.text import mission_display_name
from tests.test_video_assets import RAW


def official_name(name='西光肆号01星'):
    return search.SearchResult(name + '整装出征', 'https://www.xiopmspace.com/news/example',
                               name + '是遥感卫星。', True)


class DeepSeekSearchTests(unittest.TestCase):
    def test_settings_bilingual_selection_save_and_keys_do_not_cross_services(self):
        from PySide6.QtWidgets import QApplication
        from ui.settings_dialog import SettingsDialog
        from i18n import set_language
        app = QApplication.instance() or QApplication([])
        self.addCleanup(set_language, 'zh_CN')
        for language in ('zh_CN', 'en_US'):
            set_language(language)
            dialog = SettingsDialog(replace(default_settings(), web_search_provider='brave',
                                            web_search_api_key='fake-brave-key'))
            index = dialog.search_provider_combo.findData('deepseek')
            self.assertGreaterEqual(index, 0)
            dialog.search_provider_combo.setCurrentIndex(index)
            self.assertFalse(dialog.search_api_key_edit.text())
            self.assertTrue(dialog.search_api_key_edit.isEnabled())
            dialog.search_api_key_edit.setText('fake-deepseek-key')
            dialog.web_search_check.setChecked(True)
            saved = dialog.settings()
            self.assertEqual(saved.web_search_provider, 'deepseek')
            self.assertEqual(saved.web_search_api_key, 'fake-deepseek-key')
            self.assertTrue(saved.web_search_enabled)
            dialog.close()

    def response(self, data):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode('utf-8')
        return response

    def test_request_and_official_result_without_fetch_or_new_dependency(self):
        payload = dict(results=[dict(title='西光肆号01星出征', url=official_name().url,
                                    summary='西光肆号01星（五羊星座先导A星）', authority=0),
                                dict(title='bad', url='javascript:bad'), None])
        with patch.object(search.request, 'urlopen', return_value=self.response(payload)) as call:
            results = search.DeepSeekSearchProvider('fake-search-key', 9).search('Xiguang-4 01', 5)
        req = call.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.data), dict(query='Xiguang-4 01', count=5, need_content=False))
        self.assertEqual(req.get_header('Authorization'), 'Bearer fake-search-key')
        self.assertEqual(call.call_args.kwargs['timeout'], 9)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].official)

    def test_factory_reuses_only_official_direct_deepseek_credentials(self):
        base = replace(default_settings(), ai_provider='DeepSeek', web_search_provider='deepseek',
                       deepseek_base_url='https://api.deepseek.com/v1', deepseek_api_key='fake-ai-key',
                       web_search_api_key='')
        self.assertEqual(search.create_search_provider(base).api_key, 'fake-ai-key')
        for options in (dict(ai_provider='OpenAI'), dict(deepseek_base_url='https://proxy.example/v1'),
                        dict(deepseek_base_url='http://api.deepseek.com')):
            self.assertFalse(search.create_search_provider(replace(base, **options)).api_key)
        self.assertEqual(search.create_search_provider(replace(base, ai_provider='OpenAI',
                         web_search_api_key='fake-separate-key')).api_key, 'fake-separate-key')

    def test_empty_malformed_errors_and_credentials_are_not_reported_as_success(self):
        provider = search.DeepSeekSearchProvider('fake-search-key')
        with patch.object(search.request, 'urlopen', return_value=self.response(dict(results=[]))):
            self.assertEqual(provider.search('satellite'), [])
        for payload in ([], {}, dict(results={})):
            with patch.object(search.request, 'urlopen', return_value=self.response(payload)):
                with self.assertRaises(search.SearchProviderError):
                    provider.search('satellite')
        for code in (401, 402, 429, 504):
            failure = HTTPError('https://api.deepseek.com/v1/tools/search', code, 'error', {}, None)
            with patch.object(search.request, 'urlopen', side_effect=failure):
                with self.assertRaises(search.SearchProviderError) as caught:
                    provider.search('satellite')
            self.assertNotIn('fake-search-key', str(caught.exception))
            self.assertEqual(caught.exception.retryable, code in (429, 504))
            self.assertEqual(isinstance(caught.exception, search.SearchRateLimitError), code == 429)
        with patch.object(search.request, 'urlopen') as call:
            for query in (' ', 'x'*129):
                with self.assertRaises(search.SearchProviderError):
                    provider.search(query)
            with self.assertRaises(search.SearchProviderError):
                search.DeepSeekSearchProvider('').search('satellite')
        call.assert_not_called()

    def test_chinese_queries_fit_api_limit_and_permanent_failures_do_not_retry(self):
        provider = Mock(name='provider')
        provider.search.return_value = [official_name()]
        search.search_aerospace_sources(provider, 'Xiguang-4 01 ' * 20)
        queries = [call.args[0] for call in provider.search.call_args_list]
        self.assertTrue(all(len(query) <= 128 for query in queries))
        self.assertTrue(any('中文名' in query for query in queries))
        provider.search.reset_mock()
        provider.search.side_effect = search.SearchProviderError('HTTP 401', retryable=False)
        with self.assertRaises(search.SearchProviderError):
            search.search_aerospace_sources(provider, 'Xiguang-4 01', retry_count=2)
        provider.search.assert_called_once()


class VerifiedNameTests(unittest.TestCase):
    def test_first_workspace_load_merges_new_defaults_and_keeps_other_bundled_terms(self):
        with tempfile.TemporaryDirectory(dir='build') as directory:
            root = Path(directory)
            bundled = root / 'bundled.json'
            bundled.write_text(json.dumps({'CustomSat': {'translation': '测试星', 'type': 'mission'}}), encoding='utf-8')
            target = root / 'workspace/dictionary.json'
            with patch.object(dictionary, '_dictionary_path', return_value=target), \
                 patch.object(dictionary, 'resource_path', return_value=root / 'missing-legacy'), \
                 patch.object(dictionary, 'bundled_resource_path', return_value=bundled):
                self.assertEqual(dictionary.exact_dictionary_translation('Xiguang-4 01'), '')
                self.assertEqual(dictionary.exact_dictionary_translation('CustomSat'), '测试星')
                self.assertIn('Jiuquan', dictionary.load_dictionary())
            self.assertFalse(target.exists())

    def test_official_name_and_glossary_agreement_overrides_unchanged_ai_name(self):
        settings = replace(default_settings(), language='zh_CN', web_search_enabled=True, retry_count=0)
        provider = Mock()
        provider.search.return_value = [official_name()]
        translator = Translator(settings=settings, mode='ai', search_provider=provider,
                                ai_callable=lambda *_: 'Xiguang-4 01')
        with patch('translator.translator.get_cached_translation',
                   return_value=dict(translation='Xiguang-4 01', source='web+llm')), \
             patch('translator.translator.cache_translation'), patch('translator.translator.add_pending_term'):
            self.assertEqual(translator.translate_term('Xiguang-4 01', 'mission'), 'Xiguang-4 01')
        self.assertTrue(provider.search.called)
        def request(prompt, config, **options):
            with self.assertRaises(AIResponseParseError):
                options['validator'](dict(translation='西光肆号01星'))
            raise AIResponseParseError('Search snippets cannot verify object identity')
            return dict(translation='西光肆号01星')
        translator = Translator(settings=settings, mode='ai', search_provider=provider)
        with patch('translator.translator.get_cached_translation', return_value=None), \
             patch('translator.translator.cache_translation'), patch('translator.translator.add_pending_term'), \
             patch('translator.translator.request_json', side_effect=request):
            self.assertEqual(translator.translate_term('Xiguang-4 01', 'mission'), 'Xiguang-4 01')

    def test_chinese_model_ordinal_needs_exact_official_evidence(self):
        value = dict(translation='西光肆号01星')
        with self.assertRaises(AIResponseParseError):
            _validate_translation_payload(value, original='Xiguang-4 01', long_text=False,
                                          sources=[official_name()])
        for evidence in ([], [replace(official_name(), official=False)], [official_name('西光叁号01星')]):
            with self.assertRaises(AIResponseParseError):
                _validate_translation_payload(value, original='Xiguang-4 01', long_text=False, sources=evidence)
        for name in ('西光肆号02星', '西光叁号01星'):
            with self.assertRaises(AIResponseParseError):
                _validate_translation_payload(dict(translation=name), original='Xiguang-4 01',
                                              long_text=False, sources=[official_name(name)])

    def test_unknown_model_search_refreshes_ai_only_cache_and_reaches_payload(self):
        settings = replace(default_settings(), language='zh_CN', web_search_enabled=True, retry_count=0)
        provider = Mock()
        provider.search.return_value = [official_name('灵光肆号01星')]
        translator = Translator(settings=settings, mode='ai', search_provider=provider,
                                ai_callable=lambda *_: '灵光肆号01星')
        with patch('translator.translator.get_cached_translation',
                   return_value=dict(translation='Lumin-4 01', source='llm')), \
             patch('translator.translator.cache_translation'), patch('translator.translator.add_pending_term'):
            self.assertEqual(translator.summarize_payload('Lumin-4 01', 'Earth observation satellite.'),
                             'Earth observation satellite.')
            first_calls = provider.search.call_count
            translator.summarize_payload('Lumin-4 01', 'Earth observation satellite.')
            self.assertEqual(provider.search.call_count, first_calls)
        self.assertGreater(first_calls, 0)

    def test_known_official_name_and_payload_still_reject_invented_versions(self):
        translator = Translator(mode='hybrid', ai_callable=lambda *_: '西光肆号01星', web_search=False)
        with patch('translator.translator.exact_dictionary_translation', return_value='西光肆号01星'):
            self.assertEqual(translator.translate_term('Xiguang-4 01', 'mission'), '西光肆号01星')
            self.assertEqual(translator.summarize_payload('Xiguang-4 01', 'Earth observation satellite.'), '西光肆号01星')
        source = json.dumps(dict(mission='Xiguang-4 01', payload='Earth observation satellite.',
                                 verified_name='西光肆号01星'))
        with self.assertRaises(AIResponseParseError):
            _validate_payload_summary(dict(translation='西光肆号01星V2 Mini'), source, 'zh_CN')

    def test_cancel_after_search_prevents_ai_request(self):
        token = CancellationToken()
        provider = Mock()
        def search_and_cancel(*args, **kwargs):
            token.cancel()
            return [official_name()]
        provider.search.side_effect = search_and_cancel
        ai = Mock()
        translator = Translator(settings=replace(default_settings(), web_search_enabled=True, retry_count=0),
                                mode='ai', ai_callable=ai, cancel_token=token, search_provider=provider)
        with patch('translator.translator.get_cached_translation', return_value=None), \
             patch('translator.translator.add_pending_term'), self.assertRaises(TaskCancelledError):
            translator.translate_term('Lumin-4 01', 'mission')
        ai.assert_not_called()


class MissionNameTests(unittest.TestCase):
    def test_balanced_nested_notes_are_removed_without_breaking_name(self):
        for original, expected in [('Bandwagon 5 (Dedicated rideshare)', 'Bandwagon 5'),
                                   ('任务（说明 (子说明)） / 其他任务 (备注)', '任务 / 其他任务'),
                                   ('Starlink Group 15-15', 'Starlink Group 15-15'),
                                   ('任务 (未闭合', '任务 (未闭合'), ('(MMX)', '(MMX)')]:
            self.assertEqual(mission_display_name(original), expected)

    def test_reports_json_snapshot_search_and_offline_localization_keep_original_untouched(self):
        from docx import Document
        from tools.video_cards import validate_snapshot
        raw = copy.deepcopy(RAW)
        raw['name'] = 'Rocket | Test mission (launch note)'
        raw['mission'] = dict(raw['mission'], name='Test mission（extra explanation）',
                              description='Payload (description stays complete).')
        original = copy.deepcopy(raw)
        article = _launch_to_article(raw)
        item = _launch_video_item(raw, 'UTC')
        before = copy.deepcopy(item)
        self.assertEqual(launch_fields(article)['mission_name'], 'Test mission（extra explanation）')
        self.assertEqual(mission_search_keywords(item)[0], 'Test mission')
        self.assertEqual(_localize_launch_video_items([item], None, CancellationToken())[0]['summary'], 'Test mission')
        with tempfile.TemporaryDirectory(dir='build') as directory:
            root = Path(directory)
            with patch.object(premiere, 'OUTPUT_DIR', root), patch.object(word, 'OUTPUT_DIR', root), \
                 patch.object(markdown, 'OUTPUT_DIR', root):
                path = premiere.export_premiere([], {}, _export_context(default_settings(), {}), 'zh_CN', launches=[item])
                document = word.export_word([], launches=[article])
                md = markdown.export_markdown([], launches=[article]).read_text(encoding='utf-8')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['launches'][0]['summary'], 'Test mission')
            # Older JSON is normalized in a copy when loaded; news prose is unchanged.
            data['launches'][0]['summary'] = before['summary']
            snapshot_before = copy.deepcopy(data)
            self.assertEqual(validate_snapshot(data)['items'][0]['summary'], 'Test mission')
            self.assertEqual(data, snapshot_before)
            paragraphs = '\n'.join(p.text for p in Document(document).paragraphs)
            # Formal data/Markdown retain the full name; Word keeps its existing compact heading.
            self.assertNotIn('extra explanation',paragraphs)
            self.assertIn('extra explanation',md)
            for text in (paragraphs, md):
                self.assertNotIn('launch note', text)
                self.assertIn('Payload (description stays complete).', text)
        self.assertEqual(item, before)
        self.assertEqual(raw, original)


if __name__ == '__main__':
    unittest.main()
