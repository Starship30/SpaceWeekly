"""Fixture audio/times exercise visual editing, not real TTS or alignment acceptance."""
from copy import deepcopy
import unittest

from services import roundup_sync as sync,launch_roundup as r
from services.roundup_audio import audio_key,current_audio
from tests import test_roundup_sync as fixtures
from tools import roundup_visuals as visuals


class ShotTests(unittest.TestCase):
    setUp=fixtures.RoundupSyncTests.setUp
    audio=fixtures.RoundupSyncTests.audio
    aligned=fixtures.RoundupSyncTests.aligned

    def table(self):
        self.audio();self.aligned()
        return sync.build_timeline(self.draft)

    def test_default_group_removal_holds_without_anticipating_tasks_or_changing_audio(self):
        timeline=self.table();before=deepcopy(self.draft);key=audio_key(self.draft,self.segment)
        plan=visuals.shot_plan(self.draft,timeline)
        self.assertEqual([s['start_seconds'] for s in plan['shots']],[0,.7,1.8,3.2])
        self.assertFalse(any(s['scene']['display']=='group' for s in plan['shots']))
        self.assertEqual(plan['shots'][0]['scene']['active_ids'],[])
        self.assertEqual(plan['total_samples'],timeline['total_samples'])
        self.assertEqual(self.draft,before);self.assertEqual(audio_key(self.draft,self.segment),key)

    def test_enable_remove_one_group_and_save_preserve_the_clock_and_current_audio(self):
        timeline=self.table();self.draft['visuals']=dict(group_cards=True,removed_groups=[])
        cue=visuals.group_cues(self.draft,timeline)[0]
        enabled=visuals.shot_plan(self.draft,timeline)
        self.assertTrue(any(s['scene']['display']=='group' for s in enabled['shots']))
        self.draft['visuals']['removed_groups'].append(cue['key'])
        removed=visuals.shot_plan(self.draft,timeline)
        self.assertEqual(removed['transition_count'],enabled['transition_count']-1)
        self.assertEqual(sync.build_timeline(self.draft),timeline)
        path=self.root/'visual-draft.json';r.save_draft(self.draft,path);loaded=r.load_draft(path)
        self.assertEqual(loaded['visuals'],self.draft['visuals']);self.assertIsNotNone(current_audio(loaded,loaded['segments'][0]))

    def test_different_unit_ids_and_task_order_do_not_restart_identical_visuals(self):
        timeline=self.table();units=timeline['segments'][0]['units']
        duplicate=deepcopy(units[1]);duplicate.update(id='another-unit-id',start_sample=24000)
        units.insert(2,duplicate)
        summary=deepcopy(units[-1]);summary.update(id='another-summary-id',task_ids=list(reversed(summary['task_ids'])),start_sample=85000)
        units.append(summary)
        plan=visuals.shot_plan(self.draft,timeline)
        self.assertEqual([s['start_seconds'] for s in plan['shots']],[0,.7,1.8,3.2])
        self.assertEqual(len(plan['shots'][1]['sources']),2)
        self.assertEqual(len(plan['shots'][-1]['sources']),2)

    def test_short_tasks_use_light_updates_and_multi_task_sentence_is_not_split(self):
        timeline=self.table();timeline['segments'][0]['units'][1]['task_ids']=['launch-1','launch-2']
        plan=visuals.shot_plan(self.draft,timeline)
        self.assertEqual(plan['shots'][1]['scene']['display'],'summary')
        self.assertIsNone(plan['shots'][1]['scene']['focus_id'])
        self.assertIn(1,plan['short_shots']);self.assertEqual(plan['shots'][1]['animation'],'update')
        self.assertEqual(plan['total_samples'],96000+36000)
        self.assertEqual(plan['shots'][-1]['end_sample'],plan['total_samples'])

    def test_transition_and_paragraph_pause_hold_summary_to_the_audio_end(self):
        self.table()
        closing=dict(id='closing',kind='transition',spoken='接下来。',screen='过渡',task_ids=[],source_refs=[],preview_seconds=2)
        sync.set_units(closing,[dict(text='接下来。',task_ids=[],display='transition')])
        self.draft['segments'].append(closing);self.audio(closing,frames=48000)
        timeline=sync.build_timeline(self.draft);plan=visuals.shot_plan(self.draft,timeline)
        self.assertEqual(plan['shots'][-1]['end_sample'],timeline['total_samples'])
        self.assertEqual(plan['shots'][-1]['scene']['active_ids'],['launch-1','launch-2'])
        prepared=visuals.prepare_frames(self.draft,closing)
        self.assertLess(prepared['starts'][0],0)
        self.assertEqual(visuals.frame_at(prepared,0).tobytes(),visuals.frame_at(prepared,2).tobytes())

    def test_group_preference_is_validated_at_both_restore_boundaries(self):
        from services.production import validate_session
        for bad in (None,dict(group_cards='yes'),dict(removed_groups=[3])):
            self.draft['visuals']=bad
            with self.assertRaises(ValueError):r.validate_draft(self.draft)
            with self.assertRaises(ValueError):validate_session(dict(version=1,roundup=self.draft))

    def test_next_group_summary_moves_on_its_intro_without_anticipating_individual_task(self):
        timeline=self.table()
        later=deepcopy(self.segment);later['id']='next-company';self.draft['segments'].append(later)
        # Explicit IDs in the new group introduction, independent of text or unit IDs.
        for item in self.draft['snapshot']['items']:item['provider']='CASC'
        next_item=deepcopy(self.draft['snapshot']['items'][0]);next_item.update(ll2_id='launch-3',provider='SpaceX')
        self.draft['snapshot']['items'].append(next_item)
        self.draft['selection'].append(dict(id='launch-3',included=True,featured=False,group=''))
        later['task_ids']=['launch-3'];later['units'][0]['task_ids']=['launch-3']
        clock=deepcopy(timeline['segments'][0]);clock.update(segment_id=later['id'],start_sample=103200,end_sample=199200,speech_end_sample=199200)
        for unit in clock['units']:unit['task_ids']=['launch-3']
        timeline['segments'].append(clock);timeline['total_samples']=199200
        for removed in (False,True):
            self.draft['visuals']=dict(group_cards=removed,removed_groups=[later['id']+'/'+clock['units'][0]['id']] if removed else [])
            plan=visuals.shot_plan(self.draft,timeline)
            state=next(s for s in plan['shots'] if s['start_sample']==105600)
            self.assertEqual(state['scene']['active_ids'],['launch-3'])
            self.assertEqual(state['scene']['display'],'summary')
            self.assertIsNone(state['scene']['focus_id'])
            self.assertEqual(state['animation'],'update')
            task=next(s for s in plan['shots'] if s['start_sample']==120000)
            self.assertEqual(task['scene']['focus_id'],'launch-3')

    def test_transition_midpoint_is_identical_for_equivalent_float_expressions(self):
        from math import nextafter
        self.table();prepared=visuals.prepare_frames(self.draft,self.segment)
        expected=visuals.frame_at(prepared,3.4).tobytes()
        self.assertEqual(visuals.frame_at(prepared,nextafter(3.4,0)).tobytes(),expected)
        self.assertEqual(visuals.frame_at(prepared,nextafter(3.4,4)).tobytes(),expected)


if __name__=='__main__':unittest.main()
