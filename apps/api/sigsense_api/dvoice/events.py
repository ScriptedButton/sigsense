"""Typed events shared by the engine, the API and the web client (CONTRACT v1).

Everything a protocol decoder reports is one of the dataclasses below. The API
sends each as a JSON envelope (`make_event`); voice audio travels separately as
compact binary frames (`pack_audio_frame`). The TypeScript mirror lives in
apps/web/src/lib/sdr/dvoice/types.ts and must be kept in step (the contract
test checks the field names).

Owner of this file: the architect (change only via a CONTRACT_VERSION bump).
"""

from __future__ import annotations

import dataclasses
import enum
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice import CONTRACT_VERSION


class Protocol(str, enum.Enum):
    P25P1 = "p25p1"
    P25P2 = "p25p2"
    DMR = "dmr"
    NXDN48 = "nxdn48"
    NXDN96 = "nxdn96"
    DSTAR = "dstar"
    YSF = "ysf"
    M17 = "m17"
    # Reserved: named so the UI/config can refer to them, not implemented in v1.
    DPMR = "dpmr"
    X2TDMA = "x2tdma"
    PROVOICE = "provoice"
    EDACS = "edacs"
    UNKNOWN = "unknown"


class Modulation(str, enum.Enum):
    C4FM = "c4fm"  # 4-level FSK, discriminator path (P25 P1, DMR, YSF, M17, NXDN)
    FSK4 = "fsk4"  # alias class for non-shaped 4FSK (DMR/NXDN/M17 slicer path)
    CQPSK = "cqpsk"  # differential QPSK/LSM path (P25 P1 simulcast, P25 P2)
    GMSK = "gmsk"  # 2-level (D-STAR)


class CallKind(str, enum.Enum):
    GROUP = "group"
    PRIVATE = "private"
    ALL = "all"
    DATA = "data"
    TELEPHONE = "telephone"
    UNKNOWN = "unknown"


class EncryptionState(str, enum.Enum):
    CLEAR = "clear"
    ENCRYPTED = "encrypted"  # reported, never decoded
    UNKNOWN = "unknown"


class VocoderId(str, enum.Enum):
    IMBE_7200X4400 = "imbe_7200x4400"  # P25 Phase 1
    IMBE_7100X4400 = "imbe_7100x4400"  # ProVoice (reserved)
    AMBE_3600X2400 = "ambe_3600x2400"  # D-STAR
    AMBE_PLUS2_3600X2450 = "ambe_plus2_3600x2450"  # DMR, NXDN, YSF, P25 Phase 2
    CODEC2_3200 = "codec2_3200"  # M17 voice
    CODEC2_1600 = "codec2_1600"  # M17 voice (alt), reserved


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class EncryptionInfo:
    """What the air interface says about encryption. Informational only."""

    state: EncryptionState = EncryptionState.UNKNOWN
    algorithm_id: Optional[int] = None
    algorithm_name: Optional[str] = None  # e.g. "AES-256", "DES-OFB", "ADP" when known
    key_id: Optional[int] = None
    mi_present: bool = False
    # True only when the audio was clear and a vocoder produced PCM.
    audio_decoded: bool = False


@dataclass
class SystemInfo:
    protocol: Protocol
    # P25: wacn/system_id/nac/rfss/site. DMR: color_code/system code/net+site. NXDN: ran/site.
    wacn: Optional[int] = None
    system_id: Optional[int] = None
    nac: Optional[int] = None
    rfss_id: Optional[int] = None
    site_id: Optional[int] = None
    color_code: Optional[int] = None
    ran: Optional[int] = None
    network_id: Optional[int] = None
    name: Optional[str] = None
    control_channel_hz: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Talkgroup:
    id: int
    protocol: Protocol
    label: Optional[str] = None
    last_heard: Optional[float] = None  # epoch seconds
    call_count: int = 0
    encrypted_calls: int = 0


@dataclass
class Call:
    """One transmission. `call_id` is stable for its life; updates re-send it."""

    call_id: str
    protocol: Protocol
    slot: Optional[int] = None  # DMR / P25 P2 timeslot (0/1); None for single-slot
    kind: CallKind = CallKind.UNKNOWN
    target_id: Optional[int] = None  # talkgroup / destination
    source_id: Optional[int] = None  # radio id / source
    frequency_hz: Optional[float] = None
    channel: Optional[str] = None  # channel name/number if known
    started_at: float = 0.0  # epoch seconds
    updated_at: float = 0.0
    ended_at: Optional[float] = None
    encryption: EncryptionInfo = field(default_factory=EncryptionInfo)
    emergency: bool = False
    priority: Optional[int] = None
    voice_frames: int = 0
    voice_frames_bad: int = 0  # frames the FEC could not repair
    ber: Optional[float] = None
    end_reason: Optional[str] = None  # "terminator" | "timeout" | "sync_loss" | "retune"
    meta: dict[str, Any] = field(default_factory=dict)  # alias text, GPS, service options...


@dataclass
class VoiceFrame:
    """One vocoder frame's decoded audio (not JSON: sent as a binary frame)."""

    call_id: str
    seq: int
    vocoder: VocoderId
    pcm: Optional[np.ndarray] = None  # int16 mono, 160 samples @ 8 kHz, None if unavailable
    sample_rate: int = 8000
    errors: int = 0  # bit errors the FEC corrected in this frame
    timestamp: float = 0.0


@dataclass
class LinkStats:
    """Receiver health, emitted every ~500 ms per channel."""

    channel: str = "ch0"
    protocol: Optional[Protocol] = None
    state: str = "searching"  # searching | synced | voice | lost
    sync_ratio: float = 0.0  # share of recent frame slots that hit sync (0..1)
    ber: Optional[float] = None  # corrected-bit ratio over the last window
    fec_corrected: int = 0
    fec_failed: int = 0
    frames_ok: int = 0
    frames_bad: int = 0
    symbol_rate: float = 0.0
    snr_db: Optional[float] = None
    freq_error_hz: Optional[float] = None
    level: Optional[float] = None
    polarity_inverted: bool = False


@dataclass
class SyncEvent:
    protocol: Protocol
    pattern: str  # name in sync.patterns
    errors: int  # symbol errors in the sync word
    inverted: bool
    symbol_index: int


@dataclass
class TrunkEvent:
    """Control-channel derived: grants, system updates, hunting."""

    kind: str  # cc_acquired | cc_lost | grant | release | system_update | hunting
    frequency_hz: Optional[float] = None
    talkgroup: Optional[int] = None
    source_id: Optional[int] = None
    slot: Optional[int] = None
    encrypted: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorEvent:
    code: str  # e.g. "vocoder_unavailable", "sync_lost", "input_stalled", "unsupported_protocol"
    severity: Severity = Severity.WARNING
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


# JSON event types the API may send (payload class in comments).
EVENT_TYPES: tuple[str, ...] = (
    "status",  # EngineStatus dict (see engine.py)
    "link_stats",  # LinkStats
    "sync",  # SyncEvent
    "system_info",  # SystemInfo
    "call_start",  # Call
    "call_update",  # Call
    "call_end",  # Call
    "talkgroup",  # Talkgroup
    "trunk",  # TrunkEvent
    "error",  # ErrorEvent
)


def to_jsonable(obj: Any) -> Any:
    """Dataclasses/enums/numpy -> plain JSON types. `VoiceFrame.pcm` is dropped."""
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in dataclasses.fields(obj):
            if f.name == "pcm":
                continue
            out[f.name] = to_jsonable(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def make_event(
    type_: str, payload: Any, *, channel: str = "ch0", t: Optional[float] = None
) -> dict[str, Any]:
    """The JSON envelope: {"v":1,"type":...,"t":epoch_ms,"channel":...,"data":{...}}."""
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type_!r}")
    return {
        "v": CONTRACT_VERSION,
        "type": type_,
        "t": int(round((time.time() if t is None else t) * 1000)),
        "channel": channel,
        "data": to_jsonable(payload),
    }


# --- binary audio frames -------------------------------------------------------
#
# WS /dvoice/stream sends voice as binary messages (JSON events are text):
#   offset size field
#   0      1    magic 0xA7
#   1      1    version (=CONTRACT_VERSION)
#   2      1    slot (0/1, 0xFF = none)
#   3      1    flags (bit0: end-of-call marker, bit1: frame had uncorrectable errors)
#   4      4    call_num  (u32 LE, per-engine counter; Call.meta["call_num"] maps call_id)
#   8      4    seq       (u32 LE, voice frame number within the call)
#   12     2    sample_rate (u16 LE, normally 8000)
#   14     2    n_samples   (u16 LE)
#   16     2*n  int16 LE PCM, mono
AUDIO_MAGIC = 0xA7
AUDIO_HEADER = struct.Struct("<BBBBIIHH")  # 16 bytes
AUDIO_FLAG_END = 0x01
AUDIO_FLAG_ERRORS = 0x02


def pack_audio_frame(
    call_num: int,
    seq: int,
    pcm: np.ndarray,
    *,
    slot: Optional[int] = None,
    sample_rate: int = 8000,
    end_of_call: bool = False,
    had_errors: bool = False,
) -> bytes:
    samples = np.ascontiguousarray(pcm, dtype="<i2")
    flags = (AUDIO_FLAG_END if end_of_call else 0) | (AUDIO_FLAG_ERRORS if had_errors else 0)
    header = AUDIO_HEADER.pack(
        AUDIO_MAGIC,
        CONTRACT_VERSION,
        0xFF if slot is None else int(slot) & 0xFF,
        flags,
        int(call_num) & 0xFFFFFFFF,
        int(seq) & 0xFFFFFFFF,
        int(sample_rate),
        int(samples.size),
    )
    return header + samples.tobytes()


def unpack_audio_frame(data: bytes) -> tuple[dict[str, Any], np.ndarray]:
    if len(data) < AUDIO_HEADER.size:
        raise ValueError("audio frame shorter than its header")
    magic, version, slot, flags, call_num, seq, rate, n = AUDIO_HEADER.unpack_from(data)
    if magic != AUDIO_MAGIC:
        raise ValueError("bad audio frame magic")
    if len(data) != AUDIO_HEADER.size + 2 * n:
        raise ValueError("audio frame length does not match n_samples")
    pcm = np.frombuffer(data, dtype="<i2", offset=AUDIO_HEADER.size, count=n).astype(np.int16)
    return (
        {
            "version": version,
            "slot": None if slot == 0xFF else slot,
            "end_of_call": bool(flags & AUDIO_FLAG_END),
            "had_errors": bool(flags & AUDIO_FLAG_ERRORS),
            "call_num": call_num,
            "seq": seq,
            "sample_rate": rate,
        },
        pcm,
    )
