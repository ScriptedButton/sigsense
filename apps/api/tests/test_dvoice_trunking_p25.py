"""P25 trunking: band plan and voice-follow policy (WP-C)."""

import unittest

from sigsense_api.dvoice.events import SystemInfo, Protocol, TrunkEvent
from sigsense_api.dvoice.trunking.base import FollowPolicy
from sigsense_api.dvoice.trunking.p25 import IdenEntry, P25BandPlan, P25TrunkController


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def iden_event(iden=1, base=851_006_250, spacing=12_500):
    return TrunkEvent("system_update", detail={"opcode": "IDEN_UP", "iden_fields": {"iden": iden, "base_hz": base, "spacing_hz": spacing}})


def grant(tg, channel, *, hz=None, src=1, encrypted=False, emergency=False):
    return TrunkEvent("grant", frequency_hz=hz, talkgroup=tg, source_id=src, encrypted=encrypted, detail={"channel": channel, "emergency": emergency})


class BandPlanTests(unittest.TestCase):
    def test_channel_to_frequency(self):
        bp = P25BandPlan()
        bp.update(IdenEntry(iden=1, base_hz=851_006_250, spacing_hz=12_500))
        self.assertEqual(bp.frequency((1 << 12) | 0), 851_006_250.0)
        self.assertEqual(bp.frequency((1 << 12) | 178), 851_006_250.0 + 178 * 12_500)  # 853.231250 MHz
        self.assertEqual(bp.frequency((1 << 12) | 4095), 851_006_250.0 + 4095 * 12_500)

    def test_unknown_or_unusable_iden_gives_none(self):
        bp = P25BandPlan()
        self.assertIsNone(bp.frequency(0x1001))
        bp.update(IdenEntry(iden=2, base_hz=0, spacing_hz=12_500))
        self.assertIsNone(bp.frequency(0x2001))
        bp.update(IdenEntry(iden=3, base_hz=450_000_000, spacing_hz=0))
        self.assertIsNone(bp.frequency(0x3001))

    def test_sixteen_identifiers(self):
        bp = P25BandPlan()
        for i in range(16):
            bp.update(IdenEntry(iden=i, base_hz=100_000_000 + i * 1_000_000, spacing_hz=6_250))
        self.assertEqual(len(bp), 16)
        self.assertEqual(bp.frequency((15 << 12) | 2), 115_000_000.0 + 2 * 6_250)


class FollowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def ctrl(self, **policy):
        c = P25TrunkController(FollowPolicy(**policy), clock=self.clock)
        c.on_event("trunk", iden_event())
        c.on_event("trunk", TrunkEvent("cc_acquired", frequency_hz=852_000_000.0))
        return c

    CH = (1 << 12) | 0x10

    def test_follows_a_grant_and_returns_to_control(self):
        c = self.ctrl()
        out = c.on_event("trunk", grant(100, self.CH))
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].role, out[0].talkgroup), ("voice", 100))
        self.assertAlmostEqual(out[0].frequency_hz, 851_006_250 + 0x10 * 12_500)
        back = c.on_voice_ended(out[0].frequency_hz)
        self.assertEqual((back[0].role, back[0].frequency_hz, back[0].reason), ("control", 852_000_000.0, "return_to_control"))

    def test_repeated_grants_do_not_retune(self):
        c = self.ctrl()
        c.on_event("trunk", grant(100, self.CH))
        self.assertEqual(c.on_event("trunk", grant(100, self.CH)), [])

    def test_encrypted_grants_are_skipped_by_default(self):
        c = self.ctrl()
        self.assertEqual(c.on_event("trunk", grant(100, self.CH, encrypted=True)), [])
        self.assertEqual(c.status()["skipped_encrypted"], 1)
        c2 = self.ctrl(skip_encrypted=False)
        self.assertEqual(len(c2.on_event("trunk", grant(100, self.CH, encrypted=True))), 1)

    def test_hold_list_restricts_to_listed_talkgroups(self):
        c = self.ctrl(hold_talkgroups={100})
        self.assertEqual(c.on_event("trunk", grant(200, self.CH)), [])
        self.assertEqual(len(c.on_event("trunk", grant(100, self.CH + 1))), 1)

    def test_skip_list(self):
        c = self.ctrl(skip_talkgroups={5})
        self.assertEqual(c.on_event("trunk", grant(5, self.CH)), [])
        self.assertEqual(c.status()["skipped_policy"], 1)

    def test_busy_follower_ignores_ordinary_grants(self):
        c = self.ctrl()
        c.on_event("trunk", grant(100, self.CH))
        self.assertEqual(c.on_event("trunk", grant(200, self.CH + 1)), [])
        self.assertEqual(c.status()["busy"], 1)

    def test_priority_talkgroup_preempts(self):
        c = self.ctrl(priority_talkgroups=[300, 100])
        c.on_event("trunk", grant(100, self.CH))
        out = c.on_event("trunk", grant(300, self.CH + 1))
        self.assertEqual([r.talkgroup for r in out], [300])
        self.assertEqual(c.status()["preempted"], 1)
        # a lower-priority one does not take over the higher
        self.assertEqual(c.on_event("trunk", grant(100, self.CH + 2)), [])

    def test_emergency_preempts_non_priority_calls(self):
        c = self.ctrl()
        c.on_event("trunk", grant(100, self.CH))
        self.assertEqual(len(c.on_event("trunk", grant(200, self.CH + 1, emergency=True))), 1)

    def test_multiple_followers_when_allowed(self):
        c = self.ctrl(max_voice_followers=2)
        c.on_event("trunk", grant(1, self.CH))
        self.assertEqual(len(c.on_event("trunk", grant(2, self.CH + 1))), 1)
        self.assertEqual(c.on_event("trunk", grant(3, self.CH + 2)), [])

    def test_grant_without_iden_is_not_followed(self):
        c = P25TrunkController(clock=self.clock)
        self.assertEqual(c.on_event("trunk", grant(1, self.CH)), [])

    def test_late_iden_makes_later_grants_followable(self):
        c = P25TrunkController(clock=self.clock)
        self.assertEqual(c.on_event("trunk", grant(1, self.CH)), [])
        c.on_event("trunk", iden_event())
        self.assertEqual(len(c.on_event("trunk", grant(1, self.CH))), 1)

    def test_explicit_frequency_wins_over_the_table(self):
        c = P25TrunkController(clock=self.clock)
        out = c.on_event("trunk", grant(1, self.CH, hz=460_100_000.0))
        self.assertEqual(out[0].frequency_hz, 460_100_000.0)

    def test_release_event_returns_to_control(self):
        c = self.ctrl()
        out = c.on_event("trunk", grant(1, self.CH))
        back = c.on_event("trunk", TrunkEvent("release", frequency_hz=out[0].frequency_hz))
        self.assertEqual(back[0].role, "control")

    def test_lost_release_is_cleaned_up_by_tick(self):
        c = P25TrunkController(clock=self.clock, max_call_s=60)
        c.on_event("trunk", iden_event())
        c.on_event("trunk", TrunkEvent("cc_acquired", frequency_hz=852_000_000.0))
        c.on_event("trunk", grant(1, self.CH))
        self.clock.t = 30
        self.assertEqual(c.tick(), [])
        self.clock.t = 61
        self.assertEqual(c.tick()[0].role, "control")
        self.assertEqual(c.status()["following"], [])

    def test_control_channel_loss_hunts_the_alternates(self):
        c = self.ctrl()
        c.on_event("trunk", TrunkEvent("system_update", frequency_hz=853_000_000.0, detail={"opcode": "ADJ_STS_BCST", "channel": self.CH}))
        out = c.on_event("trunk", TrunkEvent("cc_lost"))
        self.assertEqual((out[0].role, out[0].reason), ("control", "cc_lost_hunt"))
        self.assertFalse(c.status()["cc_locked"])

    def test_system_info_is_tracked(self):
        c = P25TrunkController()
        c.on_event("system_info", SystemInfo(Protocol.P25P1, wacn=0xBEE00, system_id=0x3A1, nac=0x293, control_channel_hz=852_500_000.0))
        s = c.status()
        self.assertEqual((s["system"]["wacn"], s["control_hz"]), (0xBEE00, 852_500_000.0))

    def test_unrelated_events_are_ignored(self):
        c = P25TrunkController()
        self.assertEqual(c.on_event("call_start", object()), [])
        self.assertEqual(c.on_voice_ended(1.0), [])


if __name__ == "__main__":
    unittest.main()
