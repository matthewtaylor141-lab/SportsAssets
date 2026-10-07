from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


@dataclass
class ModelCard:
    model_name: str
    version: str
    code_sha: str
    data_sha: str
    train_end: str
    feature_schema_sha: str
    hyperparameters: dict
    calibration: dict
    validation: dict
    limitations: list[str]
    capital_status: str = "RESEARCH_ONLY"

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return sha256_bytes(self.canonical_json().encode())

    def write(self, path: str | Path) -> None:
        payload = asdict(self)
        payload["model_card_sha256"] = self.fingerprint()
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
