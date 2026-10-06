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


#: the book schema the certified comparison was made against
BOOK_SCHEMA_VERSION = "INSTITUTIONAL_STREAM_V1/RETAIL_BOOK_V1"
ONTOLOGY_VERSION = "CONTRACT_ONTOLOGY_V1"


def identity_for(symbol: str, *, price_scale, qty_scale,
                 price_transform="IDENTITY", venue="POLYMARKET_US") -> dict:
    """(integration) THE ONE IDENTITY BOTH SIDES FINGERPRINT: the workers'
    certifier and the API's held-mark lane build it from the same fields, so
    a certificate is reused only for the same contract, scales, transform,
    ontology and book schema. Scales are normalised to int (refdata carries
    strings, the identity mapper ints)."""
    def _i(v):
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return None
    return {"venue": venue, "contract_id": str(symbol),
            "institutional_symbol": str(symbol),
            "price_transform": price_transform or "IDENTITY",
            "price_scale": _i(price_scale), "qty_scale": _i(qty_scale),
            "ontology_version": ONTOLOGY_VERSION,
            "book_schema_version": BOOK_SCHEMA_VERSION}
