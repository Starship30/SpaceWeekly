import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from services import updates
from services.workspace_import import import_data,prepare_release
from services.cancellation import CancellationToken,TaskCancelledError


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(prefix='安装 更新 ',dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();p=patch.object(updates,'app_data_directory',return_value=self.root/'app-data');p.start();self.addCleanup(p.stop)

    def test_installer_splash_has_its_own_background_and_readable_text(self):
        from tools.build_installer import installer_splash
        from PIL import Image
        path=self.root/'安装 进度.png';installer_splash(path)
        with Image.open(path) as picture:
            self.assertEqual(picture.size,(640,280))
            self.assertEqual(picture.mode,'RGB')
            self.assertEqual(picture.getpixel((320,250)),(255,255,255))
            colors=picture.crop((40,94,580,140)).getcolors(640*280)
            self.assertTrue(any(color==(34,43,61) for count,color in colors))

    def test_update_preferences_and_pending_survive_reload(self):
        self.assertTrue(updates.load()['automatic']);updates.save(automatic=False,pending={'Version':'3.1.1'})
        self.assertFalse(updates.load()['automatic']);self.assertEqual(updates.load()['pending']['Version'],'3.1.1')

    def test_framework_installs_standalone_entry_on_install_and_update(self):
        import sys
        from services import release_startup
        sdk=Mock();builder=sdk.App.return_value
        for name in ('set_auto_apply_on_startup','on_after_install_fast_callback','on_after_update_fast_callback'):
            getattr(builder,name).return_value=builder
        with patch.dict(sys.modules,velopack=sdk),patch.object(sys,'frozen',True,create=True),patch.object(sys,'argv',['SpaceWeekly.exe']):
            self.assertFalse(release_startup.early_startup())
        builder.on_after_install_fast_callback.assert_called_once_with(release_startup.install_uninstaller)
        builder.on_after_update_fast_callback.assert_called_once_with(release_startup.install_uninstaller)
        builder.run.assert_called_once()

    def test_development_does_not_require_sdk_or_spawn_updates(self):
        self.assertFalse(updates.installed())
        with self.assertRaises(ValueError):updates.run_job('check',CancellationToken())
        with self.assertRaises(ValueError):updates.handoff_uninstall()

    def test_standalone_uninstall_is_built_and_used_by_the_application(self):
        import os,sys,subprocess
        from tools.build_installer import compile_uninstaller,ROOT
        from services.release_startup import install_uninstaller
        root=self.root/'程序 安装';(root/'current').mkdir(parents=True)
        compile_uninstaller(root/'current',self.root,dict(version='3.1.0',package_id='Starship30.SpaceWeekly.QA'))
        (root/'Update.exe').write_bytes(b'fixture: never executed')
        with patch.object(updates,'installed',return_value=True),patch.object(sys,'executable',str(root/'current/SpaceWeekly.exe')):
            install_uninstaller()
            self.assertEqual((root/'Uninstall.exe').read_bytes(),(root/'current/Uninstall.exe').read_bytes())
            with patch.object(updates.subprocess,'Popen') as spawn:
                updates.handoff_uninstall();self.assertEqual(spawn.call_args.args[0],[str(root/'Uninstall.exe'),'--confirmed'])
        compiler=Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        probe=self.root/'UninstallProbe.exe'
        subprocess.run([str(compiler),'/nologo','/codepage:65001','/platform:x64','/target:exe','/main:UninstallProbe',
            '/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/out:'+str(probe),
            str(ROOT/'tools/windows/Uninstall.cs'),str(self.root/'Uninstall.Identity.cs'),str(ROOT/'tools/windows/UninstallProbe.cs')],check=True)
        output=subprocess.check_output([str(probe),str(root)],text=True,encoding='utf-8')
        self.assertIn(str(root/'Update.exe'),output);self.assertIn('--silent uninstall',output)

    def test_local_discovery_cannot_retrust_a_modified_package(self):
        from types import SimpleNamespace
        import hashlib,sys
        package=self.root/'install/packages/full.nupkg';package.parent.mkdir(parents=True);package.write_bytes(b'original package')
        known={k:'' for k in updates.ASSET_KEYS};known.update(Version='3.1.1',PackageId='app',FileName='full.nupkg',SHA256=hashlib.sha256(package.read_bytes()).hexdigest(),Size=package.stat().st_size)
        updates.save(pending=known);mgr=Mock();mgr.get_update_pending_restart.return_value=SimpleNamespace(**dict(known,SHA256=''))
        with patch.object(sys,'executable',str(self.root/'install/current/SpaceWeekly.exe')),patch.object(updates,'asset',side_effect=lambda value:SimpleNamespace(**value)):
            self.assertEqual(updates.checked_pending(mgr).SHA256,known['SHA256'])
            package.write_bytes(b'modified package')
            with self.assertRaises(ValueError):updates.checked_pending(mgr)

    def test_native_base_without_checksum_falls_back_to_verified_full_package(self):
        import sys
        sdk=Mock();target={k:'' for k in updates.ASSET_KEYS}
        target.update(PackageId='app',Version='3.1.1',FileName='target.nupkg',SHA256='a'*64,Size=100)
        with patch.dict(sys.modules,velopack=sdk),patch.object(updates,'profile',return_value={'package_id':'app'}):
            updates.info(dict(TargetFullRelease=target,BaseRelease=dict(target,SHA256=''),DeltasToTarget=[target]))
            self.assertEqual(sdk.UpdateInfo.call_args.args[1:],([],False,None))

    def test_official_github_source_has_no_token_and_excludes_prereleases(self):
        import sys
        sdk=Mock();profile=dict(package_id='Starship30.SpaceWeekly',channel='win-x64',qa=False)
        with patch.dict(sys.modules,velopack=sdk),patch.object(updates,'profile',return_value=profile):
            updates.manager();sdk.GithubSource.assert_called_once_with(updates.REPOSITORY,access_token=None,prerelease=False)
            sdk.UpdateOptions.assert_called_once_with(False,10,'win-x64')
            value={k:'' for k in updates.ASSET_KEYS};value.update(PackageId=profile['package_id'],Version='3.1.1-beta.1',FileName='valid.nupkg')
            with self.assertRaises(ValueError):updates.asset(value)
            value.update(Version='3.1.1',FileName='../outside.nupkg')
            with self.assertRaises(ValueError):updates.asset(value)

    def test_backed_up_import_rebases_assets_and_preserves_secrets_and_times(self):
        source=self.root/'旧 便携版';source.mkdir();destination=self.root/'导入 工作区'
        (source/'config.json').write_text(json.dumps(dict(deepseek_api_key='fixture-private-canary',output_dir=str(source/'output'))))
        (source/'output').mkdir();(source/'output/原声.wav').write_bytes(b'fixture not real audio')
        (source/'config').mkdir();(source/'config/cache-maintenance.json').write_text('{"last_check":100,"last_clean":20}')
        (source/'config/production-session.json').write_text(json.dumps({'audio':str(source/'output/原声.wav')}))
        (source/'SpaceWeekly.exe').write_bytes(b'program must not migrate')
        result=import_data(source,destination,CancellationToken());self.assertEqual(result,str(destination))
        self.assertTrue(source.is_dir());self.assertFalse((destination/'SpaceWeekly.exe').exists())
        self.assertEqual(json.loads((destination/'config.json').read_text(encoding='utf-8'))['deepseek_api_key'],'fixture-private-canary')
        self.assertEqual(json.loads((destination/'config/production-session.json').read_text(encoding='utf-8'))['audio'],str(destination/'output/原声.wav'))
        self.assertEqual(json.loads((destination/'config/cache-maintenance.json').read_text())['last_check'],100)
        record=next((destination/'config').glob('import-record-*.json'))
        self.assertTrue(Path(json.loads(record.read_text(encoding='utf-8'))['backup']).is_file())
        with self.assertRaises(ValueError):import_data(source,destination,CancellationToken())

    def test_import_cancel_and_space_failure_never_delete_source(self):
        source=self.root/'source';source.mkdir();(source/'config.json').write_text('{}');token=CancellationToken();token.cancel()
        with self.assertRaises(TaskCancelledError):import_data(source,self.root/'new',token)
        self.assertTrue((source/'config.json').is_file())
        with patch('services.workspace_import.shutil.disk_usage',return_value=Mock(free=0)),self.assertRaises(ValueError):import_data(source,self.root/'new',CancellationToken())

    def test_newer_schema_is_rejected_without_changing_data(self):
        p=self.root/'config';p.mkdir();marker=p/'workspace-format.json';marker.write_text('{"schema":999}')
        with self.assertRaises(ValueError):prepare_release(self.root)
        self.assertEqual(marker.read_text(),'{"schema":999}')
