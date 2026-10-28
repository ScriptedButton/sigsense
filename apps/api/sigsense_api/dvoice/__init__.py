"""SigSense digital voice engine (DSD-family architecture, original code).

Architecture and ownership: docs/digital-voice-architecture.md. Contract
version: CONTRACT_VERSION (bumped on any breaking change to events.py, the
fec/ signatures, ProtocolDecoder, Vocoder or the WebSocket framing).

Scope policy: clear (unencrypted) voice and signalling only. Encryption is
DETECTED and REPORTED (algorithm/key ids, message-indicator presence). There is
no key recovery, brute force, or key handling anywhere in this package.
"""

CONTRACT_VERSION = 1
