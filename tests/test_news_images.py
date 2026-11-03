"""Fixture extraction/download contracts; live RSS/API evidence is recorded separately."""
import json
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch,MagicMock
from dataclasses import asdict
import feedparser
from PIL import Image
import resources
from models.news import News,news_id
from models.article import Article
from services import news_images as images,production
from services.cancellation import CancellationToken,TaskCancelledError
from downloader import client


class NewsImageTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True);temp=tempfile.TemporaryDirectory(prefix='新闻 图片 ',dir='build');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();p=patch.object(resources,'_WORKSPACE_PATH',self.root);p.start();self.addCleanup(p.stop)
        self.token=CancellationToken();self.article=Article(News('Fixture','中文标题 / : ?','2026-11-01','摘要','https://example.org/news/1'),'正文','fixture')
        self.png=self.root/'原图 空格.png';Image.new('RGB',(96,64),'red').save(self.png)

    def test_entry_media_thumbnail_enclosure_html_relative_and_no_channel_logo(self):
        feed=feedparser.parse('''<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><title>fixture</title><image><url>https://example.org/logo.png</url></image><item><title>news</title><link>https://example.org/a/item</link><media:content url="../main" type="image/jpeg"/><media:thumbnail url="/thumb"/><enclosure url="/enclosed" type="image/png"/><description><![CDATA[<img src="/summary">]]></description></item></channel></rss>''')
        rows=images.feed_candidates(feed.entries[0],'https://example.org/a/item')
        self.assertEqual([r['url'] for r in rows],['https://example.org/main','https://example.org/thumb','https://example.org/enclosed','https://example.org/summary'])
        self.assertNotIn('logo',str(rows));self.assertEqual(images.feed_candidates({},'https://example.org'),[])
        self.assertEqual(images.api_candidates({'image_url':'/api.jpg'},'https://example.org/news')[0]['via'],'api_image_url')
        self.assertEqual(images.api_candidates({},'https://example.org/news'),[])
        self.assertEqual(images.api_candidates({'image_url':None},'https://example.org/news'),[])

    def test_page_fallback_scoped_body_og_relative_and_unsafe_urls(self):
        html='<header><img src="/logo"></header><meta property="og:image" content="../hero"><article><figure><img src="detail"><figcaption>Photo: NASA</figcaption></figure></article><script>ignored</script>'
        rows=images.page_candidates(html,'https://example.org/a/news');self.assertEqual(rows[0]['url'],'https://example.org/hero')
        self.assertEqual(rows[1]['credit'],'Photo: NASA');self.assertNotIn('/logo',str(rows))
        self.assertEqual(images.candidates([{'url':u} for u in ['file:///a','data:image/png','http://127.0.0.1/a','https://user:pass@example.org/img']],''),[])

    def test_decoded_content_dedup_shared_associations_persistence_and_local_import(self):
        self.article.news.images=images.api_candidates({'image_url':'/no-extension'},self.article.news.url)
        def download(url,path,token,**kw):path.write_bytes(self.png.read_bytes())
        with patch.object(images,'download_to',side_effect=download) as get:
            first=images.acquire(self.article,self.token);images.acquire(self.article,self.token);self.assertEqual(get.call_count,1)
            second=Article(News('Fixture','Second','2026-11-01','','https://example.org/news/2',self.article.news.images),'','fixture')
            images.acquire(second,self.token)
        self.assertEqual(first['format'],'PNG');self.assertEqual(len(images.load()['images']),1);self.assertEqual(len(images.load()['news']),2)
        self.assertEqual(first['path'],'news-images/'+first['sha256']+'.png')
        bundle=dict(articles=[self.article],summaries={},assessments={})
        restored=production.restore_news(production.serialize_news(bundle));self.assertEqual(restored['articles'][0].news.images,self.article.news.images)
        self.assertTrue(images.thumbnail(news_id(self.article.news.url)).is_file())
        images.acquire(self.article,self.token,local=self.png);self.assertEqual(len(images.load()['images']),1)
        self.assertTrue(any(r['via']=='local_import' and r['url'] is None for r in images.selected(news_id(self.article.news.url))['origins']))
        with patch.object(resources,'_WORKSPACE_PATH',self.root/'其他工作区'):self.assertIsNone(images.selected(news_id(self.article.news.url)))
        self.assertIsNotNone(images.selected(news_id(self.article.news.url)))

    def test_invalid_response_failure_cancel_and_resume_keep_formal_image(self):
        images.acquire(self.article,self.token,local=self.png);first=images.selected(news_id(self.article.news.url))
        self.article.news.images=[dict(url='https://example.org/bad',via='api')]
        def broken(url,path,token,**kw):path.write_text('<html>not image</html>')
        with patch.object(images,'download_to',side_effect=broken):self.assertIsNone(images.acquire(self.article,self.token,candidate_url='https://example.org/bad'))
        self.assertEqual(images.selected(news_id(self.article.news.url))['sha256'],first['sha256'])
        def cancel(url,path,token,**kw):path.write_bytes(b'partial');token.cancel();token.check()
        with patch.object(images,'download_to',side_effect=cancel),self.assertRaises(TaskCancelledError):images.acquire(self.article,self.token,candidate_url='https://example.org/bad')
        self.assertFalse(list((self.root/'cache/news_image_downloads').glob('*.part')))
        self.assertTrue(Path(first['absolute_path']).exists())
        images.acquire(self.article,CancellationToken(),local=self.png);self.assertEqual(images.load()['news'][news_id(self.article.news.url)]['status'],'ready')

    def test_bounded_binary_request_retries_progress_and_size(self):
        response=MagicMock();response.__enter__.return_value=response;response.status_code=200
        response.headers={'Content-Length':'4','Content-Type':'text/html'};response.iter_content.return_value=[b'data']
        progress=[]
        with patch.object(client,'_session') as session:
            session.return_value.get.return_value=response
            client.download_to('https://example.org/x',self.root/'x',self.token,progress=lambda n,t:progress.append((n,t)))
            self.assertEqual(progress,[(4,4)]);self.assertFalse(session.return_value.get.call_args.kwargs['allow_redirects'])
            with self.assertRaises(ValueError):client.download_to('https://example.org/x',self.root/'x',self.token,max_bytes=3)

    def test_one_page_fallback_no_image_is_not_collection_failure(self):
        def html(url,path,token,**kw):path.write_text('<article>no image</article>')
        with patch.object(images,'download_to',side_effect=html) as get:
            images.collect([self.article],self.token);self.assertEqual(get.call_count,1)
        row=images.load()['news'][news_id(self.article.news.url)];self.assertEqual(row['status'],'missing');self.assertFalse(row['assets'])

    def test_image_can_be_disabled_without_deleting_or_redownloading(self):
        identifier=news_id(self.article.news.url)
        asset=images.acquire(self.article,self.token,local=self.png)
        images.set_enabled(self.article,False)
        self.assertIsNone(images.selected(identifier))
        self.assertEqual(images.selected(identifier,include_disabled=True)['sha256'],asset['sha256'])
        self.assertTrue(images.thumbnail(identifier,include_disabled=True).is_file())
        self.assertFalse(images.load()['news'][identifier]['use_image'])
        with patch.object(images,'download_to',side_effect=AssertionError('Disabled images must not auto-download')):
            images.collect([self.article],self.token)
        self.assertTrue(Path(asset['absolute_path']).is_file())
        replacement=self.root/'自己寻找的 配图.png';Image.new('RGB',(100,80),'blue').save(replacement)
        images.acquire(self.article,self.token,local=replacement)
        self.assertIsNone(images.selected(identifier))
        images.set_enabled(self.article,True)
        self.assertNotEqual(images.selected(identifier)['sha256'],asset['sha256'])
        self.assertTrue(Path(asset['absolute_path']).exists())

    def test_disabling_during_download_is_not_undone_by_completion(self):
        self.article.news.images=images.api_candidates({'image_url':'/photo'},self.article.news.url)
        def download(url,path,token,**kwargs):
            images.set_enabled(self.article,False);path.write_bytes(self.png.read_bytes())
        with patch.object(images,'download_to',side_effect=download):
            self.assertIsNotNone(images.acquire(self.article,self.token))
        self.assertIsNone(images.selected(news_id(self.article.news.url)))

    def test_dedup_retains_candidate_from_second_source_without_changing_identity(self):
        from services.weekly_report import _deduplicate_articles
        other=Article(News('API','Another title','2026-11-01','','https://example.org/news/1',images.api_candidates({'image_url':'/photo'},self.article.news.url)),'','API')
        rows=_deduplicate_articles([self.article,other],self.token)
        self.assertEqual(len(rows),1);self.assertIs(rows[0],self.article);self.assertEqual(rows[0].news.images[0]['url'],'https://example.org/photo')

    def test_old_database_migrates_images_without_overwriting_existing_news(self):
        import sqlite3
        from database import sqlite
        path=self.root/'旧资料 新闻.db'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE articles(id INTEGER PRIMARY KEY,source TEXT,title TEXT,published TEXT,summary TEXT,url TEXT UNIQUE,body TEXT,parser TEXT,created_at TEXT)')
        connection.close()
        with patch.object(sqlite,'DATABASE_PATH',path):
            sqlite.initialize_database();self.assertTrue(sqlite.save_article(self.article))
            self.article.news.images=images.api_candidates({'image_url':'/new'},self.article.news.url)
            self.article.news.title='changed import title'
            self.assertFalse(sqlite.save_article(self.article));restored=sqlite.get_articles()[0]
            self.assertNotEqual(restored.news.title,self.article.news.title);self.assertEqual(len(restored.news.images),1)


if __name__=='__main__':unittest.main()
