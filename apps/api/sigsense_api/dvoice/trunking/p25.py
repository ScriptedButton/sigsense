"""P25 trunking: band plan (IDEN_UP) and voice-grant follow policy. Original code.

The control channel decoder (protocols/p25p1) reports grants and system broadcasts
as `trunk` events. This controller turns them into `TuneRequest`s for the engine:
which voice frequency to follow, and when to return to the control channel. It
never touches hardware.

Channel numbers: a 16-bit channel identifier is iden(4) + channel number(12). The
identifier (from an IDEN_UP broadcast) gives the base frequency and spacing:

    downlink Hz = base_hz + channel_number * spacing_hz

Encrypted calls are skipped by default (`FollowPolicy.skip_encrypted`): there is
no audio to follow, and this package never handles keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sigsense_api.dvoice.events import Protocol, SystemInfo, TrunkEvent
from sigsense_api.dvoice.trunking.base import FollowPolicy, TrunkController, TuneRequest


@dataclass
class IdenEntry:
    iden: int
    base_hz: int
    spacing_hz: int
    bandwidth_hz: Optional[int] = None
    tx_offset_hz: Optional[int] = None


class P25BandPlan:
    """Identifier table (up to 16 entries per site) and channel -> frequency conversion."""

    def __init__(self) -> None:
        self.entries: dict[int, IdenEntry] = {}

    def update(self, entry: IdenEntry) -> None:
        self.entries[entry.iden & 0xF] = entry

    def update_from_fields(self, fields: dict[str, Any]) -> None:
        self.update(
            IdenEntry(
                iden=int(fields["iden"]),
                base_hz=int(fields["base_hz"]),
                spacing_hz=int(fields["spacing_hz"]),
                bandwidth_hz=fields.get("bandwidth_hz"),
                tx_offset_hz=fields.get("tx_offset_hz"),
            )
        )

    def frequency(self, channel: int) -> Optional[float]:
        """Downlink frequency of a 16-bit channel identifier, or None if its IDEN is unknown."""
        entry = self.entries.get((channel >> 12) & 0xF)
        if entry is None or entry.spacing_hz <= 0 or entry.base_hz <= 0:
            return None
        return float(entry.base_hz + (channel & 0xFFF) * entry.spacing_hz)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class _Follow:
    frequency_hz: float
    talkgroup: Optional[int]
    source_id: Optional[int]
    since: float
    priority_rank: int


class P25TrunkController(TrunkController):
    protocol = Protocol.P25P1

    def __init__(
        self,
        policy: Optional[FollowPolicy] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_call_s: float = 300.0,
    ) -> None:
        super().__init__(policy)
        self.clock = clock
        self.max_call_s = max_call_s
        self.bandplan = P25BandPlan()
        self.system: dict[str, Any] = {}
        self.control_hz: Optional[float] = None
        self.alternates: list[float] = []  # other control channels heard (adjacent/secondary)
        self.cc_locked = False
        self.following: list[_Follow] = []
        self.counters = {"grants": 0, "followed": 0, "skipped_encrypted": 0, "skipped_policy": 0, "busy": 0, "preempted": 0}

    # -- engine interface ------------------------------------------------------

    def on_event(self, event_type: str, payload: Any) -> list[TuneRequest]:
        if event_type == "system_info" and isinstance(payload, SystemInfo):
            self._system_info(payload)
            return []
        if event_type == "trunk" and isinstance(payload, TrunkEvent):
            return self._trunk(payload)
        return []

    def on_voice_ended(self, frequency_hz: float) -> list[TuneRequest]:
        before = len(self.following)
        self.following = [f for f in self.following if abs(f.frequency_hz - frequency_hz) > 1.0]
        if before and not self.following and self.control_hz:
            return [TuneRequest(role="control", frequency_hz=self.control_hz, reason="return_to_control")]
        return []

    def tick(self) -> list[TuneRequest]:
        """Drop followers that outlived `max_call_s` (a lost release); returns to control if idle."""
        now = self.clock()
        stale = [f for f in self.following if now - f.since > self.max_call_s]
        out: list[TuneRequest] = []
        for f in stale:
            out += self.on_voice_ended(f.frequency_hz)
        return out

    def status(self) -> dict[str, Any]:
        return {
            "system": dict(self.system),
            "control_hz": self.control_hz,
            "cc_locked": self.cc_locked,
            "iden_entries": len(self.bandplan),
            "following": [
                {"frequency_hz": f.frequency_hz, "talkgroup": f.talkgroup, "source_id": f.source_id} for f in self.following
            ],
            "alternates": list(self.alternates),
            **self.counters,
        }

    # -- internals -------------------------------------------------------------

    def _system_info(self, info: SystemInfo) -> None:
        for key in ("wacn", "system_id", "nac", "rfss_id", "site_id"):
            value = getattr(info, key, None)
            if value is not None:
                self.system[key] = value
        if info.control_channel_hz:
            self.control_hz = info.control_channel_hz

    def _trunk(self, ev: TrunkEvent) -> list[TuneRequest]:
        detail = ev.detail or {}
        if ev.kind == "system_update":
            op = detail.get("opcode")
            if op in ("IDEN_UP", "IDEN_UP_VU") and "iden_fields" in detail:
                self.bandplan.update_from_fields(detail["iden_fields"])
            elif op in ("ADJ_STS_BCST", "RFSS_STS_BCST", "SECONDARY_CC"):
                hz = ev.frequency_hz or self._freq(detail.get("channel"))
                if hz and hz != self.control_hz and hz not in self.alternates:
                    self.alternates.append(hz)
                    del self.alternates[:-8]
            return []
        if ev.kind == "cc_acquired":
            self.cc_locked = True
            if ev.frequency_hz:
                self.control_hz = ev.frequency_hz
            return []
        if ev.kind == "cc_lost":
            self.cc_locked = False
            for hz in self.alternates:
                return [TuneRequest(role="control", frequency_hz=hz, reason="cc_lost_hunt", detail={"candidates": len(self.alternates)})]
            return []
        if ev.kind == "grant":
            return self._grant(ev)
        if ev.kind == "release" and ev.frequency_hz:
            return self.on_voice_ended(ev.frequency_hz)
        return []

    def _freq(self, channel: Optional[int]) -> Optional[float]:
        return None if channel is None else self.bandplan.frequency(int(channel))

    def _rank(self, talkgroup: Optional[int]) -> int:
        """Lower is more important; non-priority talkgroups share the last rank."""
        prio = self.policy.priority_talkgroups
        if talkgroup in prio:
            return prio.index(talkgroup)
        return len(prio)

    def _grant(self, ev: TrunkEvent) -> list[TuneRequest]:
        self.counters["grants"] += 1
        tg = ev.talkgroup
        freq = ev.frequency_hz or self._freq((ev.detail or {}).get("channel"))
        if freq is None:
            return []  # IDEN not heard yet: cannot place the call (counted as a grant only)
        if ev.encrypted and self.policy.skip_encrypted:
            self.counters["skipped_encrypted"] += 1
            return []
        if self.policy.hold_talkgroups and tg not in self.policy.hold_talkgroups:
            self.counters["skipped_policy"] += 1
            return []
        if tg in self.policy.skip_talkgroups:
            self.counters["skipped_policy"] += 1
            return []
        for f in self.following:
            if abs(f.frequency_hz - freq) <= 1.0:
                return []  # already there (grants repeat every couple of hundred ms)
        rank = self._rank(tg)
        emergency = bool((ev.detail or {}).get("emergency"))
        if len(self.following) >= max(1, self.policy.max_voice_followers):
            worst = max(self.following, key=lambda f: f.priority_rank)
            if rank < worst.priority_rank or (emergency and worst.priority_rank >= len(self.policy.priority_talkgroups)):
                self.following.remove(worst)
                self.counters["preempted"] += 1
            else:
                self.counters["busy"] += 1
                return []
        self.following.append(_Follow(freq, tg, ev.source_id, self.clock(), rank))
        self.counters["followed"] += 1
        return [
            TuneRequest(
                role="voice",
                frequency_hz=freq,
                reason=f"grant tg={tg} src={ev.source_id}",
                talkgroup=tg,
                detail={"emergency": emergency, "priority_rank": rank, **{k: v for k, v in (ev.detail or {}).items() if k in ("channel", "service_options")}},
            )
        ]
