"""NXDN protocol package (CONTRACT v1)."""

from sigsense_api.dvoice.protocols.nxdn.decoder import Nxdn48Decoder, Nxdn96Decoder

DECODERS = (Nxdn48Decoder, Nxdn96Decoder)
