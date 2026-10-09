"""CAPITAL-CRITICAL (research path): THE PROFITABILITY WAREHOUSE IS BUILT
OFF THE API EVENT LOOP, OVER ONE INDEX OF ITS CONTEXT, A SLICE AT A TIME, AND
BUILDS THE SAME RECORDS IT BUILT BEFORE.

THE EVIDENCE. RC6.1 production, 2026-10-09 (render-ops logs run
37949540216; the API loop watchdog's persisted ring, research-sql
rc6_api-responsive_loop_stalls.sql run 37950182966): at 14:48Z the hourly
profitability cycle held the API event loop 8.7 s -- task
profitability/runner.py:run, the loop thread in warehouse.py `rows` <-
`build` <- runner `warehouse` -- every position filtering every context list
(reviews, ledger, findings, regimes ... up to reads.MAX_ROWS rows each) on
the loop.

Pinned here:
  * the records are EXACTLY the pre-index build's (`_build_before` below is
    warehouse.build verbatim from b3f1b0cd) on production-shaped context --
    groups sharing rows, rows of no position, a row without an optional
    field, regimes with ties, an unsorted regime list, repeated previous
    revisions and valuations, a list absent from the database (None) --
    position by position, through `build_all` and slice by slice through
    `build_some`;
  * the runner builds them off the loop -- in the API (its lifespan installs
    the lane) on the CPU lane's thread, elsewhere on asyncio.to_thread -- a
    spy on the build's thread through the cycle;
  * a production-size block (reads' row bounds) leaves the loop's longest
    gap below the watchdog's 1.0 s lag threshold;
  * THE LANE STAYS AVAILABLE: a Command read's real lane job submitted while
    that block is built finishes in under LANE_WAIT_S (tests/_lane_probe.py),
    where one job building the whole block kept it waiting for the block;
    and the cycle's economics compute stays on its own asyncio.to_thread, as
    on b3f1b0cd, never queued on the lane.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import random
import threading
import time

import pytest

from sportsassets import cpu_lane
from sportsassets.profitability import common as C
from sportsassets.profitability import warehouse as WH
from sportsassets.profitability.warehouse import (ARRAYS, INT_ARRAYS,
                                                  VERSION, _stage, _union,
                                                  content_sha)

try:
    from tests._lane_probe import (LANE_WAIT_S, command_reads_while,
                                   production_gil)
except ImportError:                                             # pragma: no cover
    from _lane_probe import LANE_WAIT_S, command_reads_while, production_gil

#: the API loop watchdog's lag threshold (loop_watchdog.LAG_LOG_S)
WATCHDOG_LAG_S = 1.0


# ═════════════════════════════════════════════════════════════════════
# the pre-index build, VERBATIM from b3f1b0cd (renamed only)
# ═════════════════════════════════════════════════════════════════════



def _build_before(pos: dict, econ: dict | None, *, ctx: dict,
          decisions: dict) -> dict:
    """The lineage record of ONE position (store adds run id, revision)."""
    g = pos.get("group_id")
    slug = pos.get("us_market_slug")
    side = pos.get("holding_side")
    src = pos.get("src") or {}
    book = pos["book"]
    kind = "PAPER" if book == "PAPER" else "ACTUAL"
    dids = list(src.get("decision_ids") or [])
    decs = [decisions[d] for d in dids if d in decisions]
    vids = sorted({int(d["valuation_id"]) for d in decs
                   if d.get("valuation_id") is not None}
                  | set(src.get("valuation_ids") or []))
    vals = {int(v["id"]): v for v in (ctx.get("valuations") or [])}
    myvals = [vals[v] for v in vids if v in vals]

    def rows(name, pred):
        lst = ctx.get(name)
        return None if lst is None else [r for r in lst if pred(r)]

    ids_all = {g, pos["position_key"], *dids}
    reviews = rows("reviews", lambda r: r["group_id"] == g) if book == \
        "PAPER" else rows("actual_reviews", lambda r: r["group_id"] == g)
    theses = rows("theses", lambda r: r["group_id"] == g
                  and r["position_kind"] == kind
                  and r.get("us_market_slug") in (slug, None))
    assess = rows("assessments", lambda r: r["group_id"] == g
                  and r["position_kind"] == kind)
    vadd = rows("value_add", lambda r: r["group_id"] == g
                and r["position_kind"] == kind)
    pms = rows("postmortems", lambda r: r["group_id"] == g
               and r["book"] == book and r.get("us_market_slug") in (slug, None)
               and str(r.get("holding_side") or side) == str(side))
    chal = rows("challenges", lambda r: r["target_id"] in ids_all)
    finds = rows("findings", lambda r: r["subject"] in ids_all)
    adec = rows("agent_decisions", lambda r: r["subject"] in ids_all)
    ledger = rows("ledger", lambda r: r["group_id"] == g and (
        r.get("position_key") in (None, pos["position_key"]))) \
        if book == "PAPER" else []
    attr = rows("attribution", lambda r: r["subject_id"] == g
                and r["book"] == book)
    siz = rows("sizing", lambda r: r["decision_id"] in dids)
    alloc = rows("allocations", lambda r: r["decision_id"] in dids)
    cap = rows("capacity", lambda r: r["candidate_id"] in dids)
    intents = rows("intents", lambda r: r["group_id"] == g
                   or r.get("decision_id") in dids)
    handoffs = list(src.get("handoff_ids") or [])
    if book == "PAPER":
        handoffs += [r["handoff_id"] for r in (rows(
            "paper_handoffs", lambda r: r["group_id"] == g) or [])]
    regimes = []
    rg = ctx.get("regimes")
    for d in decs:
        t = C.epoch(d.get("decided_at"))
        if rg and t is not None:
            before = [r for r in rg if r["at"] <= t]
            if before:
                regimes.append(before[-1]["run_id"])

    def ids(lst, key):
        return [] if lst is None else [r[key] for r in lst]

    rec = {
        "book": book, "position_key": pos["position_key"],
        "venue": pos.get("venue"), "account_id": pos.get("account_id"),
        "group_id": g, "us_market_slug": slug, "holding_side": side,
        "strategy": pos.get("strategy"),
        "opened_at": (econ or {}).get("opened_at"),
        "closed_at": (econ or {}).get("released_at"),
        "state": (econ or {}).get("state") or "OPEN",
        "valuation_ids": vids, "decision_ids": dids,
        "intent_ids": sorted(set(src.get("intent_ids") or [])
                             | set(ids(intents, "intent_id"))),
        "order_ids": list(src.get("order_ids") or []),
        "fill_ids": list(src.get("fill_ids") or []),
        "book_obs_ids": sorted(set(src.get("book_obs_ids") or [])
                               | {int(d["book_obs_id"]) for d in decs
                                  if d.get("book_obs_id") is not None}),
        "settlement_ids": list(src.get("settlement_ids") or []),
        "ledger_seqs": ids(ledger, "seq"),
        "review_ids": ids(reviews, "review_id"),
        "handoff_ids": sorted(set(handoffs)),
        "thesis_ids": ids(theses, "thesis_id"),
        "assessment_ids": ids(assess, "assessment_id"),
        "value_add_ids": ids(vadd, "value_add_id"),
        "postmortem_keys": ["%s|%s" % (r["book"], r["position_key"])
                            for r in (pms or [])],
        "challenge_ids": ids(chal, "challenge_id"),
        "audit_finding_ids": ids(finds, "finding_id"),
        "agent_decision_refs": ids(adec, "decision_ref"),
        "attribution_refs": ["%s|%s" % (r["book"], r["subject_id"])
                             for r in (attr or [])],
        "sizing_refs": ids(siz, "decision_id"),
        "allocation_refs": ["%s|%s" % (r["run_id"], r["candidate_id"])
                            for r in (alloc or [])],
        "regime_run_ids": sorted(set(regimes)),
        "capacity_ids": ids(cap, "capacity_id"),
        "model_versions": sorted(
            {"valuation:%s" % v["version"] for v in myvals if v.get("version")}
            | {"devig:%s" % v["devig_method"] for v in myvals
               if v.get("devig_method")}
            | {"internal_model:%s" % d["model_version"] for d in decs
               if d.get("model_version")}),
        "policy_versions": sorted({d["policy_version"] for d in decs
                                   if d.get("policy_version")}
                                  | set(src.get("policy_versions") or [])
                                  | {r["policy_version"]
                                     for r in (intents or [])
                                     if r.get("policy_version")}),
        "experiment_ids": sorted({v["experiment_id"] for v in myvals
                                  if v.get("experiment_id")}),
        "simulator_versions": sorted(set(src.get("simulator_versions") or [])
                                     | {d["simulator_version"] for d in decs
                                        if d.get("simulator_version")}),
    }
    # ── union with the previous revision (append-only knowledge) ─────
    prev = next((p for p in (ctx.get("previous") or [])
                 if p["book"] == book
                 and p["position_key"] == pos["position_key"]), None)
    if prev:
        for k in ARRAYS:
            rec[k] = _union(prev.get(k), rec[k], ints=k in INT_ARRAYS)
    else:
        for k in ARRAYS:
            rec[k] = _union([], rec[k], ints=k in INT_ARRAYS)
    # ── stages ────────────────────────────────────────────────────────
    p = pos.get("probability")
    st = {}
    st["CANDIDATE"] = _stage(rec["valuation_ids"], rec["valuation_ids"],
                             "NO_VALUATION_LINKED_TO_THE_DECISION")
    st["PROBABILITY"] = _stage(p is not None, rec["decision_ids"][:1],
                               "DECISION_CARRIES_NO_PROBABILITY",
                               basis=pos.get("probability_basis"), value=p)
    st["DECISION"] = _stage(rec["decision_ids"], rec["decision_ids"],
                            "NO_DECISION_LINKED_TO_THE_POSITION")
    st["EXECUTION_INTENT"] = _stage(
        rec["intent_ids"], rec["intent_ids"],
        ("NO_EXECUTION_INTENT_DERIVED_FROM_THIS_PAPER_DECISION"
         if intents is not None else "EXECUTION_INTENTS_TABLE_ABSENT")
        if book == "PAPER" else "NO_EXECUTION_INTENT_FOR_THE_GROUP")
    st["ORDER"] = _stage(rec["order_ids"], rec["order_ids"], "NO_ORDER")
    st["FILL"] = _stage(rec["fill_ids"], rec["fill_ids"], "NO_FILL")
    st["POSITION"] = _stage(True, [pos["position_key"]], None,
                            state=rec["state"])
    mgmt = rec["review_ids"] + rec["thesis_ids"] + rec["assessment_ids"]
    st["MANAGEMENT_REVIEW"] = _stage(
        mgmt, mgmt, "NO_MANAGEMENT_RECORD" if reviews is not None
        else "REVIEW_TABLE_ABSENT_IN_THIS_DATABASE")
    st["SETTLEMENT"] = _stage(rec["settlement_ids"], rec["settlement_ids"],
                              "NOT_SETTLED" if rec["state"] == "OPEN"
                              else "CLOSED_BY_SALE_OR_NO_SETTLEMENT_RECORD")
    pnl_ids = ["seq:%s" % s for s in rec["ledger_seqs"]]
    st["PNL"] = _stage(econ is not None and econ.get("net_profit_usd")
                       is not None, pnl_ids,
                       (econ or {}).get("unmeasured", {}).get(
                           "net_profit_usd") or "NO_ECONOMICS",
                       net_profit_usd=(econ or {}).get("net_profit_usd"))
    rec["stages"] = st
    # ── reproducibility gaps for THIS position ───────────────────────
    gaps = []
    if not rec["valuation_ids"]:
        gaps.append({"gap": "PROVIDER_INPUTS",
                     "why": "no valuation linked: provider odds, devig and "
                            "receipt times cannot be reproduced"})
    elif not any(v.get("has_raw_inputs") for v in myvals):
        gaps.append({"gap": "PROVIDER_RAW_ODDS",
                     "why": "the linked valuation carries no raw_odds"})
    if not rec["book_obs_ids"]:
        gaps.append({"gap": "BOOK_AT_DECISION",
                     "why": "no recorded book observation id on the decision "
                            "or the fills"})
    if p is None:
        gaps.append({"gap": "PROBABILITY_AT_DECISION",
                     "why": "the entry decision carries no probability"})
    if book == "ACTUAL":
        gaps.append({"gap": "ACTUAL_VENUE_FILL_TIME",
                     "why": "execmirror_fills/kalshi_live_fills record when "
                            "we observed the fill, not the venue's match "
                            "time; capital-hours start at observation"})
        if rec["settlement_ids"]:
            gaps.append({"gap": "ACTUAL_SETTLEMENT_CASH_TIME",
                         "why": "the settlement time is the paper settlement "
                                "of the same contract, a proxy for when the "
                                "venue released the cash"})
    for name, lst in (("MANAGEMENT_REVIEWS", reviews), ("THESES", theses),
                      ("CHALLENGES", chal), ("AUDIT_FINDINGS", finds),
                      ("RISK_ATTRIBUTION", attr), ("REGIME", rg)):
        if lst is None:
            gaps.append({"gap": name + "_TABLE_ABSENT",
                         "why": "the source table does not exist in this "
                                "database"})
    if decs and not any(d.get("has_features") for d in decs):
        gaps.append({"gap": "FEATURES",
                     "why": "the decision's internal_model record is empty"})
    rec["gaps"] = gaps
    rec["version"] = VERSION
    rec["content_sha256"] = content_sha(rec)
    return rec


# ═════════════════════════════════════════════════════════════════════
# production-shaped context
# ═════════════════════════════════════════════════════════════════════

def _block(rng, *, positions=300, rows=4000, book="PAPER", sorted_regimes=True,
           absent=()):
    """(positions, decisions, econ by key, ctx) shaped like reads.paper_positions
    / reads.lineage_context: `rows` sizes the big lists (reviews, ledger,
    regimes ...), the rest scale from it; ids repeat across groups the way
    group ids, decision ids and position keys do in production."""
    kind = "PAPER" if book == "PAPER" else "ACTUAL"
    gids = ["grp:%d" % i for i in range(positions)]
    pos, decisions, econ = [], {}, {}
    base_t = 1_790_000_000.0
    for i, g in enumerate(gids):
        dids = ["dec:%s:%d" % (book, i * 3 + k)
                for k in range(rng.choice([0, 1, 1, 2, 3]))]
        for d in dids:
            decisions[d] = {
                "decision_id": d,
                "valuation_id": rng.choice([None, rng.randrange(5000)]),
                "book_obs_id": rng.choice([None, rng.randrange(90000)]),
                "decided_at": rng.choice([base_t + rng.uniform(0, 86400 * 9),
                                          None]),
                "model_version": rng.choice([None, "m1", "m2"]),
                "policy_version": rng.choice([None, "P7", "P8"]),
                "simulator_version": rng.choice([None, "SIM3"]),
                "has_features": rng.random() < 0.5}
        key = "%s|%s|%d" % (book, g, rng.choice([0, 1]))
        p = {"book": book, "position_key": key, "group_id": g,
             "venue": "pmus", "account_id": "acct", "strategy": "s1",
             "us_market_slug": rng.choice(["slug-a", "slug-b", None]),
             "holding_side": rng.choice(["YES", "NO", None]),
             "probability": rng.choice([None, 0.41]),
             "probability_basis": "TEST",
             "src": {"decision_ids": dids,
                     "valuation_ids": rng.sample(range(5000),
                                                 rng.choice([0, 1, 2])),
                     "intent_ids": ["int:%d" % rng.randrange(900)],
                     "order_ids": ["ord:%d" % i], "fill_ids": ["fil:%d" % i],
                     "book_obs_ids": [rng.randrange(90000)],
                     "settlement_ids": rng.choice([[], ["set:%d" % i]]),
                     "handoff_ids": rng.choice([[], ["hof:%d" % i]]),
                     "policy_versions": ["P7"],
                     "simulator_versions": ["SIM3"]}}
        pos.append(p)
        if rng.random() < 0.9:
            econ[(book, key)] = {
                "opened_at": base_t, "released_at": rng.choice([None, base_t]),
                "state": rng.choice(["OPEN", "SETTLED", "SOLD", None]),
                "net_profit_usd": rng.choice([None, 1.5, -2.0]),
                "unmeasured": rng.choice([{}, {"net_profit_usd": "OPEN"}])}
    all_dids = list(decisions)
    keys = [p["position_key"] for p in pos]

    def g_or_other():
        return rng.choice(gids) if rng.random() < 0.85 else \
            "grp:other:%d" % rng.randrange(500)

    def subj():
        return rng.choice([g_or_other(), rng.choice(keys),
                           rng.choice(all_dids) if all_dids else "x",
                           "nobody"])
    ctx = {
        "valuations": [{"id": v % 4000, "version": rng.choice(["V1", None]),
                        "devig_method": rng.choice(["mult", None]),
                        "experiment_id": rng.choice(["E1", None]),
                        "has_raw_inputs": rng.random() < 0.5}
                       for v in range(rows // 2)],
        "intents": [{"intent_id": "int:%d" % i, "group_id": g_or_other(),
                     "decision_id": rng.choice(all_dids + [None]),
                     "policy_version": rng.choice(["P7", None])}
                    for i in range(rows // 4)]
        + [{"intent_id": "int:nodec", "group_id": gids[0]}],
        "reviews": [{"review_id": "rev:%d" % i, "group_id": g_or_other(),
                     "recommendation": "HOLD", "acted": False,
                     "at": base_t + i} for i in range(rows)],
        "actual_reviews": [{"review_id": "arev:%d" % i,
                            "group_id": g_or_other(), "action": "HOLD"}
                           for i in range(rows // 4)],
        "paper_handoffs": [{"handoff_id": "hof:x%d" % i,
                            "group_id": g_or_other()}
                           for i in range(rows // 8)],
        "theses": [{"thesis_id": "th:%d" % i,
                    "position_kind": rng.choice(["PAPER", "ACTUAL"]),
                    "group_id": g_or_other(),
                    "us_market_slug": rng.choice(["slug-a", "slug-b",
                                                  None])}
                   for i in range(rows // 4)],
        "assessments": [{"assessment_id": "as:%d" % i,
                         "position_kind": rng.choice([kind, "PAPER"]),
                         "group_id": g_or_other()}
                        for i in range(rows // 2)],
        "value_add": [{"value_add_id": "va:%d" % i,
                       "position_kind": kind, "group_id": g_or_other()}
                      for i in range(rows // 4)],
        "postmortems": [{"book": rng.choice([book, "PAPER"]),
                         "position_key": rng.choice(keys),
                         "group_id": g_or_other(),
                         "us_market_slug": rng.choice(["slug-a", None]),
                         "holding_side": rng.choice(["YES", "NO", None])}
                        for _ in range(rows // 8)],
        "challenges": [{"challenge_id": "ch:%d" % i, "target_id": subj()}
                       for i in range(rows // 4)],
        "findings": [{"finding_id": "fi:%d" % i, "subject": subj()}
                     for i in range(rows // 2)],
        "agent_decisions": [{"decision_ref": "ad:%d" % i, "subject": subj()}
                            for i in range(rows // 2)],
        "ledger": [{"seq": i, "group_id": g_or_other(),
                    "position_key": rng.choice([None, rng.choice(keys)])}
                   for i in range(rows)],
        "attribution": [{"book": rng.choice(["PAPER", "ACTUAL"]),
                         "subject_id": g_or_other()}
                        for _ in range(rows // 8)],
        "sizing": [{"decision_id": rng.choice(all_dids + ["d:none"])}
                   for _ in range(rows // 8)],
        "allocations": [{"decision_id": rng.choice(all_dids + ["d:none"]),
                         "run_id": "ar:%d" % i, "candidate_id": "c:%d" % i}
                        for i in range(rows // 8)],
        "capacity": [{"candidate_id": rng.choice(all_dids + ["d:none"]),
                      "capacity_id": "cap:%d" % i}
                     for i in range(rows // 8)],
        "regimes": [{"run_id": "rg:%d" % i,
                     "at": base_t + 86400 * 10 * (i // 3) / (rows // 3)}
                    for i in range(rows)],
        "previous": [],
    }
    if not sorted_regimes:
        rng.shuffle(ctx["regimes"])
    for p in rng.sample(pos, len(pos) // 2):
        prev = {"book": p["book"], "position_key": p["position_key"]}
        for k in ARRAYS:
            prev[k] = ([rng.randrange(1000)] if k in INT_ARRAYS
                       else ["old:%s:%d" % (k, rng.randrange(1000))])
        ctx["previous"].append(prev)
        if rng.random() < 0.2:      # a second row of the same key: the FIRST
            ctx["previous"].append(dict(prev, decision_ids=["later"]))
    for name in absent:
        ctx[name] = None
    return pos, decisions, econ, ctx


def _before_block(pos, decisions, econ, ctx):
    return [_build_before(p, econ.get((p["book"], p["position_key"])),
                          ctx=ctx, decisions=decisions) for p in pos]


@pytest.mark.parametrize("book,sorted_regimes,absent", [
    ("PAPER", True, ()),
    ("PAPER", False, ("actual_reviews",)),
    ("ACTUAL", True, ("actual_reviews", "intents", "regimes")),
    ("PAPER", True, ("reviews", "theses", "challenges", "findings",
                     "attribution", "regimes", "previous")),
])
def test_the_indexed_build_is_the_pre_index_build_record_for_record(
        book, sorted_regimes, absent):
    rng = random.Random(hash((book, sorted_regimes, absent)) & 0xFFFF)
    pos, decisions, econ, ctx = _block(rng, positions=260, rows=3000,
                                       book=book,
                                       sorted_regimes=sorted_regimes,
                                       absent=absent)
    want = _before_block(pos, decisions, econ, ctx)
    got = WH.build_all(pos, econ, ctx=ctx, decisions=decisions)
    assert got == want
    assert [r["content_sha256"] for r in got] == \
        [r["content_sha256"] for r in want]
    # one position at a time (no shared index) answers the same
    for p, w in list(zip(pos, want))[:40]:
        assert WH.build(p, econ.get((p["book"], p["position_key"])),
                        ctx=ctx, decisions=decisions) == w
    # the comparison is not vacuous: the context's lists are referenced
    for k in ("review_ids", "ledger_seqs", "audit_finding_ids",
              "regime_run_ids", "intent_ids", "challenge_ids",
              "capacity_ids", "postmortem_keys"):
        if any(r.get(k) for r in want):
            continue
        assert k in ("review_ids", "ledger_seqs", "audit_finding_ids",
                     "regime_run_ids", "challenge_ids") and (
            book != "PAPER" or set(absent) & {"reviews", "findings",
                                              "regimes", "challenges"}), k


def test_the_regime_in_force_is_the_scans_last_row_at_or_before():
    """`[r for r in rg if r["at"] <= t][-1]["run_id"]`, found or not --
    ties, a None run id, a decision before every row or after all of them,
    by bisection on the sorted list and by the scan on an unsorted one."""
    rows = [{"run_id": "a", "at": 10.0}, {"run_id": None, "at": 20.0},
            {"run_id": "b", "at": 20.0}, {"run_id": "c", "at": 30.0}]
    shuffled = [rows[2], rows[0], rows[3], rows[1]]
    for rg in (rows, shuffled):
        ix = WH.Index({"regimes": rg})
        for t in (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 99.0, 20, float("nan")):
            before = [r for r in rg if r["at"] <= t]
            want = (True, before[-1]["run_id"]) if before else (False, None)
            assert ix.regime_before(rg, t) == want, (t, rg is rows)
    nones = [{"run_id": None, "at": 1.0}, {"run_id": None, "at": 2.0}]
    assert WH.Index({"regimes": nones}).regime_before(nones, 1.5) == \
        (True, None)


def _sliced(pos, decisions, econ, ctx, budget_s=None):
    """The runner's loop over `build_some`: (records, slice sizes)."""
    if budget_s is None:
        budget_s = WH.BUILD_SLICE_S
    index, i, out, sizes = WH.Index(ctx), 0, [], []
    while i < len(pos):
        part, j = WH.build_some(pos, i, econ, ctx=ctx, decisions=decisions,
                                index=index, budget_s=budget_s)
        assert j > i and len(part) == j - i
        out.extend(part)
        sizes.append(len(part))
        i = j
    return out, sizes


def test_the_block_built_in_slices_is_the_pre_index_build():
    """`build_some` until the block ends is build_all, and the pre-index
    build, record for record and in order -- one record per slice, a few
    per slice, the whole block in one -- and it raises what they raise."""
    rng = random.Random(21)
    pos, decisions, econ, ctx = _block(rng, positions=180, rows=2000)
    want = _before_block(pos, decisions, econ, ctx)
    assert WH.build_all(pos, econ, ctx=ctx, decisions=decisions) == want
    for budget in (0.0, 0.002, 60.0):
        got, sizes = _sliced(pos, decisions, econ, ctx, budget_s=budget)
        assert got == want, budget
        if budget == 0.0:
            assert sizes == [1] * len(pos)
        if budget == 60.0:
            assert sizes == [len(pos)]
    assert WH.build_some(pos, len(pos), econ, ctx=ctx, decisions=decisions,
                         index=WH.Index(ctx)) == ([], len(pos))
    ctx["regimes"].append({"run_id": "rg:none-at", "at": None})
    with pytest.raises(TypeError):
        _before_block(pos, decisions, econ, ctx)
    with pytest.raises(TypeError):
        _sliced(pos, decisions, econ, ctx, budget_s=0.0)


def test_a_list_that_cannot_be_grouped_is_scanned_as_before():
    rng = random.Random(7)
    pos, decisions, econ, ctx = _block(rng, positions=60, rows=600)
    ctx["findings"] = tuple(ctx["findings"])          # not a list
    ctx["ledger"].append({"seq": 10**6, "group_id": ["unhashable"],
                          "position_key": None})   # == works, hashing not
    ctx["regimes"].append({"run_id": "rg:none-at", "at": None})
    with pytest.raises(TypeError):                    # None <= t, as before
        _before_block(pos, decisions, econ, ctx)
    with pytest.raises(TypeError):
        WH.build_all(pos, econ, ctx=ctx, decisions=decisions)
    ctx["regimes"].pop()
    assert WH.build_all(pos, econ, ctx=ctx, decisions=decisions) == \
        _before_block(pos, decisions, econ, ctx)
    del ctx["ledger"][3]["group_id"]                  # a row without its key
    with pytest.raises(KeyError):
        _before_block(pos, decisions, econ, ctx)
    with pytest.raises(KeyError):
        WH.build_all(pos, econ, ctx=ctx, decisions=decisions)


# ═════════════════════════════════════════════════════════════════════
# off the loop: the runner builds on the CPU lane
# ═════════════════════════════════════════════════════════════════════

class _Conn:
    """Just enough of an asyncpg connection for run_cycle's own calls; every
    read and write of reads/store is replaced below."""

    def transaction(self):
        return contextlib.AsyncExitStack()

    async def execute(self, *a, **k):
        return "SET"


def _fake_cycle(monkeypatch, blocks):
    from sportsassets.profitability import economics as EC
    from sportsassets.profitability import reads as R
    from sportsassets.profitability import runner as RUN
    from sportsassets.profitability import store as ST

    async def none(*a, **k):
        return None

    async def empty(*a, **k):
        return []

    async def boom(*a, **k):
        raise RuntimeError("not under test")

    monkeypatch.setattr(ST, "tables_ready", lambda c: _true())
    monkeypatch.setattr(ST, "record_component", none)
    monkeypatch.setattr(ST, "save_snapshot", none)
    monkeypatch.setattr(R, "capacity_candidates", boom)
    monkeypatch.setattr(R, "settlement_lag_samples", empty)
    paper, actual = blocks
    monkeypatch.setattr(R, "paper_positions", lambda c, **k: _val(
        {"positions": paper[0], "decisions": paper[1]}))
    monkeypatch.setattr(R, "actual_positions", lambda c, **k: _val(
        {"positions": actual[0], "decisions": actual[1]}))
    econ = dict(paper[2])
    econ.update(actual[2])
    monkeypatch.setattr(EC, "compute_position", lambda p, **k: dict(
        econ.get((p["book"], p["position_key"])) or {},
        book=p["book"], position_key=p["position_key"]))
    monkeypatch.setattr(EC, "counterfactual_hold", lambda p, **k: None)
    ctxs = {"PAPER": paper[3], "ACTUAL": actual[3]}

    async def lineage_context(conn, *, positions, **k):
        return ctxs[positions[0]["book"]]
    monkeypatch.setattr(R, "lineage_context", lineage_context)
    saved: list = []

    async def save_lineage(conn, *, records, **k):
        saved.extend(records)
        return len(records)
    monkeypatch.setattr(ST, "save_lineage", save_lineage)
    monkeypatch.setattr(ST, "latest_lineage_ids", lambda c, keys: _val({}))
    monkeypatch.setattr(ST, "save_economics", lambda c, **k: _val(0))
    for name in ("paper_capital", "previous_metrics", "unscored_forecasts"):
        monkeypatch.setattr(R, name, boom)
    from sportsassets.lost_opportunity import runner as LOL
    monkeypatch.setattr(LOL, "run_component", none)
    return RUN, saved


async def _true():
    return True


async def _val(v):
    return v


@contextlib.contextmanager
def _collector_paused():
    """A full collection holds the GIL whichever thread triggers it -- in a
    long test session's heap, for longer than the work measured here (the
    API loop watchdog reports the collector's share apart) -- so it is
    paused while a loop gap is measured."""
    was = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


async def _run_with_ticker(coro):
    """(result, the loop's longest gap). A task stamps each turn it gets; it
    has run once before `coro` starts, and its last stamp is the turn it gets
    after `coro` returns -- so a coroutine that never yields shows as one gap
    as long as itself."""
    stamps = []
    done = asyncio.Event()

    async def tick():
        while True:
            stamps.append(time.monotonic())
            if done.is_set():
                return
            await asyncio.sleep(0.005)
    t = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        out = await coro
    finally:
        done.set()
        await t
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return out, max(gaps or [0.0])


def _install_the_apis_lane(monkeypatch):
    """What the API's lifespan does (api.app._install_cpu_lane): the intel
    layer's offload hook -- which this layer's `common.offload` calls -- is
    the CPU lane. Restored after the test."""
    from sportsassets.api import app as A
    from sportsassets.intel import common as IC
    monkeypatch.setattr(IC, "offload", IC.offload)
    assert A._install_cpu_lane() == ["intel.common.offload"]
    assert IC.offload is cpu_lane.run


def _threads_of_build(monkeypatch):
    threads = []
    real = WH.build

    def spy(*a, **k):
        threads.append(threading.current_thread().name)
        return real(*a, **k)
    monkeypatch.setattr(WH, "build", spy)
    return threads


def _cycle_threads(RUN):
    async def go():
        loop_thread = threading.current_thread().name
        got = await RUN.run_cycle(_Conn(), now=1_790_900_000.0,
                                  account_id="acct", include_actual=True)
        return loop_thread, got
    return asyncio.run(go())


def test_the_runner_builds_the_lineage_on_the_cpu_lane(monkeypatch):
    """In the API process (its lifespan installs the lane) every record is
    built on the CPU lane's thread; the records are the pre-index build's."""
    rng = random.Random(11)
    paper = _block(rng, positions=40, rows=400)
    actual = _block(rng, positions=10, rows=100, book="ACTUAL")
    RUN, saved = _fake_cycle(monkeypatch, (paper, actual))
    _install_the_apis_lane(monkeypatch)
    threads = _threads_of_build(monkeypatch)
    loop_thread, got = _cycle_threads(RUN)
    assert got["components"]["WAREHOUSE"] == "OK", got
    assert len(threads) == 50 and len(saved) == 50
    assert loop_thread not in threads
    assert {t.split("_")[0] for t in threads} == {cpu_lane.THREAD_PREFIX}
    assert saved == _before_block(*paper[:3], paper[3]) + \
        _before_block(*actual[:3], actual[3])


def test_without_the_api_the_lineage_is_still_built_off_the_loop(
        monkeypatch):
    """A process that installs no lane (a script, a worker) gets
    asyncio.to_thread, as the cycle's other computes always did."""
    from sportsassets.intel import common as IC
    monkeypatch.setattr(IC, "offload", asyncio.to_thread)
    rng = random.Random(12)
    paper = _block(rng, positions=20, rows=200)
    actual = _block(rng, positions=5, rows=50, book="ACTUAL")
    RUN, saved = _fake_cycle(monkeypatch, (paper, actual))
    threads = _threads_of_build(monkeypatch)
    loop_thread, got = _cycle_threads(RUN)
    assert got["components"]["WAREHOUSE"] == "OK", got
    assert len(threads) == 25 and loop_thread not in threads


def test_a_production_size_block_leaves_the_loop_responsive(monkeypatch):
    """reads.MAX_ROWS rows in the big lists (the read's own bound), and a
    position count the size of the RC6.1 production block: the loop's
    longest gap stays under the watchdog's lag threshold, where the
    pre-fix build held it for the whole block."""
    from sportsassets.profitability import reads as R
    rng = random.Random(5)
    paper = _block(rng, positions=1200, rows=R.MAX_ROWS)
    actual = _block(rng, positions=200, rows=R.MAX_ROWS // 4, book="ACTUAL")
    RUN, saved = _fake_cycle(monkeypatch, (paper, actual))
    _install_the_apis_lane(monkeypatch)

    async def go():
        return await _run_with_ticker(RUN.run_cycle(
            _Conn(), now=1_790_900_000.0, account_id="acct",
            include_actual=True))
    t0 = time.monotonic()
    with _collector_paused():
        got, gap = asyncio.run(go())
    took = time.monotonic() - t0
    print("MEASURED longest loop gap %.4f s; the cycle took %.4f s"
          % (gap, took))
    assert got["components"]["WAREHOUSE"] == "OK", got
    assert len(saved) == 1400
    assert gap < WATCHDOG_LAG_S, (gap, took)


def test_a_lane_job_is_not_queued_behind_a_production_size_block(
        monkeypatch):
    """The production-size block of the test above, built through the
    cycle in the API's lane, while a Command read's lane job runs three
    times: each finishes in under LANE_WAIT_S, the block still being built.
    Built as one lane job (this change's first version, 3dbaf69d, never
    merged), the block kept each waiting for the rest of it.

    THE BLOCK IS MADE TO TAKE SECONDS. With the collector paused (as the
    measurement needs) this block builds in ~0.5 s here; with it running,
    in ~2.8 s (LOCAL measurement), and the production block grows with the
    data (8.7 s, then 13.7 s an hour later, on b3f1b0cd's build). So each
    record's build is followed by PAD_S of pure Python on the same thread:
    the block then takes ~3.3 s whatever this machine's speed, and what is
    measured is how the lane is shared, not how fast a record is built.
    The records are still the real build's, compared below."""
    from sportsassets.profitability import reads as R
    PAD_S = 0.002
    rng = random.Random(5)
    paper = _block(rng, positions=1200, rows=R.MAX_ROWS)
    actual = _block(rng, positions=200, rows=R.MAX_ROWS // 4, book="ACTUAL")
    RUN, saved = _fake_cycle(monkeypatch, (paper, actual))
    _install_the_apis_lane(monkeypatch)
    started, built = threading.Event(), [0]
    real = WH.build

    def spy(*a, **k):
        started.set()
        out = real(*a, **k)
        end = time.monotonic() + PAD_S
        while time.monotonic() < end:
            pass
        built[0] += 1
        return out
    monkeypatch.setattr(WH, "build", spy)

    async def go():
        cyc = asyncio.create_task(RUN.run_cycle(
            _Conn(), now=1_790_900_000.0, account_id="acct",
            include_actual=True))
        lat, during = await command_reads_while(
            started, lambda: built[0] >= 1400)
        return lat, during, built[0], await cyc
    with _collector_paused(), production_gil():
        lat, during, at_last, got = asyncio.run(go())
    print("MEASURED Command read lane job while the block was built: %s s "
          "(%d of 1400 records built after the last)" % (lat, at_last))
    assert all(x is not None and x < LANE_WAIT_S for x in lat), lat
    assert during and at_last < 1400, "nothing was measured"
    assert got["components"]["WAREHOUSE"] == "OK", got
    assert saved == _before_block(*paper[:3], paper[3]) + \
        _before_block(*actual[:3], actual[3])


def test_the_cycles_economics_compute_stays_on_its_own_thread(monkeypatch):
    """The economics, metrics and forecast computes ran on asyncio.to_thread
    on b3f1b0cd, already off the loop; they stay there -- not queued on the
    API's CPU lane -- so a long cycle never holds the lane in one job."""
    from sportsassets.profitability import economics as EC
    rng = random.Random(13)
    paper = _block(rng, positions=30, rows=300)
    actual = _block(rng, positions=5, rows=50, book="ACTUAL")
    RUN, saved = _fake_cycle(monkeypatch, (paper, actual))
    _install_the_apis_lane(monkeypatch)
    threads = []
    real = EC.compute_position

    def spy(p, **k):
        threads.append(threading.current_thread().name)
        return real(p, **k)
    monkeypatch.setattr(EC, "compute_position", spy)
    loop_thread, got = _cycle_threads(RUN)
    assert got["components"]["ECONOMICS"] == "OK", got
    assert len(threads) == 35 and loop_thread not in threads
    assert not any(t.startswith(cpu_lane.THREAD_PREFIX) for t in threads), \
        threads
