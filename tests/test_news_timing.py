"""Measured voice end drives summary scroll and retraction, without changing the strip."""
import unittest
from services.news_timing import animation
from tools.video_cards import scroll_offset


class NewsTimingTests(unittest.TestCase):
    def clock(self,duration=30,**settings):
        row=dict(start_seconds=2.15,speech_end_seconds=2.15+duration,audio={'sha256':'existing'},
                 units=[dict(global_start_seconds=2.15),dict(global_start_seconds=4)])
        return animation(dict(animation_gaps=settings),dict(segments=[row],duration_seconds=duration+3.85))

    def test_long_final_sentence_does_not_hide_summary_early(self):
        clock=self.clock()
        self.assertAlmostEqual(clock['summary_out'],clock['voice_end']-.85)
        self.assertAlmostEqual(clock['summary_out']+clock['summary_out_duration'],clock['voice_end'])

    def test_short_long_and_manual_retractions_end_at_voice_end(self):
        for duration in (.1,.5,2,12,60,180):
            for custom in (0,.01,.3,2,60):
                with self.subTest(duration=duration,custom=custom):
                    clock=self.clock(duration,summary_close_before_end=custom)
                    self.assertAlmostEqual(clock['summary_out']+clock['summary_out_duration'],clock['voice_end'])
                    self.assertLess(clock['summary_in']+clock['summary_in_duration'],clock['summary_scroll_start'])
                    self.assertLess(clock['summary_scroll_start'],clock['summary_scroll_end'])
                    self.assertLess(clock['summary_scroll_end'],clock['summary_out'])
                    self.assertLess(clock['voice_end'],clock['strip_out'])

    def test_full_marquee_distance_fits_the_available_audio_clock(self):
        card=dict(scroll_distance=12000)
        for duration in (.5,12,180):
            clock=self.clock(duration)
            start,end=clock['summary_scroll_start'],clock['summary_scroll_end']
            self.assertEqual(scroll_offset(card,start,clock),0)
            self.assertAlmostEqual(scroll_offset(card,(start+end)/2,clock),6000)
            self.assertEqual(scroll_offset(card,end,clock),12000)
            self.assertEqual(scroll_offset(card,clock['summary_out'],clock),12000)
            # Seeking does not depend on rendering the preceding frames.
            scroll_offset(card,clock['voice_end'],clock)
            self.assertAlmostEqual(scroll_offset(card,(start+end)/2,clock),6000)
        self.assertEqual(scroll_offset(dict(scroll_distance=0),5,self.clock()),0)

    def test_invalid_custom_duration_is_rejected(self):
        for value in (-1,61,float('nan'),float('inf'),True,'1'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.clock(summary_close_before_end=value)

    def test_exit_keeps_every_layer_inside_the_plate_until_fully_retracted(self):
        from PIL import Image,ImageChops,ImageDraw
        from tools import video_cards as video
        card=video.make_card(dict(title='赫拉号探测器首次近距离观测迪迪莫斯双星系统',summary='欧洲航天局赫拉号探测器近距离观测目标。'),1)
        clock=self.clock(12.96)
        out=clock['strip_out'];end=clock['end']
        stable=video.render_frame(card,out, end,timing=clock)
        self.assertEqual(stable.tobytes(),video.render_frame(card,out-1/30,end,timing=clock).tobytes())
        for number in range(round(out*30),round(end*30)+1):
            time=number/30
            with self.subTest(time=time):
                frame=video.render_frame(card,time,end,timing=clock)
                offset=round(video.W*video.smooth(time,clock['background_out'],clock['background_out_duration']))
                outside=Image.new('L',frame.size,255)
                ImageDraw.Draw(outside).rectangle((48+offset,812,1872+offset,1032),fill=0)
                self.assertIsNone(ImageChops.multiply(frame.getchannel('A'),outside).getbbox())
                if time>=clock['background_out']:
                    plate=Image.new('RGBA',frame.size)
                    ImageDraw.Draw(plate).rectangle((48+offset,812,1872+offset,1032),fill=video.BLUE)
                    self.assertIsNone(ImageChops.difference(frame,plate).convert('RGB').getbbox())
        self.assertIsNone(video.render_frame(card,end-1/30,end,timing=clock).getbbox())
