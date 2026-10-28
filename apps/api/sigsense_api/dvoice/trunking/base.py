"""Trunking interface (CONTRACT v1). Owner: architect (controllers: WP-C, WP-D).

A TrunkController consumes the typed events from a control-channel decoder and
returns `TuneRequest`s for the engine/SDR layer to execute (retune a voice
follower, or return to the control channel). It owns the follow POLICY (what to
follow) but never touches hardware: retuning is the engine's job (WP-G), which
may run one SDR as control-channel-only, or a second channelizer on the same
wideband capture for the voice channel (preferred for HackRF: no retune).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

from sigsense_api.dvoice.events import Protocol


@dataclass
class FollowPolicy:
    hold_talkgroups: set[int] = field(default_factory=set)  # follow only these when non-empty
    skip_talkgroups: set[int] = field(default_factory=set)
    priority_talkgroups: list[int] = field(default_factory=list)  # preempt order, first = highest
    skip_encrypted: bool = True  # never worth following: no audio
    hang_time_s: float = 2.0
    max_voice_followers: int = 1
    follow_data: bool = False


@dataclass
class TuneRequest:
    role: str  # "control" | "voice"
    frequency_hz: float
    reason: str  # "grant tg=123 src=456" | "return_to_control" | "cc_lost_hunt"
    talkgroup: Optional[int] = None
    slot: Optional[int] = None
    detail: dict[str, Any] = field(default_factory=dict)


class TrunkController(abc.ABC):
    protocol: Protocol

    def __init__(self, policy: Optional[FollowPolicy] = None) -> None:
        self.policy = policy or FollowPolicy()

    @abc.abstractmethod
    def on_event(self, event_type: str, payload: Any) -> list[TuneRequest]:
        """Feed every event from the control-channel decoder (system_info, trunk, ...)."""

    @abc.abstractmethod
    def on_voice_ended(self, frequency_hz: float) -> list[TuneRequest]:
        """The followed voice channel went idle past the hang time: usually 'return to control'."""

    def status(self) -> dict[str, Any]:
        return {}
