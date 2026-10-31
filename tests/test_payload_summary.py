"""Short payload phrases and subtitle preference."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from ai.json_utils import AIResponseParseError
from feeds.launches import _launch_video_item
from models.video_download import VideoSearchResults
from services import video_download as video
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from services.weekly_report import _export_context, _export_reports, _localize_launch_video_items
from translator.translator import Translator, _translation_prompt, _validate_payload_summary
from tests.test_video_download_demo import candidate, WINDOW

MISSION = 'Starlink Group 15-15'
DESCRIPTION = 'A batch of 27 satellites for the Starlink constellation.'


class PayloadSummaryTests(unittest.TestCase):
    def test_short_phrase_cached_with_source_context(self):
        ai = Mock(return_value='27颗星链卫星')
        translator = Translator(ai_callable=ai, mode='ai')
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), '27颗星链卫星')
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), '27颗星链卫星')
        ai.assert_called_once()
        source, kind, long_text = ai.call_args.args
        self.assertEqual(json.loads(source), dict(mission=MISSION, payload=DESCRIPTION))
        self.assertEqual((kind, long_text), ('payload', True))

    def test_new_numbers_or_model_version_rejected_but_explicit_version_allowed(self):
        source = json.dumps(dict(mission='Starlink Group 2', payload=DESCRIPTION))
        for phrase in ('28颗星链卫星', '27颗星链V2 Mini卫星'):
            with self.subTest(phrase=phrase), self.assertRaises(AIResponseParseError):
                _validate_payload_summary(dict(translation=phrase), source, 'zh_CN')
        translator = Translator(ai_callable=lambda *_: '27颗星链V2 Mini卫星', mode='ai')
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), DESCRIPTION)
        self.assertEqual(translator.summarize_payload(MISSION, '27 Starlink V2 Mini satellites.'),
                         '27颗星链V2 Mini卫星')

    def test_ascii_model_code_is_not_replaced_with_invented_translation(self):
        source = json.dumps(dict(mission='Xiguang-4 01', payload='Earth observation satellite.'))
        with self.assertRaises(AIResponseParseError):
            _validate_payload_summary(dict(translation='西光四号01卫星'), source, 'zh_CN')
        _validate_payload_summary(dict(translation='Xiguang-4 01卫星'), source, 'zh_CN')

    def test_summary_shape_is_one_short_nonempty_phrase(self):
        for value in (None, '', 'unknown', 'x'*49, 'line one\nline two'):
            with self.subTest(value=value), self.assertRaises(AIResponseParseError):
                _validate_payload_summary(dict(translation=value), DESCRIPTION, 'zh_CN')
        with self.assertRaises(AIResponseParseError):
            _validate_payload_summary(dict(translation='satellite', extra='bad'), DESCRIPTION, 'en_US')

    def test_dictionary_empty_and_offline_fallback_never_fake_summary(self):
        ai = Mock(side_effect=OSError('offline'))
        translator = Translator(ai_callable=ai, mode='dictionary')
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), DESCRIPTION)
        ai.assert_not_called()
        translator.mode = 'ai'
        self.assertEqual(translator.summarize_payload(MISSION, ''), '')
        ai.assert_not_called()
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), DESCRIPTION)
        self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), DESCRIPTION)
        ai.assert_called_once()

    def test_cancelled_summary_is_not_cached_or_swallowed(self):
        token = CancellationToken()
        def complete(*_):
            token.cancel()
            return '27颗星链卫星'
        translator = Translator(ai_callable=complete, mode='ai', cancel_token=token)
        with self.assertRaises(TaskCancelledError):
            translator.summarize_payload(MISSION, DESCRIPTION)
        self.assertFalse(translator._cache)

    def test_prompt_is_short_source_bound_and_bilingual(self):
        source = json.dumps(dict(mission=MISSION, payload=DESCRIPTION))
        for language in ('zh_CN', 'en_US'):
            prompt = _translation_prompt(source, 'payload', True, language, [], '')
            for rule in ('short noun phrase', 'Mission/batch identifiers are not satellite counts',
                         'do not add V2 Mini', 'untrusted source data', source):
                self.assertIn(rule, prompt)

    def test_provider_path_uses_payload_validator(self):
        translator = Translator(settings=default_settings(), mode='ai')
        def request(prompt, config, **options):
            options['validator'](dict(translation='27颗星链卫星'))
            with self.assertRaises(AIResponseParseError):
                options['validator'](dict(translation='28颗星链卫星'))
            return dict(translation='27颗星链卫星')
        with patch('translator.translator.request_json', side_effect=request) as client:
            self.assertEqual(translator.summarize_payload(MISSION, DESCRIPTION), '27颗星链卫星')
        client.assert_called_once()

    def test_english_summary_preserves_names_and_word_language(self):
        settings = replace(default_settings(), language='en_US', export_premiere=True)
        translator = Translator(settings=settings, mode='ai', ai_callable=lambda *_: '27 Starlink satellites')
        item = _launch_video_item(dict(id='fixture', name='Falcon 9 | '+MISSION,
                 rocket=dict(configuration=dict(full_name='Falcon 9')),
                 mission=dict(name=MISSION, description=DESCRIPTION)), 'UTC')
        original = copy.deepcopy(item)
        with patch.object(translator, 'translate_term_result') as terms:
            localized = _localize_launch_video_items([item], translator, CancellationToken())[0]
        terms.assert_not_called()
        self.assertEqual(localized['launch']['payload'], '27 Starlink satellites')
        self.assertEqual(localized['title'], 'Falcon 9')
        self.assertEqual(item, original)
        with patch('services.weekly_report._launch_term_translator', return_value=translator), \
             patch('services.weekly_report.export_word') as word, \
             patch('services.weekly_report.export_premiere') as premiere:
            _export_reports([], {}, {}, _export_context(settings, {}), [], settings, False, True,
                            CancellationToken(), launch_video_items=[item])
        self.assertIsNone(word.call_args.kwargs['launch_term_translator'])
        self.assertEqual(premiere.call_args.kwargs['launches'][0]['launch']['payload'], '27 Starlink satellites')


class SubtitlePriorityTests(unittest.TestCase):
    def test_no_subtitles_outranks_up_and_each_time_order(self):
        sub = candidate('[中字] oldest', WINDOW[0]+1)
        sub.author_id = '123'
        raw = candidate('Original video', WINDOW[0]+3)
        raw.author_id = '999'
        preferred = candidate('Preferred original', WINDOW[0]+4)
        preferred.author_id = '123'
        for order in ('asc', 'desc', 'relevance'):
            result = video.sort_search_items([sub, raw, preferred], order, preferred_up_ids=['123'])
            self.assertEqual([item.title for item in result], [preferred.title, raw.title, sub.title])
        self.assertEqual(video.sort_search_items([sub, raw], 'asc'), [raw, sub])
        self.assertEqual(video.sort_search_items([sub], 'asc'), [sub])

    def test_priority_search_and_demo_choose_unsubtitled_candidate(self):
        sub, raw = candidate('[中文字幕] video'), candidate('original')
        sub.author_id, raw.author_id = '123', '999'
        sub.id, raw.id = 'sub', 'raw'
        raw.url = 'https://www.bilibili.com/video/BV1Q8HP69EJh'
        # The two recursive searches are mocked; the priority merge and ranking are real.
        service_search = video.search_videos
        def search(keyword, *args, **options):
            if options.get('prefer_up_ids'):
                return service_search(keyword, *args, **options)
            return VideoSearchResults([sub] if args[2] else [raw])
        with patch.object(video, 'search_videos', side_effect=search):
            found = service_search('mission', up_ids=['123'], prefer_up_ids=True)
        self.assertEqual(found.items, [raw, sub])
        with patch.object(video, 'search_videos', return_value=found), \
             patch.object(video, 'parse_video', return_value=raw) as parse, \
             patch.object(video, 'preferred_format', return_value=raw.formats[0]), \
             patch.object(video, 'download_video', return_value=Path('original.mp4')):
            video.demo_download('mission', WINDOW, Path('workspace'), up_ids=['123'], prefer_up_ids=True)
        self.assertEqual(parse.call_args.args[0], raw.url)


if __name__ == '__main__':
    unittest.main()
