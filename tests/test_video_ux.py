"""Video controls, saved opacity and headline rules; all network/AI calls are mocked."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QTimer
from ai.deepseek import translate_titles
from ai.prompts import build_title_translation_prompt
from i18n import set_language, tr
from models.ai_summary import AISummary
from models.article import Article
from models.news import News
from models.video_download import VideoFormat, VideoInfo
from services import prompt_manager, settings as settings_service
from services import video_download as downloads, video_download_state as download_state
from models.video_download import VideoSearchResults
from services.cancellation import CancellationToken, TaskCancelledError
from services.weekly_report import _localize_selected_titles
from tools import video_cards as video
from ui.prompt_studio_dialog import PromptStudioDialog
from ui.video_cards_dialog import VideoCardsDialog
from ui.video_intro_dialog import VideoIntroDialog, IntroWorker
from tools import video_intro as opener
from ui.video_download_dialog import VideoDownloadDialog, VideoDownloadWorker
from utils.output_files import reserved_output

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.bilibili.com/video/BV1xx411c7mD'


def launch():
    return dict(id='fictional-launch', title='测试火箭', summary='测试任务',
                source='Fictional fixture', published='2026-10-10T01:00:00Z',
                url='https://example.invalid/fixture', categories=[], video_type='launch',
                launch=dict(orbit='近地轨道', payload='试验卫星', site='测试场', time='2026/10/10 09:00'))


class VideoUXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        (ROOT/'build').mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT/'build')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        set_language('zh_CN')

    def dialog(self):
        d = VideoDownloadDialog(self.root, [launch()])
        d._restore_pending = False
        self.addCleanup(d.reject)
        return d

    def test_opacity_changes_only_panel_and_validates_values(self):
        item = launch()
        for value in (0, 35, 100):
            item['launch']['background_opacity'] = value
            frame = video.render_frame(video.make_card(item, 1), 5, 12)
            self.assertEqual(frame.getpixel((20, 500)), video.BLUE[:3] + (round(255*value/100),))
            self.assertEqual(frame.getpixel((600, 500)), video.WHITE)
            self.assertEqual(frame.getpixel((1200, 500))[3], 0)
        for value in (-1, 101, True, '50', float('nan'), float('inf'), None):
            item['launch']['background_opacity'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                video.validate_snapshot(dict(protocol_version='1.0',job_id='test',report_title='test',language='zh_CN',items=[item]))
        item['launch'].pop('background_opacity')
        self.assertEqual(video.make_card(item, 1)['background_alpha'], 255)

    def test_opacity_save_reload_preserves_original_snapshot(self):
        source = ROOT/'plugins/premiere-uxp/example.premiere.json'
        original = source.read_bytes()
        d = VideoCardsDialog(self.root)
        self.addCleanup(d.reject)
        d.load_snapshot(source)
        d._add_launch()
        self.assertEqual(d.launch_opacity.value(), 100)
        d.launch_opacity.setValue(35)
        current_id = d.data['items'][d.index]['id']
        d.items.setCurrentRow(0)
        d.items.setCurrentRow(next(i for i,item in enumerate(d.data['items']) if item['id']==current_id))
        self.assertEqual(d.launch_opacity.value(), 35)
        saved = self.root/'edited.json'
        with patch('ui.video_cards_dialog.QFileDialog.getSaveFileName',return_value=(str(saved),'')):
            d._save_snapshot()
        d.load_snapshot(saved)
        d.items.setCurrentRow(next(i for i,item in enumerate(d.data['items']) if item['id']==current_id))
        self.assertEqual(d.launch_opacity.value(), 35)
        self.assertEqual(video.launch_background_alpha(d.data['items'][d.index]), 89)
        self.assertEqual(source.read_bytes(), original)

    def test_opacity_invalidates_cache_without_overwriting_previous_file(self):
        item = launch()
        data = dict(protocol_version='1.0', job_id='opacity-fixture', report_title='Fixture', language='zh_CN', items=[item])
        def encode(card,directory,*args):
            with reserved_output(directory,'.launch.mov') as (final,temp):
                temp.write_bytes(bytes([card['background_alpha']]))
            return final
        with patch.object(video.shutil,'which',side_effect=lambda name:name), patch.object(video,'encode_card',side_effect=encode) as encoding:
            manifest = video.generate(data,self.root)
            first = json.loads(manifest.read_text(encoding='utf-8'))['cards'][0]
            video.generate(data,self.root)
            self.assertEqual(encoding.call_count,1)
            item['launch']['background_opacity'] = 35
            video.generate(data,self.root)
            self.assertEqual(encoding.call_count,2)
        latest = json.loads(manifest.read_text(encoding='utf-8'))['cards'][0]
        self.assertEqual(latest['background_opacity'],35)
        self.assertNotEqual(first['fingerprint'],latest['fingerprint'])
        self.assertNotEqual(first['file'],latest['file'])
        self.assertEqual((manifest.parent/first['file']).read_bytes(),bytes([255]))

    def test_news_default_title_is_larger_and_stays_one_line(self):
        card = video.make_card(dict(title='航天任务进展', summary='试验顺利完成'),1)
        self.assertGreater(card['title_font_size'],50)
        self.assertEqual(len(card['title_lines']),1)

    def test_download_has_seven_buttons_and_keeps_auxiliary_actions_in_menu(self):
        d = self.dialog()
        buttons = d.findChildren(QPushButton)
        self.assertLessEqual(len(buttons),7)
        self.assertTrue(all(not button.autoDefault() for button in buttons))
        self.assertIn(d.demo,d.more_menu.actions())
        self.assertIn(d.open_file,d.more_menu.actions())
        self.assertIn(d.add_up_action,d.more_menu.actions())
        self.assertTrue(d.cancel.isHidden())
        self.assertFalse(d.parse_button.isEnabled())
        self.assertFalse(d.download.isEnabled())
        self.assertTrue(d.search_button.isEnabled())
        d.keyword.setEditText('')
        self.assertFalse(d.search_button.isEnabled())
        d.url.setText(URL)
        self.assertTrue(d.parse_button.isEnabled())
        d.url.clear()
        self.assertFalse(d.parse_button.isEnabled())

    def test_invalid_url_does_not_start_background_worker(self):
        d = self.dialog()
        d.url.setText('https://example.invalid/video')
        with patch('ui.video_download_dialog.service.parse_video') as parse:
            d._parse()
        self.assertIsNone(d.worker)
        parse.assert_not_called()
        self.assertIn('链接无效',d.status.text())

    def test_changing_task_date_clears_previous_video_and_download_state(self):
        d = self.dialog()
        d.url.setText(URL)
        info = VideoInfo(id='x',title='test',url=URL,formats=[VideoFormat('v',720,'avc1')])
        d._show_parsed_video(info)
        d.rights.setChecked(True)
        self.assertTrue(d.download.isEnabled())
        d.use_task_date.setChecked(False)
        self.assertEqual(d.url.text(),'')
        self.assertIsNone(d.info)
        self.assertFalse(d.download.isEnabled())
        self.assertFalse(d.parse_button.isEnabled())

    def test_busy_and_cancel_controls_restore_after_worker_finishes(self):
        d = self.dialog()
        d.url.setText(URL)
        d.rights.setChecked(True)
        with patch.object(VideoDownloadWorker,'start'):
            d._parse()
        self.assertFalse(d.cancel.isHidden())
        self.assertFalse(d.parse_button.isEnabled())
        self.assertFalse(d.search_button.isEnabled())
        self.assertFalse(d.demo.isEnabled())
        d._finished()
        self.assertTrue(d.cancel.isHidden())
        self.assertTrue(d.parse_button.isEnabled())
        self.assertTrue(d.search_button.isEnabled())
        self.assertTrue(d.demo.isEnabled())


    def test_up_preferences_are_saved_on_close_and_manual_keyword_is_respected(self):
        d = self.dialog()
        d.up_ids.setText('123, 456')
        d.keyword.setEditText('手动精确检索')
        options = d._search_options()
        self.assertTrue(options['prefer_up_ids'])
        self.assertEqual(options['up_ids'],['123','456'])
        self.assertEqual(options['fallback_keywords'],[])
        d.up_ids.setText('123, 789')
        d.reject()
        self.assertEqual(download_state.load_preferences(self.root)['up_ids'],['123','789'])
        reopened=self.dialog()
        self.assertEqual(reopened.up_ids.text(),'123, 789')


class HeadlineRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT/'build')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.presets = patch.object(prompt_manager,'PROMPTS_DIR',self.root/'prompts')
        self.presets.start();self.addCleanup(self.presets.stop)
        set_language('zh_CN')
        self.settings = replace(settings_service.default_settings(),deepseek_api_key='test-only',
            deepseek_base_url='https://example.invalid',deepseek_model='fixture',title_prompt='用一句话概括 {title}，突出任务成果')

    def test_rule_ui_and_preset_export_import_round_trip(self):
        d = PromptStudioDialog(self.settings);self.addCleanup(d.reject)
        d.type_list.setCurrentRow(prompt_manager.PROMPT_TYPES.index('title'))
        self.assertEqual(d.editor.toPlainText(),self.settings.title_prompt)
        d.editor.setPlainText('根据文章内容概括，不超过二十字')
        self.assertEqual(d.settings().title_prompt,'根据文章内容概括，不超过二十字')
        exported = self.root/'exported.json'
        with patch('ui.prompt_studio_dialog.QFileDialog.getSaveFileName',return_value=(str(exported),'')), patch('ui.prompt_studio_dialog.QMessageBox.information'):
            d._export_preset()
        data = json.loads(exported.read_text(encoding='utf-8'))
        self.assertEqual(data['title'],d.settings().title_prompt)
        data['name']='Imported';exported.write_text(json.dumps(data),encoding='utf-8')
        with patch('ui.prompt_studio_dialog.QFileDialog.getOpenFileName',return_value=(str(exported),'')), patch('ui.prompt_studio_dialog.QMessageBox.information'):
            d._import_preset()
        self.assertEqual(prompt_manager.find_preset('Imported').title,data['title'])
        d._load_preset('Imported')
        self.assertEqual(d.editor.toPlainText(),data['title'])

    def test_legacy_preset_and_settings_keep_default_rule(self):
        old = self.root/'prompts/old.json';old.parent.mkdir()
        old.write_text(json.dumps(dict(name='Old',filter='filter',summary='summary')),encoding='utf-8')
        self.assertEqual(prompt_manager.find_preset('Old').title,prompt_manager.DEFAULT_TITLE_RULE)
        config = self.root/'config.json'
        with patch.object(settings_service,'CONFIG_PATH',config):
            settings_service.save_settings(self.settings)
            self.assertEqual(settings_service.load_settings().title_prompt,self.settings.title_prompt)
            data=json.loads(config.read_text(encoding='utf-8'));data.pop('title_prompt')
            config.write_text(json.dumps(data),encoding='utf-8')
            self.assertEqual(settings_service.load_settings().title_prompt,'')

    def test_generated_titles_use_body_summary_and_custom_rule_for_same_language_news(self):
        a = Article(News('Fixture','原有中文标题','2026-10-10','原始摘要','https://example.invalid/1'),'任务完成热试车，这是正文事实。','fixture')
        with patch('services.weekly_report.translate_titles',return_value={'item_000':'测试火箭完成热试车'}) as generate:
            titles,calls = _localize_selected_titles([a],self.settings,0,CancellationToken(),
                summaries={a.news.url:AISummary('已完成关键试验',['试验'])})
        self.assertEqual(titles[a.news.url],'测试火箭完成热试车')
        self.assertEqual(calls,1)
        context = generate.call_args.kwargs['article_contexts']['item_000']
        self.assertIn('正文事实',context['body'])
        self.assertEqual(context['summary'],'已完成关键试验')
        self.assertIn('原有中文标题',context['rule'])
        self.assertNotIn('{title}',context['rule'])
        self.assertEqual(a.news.title,'原有中文标题')

    def test_generation_failure_keeps_original_and_cancellation_propagates(self):
        a = Article(News('Fixture','原有中文标题','','','https://example.invalid/1'),'正文','fixture')
        with patch('services.weekly_report.translate_titles',side_effect=RuntimeError('offline')), self.assertLogs('services.weekly_report',level='ERROR'):
            titles,calls = _localize_selected_titles([a],self.settings,0,CancellationToken())
        self.assertEqual(titles[a.news.url],a.news.title)
        with patch('services.weekly_report.translate_titles',side_effect=TaskCancelledError):
            with self.assertRaises(TaskCancelledError):
                _localize_selected_titles([a],self.settings,0,CancellationToken())

    def test_title_request_uses_generation_module_and_validates_ids(self):
        def response(prompt,provider,**kwargs):
            self.assertIn('body fact',prompt)
            self.assertEqual(kwargs['module_name'],'title_generation')
            result={'one':'任务完成测试'}
            kwargs['validator'](result)
            with self.assertRaises(Exception):kwargs['validator']({'wrong':'标题'})
            return result
        with patch('ai.deepseek.request_json',side_effect=response):
            result=translate_titles({'one':'Original'},self.settings,custom_prompt='简明概括',article_contexts={'one':{'body':'body fact'}})
        self.assertEqual(result,{'one':'任务完成测试'})
        prompt = build_title_translation_prompt({'one':'Original'},'en_US','Write a clear headline',{'one':{'body':'body fact'}})
        self.assertIn('All generated text must be in English',prompt)
        self.assertIn('body fact',prompt)



class SearchPriorityTests(unittest.TestCase):
    def test_generic_payload_uses_rocket_variants_and_specific_missions_stay_first(self):
        item=dict(video_type='launch',title='朱雀二号E',summary='10颗卫星',published='2026-10-14')
        queries=downloads.mission_search_keywords(item)
        self.assertEqual(queries[0],'朱雀二号E')
        self.assertIn('朱雀2号E',queries)
        self.assertIn('朱雀二号',queries)
        self.assertNotEqual(queries[0],'10颗卫星')
        for name in ('星链10-12任务','商业卫星试验任务','载人飞船测试'):
            self.assertEqual(downloads.mission_search_keywords(dict(item,summary=name))[0],name)
        self.assertIn('朱雀二号',downloads.mission_search_keywords(dict(item,title='朱雀2e')))
        self.assertEqual(downloads.mission_search_keywords(dict(item,title='ZhuQue-2E'))[0],'ZhuQue-2E')
        self.assertNotIn('号',downloads.mission_search_keywords(dict(item,title='ZhuQue-2E'))[0])

    def test_preferred_up_is_ranked_first_while_other_uploaders_are_kept(self):
        preferred=VideoInfo('preferred','preferred',URL,author_id='123',published='2026-10-15')
        rows=[dict(bvid='BV1xx411c7mD',title='other',mid=999,pubdate=1791900000)]
        with patch.object(downloads,'_get',return_value=''), \
             patch.object(downloads,'_up_search',return_value=VideoSearchResults([preferred],'up')), \
             patch.object(downloads,'_global_rows',return_value=rows):
            result=downloads.search_videos('rocket',up_ids=['123'],prefer_up_ids=True)
        self.assertEqual(result.method,'priority')
        self.assertEqual([item.author_id for item in result.items],['123','999'])

    def test_preferred_up_failure_still_allows_public_results_and_retains_error(self):
        rows=[dict(bvid='BV1xx411c7mD',title='other',mid=999,pubdate=1791900000)]
        with patch.object(downloads,'_get',return_value=''), \
             patch.object(downloads,'_up_search',side_effect=downloads.VideoDownloadError('up_search','HTTP 403')), \
             patch.object(downloads,'_global_rows',return_value=rows):
            result=downloads.search_videos('rocket',up_ids=['123'],prefer_up_ids=True)
        self.assertEqual(result.items[0].author_id,'999')
        self.assertIn('403',result.warning)
        with patch.object(downloads,'_get',return_value=''), \
             patch.object(downloads,'_up_search',side_effect=downloads.VideoDownloadError('up_search','HTTP 403')), \
             patch.object(downloads,'_global_rows',return_value=[]):
            with self.assertRaises(downloads.VideoDownloadError):
                downloads.search_videos('rocket',up_ids=['123'],prefer_up_ids=True)

    def test_empty_query_tries_name_fallback_and_reports_actual_query(self):
        def rows(session,keyword,*args):
            return [] if keyword=='朱雀二号E' else [dict(bvid='BV1xx411c7mD',title='朱雀二号发射',mid=999,pubdate=1791900000)]
        with patch.object(downloads,'_get',return_value=''),patch.object(downloads,'_global_rows',side_effect=rows) as read:
            result=downloads.search_videos('朱雀二号E',fallback_keywords=['朱雀二号'])
        self.assertEqual(result.keyword,'朱雀二号')
        self.assertEqual([call.args[1] for call in read.call_args_list],['朱雀二号E','朱雀二号'])
        with patch.object(downloads,'_get',return_value=''),patch.object(downloads,'_global_rows',side_effect=rows) as read:
            result=downloads.search_videos('朱雀二号',fallback_keywords=['不要运行'])
        self.assertEqual(read.call_count,1)

    def test_network_errors_do_not_become_empty_success_or_trigger_query_retries(self):
        with patch.object(downloads,'_get',return_value=''), \
             patch.object(downloads,'_global_rows',side_effect=downloads.VideoDownloadError('search','HTTP 412')) as read:
            with self.assertRaises(downloads.VideoDownloadError):
                downloads.search_videos('rocket',fallback_keywords=['other name'])
        self.assertEqual(read.call_count,1)

    def test_priority_demo_can_download_other_up_without_ignoring_date_checks(self):
        window=downloads.mission_date_range(dict(published='2026-10-14'))
        info=VideoInfo('fixture','fixture',URL,author_id='999',published_timestamp=window[0]+1,
                       formats=[VideoFormat('v',720,'avc1')])
        with patch.object(downloads,'search_videos',return_value=VideoSearchResults([info])), \
             patch.object(downloads,'parse_video',return_value=info), \
             patch.object(downloads,'download_video',return_value=Path('fixture.mp4')) as save:
            downloads.demo_download('rocket',window,Path('fixture'),up_ids=['123'],prefer_up_ids=True)
            self.assertEqual(save.call_count,1)
            info.published_timestamp=window[1]
            with self.assertRaises(downloads.VideoDownloadError):
                downloads.demo_download('rocket',window,Path('fixture'),up_ids=['123'],prefer_up_ids=True)
            self.assertEqual(save.call_count,1)



class IntroPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory(dir=ROOT/'build')
        self.addCleanup(self.scratch.cleanup)
        self.root=Path(self.scratch.name)
        set_language('zh_CN')
        self.d=VideoIntroDialog(self.root/'intro')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.d.reject()
        if self.d.preview_worker is not None:
            self.d.preview_worker.wait(3000)
            self.app.processEvents()
        self.d.deleteLater()
        self.app.processEvents()

    def gif(self):
        path=self.root/'animated.gif'
        a=video.Image.new('RGBA',(40,40),'red')
        b=video.Image.new('RGBA',(40,40),'blue')
        a.save(path,save_all=True,append_images=[b],duration=100,loop=0)
        self.d.logo.setText(str(path));self.d.refresh.stop();self.d._preview()
        return path

    def wait_preview(self):
        deadline=time.monotonic()+4
        while self.d.preview_worker is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.d.preview_worker)

    def test_play_pause_seek_resume_end_and_edit_reset(self):
        self.assertEqual(self.d.preview_slider.maximum(),420)
        self.d._toggle_preview()
        self.assertTrue(self.d._preview_timer.isActive())
        self.assertEqual(self.d.preview_slider.value(),0)
        self.d.preview_slider.setValue(120)
        self.d._toggle_preview()
        self.assertFalse(self.d._preview_timer.isActive())
        self.assertEqual(self.d.preview_slider.value(),120)
        self.assertIn('2.0',self.d.preview_time.text())
        self.d._toggle_preview()
        self.assertEqual(self.d._play_offset,120)
        self.d._play_offset=420;self.d._tick_preview()
        self.assertFalse(self.d._preview_timer.isActive())
        self.d._toggle_preview()
        self.assertEqual(self.d.preview_slider.value(),0)
        self.d.title.setText('新片头');self.d.refresh.stop();self.d._preview()
        self.assertFalse(self.d._preview_timer.isActive())
        self.assertEqual(self.d.preview_slider.value(),180)
        self.assertEqual(self.d._preview_card['title'],'新片头')
        self.d.reject()
        self.assertFalse(self.d._preview_timer.isActive())

    def test_animated_preview_loads_in_worker_and_caches_full_logo(self):
        self.gif()
        original=opener.load_logo
        threads=[];ticks=[]
        def load(*args,**kwargs):
            threads.append(threading.get_ident());time.sleep(.12)
            return original(*args,**kwargs)
        timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
        with patch.object(opener,'load_logo',side_effect=load):
            self.d._toggle_preview();self.wait_preview()
        timer.stop()
        self.assertGreater(len(ticks),3)
        self.assertNotEqual(threads[0],threading.get_ident())
        self.assertEqual(len(self.d._preview_card['logo_frames']),2)
        self.assertTrue(self.d._preview_timer.isActive())
        self.d._stop_preview();self.d.preview_slider.setValue(141)
        self.assertEqual(opener.render_frame(self.d._preview_card,2.35).getpixel((620,530))[:3],(0,0,255))
        with patch.object(opener,'load_logo') as decode:
            self.d._toggle_preview();self.d._stop_preview();self.d._prepare_card()
        decode.assert_not_called()

    def test_seek_can_prepare_animation_without_starting_playback(self):
        self.gif()
        self.d.preview_slider.setValue(141)
        self.wait_preview()
        self.assertFalse(self.d._preview_timer.isActive())
        self.assertEqual(self.d.preview_slider.value(),141)
        self.assertEqual(len(self.d._preview_card['logo_frames']),2)

    def test_close_cancels_loading_and_generation_stops_preview(self):
        self.gif();started=threading.Event()
        def slow(**options):
            started.set()
            while True:
                options['cancel_check']();time.sleep(.01)
        self.d.show()
        with patch.object(opener,'prepare',side_effect=slow):
            self.d._toggle_preview();self.assertTrue(started.wait(1))
            self.d.reject();self.assertTrue(self.d.isVisible());self.wait_preview()
        self.assertFalse(self.d.isVisible())
        # A separate static preview must stop before starting the encoder.
        d=VideoIntroDialog(self.root/'static')
        d._toggle_preview()
        with patch.object(IntroWorker,'start'):
            d._start()
            self.assertFalse(d._preview_timer.isActive())
            d._finished()
        d.reject();d.deleteLater()

if __name__ == '__main__':
    unittest.main()
