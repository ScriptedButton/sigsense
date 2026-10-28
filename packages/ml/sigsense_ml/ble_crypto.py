"""Passive BLE AES-CCM authentication/decryption with supplied session material.

Keys use normal AES/MSO-first hex notation. SKD and IV octet strings are in
transmission order (central portion followed by peripheral portion). Counters
and direction must come from the capture; this module never guesses keys.
"""

from __future__ import annotations


def derive_session_key(ltk: bytes, skd_on_air: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(ltk) != 16 or len(skd_on_air) != 16:
        raise ValueError("LTK and combined SKD must each contain 16 bytes")
    cipher = Cipher(algorithms.AES(ltk), modes.ECB()).encryptor()
    return cipher.update(skd_on_air[::-1]) + cipher.finalize()


def decrypt_payload(
    *,
    session_key: bytes,
    iv_on_air: bytes,
    header: bytes,
    ciphertext_mic: bytes,
    packet_counter: int,
    direction: str,
) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM

    if len(session_key) != 16 or len(iv_on_air) != 8:
        raise ValueError(
            "Session key must contain 16 bytes and IV must contain 8 bytes"
        )
    if len(header) != 2 or header[0] & 0x20:
        raise ValueError(
            "Decryption currently accepts two-octet ACL headers without CTEInfo"
        )
    if header[1] != len(ciphertext_mic) or len(ciphertext_mic) < 4:
        raise ValueError("Header length does not match ciphertext plus four-byte MIC")
    if not 0 <= packet_counter < 1 << 39:
        raise ValueError("Packet counter must fit in 39 bits")
    if direction not in {"central_to_peripheral", "peripheral_to_central"}:
        raise ValueError("Specify central_to_peripheral or peripheral_to_central")
    counter = bytearray(packet_counter.to_bytes(5, "little"))
    counter[4] |= 0x80 if direction == "central_to_peripheral" else 0
    nonce = bytes(counter) + iv_on_air
    aad = bytes([header[0] & 0xE3])  # SN/NESN/MD are not authenticated.
    try:
        return AESCCM(session_key, tag_length=4).decrypt(nonce, ciphertext_mic, aad)
    except InvalidTag as exc:
        raise ValueError("MIC verification failed; plaintext was not released") from exc
