"""DMR Tier III trunking follower policy (WP-D). Original code.

Consumes the decoder's `trunk`/`system_info` events from a control channel (TSCC) and decides
which voice grants to follow. A TV_GRANT/PV_GRANT names a *logical* physical channel number
(TS 102 361-4 tables 7.9-7.12), not a frequency: the mapping comes from the system's channel
plan (announced with C_BCAST/BC_AP multi-block messages, which this release does not decode, or
supplied by the operator with `set_channel_plan`). A grant whose channel is not in the plan is
never guessed: it is counted and reported in `status()` so the UI can ask for the plan.

Policy: hold / skip / priority talkgroups, `skip_encrypted`, data grants only when `follow_data`,
a follower limit with priority pre-emption, and a return to the control channel when the last
voice follower goes idle past the hang time (the decoder reports that via `on_voice_ended`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sigsense_api.dvoice.events import Protocol
from sigsense_api.dvoice.trunking.base import FollowPolicy, TrunkController, TuneRequest


@dataclass
class _Follower:
    frequency_hz: float
    talkgroup: Optional[int]
    slot: Optional[int]
    channel: int


class DmrTrunkController(TrunkController):
    protocol = Protocol.DMR

    def __init__(
        self,
        policy: Optional[FollowPolicy] = None,
        channel_plan: Optional[dict[int, float]] = None,
        control_frequency_hz: Optional[float] = None,
    ) -> None:
        super().__init__(policy)
        self.channel_plan: dict[int, float] = dict(channel_plan or {})
        self.control_frequency_hz = control_frequency_hz
        self.on_control = control_frequency_hz is not None
        self.followers: list[_Follower] = []
        self.system: dict[str, Any] = {}
        self._counts = {"grants": 0, "followed": 0, "skipped": 0, "unmapped": 0, "preempted": 0}
        self._unmapped: set[int] = set()

    def set_channel_plan(self, plan: dict[int, float]) -> None:
        """Logical physical channel number -> RF frequency in Hz (the receive frequency)."""
        self.channel_plan = {int(k): float(v) for k, v in plan.items()}

    # -------------------------------------------------------------- events

    def on_event(self, event_type: str, payload: Any) -> list[TuneRequest]:
        if event_type == "system_info":
            if getattr(payload, "network_id", None) is not None:
                self.system = {"network_id": payload.network_id, "site_id": payload.site_id,
                               "color_code": payload.color_code, **(payload.extra or {})}
            return []
        if event_type != "trunk":
            return []
        kind = payload.kind
        if kind == "cc_acquired":
            if payload.frequency_hz:
                self.control_frequency_hz = payload.frequency_hz
            self.on_control = True
            return []
        if kind == "cc_lost":
            self.on_control = False
            return []
        if kind == "grant":
            return self._on_grant(payload)
        return []

    def _on_grant(self, g: Any) -> list[TuneRequest]:
        self._counts["grants"] += 1
        detail = g.detail or {}
        tg = g.talkgroup if detail.get("call") != "private" else None
        is_data = detail.get("call") == "data"
        p = self.policy
        if is_data and not p.follow_data:
            return self._skip("data")
        if g.encrypted and p.skip_encrypted:
            return self._skip("encrypted")
        if tg is not None and tg in p.skip_talkgroups:
            return self._skip("skip list")
        if p.hold_talkgroups and (tg is None or tg not in p.hold_talkgroups):
            return self._skip("not in hold list")
        channel = detail.get("logical_channel")
        if channel is None or channel not in self.channel_plan:
            self._counts["unmapped"] += 1
            if channel is not None:
                self._unmapped.add(int(channel))
            return []
        freq = self.channel_plan[channel]
        if any(f.channel == channel and f.slot == g.slot for f in self.followers):
            return []  # already following this call
        preempt: list[TuneRequest] = []
        if len(self.followers) >= p.max_voice_followers:
            victim = self._lowest_priority_follower()
            if victim is None or not self._outranks(tg, victim.talkgroup):
                return self._skip("busy")
            self.followers.remove(victim)
            self._counts["preempted"] += 1
        self.followers.append(_Follower(freq, tg, g.slot, int(channel)))
        self.on_control = False
        self._counts["followed"] += 1
        return preempt + [
            TuneRequest("voice", freq, f"grant tg={tg} src={g.source_id}", tg, g.slot,
                        {"logical_channel": channel, "emergency": bool(detail.get("emergency"))})
        ]

    def _skip(self, why: str) -> list[TuneRequest]:
        self._counts["skipped"] += 1
        return []

    # -------------------------------------------------------------- priority

    def _rank(self, tg: Optional[int]) -> int:
        order = self.policy.priority_talkgroups
        return order.index(tg) if tg in order else len(order)

    def _outranks(self, new: Optional[int], old: Optional[int]) -> bool:
        return self._rank(new) < self._rank(old)

    def _lowest_priority_follower(self) -> Optional[_Follower]:
        return max(self.followers, key=lambda f: self._rank(f.talkgroup), default=None)

    # -------------------------------------------------------------- voice ended

    def on_voice_ended(self, frequency_hz: float) -> list[TuneRequest]:
        self.followers = [f for f in self.followers if abs(f.frequency_hz - frequency_hz) > 1.0]
        if self.followers or self.control_frequency_hz is None:
            return []
        self.on_control = True
        return [TuneRequest("control", self.control_frequency_hz, "return_to_control")]

    def status(self) -> dict[str, Any]:
        return {
            "on_control_channel": self.on_control,
            "control_frequency_hz": self.control_frequency_hz,
            "followers": [
                {"frequency_hz": f.frequency_hz, "talkgroup": f.talkgroup, "slot": f.slot, "channel": f.channel}
                for f in self.followers
            ],
            "system": self.system,
            "channel_plan_size": len(self.channel_plan),
            "unmapped_channels": sorted(self._unmapped),
            **self._counts,
        }
