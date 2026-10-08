import os,tempfile,json,unittest
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['APPDATA']=tempfile.mkdtemp(prefix='spaceweekly-model-tests-')
from dataclasses import replace
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication
from ai.configuration import PROVIDER_URLS,provider_config_from_settings
from ai.providers import ProviderConfig,list_models,complete
from services.settings import default_settings
from ui.settings_dialog import SettingsDialog
from ui.widgets.model_worker import ModelWorker

class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def dialog(self):
        return SettingsDialog(replace(default_settings(),deepseek_api_key='',deepseek_model='saved-model',deepseek_base_url=PROVIDER_URLS['DeepSeek']))

    def test_switch_defaults_custom_and_credentials(self):
        dialog=self.dialog()
        dialog.api_key_edit.setText('deepseek-test-key')
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('OpenAI'))
        self.assertEqual(dialog.base_url_edit.text(),PROVIDER_URLS['OpenAI'])
        self.assertEqual(dialog.api_key_edit.text(),'')
        self.assertTrue(dialog.base_url_edit.isReadOnly())
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('Custom'))
        self.assertFalse(dialog.base_url_edit.isReadOnly())
        dialog.base_url_edit.setText('https://example.com/v1')
        dialog.model_combo.setCurrentText('custom-model')
        self.assertEqual(dialog.settings().ai_provider,'Custom')
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('DeepSeek'))
        self.assertEqual(dialog.api_key_edit.text(),'deepseek-test-key')
        self.assertEqual(dialog.model_combo.currentText(),'saved-model')
        dialog._model_timer.stop();dialog.close()

    def test_custom_url_migration(self):
        dialog=SettingsDialog(replace(default_settings(),deepseek_api_key='',deepseek_base_url='https://example.com/v1'))
        self.assertEqual(dialog.settings().ai_provider,'Custom')
        self.assertEqual(dialog.settings().deepseek_base_url,'https://example.com/v1')
        dialog.close()

    def test_background_discovery_and_stale_response(self):
        dialog=self.dialog();dialog.api_key_edit.setText('test-key')
        with patch('ui.widgets.model_worker.list_models',return_value=['saved-model','new-model']):
            dialog._discover_models()
            workers=self.app.findChildren(ModelWorker)
            self.assertTrue(workers)
            for worker in workers: self.assertTrue(worker.wait(1000))
            self.app.processEvents()
        self.assertEqual([dialog.model_combo.itemText(i) for i in range(dialog.model_combo.count())],['saved-model','new-model'])
        old=provider_config_from_settings(dialog.settings())
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('OpenAI'))
        dialog._models_discovered(old,['wrong-provider-model'],'')
        self.assertEqual(dialog.model_combo.count(),0)
        dialog._model_timer.stop();dialog.close()

    def test_failed_or_unsupported_discovery_preserves_manual_model(self):
        dialog=self.dialog();config=provider_config_from_settings(dialog.settings());dialog._model_request=config
        dialog._models_discovered(config,None,'offline')
        self.assertIn('offline',dialog.model_status.text())
        self.assertEqual(dialog.model_combo.currentText(),'saved-model')
        dialog._models_discovered(config,None,'')
        self.assertEqual(dialog.model_combo.currentText(),'saved-model')
        dialog.close()

    def test_claude_models_and_messages_protocol(self):
        config=ProviderConfig('Claude','test-key',PROVIDER_URLS['Claude'],'claude-test',0.2,10)
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps({'data':[{'id':'claude-test'}]}).encode()
        with patch('ai.providers.request.urlopen',return_value=response) as send:
            self.assertEqual(list_models(config),['claude-test'])
            req=send.call_args.args[0]
            self.assertEqual(req.get_header('X-api-key'),'test-key')
            self.assertEqual(req.full_url,PROVIDER_URLS['Claude']+'/models?limit=1000')
        response.read.return_value=json.dumps({'content':[{'type':'text','text':'{"ok":true}'}]}).encode()
        with patch('ai.providers.request.urlopen',return_value=response) as send:
            self.assertEqual(complete('Test',config),'{"ok":true}')
            req=send.call_args.args[0];payload=json.loads(req.data)
            self.assertEqual(req.full_url,PROVIDER_URLS['Claude']+'/messages')
            self.assertIn('system',payload);self.assertIn('max_tokens',payload)
            self.assertNotIn('response_format',payload)

if __name__=='__main__': unittest.main()
