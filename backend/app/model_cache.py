"""Validate cached safetensors metadata without materializing model tensors."""

import json
import os
from pathlib import Path


class ModelCacheIncompleteError(RuntimeError):
    def __init__(self, reason, *, errno=None):
        super().__init__("model_cache_incomplete")
        self.cache_reason = reason
        self.errno = errno


def validate_model_snapshot(path: str) -> None:
    """Require every indexed tensor and shard, including readable, valid file metadata."""
    from safetensors import SafetensorError, safe_open

    root = Path(path)
    index = root / "model.safetensors.index.json"
    if os.path.lexists(index):
        try:
            with index.open(encoding="utf-8") as stream:
                value = json.load(stream)
            weight_map = value["weight_map"]
            if not isinstance(weight_map, dict) or not weight_map:
                raise ValueError
            for tensor, name in weight_map.items():
                if (
                    not isinstance(tensor, str)
                    or not tensor
                    or not isinstance(name, str)
                    or "\0" in name
                    or Path(name).name != name
                    or not name.endswith(".safetensors")
                ):
                    raise ValueError
        except OSError as exc:
            raise ModelCacheIncompleteError("unreadable_index", errno=exc.errno) from None
        except (KeyError, TypeError, ValueError):
            raise ModelCacheIncompleteError("invalid_index") from None
        expected = {
            name: {tensor for tensor, filename in weight_map.items() if filename == name}
            for name in set(weight_map.values())
        }
    else:
        expected = {"model.safetensors": None}

    for name, tensors in expected.items():
        filename = root / name
        try:
            # Opening first preserves a useful errno for missing, broken or unreadable files.
            with filename.open("rb"):
                pass
            with safe_open(filename, framework="numpy") as weights:
                keys = set(weights.keys())
                if not keys or (tensors is not None and keys != tensors):
                    raise ModelCacheIncompleteError("index_tensor_mismatch")
        except OSError as exc:
            reason = "missing_shard" if isinstance(exc, FileNotFoundError) else "unreadable_shard"
            raise ModelCacheIncompleteError(reason, errno=exc.errno) from None
        except SafetensorError:
            raise ModelCacheIncompleteError("invalid_safetensors") from None
