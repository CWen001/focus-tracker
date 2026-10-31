import unittest
from dataclasses import replace
from activity_log.timeline import Observation, Timeline


def obs(t, **changes):
    return Observation(at=t, utc=1700000000+t, offset=480, app='editor.exe', idle=int(t*1000), **changes)


def advance(timeline, start, end, **changes):
    for t in range(start, end+1):
        timeline.observe(obs(t, **changes))


def duration(timeline, state):
    return sum(s.end-s.start for s in timeline.segments if s.state == state)


class TimelineTests(unittest.TestCase):
    def test_299_pending_300_idle_and_stable_revision(self):
        timeline=Timeline('a')
        advance(timeline,0,299)
        self.assertEqual(duration(timeline,'pending'),299)
        identity=timeline.segments[0].id
        revision=timeline.segments[0].revision
        timeline.observe(obs(300))
        self.assertEqual(duration(timeline,'idle'),300)
        self.assertEqual(timeline.segments[0].id,identity)
        self.assertGreater(timeline.segments[0].revision,revision)

    def test_playback_is_never_deducted_after_stopping(self):
        timeline=Timeline('a')
        advance(timeline,0,600,playback='playing')
        timeline.observe(obs(601,playback='not-playing'))
        advance(timeline,602,901)
        self.assertEqual(duration(timeline,'activity'),601)
        self.assertEqual(duration(timeline,'idle'),300)

    def test_unknown_playback_does_not_become_idle(self):
        timeline=Timeline('a')
        advance(timeline,0,300,playback='unknown')
        self.assertEqual(duration(timeline,'unknown'),300)
        self.assertEqual(duration(timeline,'idle'),0)

    def test_input_finalizes_short_candidate(self):
        timeline=Timeline('a')
        advance(timeline,0,20)
        timeline.observe(replace(obs(21),idle=0))
        self.assertEqual(duration(timeline,'activity'),21)

    def test_gap_preserves_pending_and_does_not_extend_activity(self):
        timeline=Timeline('a')
        advance(timeline,0,20)
        timeline.observe(obs(60))
        self.assertEqual(duration(timeline,'pending'),20)
        self.assertEqual(duration(timeline,'unknown'),40)
        timeline.observe(replace(obs(61),idle=0))
        self.assertEqual(duration(timeline,'pending'),20)

    def test_lock_wins_over_playback(self):
        timeline=Timeline('a')
        advance(timeline,0,10,playback='playing',system='locked')
        self.assertEqual(duration(timeline,'locked'),10)

    def test_known_suspend_is_not_an_unknown_sampling_gap(self):
        timeline=Timeline('a')
        timeline.observe(obs(0,system='suspended'))
        timeline.observe(obs(600,system='active'))
        self.assertEqual(duration(timeline,'suspended'),600)

    def test_clock_jump_and_duplicate(self):
        timeline=Timeline('a')
        timeline.observe(obs(0))
        timeline.observe(obs(0))
        timeline.observe(replace(obs(1),utc=1700001000))
        self.assertEqual(duration(timeline,'unknown'),1)
        with self.assertRaises(ValueError): timeline.observe(obs(0))

    def test_invalid_times_and_unknown_input(self):
        for value in [float('nan'),float('inf'),-1]:
            with self.assertRaises(ValueError): Timeline('a').observe(replace(obs(0), at=value))
        timeline=Timeline('a')
        timeline.observe(replace(obs(0),idle=None))
        timeline.observe(replace(obs(1),idle=None))
        self.assertEqual(duration(timeline,'unknown'),1)

    def test_background_app_does_not_inherit_domain(self):
        timeline=Timeline('a')
        with self.assertRaises(ValueError):
            timeline.observe(replace(obs(0),domain='youtube.com'))
