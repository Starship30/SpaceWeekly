"""Separate title/summary animation on the measured narration clock."""
def animation(draft,timeline):
    row=timeline['segments'][0];start=row['start_seconds'];end=row['speech_end_seconds'];duration=end-start
    reveal=min(.45,max(0,duration/4));show=start+min(.15,duration/10)
    settings=draft.get('animation_gaps',{})
    retract=settings.get('summary_close_before_end',.85)
    if type(retract) not in (float,int) or not 0<=retract<=60:raise ValueError('摘要收起时长应为0至60秒')
    retract=min(retract or .85,max(0,duration/4))
    close=end-retract
    # A long final sentence is not a cue to hide the summary at its start.
    full=show+reveal;hold=min(1,max(0,(close-full)/6))
    out=end+settings.get('after_voice',.2)
    return dict(version=4,title_ready=1.95,voice_start=start,voice_end=end,
        summary_in=show,summary_in_duration=reveal,summary_out=close,summary_out_duration=retract,
        summary_scroll_start=full+hold,summary_scroll_end=close-hold,
        strip_out=out,background_out=out+.55,background_out_duration=.67,end=timeline['duration_seconds'],
        basis='actual_audio_end' if row['audio'] else 'unmeasured_preview')
