"""EDGE DECAY READERS (LAB-A): the database reader and the production
extraction loader. Both produce the SAME per-opportunity record the pure
core (`sportsassets.lab.edge_decay.evaluate`) consumes, and every row is
read through the lab's ONE point-in-time accessor (`sportsassets.lab.pit`):
the database reader with `pit.read` (SQL clock filter + post-check), the
extraction loader by the core's per-instant `pit.visible` calls (the
extraction carries each row's own recorded stamps).

FORWARD STAGE STAMPS: when the canonical decision intent table exists and
carries the intent stream's `latency_stages` column, the reader attaches
them (and the PAPER adapter record's `refs.stages`) to the record as
`intent`; the core's latency chain consumes them. Absent, the historical
record stands and Karen / Allie / Eddie stay UNAVAILABLE with their reason.

Read only. Imports nothing from any order, venue, execution or funded module.
"""
from __future__ import annotations

import json
import re

from . import edge_decay as ED
from . import pit as PIT

DEFAULT_DAYS = 7.0
MAX_DECISIONS = 1500
EXTRACT_VERSION = "LAB_A_EDGE_DECAY_EXTRACT_V1"


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _has_column(conn, table: str, column: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        " WHERE table_schema = current_schema() AND table_name = $1 "
        "   AND column_name = $2)", table, column))


async def _has_table(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def load_records(conn, *, now: float, since: float, until: float | None = None,
                       limit: int = MAX_DECISIONS) -> dict:
    """THE QUALIFIED OPPORTUNITIES decided in [since, until) and everything
    recorded about them up to `now`, as evaluate() records. Returns
    {records, derek_v2_rows, bounded}."""
    until = float(now if until is None else until)
    lim = max(1, min(int(limit), MAX_DECISIONS))
    decs = await PIT.read(
        conn, "paper_decisions", clock=now,
        columns=["decision_id", "strategy", "policy_version", "verdict",
                 "refusals", "us_market_slug", "holding_side", "intent",
                 "fixture", "valuation_id", "p_pinnacle", "proposed_qty",
                 "limit_price", "book_obs_id", "label", "economics",
                 "pinnacle"],
        where=("strategy = $1 AND book_obs_id IS NOT NULL "
               "AND decided_at >= to_timestamp($2) "
               "AND decided_at < to_timestamp($3) "
               "AND (verdict = 'ENTER' OR (refusals <@ $4::text[] "
               "     AND cardinality(refusals) > 0))"),
        args=(ED.CG_STRATEGY, float(since), until,
              sorted(ED.ECONOMIC_REFUSALS)),
        order="decided_at", limit=lim)
    derek = await PIT.read(
        conn, "paper_decisions", clock=now,
        columns=["decision_id", "verdict"],
        where=("strategy = $1 AND decided_at >= to_timestamp($2) "
               "AND decided_at < to_timestamp($3)"),
        args=(ED.DEREK_V2, float(since), until), limit=PIT.MAX_LIMIT)
    out = {"records": [], "derek_v2_rows": len(derek),
           "bounded": len(decs) >= lim}
    if not decs:
        return out
    for d in decs:
        for k in ("label", "economics", "pinnacle"):
            d[k] = _j(d.get(k)) or {}
        econ = d["economics"]
        acq = econ.get("acquisition") or {}
        d["economics"] = {
            "threshold_edge_pp": econ.get("threshold_edge_pp"),
            "best_level_edge_pp": econ.get("best_level_edge_pp"),
            "depth_within_limit": econ.get("depth_within_limit"),
            "expected_net_profit_usd": acq.get("expected_net_profit_usd"),
            "fees_usd": acq.get("fees_usd"), "qty": acq.get("qty"),
            "vwap": acq.get("vwap"),
            "sport_family": (econ.get("mapping_assumptions") or {}).get(
                "sport_family")}
    obs_ids = sorted({d["book_obs_id"] for d in decs})
    b0s = {b["obs_id"]: b for b in await PIT.read(
        conn, "paper_book_observations", clock=now,
        columns=["obs_id", "us_market_slug", "bids", "offers", "error"],
        where="obs_id = ANY($1::bigint[])", args=(obs_ids,),
        limit=PIT.MAX_LIMIT)}
    slugs = sorted({d["us_market_slug"] for d in decs if d["us_market_slug"]})
    t_lo = min(d["decided_at"] for d in decs)
    t_hi = max(d["decided_at"] for d in decs) + ED.MAX_HORIZON_S
    later = await PIT.read(
        conn, "paper_book_observations", clock=now,
        columns=["obs_id", "us_market_slug", "bids", "offers"],
        where=("us_market_slug = ANY($1::text[]) AND error IS NULL "
               "AND observed_at > to_timestamp($2) "
               "AND observed_at <= to_timestamp($3)"),
        args=(slugs, t_lo, t_hi), order="observed_at", limit=PIT.MAX_LIMIT)
    by_slug: dict = {}
    for b in later:
        by_slug.setdefault(b["us_market_slug"], []).append(b)
    vids = sorted({d["valuation_id"] for d in decs
                   if d.get("valuation_id") is not None})
    v0s = {v["id"]: v for v in await PIT.read(
        conn, "external_valuations", clock=now,
        columns=["id", "probability", "payout_event", "buy_intent",
                 "sport_family", "market", "settlement_comparison",
                 "observed_at"],
        where="id = ANY($1::bigint[])", args=(vids,), limit=PIT.MAX_LIMIT)}
    lvals = await PIT.read(
        conn, "external_valuations", clock=now,
        columns=["id", "us_market_slug", "probability", "payout_event",
                 "buy_intent", "observed_at"],
        where=("experiment_id = $1 AND us_market_slug = ANY($2::text[]) "
               "AND probability IS NOT NULL "
               "AND decided_at > to_timestamp($3) "
               "AND decided_at <= to_timestamp($4)"),
        args=("EXT_PINNACLE_DEVIG_V1_SHADOW", slugs, t_lo, t_hi),
        order="decided_at", limit=PIT.MAX_LIMIT)
    vals_by_slug: dict = {}
    for v in lvals:
        vals_by_slug.setdefault(v["us_market_slug"], []).append(v)
    enter_ids = [d["decision_id"] for d in decs if d["verdict"] == "ENTER"]
    orders = await PIT.read(
        conn, "paper_orders", clock=now,
        columns=["order_id", "decision_id", "qty", "limit_price",
                 "order_type", "time_in_force", "eligible_at", "expires_at",
                 "state", "filled_qty", "terminal_at", "terminal_reason"],
        where="decision_id = ANY($1::text[]) AND role = 'ENTRY'",
        args=(enter_ids,), limit=PIT.MAX_LIMIT) if enter_ids else []
    oid_dec = {o["order_id"]: o["decision_id"] for o in orders}
    fills = await PIT.read(
        conn, "paper_fills", clock=now,
        columns=["order_id", "qty", "price", "fee_usd", "book_obs_id",
                 "book_observed_at"],
        where="order_id = ANY($1::text[])", args=(sorted(oid_dec),),
        order="filled_at", limit=PIT.MAX_LIMIT) if oid_dec else []
    xis = {x["decision_id"]: x for x in await PIT.read(
        conn, "execution_intents", clock=now,
        columns=["decision_id", "actual_state", "actual_refusal", "timeline"],
        where="decision_id = ANY($1::text[])", args=(enter_ids,),
        limit=PIT.MAX_LIMIT)} if enter_ids and await _has_table(
        conn, "execution_intents") else {}
    atts = {}
    for a in await PIT.read(
            conn, "paper_evaluation_attempts", clock=now,
            columns=["attempt_id", "decision_id", "elapsed_s", "via"],
            where="decision_id = ANY($1::text[]) AND outcome = 'DECIDED'",
            args=([d["decision_id"] for d in decs],), order="attempt_id",
            limit=PIT.MAX_LIMIT):
        atts.setdefault(a["decision_id"], a)
    intents = await _forward_intents(conn, now=now, ids=enter_ids)
    for d in decs:
        side = d.get("holding_side")
        b = b0s.get(d["book_obs_id"]) or {}
        v0 = v0s.get(d.get("valuation_id")) or {}
        sc = _j(v0.get("settlement_comparison")) or {}
        rec = {
            "d": d,
            "b0": {"obs_id": b.get("obs_id"),
                   "observed_at": b.get("observed_at"),
                   "recorded_at": b.get("recorded_at"),
                   "error": b.get("error"),
                   "levels": ED.side_of({"bids": _j(b.get("bids")),
                                         "offers": _j(b.get("offers"))},
                                        side) or []} if b else {},
            "v0": {"id": v0.get("id"), "observed_at": v0.get("observed_at"),
                   "received_at": v0.get("received_at"),
                   "decided_at": v0.get("decided_at"),
                   "probability": v0.get("probability"),
                   "payout_event": v0.get("payout_event"),
                   "buy_intent": v0.get("buy_intent"),
                   "sport_family": v0.get("sport_family"),
                   "market": v0.get("market"),
                   "quote_context": sc.get("quote_context")},
            "books": [{"obs_id": x["obs_id"], "observed_at": x["observed_at"],
                       "recorded_at": x["recorded_at"],
                       "levels": ED.side_of({"bids": _j(x.get("bids")),
                                             "offers": _j(x.get("offers"))},
                                            side) or []}
                      for x in by_slug.get(d["us_market_slug"], [])
                      if x["obs_id"] != d["book_obs_id"]
                      and d["decided_at"] < x["observed_at"]
                      <= d["decided_at"] + ED.MAX_HORIZON_S],
            "vals": [{"valuation_id": v["id"], "p": v["probability"],
                      "observed_at": v["observed_at"],
                      "received_at": v["received_at"],
                      "decided_at": v["decided_at"]}
                     for v in vals_by_slug.get(d["us_market_slug"], [])
                     if v["id"] != d.get("valuation_id")
                     and v.get("buy_intent") == v0.get("buy_intent")
                     and v.get("payout_event") == v0.get("payout_event")
                     and d["decided_at"] < v["decided_at"]
                     <= d["decided_at"] + ED.MAX_HORIZON_S],
            "orders": [o for o in orders
                       if o["decision_id"] == d["decision_id"]],
            "fills": [[f["filled_at"], f["recorded_at"], f["qty"], f["price"],
                       f["fee_usd"], f["book_obs_id"], f["book_observed_at"]]
                      for f in fills
                      if oid_dec.get(f["order_id"]) == d["decision_id"]],
            "xi": ({"actual_state": xis[d["decision_id"]]["actual_state"],
                    "timeline": _j(xis[d["decision_id"]]["timeline"]) or {}}
                   if d["decision_id"] in xis else None),
            "att": atts.get(d["decision_id"]),
            "intent": intents.get(d["decision_id"])}
        out["records"].append(rec)
    return out


async def _forward_intents(conn, *, now: float, ids: list) -> dict:
    """{decision_id: {latency_stages, adapter_stages}} from the canonical
    decision intents, when the table exists. The stage column is the intent
    stream's (`latency_stages`); its absence leaves the stages empty and the
    core reports them UNAVAILABLE with NOT_STAMPED."""
    if not ids or not await _has_table(conn, "canonical_decision_intents"):
        return {}
    cols = ["intent_id", "decision_id"]
    stages = await _has_column(conn, "canonical_decision_intents",
                               "latency_stages")
    if stages:
        cols.append("latency_stages")
    rows = await PIT.read(conn, "canonical_decision_intents", clock=now,
                          columns=cols, where="decision_id = ANY($1::text[])",
                          args=(ids,), limit=PIT.MAX_LIMIT)
    out = {}
    adapter = {}
    if rows and await _has_table(conn, "canonical_intent_executions"):
        for e in await PIT.read(
                conn, "canonical_intent_executions", clock=now,
                columns=["intent_id", "adapter", "refs"],
                where="intent_id = ANY($1::text[]) AND adapter = 'PAPER'",
                args=([r["intent_id"] for r in rows],), limit=PIT.MAX_LIMIT):
            refs = _j(e.get("refs")) or {}
            st = dict(refs.get("stages") or {})
            st.setdefault("intent_recorded_at", e.get("created_at"))
            adapter[e["intent_id"]] = st
    for r in rows:
        out[r["decision_id"]] = {
            "intent_id": r["intent_id"],
            "latency_stages": (_j(r.get("latency_stages")) or {})
            if stages else {},
            "adapter_stages": adapter.get(r["intent_id"]) or {},
            "stage_column_present": stages}
    return out


# ─────────────────────────── the production extraction ─────────────────

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ")
_SHA = re.compile(r"^== sha256 ([0-9a-f]{64}) ==")


def parse_extract(text: str) -> dict:
    """The rows of research/lab_a_edge_decay_extract.sql as printed by the
    research-sql workflow (one JSON object per line, behind the runner's
    timestamp prefix). Returns {records, rows_declared, sql_sha256}."""
    recs, declared, sha = [], None, None
    started = False
    for line in text.splitlines():
        body = _TS.sub("", line.lstrip("\ufeff"), count=1)
        if EXTRACT_VERSION + " rows follow" in body:
            started = True
            continue
        m = _SHA.search(body)
        if m and sha is None:
            sha = m.group(1)
        m = re.match(r"^\((\d+) rows?\)$", body.strip())
        if m and started:
            declared = int(m.group(1))
            continue
        s = body.strip()
        if started and s.startswith("{") and s.endswith("}"):
            try:
                recs.append(json.loads(s))
            except ValueError:
                continue
    for r in recs:
        b0 = r.get("b0") or {}
        if b0 and b0.get("levels") is None:
            b0["levels"] = []
    return {"records": recs, "rows_declared": declared, "sql_sha256": sha}
