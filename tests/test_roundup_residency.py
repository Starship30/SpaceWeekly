"""Persistent worker control, real subprocess IPC with mocked inference, explicit script origins."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from ai.qwen_tts import QwenTTSProvider
from ai.speech import SpeechRequest, generate_speech
from services.cancellation import CancellationToken, TaskCancelledError
from services import launch_roundup as roundup
from tests import test_launch_roundup as base
from tests.test_launch_roundup import generated
from tests.qwen_fixture import worker_source
from tools.qwen_tts_worker import QwenRuntime


class ResidentTests(unittest.TestCase):
    setUp = base.RoundupTests.setUp

    def resident_provider(self):
        model = self.root / 'model'
        model.mkdir(exist_ok=True)
        (model / 'config.json').write_text(json.dumps(dict(tts_model_type='custom_voice', tts_model_size='1b7')))
        fake = self.root / 'mock_worker.py'
        fake.write_text(worker_source(), encoding='utf-8')
        patched = patch('ai.qwen_tts.bundled_resource_path', return_value=fake)
        patched.start()
        self.addCleanup(patched.stop)
        states = []
        provider = QwenTTSProvider(dict(python=sys.executable, model=str(model), device='cpu', timeout=10), states.append)
        self.addCleanup(provider.unload)
        return provider, model, states

    def speak(self, provider, model, text='first', **kwargs):
        return generate_speech(SpeechRequest(text, model=str(model), voice=kwargs.pop('voice', 'vivian'),
            style=kwargs.pop('style', '')), self.root / 'audio', provider=provider, **kwargs)

    def test_second_paragraph_speaker_and_style_reuse_one_loaded_instance(self):
        provider, model, states = self.resident_provider()
        self.assertFalse(provider.is_running)

        first = self.speak(provider, model)
        pid = provider.process.pid
        second = self.speak(provider, model, 'second', voice='serena', style='calm')
        self.assertEqual(provider.load_count, 1)
        self.assertEqual(provider.process.pid, pid)
        self.assertTrue(first.path.exists() and second.path.exists())
        self.assertTrue(any('复用已加载' in state for state in states))
        provider.unload()
        self.assertFalse(provider.is_running)

    def test_unicode_model_path_and_chinese_reading_cross_the_real_worker_protocol(self):
        provider,model,_=self.resident_provider()
        renamed=self.root/'中文 模型';model.rename(renamed)
        provider.configure(dict(provider.settings,model=str(renamed)))
        result=self.speak(provider,renamed,'遥感零三星，九A工位。')
        self.assertTrue(result.path.exists());self.assertEqual(provider.load_count,1)

    def test_temporary_log_cleanup_error_does_not_discard_completed_audio(self):
        provider,model,states=self.resident_provider();result=self.speak(provider,model)
        content=result.path.read_bytes();temporary=provider._runtime
        with patch.object(temporary,'cleanup',side_effect=PermissionError('file scanner holds log')):
            provider.unload()
        self.assertFalse(provider.is_running);self.assertEqual(result.path.read_bytes(),content)
        self.assertTrue(any('临时日志' in state for state in states));temporary.cleanup()

    def test_switch_device_or_model_stops_old_process_before_loading_another(self):
        provider, model, _ = self.resident_provider()
        self.speak(provider, model)
        old = provider.process
        provider.configure(dict(provider.settings, device='cuda:0'))
        self.speak(provider, model, 'second')
        self.assertIsNotNone(old.poll())
        self.assertNotEqual(old.pid, provider.process.pid)
        second = provider.process
        alternate = self.root / 'another-model'
        alternate.mkdir()
        (alternate / 'config.json').write_bytes((model / 'config.json').read_bytes())
        provider.configure(dict(provider.settings, model=str(alternate)))
        self.speak(provider, alternate)
        self.assertIsNotNone(second.poll())
        self.assertEqual(provider.load_count, 1)

    def test_failure_keeps_model_and_next_request_recovers_without_reload(self):
        provider, model, states = self.resident_provider()
        self.speak(provider, model)
        pid = provider.process.pid
        with self.assertRaisesRegex(RuntimeError, 'mock inference failure'):
            self.speak(provider, model, 'fail')
        self.speak(provider, model, 'after failure')
        self.assertEqual((provider.process.pid, provider.load_count), (pid, 1))
        self.assertTrue(any('失败' in state for state in states))

    def test_cancel_confirms_worker_exit_and_preserves_completed_audio(self):
        provider, model, states = self.resident_provider()
        first = self.speak(provider, model)
        content = first.path.read_bytes()
        process = provider.process
        def cancel():
            if states and states[-1] == '模型已就绪，正在生成音频':
                raise TaskCancelledError('mock cancel')
        states.clear()
        with self.assertRaises(TaskCancelledError):
            self.speak(provider, model, 'slow', cancel_check=cancel)
        self.assertFalse(provider.is_running)
        self.assertIsNotNone(process.poll())
        self.assertEqual(first.path.read_bytes(), content)
        self.assertEqual(len(list((self.root / 'audio').glob('*.wav'))), 1)

    def test_real_runtime_load_method_called_once_and_releases_on_switch(self):
        runtime = QwenRuntime()
        model = self.root / 'model'
        model.mkdir()
        (model / 'config.json').write_text(json.dumps(dict(tts_model_type='custom_voice', tts_model_size='1b7')))
        voice = Mock()
        voice.get_supported_speakers.return_value = ['vivian']
        voice.generate_custom_voice.return_value = ([b'mocked'], 24000)
        def load(job):
            runtime.model, runtime.sf = voice, Mock()
        job = dict(model=str(model), device='cpu', voice='vivian', text='one', output=str(self.root/'a.wav'))
        with patch.object(runtime, '_load', side_effect=load) as loads:
            runtime.generate(job, lambda *_: None)
            runtime.generate(dict(job, text='two'), lambda *_: None)
            self.assertEqual(loads.call_count, 1)
            runtime.generate(dict(job, device='cuda:0'), lambda *_: None)
            self.assertEqual(loads.call_count, 2)
        runtime.release()
        self.assertIsNone(runtime.model)

    def test_no_credentials_uses_labelled_local_template_without_api_call(self):
        self.settings.deepseek_api_key = ''
        with patch.object(roundup, 'request_json') as call:
            segments = roundup.generate_script(self.draft, self.settings, self.token)
        call.assert_not_called()
        self.assertEqual({s['origin'] for s in segments}, {'local_template'})
        self.assertIn('未调用AI', segments[0]['generation_reason'])
        self.assertIn('Fixture mission 3', segments[-2]['spoken'])

    def test_valid_json_with_wrong_ids_or_facts_is_not_reported_as_ai_success(self):
        self.settings.deepseek_api_key = 'mock-key'
        for value in ('[[name:fake-id]]', '首次创造世界纪录。', '本周全球完成999次发射。',
                      '同组任务还有[[name:launch-1]]。'):
            bad = generated()
            bad['segments'][1]['spoken'] = value
            with patch.object(roundup, 'request_json', return_value=bad):
                segments = roundup.generate_script(self.draft, self.settings, self.token)
            self.assertEqual({s['origin'] for s in segments}, {'local_template'})
            self.assertIn('事实核验失败', segments[0]['generation_reason'])

    def test_success_records_provider_and_stats_groups_without_credentials_in_prompt(self):
        self.settings.deepseek_api_key = 'do-not-log-this-credential'
        def response(prompt, config, **kwargs):
            self.assertIn('computed_statistics', prompt)
            self.assertIn('thematic_groups', prompt)
            self.assertNotIn(self.settings.deepseek_api_key, prompt)
            from tests.test_launch_roundup import styled_generated
            kwargs['validator'](styled_generated())
            return styled_generated()
        with patch.object(roundup, 'request_json', side_effect=response):
            segments = roundup.generate_script(self.draft, self.settings, self.token)
        self.assertEqual({s['origin'] for s in segments}, {'ai'})
        self.assertEqual(segments[0]['text_provider'], self.settings.ai_provider)

    def test_api_failure_reason_redacted_and_cancel_never_falls_back(self):
        self.settings.deepseek_api_key = 'secret-for-test'
        with patch.object(roundup, 'request_json', side_effect=OSError('secret-for-test network failure')):
            segments = roundup.generate_script(self.draft, self.settings, self.token)
        self.assertNotIn('secret-for-test', segments[0]['generation_reason'])
        with patch.object(roundup, 'request_json', side_effect=TaskCancelledError('cancel')):
            with self.assertRaises(TaskCancelledError):
                roundup.generate_script(self.draft, self.settings, self.token)

    def test_single_fallback_does_not_modify_other_human_paragraphs(self):
        self.settings.deepseek_api_key = ''
        original = deepcopy(self.draft['segments'][1])
        original.update(spoken='人工修改', edited=True)
        self.draft['segments'][1] = original
        segments = roundup.generate_script(self.draft, self.settings, self.token, single_id='focus2')
        roundup.apply_segments(self.draft, segments, single_id='focus2')
        self.assertEqual(self.draft['segments'][1], original)

    def test_preview_and_batch_use_the_same_dialog_provider_and_label_local_script(self):
        from PySide6.QtWidgets import QApplication
        from ui.launch_roundup_dialog import LaunchRoundupDialog
        app = QApplication.instance() or QApplication([])
        with patch('ui.launch_roundup_dialog.load_existing', return_value=self.collection):
            dialog = LaunchRoundupDialog(self.settings)
        self.addCleanup(dialog.close)
        model = self.root / 'ui-model'
        model.mkdir()
        (model / 'config.json').write_text(json.dumps(dict(tts_model_type='custom_voice', tts_model_size='0b6')))
        dialog.draft = deepcopy(self.draft)
        dialog.draft['tts'].update(python=sys.executable, model=str(model))
        dialog._show_draft()
        provider = Mock(is_running=False)
        dialog.tts_provider = provider
        used = []
        with patch.object(dialog, '_start_job', side_effect=lambda kind, action, data: action(data, CancellationToken())), \
             patch('ui.launch_roundup_dialog.generate_audio', side_effect=lambda data, p, token, **kw: used.append(p)):
            dialog._audio_job('focus1', play=True)
            dialog._audio_job()
        self.assertEqual(used, [provider, provider])
        self.assertEqual(provider.configure.call_count, 2)
        dialog.draft['segments'] = roundup.local_script(dialog.draft, '无配置，未调用AI')
        dialog._show_segments()
        self.assertIn('本地模板稿', dialog.script_origin.text())
        self.assertNotIn('AI综述（资料约束生成）', dialog.script_origin.text())

    def test_parent_window_close_unloads_an_idle_resident_model_first(self):
        from ui.main_window import MainWindow
        production=SimpleNamespace(request_leave=Mock())
        production.guard=lambda action:action()
        owner = SimpleNamespace(production=production, _closing=False)
        event = Mock()
        MainWindow.closeEvent(owner, event)
        production.request_leave.assert_called_once_with('close')
        event.ignore.assert_called_once()

    def test_failed_os_stop_is_not_reported_as_stopped(self):
        states = []
        provider = QwenTTSProvider({}, states.append)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('mock-worker', 1)
        provider.process = process
        with self.assertRaisesRegex(RuntimeError, '未确认'):
            provider.unload(force=True)
        self.assertTrue(provider.is_running)
        self.assertIn('停止尚未确认', states[-1])
        provider.process = None


if __name__ == '__main__':
    unittest.main()
