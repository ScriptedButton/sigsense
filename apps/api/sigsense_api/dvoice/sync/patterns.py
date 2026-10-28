"""Frame-sync patterns as dibit strings (CONTRACT v1). Owner: WP-B.

WP-B changes: `nxdn_fsw` now holds the EXACT dibits of the 20-bit word 0xCDF59
("3031331121": its sign pattern is the reference's "3131331131", so the contract test
still holds). The previous outer-only string could never match the true word exactly
(two inner symbols), so exact-dibit matching would have needed more errors than allowed.
This constant comes from the commonly cited FSW value; the NXDN TS is paywalled, so WP-E
should confirm it with a second source.

Each digit is a dibit VALUE 0..3 in the convention of dsp/types.py ('1' = the +3
symbol, '3' = the -3 symbol, '0' = +1, '2' = -1). These are public air-interface
constants (TIA-102, ETSI TS 102 361 / 102 490, NXDN, JARL D-STAR, YSF, M17 specs).
They were cross-checked against the reference architecture's published table
and, in the tests, against their hexadecimal forms.

NOTE (found while cross-checking): the reference table stores SIGN-ONLY patterns
('1' = positive symbol, '3' = negative), which is identical to the exact word for
sync words made of outer symbols only (P25, DMR, M17, NXDN FSW) but collapses
inner symbols for words that contain them (the YSF FSW). We store EXACT dibits
here and expose `signs` for sign-only matching, which is more tolerant of slicer
level errors; WP-B chooses per protocol and tests both. Re-verify every entry
against the spec section and add the missing ones (YSF FICH/data, D-STAR header
vs data sync, NXDN variants, P25 P2 inverted-polarity handling).
"""

from __future__ import annotations

from dataclasses import dataclass

from sigsense_api.dvoice.events import Protocol


@dataclass(frozen=True)
class SyncSpec:
    name: str
    protocol: Protocol
    dibits: str  # e.g. "111113113311333313133333"
    symbol_rate: float
    max_errors: int  # tolerated symbol errors when searching (tuned by tests)
    note: str = ""

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(int(c) for c in self.dibits)

    @property
    def signs(self) -> tuple[int, ...]:
        """Sign-only form: 0 = positive symbol (dibit 0/1), 2 = negative (dibit 2/3)."""
        return tuple(v & 2 for v in self.values)

    @property
    def inverted_values(self) -> tuple[int, ...]:
        """Same word received with inverted polarity (dibit d -> d ^ 2)."""
        return tuple(v ^ 2 for v in self.values)


SYNC_SPECS: dict[str, SyncSpec] = {
    s.name: s
    for s in (
        SyncSpec("p25p1", Protocol.P25P1, "111113113311333313133333", 4800, 4, "0x5575F5FF77FF"),
        SyncSpec("p25p2", Protocol.P25P2, "11131131111333133333", 6000, 3, "20-symbol frame sync"),
        SyncSpec("dmr_bs_voice", Protocol.DMR, "131111333113313313113313", 4800, 4, "0x755FD7DF75F7"),
        SyncSpec("dmr_bs_data", Protocol.DMR, "313333111331131131331131", 4800, 4, "0xDFF57D75DF5D"),
        SyncSpec("dmr_ms_voice", Protocol.DMR, "133313311131311113313331", 4800, 4, "0x7F7D5DD57DFD"),
        SyncSpec("dmr_ms_data", Protocol.DMR, "311131133313133331131113", 4800, 4, "0xD5D7F77FD757"),
        SyncSpec("nxdn_fsw", Protocol.NXDN96, "3031331121", 4800, 1, "FSW 0xCDF59 (20 bits, 10 symbols; has 2 inner symbols). Exact form derived from the commonly cited constant - verify against NXDN TS 1-A"),
        SyncSpec("ysf_fsw", Protocol.YSF, "31101301302112031031", 4800, 3, "0xD471C9634D (exact; has inner symbols)"),
        SyncSpec("m17_lsf", Protocol.M17, "11113313", 4800, 1, "0x55F7"),
        SyncSpec("m17_stream", Protocol.M17, "33331131", 4800, 1, "0xFF5D"),
        SyncSpec("m17_bert", Protocol.M17, "31331111", 4800, 1, "0xDF55"),
        SyncSpec("m17_packet", Protocol.M17, "13113333", 4800, 1, "0x75FF"),
        SyncSpec("dstar_header", Protocol.DSTAR, "131313131333133113131111", 4800, 3, "2-level; WP-E verifies"),
        SyncSpec("dstar_sync", Protocol.DSTAR, "313131313133131113313111", 4800, 3, "2-level; WP-E verifies"),
    )
}


def dibits_to_hex(dibits: str) -> str:
    """Debug helper: '1111...' -> hex string (two bits per digit as value->bits)."""
    bits = "".join(f"{int(c):02b}" for c in dibits)
    return f"0x{int(bits, 2):0{len(bits) // 4}X}"
