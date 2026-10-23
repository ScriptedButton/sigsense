"""v1 signal class taxonomy for SigSense."""

SIGNAL_CLASSES: list[str] = [
    # Analog
    "AM",
    "NFM",
    "WFM",
    "USB",
    "LSB",
    "CW",
    # Digital modulation
    "ASK",
    "FSK",
    "GFSK",
    "BPSK",
    "QPSK",
    "8PSK",
    "QAM16",
    "QAM64",
    "OFDM",
    # Protocols
    "ADS-B",
    "AIS",
    "APRS",
    "LoRa",
    "WiFi",
    "Bluetooth-LE",
    "Zigbee",
    "LTE",
    "GSM",
    "DMR",
    "P25",
    "ACARS",
    # Radar / pulsed
    "Pulse",
    "Chirp-LFM",
    "FMCW",
    # Noise / clutter
    "Noise",
    "Impulsive",
]

class_to_idx: dict[str, int] = {name: i for i, name in enumerate(SIGNAL_CLASSES)}
idx_to_class: dict[int, str] = {i: name for i, name in enumerate(SIGNAL_CLASSES)}

NUM_CLASSES = len(SIGNAL_CLASSES)
EMBEDDING_DIM = 128
SPECTROGRAM_SIZE = 256
