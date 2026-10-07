"""BETTOR records -> the EV Probability Engine's input frame (read-only).

Source: research/ev_extract_v1.sql (SELECT-only research-SQL workflow). One
row per decision, oriented to the side the decision would hold:

  p_venue  the venue's own no-vig price: mid of the venue book the decision
           recorded (PMUS quotes YES and NO from one book, so the mid is the
           no-vig probability of the held side)
  p_sharp  the devigged Pinnacle probability the decision recorded
  p_raw    the probability the decision actually USED (economics.probability;
           falls back to p_sharp, and the source is kept)
  p_int    BETTOR's internal model probability, where one was recorded
  p_close  the venue mid of the last book before the event start, when that
           book exists and is two-sided (closing line; otherwise None)

Identity: the statistical unit is the canonical EVENT (premap event_slug,
else the market slug minus its family prefix / outcome suffix); a settled
market is a slug. Repeated 15-minute evaluations are rows, never events.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

VENUE = "POLYMARKET_US"
_LOG_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s")
_EVENT_RE = re.compile(r"^[a-z]+-([a-z0-9]+(?:-[a-z0-9]+)*?-\d{4}-\d{2}-\d{2})")
PREFIX_FAMILY = {"aec": "MONEYLINE", "atc": "MONEYLINE_3WAY", "asc": "SPREAD", "tsc": "TOTAL"}
FEE_QTY = 100


def parse_log(text: str) -> list[dict]:
    """research-sql log -> JSON rows (one object per line; BOM / timestamp
    prefixes stripped)."""
    out = []
    for ln in text.splitlines():
        s = _LOG_PREFIX.sub("", ln.lstrip("﻿")).strip()
        if s.startswith('{"') and s.endswith("}"):
            out.append(json.loads(s))
    return out


def declared_rows(text: str) -> int | None:
    """psql's '(N rows)' footer, with log BOM / timestamp prefixes removed."""
    for ln in text.splitlines():
        s = _LOG_PREFIX.sub("", ln.lstrip("\ufeff")).strip()
        m = re.fullmatch(r"\((\d+) rows?\)", s)
        if m:
            return int(m.group(1))
    return None


def event_identity(r: dict) -> tuple[str | None, str]:
    if r.get("event"):
        return str(r["event"]), "PREMAP_EVENT_SLUG"
    m = _EVENT_RE.match(str(r.get("slug") or ""))
    return (m.group(1), "SLUG_DERIVED") if m else (None, "NO_EVENT_IDENTITY")


def family_class(slug: str, sports_type) -> str:
    """moneyline / 3-way / spread / total / team total. Alternate lines are not
    flagged in the records, so ALTERNATE is never claimed."""
    pre = (slug or "")[:3]
    if pre == "tsc" and "-tt-" in slug:
        return "TEAM_TOTAL"
    return PREFIX_FAMILY.get(pre, "UNKNOWN")


def orient(bid, ask, side):
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    if side == "SHORT":
        bid, ask = 1.0 - ask, 1.0 - bid
    if not (0.0 < bid < ask < 1.0):
        return None
    return bid, ask


def band(x, edges, labels, unknown="UNKNOWN"):
    if x is None:
        return unknown
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]


def _fees():
    root = REPO / "backend"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from sportsassets import calibration_fees as CF
    return CF


def taker_fee(price: float, at: float):
    """(fee per contract, provenance) from BETTOR's published schedule; None
    when the schedule refuses (UNMEASURED, never zero)."""
    import datetime as dt
    CF = _fees()
    iso = dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    got = CF.expected_fee(float(price), FEE_QTY, role=CF.ROLE_TAKER, at=iso) or {}
    if got.get("BLOCKER") or got.get("FEE") is None:
        return None, got.get("BLOCKER") or "NO_FEE_STATED"
    return float(got["FEE"]) / FEE_QTY, "calibration_fees.expected_fee(TAKER,%d,%s)" % (FEE_QTY, got.get("schedule"))


def frame_rows(raw: list[dict]) -> tuple[list[dict], dict]:
    """Oriented, typed rows + a count of every row dropped and why."""
    out, dropped = [], {}

    def drop(why):
        dropped[why] = dropped.get(why, 0) + 1

    for r in raw:
        side = r.get("side")
        b = orient(r.get("bid"), r.get("ask"), side)
        if b is None:
            drop("NO_TWO_SIDED_VENUE_BOOK"); continue
        if r.get("y_long") is None:
            drop("NO_SETTLED_OUTCOME"); continue
        p_sharp = r.get("p_pin")
        # the probability the decision's policy used: economics.probability;
        # DEREK records none there and decides on p_blended (Pinnacle blended
        # with its internal model); otherwise the Pinnacle probability
        if r.get("p_used") is not None:
            p_raw, raw_src = r["p_used"], "ECONOMICS_PROBABILITY"
        elif r.get("p_blend") is not None:
            p_raw, raw_src = r["p_blend"], "P_BLENDED"
        else:
            p_raw, raw_src = p_sharp, "P_PINNACLE_FALLBACK"
        if p_raw is None:
            drop("NO_DECISION_PROBABILITY"); continue
        ev, ev_src = event_identity(r)
        if ev is None:
            drop("NO_EVENT_IDENTITY"); continue
        at = float(r["at"])
        start = r.get("start")
        y = float(r["y_long"]) if side != "SHORT" else 1.0 - float(r["y_long"])
        cb = orient(r.get("close_bid"), r.get("close_ask"), side)
        p_close = 0.5 * (cb[0] + cb[1]) if (cb and r.get("close_at") and start
                                            and float(r["close_at"]) <= float(start)) else None
        fee, fee_basis = taker_fee(b[1], at)
        ages = [x for x in (r.get("prob_age_s"), r.get("v_age_s")) if x is not None]
        src_age = max(ages) if ages else None
        tts = (float(start) - at) / 60.0 if start else None
        p_venue = 0.5 * (b[0] + b[1])
        sport = (r.get("league") or (ev.split("-")[0] if ev else "unknown")).lower()
        fam = (r.get("sports_type") or family_class(r["slug"], None)).upper()
        regime = "REGIME_UNKNOWN" if start is None else ("PREGAME" if at < float(start) else "IN_PLAY")
        out.append({
            "decision_id": r["id"], "at": at, "event_id": ev, "event_src": ev_src,
            "market_id": r["slug"], "side": side, "strategy": r.get("strategy"),
            "verdict": r.get("verdict"), "n_bucket": int(r.get("n_bucket") or 1),
            "sport": sport, "league": (r.get("league") or "UNKNOWN").lower(),
            "family": family_class(r["slug"], r.get("sports_type")), "family_detail": fam,
            "family_class": family_class(r["slug"], r.get("sports_type")),
            "regime": regime, "venue": VENUE,
            "settlement_identity": "|".join(str(x) for x in (
                r.get("v_settlement_rule"), r.get("v_payout_event"), r.get("v_identity_basis"))),
            "registry_settlement": r.get("registry_settlement") or "NOT_IN_REGISTRY",
            "p_venue": p_venue, "p_sharp": None if p_sharp is None else float(p_sharp),
            "p_raw": float(p_raw), "raw_src": raw_src,
            "p_int": None if r.get("p_int") is None else float(r["p_int"]),
            "p_blend": None if r.get("p_blend") is None else float(r["p_blend"]),
            "p_close": p_close, "bid": b[0], "ask": b[1], "spread": b[1] - b[0],
            "top_qty": r.get("bid_q") if side == "SHORT" else r.get("ask_q"),
            "fee": fee, "fee_basis": fee_basis, "outcome": y,
            "settled_at": r.get("settled_at"), "start": start,
            "time_to_start_min": tts, "source_age_s": src_age,
            "prob_age_s": r.get("prob_age_s"), "book_age_s": r.get("book_age_s"),
            "v_age_s": r.get("v_age_s"),
            "model_id": "|".join(str(x) for x in (r.get("strategy"), r.get("prob_basis"),
                                                  r.get("policy_version"), r.get("v_provider"),
                                                  r.get("v_book"), r.get("v_devig"))),
            "favorite": "FAVORITE" if p_venue >= 0.5 else "LONGSHOT",
            "price_band": band(b[1], (0.2, 0.4, 0.6, 0.8), ("00-20", "20-40", "40-60", "60-80", "80-100")),
            "prob_band": band(float(p_raw), (0.2, 0.4, 0.6, 0.8), ("00-20", "20-40", "40-60", "60-80", "80-100")),
            "tts_band": band(tts, (0, 60, 360, 1440), ("IN_PLAY_OR_PAST_START", "0-1h", "1-6h", "6-24h", "24h+")),
            # decisions only exist with sources < 30 s old (the 30 s probability rule)
            "freshness_band": band(src_age, (5, 10, 20, 30), ("0-5s", "5-10s", "10-20s", "20-30s", "30s+")),
            "label_src": r.get("label_src"),
        })
    return out, dropped


def write_jsonl_gz(path: Path, rows) -> str:
    import hashlib
    data = ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode()
    with open(path, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(data)
    return hashlib.sha256(data).hexdigest()


def read_jsonl_gz(path) -> list[dict]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(x) for x in fh if x.strip()]
