"""DMR Tier III follow policy (WP-D)."""

import unittest

from sigsense_api.dvoice.events import Protocol, SystemInfo, TrunkEvent
from sigsense_api.dvoice.trunking.base import FollowPolicy
from sigsense_api.dvoice.trunking.dmr import DmrTrunkController


def grant(tg, src=1, channel=5, slot=0, call="group", emergency=False, encrypted=False):
    return TrunkEvent("grant", None, tg, src, slot, encrypted,
                      {"call": call, "logical_channel": channel, "emergency": emergency, "target": tg})


PLAN = {5: 462_550_000.0, 6: 462_575_000.0, 7: 462_600_000.0}


class FollowTests(unittest.TestCase):
    def make(self, **policy):
        c = DmrTrunkController(FollowPolicy(**policy), PLAN, control_frequency_hz=461_000_000.0)
        c.on_event("trunk", TrunkEvent("cc_acquired", 461_000_000.0))
        return c

    def test_grant_maps_the_logical_channel_to_a_frequency(self):
        c = self.make()
        req = c.on_event("trunk", grant(300, 400, channel=6, slot=1))
        self.assertEqual(len(req), 1)
        self.assertEqual((req[0].role, req[0].frequency_hz, req[0].talkgroup, req[0].slot), ("voice", PLAN[6], 300, 1))
        self.assertFalse(c.status()["on_control_channel"])

    def test_unmapped_channels_are_never_guessed(self):
        c = self.make()
        self.assertEqual(c.on_event("trunk", grant(300, channel=99)), [])
        s = c.status()
        self.assertEqual((s["unmapped"], s["unmapped_channels"]), (1, [99]))

    def test_hold_skip_and_data_policies(self):
        c = self.make(hold_talkgroups={10}, skip_talkgroups={11})
        self.assertEqual(c.on_event("trunk", grant(12)), [])  # not held
        self.assertEqual(len(c.on_event("trunk", grant(10, channel=6))), 1)
        c2 = self.make(skip_talkgroups={11})
        self.assertEqual(c2.on_event("trunk", grant(11)), [])
        c3 = self.make()
        self.assertEqual(c3.on_event("trunk", grant(5, call="data")), [])
        c4 = self.make(follow_data=True)
        self.assertEqual(len(c4.on_event("trunk", grant(5, call="data"))), 1)

    def test_encrypted_grants_are_skipped_by_default(self):
        self.assertEqual(self.make().on_event("trunk", grant(1, encrypted=True)), [])
        self.assertEqual(len(self.make(skip_encrypted=False).on_event("trunk", grant(1, encrypted=True))), 1)

    def test_follower_limit_and_priority_preemption(self):
        c = self.make(max_voice_followers=1, priority_talkgroups=[900, 800])
        self.assertEqual(len(c.on_event("trunk", grant(500, channel=5))), 1)
        self.assertEqual(c.on_event("trunk", grant(501, channel=6)), [])  # busy, no priority
        req = c.on_event("trunk", grant(800, channel=6))  # priority talkgroup pre-empts
        self.assertEqual((len(req), req[0].talkgroup), (1, 800))
        self.assertEqual(c.status()["preempted"], 1)
        self.assertEqual(c.on_event("trunk", grant(500, channel=7)), [])  # lower rank cannot take it back

    def test_duplicate_grants_do_not_retune_twice(self):
        c = self.make(max_voice_followers=2)
        self.assertEqual(len(c.on_event("trunk", grant(300, channel=5, slot=0))), 1)
        self.assertEqual(c.on_event("trunk", grant(300, channel=5, slot=0)), [])

    def test_returns_to_control_when_the_last_follower_ends(self):
        c = self.make(max_voice_followers=2)
        c.on_event("trunk", grant(1, channel=5))
        c.on_event("trunk", grant(2, channel=6))
        self.assertEqual(c.on_voice_ended(PLAN[5]), [])  # one follower still active
        back = c.on_voice_ended(PLAN[6])
        self.assertEqual((back[0].role, back[0].frequency_hz, back[0].reason), ("control", 461_000_000.0, "return_to_control"))
        self.assertTrue(c.status()["on_control_channel"])

    def test_system_info_is_tracked(self):
        c = self.make()
        c.on_event("system_info", SystemInfo(Protocol.DMR, color_code=3, network_id=5, site_id=9, extra={"model": "small"}))
        self.assertEqual(c.status()["system"]["site_id"], 9)

    def test_unrelated_events_are_ignored(self):
        self.assertEqual(self.make().on_event("error", object()), [])


if __name__ == "__main__":
    unittest.main()
