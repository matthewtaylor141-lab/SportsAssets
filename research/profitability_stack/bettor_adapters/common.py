"""Shared, read-only plumbing for the BETTOR adapters of the Profitability Stack.

RESEARCH / PAPER-SHADOW ONLY. Nothing here can reach a database, a network, a
credential, an order path or capital: the adapters read extracts that the
SELECT-only research-SQL workflow already produced, and write receipt files.

Conventions every adapter shares:
  * the statistical unit is the canonical EVENT (one game / fixture), never a
    decision row: many evaluations of one game are not independent;
  * a cost or a measurement the records do not contain is UNMEASURED (None plus
    a reason), never silently zero;
  * a book is oriented to the side held: a SHORT holding of a PMUS market is
    the complement view of the one LONG book (bid = 1 - ask, ask = 1 - bid).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STACK = HERE.parent
REPO = STACK.parents[1]
if str(STACK) not in sys.path:
    sys.path.insert(0, str(STACK))

AUTHORITY = "RESEARCH_PAPER_SHADOW_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
VENUE_PMUS = "POLYMARKET_US"
FEE_QTY = 100            # fee per contract is quoted at this order size
Z = 1.645                # one-sided 95% for every conservative bound

# PMUS market-slug prefixes -> family. Used only when the premap row carries no
# sports_type; the source of the label is recorded next to it.
PREFIX_FAMILY = {"aec": "MONEYLINE", "atc": "TEAM_TO_WIN_3WAY",
                 "asc": "SPREAD", "tsc": "TOTAL"}
_LOG_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s")
_EVENT_RE = re.compile(r"^[a-z]+-([a-z0-9]+(?:-[a-z0-9]+)*?-\d{4}-\d{2}-\d{2})")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p) -> str:
    return sha256_bytes(Path(p).read_bytes())


def dataset_sha(rows) -> str:
    return sha256_bytes("\n".join(json.dumps(r, sort_keys=True) for r in rows).encode())


def code_sha(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        h.update(Path(p).name.encode() + b"\0" + Path(p).read_bytes())
    return h.hexdigest()


def parse_extract_log(text: str, first_key: str) -> list[dict]:
    """research-sql log -> rows: one JSON object per line, keyed by first_key."""
    out, lead = [], '{"%s"' % first_key
    for ln in text.splitlines():
        # a GitHub log line can keep its BOM + timestamp prefix
        s = _LOG_PREFIX.sub("", ln.lstrip("\ufeff")).strip()
        if s.startswith(lead):
            out.append(json.loads(s))
    return out


def read_jsonl_gz(p) -> list[dict]:
    with gzip.open(p, "rt") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def event_identity(row: dict) -> tuple[str | None, str]:
    """(canonical event key, its source). The premap event_slug wins; otherwise
    the event is the market slug minus its 3-letter family prefix and outcome
    suffix (aec-mlb-sd-mil-2026-10-03 -> mlb-sd-mil-2026-10-03), which is the
    same key the premap uses where it is present."""
    ev = row.get("event") or row.get("event_id")
    if ev:
        return str(ev), "PREMAP_EVENT_SLUG"
    m = _EVENT_RE.match(str(row.get("slug") or ""))
    if m:
        return m.group(1), "SLUG_DERIVED"
    return None, "NO_EVENT_IDENTITY"


def sport_of(row: dict) -> str:
    if row.get("league"):
        return str(row["league"]).lower()
    ev, _ = event_identity(row)
    return ev.split("-")[0] if ev else "UNKNOWN"


def family_of(row: dict) -> str:
    st = row.get("sports_type")
    if st:
        return str(st).upper()
    return PREFIX_FAMILY.get(str(row.get("slug") or "")[:3], "UNKNOWN")


def price_band(p) -> str:
    if p is None:
        return "PRICE_UNKNOWN"
    p = float(p)
    for hi, lab in ((0.2, "00-20"), (0.4, "20-40"), (0.6, "40-60"), (0.8, "60-80")):
        if p < hi:
            return lab
    return "80-100"


def regime_of(at, start) -> str:
    """PREGAME / IN_PLAY relative to the premap game start; unknown start is
    reported as such rather than guessed."""
    if at is None or start is None:
        return "REGIME_UNKNOWN"
    return "PREGAME" if float(at) < float(start) else "IN_PLAY"


def orient(bid, ask, side: str):
    """LONG book -> (bid, ask) of the side held. None when either is missing
    or the book is crossed/locked."""
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    if side == "SHORT":
        bid, ask = 1.0 - ask, 1.0 - bid
    if not (0.0 <= bid < ask <= 1.0):
        return None
    return bid, ask


def mid(b):
    return None if b is None else 0.5 * (b[0] + b[1])


# ── fees: BETTOR's own published-schedule implementation ─────────────────
def _fees_module():
    root = REPO / "backend"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from sportsassets import calibration_fees as CF   # pure: decimal only
    return CF


def fee_per_contract(price: float, role: str, at_iso: str | None):
    """(fee per contract, provenance). Positive = taker charge, negative =
    maker rebate. (None, reason) when the schedule refuses: UNMEASURED."""
    CF = _fees_module()
    r = CF.ROLE_TAKER if role == "TAKER" else CF.ROLE_MAKER
    got = CF.expected_fee(float(price), FEE_QTY, role=r, at=at_iso) or {}
    if got.get("BLOCKER") or got.get("FEE") is None:
        return None, got.get("BLOCKER") or "NO_FEE_STATED"
    return float(got["FEE"]) / FEE_QTY, "calibration_fees.expected_fee(%s, qty=%d, %s)" % (
        role, FEE_QTY, got.get("schedule"))


def iso(ts) -> str | None:
    import datetime as dt
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(ts_list) -> dict:
    ts = [float(t) for t in ts_list if t is not None]
    return {"from": iso(min(ts)) if ts else None, "to": iso(max(ts)) if ts else None}


def cluster_mean_ci(values, clusters, z: float = Z) -> dict:
    """Mean with a cluster-robust SE: the event is the unit, so the effective
    n is the number of distinct events, not rows."""
    import numpy as np
    from collections import defaultdict
    g = defaultdict(list)
    for v, c in zip(values, clusters):
        g[c].append(float(v))
    n_rows = sum(len(x) for x in g.values())
    k = len(g)
    if k == 0:
        return {"n_rows": 0, "events": 0, "mean": None, "lower": None, "upper": None, "se": None}
    mean = sum(sum(x) for x in g.values()) / n_rows
    if k < 2:
        return {"n_rows": n_rows, "events": k, "mean": mean, "lower": None, "upper": None,
                "se": None, "status": "UNMEASURED_ONE_EVENT"}
    resid = np.array([sum(x) - mean * len(x) for x in g.values()])
    se = float(np.sqrt(k / (k - 1) * (resid ** 2).sum()) / n_rows)
    return {"n_rows": n_rows, "events": k, "mean": mean, "lower": mean - z * se,
            "upper": mean + z * se, "se": se}


def receipt_envelope(kind: str, payload: dict, *, code: str, data: dict, generated_at: str) -> dict:
    """Package Receipt (kind/version/payload/sha256) with the provenance every
    adapter receipt must carry."""
    from bettor_profit_stack.common import Receipt
    payload = dict(payload)
    payload["provenance"] = {"authority": AUTHORITY, "code_sha256": code,
                             "datasets": data, "generated_at": generated_at}
    return Receipt(kind, "bettor-adapter-v1", payload).as_dict()
