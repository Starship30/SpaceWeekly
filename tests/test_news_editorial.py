from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from models.article import Article
from models.news import News
from services import news_editorial as editorial, news_voice
from services.cancellation import CancellationToken
from services.settings import default_settings


def article(key,title,body,day=24,*,parser='fixture'):
    return Article(News('固定样本',title,f'2026-10-{day:02d}T06:00:00Z','',f'https://example.invalid/{key}'),body,parser)


def sample():
    return {
        'preview':article('preview','星舰第15次试飞预告：计划尝试双重回收','星舰计划在试飞中尝试回收一级助推器和飞船。',26),
        'live':article('live','星舰第15次试飞已起飞','星舰已起飞并进入飞行阶段。'),
        'result':article('result','星舰第15次试飞完成，飞船成功返回','星舰飞船成功返回，并被塔架捕获。一级助推器未能返回发射场。',25),
        'analysis':article('analysis','星舰第15次试飞复盘','飞船成功返回。任务团队确认飞船返回时保持姿态稳定。',26),
        'late':article('late','星舰第15次试飞完成后公布新资料','星舰飞船成功返回。团队在截稿后才公布新的回收数据。',30),
        'fourteen':article('fourteen','星舰第十四次试飞完成','星舰飞船在试飞结束后溅落。'),
        'ended':article('ended','星舰第16次试飞结束','飞行已经结束，任务团队尚未公布确认后的飞后结果。'),
        'future':article('future','星舰第17次试飞预告','星舰预计下个月进行下一次试飞。'),
        'missing':article('missing','机构发布事故调查报告','机构发布事故调查报告',parser='RSS Summary'),
        'enough':article('enough','任务小组发布事故调查通报','任务小组确认回收舱已安全着陆，故障原因仍在调查。'),
    }


def voyager_sample():
    return [
        article('v1','旅行者1号已达到距地球一光日','旅行者1号于10月24日达到距地球一光日。信号单程用时24小时。信号往返需48小时。'),
        article('v1-second','旅行者1号抵达距地球一光日里程碑','旅行者1号达到距地球一光日。信号单程需36小时。'),
        article('v2','旅行者2号预计明年达到距地球一光日','旅行者2号预计于2027年达到距地球一光日。'),
    ]


class EditorialTests(TestCase):
    def test_exact_headlines_from_different_sources_remain_available_for_the_event(self):
        from services.weekly_report import _deduplicate_articles
        first,second,_=voyager_sample()
        second.news.title=first.news.title;second.news.source='第二家媒体'
        self.assertEqual(_deduplicate_articles([first,second],CancellationToken()),[first,second])

    def test_voyager_milestone_dedup_conflicts_and_merge_of_distinct_events(self):
        first,second,plan=rows=voyager_sample()
        cutoff=datetime(2026,10,29,16,tzinfo=timezone.utc)
        output,events=editorial.recommend(rows,rows,[],cutoff)
        self.assertEqual(output,[first,plan])
        result=events[first.news.key]
        self.assertEqual(result['related'],[second.news.key])
        self.assertEqual(result['conflicts'],{'信号单程时间':'单程用时24小时 / 单程需36小时'})
        writing=editorial.writing_article(first,result)
        self.assertNotIn('单程用时24小时',writing.body)
        self.assertIn('信号往返需48小时',writing.body)
        self.assertNotIn('单程需36小时',''.join(q['quote'] for q in writing.evidence))
        combined=editorial.merge_preview([plan,first,second],events)
        self.assertEqual(combined['spoken'].count('旅行者1号'),1)
        self.assertIn('旅行者2号预计于2027年',combined['spoken'])
        self.assertIn('旅行者1号于10月24日达到',combined['spoken'])
        self.assertCountEqual([cite['url'] for cite in combined['evidence']],[row.news.url for row in rows])
        self.assertLess(combined['spoken'].index('旅行者1号'),combined['spoken'].index('旅行者2号'))

    def test_group_by_identity_and_stage_with_a_hard_cutoff(self):
        rows=sample();cutoff=datetime(2026,10,29,16,tzinfo=timezone.utc)
        # An updated preview stays a preview even when it contains a later recap.
        rows['preview'].body+='随后，飞船成功返回。'
        self.assertEqual(editorial.phase(rows['preview']),'preview')
        self.assertEqual(editorial.phase(rows['live']),'live')
        self.assertEqual(editorial.event_key(rows['fourteen']),'starship:14')
        output,events=editorial.recommend(list(rows.values()),list(rows.values()),[],cutoff)
        self.assertNotIn(rows['late'].news.key,[a.news.key for a in output])
        self.assertEqual([a.news.key for a in output if editorial.event_key(a)=='starship:15'],[rows['result'].news.key])
        self.assertEqual(events[rows['ended'].news.key]['phase'],'pending')
        self.assertEqual(events[rows['future'].news.key]['phase'],'preview')
        group=events[rows['result'].news.key]
        self.assertCountEqual(group['related'],[rows[key].news.key for key in ('preview','live','analysis')])
        self.assertTrue(any(s['url']==rows['analysis'].news.url and '姿态稳定' in s['quote'] for s in group['evidence']))

    def test_confirmed_launch_record_blocks_old_preview_without_a_result(self):
        preview=sample()['preview']
        launch=dict(english_name='Starship Flight 15',net='2026-10-25T04:00:00Z',status_id=3,ll2_id='fixture')
        _,events=editorial.recommend([preview],[preview],[launch],datetime(2026,10,29,16,tzinfo=timezone.utc))
        self.assertEqual(events[preview.news.key]['phase'],'pending')

    def test_conflicting_core_fact_is_not_replaced_by_unrelated_details(self):
        first=article('a','旅行者1号跨过一光日，信号单程24小时','任务团队将继续接收科学数据。')
        second=article('b','旅行者1号达到一光日时，信号单程36小时','团队在等下一批数据。')
        cutoff=datetime(2026,10,29,16,tzinfo=timezone.utc)
        output,events=editorial.recommend([first,second],[first,second],[],cutoff)
        draft=editorial.writing_article(output[0],events[output[0].news.key])
        with self.assertRaises(editorial.MissingFacts):editorial.require_material(draft)

    def test_voyager_validator_rejects_reusing_the_wrong_measurement(self):
        row=voyager_sample()[0]
        def request(*args,validator,**kwargs):
            validator(dict(spoken='旅行者1号已达到距地球一光日，信号单程需48小时。',
                           quotes=['旅行者1号于10月24日达到距地球一光日。','信号往返需48小时。']))
        with patch.object(news_voice,'request_json',side_effect=request):
            with self.assertRaisesRegex(ValueError,'引用口径'):news_voice.propose(row,default_settings(),CancellationToken())

    def test_model_cannot_save_dangling_attribution_and_may_report_missing_facts(self):
        row=sample()['enough'];token=CancellationToken()
        def reply(value):
            def request(*args,validator,**kwargs):validator(value);return value
            return request
        with patch.object(news_voice,'request_json',side_effect=reply(dict(spoken='任务小组确认回收舱已安全着陆。报告提到。',quotes=[row.body]))):
            with self.assertRaisesRegex(ValueError,'未完成'):news_voice.propose(row,default_settings(),token)
        with patch.object(news_voice,'request_json',side_effect=reply(dict(missing='回收地点'))):
            with self.assertRaises(editorial.MissingFacts):news_voice.propose(row,default_settings(),token)

    def test_retry_obeys_updated_metadata_in_the_original_fetcher(self):
        from services.weekly_report import _parse_article
        row=sample()['missing']
        with patch('services.weekly_report.download',return_value='<meta property="article:modified_time" content="2026-10-30T00:00:00Z">'):
            self.assertIsNone(_parse_article(row.news,CancellationToken(),cutoff=datetime(2026,10,29,16,tzinfo=timezone.utc)))
