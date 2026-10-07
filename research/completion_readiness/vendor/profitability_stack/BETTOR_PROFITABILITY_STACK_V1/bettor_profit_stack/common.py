from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path

EPS = 1e-12

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())

def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def fingerprint(obj) -> str:
    return sha256_bytes(canonical_json(obj).encode())

@dataclass(frozen=True)
class Receipt:
    kind: str
    version: str
    payload: dict

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "version": self.version, "payload": self.payload}
        d["sha256"] = fingerprint(d)
        return d

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n")
