"""SigSense ML package: preprocess, model, library matching."""

from sigsense_ml.classes import SIGNAL_CLASSES, class_to_idx, idx_to_class
from sigsense_ml.model import SignalEmbeddingNet, load_model
from sigsense_ml.preprocess import (
    iq_to_spectrogram,
    load_iq_file,
    load_wav_iq,
    normalize_spectrogram,
    waterfall_image_to_spectrogram,
)
from sigsense_ml.meta import estimate_rf_meta
from sigsense_ml.ble import (
    decode_ble_iq,
    decode_unwhitened_pdu,
    decode_whitened_pdu,
    generate_ble_adv_iq,
    modulate_ble_packet_iq,
    encode_adv_pdu_crc,
    CRC_INIT_ADVERTISING,
)

__all__ = [
    "SIGNAL_CLASSES",
    "class_to_idx",
    "idx_to_class",
    "SignalEmbeddingNet",
    "load_model",
    "iq_to_spectrogram",
    "load_iq_file",
    "load_wav_iq",
    "normalize_spectrogram",
    "waterfall_image_to_spectrogram",
    "estimate_rf_meta",
    "decode_ble_iq",
    "generate_ble_adv_iq",
    "modulate_ble_packet_iq",
    "encode_adv_pdu_crc",
]
