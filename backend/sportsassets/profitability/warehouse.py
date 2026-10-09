"""THE PROFITABILITY DATA WAREHOUSE: LINEAGE BY POSITION (RESEARCH). Pure.

For every position (PAPER and ACTUAL, separately), the SOURCE RECORD IDS of
every stage of its life and of its context, so any economic conclusion can
be traced back to the rows it came from -- references, not copies:

  CHAIN    candidate (external_valuations) -> probability (the decision's
           p and the valuation it came from) -> decision (paper_decisions)
           -> execution intent (execution_intents) -> order (paper_orders /
           execmirror_orders / kalshi_live_intents) -> fill (paper_fills /
           execmirror_fills / kalshi_live_fills) -> position (the key) ->
           management review (paper_xavier_reviews / smalllive_reviews,
           Xavier's theses / assessments / value-add) -> settlement
           (paper_settlements, every version) -> P&L (paper_ledger seqs and
           pos_position_economics)
  CONTEXT  agent actions (reviews that acted, handoffs, agent_decisions),
           challenges (karen_challenges), audit findings
           (paper_audrey_findings), features (the decision's internal model
           record), model / policy / experiment / simulator versions,
           counterfactuals (thesis counterfactuals, value-add, the
           HOLD_TO_SETTLEMENT economics), risk (intel attribution / sizing /
           allocation), capacity (pos_capacity), regime (the intel regime
           state in force at the decision)

`stages` says, per stage, whether it is present, its ids and -- when absent
-- WHY. `gaps` lists what cannot currently be reproduced for this position,
by name (see STANDING_GAPS for the ones that hold for every position).

APPEND-ONLY KNOWLEDGE: a new revision is the UNION of the previous
revision's ids and today's, so a referenced record that a source table later
prunes stays referenced (its absence is then itself evidence).
"""
from __future__ import annotations

import time

from . import common as C

VERSION = "POS_WAREHOUSE_V1"
CHAIN = ("CANDIDATE", "PROBABILITY", "DECISION", "EXECUTION_INTENT", "ORDER",
         "FILL", "POSITION", "MANAGEMENT_REVIEW", "SETTLEMENT", "PNL")
ARRAYS = ("valuation_ids", "decision_ids", "intent_ids", "order_ids",
          "fill_ids", "book_obs_ids", "settlement_ids", "ledger_seqs",
          "review_ids", "handoff_ids", "thesis_ids", "assessment_ids",
          "value_add_ids", "postmortem_keys", "challenge_ids",
          "audit_finding_ids", "agent_decision_refs", "attribution_refs",
          "sizing_refs", "allocation_refs", "regime_run_ids", "capacity_ids",
          "model_versions", "policy_versions", "experiment_ids",
          "simulator_versions")
INT_ARRAYS = ("valuation_ids", "book_obs_ids", "ledger_seqs")

#: Reproducibility gaps that hold for EVERY position today (by name).
STANDING_GAPS = (
    {"gap": "PORTFOLIO_STATE_AT_DECISION",
     "why": "no snapshot of the whole portfolio is written at the decision "
            "instant; only periodic paper_equity_snapshots exist"},
    {"gap": "AGENT_PERSONA_VERSION_AT_DECISION",
     "why": "agent_persona_versions is not linked to the decisions or "
            "reviews it shaped"},
    {"gap": "INTEL_RISK_AND_REGIME_HISTORY_PRUNED_AFTER_30D",
     "why": "intel_snapshots / intel_regime_states are pruned after 30 days "
            "(intel/store.py RETENTION_DAYS); referenced run ids may no "
            "longer resolve"},
    {"gap": "INTEL_ATTRIBUTION_AND_SIZING_ARE_LATEST_ONLY",
     "why": "intel_attribution / intel_sizing are upserted per subject; the "
            "reference resolves to the latest computation, not the one in "
            "force at the decision"},
    {"gap": "ORDER_STATE_ROWS_ARE_MUTABLE",
     "why": "paper_orders / execmirror_orders rows are updated in place; "
            "their history lives in paper_order_events / execmirror_events "
            "(referenced through the order ids)"},
)


def _union(prev, cur, ints=False):
    s = set(prev or []) | set(cur or [])
    s.discard(None)
    if ints:
        return sorted(int(x) for x in s)
    return sorted(str(x) for x in s)


def _stage(present, ids, why, **extra):
    d = {"present": bool(present), "ids": list(ids)[:200],
         "n": len(list(ids)), "why": None if present else why}
    d.update(extra)
    return d


# ── (RC6.1 api-stall2) ONE INDEX OF THE CONTEXT PER BLOCK, OFF THE LOOP ──
#
# PRODUCTION 2026-10-09 (render-ops logs run 37949540216; the API loop
# watchdog's ring, research-sql rc6_api-responsive_loop_stalls.sql run
# 37950182966): the hourly cycle held the API event loop 8.7 s at 14:48Z,
# task profitability/runner.py:run, the loop thread in `rows` <- `build` <-
# runner `warehouse` -- every position filtered every context list (up to
# reads.MAX_ROWS rows each: reviews, ledger, findings, regimes ...) on the
# loop, positions x rows. Now the runner builds a block's records off the
# loop a slice at a time (`build_some` through common.offload: the API's CPU
# lane), and `build` reads the context through an `Index`: each list grouped once by the field its
# filter keys on, so a position looks at only the rows that can match --
# then applies the SAME filter to them, in the list's order. Same rows,
# same order, same records (tests/test_rc6_api_stall2_profitability.py
# compares them with the pre-index build on production-shaped data). A list
# that cannot be grouped (not a list, a row without the field, an
# unhashable value) is scanned exactly as before.
class Index:
    """The read-only lookups `build` makes of ONE `ctx`, built once."""

    def __init__(self, ctx: dict):
        self.ctx = ctx
        self._groups: dict = {}
        self._vals = None
        self._prev = None
        self._regimes = None

    def _group(self, name, field, via_get):
        k = (name, field, via_get)
        if k not in self._groups:
            lst = self.ctx.get(name)
            out = None
            if isinstance(lst, list):
                out = {}
                try:
                    for i, r in enumerate(lst):
                        v = r.get(field) if via_get else r[field]
                        out.setdefault(v, []).append(i)
                except Exception:                              # noqa: BLE001
                    out = None
            self._groups[k] = out
        return self._groups[k]

    def rows(self, name, pred, *keyed):
        """`[r for r in ctx[name] if pred(r)]` (None when the list is), where
        `keyed` = (field, keys[, via_get]) pairs name every way `pred` can
        hold: r[field] (or r.get(field)) equals one of `keys`."""
        lst = self.ctx.get(name)
        if lst is None:
            return None
        hit: set = set()
        for spec in keyed:
            field, keys = spec[0], spec[1]
            grp = self._group(name, field, bool(spec[2:] and spec[2]))
            if grp is None:
                return [r for r in lst if pred(r)]
            try:
                for key in keys:
                    hit.update(grp.get(key, ()))
            except TypeError:                     # an unhashable key: scan
                return [r for r in lst if pred(r)]
        return [lst[i] for i in sorted(hit) if pred(lst[i])]

    def vals(self) -> dict:
        if self._vals is None:
            self._vals = {int(v["id"]): v
                          for v in (self.ctx.get("valuations") or [])}
        return self._vals

    def previous(self, book, key):
        """The first `previous` row of (book, position key), in list order."""
        lst = self.ctx.get("previous") or []
        if self._prev is None:
            self._prev = False
            if isinstance(lst, list):
                try:
                    idx: dict = {}
                    for p in lst:
                        idx.setdefault((p["book"], p["position_key"]), p)
                    self._prev = idx
                except Exception:                              # noqa: BLE001
                    self._prev = False
        if self._prev is not False:
            try:
                return self._prev.get((book, key))
            except TypeError:
                pass
        return next((p for p in lst
                     if p["book"] == book and p["position_key"] == key),
                    None)

    def regime_before(self, rg, t):
        """(True, `[r for r in rg if r["at"] <= t][-1]["run_id"]`), or
        (False, None) when no row is at or before t -- by bisection when `rg`
        is the context's list and its `at` are finite numbers in
        non-decreasing order (the read's ORDER BY computed_at), else by that
        scan."""
        if self._regimes is None:
            self._regimes = False
            lst = self.ctx.get("regimes")
            try:
                ats = [r["at"] for r in lst]
                if all(type(a) in (int, float) and a == a for a in ats) and \
                        all(ats[i] <= ats[i + 1] for i in range(len(ats) - 1)):
                    self._regimes = (lst, ats)
            except Exception:                                  # noqa: BLE001
                self._regimes = False
        if self._regimes is not False and self._regimes[0] is rg and \
                type(t) in (int, float) and t == t:
            ats, lo, hi = self._regimes[1], 0, len(self._regimes[1])
            while lo < hi:                     # bisect_right(ats, t)
                mid = (lo + hi) // 2
                if t < ats[mid]:
                    hi = mid
                else:
                    lo = mid + 1
            return (True, rg[lo - 1]["run_id"]) if lo else (False, None)
        before = [r for r in rg if r["at"] <= t]
        return (True, before[-1]["run_id"]) if before else (False, None)


def build_all(positions, econ_by_key: dict, *, ctx: dict,
              decisions: dict) -> list:
    """`build` for every position of ONE block (in order), over one `Index`
    of its `ctx`, in one call."""
    index = Index(ctx)
    return [build(p, econ_by_key.get((p["book"], p["position_key"])),
                  ctx=ctx, decisions=decisions, index=index)
            for p in positions]


#: (RC6.1 api-stall2) the longest one `build_some` call runs before it
#: returns what it has built (the same bound as cpu_lane.SLICE_S, which this
#: layer may not import). A scheduling bound: it decides where one off-loop
#: job ends and the next begins, never what a record holds.
BUILD_SLICE_S = 0.25


def build_some(positions, start: int, econ_by_key: dict, *, ctx: dict,
               decisions: dict, index: Index,
               budget_s: float = BUILD_SLICE_S) -> tuple:
    """(records, next start): `build` for positions[start], [start + 1], ...
    in order, over `index` (an `Index` of this same `ctx`, shared by every
    slice of the block), until the list ends or `budget_s` has passed
    (checked after each record). The runner calls it until `next start`
    reaches the end, so the slices together are `build_all`'s records, in
    its order. Pure; a record that raises raises here, as in `build_all`.

    WHY SLICES (RC6.1 api-stall2). In the API each call is one job on the
    CPU lane, where other jobs wait behind the one running -- some under a
    deadline (cpu_lane's docstring). A 1,200-position block is ~3 s of
    builds; one slice at a time, nothing queued waits more than one."""
    end = time.monotonic() + budget_s
    out, i, n = [], start, len(positions)
    while i < n:
        p = positions[i]
        out.append(build(p, econ_by_key.get((p["book"], p["position_key"])),
                         ctx=ctx, decisions=decisions, index=index))
        i += 1
        if time.monotonic() >= end:
            break
    return out, i


def build(pos: dict, econ: dict | None, *, ctx: dict,
          decisions: dict, index: Index | None = None) -> dict:
    """The lineage record of ONE position (store adds run id, revision).
    `index` (an `Index` of this same `ctx`) is shared across a block."""
    ix = index if index is not None and index.ctx is ctx else Index(ctx)
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
    vals = ix.vals()
    myvals = [vals[v] for v in vids if v in vals]
    rows = ix.rows

    ids_all = {g, pos["position_key"], *dids}
    by_g = ("group_id", (g,))
    reviews = rows("reviews", lambda r: r["group_id"] == g, by_g) \
        if book == "PAPER" else rows("actual_reviews",
                                     lambda r: r["group_id"] == g, by_g)
    theses = rows("theses", lambda r: r["group_id"] == g
                  and r["position_kind"] == kind
                  and r.get("us_market_slug") in (slug, None), by_g)
    assess = rows("assessments", lambda r: r["group_id"] == g
                  and r["position_kind"] == kind, by_g)
    vadd = rows("value_add", lambda r: r["group_id"] == g
                and r["position_kind"] == kind, by_g)
    pms = rows("postmortems", lambda r: r["group_id"] == g
               and r["book"] == book and r.get("us_market_slug") in (slug, None)
               and str(r.get("holding_side") or side) == str(side), by_g)
    chal = rows("challenges", lambda r: r["target_id"] in ids_all,
                ("target_id", ids_all))
    finds = rows("findings", lambda r: r["subject"] in ids_all,
                 ("subject", ids_all))
    adec = rows("agent_decisions", lambda r: r["subject"] in ids_all,
                ("subject", ids_all))
    ledger = rows("ledger", lambda r: r["group_id"] == g and (
        r.get("position_key") in (None, pos["position_key"])), by_g) \
        if book == "PAPER" else []
    attr = rows("attribution", lambda r: r["subject_id"] == g
                and r["book"] == book, ("subject_id", (g,)))
    siz = rows("sizing", lambda r: r["decision_id"] in dids,
               ("decision_id", dids))
    alloc = rows("allocations", lambda r: r["decision_id"] in dids,
                 ("decision_id", dids))
    cap = rows("capacity", lambda r: r["candidate_id"] in dids,
               ("candidate_id", dids))
    intents = rows("intents", lambda r: r["group_id"] == g
                   or r.get("decision_id") in dids, by_g,
                   ("decision_id", dids, True))
    handoffs = list(src.get("handoff_ids") or [])
    if book == "PAPER":
        handoffs += [r["handoff_id"] for r in (rows(
            "paper_handoffs", lambda r: r["group_id"] == g, by_g) or [])]
    regimes = []
    rg = ctx.get("regimes")
    for d in decs:
        t = C.epoch(d.get("decided_at"))
        if rg and t is not None:
            found, run_id = ix.regime_before(rg, t)
            if found:
                regimes.append(run_id)

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
    prev = ix.previous(book, pos["position_key"])
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


#: Reference streams that grow while a position is merely held (Xavier
#: reviews every few minutes). Their ids are refreshed whenever a revision
#: is written for another reason (a fill, a sale, a settlement, a new
#: challenge or finding, the close) but do not by themselves cause one: the
#: source tables are append-only and keyed by group_id, so nothing is lost
#: between revisions, and the warehouse grows with events, not with cycles.
STREAMS = ("review_ids", "assessment_ids", "ledger_seqs")


def content_sha(rec: dict) -> str:
    body = {k: rec[k] for k in rec if k not in ("version", "content_sha256")
            and k not in STREAMS}
    st = dict(body.get("stages") or {})
    if "MANAGEMENT_REVIEW" in st:
        st["MANAGEMENT_REVIEW"] = {"present": st["MANAGEMENT_REVIEW"][
            "present"]}
    if "PNL" in st:
        st["PNL"] = {k: v for k, v in st["PNL"].items()
                     if k not in ("ids", "n")}
    body["stages"] = st
    return C.sha(body)


def summary(records: list) -> dict:
    """Coverage of each chain stage across positions, per book."""
    out = {"label": C.LABEL, "authority": C.AUTHORITY, "version": VERSION,
           "standing_gaps": list(STANDING_GAPS)}
    for book in C.BOOKS:
        mine = [r for r in records if r["book"] == book]
        stages = {s: sum(1 for r in mine if r["stages"][s]["present"])
                  for s in CHAIN}
        gaps: dict = {}
        for r in mine:
            for gp in r["gaps"]:
                gaps[gp["gap"]] = gaps.get(gp["gap"], 0) + 1
        out[book] = {"positions": len(mine), "stage_present": stages,
                     "gaps": gaps}
    out["summed_across_books"] = False
    return out
