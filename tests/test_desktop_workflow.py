"""Desktop workflow tests: fixed sources and mocked services; no real AI/TTS claims."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,time,unittest
from unittest.mock import patch,Mock
from contextlib import ExitStack
from PySide6.QtWidgets import QApplication,QMessageBox,QWidget,QDialog,QTableWidgetItem,QPlainTextEdit
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

    def test_media_dropdowns_use_shared_rounded_popup_and_about_contains_updates(self):
        from ui.widgets.combo_box import RoundedComboBox
        from PySide6.QtWidgets import QComboBox
        self.assertTrue(self.p.findChildren(QComboBox))
        for combo in self.p.findChildren(QComboBox):
            self.assertIsInstance(combo, RoundedComboBox, combo.objectName())
        self.p.show_about()
        self.assertEqual(self.p.pages.currentIndex(),4)
        self.assertEqual(self.p.preferences.nav.currentRow(),self.p.preferences.about_index)
        self.assertTrue(self.p.preferences.isAncestorOf(self.p.updates))
        self.assertTrue(self.p.updates.check.text())
        from services import updates
        with patch.object(updates,'load',return_value={'automatic':False}):
            self.p.updates.version.setText('可用版本：过期记录');self.p.updates.notes.setPlainText('过期说明');self.p.updates.refresh()
            self.assertNotIn('过期记录',self.p.updates.version.text());self.assertFalse(self.p.updates.notes.toPlainText())
        with patch.object(self.p,'request_leave') as leave,patch.object(self.p,'busy',return_value=False):
            self.p.updates.exit_action='uninstall';self.p.updates.coordinate_exit()
            leave.assert_called_once_with('uninstall')

    def enable_materials(self):
        self.p.video_workflow.setCurrentIndex(self.p.video_workflow.findData('materials'))
        self.p.apply_preferences()

    def test_launch_materials_mode_reuses_task_ids_and_stops_narrated_preview(self):
        self.p.content.setCurrentIndex(1)
        original=deepcopy(self.p.roundup.draft)
        self.assertEqual(self.p.media.tabText(1),'画面素材')
        self.assertFalse(self.p.media.isTabVisible(1))
        self.assertTrue(self.p.roundup.export_controls.isHidden())
        self.enable_materials()
        self.assertTrue(self.p.media.isTabVisible(1))
        self.assertFalse(self.p.roundup.export_controls.isHidden())
        self.assertIn('无声',self.p.cards_mode_note.text())
        self.p.media_route(0)
        with patch.object(self.p,'_release_preview') as stop:
            self.p.media.setCurrentIndex(1)
            stop.assert_called_once()
        with patch.object(self.p.cards,'_set_data') as receive:
            self.p.cards.data=None;self.p.cards_from_script()
            cards=receive.call_args.args[0]['items']
        included={s['id'] for s in original['selection'] if s['included']}
        self.assertEqual({i['id'] for i in cards},{'ll2:'+i for i in included})
        self.assertTrue(all(i['video_type']=='launch' and 'audio' not in i for i in cards))
        self.assertEqual(self.p.roundup.draft,original)
        self.assertIsNone(self.p.roundup.tts_provider.process)

    def test_roundup_video_export_uses_single_file_exporter(self):
        self.p.content.setCurrentIndex(1)
        dialog=self.p.roundup
        video=self.root/'output/SpaceWeekly_2026-11-02_001.mp4'
        video.touch()
        legacy=self.root/'output/SpaceWeekly_roundup_old'
        legacy.mkdir()
        self.p.add_result(legacy,self.p.export_scope())
        with patch('ui.launch_roundup_dialog.export_complete_video',return_value=video) as complete, \
                patch('ui.launch_roundup_dialog.export_package') as package:
            dialog._export(True);self.wait_idle()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[:2],([dialog.draft],str(self.root/'output')))
        package.assert_not_called()
        self.p._poll()
        self.assertEqual(self.p.output_files.count(),1)
        self.assertEqual(self.p.output_files.item(0).data(Qt.ItemDataRole.UserRole),str(video))
        self.p.history_toggle.setChecked(True)
        self.assertEqual(self.p.output_files.count(),1)
        self.assertEqual(self.p.output_files.item(0).data(Qt.ItemDataRole.UserRole),str(legacy))

    def test_voice_setup_background_navigation_cancel_and_existing_fields(self):
        panel=self.p.voice_setup;r=self.p.roundup
        self.assertEqual([panel.tabs.tabText(i) for i in range(3)],['自动配置','使用已有环境','检查与修复'])
        before=deepcopy(r.draft['segments'])
        def waiting(token,emit):
            emit(dict(status='configuring',message='测试后台取消'))
            while not token.wait(.01):pass
            token.check()
        panel.start('deploy',waiting)
        progress_status=panel.status.text();panel.on_progress(dict(activity=''))
        self.assertEqual(panel.status.text(),progress_status)
        self.assertTrue(self.p.busy());self.assertFalse(panel.browse.isEnabled())
        for page in range(4):self.p.navigation.setCurrentRow(page);self.app.processEvents()
        with self.assertRaises(ValueError):panel.start('deploy',waiting)
        panel.cancel_work();self.wait_idle()
        self.assertIn('已取消',panel.status.text());self.assertEqual(before,r.draft['segments'])
        self.assertIn('已取消',self.p.task_text.text())
        self.assertTrue(panel.browse.isEnabled());self.assertIsNotNone(r.python.parent())

    def test_voice_setup_adopts_only_verified_roles_and_preserves_current_manual_text(self):
        import sys
        panel=self.p.voice_setup;r=self.p.roundup
        r.segment_list.setCurrentRow(1);r.spoken.setPlainText('当前人工稿，配置环境后保留。')
        before=deepcopy(r.draft['segments']);old_align=deepcopy(r.draft.get('aligner',{}))
        model=self.root/'fixture-model';model.mkdir()
        (model/'config.json').write_text(json.dumps(dict(tts_model_type='custom_voice',tts_model_size='0b6',talker_config=dict(spk_id={'vivian':1,'serena':2}))))
        r.speaker.setCurrentText('Serena')
        voice=dict(python=sys.executable,model=str(model),speaker='vivian',style='',device='cpu',timeout=1200,
            ready=True,capabilities=dict(speakers=['vivian','serena'],style=False))
        panel.adopt(dict(validation=dict(tts=voice,align=dict(ready=False)),active=dict(version='test-fixture')))
        self.assertEqual(r.draft['segments'],before);self.assertEqual(r.draft['tts']['speaker'],'Serena')
        self.assertEqual(r.draft.get('aligner',{}),old_align);self.assertFalse(r.draft['voice_deployment']['align_ready'])
        self.assertIn('当前人工稿',r.spoken.toPlainText())

    def test_voice_setup_profile_change_does_not_reuse_previous_ready_label(self):
        panel=self.p.voice_setup;panel.status.setText('已可用')
        panel.device.setCurrentIndex(1-panel.device.currentIndex())
        self.assertIn('待检查',panel.status.text());self.assertNotIn('已可用',panel.status.text())
        self.assertFalse(panel.start_button.isEnabled())
        selected=panel.device.currentData()
        with patch.object(panel,'refresh_state') as refresh:
            panel.root_finished();refresh.assert_not_called()
        self.assertEqual(panel.device.currentData(),selected)

    def test_segmented_pages_bulk_selection_and_keyboard_keep_existing_state(self):
        from ui.component import SegmentedTabs
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QAbstractItemView
        tabs=self.p.materials
        self.assertIsInstance(tabs,SegmentedTabs)
        instances=[tabs.widget(i) for i in range(tabs.count())]
        self.p.roundup.spoken.setPlainText('分段导航切换仍保留人工稿')
        self.w.show();tabs.setCurrentIndex(0);tabs.tabBar().setFocus();self.app.processEvents()
        QTest.keyClick(tabs.tabBar(),Qt.Key.Key_Right);self.app.processEvents()
        self.assertEqual(tabs.currentIndex(),1)
        self.p.roundup.include_all.setChecked(False)
        self.assertFalse(any(s['included'] for s in self.p.roundup.draft['selection']))
        self.p.roundup.include_all.setChecked(True)
        self.assertTrue(all(s['included'] for s in self.p.roundup.draft['selection']))
        self.assertFalse(self.p.roundup.tasks.showGrid())
        self.assertTrue(self.p.roundup.tasks.verticalHeader().isHidden())
        self.assertEqual(self.p.roundup.tasks.selectionBehavior(),QAbstractItemView.SelectionBehavior.SelectRows)
        self.assertEqual(instances,[tabs.widget(i) for i in range(tabs.count())])
        self.assertIn('保留人工稿',self.p.roundup.spoken.toPlainText())

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
        from PySide6.QtWidgets import QPushButton
        self.enable_materials()
        self.p.content.setCurrentIndex(1);self.p.media_route(1)
        self.assertEqual({i['id'] for i in self.p.cards.data['items']},{'ll2:launch-1','ll2:launch-2','ll2:launch-3'})
        self.p.media.setCurrentIndex(2);self.assertIn('片头',self.p.media.tabText(2))
        self.p.media.setCurrentIndex(3);self.assertIn('视频下载',self.p.media.tabText(3))
        self.assertEqual(len(self.p.downloads.snapshot_items),3)
        self.assertFalse(any(b.text() in ('制作片头','视频下载') for b in self.p.cards.findChildren(QPushButton)))

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

    def test_source_dates_survive_an_older_edition_and_reload(self):
        p=self.p
        p.save()
        dashboard=p.dashboard
        dashboard.start_date_edit.setDate(QDate(2026,10,22))
        dashboard.end_date_edit.setDate(QDate(2026,10,31))
        dashboard.launch_range_combo.setCurrentIndex(dashboard.launch_range_combo.findData('past_week'))
        self.assertEqual(json.loads((self.root/'config/source-date-ranges.json').read_text(encoding='utf-8'))['start_date'],'2026-10-22')
        reopened=MainWindow()
        try:
            settings=reopened.production.current_settings()
            self.assertEqual((settings.date_mode,settings.start_date,settings.end_date),('custom','2026-10-22','2026-10-31'))
            self.assertEqual(settings.launch_range,'past_week')
        finally:
            reopened.production.timer.stop();reopened.production.intro.refresh.stop()
            reopened._closing=True;reopened.close()

    def test_weekly_write_generates_missing_narration_and_retains_drafts(self):
        from models.news import news_id
        from services import news_voice,weekly_report
        p=self.p;bundle=self.bundle();first=bundle['articles'][0]
        other=Article(News('Fixture','Other news','2026-10-24','Second summary','https://example.invalid/other'),'Other body','fixture')
        failed=Article(News('Fixture','Failed news','2026-10-25','Third summary','https://example.invalid/failed'),'Third body','fixture')
        bundle['articles'].extend([other,failed]);bundle['source_articles']=list(bundle['articles'])
        p.news.adopt(bundle,collected=True);p.news.oral.setPlainText('人工保留的口播。')
        p.news.articles.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(p.news.generate.text(),'生成新闻稿（3 条）')
        self.assertEqual(len(p.news.script_included),2)
        settings=replace(self.settings,deepseek_api_key='fixture',deepseek_base_url='https://example.invalid',deepseek_model='fixture',news_filter_level='All')
        p.settings=settings
        proposal=dict(spoken='自动生成的第二条口播。',quotes=[other.body])
        with patch.object(weekly_report,'_resolve_runtime_model',side_effect=lambda settings,token:settings), \
             patch.object(weekly_report,'_screen_articles',side_effect=lambda articles,*args:(articles,0)), \
             patch.object(weekly_report,'_generate_selected_content',return_value=0), \
             patch.object(weekly_report,'_localize_selected_titles',return_value=({},0)), \
             patch('services.news_images.collect'), \
             patch.object(news_voice,'propose',side_effect=[proposal,ValueError('本条校验失败')]) as propose:
            p.news.generate.click();self.wait_idle()
        self.assertEqual([call.args[0].news.url for call in propose.call_args_list],[other.news.url,failed.news.url])
        self.assertEqual(p.news.voices[news_id(first.news.url)]['segments'][0]['spoken'],'人工保留的口播。')
        second=p.news.voices[news_id(other.news.url)]
        self.assertEqual(second['segments'][0]['spoken'],proposal['spoken'])
        self.assertEqual(second['segments'][0]['source_refs'][0]['quote'],other.body)
        self.assertNotIn('approved_text',second)
        self.assertEqual(p.news.batch_hint.text(),'已生成1条，已有1条，失败1条')
        self.assertTrue(p.inspector.isHidden())
        self.assertEqual(p.news.failed_articles(),[failed])
        self.assertEqual(p.news.retry.text(),'重试失败项（1 条）')
        self.assertNotIn(other.news.url,p.news.script_included)
        with patch.object(news_voice,'propose',return_value=dict(spoken='重试后已生成。',quotes=[failed.body])) as retry:
            p.news.retry.click();self.wait_idle()
        retry.assert_called_once()
        self.assertEqual(retry.call_args.args[0].news.url,failed.news.url)
        self.assertFalse(p.news.failed_articles())
        self.assertEqual(p.news.voices[news_id(other.news.url)],second)

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

    def test_news_write_reuses_legacy_rolling_sources_after_time_passes(self):
        from datetime import datetime,timezone,timedelta
        from feeds import launches
        from services import weekly_report
        settings=replace(self.settings,launch_range='past_week')
        self.p.dashboard.apply_settings(settings)
        with patch.object(launches,'datetime',wraps=datetime) as clock, \
             patch.object(weekly_report,'get_news',side_effect=AssertionError('Write must reuse sources')) as fetch, \
             patch('services.news_images.collect'):
            now=datetime(2026,11,3,15,13,55,tzinfo=timezone.utc);clock.now.return_value=now
            bundle=self.bundle();scope=prepared_scope(settings);scope.pop('launch_range')
            scope['launches']={key:scope['launches'].get(key) for key in ('net__gte','net__lt')}
            bundle.update(source_scope=scope,context=None,summaries={})
            self.p.news.adopt(bundle,collected=True)
            clock.now.return_value=now+timedelta(minutes=10)
            self.p.start_news_write();self.wait_idle()
        self.assertIsNotNone(self.p.news.bundle['context'])
        self.assertEqual(len(self.p.news.bundle['assessments']),1)
        self.assertEqual(self.p.news.bundle['source_scope'],scope)
        fetch.assert_not_called()

    def test_news_scripts_default_checked_and_order_reaches_cards_and_recovery(self):
        p=self.p;bundle=self.bundle()
        for index,day in ((2,21),(3,22)):
            bundle['articles'].append(Article(News('Fixture',f'News {index}',f'2026-10-{day}T08:00:00Z',
                f'Summary {index}',f'https://example.invalid/news/{index}'),f'Body {index}','fixture'))
        p.news.adopt(bundle,collected=True)
        sources=[a.news.url for a in p.news.bundle['source_articles']]
        self.assertEqual(len(p.news.script_included),3)
        self.assertTrue(all(p.news.articles.item(i).checkState()==Qt.CheckState.Checked for i in range(3)))
        p.news.articles.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(len(p.news.included),3)
        p.news.articles.setCurrentRow(2);url=p.news.current_article().news.url
        self.assertEqual(p.news.proposal.text(),'生成本条口播')
        p.news.oral.setPlainText('人工口播仍属于第三条新闻。')
        self.assertEqual(p.news.proposal.text(),'重写本条口播')
        p.news.editor_tabs.setCurrentIndex(1)
        p.news.title.setText('第三条的屏幕标题');p.news.summary.setPlainText('第三条的屏幕摘要')
        p.news.script_actions[1].click()
        self.assertFalse(p.news.script_included)
        self.assertEqual(p.news.editor_tabs.currentIndex(),1)
        self.assertEqual(p.news.current_article().news.url,url)
        self.assertEqual(p.news.summary.toPlainText(),'第三条的屏幕摘要')
        p.news.script_actions[0].click()
        self.assertEqual(len(p.news.script_included),3)
        p.news.articles.item(1).setCheckState(Qt.CheckState.Unchecked)
        checked=set(p.news.script_included)
        p.news.editor_tabs.setCurrentIndex(0)
        self.assertEqual(p.news.script_included,checked)
        p.news.articles.setCurrentRow(0);p.news.articles.setCurrentRow(2)
        self.assertEqual(p.news.current_title.text(),'第三条的屏幕标题')
        p.news.script_actions[2].click();p.news.script_actions[2].click()
        self.assertEqual(p.news.script_included,checked)
        self.assertEqual(p.news.current_article().news.url,url)
        self.assertEqual([a.news.url for a in p.news.bundle['source_articles']],sources)
        self.assertNotEqual([a.news.url for a in p.news.bundle['articles']],sources)
        self.assertEqual(p.news.oral.toPlainText(),'人工口播仍属于第三条新闻。')
        self.assertEqual(p.news.summary.toPlainText(),'第三条的屏幕摘要')
        ordered=[a.news.url for a in p.news.selected_bundle()['articles']]
        self.assertEqual(ordered,[url,'https://example.invalid/news'])
        self.assertTrue(p.news.articles.item(0).text().startswith('01  '))
        self.assertTrue(p.news.articles.item(1).text().startswith('02  '))
        self.enable_materials();p.media_route(1)
        self.assertEqual(p.cards.sort_order.currentData(),'input')
        self.assertEqual([item['url'] for item in p.cards.data['items']],ordered)
        p.cards.sort_order.setCurrentIndex(p.cards.sort_order.findData('desc'))
        self.assertNotEqual([item['url'] for item in p.cards.data['items']],ordered)
        p.cards.sort_order.setCurrentIndex(p.cards.sort_order.findData('input'))
        self.assertEqual([item['url'] for item in p.cards.data['items']],ordered)
        p.save();p.news._check_scripts(False);p._restore(self.root/'config/production-session.json')
        self.assertEqual([a.news.url for a in p.news.selected_bundle()['articles']],ordered)
        self.assertEqual([item['url'] for item in p.cards.data['items']],ordered)
        self.assertEqual(p.cards.sort_order.currentData(),'input')
        legacy=p.news.snapshot();legacy.pop('script_included');p.news.restore(legacy)
        self.assertEqual(len(p.news.script_included),3)

    def test_news_image_opt_out_reaches_preview_without_changing_audio_key(self):
        from PIL import Image
        from services import news_images
        from services.roundup_audio import audio_key
        p=self.p;p.news.adopt(self.bundle())
        source=self.root/'自选 配图.png';Image.new('RGB',(96,64),'blue').save(source)
        news_images.acquire(p.news.current_article(),CancellationToken(),local=source)
        p.news.oral.setPlainText('人工新闻口播。');p.news.refresh_image()
        draft=p.news.voice_draft();key=audio_key(draft,draft['segments'][0])
        self.assertIsNotNone(draft['screen_item']['image'])
        p.news.image_use.setChecked(False)
        draft=p.news.voice_draft();self.assertIsNone(draft['screen_item']['image'])
        self.assertEqual(audio_key(draft,draft['segments'][0]),key)
        self.assertTrue(source.is_file())
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertFalse(p.news.image_use.isChecked())
        self.assertIsNone(p.news.voice_draft()['screen_item']['image'])
        p.news.image_use.setChecked(True)
        self.assertIsNotNone(p.news.voice_draft()['screen_item']['image'])

    def test_current_cards_follow_new_screen_text_and_keep_local_overrides(self):
        p=self.p;p.news.adopt(self.bundle())
        self.enable_materials()
        p.media_route(1)
        self.assertIsNone(p.cards.source)
        self.assertFalse(list(Path(p.settings.output_dir).glob('*.premiere.json')))
        p.navigation.setCurrentRow(1);p.news.title.setText('修改稿件屏幕标题')
        p.media_route(1)
        self.assertEqual(p.cards.data['items'][0]['title'],'修改稿件屏幕标题')
        p.cards.summary_edit.setPlainText('画面专用摘要')
        p.navigation.setCurrentRow(1);p.news.title.setText('第二次修改稿件屏幕标题')
        p.media_route(1)
        self.assertEqual(p.cards.data['items'][0]['title'],'第二次修改稿件屏幕标题')
        self.assertEqual(p.cards.data['items'][0]['summary'],'画面专用摘要')

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
        from tools.roundup_visuals import DEFAULT_CARD_GAP
        self.assertEqual(self.p._preview_frames['card_gap'],DEFAULT_CARD_GAP)
        self.w.resize(1000,720);self.app.processEvents()
        self.assertLessEqual(self.p.frame.pixmap().width(),self.p.frame.width())
        self.assertEqual(self.p.roundup.draft,before)
        self.p.roundup.segment_list.setCurrentRow(1);self.app.processEvents()
        self.assertIsNotNone(self.p.frame.image)
        self.assertEqual(self.p._frame_key,self.p._preview_key())

    def test_shot_settings_undo_cancel_apply_and_restore_do_not_invalidate_voice(self):
        from ui.roundup_shots_dialog import RoundupShotsDialog
        from services.roundup_audio import audio_key
        from services.roundup_sync import build_timeline
        p=self.p;draft=p.roundup.draft
        before=deepcopy(draft);keys=[audio_key(draft,s) for s in draft['segments']];clock=build_timeline(draft)
        dialog=RoundupShotsDialog(p);self.addCleanup(dialog.close)
        self.assertFalse(dialog.enabled.isChecked())
        dialog.enabled.setChecked(True);dialog.undo_edit();self.assertFalse(dialog.enabled.isChecked())
        dialog.enabled.setChecked(True);dialog.reject();self.assertEqual(draft,before)
        dialog=RoundupShotsDialog(p);self.addCleanup(dialog.close)
        dialog.enabled.setChecked(True);dialog.reset();self.assertFalse(dialog.enabled.isChecked())
        dialog.undo_edit();self.assertTrue(dialog.enabled.isChecked());dialog.apply()
        self.assertTrue(draft['visuals']['group_cards'])
        self.assertEqual([audio_key(draft,s) for s in draft['segments']],keys)
        self.assertEqual(build_timeline(draft),clock)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertEqual(p.roundup.draft['visuals'],draft['visuals'])

    def test_voice_rule_table_and_editable_reading_use_existing_saved_fields(self):
        self.p.content.setCurrentIndex(1)
        self.p.roundup.segment_list.setCurrentRow(1);self.p.media_route(0)
        self.p.voice_text.setPlainText('词典名称按读法朗读')
        self.assertEqual(self.p.roundup.draft['segments'][1]['narration']['text'],'词典名称按读法朗读')
        table=self.p.pronunciation_table;row=table.rowCount();table.insertRow(row)
        table.setItem(row,0,QTableWidgetItem('读法'));table.setItem(row,1,QTableWidgetItem('读音'))
        self.assertEqual(json.loads(self.p.roundup.pronunciations.toPlainText())['读法'],'读音')
        self.assertIn('读音',self.p.roundup.reading_preview.toPlainText())
        self.assertTrue(self.p.roundup._voice_dirty)

    def test_news_voice_is_separate_from_summary_and_keeps_background_result_by_id(self):
        import sys,wave,threading
        from models.news import news_id
        from services.roundup_audio import current_audio
        from ui.roundup_sync_dialog import RoundupSyncDialog
        p=self.p;bundle=self.bundle();first=bundle['articles'][0]
        other=Article(News('Fixture','Other news','2026-10-24','Other screen summary','https://example.invalid/other'),'Other body','fixture')
        bundle['articles'].append(other);p.news.adopt(bundle)
        model=self.root/'模型 路径';model.mkdir();p.roundup.model.setText(str(model));p.roundup.python.setText(sys.executable)
        p.roundup.align_model.clear();p.news.oral.setPlainText('新闻口播第一句。新闻口播第二句。')
        self.assertEqual(p.news.summary.toPlainText(),'Prepared fixture summary')
        self.assertEqual(p.voice_text.toPlainText(),p.news.oral.toPlainText())
        started=threading.Event();release=threading.Event()
        def synthesize(request,path,cancel_check):
            started.set()
            while not release.wait(.01):cancel_check()
            with wave.open(str(path),'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*96000)
        provider=p.roundup.tts_provider
        with patch.object(provider,'configure'),patch.object(provider,'synthesize',side_effect=synthesize),patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):
            p.news_audio();self.assertTrue(started.wait(2));p.news.articles.setCurrentRow(1);self.app.processEvents()
            release.set();self.wait_idle()
        identifier=news_id(first.news.url);draft=p.news.voices[identifier]
        self.assertIsNotNone(current_audio(draft,draft['segments'][0]))
        self.assertEqual(p.news.articles.currentRow(),1);self.assertIsNone(p.news.voice_draft()['segments'][0]['audio'])
        p.news.articles.setCurrentRow(0);p.news.summary.setPlainText('只修改屏幕摘要')
        self.assertIsNotNone(current_audio(p.news.voice_draft(),draft['segments'][0]))
        dialog=RoundupSyncDialog(p);self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.rowCount(),2);self.assertEqual(dialog.table.item(0,2).text(),'新闻内容')
        p.save();p._restore(self.root/'config/production-session.json')
        restored=p.news.voices[identifier];self.assertIsNotNone(current_audio(restored,restored['segments'][0]))
        self.w.show();p.navigation.setCurrentRow(0);self.app.processEvents()
        p.navigation.setCurrentRow(2);self.app.processEvents()
        self.assertAlmostEqual(p._preview_timeline['duration_seconds'],4)
        p._audio_playback=True;p.news.oral.insertPlainText('已修改。')
        self.assertFalse(p._audio_playback);self.assertIn('过期',p.play_state.text())

    def test_news_local_image_job_and_cache_preferences_survive_session_restore(self):
        from PIL import Image
        from models.news import news_id
        from services import news_images,cache_maintenance
        p=self.p;p.maintenance.timer.stop();p.news.adopt(self.bundle())
        article=p.news.current_article();source=self.root/'本地 配图.png';Image.new('RGB',(96,64),'blue').save(source)
        p.start_news_image(article,dict(local=str(source)));self.wait_idle()
        self.assertIsNotNone(news_images.selected(news_id(article.news.url)))
        p.maintenance.enabled.setChecked(False);p.maintenance.interval.setValue(5);p.maintenance.retention.setValue(7)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertIsNotNone(news_images.selected(news_id(article.news.url)))
        settings=cache_maintenance.load();self.assertFalse(settings['enabled']);self.assertEqual((settings['interval_days'],settings['retention_days']),(5,7))
        (self.root/'news-images/manifest.json').write_text('{broken',encoding='utf-8')
        self.assertIsNotNone(p.news.voice_draft())
        from ui.cache_maintenance_panel import CacheMaintenancePanel
        (self.root/'config/cache-maintenance.json').write_text('{broken',encoding='utf-8')
        panel=CacheMaintenancePanel(p);panel.timer.stop();self.addCleanup(panel.deleteLater)
        self.assertIn('原文件保留',panel.status.text())

    def test_visual_playback_advances_existing_segments_and_editing_pauses(self):
        self.p.content.setCurrentIndex(1);self.p.media_route(0);self.w.show();self.app.processEvents()
        self.p.roundup.segment_list.setCurrentRow(0);before=deepcopy(self.p.roundup.draft)
        self.p._toggle_preview();self.assertTrue(self.p.preview_timer.isActive())
        duration=self.p._preview_timing['duration_seconds']+self.p.roundup.draft['pause_seconds']
        self.p._preview_started=time.monotonic()-duration-.1
        self.p._advance_preview()
        self.assertEqual(self.p.roundup.segment_list.currentRow(),1)
        self.assertEqual(self.p.roundup.draft,before)
        self.p.voice_text.insertPlainText('人工朗读编辑')
        self.assertFalse(self.p.preview_timer.isActive())
        self.assertIn('人工朗读编辑',self.p.voice_text.toPlainText())

    def test_audio_preview_clock_uses_player_position_and_seek_reconstructs_state(self):
        from services.roundup_sync import build_timeline
        p=self.p;p.content.setCurrentIndex(1);p.media_route(0);self.w.show();self.app.processEvents()
        p.roundup.segment_list.setCurrentRow(0);p.preview_roundup()
        table=build_timeline(p.roundup.draft);target=table['segments'][1]['start_seconds']+1.3
        player=Mock();player.position.return_value=round(target*1000)
        p.preview_player=player;p._audio_playback=True;p._audio_ready=True;p._audio_offset=0
        p._audio_end=table['duration_seconds'];p._preview_started=-999999
        p._advance_preview()
        self.assertAlmostEqual(p.preview_slider.value()/1000,target,places=2)
        self.assertEqual(p.roundup.segment_list.currentRow(),1)
        self.assertIn('播放器时间',p.preview_clock.text())
        p.preview_slider.setValue(round((target+1)*1000))
        player.setPosition.assert_called_with(round((target+1)*1000))
        expected=p.frame.image.toImage()
        p.preview_slider.setValue(200);p.preview_slider.setValue(round((target+1)*1000))
        self.assertEqual(p.frame.image.toImage(),expected)
        p._stop_preview();player.pause.assert_called()

    def test_audio_loading_pause_and_end_do_not_restart_or_advance_an_extra_clock(self):
        from PySide6.QtMultimedia import QMediaPlayer
        p=self.p;p.content.setCurrentIndex(1);p.media_route(0);self.w.show();self.app.processEvents();p.preview_roundup()
        player=Mock();p.preview_player=player;p._audio_offset=0;p._audio_end=p._preview_timeline['duration_seconds']
        p._audio_playback=True;p._audio_pending_position=1300;p._audio_ready=False
        p._stop_preview();p._audio_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
        player.setPosition.assert_called_with(1300);player.play.assert_not_called()
        p._audio_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(p.preview_slider.value(),p.preview_slider.maximum());self.assertFalse(p.preview_timer.isActive())

    def test_selecting_fractional_audio_boundary_does_not_jump_to_previous_paragraph(self):
        self.p.content.setCurrentIndex(1)
        import wave
        from services.roundup_audio import audio_key,file_hash
        p=self.p;first=p.roundup.draft['segments'][0];path=self.root/'fractional-boundary.wav'
        with wave.open(str(path),'wb') as handle:
            handle.setparams((1,2,24000,0,'NONE','not compressed'));handle.writeframes(b'\0\0'*2500)
        first['audio']=dict(key=audio_key(p.roundup.draft,first),path=str(path),sha256=file_hash(path),sample_rate=24000,sample_count=2500)
        p.roundup.segment_list.setCurrentRow(1);p.preview_roundup()
        self.assertEqual(p.roundup.segment_list.currentRow(),1)
        start=p._preview_timeline['segments'][1]['start_seconds']
        self.assertGreaterEqual(p.preview_slider.value()/1000,start)
        self.assertLess(p.preview_slider.value()/1000-start,.001)

    def test_sync_calibration_uses_existing_draft_and_survives_save_restore(self):
        self.p.content.setCurrentIndex(1)
        from ui.roundup_sync_dialog import RoundupSyncDialog
        from services.roundup_sync import set_units,save_manual,segment_schedule
        from services.roundup_audio import audio_key,file_hash
        import wave
        p=self.p;p.roundup.segment_list.setCurrentRow(1);segment=p.roundup.draft['segments'][1]
        set_units(segment,[dict(text=r.narration_text(segment),task_ids=segment['task_ids'],display='task')])
        audio=self.root/'unit-fixture.wav'
        with wave.open(str(audio),'wb') as handle:
            handle.setparams((1,2,24000,0,'NONE','not compressed'));handle.writeframes(b'\0\0'*48000)
        segment['audio']=dict(key=audio_key(p.roundup.draft,segment),path=str(audio),sha256=file_hash(audio),
            duration_seconds=2,sample_rate=24000,reading_text=r.narration_text(segment))
        units=segment['units'];duration=2/len(units)
        save_manual(p.roundup.draft,segment,[(n*duration,(n+1)*duration) for n in range(len(units))])
        dialog=RoundupSyncDialog(p)
        self.assertEqual(dialog.table.rowCount(),len(units));self.assertIn('人工校准',dialog.info.text());dialog.close()
        p.save();p._restore(self.root/'config/production-session.json')
        restored=next(s for s in p.roundup.draft['segments'] if s['id']==segment['id'])
        self.assertEqual(segment_schedule(p.roundup.draft,restored)['status'],'manual')
        self.assertEqual(restored['units'],units)

    def test_real_qt_player_transport_with_muted_pcm_fixture_not_model_acceptance(self):
        import wave
        from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
        from services.roundup_audio import audio_key,file_hash,reading_text
        p=self.p;p.content.setCurrentIndex(1);p.media_route(0)
        segment=deepcopy(p.roundup.draft['segments'][1]);p.roundup.draft['segments']=[segment];p.roundup._show_draft()
        audio=self.root/'transport-test-fixture.wav'
        with wave.open(str(audio),'wb') as handle:
            handle.setparams((1,2,24000,0,'NONE','not compressed'));handle.writeframes(b'\0\0'*48000)
        segment['audio']=dict(key=audio_key(p.roundup.draft,segment),path=str(audio),sha256=file_hash(audio),
            duration_seconds=2,sample_rate=24000,reading_text=reading_text(p.roundup.draft,segment))
        p.preview_player=QMediaPlayer(p);p.preview_audio_output=QAudioOutput(p);p.preview_audio_output.setVolume(0)
        p.preview_player.setAudioOutput(p.preview_audio_output);p.preview_player.mediaStatusChanged.connect(p._audio_media_status)
        p.preview_player.positionChanged.connect(p._audio_position_changed)
        p.preview_player.errorOccurred.connect(lambda *_:p._audio_preview_error())
        self.w.show();self.app.processEvents();p._toggle_preview();self.wait_idle()
        deadline=time.monotonic()+5
        while p.preview_player.position()<200 and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        if p.preview_player.error()!=QMediaPlayer.Error.NoError:
            self.skipTest('Qt media backend unavailable: '+p.preview_player.errorString())
        self.assertGreaterEqual(p.preview_player.position(),200)
        p._stop_preview();self.app.processEvents();before=p.preview_player.position();time.sleep(.08);self.app.processEvents()
        self.assertEqual(p.preview_player.position(),before)
        self.assertLessEqual(abs(p.preview_slider.value()-before),1)
        p._advance_preview();self.assertLessEqual(abs(p.preview_slider.value()-before),1)
        p.preview_slider.setValue(1100);self.app.processEvents()
        self.assertLessEqual(abs(p.preview_player.position()-1100),1)
        p._toggle_preview();self.wait_idle();deadline=time.monotonic()+4
        while p.preview_timer.isActive() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertFalse(p.preview_timer.isActive());self.assertEqual(p.preview_slider.value(),p.preview_slider.maximum())

    def test_date_changes_filter_locally_without_requesting_network(self):
        self.p.roundup.draft['segments']=[]
        with patch('ui.launch_roundup_dialog.refresh_collection',side_effect=AssertionError('filter must stay local')):
            self.p.roundup.start.setDate(QDate(2026,10,21));self.p.roundup.end.setDate(QDate(2026,10,25))
            self.p.roundup.range_timer.stop();self.p.roundup._apply_range()
        self.assertEqual((self.p.roundup.draft['start'],self.p.roundup.draft['end']),('2026-10-21','2026-10-25'))
        self.assertIsNone(self.p.roundup.worker)

    def test_launch_detail_shows_target_orbit_without_editing_manual_script(self):
        item=self.p.roundup.draft['snapshot']['items'][0];item['orbit']='Low Earth Orbit'
        segment=self.p.roundup.draft['segments'][1];segment.update(spoken='人工原稿保持不变。',edited=True)
        self.p.roundup.tasks.setCurrentCell(0,12)
        before=deepcopy(self.p.roundup.draft)
        with patch('services.launch_research.load_dictionary',return_value={'Low Earth Orbit':dict(translation='近地轨道',type='orbit')}):
            self.p._launch_detail()
        text=self.p.launch_details.toPlainText()
        self.assertIn('目标轨道',text);self.assertIn('近地轨道',text)
        self.assertEqual(self.p.roundup.draft,before)

    def test_narration_comparison_cancel_apply_and_restore_preserve_manual_original(self):
        from services.roundup_polish import organize_segments
        dialog=self.p.roundup;self.p.content.setCurrentIndex(1)
        dialog.segment_list.setCurrentRow(1)
        dialog.spoken.setPlainText('人工事实原稿，不应被自动覆盖。')
        dialog._sync();before=deepcopy(dialog.draft)
        result=organize_segments(dialog.draft,[dialog.draft['segments'][1]['id']])
        observed=[]
        def choose(accept):
            modal=QApplication.activeModalWidget()
            observed.append(modal.windowTitle())
            observed.extend(edit.toPlainText() for edit in modal.findChildren(QPlainTextEdit))
            modal.accept() if accept else modal.reject()
        QTimer.singleShot(0,lambda:choose(False))
        self.assertFalse(dialog._review_narration(result,'口播整理'))
        self.assertEqual(dialog.draft,before)
        QTimer.singleShot(0,lambda:choose(True))
        self.assertTrue(dialog._review_narration(result,'口播整理'))
        self.assertEqual(dialog.draft['segments'][1]['spoken'],before['segments'][1]['spoken'])
        self.assertNotEqual(r.narration_text(dialog.draft['segments'][1]),before['segments'][1]['spoken'])
        self.assertTrue(any('修改预览' in text for text in observed))
        self.assertTrue(any('二十一日' in text for text in observed))
        path=self.root/'saved-reading.json';r.save_draft(dialog.draft,path);loaded=r.load_draft(path)
        self.assertEqual(loaded['polish_history'],dialog.draft['polish_history'])
        self.assertEqual(loaded['segments'][1]['units'],dialog.draft['segments'][1]['units'])

    def test_paused_clock_refresh_ignores_late_backend_reset_and_stale_text(self):
        p=self.p;p.content.setCurrentIndex(1);p.media_route(0);self.w.show();self.app.processEvents()
        p._audio_playback=True;p._audio_ready=True;p.preview_timer.stop()
        p._timeline_key=r.digest([p.roundup.draft,p.roundup._voice_dirty])
        with patch.object(p,'_advance_preview') as refresh:
            p._audio_finished=False;p._audio_position_changed(1200);refresh.assert_called_once()
            refresh.reset_mock();p._audio_finished=True;p._audio_position_changed(0);refresh.assert_not_called()
            p._audio_finished=False;p.roundup.draft['pronunciations']['changed']='新读法'
            p._audio_position_changed(1200);refresh.assert_not_called()

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
