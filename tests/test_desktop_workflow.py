"""Desktop workflow tests: fixed sources and mocked services; no real AI/TTS claims."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,time,unittest
from unittest.mock import patch,Mock
from contextlib import ExitStack
from PySide6.QtWidgets import QApplication,QMessageBox,QWidget,QDialog
from PySide6.QtCore import Qt,QDate,QTimer
import resources,config
from services import settings as settings_module,prompt_manager,feed_manager
from services import production as service,launch_roundup as r
from services.cancellation import CancellationToken,TaskCancelledError
from services.settings import default_settings,save_settings
from ui.main_window import MainWindow
from models.article import Article
from models.news import News
from models.ai_summary import AISummary
from services.weekly_report import _export_context,prepared_scope
from tests.test_launch_roundup import context,styled_generated

class DesktopWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        Path('build').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        for name in ('config','cache','output','database','prompts'):(self.root/name).mkdir()
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for module,key,value in ((resources,'_WORKSPACE_PATH',self.root),(settings_module,'CONFIG_PATH',self.root/'config.json'),
          (feed_manager,'FEEDS_PATH',self.root/'feeds.json'),(prompt_manager,'PROMPTS_DIR',self.root/'prompts'),
          (config,'OUTPUT_DIR',self.root/'output'),(config,'DATABASE_PATH',self.root/'database/test.db'),
          (config,'DEEPSEEK_API_KEY',''),(config,'WEB_SEARCH_API_KEY','')):
            self.stack.enter_context(patch.object(module,key,value))
        self.settings=replace(default_settings(),output_dir=str(self.root/'output'),sqlite_path=str(self.root/'database/test.db'),
          deepseek_api_key='',deepseek_base_url='',web_search_api_key='',web_search_enabled=False,aerospace_translation_enabled=False,
          date_mode='custom',start_date='2026-10-20',end_date='2026-10-29',launch_range='disabled',theme_mode='light',export_sqlite=False)
        save_settings(self.settings);(self.root/'feeds.json').write_text('[]',encoding='utf-8')
        self.stack.enter_context(patch('services.weekly_report.setup_logging'))
        self.w=MainWindow();self.p=self.w.production;self.p.timer.stop();self.p.downloads._restore_pending=False
        self.addCleanup(self.cleanup)
        collection=dict(items=[context(1),context(2),context(3)],complete=False,start='2026-10-20',end='2026-10-29')
        self.p.roundup.draft=r.create_draft(collection,'2026-10-20','2026-10-29')
        self.p.roundup.draft['segments']=r.validate_generated(styled_generated(),self.p.roundup.draft)
        self.p.roundup._show_draft()

    def cleanup(self):
        self.p.timer.stop();self.p.intro.refresh.stop()
        if self.p.busy():
            self.p.cancel_all();self.wait_idle()
        self.p.downloads._restore_pending=False
        self.w._closing=True;self.w.close();self.app.processEvents()

    def wait_idle(self,limit=6):
        deadline=time.monotonic()+limit
        while self.p.busy() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.app.processEvents();self.assertFalse(self.p.busy())

    def bundle(self):
        article=Article(News('Fictional test source','Fixture news','2026-10-23T08:00:00Z','Original source summary','https://example.invalid/news'),'Fixed source body','fixture')
        return dict(articles=[article],source_articles=[article],launch_articles=[],launch_video_items=[],
          summaries={article.news.url:AISummary('Prepared fixture summary',[],[])},assessments={},context=_export_context(self.settings,{}),source_scope=prepared_scope(self.settings))

    def test_four_pages_embed_widgets_without_modal_recreation_or_tts_load(self):
        self.assertEqual(self.p.navigation.count(),4)
        self.assertIsInstance(self.p.roundup,QWidget);self.assertNotIsInstance(self.p.roundup,QDialog)
        instances=[self.p.roundup,self.p.cards,self.p.intro,self.p.downloads]
        provider=self.p.roundup.tts_provider
        for _ in range(2):
            for page in range(4):self.p.navigation.setCurrentRow(page);self.app.processEvents()
        self.assertEqual(instances,[self.p.roundup,self.p.cards,self.p.intro,self.p.downloads])
        self.assertIs(provider,self.p.roundup.tts_provider);self.assertIsNone(provider.process)
        from ui.component import ProductionComponent
        class DeferredClose(ProductionComponent):
            def reject(self):QTimer.singleShot(20,super().reject)
        standalone=DeferredClose()
        QTimer.singleShot(20,lambda:standalone.window().close())
        self.assertEqual(standalone.exec(),QDialog.DialogCode.Rejected)
        standalone.deleteLater()

    def test_main_window_dates_settings_and_start_validation(self):
        panel=self.p.dashboard
        panel.start_date_edit.setDate(QDate(2026,10,8));panel.end_date_edit.setDate(QDate(2026,10,1))
        with patch('ui.production_workspace.save_settings') as save:
            with self.assertRaises(ValueError):self.p.start_news_collect()
            save.assert_not_called();self.assertIsNone(self.p.worker)
        self.w._open_settings();self.assertEqual(self.p.pages.currentIndex(),4)
        self.assertEqual(panel.start_date_edit.date(),QDate(2026,10,8))
        panel.end_date_edit.setDate(QDate(2026,10,10));panel.launch_range_combo.setCurrentIndex(panel.launch_range_combo.findData('custom'))
        panel.launch_start_date_edit.setDate(QDate(2026,10,2));panel.launch_end_date_edit.setDate(QDate(2026,10,9))
        with patch('feeds.launches.get_launches',return_value=[]) as launches,patch('feeds.launches.last_error',return_value=''):
            self.p.start_diagnostic('launch');self.wait_idle()
        self.assertEqual(launches.call_args.kwargs['start_date'],'2026-10-02')
        self.assertEqual(launches.call_args.kwargs['end_date'],'2026-10-09')
        with patch('ui.production_workspace.prepare_news',return_value=self.bundle()),patch('ui.production_workspace.save_settings') as save:
            self.p.start_news_collect();self.wait_idle()
        saved=save.call_args.args[0]
        self.assertEqual((saved.start_date,saved.end_date),('2026-10-08','2026-10-10'))
        self.assertEqual(saved.launch_range,'custom')

    def test_manual_edits_selection_and_scroll_survive_navigation_and_recovery(self):
        self.p.roundup.segment_list.setCurrentRow(1);self.p.roundup.spoken.setPlainText('人工口播，不应因页面切换丢失。')
        self.p.roundup.tasks.item(2,1).setCheckState(Qt.CheckState.Checked)
        self.p.news.adopt(self.bundle());self.p.news.summary.setPlainText('人工新闻摘要')
        self.p.content.setCurrentIndex(1)
        for page in (2,3,0,1):self.p.navigation.setCurrentRow(page)
        self.assertEqual(self.p.roundup.draft['segments'][1]['spoken'],'人工口播，不应因页面切换丢失。')
        self.assertEqual(self.p.news.bundle['summaries']['https://example.invalid/news'].summary,'人工新闻摘要')
        self.p.save();saved=self.p.snapshot()
        self.p.roundup.spoken.setPlainText('未保存的后续编辑');self.p._poll()
        self.p._restoring=True;self.p._restore();self.p._restoring=False
        self.assertEqual(self.p.roundup.draft['segments'][1]['spoken'],'未保存的后续编辑')
        self.assertNotEqual(r.digest(self.p.content_state()),self.p._saved_hash)
        self.assertEqual(saved['daily']['start_date'],'2026-10-20')
        self.assertEqual(self.p.roundup.draft['start'],'2026-10-20')

    def test_background_generation_allows_navigation_sources_and_other_content_edits(self):
        self.p.news.adopt(self.bundle())
        before=deepcopy(self.p.roundup.draft['segments'])
        def slow(data,token):
            for _ in range(100):token.check();time.sleep(.005)
            return r.local_script(data,'fixture template')
        self.p.roundup._start_job('script',slow,deepcopy(self.p.roundup.draft))
        self.p.navigation.setCurrentRow(0);self.p.roundup.tasks.setCurrentCell(1,2)
        self.p.news.summary.setPlainText('另一内容类型的人工编辑仍可继续')
        self.assertTrue(self.p.pages.isEnabled());self.assertTrue(self.p.roundup.spoken.isReadOnly())
        self.p.cancel_all();self.wait_idle()
        self.assertEqual(self.p.roundup.draft['segments'],before)
        self.assertIn('取消',self.p.roundup.status.text())
        self.assertEqual(self.p.news.bundle['summaries']['https://example.invalid/news'].summary,'另一内容类型的人工编辑仍可继续')

    def test_script_only_export_without_tts_is_real_and_does_not_create_fake_audio(self):
        self.p.roundup._export(False);self.wait_idle();self.p._poll()
        directory=Path(self.p.roundup.last_export_path)
        self.assertTrue((directory/'reading.txt').is_file())
        self.assertTrue((directory/'draft.json').is_file())
        self.assertFalse(list(directory.glob('*.wav')));self.assertFalse(list(directory.glob('*.srt')))
        self.assertIsNone(self.p.roundup.tts_provider.process)
        self.assertIn(str(directory),self.p.results)

    def test_close_waits_for_idle_resident_model_unload_acknowledgement(self):
        self.p.save()
        provider=Mock(is_running=True)
        def unload():time.sleep(.03);provider.is_running=False
        provider.unload.side_effect=unload;self.p.roundup.tts_provider=provider
        closed=[];self.p.close_ready.connect(lambda:closed.append(True))
        self.p.request_leave('close');self.assertFalse(closed)
        self.wait_idle();self.p._poll()
        provider.unload.assert_called_once();self.assertEqual(closed,[True])

    def test_imported_launch_manual_confirmation_has_no_automatic_evidence_upgrade(self):
        self.p.roundup.tasks.setCurrentCell(0,2);self.p._launch_detail()
        self.p.chinese_name.setText('人工核实的任务名');self.p.manual_summary.setPlainText('人工填写的背景')
        self.p.confirm_launch()
        item=self.p.roundup.draft['snapshot']['items'][0];state=r.material_status(item)
        self.assertEqual(state['human_confirmation'],'current');self.assertFalse(state['independent_support'])
        self.assertEqual(r.confirmed_values(item['research'],item)['chinese_name'],'人工核实的任务名')

    def test_cards_and_downloads_use_current_launch_ids_without_nested_windows(self):
        self.p.content.setCurrentIndex(1);self.p.cards_from_script()
        self.assertEqual({i['id'] for i in self.p.cards.data['items']},{'ll2:launch-1','ll2:launch-2','ll2:launch-3'})
        self.p.cards._open_intro();self.assertEqual(self.p.media.currentIndex(),2)
        self.p.cards._open_download();self.assertEqual(self.p.media.currentIndex(),3)
        self.assertEqual(len(self.p.downloads.snapshot_items),3)
        self.assertIsNone(self.p.cards.intro_dialog);self.assertIsNone(self.p.cards.download_dialog)

    def test_corrupt_recovery_is_retained_without_partial_controller_adoption(self):
        self.p.save();before=deepcopy(self.p.roundup.draft)
        path=self.root/'cache/production-recovery.json';value=self.p.snapshot();value['roundup']['start']='invalid-date'
        path.write_text(json.dumps(value),encoding='utf-8');self.p._restore(path)
        self.assertEqual(self.p.roundup.draft,before);self.assertTrue(self.p._recovery_blocked)
        original=path.read_bytes();self.p._poll();self.assertEqual(path.read_bytes(),original)

    def test_session_never_contains_credentials_and_keeps_pending_inputs(self):
        self.p.preferences.api_key_edit.setText('SECRET_CANARY_NOT_REAL')
        self.p.roundup.start.setDate(self.p.roundup.start.date().addDays(-1))
        self.p.roundup.python.setText('D:/custom/python.exe')
        self.p.save()
        text=(self.root/'config/production-session.json').read_text(encoding='utf-8')
        self.assertNotIn('SECRET_CANARY_NOT_REAL',text)
        value=json.loads(text)
        self.assertNotEqual(value['roundup']['start'],value['roundup_controls']['start'])
        self.assertEqual(value['roundup_controls']['python'],'D:/custom/python.exe')

    def test_news_generation_preserves_unselected_raw_sources_for_later_use(self):
        bundle=self.bundle();other=Article(News('Fictional','Other','2026-10-23T09:00:00Z','Other summary','https://example.invalid/other'),'Other body','fixture')
        bundle['articles'].append(other);bundle['source_articles']=bundle['articles']
        self.p.news.adopt(bundle,collected=True)
        generated=self.bundle();self.p.news.adopt(generated)
        self.assertEqual(self.p.news.sources.count(),2);self.assertEqual(self.p.news.articles.count(),1)
        self.p.news.sources.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(len(self.p.news.selected_bundle(for_write=True)['articles']),2)

    def test_news_offline_export_and_dates_scope_are_preserved(self):
        bundle=self.bundle();token=CancellationToken()
        with patch('requests.get',side_effect=AssertionError('export must be offline')),patch('services.weekly_report._prepare_launch_research',side_effect=AssertionError('export must not research')):
            paths=service.export_news(bundle,self.settings,token)
        self.assertTrue(paths);self.assertTrue(Path(paths[0]).exists())
        with self.assertRaisesRegex(ValueError,'范围'):
            service.export_news(bundle,replace(self.settings,start_date='2026-10-19'),token)
        restored=service.restore_news(service.serialize_news(bundle))
        self.assertEqual(restored['source_scope'],bundle['source_scope'])
        self.assertIs(restored['articles'][0],restored['source_articles'][0])

    def test_draft_open_and_pending_recovery_validate_before_adoption(self):
        path=self.root/'draft.json';r.save_draft(self.p.roundup.draft,path)
        self.p.roundup.open_draft(path)
        self.p.roundup.draft['tts']['device']='cuda:1';self.p.roundup._show_draft()
        self.assertEqual(self.p.roundup.device.currentText(),'cuda:1')
        value=self.p.snapshot();value['roundup']['tts']['timeout']='broken'
        before=deepcopy(self.p.roundup.draft)
        path.write_text(json.dumps(value),encoding='utf-8');self.p._restore(path)
        self.assertEqual(self.p.roundup.draft,before);self.assertTrue(self.p._recovery_blocked)

    def test_workspace_switch_restores_each_workspace_without_content_copies(self):
        self.p.roundup.spoken.setPlainText('第一个工作区的人工编辑')
        self.p.save();second=self.root/'second-workspace'
        with patch('resources.workspace_pointer_path',return_value=self.root/'test-pointer.json'):
            self.w._switch_workspace(str(second));self.app.processEvents();self.p=self.w.production
            self.assertEqual(self.p.roundup.draft['segments'],[])
            self.p.save()
            self.w._switch_workspace(str(self.root));self.app.processEvents();self.p=self.w.production
        self.assertEqual(self.p.roundup.draft['segments'][0]['spoken'],'第一个工作区的人工编辑')

    def test_preview_resizes_and_invalidates_without_changing_the_shared_draft(self):
        self.p.content.setCurrentIndex(1);self.p.media_route(0);self.w.show();self.app.processEvents()
        self.p.preview_roundup();before=deepcopy(self.p.roundup.draft)
        self.w.resize(1000,720);self.app.processEvents()
        self.assertLessEqual(self.p.frame.pixmap().width(),self.p.frame.width())
        self.assertEqual(self.p.roundup.draft,before)
        self.p.roundup.segment_list.setCurrentRow(1);self.app.processEvents()
        self.assertIsNone(self.p.frame.image)

    def test_prepared_news_generation_does_not_recollect_and_preserves_original_pipeline(self):
        from services.weekly_report import generate_weekly
        bundle=self.bundle();result=[]
        with patch('services.weekly_report.get_news',side_effect=AssertionError('no recollection')),patch('services.weekly_report.verify_news_dates',side_effect=lambda rows,*args:(rows,0)):
            generate_weekly([],False,False,False,CancellationToken(),prepared=bundle,defer_exports=True,result_callback=result.append)
        self.assertEqual(result[0]['articles'][0].news.url,'https://example.invalid/news')
        self.assertFalse(list((self.root/'output').glob('*.md')))

    def test_small_window_theme_and_language_switch_keep_controller_and_text(self):
        self.p.content.setCurrentIndex(1);self.p.roundup.segment_list.setCurrentRow(1)
        self.p.roundup.spoken.setPlainText('保留中文口播，切换英文界面不能翻译内容。')
        controller=self.p.roundup;self.w.resize(1000,720);self.w.show();self.app.processEvents()
        self.assertEqual(self.w.width(),1000)
        self.p.preferences.language_combo.setCurrentIndex(self.p.preferences.language_combo.findData('en_US'))
        self.p.preferences.theme_mode_combo.setCurrentIndex(self.p.preferences.theme_mode_combo.findData('dark'))
        self.p.apply_preferences();self.app.processEvents()
        self.assertEqual(self.p.navigation.item(1).text(),'Script')
        self.assertIs(self.p.roundup,controller)
        self.assertIn('不能翻译内容',self.p.roundup.spoken.toPlainText())

if __name__=='__main__':unittest.main()
