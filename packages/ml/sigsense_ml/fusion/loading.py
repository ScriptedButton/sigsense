"""Safe weight loading: safetensors without the safetensors package, or tensor-only torch pickles."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import torch

_DTYPES = {"F32": np.float32, "F16": np.float16, "I64": np.int64, "I32": np.int32}


def read_safetensors(path: str | Path) -> dict[str, torch.Tensor]:
    """Parse a .safetensors file directly (header JSON + raw little-endian buffers)."""
    path = Path(path)
    with path.open("rb") as handle:
        (header_len,) = struct.unpack("<Q", handle.read(8))
        if header_len > 100 * 1024 * 1024:
            raise ValueError("implausible safetensors header")
        header = json.loads(handle.read(header_len))
        base = 8 + header_len
        out: dict[str, torch.Tensor] = {}
        for name, info in header.items():
            if name == "__metadata__":
                continue
            dtype = _DTYPES.get(info["dtype"])
            if dtype is None:
                raise ValueError(f"unsupported dtype {info['dtype']} for {name}")
            start, end = info["data_offsets"]
            handle.seek(base + start)
            buffer = handle.read(end - start)
            array = np.frombuffer(buffer, dtype=dtype).reshape(info["shape"]).copy()
            out[name] = torch.from_numpy(array)
    return out


def read_tensor_checkpoint(path: str | Path, key: str | None = None) -> dict[str, torch.Tensor]:
    """Load a torch checkpoint that must contain only tensors (weights_only=True refuses anything else)."""
    obj = torch.load(str(path), map_location="cpu", weights_only=True)
    if key is not None and isinstance(obj, dict) and key in obj:
        obj = obj[key]
    if not isinstance(obj, dict) or not all(isinstance(v, torch.Tensor) for v in obj.values()):
        raise ValueError("checkpoint is not a flat dict of tensors")
    return obj
