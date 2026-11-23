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

    def test_edition_edit_keeps_source_audio_and_persists_spoken_invalidation(self):
        from ui.launch_edit_dialog import LaunchEditDialog
        from services.roundup_audio import generate_audio,current_audio
        from tests.test_launch_roundup import write_pcm
        draft=self.p.roundup.draft;item=draft['snapshot']['items'][0]
        source=deepcopy(item);provider=Mock()
        provider.synthesize.side_effect=lambda req,path,cancel_check:write_pcm(path)
        generate_audio(draft,provider,CancellationToken())
        segment=next(s for s in draft['segments'] if item['ll2_id'] in s['task_ids'])
        audio=deepcopy(segment['audio'])
        editor=LaunchEditDialog(self.p,item)
        self.assertEqual(editor.fields['payload_count'].text(),'')
        editor.fields['payload'].setText('本期人工整理的载荷')
        editor.fields['payload_count'].setText('6');editor.save()
        self.assertEqual(item['source_original'],source)
        self.assertEqual(item['edition_fields']['payload_count'],6)
        self.assertEqual(current_audio(draft,segment),audio)
        editor=LaunchEditDialog(self.p,item)
        self.assertEqual(editor.fields['payload_count'].text(),'6')
        editor.segments[0][1].setPlainText('人工修改后的完整口播。');editor.save()
        self.assertIsNone(current_audio(draft,segment));self.assertEqual(segment['audio'],audio)
        path=self.root/'edition.json';r.save_draft(draft,path);restored=r.load_draft(path)
        saved=next(i for i in restored['snapshot']['items'] if i['ll2_id']==item['ll2_id'])
        self.assertEqual(saved['edition_fields'],item['edition_fields'])
        self.assertEqual(saved['source_original'],source)

    def test_materials_name_can_be_edited_in_table_and_inline_and_survives_restore(self):
        self.p.content.setCurrentIndex(1);self.p.navigation.setCurrentRow(0);self.p.materials.setCurrentIndex(1)
        self.p.roundup.tasks.setCurrentCell(0,2);item=self.p.roundup.draft['snapshot']['items'][0]
        original=deepcopy(item);paragraphs=deepcopy(self.p.roundup.draft['segments'])
        cell=self.p.roundup.tasks.item(0,2);self.assertTrue(cell.flags() & Qt.ItemFlag.ItemIsEditable)
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLineEdit
        self.w.show();self.app.processEvents();self.p.roundup.tasks.setFocus()
        QTest.keyClick(self.p.roundup.tasks,Qt.Key.Key_F2);self.app.processEvents()
        editor=self.p.roundup.tasks.findChild(QLineEdit);self.assertIsNotNone(editor)
        editor.setText('表格中修改的任务名称');QTest.keyClick(editor,Qt.Key.Key_Return);QTest.qWait(50)
        self.assertEqual(r.display_names(item)['name'],'表格中修改的任务名称')
        self.assertEqual(item['english_name'],original['english_name']);self.assertEqual(item['source_original'],original)
        self.assertEqual(self.p.roundup.draft['segments'],paragraphs)
        self.p.launch_name_edit.setText('右侧保存的任务名称');self.p.save_launch_name_button.click();self.app.processEvents()
        self.assertEqual(r.display_names(item)['name'],'右侧保存的任务名称')
        self.p.save();self.p._restore(self.root/'config/production-session.json')
        self.assertEqual(r.display_names(self.p.roundup.draft['snapshot']['items'][0])['name'],'右侧保存的任务名称')

    def test_materials_name_empty_input_is_rejected_and_running_job_locks_edits(self):
        self.p.roundup.tasks.setCurrentCell(0,2);item=self.p.roundup.draft['snapshot']['items'][0]
        name=r.display_names(item)['name'];self.p.launch_name_edit.setText('')
        self.p.save_launch_name_button.click();self.app.processEvents()
        self.assertEqual(r.display_names(item)['name'],name)
        before=deepcopy(self.p.roundup.draft['segments'])
        def slow(data,token):
            for _ in range(100):token.check();time.sleep(.005)
        self.p.roundup._start_job('translations',slow,deepcopy(self.p.roundup.draft))
        self.assertFalse(self.p.roundup.tasks.item(0,2).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(self.p.save_launch_name_button.isEnabled())
        self.p.cancel_all();self.wait_idle()
        self.assertEqual(self.p.roundup.draft['segments'],before)

    def test_materials_has_one_combined_completion_entry_and_keeps_refresh_secondary(self):
        from PySide6.QtWidgets import QPushButton
        from i18n import set_language
        self.assertIs(self.p.complete_launch_materials_button,self.p.roundup.refresh_button)
        self.assertEqual(self.p.complete_launch_materials_button.text(),'补全中文资料')
        menus=[b.menu() for b in self.p.roundup.data_page.findChildren(QPushButton) if b.menu()]
        labels=[a.text() for menu in menus for a in menu.actions()]
        self.assertIn('刷新发射列表（联网）',labels);self.assertIn('重新补全本任务',labels)
        self.assertNotIn('AI翻译缺失字段',labels);self.assertNotIn('重新翻译本任务',labels)
        with patch.object(self.p.roundup,'_supplement_materials') as complete:
            self.p.complete_launch_materials_button.click()
        complete.assert_called_once()
        set_language('en_US');self.p.retranslate()
        self.assertEqual(self.p.complete_launch_materials_button.text(),'Complete Chinese materials')
        self.assertEqual(self.p.save_launch_name_button.text(),'Save name')
        self.assertIn('Refresh launch list (online)',[a.text() for menu in menus for a in menu.actions()])
        set_language('zh_CN');self.p.retranslate()

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

    def test_idle_poll_reuses_news_digest_and_recovery_captures_new_edits(self):
        self.p.news.adopt(self.bundle())
        self.p._poll();self.p._finish_recovery_write(wait=True)
        recovery=self.root/'cache/production-recovery.json'
        with patch('ui.news_workspace.serialize_news',wraps=service.serialize_news) as serialize:
            self.p._poll();self.p._poll()
            serialize.assert_not_called()
        self.p.news.summary.setPlainText('刷新后应恢复的摘要')
        self.p._poll();self.p._finish_recovery_write(wait=True)
        news=json.loads(recovery.read_text(encoding='utf-8'))['news']['bundle']
        self.assertEqual(news['summaries']['https://example.invalid/news']['summary'],'刷新后应恢复的摘要')
        updated=deepcopy(self.p.news.current_article());updated.news.images=['https://example.invalid/story.jpg']
        self.p.news.set_article_images(updated)
        self.p._poll();self.p._finish_recovery_write(wait=True)
        news=json.loads(recovery.read_text(encoding='utf-8'))['news']['bundle']
        self.assertEqual(news['source_articles'][0]['news']['images'],updated.news.images)

    def test_roundup_paragraph_selection_defers_hidden_voice_refresh(self):
        p=self.p;p.content.setCurrentIndex(1);p.navigation.setCurrentRow(1)
        with patch.object(p,'_voice_refresh',wraps=p._voice_refresh) as refresh:
            p.roundup.segment_list.setCurrentRow(1)
            p._poll()
            refresh.assert_not_called()
            p.navigation.setCurrentRow(2)
            refresh.assert_called_once()
        self.assertEqual(p.voice_segments.currentData(),p.roundup.draft['segments'][1]['id'])

    def test_unsaved_exit_uses_the_selected_language_for_qt_buttons(self):
        from ui.localize import set_ui_language
        self.addCleanup(set_ui_language,'zh_CN')
        buttons=QMessageBox.StandardButton
        for language,expected in (('zh_CN',('保存','丢弃','取消')),('en_US',('Save','Discard','Cancel'))):
            set_ui_language(language);labels=[]
            def cancel_leave():
                dialog=self.app.activeModalWidget()
                labels.extend(dialog.button(button).text() for button in (buttons.Save,buttons.Discard,buttons.Cancel))
                dialog.button(buttons.Cancel).click()
            QTimer.singleShot(0,cancel_leave)
            self.assertFalse(self.p.request_leave('close'))
            for label,word in zip(labels,expected,strict=True):self.assertIn(word,label)

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

    def test_news_export_can_cancel_retry_and_reject_results_from_previous_edition(self):
        from threading import Event
        p=self.p;p.news.adopt(self.bundle(),collected=True)
        path=self.root/'fixture.md'
        entered=Event()
        def export(data,token):
            entered.set()
            while not token.is_cancelled:time.sleep(.005)
            token.check()
        p._news_job('export',export,{})
        self.assertTrue(entered.wait(3))
        self.assertTrue(p.cancel_tasks.isVisibleTo(p))
        for page in (0,1,2,3):p.navigation.setCurrentRow(page);self.app.processEvents()
        self.assertEqual(p.navigation.currentRow(),3)
        p.news.oral.setPlainText('导出期间可以保存下一版文稿。')
        with self.assertRaises(ValueError):p._news_job('export',export,{})
        with self.assertRaises(ValueError):p.open_edition()
        p.cancel_all();self.wait_idle()
        self.assertFalse(p.results)
        self.assertEqual(p.news.voice_draft()['segments'][0]['spoken'],'导出期间可以保存下一版文稿。')
        def retry(data,token):
            path.write_text('固定导出内容',encoding='utf-8')
            return [str(path)]
        p._news_job('export',retry,{});self.wait_idle()
        self.assertIn(str(path),p.results)
        p.save()
        obsolete=self.root/'obsolete.md'
        release=Event()
        p._news_job('export',lambda data,token:(release.wait(3),[str(obsolete)])[1],{})
        try:
            p._restoring=True
            p._restore(self.root/'config/production-session.json')
        finally:
            p._restoring=False;release.set()
        self.wait_idle()
        self.assertNotIn(str(obsolete),p.results)
        self.assertIn(str(path),p.results)

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

    def test_edition_editor_replaces_old_entry_points_without_confirming_background(self):
        from ui.launch_edit_dialog import LaunchEditDialog
        from services.launch_display import resolve_launch
        from PySide6.QtWidgets import QPushButton
        self.p.roundup.tasks.setCurrentCell(0,2);self.p._launch_detail()
        dialog=LaunchEditDialog(self.p,self.p.roundup.draft['snapshot']['items'][0]);self.addCleanup(dialog.close)
        dialog.fields['name'].setText('本期任务名');dialog.fields['summary'].setText('人工填写的背景');dialog.save()
        item=self.p.roundup.draft['snapshot']['items'][0];state=r.material_status(item)
        self.assertFalse(state['independent_support']);self.assertEqual(resolve_launch(item)['name'],'本期任务名')
        self.assertFalse(any(b.text() in ('编辑资料','修正名称') for b in self.p.findChildren(QPushButton)))

    def test_cards_and_downloads_use_current_launch_ids_without_nested_windows(self):
        from PySide6.QtWidgets import QPushButton
        self.enable_materials()
        self.p.content.setCurrentIndex(1);self.p.media_route(1)
        self.assertEqual({i['id'] for i in self.p.cards.data['items']},{'ll2:launch-1','ll2:launch-2','ll2:launch-3'})
        self.p.media.setCurrentIndex(2);self.assertIn('片头',self.p.media.tabText(2))
        self.p.media.setCurrentIndex(3);self.assertIn('视频下载',self.p.media.tabText(3))
        self.assertEqual(len(self.p.downloads.snapshot_items),3)
        self.assertFalse(any(b.text() in ('制作片头','视频下载') for b in self.p.cards.findChildren(QPushButton)))

    def test_unified_launch_fields_and_captions_survive_refresh_save_and_restore(self):
        from ui.launch_edit_dialog import LaunchEditDialog
        from services.launch_display import resolve_launch
        row=self.p.roundup.draft['snapshot']['items'][0];identifier=row['ll2_id']
        segment=next(s for s in self.p.roundup.draft['segments'] if identifier in s['task_ids'])
        before=deepcopy(segment)
        dialog=LaunchEditDialog(self.p,row);self.addCleanup(dialog.close)
        for key,value in dict(provider='本期机构',title='本期画面标题',payload='人工载荷',orbit='',pad_name='').items():dialog.fields[key].setText(value)
        dialog.captions[0].item(0,1).setText('独立字幕');dialog.save()
        self.assertEqual(segment['spoken'],before['spoken']);self.assertEqual(segment['units'][0]['screen'],'独立字幕')
        from services.launch_display import original_source
        collection=deepcopy(self.p.roundup.draft['snapshot']);collection['items'][0]=deepcopy(original_source(collection['items'][0]));collection['items'][0]['provider']='Fresh source provider'
        self.p.roundup._new_snapshot(collection)
        self.p.save();self.p._restore(self.root/'config/production-session.json')
        row=next(i for i in self.p.roundup.draft['snapshot']['items'] if i['ll2_id']==identifier)
        view=resolve_launch(row)
        self.assertEqual((view['provider'],view['title'],view['payload'],view['orbit'],view['pad_name']),('本期机构','本期画面标题','人工载荷','',''))
        self.assertEqual(row['source_original']['provider'],'Fresh source provider')
        segment=next(s for s in self.p.roundup.draft['segments'] if identifier in s['task_ids'])
        self.assertEqual(segment['units'][0]['screen'],'独立字幕')
        card=next(i for i in self.p.roundup_video_items() if i['id']=='ll2:'+identifier)
        self.assertEqual((card['title'],card['launch']['payload']),('本期画面标题','人工载荷'))

    def test_existing_translation_cache_change_refreshes_launch_list(self):
        row=self.p.roundup.draft['snapshot']['items'][0]
        self.p._poll()
        path=self.root/'translator/translation_cache.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({row['english_name']:dict(translation='缓存中的中文任务',source='llm',confidence=.6)},ensure_ascii=False),encoding='utf-8')
        until=time.monotonic()+5
        while self.p.roundup.tasks.item(0,2).text()!='缓存中的中文任务' and time.monotonic()<until:
            self.p._poll();self.app.processEvents();time.sleep(.02)
        self.assertEqual(self.p.roundup.tasks.item(0,2).text(),'缓存中的中文任务')

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

    def test_editorial_selection_to_narration_to_failure_dialog_and_targeted_retry(self):
        from models.news import news_id
        from services import news_voice,weekly_report
        from tests.test_news_editorial import sample
        from PySide6.QtWidgets import QDialogButtonBox,QInputDialog,QListWidget,QPlainTextEdit
        rows=sample();p=self.p
        bundle=self.bundle();bundle.update(articles=list(rows.values()),summaries={})
        p.news.adopt(bundle,collected=True)
        p.settings=replace(self.settings,deepseek_api_key='fixture',deepseek_base_url='https://example.invalid',
                           deepseek_model='fixture',news_filter_level='All',news_event_date_check_enabled=False)
        requested=[];fetched=[]
        def model(prompt,*args,validator,**kwargs):
            payload=json.loads(prompt.rsplit('\n',1)[-1]);requested.append(payload['source_url'])
            quote=payload['source_text'].partition('。')[0]+'。'
            quotes=[quote]
            if payload['source_url']==rows['result'].news.url:
                quotes.append(next(row['quote'] for row in payload['verified_sources'] if '姿态稳定' in row['quote']))
            value=dict(spoken=''.join(quotes),quotes=quotes);validator(value);return value
        def fetch(news,token,**kwargs):
            fetched.append(news.key)
            if news.key==rows['missing'].news.key:
                return Article(news,'调查认定供电线路短路导致设备停机。','fixture')
            return None
        with patch.object(weekly_report,'_resolve_runtime_model',side_effect=lambda settings,token:settings), \
             patch.object(weekly_report,'assess_importance',return_value=service.NewsAssessment('High','固定样本')), \
             patch.object(weekly_report,'_generate_selected_content',return_value=0), \
             patch.object(weekly_report,'_localize_selected_titles',return_value=({},0)), \
             patch('services.news_images.collect'),patch.object(news_voice,'request_json',side_effect=model), \
             patch.object(weekly_report,'_parse_article',side_effect=fetch):
            p.start_news_write();self.wait_idle(20)
            selected=[a.news.key for a in p.news.selected_articles()]
            self.assertEqual([a.news.key for a in p.news._rows('articles')],
                             [rows[key].news.key for key in ('result','fourteen','ended','future','missing','enough')])
            self.assertNotIn(rows['ended'].news.key,selected)
            result=p.news.voices[news_id(rows['result'].news.key)]['segments'][0]
            self.assertIn('飞船成功返回',result['spoken']);self.assertIn('姿态稳定',result['spoken'])
            self.assertIn(rows['analysis'].news.url,[ref['url'] for ref in result['source_refs']])
            self.assertNotIn(rows['preview'].news.url,requested);self.assertNotIn(rows['missing'].news.url,requested)
            self.assertTrue(p.news.blocked(rows['missing']))
            self.assertFalse(p.news.blocked(rows['enough']))
            with patch.object(p,'worker',None),patch.object(p,'_news_job',side_effect=lambda *args:setattr(p,'worker',Mock())) as queue, \
                 patch('services.voice_profiles.configured',return_value=True):
                p.export_complete()
            payload=queue.call_args.args[2]
            self.assertNotIn(rows['missing'].news.key,[a.news.key for a in payload['articles']])
            self.assertNotIn(rows['ended'].news.key,[a.news.key for a in payload['articles']])
            dialog=p.news.failure_dialog
            listing=dialog.findChild(QListWidget)
            self.assertEqual(listing.count(),2)
            self.assertIn('飞后结果待补',listing.item(0).text())
            listing.setCurrentRow(1);details=dialog.findChild(QPlainTextEdit).toPlainText()
            self.assertIn('主要结论',details)
            buttons=dialog.findChild(QDialogButtonBox).buttons()
            self.assertTrue({'重试获取','手动补充','本期跳过'}<={b.text() for b in buttons})
            if output:=getattr(self,'editorial_demo',None):
                (output/'editorial-demo.json').write_text(json.dumps(dict(
                    筛选前=[a.news.title for a in rows.values()],稿件列表=[a.news.title for a in p.news._rows('articles')],
                    相关来源=[a.news.title for a in rows.values() if a.news.key in p.news.editorial_event(rows['result'])['related']],
                    实际口播稿=result['spoken'],汇总=[listing.item(i).text() for i in range(listing.count())],缺料详情=details),
                    ensure_ascii=False,indent=2),encoding='utf-8')
                self.app.processEvents();dialog.grab().save(str(output/'editorial-failures.png'))

            p.news.restore_related(rows['preview'])
            edited=p.news.voice_draft(rows['preview']);news_voice.set_spoken(edited,'人工已保存的预告稿。')
            from tests.test_launch_roundup import write_pcm
            from services.roundup_audio import audio_key,current_audio,file_hash
            audio=self.root/'saved-voice.wav';write_pcm(audio)
            segment=edited['segments'][0]
            segment['audio']=dict(path=str(audio),key=audio_key(edited,segment),sha256=file_hash(audio))
            self.assertIsNotNone(current_audio(edited,segment))
            protected=deepcopy(edited)
            requested.clear()
            next(button for button in buttons if button.text()=='重试获取').click();self.wait_idle(20)
            self.assertCountEqual(fetched,[rows[key].news.key for key in ('ended','missing')])
            self.assertEqual(requested,[rows['missing'].news.key])
            self.assertEqual(p.news.voices[edited['news_id']],protected)
            self.assertEqual(p.news.voices[news_id(rows['result'].news.key)]['segments'][0]['spoken'],result['spoken'])
            self.assertFalse(p.news.blocked(rows['missing']))
            p.news.failure_dialog.close()
            p.start_news_write();self.wait_idle(20)
            self.assertIn(rows['preview'].news.key,p.news.script_included)
            self.assertEqual(p.news.voices[edited['news_id']],protected)
            p.news.failure_dialog.close()
            p.save();p._restore(self.root/'config/production-session.json')
            self.assertIn(rows['preview'].news.key,p.news.editorial_event(rows['result'])['related'])
            self.assertTrue(p.news.script_preferences[rows['preview'].news.key])
            self.assertIsNotNone(current_audio(p.news.voices[edited['news_id']],p.news.voices[edited['news_id']]['segments'][0]))
            ended=p.news.failed_articles()[0]
            self.assertEqual(ended.news.key,rows['ended'].news.key)
            p.news.show_failures()
            buttons=p.news.failure_dialog.findChild(QDialogButtonBox).buttons()
            next(button for button in buttons if button.text()=='本期跳过').click()
            self.assertFalse(p.news.failed_articles())
            p.news.restore_related(ended)
            p.news.show_failures()
            buttons=p.news.failure_dialog.findChild(QDialogButtonBox).buttons()
            with patch.object(QInputDialog,'getMultiLineText',return_value=('飞船成功着陆。',True)), \
                 patch.object(QInputDialog,'getText',return_value=('https://example.invalid/official-result',True)):
                next(button for button in buttons if button.text()=='手动补充').click()
            self.assertEqual(p.news.editorial_event(ended)['phase'],'result')
            self.assertTrue(ended.evidence[-1]['manual'])

    def test_news_merge_preview_save_restore_and_undo_keeps_original_voice(self):
        from services import news_editorial,news_voice
        from services.roundup_audio import audio_key,current_audio,file_hash
        from tests.test_launch_roundup import write_pcm
        from tests.test_news_editorial import voyager_sample
        from PySide6.QtWidgets import QDialogButtonBox,QListWidget
        p=self.p;first,second,plan=voyager_sample()
        bundle=self.bundle();bundle.update(articles=[first,second,plan],summaries={})
        p.news.adopt(bundle,collected=True)
        original=p.news.voice_draft(first);news_voice.set_spoken(original,'人工保存的原稿。')
        audio=self.root/'original.wav';write_pcm(audio);segment=original['segments'][0]
        segment['audio']=dict(path=str(audio),key=audio_key(original,segment),sha256=file_hash(audio))
        def choose(dialog):
            listing=dialog.findChild(QListWidget)
            if listing:
                for index in range(listing.count()):listing.item(index).setSelected(True)
                next(button for button in dialog.findChild(QDialogButtonBox).buttons() if button.text()=='生成预览').click()
            else:
                editors=dialog.findChildren(QPlainTextEdit)
                self.assertIn('旅行者2号预计于2027年',editors[-1].toPlainText())
                if output:=getattr(self,'editorial_demo',None):
                    dialog.show();self.app.processEvents();dialog.grab().save(str(output/'voyager-merge.png'))
                next(button for button in dialog.findChild(QDialogButtonBox).buttons() if button.text()=='确认合稿').click()
            return dialog.result()
        with patch.object(QDialog,'exec',choose):p.news.merge_button.trigger()
        merged,=p.news.selected_articles()
        draft=p.news.voice_draft(merged)
        self.assertEqual(draft['screen_item']['ordinal'],1)
        self.assertEqual(draft['segments'][0]['spoken'].count('旅行者1号'),1)
        self.assertTrue(current_audio(original,segment))
        self.assertCountEqual([r['url'] for r in draft['segments'][0]['source_refs']],[r.news.url for r in (first,second,plan)])
        p.news._check_scripts(True);self.assertEqual(p.news.selected_articles(),[merged])
        p.save();p._restore(self.root/'config/production-session.json')
        merged,=p.news.selected_articles()
        p.news._show_item('articles',merged.news.key)
        self.assertIn('信号单程时间',p.news.conflict_hint.text())
        p.news.unmerge_button.click()
        self.assertEqual([a.news.key for a in p.news.selected_articles()],[first.news.key,second.news.key,plan.news.key])
        restored=p.news.voice_draft(p.news.selected_articles()[0])
        self.assertEqual(restored['segments'][0]['spoken'],'人工保存的原稿。')
        self.assertTrue(current_audio(restored,restored['segments'][0]))

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
        self.assertEqual(p.news.select_visible.checkState(),Qt.CheckState.PartiallyChecked)
        p.news.select_visible.click()
        self.assertEqual(len(p.news.script_included),3)
        p.news.select_visible.click()
        self.assertFalse(p.news.script_included)
        self.assertEqual(p.news.editor_tabs.currentIndex(),1)
        self.assertEqual(p.news.current_article().news.url,url)
        self.assertEqual(p.news.summary.toPlainText(),'第三条的屏幕摘要')
        p.news.select_visible.click()
        self.assertEqual(len(p.news.script_included),3)
        p.news.articles.item(1).setCheckState(Qt.CheckState.Unchecked)
        checked=set(p.news.script_included)
        p.news.editor_tabs.setCurrentIndex(0)
        self.assertEqual(p.news.script_included,checked)
        p.news.articles.setCurrentRow(0);p.news.articles.setCurrentRow(2)
        self.assertEqual(p.news.current_title.text(),'第三条的屏幕标题')
        p.news.script_actions[0].click();p.news.script_actions[0].click()
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

    def test_news_voice_picker_follows_script_checks_and_order(self):
        from models.news import news_id
        p=self.p;bundle=self.bundle()
        for name,day in (('second',24),('third',25),('outside',30)):
            url='https://example.invalid/'+name
            bundle['articles'].append(Article(News('Fixture',name,f'2026-10-{day}','',url=url),'Body','fixture'))
        p.episode['review_range']=['2026-10-20','2026-10-29']
        p.news.adopt(bundle,collected=True)
        first,second,third,_=bundle['articles']
        p.navigation.setCurrentRow(1)
        p.news.articles.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(p.news.go_voice.isEnabled())
        self.assertIn(first.news.key,p.news.included)
        p.news.articles.setCurrentRow(2);p.news.move_article(-1)
        p.media_route(0)
        picker=p.voice_segments
        expected=[news_id(a.news.key) for a in (third,second)]
        self.assertEqual([picker.itemData(i) for i in range(picker.count())],expected)
        self.assertEqual(p.voice_scope.text(),'稿件已勾选 2 条')
        picker.setCurrentIndex(1)
        self.assertIs(p.news.current_article(),second)
        p.news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual([picker.itemData(i) for i in range(picker.count())],expected[:1])
        self.assertIs(p.news.current_article(),third)
        p.news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(picker.count(),0)
        self.assertEqual(p.voice_scope.text(),'稿件已勾选 0 条')
        self.assertFalse(p.preview_play.isEnabled())
        self.assertIsNone(p.frame.image)

    def test_media_scope_empty_clears_old_preview_and_keeps_script(self):
        from PySide6.QtGui import QPixmap
        p=self.p;p.news.adopt(self.bundle(),collected=True)
        p.news.oral.setPlainText('人工口播保留。');p.news.select_visible.click()
        p._voice_refresh()
        self.assertEqual(p.voice_segments.count(),0);self.assertFalse(p.preview_play.isEnabled())
        self.assertIsNone(p.frame.image);self.assertEqual(p.news.oral.toPlainText(),'人工口播保留。')
        p.content.setCurrentIndex(1)
        p.frame.set_image(QPixmap(20,20));p._frame_key='previous-preview'
        for row in range(p.roundup.segment_list.count()):p.roundup.segment_list.item(row).setCheckState(Qt.CheckState.Unchecked)
        p._voice_refresh()
        self.assertEqual(p.voice_segments.count(),0);self.assertEqual(p.preview_tasks.count(),1)
        self.assertIsNone(p.frame.image);self.assertFalse(p.preview_play.isEnabled())
        self.assertEqual(len(p.roundup.draft['segments']),5)

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

    def test_launch_preview_pager_and_rows_can_be_clicked_without_seeking(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle
        from tools import roundup_visuals as visuals
        p=self.p;items=[context(i) for i in range(1,8)]
        draft=r.create_draft(dict(items=items,complete=True),'2026-10-20','2026-10-29')
        ids=[row['id'] for row in draft['selection']]
        draft['segments']=[dict(id='browse',kind='highlight',task_ids=ids,screen='发射任务',spoken='',preview_seconds=25)]
        p.roundup.draft=draft;p.roundup._show_draft()
        p.content.setCurrentIndex(1);p.media_route(0);self.w.show();self.app.processEvents()
        p.preview_roundup()
        clock=p.preview_slider.value();frame=p.frame
        self.assertEqual(clock,0)

        def click(box):
            size=frame.pixmap().deviceIndependentSize().toSize()
            rect=QStyle.alignedRect(frame.layoutDirection(),frame.alignment(),size,frame.contentsRect())
            left,top,right,bottom=box
            QTest.mouseClick(frame,Qt.MouseButton.LeftButton,pos=QPoint(
                rect.x()+round((left+right)*rect.width()/(2*visuals.cards.W)),
                rect.y()+round((top+bottom)*rect.height()/(2*visuals.cards.H))))

        first=p._roundup_list_scene
        self.assertIs(p._preview_image,first['image'])
        next_task=dict(first['targets'])[visuals.PAGE_BUTTONS[1]]
        click(visuals.PAGE_BUTTONS[1]);second=p._roundup_list_scene
        self.assertEqual((second['page'],second['focus_id']),(2,next_task))
        bounds,identifier=second['targets'][1]
        click(bounds);self.assertEqual(p._roundup_list_scene['focus_id'],identifier)
        click(visuals.PAGE_BUTTONS[0]);self.assertEqual(p._roundup_list_scene['page'],1)
        self.assertEqual(p.preview_slider.value(),clock)

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
        until=time.monotonic()+8
        while p._poster_request and time.monotonic()<until:
            self.app.processEvents();time.sleep(.01)
        self.assertAlmostEqual(p._preview_timeline['duration_seconds'],7.85)
        p._audio_playback=True;p.news.oral.insertPlainText('已修改。')
        self.assertFalse(p._audio_playback);self.assertIn('过期',p.play_state.text())

    def test_news_local_image_job_and_cache_preferences_survive_session_restore(self):
        from PIL import Image
        from models.news import news_id
        from services import news_images,cache_maintenance
        p=self.p;p.maintenance.timer.stop();p.news.adopt(self.bundle())
        article=p.news.current_article();source=self.root/'本地 配图.png';Image.new('RGB',(96,64),'blue').save(source)
        second=self.root/'另外一张.png';Image.new('RGB',(96,64),'red').save(second)
        with patch('ui.news_workspace.QFileDialog.getOpenFileNames',return_value=([str(source),str(second)],'')):
            next(action for action in p.voice_image_menu.menu().actions() if action.text().startswith('添加本地图片')).trigger()
        self.wait_idle()
        self.assertIsNotNone(news_images.selected(news_id(article.news.url)))
        p.maintenance.enabled.setChecked(False);p.maintenance.interval.setValue(5);p.maintenance.retention.setValue(7)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertIsNotNone(news_images.selected(news_id(article.news.url)))
        self.assertEqual(len(p.news.voice_draft()['screen_item']['slideshow']),2)
        settings=cache_maintenance.load();self.assertFalse(settings['enabled']);self.assertEqual((settings['interval_days'],settings['retention_days']),(5,7))
        (self.root/'news-images/manifest.json').write_text('{broken',encoding='utf-8')
        self.assertIsNotNone(p.news.voice_draft())
        from ui.cache_maintenance_panel import CacheMaintenancePanel
        (self.root/'config/cache-maintenance.json').write_text('{broken',encoding='utf-8')
        panel=CacheMaintenancePanel(p);panel.timer.stop();self.addCleanup(panel.deleteLater)
        self.assertIn('原文件保留',panel.status.text())

    def test_default_export_ignores_old_strip_flag_and_legacy_mode_exports_strips(self):
        from services import news_voice
        p=self.p;p.news.adopt(self.bundle())
        news_voice.set_spoken(p.news.voice_draft(),'正在测试整期新闻口播。')
        self.assertEqual(p.settings.video_workflow,'complete')
        self.assertTrue(p.video_workflow.itemText(1).startswith('模式 2'))
        from services.launch_data import write_json
        write_json(self.root/'config/production-session.json',dict(p.snapshot(),include_news_strip=True))
        p._restore(self.root/'config/production-session.json')
        self.assertNotIn('include_news_strip',p.content_state())
        path=self.root/'output'/'complete.mp4';path.write_bytes(b'fixture')
        result=dict(paths=[str(path)],failed=[],cancelled=False)
        with (patch('services.voice_profiles.configured',return_value=True),
              patch('services.episode.produce',return_value=result) as render):
            p.export_complete();self.wait_idle()
        drafts=render.call_args.args[0]
        self.assertEqual([draft['id'] for draft in drafts],['episode-opening',p.roundup.draft['id'],
            p.news.voice_draft()['id'],'episode-closing'])
        self.assertFalse(render.call_args.kwargs['parts'])
        p.navigation.setCurrentRow(3);p.content.setCurrentIndex(1)
        self.assertEqual(p.output_files.count(),1)
        self.enable_materials();p.content.setCurrentIndex(0)
        with (patch('services.voice_profiles.configured',return_value=True),
              patch('services.episode.produce',return_value=result) as render):
            p.export_complete();self.wait_idle()
        self.assertTrue(render.call_args.kwargs['parts'])
        self.assertEqual([d['id'] for d in render.call_args.args[0]],[p.news.voice_draft()['id']])

    def test_news_video_and_subject_can_be_set_from_the_workspace(self):
        import shutil,subprocess
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('FFmpeg required')
        p=self.p;p.news.adopt(self.bundle())
        source=self.root/'本地 视频.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=0.3',
                        '-c:v','libx264',str(source)],check=True)
        actions={action.text():action for action in p.voice_image_menu.menu().actions()}
        with patch('ui.news_workspace.QFileDialog.getOpenFileName',return_value=(str(source),'')):
            actions['添加本地视频'].trigger()
        self.wait_idle()
        asset=p.news.voice_draft()['screen_item']['video']
        self.assertEqual(asset['name'],source.name)
        self.assertNotEqual(Path(asset['path']),source)
        from PySide6.QtCore import QTimer
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialogButtonBox
        from ui.news_trim_dialog import NewsTrimDialog
        original_exec=NewsTrimDialog.exec
        def set_points(dialog):
            def interact():
                dialog.slider.setFocus();dialog.slider.setValue(3);QTest.keyClick(dialog.slider,Qt.Key.Key_I)
                dialog.slider.setValue(11);QTest.keyClick(dialog.slider,Qt.Key.Key_O)
                dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
            QTimer.singleShot(50,interact)
            return original_exec(dialog)
        with patch.object(NewsTrimDialog,'exec',set_points):actions['剪裁视频（I / O）'].trigger()
        self.wait_idle();asset.update(in_frame=3,out_frame=12)
        p.news.screen_fields['subject'].textEdited.emit('人工确认的机构')
        p.save();p._restore(self.root/'config/production-session.json')
        item=p.news.voice_draft()['screen_item']
        self.assertEqual(item['subject'],'人工确认的机构')
        self.assertEqual(item['video'],asset)
        actions['移除本条视频'].trigger();self.wait_idle()
        self.assertIsNone(p.news.voice_draft()['screen_item']['video'])
        self.assertTrue(source.is_file())

    def test_background_music_selection_is_stored_in_current_edition(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog,QDialogButtonBox,QPushButton,QListWidget
        p=self.p;paths=[str(self.root/'曲一.wav'),str(self.root/'曲二.flac')]
        original_exec=QDialog.exec
        def edit(dialog):
            def interact():
                next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('添加音频')).click()
                listing=dialog.findChild(QListWidget)
                listing.addItem(listing.takeItem(0))
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            QTimer.singleShot(30,interact)
            return original_exec(dialog)
        with patch.object(QDialog,'exec',edit),patch('ui.production_workspace.QFileDialog.getOpenFileNames',return_value=(paths,'')):
            p.edit_episode()
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertEqual(p.episode['music'],paths[::-1])

    def test_music_panel_persists_levels_and_updates_both_playback_outputs(self):
        p=self.p;p.content.setCurrentIndex(1);p.media_route(0)
        before=deepcopy(p.roundup.draft['segments'])
        p._poll()
        p.preview_audio_output=Mock();p.preview_bed_output=Mock()
        with patch.object(p,'_voice_refresh') as refresh:
            p.music_panel.sliders['voice_volume'][0].setValue(67)
            p.music_panel.sliders['music_volume'][0].setValue(24)
            p._poll();refresh.assert_not_called()
        p.preview_audio_output.setVolume.assert_called_with(.67)
        p.preview_bed_output.setVolume.assert_called_with(.24)
        p.music_panel.enabled.setChecked(False)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertFalse(p.music_panel.enabled.isChecked())
        self.assertEqual(p.music_panel.sliders['music_volume'][0].value(),24)
        self.assertEqual(p.roundup.draft['segments'],before)

    def test_workflow_buttons_preserve_mode_and_return_from_voice_settings(self):
        p=self.p;self.w.show();self.app.processEvents()
        self.assertTrue(p.previous_button.isHidden())
        p.materials.setCurrentIndex(1)
        p.next_button.click()
        self.assertEqual(p.pages.currentIndex(),1)
        self.assertEqual(p.scripts.currentIndex(),1)
        p.media.setCurrentIndex(3)
        p.next_button.click()
        self.assertEqual(p.pages.currentIndex(),2)
        self.assertEqual(p.media.currentIndex(),0)
        p.next_button.click()
        self.assertEqual(p.pages.currentIndex(),3)
        self.assertTrue(p.next_button.isHidden())
        self.assertIn(f'发射任务 {p._launch_count()} 项',p.export_contents.text())
        configure=next(button for button in p.export_voice_row.findChildren(QWidget)
                       if button.property('productionKey')=='production.configure_voice')
        configure.click()
        self.assertEqual((p.pages.currentIndex(),p.settings_tabs.currentIndex()),(4,2))
        p.previous_button.click()
        self.assertEqual(p.pages.currentIndex(),3)
        p.content.setCurrentIndex(0)
        review=next(button for button in p.findChildren(QWidget)
                    if button.property('productionKey')=='production.review_script')
        review.click()
        self.assertEqual((p.pages.currentIndex(),p.scripts.currentIndex()),(1,1))
        p.previous_button.click()
        self.assertEqual((p.pages.currentIndex(),p.materials.currentIndex()),(0,1))

    def test_roundup_script_shows_grouped_coverage_and_missing_tasks(self):
        p=self.p;dialog=p.roundup
        r.apply_segments(dialog.draft,r.local_script(dialog.draft,'fixture'))
        dialog._show_segments()
        self.assertIn('稿件关联 3/3 项发射',dialog.script_origin.text())
        group=dialog.segment_list.item(1)
        self.assertTrue(group.text().startswith('3 项 · '))
        self.assertIn('Fixture mission 3',group.toolTip())
        extra=context(4)
        dialog._new_snapshot({**dialog.collection,'items':[*dialog.draft['snapshot']['items'],extra]})
        self.assertIn('稿件关联 3/4 项发射',dialog.script_origin.text())
        self.assertIn('1 项尚未编入',dialog.script_origin.text())
        self.assertIn('Fixture mission 4',dialog.script_origin.toolTip())

    def test_paused_news_preview_shows_selected_article_before_playback(self):
        from tools import news_visuals
        p=self.p;p.news.adopt(self.bundle(),collected=False)
        new=p.news.add_news('另一条新闻','这条新闻的正文用于确认预览会显示当前条目。')
        p.news.select_article(new.news.manual_id);self.w.show();p.media_route(0);self.app.processEvents()
        with patch.object(news_visuals,'slide_from',wraps=news_visuals.slide_from) as slide:
            p._preview_news()
            self.assertIn('previous_picture',p._news_frames)
            self.assertEqual(p.preview_slider.value(),0)
            slide.assert_not_called()
            p.preview_slider.setValue(100)
            slide.assert_called_once()

    def test_episode_number_and_shared_beijing_dates_reach_both_sources(self):
        from PySide6.QtWidgets import QDateEdit,QDialogButtonBox,QLineEdit,QSpinBox
        p=self.p
        bundle=self.bundle()
        outside=Article(News('Fixture','下一天的新闻','2026-10-26T16:00:00Z','','https://example.invalid/late'),'正文','fixture')
        bundle['articles'].append(outside)
        p.news.adopt(bundle,collected=False)
        item=context(1);item['net']='2026-10-22T16:00:00Z'
        p.roundup.collection=dict(items=[item],complete=True,start='2026-10-20',end='2026-10-29')
        original_exec=QDialog.exec
        names=[]
        def edit(dialog):
            def interact():
                name=dialog.findChild(QLineEdit,'EpisodeName')
                names.append(name.text())
                name.setText('深空周报')
                names.append(dialog.findChild(QPlainTextEdit).placeholderText())
                dialog.findChild(QSpinBox,'EditionNumber').setValue(43)
                dialog.findChild(QDateEdit,'EditionRangeStart').setDate(QDate(2026,10,23))
                dialog.findChild(QDateEdit,'EditionRangeEnd').setDate(QDate(2026,10,26))
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            QTimer.singleShot(30,interact)
            return original_exec(dialog)
        with patch.object(QDialog,'exec',edit):p.period.click()
        self.assertEqual(names[0],'航天周报')
        self.assertIn('深空周报',names[1])
        p._finish_recovery_write(wait=True);p._poll();p._finish_recovery_write(wait=True)
        recovered=json.loads((self.root/'cache/production-recovery.json').read_text(encoding='utf-8'))
        self.assertEqual(recovered['episode']['name'],'深空周报')
        p.save();p._restore(self.root/'config/production-session.json')
        dates=['2026-10-23','2026-10-26'];settings=p.current_settings()
        self.assertEqual(p.episode['number'],43)
        self.assertEqual(p.episode['name'],'深空周报')
        self.assertIn('第 43 期',p.period.text())
        self.assertEqual(p.episode['review_range'],dates)
        self.assertEqual([settings.start_date,settings.end_date],dates)
        self.assertEqual([settings.launch_start_date,settings.launch_end_date],dates)
        self.assertEqual([p.roundup.draft['start'],p.roundup.draft['end']],dates)
        self.assertEqual([p.intro.options()['date_text'],p.intro.options()['end_date']],dates)
        self.assertEqual([a.news.title for a in p.news.selected_articles()],['Fixture news'])
        self.assertFalse(p.roundup.recent_dates.isEnabled())
        p.intro.result_path=self.root/'older-intro.mp4'
        def start(*_):p.worker=Mock()
        try:
            with patch('services.voice_profiles.configured',return_value=True),patch.object(p,'_news_job',side_effect=start) as export:
                p.export_complete()
        finally:p.worker=None
        payload=export.call_args.args[2]
        self.assertEqual(payload['episode']['name'],'深空周报')
        self.assertIsNone(payload['intro'])
        self.assertEqual([payload['intro_options']['date_text'],payload['intro_options']['end_date']],dates)

    def test_news_pagination_keeps_review_position_and_recovers_last_page(self):
        p=self.p;news=p.news
        bundle=self.bundle();bundle['articles']=[Article(
            News('Fixture',f'新闻 {n}', '2026-10-23','','https://example.invalid/news/'+str(n)),
            '正文内容','fixture') for n in range(43)]
        news.adopt(bundle,collected=True)
        p.content.setCurrentIndex(0);p.navigation.setCurrentRow(1)
        combo= news._lists['articles'][0]
        combo.setCurrentIndex(combo.findData('selected'))
        news._page('articles',1)
        removed=news.current_article()
        news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(news._pages['articles'],1)
        self.assertEqual(news.current_article().news.title,'新闻 21')
        self.assertEqual(p.navigation.currentRow(),1)
        self.assertTrue(removed.news.key in news.included)
        news._page('articles',1)
        while news._pages['articles']==2:
            news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(news._pages['articles'],1)
        self.assertEqual(news.articles.currentRow(),19)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertEqual(news._pages['articles'],1)
        self.assertEqual(news.articles.currentRow(),19)
        combo.setCurrentIndex(combo.findData('unselected'))
        while news.articles.count():
            news.articles.currentItem().setCheckState(Qt.CheckState.Checked)
        self.assertEqual(len(news.script_included),43)
        combo.setCurrentIndex(combo.findData('all'));news._page('articles',1)
        selected=news.current_article().news.key
        news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(news.current_article().news.key,selected)
        self.assertEqual(news._pages['articles'],1)
        self.assertEqual(news.select_visible.checkState(),Qt.CheckState.PartiallyChecked)
        news.select_visible.click()
        self.assertEqual(len(news.script_included),43)
        self.assertEqual(news._pages['articles'],1)
        news.articles.currentItem().setCheckState(Qt.CheckState.Unchecked)
        outside=set(news.script_included)
        combo.setCurrentIndex(combo.findData('unselected'))
        self.assertEqual(news.select_visible.checkState(),Qt.CheckState.Unchecked)
        news.select_visible.click()
        self.assertTrue(outside<news.script_included)
        self.assertEqual(combo.currentData(),'unselected')
        self.assertEqual(news.articles.count(),0)

    def test_manual_news_without_url_survives_collection_and_restore(self):
        p=self.p;news=p.news
        first=news.add_news('第一条人工新闻','这是供编辑和验收的第一条完整内容。')
        second=news.add_news('第二条人工新闻','这是供编辑和验收的第二条完整内容。')
        self.assertNotEqual(first.news.manual_id,second.news.manual_id)
        self.assertEqual(first.news.url,'')
        self.assertIs(news.source_article(),second)
        news.title.setText('用户改写的新闻画面')
        news.summary.setPlainText('独立的屏幕摘要')
        news.adopt(self.bundle(),collected=True)
        p.save();p._restore(self.root/'config/production-session.json')
        self.assertTrue(news.select_article(second.news.manual_id))
        self.assertEqual(news.title.text(),'用户改写的新闻画面')
        self.assertEqual(news.summary.toPlainText(),'独立的屏幕摘要')
        self.assertEqual(news.voice_draft()['news_id'],second.news.manual_id)
        with self.assertRaisesRegex(ValueError,'标题和正文'):news.add_news('空正文','   ')

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
        with patch.object(p,'media_draft',wraps=p.media_draft) as prepare:
            p._advance_preview()
            prepare.assert_not_called()
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
        self.wait_idle()
        self.p.apply_preferences();self.app.processEvents()
        self.assertEqual(self.p.navigation.item(1).text(),'Script')
        self.assertIs(self.p.roundup,controller)
        self.assertIn('不能翻译内容',self.p.roundup.spoken.toPlainText())

if __name__=='__main__':unittest.main()
