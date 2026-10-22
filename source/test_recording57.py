import logging
import unittest

from main import BotEngine


class MatchRecordingEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = BotEngine.__new__(BotEngine)
        self.engine.log = logging.getLogger("recording-test")
        self.engine.recording_event_callback = None
        self.engine.last_recording_event = ""

    def test_match_boundaries_are_emitted_once_per_transition(self):
        events = []
        self.engine.recording_event_callback = events.append

        self.engine._notify_recording_event("hero_select")
        self.engine._notify_recording_event("hero_select")
        self.engine._notify_recording_event("room")
        self.engine._notify_recording_event("room")
        self.engine._notify_recording_event("hero_select")

        self.assertEqual(events, ["hero_select", "room", "hero_select"])

    def test_missing_callback_is_safe(self):
        self.engine._notify_recording_event("room")
        self.assertEqual(self.engine.last_recording_event, "room")


if __name__ == "__main__":
    unittest.main()
