"""Deployment boundaries and failure tests; mocked installers never count as model validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace

from services.cancellation import CancellationToken,TaskCancelledError
from services import voice_deployment as d,voice_storage as s,voice_download as net


class Response:
    def __init__(self,chunks,status=200,headers=None):self.chunks=chunks;self.status_code=status;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def iter_content(self,size):yield from self.chunks


class VoiceDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='语音 部署 ',dir='build');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'独立环境 with spaces';self.token=CancellationToken()
        self.registry=patch('services.voice_storage.registry_path',return_value=self.base/'registry.json');self.registry.start();self.addCleanup(self.registry.stop)
        self.program=patch('services.voice_storage.program_directory',return_value=self.base/'program');self.program.start();self.addCleanup(self.program.stop)

    def test_catalog_pins_two_independent_environments_and_cross_source_snapshots(self):
        value=d.catalog();request=d.plan(self.root,'cpu')
        self.assertEqual(request['python'],'3.12.12')
        tts=d.expected_packages(d.lock_path('tts','cpu'));align=d.expected_packages(d.lock_path('align','cpu'))
        self.assertEqual(tts['transformers'],'4.57.3');self.assertEqual(align['transformers'],'4.57.6')
        self.assertEqual(tts['torch'],'2.9.1+cpu')
        self.assertEqual(d.expected_packages(d.lock_path('tts','cu128'))['torch'],'2.9.1+cu128')
        self.assertEqual(value['models']['lite']['repo'],'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice')
        for model in request['models'].values():
            self.assertEqual(len(model['revision']),40)
            for file in model['files']:
                self.assertEqual(len(file['modelscope_revision']),40);self.assertEqual(len(file['sha256']),64)
        self.assertGreater(len(tts),70);self.assertGreater(len(align),70)

    def test_installed_root_includes_siblings_of_current_and_rejects_voice_data(self):
        import sys
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.base/'installed/current/SpaceWeekly.exe')):
            self.program.stop()
            self.assertEqual(s.program_directory(),self.base/'installed')
            with self.assertRaises(ValueError):s.validate_root(self.base/'installed/models')
            with self.assertRaises(ValueError):s.validate_voice_locations({'model':str(self.base/'installed/models')})
            s.validate_voice_locations({'python':str(self.root/'python.exe'),'model':str(self.root/'models')})
            self.program.start()

    def test_legacy_existing_runtime_can_import_when_utf8_locale_is_unavailable(self):
        import sys,locale
        from tools.qwen_align_worker import load_aligner_class
        sentinel=object()
        with patch.dict(sys.modules,{'qwen_asr':SimpleNamespace(Qwen3ForcedAligner=sentinel)}),patch('locale.setlocale',side_effect=locale.Error):
            self.assertIs(load_aligner_class(),sentinel)

    def test_failed_probe_reports_structured_cause_instead_of_only_stderr_warnings(self):
        from services.video_download import VideoDownloadError
        root=s.ensure_root(self.root);deployment=d.VoiceDeployment(root,self.token)
        def failed(*args,**kwargs):
            d.write_json(root/'checks/align-modules.json',dict(ready=False,error='具体模块检查失败'))
            raise VideoDownloadError('exit','only an unrelated warning on stderr')
        with patch.object(deployment,'command',side_effect=failed),patch.object(deployment,'log') as log:
            with self.assertRaisesRegex(ValueError,'展开详情'):deployment.probe('python.exe','align','cpu')
            self.assertIn('具体模块检查失败',log.call_args.args[0])

    def test_owned_unicode_path_and_single_operation_lock_survive_stale_state(self):
        root=s.ensure_root(self.root)
        self.assertEqual(json.loads((root/'ownership.json').read_text())['owner'],s.OWNER)
        with s.VoiceLease(root):
            with self.assertRaisesRegex(ValueError,'正在配置|进程使用'):
                with s.VoiceLease(root):pass
        with s.VoiceLease(root):pass
        d.write_json(root/'deployment.json',dict(owner=s.OWNER,status='configuring'))
        self.assertEqual(d.read_state(root)['status'],'interrupted')

    def test_updated_application_marks_old_scheme_for_check_without_download(self):
        root=s.ensure_root(self.root)
        d.write_json(root/'deployment.json',dict(owner=s.OWNER,status='ready',active=dict(version='older-scheme',tts=dict(ready=True))))
        with patch('services.voice_deployment.download') as download:
            state=d.read_state(root);download.assert_not_called()
        self.assertEqual(state['status'],'needs_check');self.assertTrue(state['active']['tts']['ready'])

    @unittest.skipUnless(__import__('os').name=='nt','Windows job ownership')
    def test_installer_child_exits_if_application_job_handle_is_lost(self):
        import subprocess,sys
        from services.video_download import _WindowsJob
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            job=_WindowsJob(child)
            if not job.handle:self.skipTest('Windows job assignment unavailable in this host')
            handle=job.handle;job.handle=None
            job.api.CloseHandle(handle)
            child.wait(timeout=5)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)

    def test_program_workspace_and_nonempty_user_directory_are_not_adopted(self):
        from resources import resource_path
        with self.assertRaises(ValueError):s.ensure_root(self.base/'program/voice')
        self.root.mkdir();(self.root/'user-data.txt').write_text('user file')
        with self.assertRaisesRegex(ValueError,'已有其他文件'):s.ensure_root(self.root)
        self.assertEqual((self.root/'user-data.txt').read_text(),'user file')

    def test_resume_real_byte_progress_and_no_repeated_valid_download(self):
        data=b'0123456789';checksum=hashlib.sha256(data).hexdigest();target=self.base/'文件.bin'
        partial=target.with_suffix('.bin.part');partial.write_bytes(data[:4])
        session=Mock();session.get.return_value=Response([data[4:7],data[7:]],206,{'Content-Range':'bytes 4-9/10'})
        updates=[]
        net.download('https://huggingface.co/fixed',target,len(data),checksum,self.token,lambda a,b:updates.append((a,b)),session=session)
        self.assertEqual(target.read_bytes(),data);self.assertEqual(session.get.call_args.kwargs['headers']['Range'],'bytes=4-')
        self.assertEqual(updates[0],(4,10));self.assertEqual(updates[-1],(10,10))
        session.reset_mock();net.download('https://modelscope.cn/same-content',target,len(data),checksum,self.token,session=session)
        session.get.assert_not_called()

    def test_cancel_retains_partial_and_source_switch_checks_same_final_hash(self):
        data=b'abcdef';checksum=hashlib.sha256(data).hexdigest();target=self.base/'model'
        def chunks():
            yield data[:3];self.token.cancel();yield data[3:]
        response=Response([]);response.iter_content=lambda size:chunks();session=Mock();session.get.return_value=response
        with self.assertRaises(TaskCancelledError):net.download('https://modelscope.cn/model',target,6,checksum,self.token,session=session)
        self.assertFalse(target.exists());self.assertEqual(target.with_suffix('.part').read_bytes(),data[:3])
        session.get.return_value=Response([data[3:]],206,{'Content-Range':'bytes 3-5/6'})
        net.download('https://huggingface.co/model',target,6,checksum,CancellationToken(),session=session)
        self.assertEqual(target.read_bytes(),data)

    def test_damage_and_wrong_content_range_never_create_a_usable_file(self):
        target=self.base/'weights';session=Mock();session.get.return_value=Response([b'bad'])
        token=Mock();token.wait.return_value=False
        with self.assertRaisesRegex(ValueError,'完整性'):net.download('https://huggingface.co/model',target,3,'0'*64,token,session=session)
        self.assertFalse(target.exists())
        target.with_suffix('.part').write_bytes(b'b')
        session.get.return_value=Response([b'ad'],206,{'Content-Range':'bytes 2-3/4'})
        with self.assertRaisesRegex(ValueError,'续传范围'):net.download('https://huggingface.co/model',target,3,'0'*64,token,session=session)
        self.assertFalse(target.exists())

    def test_network_failure_and_insufficient_space_preserve_existing_configuration(self):
        import requests
        target=self.base/'weights';session=Mock();session.get.side_effect=requests.ConnectionError('token=secret')
        token=Mock();token.wait.return_value=False
        with self.assertRaises(RuntimeError) as error:net.download('https://huggingface.co/file',target,1,'0'*64,token,session=session)
        self.assertNotIn('secret',str(error.exception))
        with patch('services.voice_download.shutil.disk_usage',return_value=SimpleNamespace(free=1)),self.assertRaisesRegex(ValueError,'空间不足'):
            net.download('https://modelscope.cn/file',target,2,'0'*64,self.token,session=session)
        root=s.ensure_root(self.root);old=dict(ready=True,python='untouched')
        d.write_json(root/'deployment.json',dict(owner=s.OWNER,status='ready',active=old))
        hardware=dict(supported=True,disk_free_bytes=1,gpus=[],memory={})
        with patch('services.voice_deployment.detect',return_value=hardware),self.assertRaisesRegex(ValueError,'空间不足'):
            d.VoiceDeployment(root,self.token).run(device='cpu')
        state=d.read_state(root);self.assertEqual(state['active'],old);self.assertEqual(state['status'],'failed')

    def test_remove_requires_owned_exact_child_and_preserves_models_and_user_paths(self):
        root=s.ensure_root(self.root);env=root/'environments/managed';env.mkdir(parents=True)
        owner=json.loads((root/'ownership.json').read_text(encoding='utf-8'))
        d.write_json(env/'spaceweekly-environment.json',dict(owner=s.OWNER,root=str(root),root_id=owner['id']))
        model=root/'models/user-model';model.mkdir(parents=True);(model/'weights').write_bytes(b'keep')
        outside=self.base/'user env';outside.mkdir();(outside/'python.exe').touch()
        with self.assertRaises(ValueError):s.remove_managed(root,str(outside))
        with self.assertRaises(ValueError):s.remove_managed(root,'models/user-model')
        data=json.loads((env/'spaceweekly-environment.json').read_text(encoding='utf-8'))
        d.write_json(env/'spaceweekly-environment.json',dict(data,root_id='another-owner'))
        with self.assertRaises(ValueError):s.remove_managed(root,'environments/managed')
        d.write_json(env/'spaceweekly-environment.json',data)
        s.remove_managed(root,'environments/managed');self.assertFalse(env.exists())
        self.assertTrue((outside/'python.exe').exists());self.assertEqual((model/'weights').read_bytes(),b'keep')

    def test_managed_environment_guard_blocks_while_deployment_owns_it(self):
        root=s.ensure_root(self.root);env=root/'environments/tts';(env/'Scripts').mkdir(parents=True);python=env/'Scripts/python.exe';python.touch()
        marker=json.loads((root/'ownership.json').read_text())
        d.write_json(env/'spaceweekly-environment.json',dict(owner=s.OWNER,root=str(root),root_id=marker['id']))
        with s.VoiceLease(root),self.assertRaises(ValueError):
            with s.environment_guard(python):pass
        with s.environment_guard(python):pass
        outside=self.base/'existing/Scripts/python.exe';outside.parent.mkdir(parents=True);outside.touch()
        with s.environment_guard(outside):pass

    def test_partial_capability_result_is_not_promoted_to_both_ready(self):
        class FixtureDeployment(d.VoiceDeployment):
            def bootstrap(self,r):return self.root/'uv.exe'
            def python(self,*a):return self.root/'python.exe'
            def environment(self,uv,py,request,role):return self.root/role/'python.exe'
            def models(self,*a,**k):return dict(tts='test-only',align='test-only')
            def verify(self,*a,**k):return dict(tts=dict(ready=True,python='fixture-not-real-inference'),align=dict(ready=False,error='fixture failure'))
        hardware=dict(supported=True,disk_free_bytes=100*d.GIB,gpus=[],memory={})
        with patch('services.voice_deployment.detect',return_value=hardware):
            state=FixtureDeployment(self.root,self.token).run(device='cpu')
        self.assertEqual(state['status'],'needs_check');self.assertFalse(state['active']['align']['ready'])
        self.assertTrue(state['active']['tts']['ready']);self.assertTrue((self.root/'deployment.json').exists())
        identity=state['request']['plan_id'];previous=deepcopy(state['profiles'][identity])
        previous['align']=dict(ready=True,python='last-working-environment')
        state['profiles'][identity]=previous;d.write_json(self.root/'deployment.json',state)
        with patch('services.voice_deployment.detect',return_value=hardware):
            state=FixtureDeployment(self.root,self.token).run(device='cpu')
        self.assertFalse(state['validation']['align']['ready'])
        self.assertEqual(state['profiles'][identity],previous)

    def test_legacy_tts_only_environment_can_be_checked_without_installing_aligner(self):
        python=self.base/'用户原环境 with spaces/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
        tts=dict(python=str(python),model='user model');align=dict(python='',model='')
        deployment=d.VoiceDeployment(self.root,self.token)
        result=dict(tts=dict(tts,ready=True),align=dict(align,ready=False,error='fixture: no alignment model'))
        with patch.object(deployment,'verify',return_value=result) as verify,patch.object(deployment,'command') as command:
            state=deployment.run_existing(tts,align)
            command.assert_not_called();self.assertFalse(verify.call_args.kwargs['managed'])
        self.assertTrue(state['validation']['tts']['ready']);self.assertFalse(state['validation']['align']['ready'])
        self.assertEqual(state['status'],'needs_check');self.assertTrue(python.exists())

    def test_logs_and_diagnostics_redact_credentials_without_invalid_json(self):
        text='https://name:password@example.com/model?token=secret Authorization: BearerKey api_key=hidden'
        cleaned=net.redact(text)
        for value in ('password@','token=secret','BearerKey','api_key=hidden'):self.assertNotIn(value,cleaned)
        root=s.ensure_root(self.root)
        d.write_json(root/'deployment.json',dict(owner=s.OWNER,status='failed',message=text,password='structured-secret',authorization='Bearer structured-token'))
        (root/'deployment.log').write_text('Earlier session: '+text,encoding='utf-8')
        target=self.base/'诊断.json';d.diagnostics(root,target)
        data=json.loads(target.read_text(encoding='utf-8'));self.assertNotIn('BearerKey',data['message'])
        self.assertIn('Earlier session',data['deployment_log'])
        self.assertNotIn('structured-secret',target.read_text(encoding='utf-8'));self.assertNotIn('structured-token',target.read_text(encoding='utf-8'))
        quoted=net.redact('{"Authorization": "Bearer private-value", "password": "has spaces"}')
        self.assertNotIn('private-value',quoted);self.assertNotIn('has spaces',quoted)
        self.assertNotIn('actual-secret',net.redact('Authorization: Bearer actual-secret'))

    def test_cancelled_deployment_is_resumable_and_rechecks_instead_of_trusting_state(self):
        class CancelDeployment(d.VoiceDeployment):
            def bootstrap(self,request):
                (self.root/'downloads').mkdir(exist_ok=True)
                (self.root/'downloads/partial.part').write_bytes(b'reusable')
                self.token.cancel();self.token.check()
        hardware=dict(supported=True,disk_free_bytes=100*d.GIB,gpus=[],memory={})
        with patch('services.voice_deployment.detect',return_value=hardware):
            with self.assertRaises(TaskCancelledError):CancelDeployment(self.root,self.token).run(device='cpu')
            state=d.read_state(self.root);self.assertEqual(state['status'],'cancelled')
            self.assertEqual(state['stages']['detect']['status'],'cancelled')
            self.assertEqual(s.default_root(),self.root)
            token=CancellationToken()
            with patch.object(CancelDeployment,'bootstrap',side_effect=RuntimeError('second checked attempt')) as start:
                with self.assertRaises(RuntimeError):CancelDeployment(self.root,token).run(device='cpu')
                start.assert_called_once()
        self.assertEqual((self.root/'downloads/partial.part').read_bytes(),b'reusable')

    def test_compatible_environment_reused_across_model_sizes(self):
        root=s.ensure_root(self.root);request=d.plan(root,'cpu','standard')
        env=root/'environments/tts';(env/'Scripts').mkdir(parents=True);python=env/'Scripts/python.exe';python.touch()
        marker=json.loads((root/'ownership.json').read_text())
        d.write_json(env/'spaceweekly-environment.json',dict(owner=s.OWNER,root=str(root),root_id=marker['id']))
        deployment=d.VoiceDeployment(root,self.token)
        deployment.state=dict(active=dict(plan_id='another-model',lock_hashes=request['locks'],tts=dict(python=str(python),ready=True)),profiles={})
        with patch.object(deployment,'probe',return_value=dict(ready=True)) as probe,patch.object(deployment,'command') as command:
            self.assertEqual(deployment.environment('uv','python',request,'tts'),python)
            probe.assert_called_once();command.assert_not_called()
        self.assertTrue(json.loads((env/'spaceweekly-environment.json').read_text(encoding='utf-8'))['dependencies_ready'])

    def test_resume_space_reserves_remaining_models_instead_of_a_second_full_environment(self):
        root=s.ensure_root(self.root);request=d.plan(root,'cpu');pending=dict(plan_id=request['plan_id'])
        marker=json.loads((root/'ownership.json').read_text())
        for role in ('tts','align'):
            env=root/'environments'/role;(env/'Scripts').mkdir(parents=True);python=env/'Scripts/python.exe';python.touch()
            d.write_json(env/'spaceweekly-environment.json',dict(owner=s.OWNER,root=str(root),root_id=marker['id'],
                dependencies_ready=True,lock_hash=request['locks'][role]))
            pending[role]=dict(python=str(python))
        d.write_json(root/'deployment.json',dict(owner=s.OWNER,status='cancelled',pending=pending,active={}))
        resumed=d.plan(root,'cpu')
        self.assertEqual(resumed['environment_estimate_bytes'],d.GIB)
        self.assertEqual(resumed['required_free_bytes'],resumed['remaining_model_bytes']+2*d.GIB)

    def test_model_repair_downloads_only_damaged_files(self):
        root=s.ensure_root(self.root);good=b'good';bad=b'bad'
        files=[dict(path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest(),modelscope_revision='a'*40)
            for name,data in [('config.json',good),('model.safetensors',bad)]]
        model=dict(repo='Qwen/fixture-only',revision='b'*40,files=files)
        target=root/'models/fixture-only'/model['revision'];target.mkdir(parents=True)
        (target/'config.json').write_bytes(good);(target/'model.safetensors').write_bytes(b'corrupt')
        deployment=d.VoiceDeployment(root,self.token);deployment.state=dict(status='configuring',stages={})
        def retrieve(url,path,*args):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(bad)
        with patch('services.voice_deployment.download',side_effect=retrieve) as get:
            deployment.models(dict(models=dict(tts=model),model_bytes=len(good)+len(bad),source='modelscope'))
        self.assertEqual(get.call_count,1);self.assertEqual((target/'model.safetensors').read_bytes(),bad)
        receipt=target/'spaceweekly-model.json';stamp=receipt.stat().st_mtime_ns;content=receipt.read_bytes()
        with patch('services.voice_deployment.download') as get:
            deployment.models(dict(models=dict(tts=model),model_bytes=len(good)+len(bad),source='huggingface'),download_missing=False)
            get.assert_not_called()
        self.assertEqual(receipt.stat().st_mtime_ns,stamp);self.assertEqual(receipt.read_bytes(),content)


if __name__=='__main__':unittest.main()
