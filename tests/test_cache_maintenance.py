"""Controlled clock tests; only isolated workspace files are ever removed."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import resources
from services import cache_maintenance as cache
from services.cancellation import CancellationToken


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True);temp=tempfile.TemporaryDirectory(prefix='清理 缓存 ',dir='build');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();p=patch.object(resources,'_WORKSPACE_PATH',self.root);p.start();self.addCleanup(p.stop)
        self.now=2000000000;self.token=CancellationToken()

    def file(self,relative,age=4):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture cache')
        timestamp=self.now-age*cache.DAY;os.utime(path,(timestamp,timestamp));cache.touch(path,now=timestamp);return path

    def test_due_snooze_disable_restart_and_independent_retention(self):
        cache.update(last_check=self.now,interval_days=3,retention_days=5)
        self.assertFalse(cache.due(self.now+3*cache.DAY-1));self.assertTrue(cache.due(self.now+3*cache.DAY))
        cache.reminded(self.now+3*cache.DAY);self.assertFalse(cache.due(self.now+4*cache.DAY))
        cache.snooze(self.now+3*cache.DAY);self.assertTrue(cache.due(self.now+4*cache.DAY));self.assertEqual(cache.load()['retention_days'],5)
        cache.update(enabled=False);self.assertFalse(cache.due(self.now+100*cache.DAY))
        self.assertEqual(json.loads((self.root/'config/cache-maintenance.json').read_text())['last_check'],self.now)

    def test_scan_does_not_touch_use_time_and_no_cache_produces_no_rows(self):
        self.assertEqual(cache.scan(self.token,now=self.now)['count'],0)
        p=self.file('cache/news_thumbnails/'+('a'*64)+'.png');before=p.stat().st_mtime_ns
        first=cache.scan(self.token,now=self.now);second=cache.scan(self.token,now=self.now+10)
        self.assertEqual(first['count'],1);self.assertEqual(second['count'],1);self.assertEqual(p.stat().st_mtime_ns,before)
        self.assertEqual(cache.load()['files'][str(p.relative_to(self.root))]['last_used'],self.now-4*cache.DAY)
        with patch.object(resources,'_WORKSPACE_PATH',self.root/'其他工作区'):
            self.assertEqual(cache.load()['files'],{})

    def test_formal_models_outputs_unknown_cache_refs_and_running_jobs_are_protected(self):
        for relative in ('news-images/photo.png','models/model.bin','config/production-session.json','translator/dictionary.json','output/movie.mp4','cache/roundup_audio/old.wav','cache/custom/user.txt'):
            p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
        keep=self.file('cache/news_pcm/'+('b'*64)+'/audio.wav')
        (self.root/'config/production-session.json').write_text(json.dumps({'audio':{'sha256':'b'*64}}))
        self.assertEqual(cache.scan(self.token,now=self.now)['count'],0)
        p=self.file('cache/news_thumbnails/'+('c'*64)+'.png')
        with cache.using_media():self.assertEqual(cache.scan(self.token,now=self.now)['count'],0)
        rows=cache.scan(self.token,now=self.now)['files'];self.assertEqual(len(rows),1)
        result=cache.clean(rows,self.token,now=self.now,extra=[str(p)]);self.assertEqual(result['deleted'],0)
        self.assertTrue(keep.exists());self.assertTrue((self.root/'news-images/photo.png').exists())

    def test_recheck_new_use_changed_file_partial_failure_and_cancel(self):
        a=self.file('cache/news_thumbnails/'+('a'*64)+'.png');b=self.file('cache/news_thumbnails/'+('b'*64)+'.png')
        rows=cache.scan(self.token,now=self.now)['files'];original=Path.unlink
        def denied(path,*args,**kw):
            if path==b:raise PermissionError('fixture occupied')
            return original(path,*args,**kw)
        with patch.object(Path,'unlink',denied):result=cache.clean(rows,self.token,now=self.now)
        self.assertEqual(result['deleted'],1);self.assertEqual(result['bytes'],len(b'fixture cache'));self.assertEqual(len(result['failed']),1);self.assertTrue(b.exists())
        self.assertEqual(cache.load()['last_result']['failed'],result['failed'])
        rows=cache.scan(self.token,now=self.now)['files'];cache.touch(b,now=self.now)
        result=cache.clean(rows,self.token,now=self.now);self.assertEqual(result['deleted'],0)
        self.token.cancel();result=cache.clean(rows,self.token,now=self.now);self.assertTrue(result['cancelled']);self.assertTrue(b.exists())

    def test_legacy_cache_waits_full_period_and_symlinks_cannot_escape(self):
        path=self.root/('cache/news_thumbnails/'+('d'*64)+'.png');path.parent.mkdir(parents=True);path.write_bytes(b'legacy')
        os.utime(path,(self.now-10*cache.DAY,)*2)
        self.assertEqual(cache.scan(self.token,now=self.now)['count'],0)
        self.assertEqual(cache.scan(self.token,now=self.now+3*cache.DAY)['count'],1)
        self.assertIsNone(cache.managed(self.root/'cache/news_thumbnails/../../config/settings.json'))
        rows=cache.scan(self.token,now=self.now+3*cache.DAY)['files'];rows[0]['path']='../outside.png'
        result=cache.clean(rows,self.token,now=self.now+3*cache.DAY);self.assertEqual(result['deleted'],0)

    def test_inventory_lists_formal_files_and_only_deletes_confirmed_owned_files(self):
        export=self.file('output/SpaceWeekly_2026-11-04_001.premiere.json')
        export.write_text('{"protocol_version":"1.0","items":[]}')
        video=self.file('video-downloads/已下载.mp4')
        record=self.root/'video-downloads/2026-11-04/metadata.json'
        record.parent.mkdir();record.write_text(json.dumps(dict(status='completed',original_url='https://example.com/',
                                                               file_path=str(video))),encoding='utf8')
        unknown=self.file('output/工作区.json')
        keep=self.file('cache/news_thumbnails/'+('f'*64)+'.png')
        result=cache.scan(self.token,now=self.now,extra=[str(video)])
        by_kind={r['category']:r for r in result['inventory']}
        self.assertEqual(set(by_kind),{'exports','downloads','thumbnail'})
        self.assertEqual(result['files'],[by_kind['thumbnail']])
        self.assertTrue(by_kind['downloads']['referenced'])
        self.assertFalse(by_kind['exports']['referenced'])
        self.assertEqual(cache.clean([by_kind['downloads']],self.token,now=self.now,extra=[str(video)],allow_formal=True)['deleted'],0)
        self.assertEqual(cache.clean([by_kind['downloads']],self.token,now=self.now,allow_formal=True,allow_referenced=True,
                                     extra=[str(video)],in_use=[str(video)])['deleted'],0)
        self.assertEqual(cache.clean([by_kind['downloads']],self.token,now=self.now,allow_formal=True,allow_referenced=True,
                                     extra=[str(video)])['deleted'],1)
        self.assertTrue(unknown.is_file() and keep.is_file() and export.is_file())


if __name__=='__main__':unittest.main()
