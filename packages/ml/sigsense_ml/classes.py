"""Signal catalog. Keep existing indices stable; checkpoints carry their own labels."""

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
    # Additional modulation orders backed by TorchSig generators.
    "ASK4", "ASK8", "ASK16",
    "FSK4", "FSK8", "FSK16", "GFSK4", "GFSK8",
    "MSK", "GMSK", "16PSK", "32PSK",
    "QAM32", "QAM256", "APSK16", "APSK32",
    # Captured-data classes supplied by the compact RadioPile bundle.
    "5G-NR", "ITS-G5", "C-V2X", "DVB-T",
]

CAPTURE_ONLY_CLASSES = frozenset({"5G-NR", "ITS-G5", "C-V2X", "DVB-T"})

# Current local generators are proxies for these services, not labeled protocol data.
# Exclude them from training by default until verified captures are available.
PROXY_CLASSES = frozenset({"AIS", "APRS", "LTE", "ACARS"})

class_to_idx: dict[str, int] = {name: i for i, name in enumerate(SIGNAL_CLASSES)}
idx_to_class: dict[int, str] = {i: name for i, name in enumerate(SIGNAL_CLASSES)}

NUM_CLASSES = len(SIGNAL_CLASSES)
EMBEDDING_DIM = 128
SPECTROGRAM_SIZE = 256
