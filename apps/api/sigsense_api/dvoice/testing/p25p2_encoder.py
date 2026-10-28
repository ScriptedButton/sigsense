"""P25 Phase 2 fragment / superframe encoder for tests and synthetic captures. Original code.

Builds the on-air dibit stream the decoder expects (layout in protocols/p25p2/framing.py): fragments of
four slots, I-ISCH on slots A/B, the S-ISCH sync word on slots C/D, scrambled voice (4V/2V with ESS) and
MAC (SACCH/FACCH/LCCH) bodies. It uses the shared constants and the MAC/ESS code *encoders* in framing.py
but none of the decoder's parsing, so a round trip exercises both directions of every field. Output is
dibits in the SymbolBlock convention (dsp/types.py); feed them to `testing.modulator` for IQ.

Like the decoder this follows SDRTrunk's published layout, so the round trip proves self-consistency only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from sigsense_api.dvoice.protocols.dmr.bits import bits_to_dibits
from sigsense_api.dvoice.protocols.p25p2 import framing as fr
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

SYNC_BITS = np.array([(v >> s) & 1 for v in SYNC_SPECS["p25p2"].values for s in (1, 0)], dtype=np.uint8)
ALG_CLEAR = 0x80


def put(bits: np.ndarray, start: int, width: int, value: int) -> None:
    for i in range(width):
        bits[start + i] = (value >> (width - 1 - i)) & 1


def default_voice_bits(counter: int) -> np.ndarray:
    """72 pseudo-random bits whose first six encode `counter % 64` (lets tests check frame order)."""
    rng = np.random.default_rng(5000 + counter)
    bits = rng.integers(0, 2, 72).astype(np.uint8)
    put(bits, 0, 6, counter % 64)
    return bits


# --- MAC payload builders (information bits before CRC) ---------------------------------------

def ptt_pdu(group: int, source: int, algorithm: int = ALG_CLEAR, key_id: int = 0, mi: bytes = bytes(9)) -> np.ndarray:
    b = np.zeros(144, dtype=np.uint8)
    put(b, 0, 3, 1)
    put(b, 8, 72, int.from_bytes(mi, "big"))
    put(b, 80, 8, algorithm)
    put(b, 88, 16, key_id)
    put(b, 104, 24, source)
    put(b, 128, 16, group)
    return b


def end_ptt_pdu(group: int, source: int, nac: int = 0) -> np.ndarray:
    b = np.zeros(144, dtype=np.uint8)
    put(b, 0, 3, 2)
    put(b, 12, 12, nac)
    put(b, 104, 24, source)
    put(b, 128, 16, group)
    return b


def active_pdu(group: int, source: int, service: int = 0, size: int = 168) -> np.ndarray:
    """MAC_ACTIVE carrying one Group Voice Channel User (abbreviated) structure."""
    b = np.zeros(size, dtype=np.uint8)
    put(b, 0, 3, 4)
    put(b, 8, 8, 0x01)
    put(b, 16, 8, service)
    put(b, 24, 16, group)
    put(b, 40, 24, source)
    return b


def idle_pdu(size: int = 144) -> np.ndarray:
    b = np.zeros(size, dtype=np.uint8)
    put(b, 0, 3, 3)
    return b


def network_status_pdu(wacn: int, system_id: int, nac: int, channel: int = 0x1001, lra: int = 1, size: int = 168) -> np.ndarray:
    """MAC_SIGNAL carrying a Network Status Broadcast (implicit)."""
    b = np.zeros(size, dtype=np.uint8)
    put(b, 8, 8, 0x7B)
    put(b, 16, 8, lra)
    put(b, 24, 20, wacn)
    put(b, 44, 12, system_id)
    put(b, 56, 4, channel >> 12)
    put(b, 60, 12, channel & 0xFFF)
    put(b, 72, 8, 0x70)
    put(b, 84, 12, nac)
    return b


def grant_pdu(group: int, source: int, channel: int, service: int = 0, size: int = 168) -> np.ndarray:
    """MAC_SIGNAL carrying a Group Voice Channel Grant (implicit)."""
    b = np.zeros(size, dtype=np.uint8)
    put(b, 8, 8, 0x40)
    put(b, 16, 8, service)
    put(b, 24, 4, channel >> 12)
    put(b, 28, 12, channel & 0xFFF)
    put(b, 40, 16, group)
    put(b, 56, 24, source)
    return b


@dataclass
class Slot:
    """What one timeslot carries. `kind`: voice4 | voice2 | sacch | facch | lcch | datch."""

    kind: str
    frames: list = field(default_factory=list)  # voice: 72-bit frames (4 or 2)
    ess: Optional[np.ndarray] = None  # voice4: 24-bit ESS-B piece; voice2: 168-bit ESS-A
    payload: Optional[np.ndarray] = None  # MAC information bits without CRC
    scrambled: bool = True  # MAC slots may be sent unscrambled


@dataclass
class P25P2Encoder:
    wacn: int = 0xBEE00
    system_id: int = 0x1C7
    nac: int = 0x013
    voice_bits = staticmethod(default_voice_bits)

    # ------------------------------------------------------------------ slots

    def body(self, slot: Slot, slot_index: int) -> np.ndarray:
        b = np.zeros(fr.BODY_BITS, dtype=np.uint8)
        if slot.kind in ("voice4", "voice2"):
            duid = fr.Duid.VOICE_4 if slot.kind == "voice4" else fr.Duid.VOICE_2
            offs = fr.VOICE4_OFFSETS if slot.kind == "voice4" else fr.VOICE2_OFFSETS
            if len(slot.frames) != len(offs):
                raise ValueError(f"{slot.kind} carries {len(offs)} frames")
            for off, frame in zip(offs, slot.frames):
                b[off : off + 72] = frame
            if slot.kind == "voice4":
                b[fr.ESS_B[0] : fr.ESS_B[1]] = np.zeros(24, np.uint8) if slot.ess is None else slot.ess
            else:
                ess_a = np.zeros(168, np.uint8) if slot.ess is None else slot.ess
                b[148:244] = ess_a[:96]
                b[246:318] = ess_a[96:]
            scrambled = True
        elif slot.kind in ("sacch", "facch", "lcch"):
            channel = {"sacch": fr.SACCH, "facch": fr.FACCH, "lcch": fr.LCCH}[slot.kind]
            fr.encode_mac(slot.payload, channel, b)
            if slot.kind == "lcch":
                duid, scrambled = fr.Duid.LCCH_U, False
            else:
                scrambled = slot.scrambled
                suffix = "S" if scrambled else "U"
                duid = getattr(fr.Duid, f"{slot.kind.upper()}_{suffix}")
        elif slot.kind == "datch":
            duid, scrambled = fr.Duid.DATCH_S, True
        else:
            raise ValueError(slot.kind)
        fr.write_duid(b, int(duid))
        if scrambled:
            b ^= fr.scramble_segment((self.wacn, self.system_id, self.nac), slot_index)
            fr.write_duid(b, int(duid))  # the DUID bits are never scrambled
        return b

    # ------------------------------------------------------------------ fragments

    def fragment(self, slots: list[Slot], seq: int, superframe: int = 0, lch_type: int = 0,
                 corrupt_isch: bool = False) -> np.ndarray:
        """Four slots (A..D) -> 720 dibits."""
        if len(slots) != 4:
            raise ValueError("a fragment has four slots")
        bits = np.zeros(1440, dtype=np.uint8)
        for ch in (0, 1):
            word = fr.encode_isch(fr.Isch(lch_type, ch, seq, 0, superframe))
            put(bits, 360 * ch, 40, word)
        bits[720 : 720 + 40] = SYNC_BITS
        bits[1080 : 1080 + 40] = SYNC_BITS
        for pos, slot in enumerate(slots):
            bits[360 * pos + 40 : 360 * pos + 360] = self.body(slot, fr.SLOTS_PER_FRAGMENT * seq + pos)
        if corrupt_isch:
            bits[0:40] ^= 1
            bits[360:400] ^= 1
        return bits_to_dibits(bits)

    def superframe(self, per_channel: list[list[Slot]], superframe: int = 0, first_seq: int = 0) -> list[np.ndarray]:
        """Two logical channels x 6 ordinals -> three fragments. Slot order within a fragment follows
        framing.slot_channel: A=ch0, B=ch1, C/D = ch0/ch1 (swapped in the final fragment)."""
        out = []
        for seq in range(3):
            final = seq == 2
            slots = []
            for pos in range(4):
                ch = fr.slot_channel(pos, final)
                slots.append(per_channel[ch][fr.channel_ordinal(seq, pos)])
            out.append(self.fragment(slots, seq, superframe))
        return out

    # ------------------------------------------------------------------ calls

    def voice_superframe(self, counter: int, algorithm: int = ALG_CLEAR, key_id: int = 0, mi: bytes = bytes(9),
                         group: int = 0, source: int = 0, service: int = 0, first: Optional[Slot] = None) -> list[Slot]:
        """One logical channel's six slots: 4V,4V,4V,4V,2V,SACCH(ACTIVE). `first` replaces the first slot."""
        ess_b, ess_a = fr.encode_ess(algorithm, key_id, mi)
        slots = []
        n = counter
        for i in range(4):
            slots.append(Slot("voice4", [self.voice_bits(n + j) for j in range(4)], ess_b[i]))
            n += 4
        slots.append(Slot("voice2", [self.voice_bits(n), self.voice_bits(n + 1)], ess_a))
        slots.append(Slot("sacch", payload=active_pdu(group, source, service), scrambled=True))
        if first is not None:
            slots[0] = first
        return slots

    @staticmethod
    def idle_superframe() -> list[Slot]:
        return [Slot("facch", payload=idle_pdu(), scrambled=False) for _ in range(6)]

    def voice_call(self, group: int, source: int, superframes: int = 3, algorithm: int = ALG_CLEAR, key_id: int = 0,
                   mi: bytes = bytes(9), channel: int = 0, service: int = 0, with_ptt: bool = True,
                   lead_idle: int = 1, tail_idle: int = 1) -> list[np.ndarray]:
        """A call on one logical channel (the other idles): FACCH PTT first (it takes the first 4V slot, so the
        decoder must cope with a missing ESS-B1), END-PTT last. Returns fragments in transmit order."""
        frags: list[np.ndarray] = []
        sf = 0

        def emit(mine: list[Slot]) -> None:
            nonlocal sf
            both = [self.idle_superframe(), self.idle_superframe()]
            both[channel] = mine
            frags.extend(self.superframe(both, sf))
            sf = (sf + 1) % 4

        for _ in range(lead_idle):
            emit(self.idle_superframe())
        n = 0
        for i in range(superframes):
            first = None
            if i == 0 and with_ptt:
                first = Slot("facch", payload=ptt_pdu(group, source, algorithm, key_id, mi), scrambled=True)
            emit(self.voice_superframe(n, algorithm, key_id, mi, group, source, service, first))
            n += 18  # a superframe uses 4*4 + 2 frame counters
        end = self.idle_superframe()
        end[0] = Slot("facch", payload=end_ptt_pdu(group, source, self.nac), scrambled=True)
        emit(end)
        for _ in range(tail_idle):
            emit(self.idle_superframe())
        return frags


def assemble_stream(fragments: list[np.ndarray], lead_dibits: int = 0, noise_seed: int = 0) -> np.ndarray:
    """Concatenate fragments, optionally behind random lead-in symbols."""
    rng = np.random.default_rng(noise_seed)
    lead = rng.integers(0, 4, lead_dibits).astype(np.uint8)
    return np.concatenate([lead] + list(fragments)).astype(np.uint8)
