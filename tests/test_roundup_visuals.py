"""Scheme B shares bounded task windows and paragraph timing with export."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.test_launch_roundup import context
from services import launch_roundup as roundup
from services.roundup_audio import segment_timing
from tools import roundup_visuals as visuals


class RoundupVisualsTests(unittest.TestCase):
    def draft(self,count=4):
        items=[context(i) for i in range(1,count+1)]
        for i,item in enumerate(items):
            item.update(net='2026-10-21T%02d:00:00Z'%(i%24),
                        provider='China Aerospace Science and Technology Corporation' if i%2==0 else 'SpaceX')
        return roundup.create_draft(dict(items=items,complete=False,start='2026-10-20',end='2026-10-29'),
                                    '2026-10-20','2026-10-29')

    def test_order_highlight_and_identity_are_shared_without_mutating_draft(self):
        draft=self.draft();segment=dict(kind='highlight',task_ids=['launch-2'],screen='existing screen',preview_seconds=2)
        original=deepcopy(draft);plan=visuals.scene_plan(draft,segment)
        self.assertEqual(plan[0]['active_ids'],['launch-2'])
        self.assertEqual(plan[0]['groups'][0]['label'],'中国方面')
        self.assertEqual(plan[0]['groups'][1]['label'],'美国 SpaceX 方面')
        self.assertEqual(draft,original)
        from services.roundup_style import affiliation
        for provider in ('CASC','中国航天科技集团公司','中国航天科技集团有限公司'):
            self.assertEqual(affiliation(dict(provider=provider))['label'],'中国方面')

    def test_many_tasks_use_fixed_windows_and_multi_task_scope(self):
        draft=self.draft(17);ids=[i['ll2_id'] for i in draft['snapshot']['items']]
        plan=visuals.scene_plan(draft,dict(task_ids=ids))
        self.assertGreater(len(plan),1)
        shown=[]
        for scene in plan:
            self.assertGreaterEqual(visuals._row_height(scene['groups']),visuals.ROW_MIN)
            self.assertEqual(scene['paragraph_task_count'],17)
            shown.append(scene['focus_id'])
        self.assertCountEqual(shown,ids)
        self.assertEqual(visuals.scene_index(3,0,9),0)
        self.assertEqual(visuals.scene_index(3,3,9),0)
        self.assertEqual(visuals.scene_index(3,6,18),1)
        self.assertEqual(visuals.scene_index(3,99,18),2)

    def test_sparse_and_long_names_render_at_video_size(self):
        draft=self.draft(1);draft['snapshot']['items'][0]['english_name']='Long mission code / '+('Existing long name '*35)
        segment=dict(kind='highlight',task_ids=['launch-1'],screen='原有屏幕文字',preview_seconds=2)
        picture=visuals.preview_frame(draft,segment)
        self.assertEqual(picture.size,(1920,1080))
        self.assertEqual(picture.tobytes(),visuals.pages(draft,segment)[0].tobytes())
        self.assertEqual(len(visuals.scene_plan(draft,segment)[0]['groups'][0]['task_ids']),1)

    def test_audio_duration_wins_and_stale_audio_uses_labelled_preview(self):
        draft=self.draft();segment=dict(preview_seconds=7)
        with patch('services.roundup_audio.current_audio',return_value=dict(duration_seconds=2.25)):
            self.assertEqual(segment_timing(draft,segment)['duration_seconds'],2.25)
            self.assertEqual(segment_timing(draft,segment)['timing_source'],'audio')
        with patch('services.roundup_audio.current_audio',return_value=None):
            self.assertEqual(segment_timing(draft,segment)['duration_seconds'],7)
            self.assertEqual(segment_timing(draft,segment)['timing_source'],'editable_preview')
            with self.assertRaises(ValueError):segment_timing(draft,dict(preview_seconds=float('nan')))

    def test_unknown_id_is_reported_instead_of_silently_dropping_highlight(self):
        with self.assertRaisesRegex(ValueError,'关联任务不存在'):
            visuals.scene_plan(self.draft(),dict(task_ids=['missing']))

    def test_payload_uses_evidence_and_never_mission_title(self):
        from services.roundup_style import payload_display
        item=dict(english_name='宣传任务名',payload='6 Chinese military “remote sensing” satellite of unknown purposes.')
        self.assertEqual(payload_display(item),'6颗遥感卫星\n具体用途未明')
        self.assertEqual(payload_display(dict(item,payload='')),'载荷资料待补充')
        self.assertEqual(payload_display(dict(item,payload='ActualPayload: source description')),'ActualPayload: source description')
        compact=payload_display(dict(item,payload='A batch of 27 satellites for the Starlink mega-constellation.'))
        self.assertTrue(compact.startswith('27颗'));self.assertTrue(compact.endswith('星座卫星'))
        self.assertNotIn('\n',compact)

    def test_recovery_is_only_for_reusable_rockets_and_not_capsules(self):
        from services.roundup_style import rocket_recovery
        for item in (dict(recovery=''),dict(rocket_reusable=False,recovery='第一级: 成功'),
                     dict(recovery='飞船 1 C123: 成功'),dict(recovery='Spacecraft 1: Successful'),dict(recovery='第一级: 不尝试回收')):
            self.assertEqual(rocket_recovery(item),'')
        self.assertEqual(rocket_recovery(dict(recovery='B1063: 成功 (ASDS · OCISLY)')),'B1063: 成功 (ASDS · OCISLY)')
        self.assertEqual(rocket_recovery(dict(rocket_reusable=True)),'回收待确认')
        self.assertEqual(rocket_recovery(dict(rocket_reusable=True,rocket_recovery='第一级: 不尝试回收')),'不尝试回收')

    def test_orbit_and_payload_share_row_without_changing_type_scale_or_data(self):
        draft=self.draft(1);item=draft['snapshot']['items'][0]
        item.update(orbit='Sun-Synchronous Orbit',payload='6 Chinese military “remote sensing” satellite of unknown purposes.')
        original=deepcopy(draft)
        entries={'Sun-Synchronous Orbit':dict(translation='用户确认的轨道名称',type='orbit')}
        with patch('services.launch_research.load_dictionary',return_value=entries),patch.object(visuals,'_text',wraps=visuals._text) as paint:
            visuals.preview_frame(draft,dict(task_ids=[item['ll2_id']],preview_seconds=12))
        calls=paint.call_args_list
        label=next(c for c in calls if c.args[1]=='目标轨道')
        value=next(c for c in calls if c.args[1]=='用户确认的轨道名称')
        payload=next(c for c in calls if c.args[1]=='6颗遥感卫星')
        self.assertEqual(label.args[3],22);self.assertEqual(value.args[3],36)
        self.assertEqual(value.args[2][1],payload.args[2][1])
        self.assertLessEqual(payload.args[2][0]+payload.args[2][2],value.args[2][0]-40)
        self.assertLess(value.args[2][1]+value.args[2][3],662)
        self.assertEqual(draft,original)

    def test_missing_and_long_orbit_stay_inside_detail_panel(self):
        draft=self.draft(1);item=draft['snapshot']['items'][0]
        segment=dict(task_ids=[item['ll2_id']],preview_seconds=12)
        for orbit,expected in [('', '待补充'),('Unknown','待补充'),('一项保存快照中的很长很长的轨道名称，需要限制在轨道栏内','一项保存快照中的很长很长的轨道名称，需要限制在轨道栏内')]:
            item['orbit']=orbit
            with patch.object(visuals,'_text',wraps=visuals._text) as paint:
                visuals.preview_frame(draft,segment)
            self.assertTrue(any(c.args[1]==expected and c.args[2][0]==1443 and c.args[2][2]==370 for c in paint.call_args_list))

    def test_right_panel_never_renders_paragraph_text_and_has_rounded_corners(self):
        draft=self.draft(1);segment=dict(kind='overview',task_ids=[],screen='字幕不得进入右侧',spoken='旁白不得进入右侧',preview_seconds=12)
        with patch.object(visuals,'_text',wraps=visuals._text) as paint:
            picture=visuals.preview_frame(draft,segment)
        labels=[str(call.args[1]) for call in paint.call_args_list]
        self.assertNotIn(segment['screen'],labels);self.assertNotIn(segment['spoken'],labels)
        self.assertEqual(picture.getpixel((882,visuals.BODY_TOP)),visuals.BG)
        self.assertEqual(picture.getpixel((1000,visuals.BODY_TOP)),visuals.PANEL)
        self.assertEqual(picture.getpixel((1000,1015)),visuals.BG)
        self.assertEqual(visuals.scene_plan(draft,segment)[0]['focus_id'],'launch-1')

    def test_v3_list_rows_fill_available_space_and_paginate_without_shrinking_text(self):
        for count in (1,4,7,10,17):
            with self.subTest(count=count):
                draft=self.draft(count);before=deepcopy(draft)
                ids=[i['ll2_id'] for i in draft['snapshot']['items']]
                providers={i['ll2_id']:i['provider'] for i in draft['snapshot']['items']}
                plan=visuals.scene_plan(draft,dict(task_ids=ids))
                self.assertCountEqual([s['focus_id'] for s in plan],ids)
                for scene in plan:
                    groups=scene['groups'];height=visuals._row_height(groups)
                    self.assertGreaterEqual(height,visuals.ROW_MIN);self.assertLessEqual(height,visuals.ROW_MAX)
                    bottom=visuals.BODY_TOP
                    for number,group in enumerate(groups):
                        bottom+=(visuals.GROUP_MARGIN if number else 0)+visuals.GROUP_HEIGHT+visuals.LIST_GAP
                        bottom+=len(group['task_ids'])*(height+visuals.LIST_GAP)
                        total=sum(provider==providers[group['task_ids'][0]] for provider in providers.values())
                        self.assertEqual(group['total_count'],total)
                    self.assertLessEqual(round(bottom-visuals.LIST_GAP),visuals.BODY_BOTTOM)
                self.assertEqual(draft,before)
        sparse=visuals.scene_plan(self.draft(1),dict(task_ids=['launch-1']))[0]
        four=visuals.scene_plan(self.draft(4),dict(task_ids=['launch-1']))[0]
        self.assertEqual(visuals._row_height(sparse['groups']),visuals.ROW_MAX)
        self.assertGreater(visuals._row_height(four['groups']),100)

    def test_v3_scope_counts_and_company_chip_use_existing_tasks(self):
        draft=self.draft(4);segment=dict(kind='highlight',task_ids=['launch-2'],preview_seconds=12)
        with patch.object(visuals,'_text',wraps=visuals._text) as paint, \
                patch.object(visuals.cards,'fit_payload_text',wraps=visuals.cards.fit_payload_text) as elide:
            visuals.preview_frame(draft,segment)
        labels=[str(call.args[1]) for call in paint.call_args_list]
        self.assertTrue(any('已选4项，已发射4项，成功4项' in text for text in labels))
        self.assertIn('· 2',labels)
        self.assertIn('中国',labels)
        self.assertEqual(labels.count('美国 SpaceX'),1)
        self.assertEqual(labels.count('SpaceX'),1)
        self.assertFalse(any('方面' in text for text in labels))
        self.assertFalse(any('示例' in text for text in labels))
        self.assertNotIn('SpaceX',[call.args[0] for call in elide.call_args_list])

    def test_company_chip_honors_existing_dictionary_without_changing_editorial_groups(self):
        draft=self.draft(1);item=draft['snapshot']['items'][0]
        original=deepcopy(draft)
        entries={item['provider']:dict(translation='用户确认的机构名称',type='organization',human_status='confirmed')}
        with patch('services.launch_research.load_dictionary',return_value=entries), \
                patch.object(visuals,'_text',wraps=visuals._text) as paint:
            visuals.preview_frame(draft,dict(task_ids=[item['ll2_id']],preview_seconds=12))
        labels=[call.args[1] for call in paint.call_args_list]
        self.assertIn('用户确认的机构名称',labels);self.assertIn('中国',labels)
        self.assertEqual(visuals.scene_plan(draft,dict(task_ids=[item['ll2_id']]))[0]['groups'][0]['label'],'中国方面')
        self.assertEqual(draft,original)

    def test_revised_header_tiles_and_daily_strip_keep_recovery_optional(self):
        draft=self.draft(1);item=draft['snapshot']['items'][0]
        segment=dict(task_ids=[item['ll2_id']],preview_seconds=12)
        for reusable,expected in ((False,False),(True,True)):
            with self.subTest(reusable=reusable):
                item.update(rocket_reusable=reusable,recovery='TEST: 成功')
                with patch.object(visuals,'_text',wraps=visuals._text) as paint:
                    picture=visuals.preview_frame(draft,segment)
                labels=[str(call.args[1]) for call in paint.call_args_list]
                self.assertNotIn('SPACEWEEKLY',labels);self.assertIn('每周发射综述',labels)
                self.assertEqual('火箭回收' in labels,expected)
                self.assertIn('发射结果',labels);self.assertIn('卫星数量',labels)
                self.assertTrue(any(text.startswith('所选每日发射次数') for text in labels))
                self.assertIn('所选第 1 次',labels)
                self.assertEqual(picture.getpixel((1000,1015)),visuals.BG)

    def test_html_type_scale_and_payload_line_spacing_are_preserved(self):
        draft=self.draft(1);item=draft['snapshot']['items'][0]
        item.update(rocket='测试型号',site_name='现有发射场',pad_name='',payload='现有载荷说明\n第二行')
        with patch.object(visuals,'_text',wraps=visuals._text) as paint:
            visuals.preview_frame(draft,dict(task_ids=[item['ll2_id']],preview_seconds=12))
        calls=paint.call_args_list
        for label,size in [('每周发射综述',54),('测试型号',68),('载荷',22),('现有载荷说明',36)]:
            matching=[c for c in calls if c.args[1]==label]
            self.assertTrue(any(c.args[3]==size for c in matching),label)
        first=next(c for c in calls if c.args[1]=='现有载荷说明')
        second=next(c for c in calls if c.args[1]=='第二行')
        self.assertEqual(second.args[2][1]-first.args[2][1],54)
        self.assertEqual(visuals.CQW,(visuals.FRAME_RIGHT-visuals.FRAME_LEFT)/100)
        self.assertLess(visuals.CQW,visuals.cards.W/100)

    def test_seven_tasks_in_four_groups_fit_reference_without_extra_pagination(self):
        draft=self.draft(7)
        providers=['CASC','CASC','CASC','SpaceX','SpaceX','Rocket Lab','Other Co']
        for item,provider in zip(draft['snapshot']['items'],providers):item['provider']=provider
        plan=visuals.scene_plan(draft,dict(task_ids=[i['ll2_id'] for i in draft['snapshot']['items']]))
        self.assertEqual(len({scene['page'] for scene in plan}),1)
        self.assertEqual(len(plan),7)
        self.assertGreaterEqual(visuals._row_height(plan[0]['groups']),visuals.ROW_MIN)

    def test_payload_line_height_retains_letters_with_descenders(self):
        canvas=visuals.Image.new('RGBA',(900,100),visuals.BG)
        with patch.object(visuals,'_text',wraps=visuals._text) as paint:
            visuals._text(canvas,'This is a replacement satellite for navigation.',(0,0,886,70),36,line_height=54)
        self.assertTrue(any(call.args[1]=='This is a replacement satellite for navigation.' for call in paint.call_args_list[1:]))

    def test_task_changes_slide_and_pause_holds_last_frame(self):
        draft=self.draft(2);segment=dict(kind='remaining',task_ids=['launch-1','launch-2'],screen='unused',preview_seconds=12)
        prepared=visuals.prepare_frames(draft,segment)
        self.assertEqual(visuals.frame_at(prepared,0).tobytes(),prepared['pictures'][0].tobytes())
        self.assertNotEqual(visuals.frame_at(prepared,6.3).tobytes(),prepared['pictures'][0].tobytes())
        self.assertNotEqual(visuals.frame_at(prepared,6.3).tobytes(),prepared['pictures'][1].tobytes())
        self.assertEqual(visuals.frame_at(prepared,7).tobytes(),prepared['pictures'][1].tobytes())
        self.assertEqual(visuals.frame_at(prepared,12).tobytes(),visuals.frame_at(prepared,13).tobytes())

    def test_whole_cards_move_up_together_and_keep_their_rounded_corners(self):
        before=visuals.Image.new('RGBA',(1920,1080),visuals.BG);after=before.copy()
        x,y,right,bottom=visuals.RIGHT_BOX;height=bottom-y
        old_color=(250,30,40,255);new_color=(30,220,250,255)
        old_y=y+3*height//4;new_y=y+height//4
        for picture,color,marker in ((before,old_color,old_y),(after,new_color,new_y)):
            draw=visuals.ImageDraw.Draw(picture)
            draw.rounded_rectangle((x,y,right-1,bottom-1),radius=29,fill=visuals.PANEL)
            draw.rectangle((x+90,marker-8,x+110,marker+8),fill=color)
        original=before.tobytes(),after.tobytes()
        canvas=after.copy();visuals._slide_right(canvas,before,after,.25,card_gap=0)
        self.assertEqual(canvas.getpixel((x+100,old_y-round(height*visuals.cards.smooth(.25,0,1)))),old_color)
        self.assertEqual(canvas.getpixel((x+100,old_y)),visuals.PANEL)
        canvas=after.copy();visuals._slide_right(canvas,before,after,.5,card_gap=0)
        shift=round(height*.5);seam=y+height-shift
        self.assertEqual(canvas.getpixel((x+100,old_y-shift)),old_color)
        self.assertEqual(canvas.getpixel((x+100,new_y+height-shift)),new_color)
        self.assertEqual(canvas.getpixel((x+100,y+height//2)),visuals.PANEL)
        self.assertEqual(canvas.getpixel((x,seam)),visuals.BG)
        self.assertEqual(canvas.getpixel((x,y)),visuals.PANEL)
        canvas=after.copy();visuals._slide_right(canvas,before,after,.75,card_gap=0)
        self.assertEqual(canvas.getpixel((x+100,new_y+height-round(height*visuals.cards.smooth(.75,0,1)))),new_color)
        self.assertEqual(canvas.getpixel((20,20)),visuals.BG)
        for progress,expected in ((0,before),(1,after)):
            canvas=after.copy();visuals._slide_right(canvas,before,after,progress,card_gap=0)
            self.assertEqual(canvas.crop(visuals.RIGHT_BOX).tobytes(),expected.crop(visuals.RIGHT_BOX).tobytes())
        self.assertEqual((before.tobytes(),after.tobytes()),original)

    def test_selector_moves_down_behind_stationary_rows_and_wraps_inside_list(self):
        draft=self.draft(2)
        prepared=visuals.prepare_frames(draft,dict(task_ids=['launch-1','launch-2'],preview_seconds=12))
        before,after=prepared['scenes'];old,new=before['selector'],after['selector']
        canvas=after['image'].copy();visuals._slide_selector(canvas,before,after,.5)
        top=round((old[1]+new[1])/2);height=old[3]-old[1]
        self.assertEqual(canvas.getpixel((visuals.LIST_RIGHT-40,top+height//2)),(37,51,111,255))
        self.assertEqual(canvas.getpixel((visuals.LIST_RIGHT-40,(old[1]+old[3])//2+20)),(20,29,64,255))
        for progress in (.25,.75):
            canvas=before['image'].copy();visuals._slide_selector(canvas,after,before,progress)
            self.assertEqual(canvas.getpixel((800,150)),before['image'].getpixel((800,150)))
            self.assertEqual(canvas.getpixel((800,1015)),before['image'].getpixel((800,1015)))

    def test_spaced_variant_keeps_visible_gap_and_original_endpoints(self):
        draft=self.draft(2);segment=dict(task_ids=['launch-1','launch-2'],preview_seconds=12)
        gap=round(visuals.RIGHT_MASK.height/4)
        original=visuals.prepare_frames(draft,segment,card_gap=0)
        spaced=visuals.prepare_frames(draft,segment)
        self.assertEqual(spaced['card_gap'],gap)
        self.assertEqual(original['card_gap'],0)
        self.assertEqual(visuals.frame_at(original,6.3).crop(visuals.LEFT_BOX).tobytes(),
                         visuals.frame_at(spaced,6.3).crop(visuals.LEFT_BOX).tobytes())
        middle=visuals.frame_at(spaced,6.3);x=visuals.RIGHT_BOX[0]+100
        y=visuals.BODY_TOP+visuals.RIGHT_MASK.height//2
        self.assertEqual(middle.getpixel((x,y)),visuals.BG)
        self.assertNotEqual(visuals.frame_at(original,6.3).getpixel((x,y)),visuals.BG)
        for elapsed in (0,7,12):
            self.assertEqual(visuals.frame_at(original,elapsed).tobytes(),visuals.frame_at(spaced,elapsed).tobytes())
        for invalid in (-1,True,'200',visuals.RIGHT_MASK.height*2+1):
            with self.assertRaisesRegex(ValueError,'卡片间距'):
                visuals.prepare_frames(draft,segment,card_gap=invalid)

    def test_summary_does_not_join_across_unselected_rows_or_group_headers(self):
        for same_group in (True,False):
            draft=self.draft(4)
            if same_group:
                for item in draft['snapshot']['items']:item['provider']='CASC'
            segment=dict(task_ids=['launch-1','launch-3'],preview_seconds=12)
            plan=visuals.scene_plan(draft,segment)[0]
            if not same_group:
                # Different groups remain separate even when each contains one selected task.
                plan['active_ids']=['launch-1','launch-2']
            summary=visuals._scene(draft,segment,dict(plan,focus_id=None,display='summary'),segment_timing(draft,segment))
            self.assertEqual(len(summary['selectors']),2)
            before=visuals._scene(draft,segment,dict(plan,focus_id=plan['active_ids'][0]),segment_timing(draft,segment))
            for progress in (.1,.25,.5,.75,.9):
                canvas=summary['image'].copy();visuals._slide_selector(canvas,before,summary,progress)
                y=(summary['selectors'][0][3]+summary['selectors'][1][1])//2
                x=visuals.LIST_RIGHT-80
                self.assertEqual(canvas.getpixel((x,y)),summary['list_base'].getpixel((x-visuals.LIST_LEFT,y-visuals.BODY_TOP)))

    def test_short_final_scene_finishes_its_motion_before_audio_end(self):
        prepared=visuals.prepare_frames(self.draft(2),dict(task_ids=['launch-1','launch-2'],preview_seconds=6.1))
        self.assertEqual(visuals.frame_at(prepared,6.1).tobytes(),prepared['pictures'][1].tobytes())

    def test_different_list_windows_clip_selector_motion_without_touching_header(self):
        draft=self.draft(17);segment=dict(task_ids=[i['ll2_id'] for i in draft['snapshot']['items']],preview_seconds=102)
        plan=visuals.scene_plan(draft,segment)
        old=next(s for s in reversed(plan) if s['page']==1)
        new=next(s for s in plan if s['page']==2)
        clock=segment_timing(draft,segment)
        before=visuals._scene(draft,segment,old,clock);after=visuals._scene(draft,segment,new,clock)
        for progress in (.25,.75):
            canvas=after['image'].copy();visuals._slide_selector(canvas,before,after,progress)
            self.assertEqual(canvas.crop((0,0,1920,visuals.BODY_TOP)).tobytes(),
                             after['image'].crop((0,0,1920,visuals.BODY_TOP)).tobytes())
            self.assertEqual(canvas.getpixel((800,1015)),after['image'].getpixel((800,1015)))


if __name__=='__main__':unittest.main()
