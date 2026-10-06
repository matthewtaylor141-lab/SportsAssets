"""Durable same-book certification semantics.

Evidence survives process restarts and is invalidated only when the contract
identity/schema fingerprint changes.
"""
from __future__ import annotations
import hashlib, json

VERSION="SAME_BOOK_CERTIFICATION_V1"
MIN_SAMPLES=30
MIN_AGREEMENT=0.95


def fingerprint(identity: dict) -> str:
    stable={k:identity.get(k) for k in ("venue","contract_id","institutional_symbol",
                                        "price_transform","price_scale","qty_scale",
                                        "ontology_version","book_schema_version")}
    return hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def verdict(*, comparable:int, agreeing:int, identity:dict) -> dict:
    ratio=None if comparable <= 0 else agreeing/float(comparable)
    supported=comparable >= MIN_SAMPLES and ratio is not None and ratio >= MIN_AGREEMENT
    return {"status":"SUPPORTED" if supported else "ACCUMULATING",
            "comparable":int(comparable),"agreeing":int(agreeing),"agreement":ratio,
            "min_samples":MIN_SAMPLES,"min_agreement":MIN_AGREEMENT,
            "fingerprint":fingerprint(identity),"version":VERSION}


def can_reuse(existing:dict|None, identity:dict)->bool:
    return bool(existing and existing.get("status")=="SUPPORTED" and
                existing.get("fingerprint")==fingerprint(identity))
