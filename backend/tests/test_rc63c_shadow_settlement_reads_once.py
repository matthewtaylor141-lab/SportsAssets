"""CAPITAL-CRITICAL: THE SHADOW-SETTLEMENT STEP READS THE SETTLEMENT EVIDENCE OF
ITS WHOLE BATCH IN TWO STATEMENTS (RC6.3c pass-hardening, round 3).

THE DEFECT (the independent reviewer of 2e106206): bettor_capital_authority.
settle_shadows -- the pass step `shadow_settlement`, which runs BEFORE `derek`
in default_steps() -- called xavier_management.settlement_outcome for every
PENDING shadow: the per-contract outcome read

    SELECT id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
      FROM external_valuations WHERE us_market_slug=$1
       AND outcome_basis IS NOT NULL ORDER BY id

PLUS paper_xavier.VENUE_PRICE_SQL when that settled nothing -- up to
2 x SETTLE_PER_PASS = 400 scans of a table with no index on us_market_slug,
every RUN_EVERY_S = 600 s, with no bound by the step's deadline and no back-off
(a pending shadow is re-read on every run until its contract settles). Measured
with the reviewer's probe on 2e106206: 200 pending shadows over a 40,000-row
table, 400 scans, 402 statements, 5.985 s on this machine (the reviewer:
10.3 s; at production's per-scan cost from the heartbeat, ~68 ms, about 27 s).
Before derek, that time spends the shared pass budget (ctx["deadline"] = t0 +
pass_budget_s, 20 s) on which derek's candidate loop breaks at once, and under
N2 it counts toward the 47 s after which an ENTER-owing step is NOT STARTED.

THE FIX: settle_shadows reads the outcome rows of ALL the batch's contracts in
one statement (paper_xavier.outcome_rows_by_slug) and the venue
price-settlement rows in one more (paper_xavier.venue_price_rows_by_slug, the
`= ANY` form of VENUE_PRICE_SQL), each grouped per contract in id order, and
judges each shadow with xavier_management.settlement_from_rows -- the verdict
settlement_outcome applies, its branches factored out unchanged (WON pays 1,
LOST 0, a VOID refunds, CONFLICTING settles nothing, else the venue's own price,
else nothing by name) over the UNCHANGED paper_xavier.outcome_for /
venue_price_settlement. `settlement_fn` stays the seam; settlement_outcome
itself (step_value_add's theses, complete_marks' acceptance read) stays per
contract. settle_shadows also stops SETTLE_STOP_BEFORE_DEADLINE_S before
ctx["step_deadline"] and names the cut (SHADOW_SETTLEMENT_CUT_AT_THE_STEP_
DEADLINE, with the shadows not examined; the next run resumes with them).

WHAT IS PROVED HERE, on a real Postgres:
  * row for row, the batched venue-price read returns for every contract
    exactly the rows VENUE_PRICE_SQL returns (interleaved ids, rows with an
    outcome basis or without a settlement read excluded, a contract with no
    rows, a contract named twice);
  * over shadows of every kind -- WON (a partial fill from the book), LOST (no
    book: the limit-bound fill), a CONFIRMED_VOID refund, CONFLICTING evidence
    (pending), the venue's price with the last-fair-price text stated (a LONG
    and a SHORT on one contract), the venue's price with no price settlement
    stated (pending), no evidence (pending), a NO_FILL, and one contract held
    LONG and SHORT settled WON / LOST -- the verdict per shadow is identical
    from the base's settlement_outcome (verbatim here), the refactored
    settlement_outcome and the batched read; and settle_shadows writes the
    same paper_shadow_counterfactual_outcomes rows and the same digest with
    the per-contract settlement_fn (11 outcome reads + 4 venue reads) and the
    batched read (1 + 1);
  * FAILS ON THE BASE (2e106206): 200 pending shadows -> ONE outcome read and
    ONE venue-price read (the base: 200 + 200);
  * FAILS ON THE BASE: 200 pending shadows over a 40,000-row table with no
    index on us_market_slug end under 2 s on the migrated test database (the
    base's per-contract loop is measured on the same table for the record);
  * the step honours ctx["step_deadline"]: with no time left nothing is
    examined, no read is spent and the cut is named with the count not
    examined; the next run (after RUN_EVERY_S) resumes with them; a deadline
    that falls mid-way stops the loop with the counts whole;
  * shadow_settlement is bettor_capital_authority.step and precedes derek in
    the pass order (with and without PAPER_BENCHMARK): its time is pre-derek
    time, which is why it is bounded here.

SYNTHETIC rows on a scratch test database under fresh paper accounts; no
venue, no order authority, nothing of the live paper account.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.agents import paper_runtime as PRT
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests.test_rc63c_settle_reads_outcomes_once import (
    AT,
    Counting,
    ev_row,
    filler_valuations,
    purge,
)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

SYN = "rc63c-sh-"
STRAT = "PINNACLE_COMPLETED_GAME_PAPER"
DECIDED = AT - 7200.0
QTY = 50.0
NO_PRICE_STATED = ("VENUE_SETTLED_AT_A_PRICE_BUT_THE_CONTRACT_TEXT_HELD_"
                   "STATES_NO_PRICE_SETTLEMENT")
NO_PRICE_TEXT = ("This market settles to the winner. Outcome sourced from "
                 "MLB.")


# ═════════════════════════════════════════════════════════════════════
# THE BASE'S PER-CONTRACT SETTLEMENT, VERBATIM
# ═════════════════════════════════════════════════════════════════════

async def base_settlement_outcome(conn, slug: str, holding_side: str) -> dict:
    """xavier_management.settlement_outcome as the base 2e106206 had it,
    verbatim: the per-contract outcome read, the branches inline, the
    per-contract venue-price read when the first settles nothing."""
    try:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
            "       outcome_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND outcome_basis IS NOT NULL "
            " ORDER BY id", slug)]
        got = PX.outcome_for(rows, holding_side=holding_side)
        if got.get("outcome") == "WON":
            return {"outcome": "WON", "payout_per_contract": 1.0,
                    "evidence": got.get("evidence")}
        if got.get("outcome") == "LOST":
            return {"outcome": "LOST", "payout_per_contract": 0.0,
                    "evidence": got.get("evidence")}
        if got.get("outcome") == "VOID_REFUND":
            return {"outcome": "VOID_REFUND", "payout_per_contract": None,
                    "refund": True, "evidence": got.get("evidence")}
        if got.get("why") == "CONFLICTING_SETTLEMENT_EVIDENCE":
            return {"outcome": None, "why": got["why"]}
        vrows = [dict(r) for r in await conn.fetch(PX.VENUE_PRICE_SQL, slug)]
        vp = PX.venue_price_settlement(vrows, holding_side=holding_side)
        if vp.get("price") is not None:
            return {"outcome": "SETTLED_AT_VENUE_PRICE",
                    "payout_per_contract": float(vp["price"]),
                    "evidence": vp.get("evidence")}
        return {"outcome": None, "why": got.get("why") or vp.get("why")}
    except Exception as exc:                                    # noqa: BLE001
        return {"outcome": None, "why": "SETTLEMENT_READ_FAILED:%s"
                % type(exc).__name__}


async def per_slug_venue_reference(conn, slugs) -> dict:
    """THE FORMER READ: one VENUE_PRICE_SQL statement per distinct contract."""
    return {s: [dict(r) for r in await conn.fetch(PX.VENUE_PRICE_SQL, s)]
            for s in sorted({s for s in slugs if s})}


class CountReads(Counting):
    """The pass connection, with every statement that reads
    external_valuations counted: the outcome reads (`outcome_reads`, from
    Counting), the venue price-settlement reads and every scan."""

    def __init__(self, conn):
        super().__init__(conn)
        self.venue_reads = 0
        self.ev_scans = 0

    def _see(self, sql):
        super()._see(sql)
        if "FROM external_valuations" in sql:
            self.ev_scans += 1
            if "settlement_read IS NOT NULL" in sql:
                self.venue_reads += 1


# ═════════════════════════════════════════════════════════════════════
# SEEDING: shadows of every kind, their contracts' evidence, their books
# ═════════════════════════════════════════════════════════════════════

def _evidence(*, slug, side, p, limit, qty):
    levels = [{"price": limit, "qty": qty}]
    ce = CA.evaluate_executable(
        p=p, levels=levels, qty=qty, limit=limit, fee_fn=H.flat_fee(0.01),
        at=DECIDED, settlement={"compatibility": "COMPATIBLE"},
        identity={"us_market_slug": slug, "payout_event": "HOME",
                  "fixture": "fx-" + slug, "holding_side": side})
    return CA.capital_evidence(ce, p=p, limit=limit, threshold_edge_pp=0.5,
                               basis="TEST", levels=levels)


async def shadow(conn, a, *, kind, slug, side="LONG", p=0.60, limit=0.40,
                 qty=QTY) -> int:
    """One recorded SHADOW_COUNTERFACTUAL (the real record path)."""
    ev = _evidence(slug=slug, side=side, p=p, limit=limit, qty=qty)
    got = await CA.record_shadow(
        conn, account_id=a["account_id"], strategy=STRAT,
        decision_id="dec:%s:%s" % (a["account_id"][-10:], kind),
        order_key=None, source=CA.SRC_DECISION,
        capital_refusal=CA.R_FORWARD_UNKNOWN, lifecycle_state=None,
        slug=slug, holding_side=side, fixture="fx-" + slug,
        payout_event="HOME", decided_at=DECIDED, delay_s=2.0,
        expires_at=DECIDED + 90.0, simulator_version="test", evidence=ev)
    assert got["recorded"], got
    return got["shadow_id"]


KINDS = ("won", "lost", "void", "conflict", "vp_long", "vp_short",
         "vp_nostate", "noev", "nofill", "pair_long", "pair_short")

#: (outcome, why) settlement_outcome gives each kind
EXPECTED_VERDICT = {
    "won": ("WON", None), "lost": ("LOST", None),
    "void": ("VOID_REFUND", None),
    "conflict": (None, "CONFLICTING_SETTLEMENT_EVIDENCE"),
    "vp_long": ("SETTLED_AT_VENUE_PRICE", None),
    "vp_short": ("SETTLED_AT_VENUE_PRICE", None),
    # the outcome rows' own why is named first, as the base did
    "vp_nostate": (None, "NO_AUTHORITATIVE_SETTLEMENT_YET"),
    "noev": (None, "NO_AUTHORITATIVE_SETTLEMENT_YET"),
    "nofill": ("WON", None), "pair_long": ("WON", None),
    "pair_short": ("LOST", None)}
EXPECTED_DIGEST = {"examined": 11, "settled": 8, "pending": 3, "errors": 0}


async def seed_kinds(conn, tag):
    """One paper account with a shadow of every kind settle_shadows branches
    on. Returns (account, {kind: {slug, side, shadow_id}})."""
    a = await H.new_account(conn, tag, now=AT - 10_000.0)
    k: dict = {}

    async def mk(kind, slug=None, side="LONG", **kw):
        slug = slug or "%s%s-%s" % (SYN, uuid.uuid4().hex[:8], kind)
        sid = await shadow(conn, a, kind=kind, slug=slug, side=side, **kw)
        k[kind] = {"slug": slug, "side": side, "shadow_id": sid}
        return slug

    # unanimous WON evidence (two rows); a book in the window fills 30 of 50
    s = await mk("won")
    await ev_row(conn, s, outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await ev_row(conn, s, outcome=1, basis="VENUE_REPORTED_OUTCOME",
                 decided_at=DECIDED + 100.0)
    await H.observe(conn, s, DECIDED + 5.0, offers=[(0.40, 30)],
                    bids=[(0.38, 10)])
    # LOST; no book in the window: the IOC limit-bound fill
    s = await mk("lost")
    await ev_row(conn, s, outcome=0, basis="VENUE_SETTLEMENT_PRICE")
    # a CONFIRMED_VOID: refund
    s = await mk("void")
    await ev_row(conn, s, basis=PX.VOID_BASIS)
    # one row WON, another CONFIRMED_VOID: conflicting, pending
    s = await mk("conflict")
    await ev_row(conn, s, outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await ev_row(conn, s, basis=PX.VOID_BASIS, decided_at=DECIDED + 100.0)
    # the venue settled at a price and the text states the last-fair-price
    # settlement: a LONG and a SHORT on the same contract
    s = await mk("vp_long")
    await mk("vp_short", slug=s, side="SHORT", p=0.70, limit=0.60)
    await ev_row(conn, s, settlement_read="0.63",
                 rules=PL.RECORDED_PHI_ATL_VENUE_PROSE)
    # the venue settled at a price but the text states no price settlement
    s = await mk("vp_nostate")
    await ev_row(conn, s, settlement_read="0.63", rules=NO_PRICE_TEXT)
    # no evidence at all
    await mk("noev")
    # WON, but the book in the window has nothing at the limit: NO_FILL
    s = await mk("nofill")
    await ev_row(conn, s, outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await H.observe(conn, s, DECIDED + 5.0, offers=[(0.45, 100)],
                    bids=[(0.30, 10)])
    # one contract held LONG and SHORT, settled WON for the long side
    s = await mk("pair_long")
    await mk("pair_short", slug=s, side="SHORT", p=0.70, limit=0.60)
    await ev_row(conn, s, outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    assert set(k) == set(KINDS)
    return a, k


def _norm_evidence(ev) -> dict:
    """The outcome row's evidence without the valuation ids (two accounts
    seeded alike hold different rows)."""
    d = H.j(ev) if isinstance(ev, str) else dict(ev)
    s = d.get("settlement")
    if isinstance(s, list):
        d["settlement"] = [{kk: vv for kk, vv in e.items()
                            if kk != "valuation_id"} for e in s]
    return d


async def outcomes_by_kind(conn, k) -> dict:
    by_id = {v["shadow_id"]: kind for kind, v in k.items()}
    out = {}
    for r in await conn.fetch(
            "SELECT * FROM paper_shadow_counterfactual_outcomes "
            " WHERE shadow_id = ANY($1::bigint[]) ORDER BY shadow_id",
            list(by_id)):
        out[by_id[r["shadow_id"]]] = {
            "outcome": r["outcome"],
            "payout": (None if r["payout_per_contract"] is None
                       else float(r["payout_per_contract"])),
            "basis": r["execution_basis"],
            "has_obs": r["execution_obs_id"] is not None,
            "filled": float(r["filled_qty"]),
            "cost": float(r["exec_cost_usd"]),
            "fees": float(r["exec_fees_usd"]),
            "pnl": float(r["counterfactual_pnl_usd"]),
            "evidence": _norm_evidence(r["evidence"]),
            "evidence_class": r["evidence_class"],
            "pnl_class": r["pnl_class"]}
    return out


SHADOW_SQL = """
INSERT INTO paper_shadow_counterfactuals (shadow_key, account_id, strategy,
  source, capital_refusal, us_market_slug, holding_side, decided_at,
  eligible_at, expires_at, decision_to_execution_delay_s, p, limit_price, qty,
  levels, fills, cost_usd, fees_usd, adverse_selection_usd,
  total_executable_ev_usd, evidence)
VALUES ($1, $2, $5, 'DECISION_CAPITAL_GATE', 'CASH_WAIT_FORWARD_ECONOMICS_UNKNOWN',
  $3, 'LONG', to_timestamp($4), to_timestamp($4 + 2), to_timestamp($4 + 90),
  2, 0.60, 0.40, 50, '[]'::jsonb, '[]'::jsonb, 20.0, 0, 0, 1.0, '{}'::jsonb)
"""


async def bulk_pending_shadows(conn, a, n, *, prefix) -> list:
    """`n` shadows on contracts that have NO external_valuations rows (their
    games are not over): each stays pending and is re-read every run."""
    acct = a["account_id"]
    slugs = ["%s%s-%05d" % (prefix, acct[-8:], i) for i in range(n)]
    await conn.executemany(SHADOW_SQL, [
        ("rc63c-sh:%s:%05d" % (acct, i), acct, slugs[i], DECIDED + i, STRAT)
        for i in range(n)])
    return slugs


async def purge_shadows(conn, accounts):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM paper_shadow_counterfactual_outcomes WHERE shadow_id "
            " IN (SELECT shadow_id FROM paper_shadow_counterfactuals "
            "      WHERE account_id = ANY($1::text[]))", list(accounts))
        await conn.execute(
            "DELETE FROM paper_shadow_counterfactuals "
            " WHERE account_id = ANY($1::text[])", list(accounts))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE BATCHED VENUE-PRICE READ IS THE PER-CONTRACT READ, ROW FOR ROW
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_batched_venue_price_read_returns_each_contracts_rows_as_the_per_slug_read_did():
    conn = await H.connect()
    pre = SYN + "vrows-" + uuid.uuid4().hex[:6] + "-"
    try:
        slugs = [pre + "a", pre + "b", pre + "c", pre + "none"]
        a_, b_, c_, none_ = slugs
        # ids interleave across contracts; a row with an outcome basis and a
        # row without a settlement read are excluded, as VENUE_PRICE_SQL does
        await ev_row(conn, a_, settlement_read="0.63",
                     rules=PL.RECORDED_PHI_ATL_VENUE_PROSE)
        await ev_row(conn, b_, outcome=1, basis="VENUE_SETTLEMENT_PRICE",
                     decided_at=DECIDED + 100.0)               # basis: out
        await ev_row(conn, a_, settlement_read="0.63", rules=NO_PRICE_TEXT,
                     buy_intent="ORDER_INTENT_BUY_SHORT",
                     decided_at=DECIDED + 200.0)
        await ev_row(conn, c_, outcome=0, basis="VENUE_REPORTED_OUTCOME",
                     decided_at=DECIDED + 300.0)               # basis: out
        await ev_row(conn, b_, settlement_read="0.5",
                     decided_at=DECIDED + 400.0)               # no rules text
        await ev_row(conn, a_, decided_at=DECIDED + 500.0)    # no read: out
        await ev_row(conn, c_, settlement_read="0.4",
                     rules=PL.RECORDED_PHI_ATL_VENUE_PROSE,
                     decided_at=DECIDED + 600.0)
        ref = await per_slug_venue_reference(conn, slugs)
        got = await PX.venue_price_rows_by_slug(conn, slugs + [a_, none_, None])
        assert set(got) == set(slugs)
        for s in slugs:
            assert got[s] == ref[s], s
        assert len(got[a_]) == 2 and len(got[b_]) == 1 and len(got[c_]) == 1
        assert got[none_] == []
        for s in (a_, b_, c_):
            assert all(set(r) == {"id", "buy_intent", "settlement_read",
                                  "settlement_read_at", "rules"}
                       for r in got[s])
            ids = [r["id"] for r in got[s]]
            assert ids == sorted(ids)
        assert got[b_][0]["rules"] is None
        assert got[a_][0]["rules"] == PL.RECORDED_PHI_ATL_VENUE_PROSE
        # and venue_price_settlement judges the same from either
        for s in slugs:
            for side in ("LONG", "SHORT"):
                assert PX.venue_price_settlement(got[s], holding_side=side) \
                    == PX.venue_price_settlement(ref[s], holding_side=side)
        assert PX.venue_price_settlement(got[a_], holding_side="LONG")[
            "price"] == 0.63
        assert PX.venue_price_settlement(got[c_], holding_side="SHORT")[
            "price"] == 0.6
        assert await PX.venue_price_rows_by_slug(conn, []) == {}
        assert await PX.venue_price_rows_by_slug(conn, [None, ""]) == {}
    finally:
        await purge(conn, [pre])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · EVERY KIND OF SHADOW: THE SAME VERDICT, THE SAME OUTCOME ROWS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_settle_shadows_writes_the_same_outcomes_with_the_batched_read_as_per_contract():
    conn = await H.connect()
    accounts = []
    try:
        a_old, k_old = await seed_kinds(conn, "shold")
        a_new, k_new = await seed_kinds(conn, "shnew")
        accounts = [a_old["account_id"], a_new["account_id"]]
        # THE VERDICT PER SHADOW, before anything is written: the base's
        # settlement_outcome verbatim, the refactored settlement_outcome and
        # the batched read agree, shadow by shadow, including the evidence
        batched = await CA.batched_settlement(
            conn, [v["slug"] for v in k_new.values()])
        for kind, v in k_new.items():
            b = await base_settlement_outcome(conn, v["slug"], v["side"])
            x = await XM.settlement_outcome(conn, v["slug"], v["side"])
            n = await batched(conn, v["slug"], v["side"])
            assert b == x == n, (kind, b, x, n)
            assert (b.get("outcome"), b.get("why")) == EXPECTED_VERDICT[kind], \
                (kind, b)
        assert batched is not None
        # THE STEP'S WRITES: the per-contract settlement_fn on one account,
        # the batched read on the other, seeded alike
        reads_old = CountReads(conn)
        res_old = await CA.settle_shadows(
            reads_old, now=AT, account_id=a_old["account_id"],
            settlement_fn=base_settlement_outcome)
        reads_new = CountReads(conn)
        res_new = await CA.settle_shadows(reads_new, now=AT,
                                          account_id=a_new["account_id"])
        print("OLD", reads_old.outcome_reads, "outcome reads",
              reads_old.venue_reads, "venue reads", json.dumps(res_old))
        print("NEW", reads_new.outcome_reads, "outcome reads",
              reads_new.venue_reads, "venue reads", json.dumps(res_new))
        # 11 shadows: 11 outcome reads and 4 venue reads before; 1 + 1 now
        assert (reads_old.outcome_reads, reads_old.venue_reads) == (11, 4)
        assert (reads_new.outcome_reads, reads_new.venue_reads) == (1, 1)
        assert reads_new.ev_scans == 2
        assert res_old == res_new == EXPECTED_DIGEST, (res_old, res_new)
        snap_old = await outcomes_by_kind(conn, k_old)
        snap_new = await outcomes_by_kind(conn, k_new)
        assert snap_new == snap_old, json.dumps(
            {"old": snap_old, "new": snap_new}, default=str, indent=1)
        # and every branch did what it should
        assert set(snap_new) == {"won", "lost", "void", "vp_long", "vp_short",
                                 "nofill", "pair_long", "pair_short"}
        won = snap_new["won"]
        assert (won["outcome"], won["payout"], won["basis"], won["filled"]) \
            == ("WON", 1.0, CA.BASIS_EXEC_BOOK, 30.0)
        assert won["has_obs"] and won["cost"] == pytest.approx(12.0)
        assert won["pnl"] == pytest.approx(30.0 - 12.0 - won["fees"])
        lost = snap_new["lost"]
        assert (lost["outcome"], lost["payout"], lost["basis"],
                lost["filled"]) == ("LOST", 0.0, CA.BASIS_LIMIT_BOUND, QTY)
        assert lost["pnl"] == pytest.approx(-(lost["cost"] + lost["fees"]))
        void = snap_new["void"]
        assert (void["outcome"], void["payout"], void["pnl"]) == (
            "VOID_REFUND", None, 0.0)
        assert snap_new["vp_long"]["outcome"] == "SETTLED_AT_VENUE_PRICE"
        assert snap_new["vp_long"]["payout"] == pytest.approx(0.63)
        assert snap_new["vp_short"]["outcome"] == "SETTLED_AT_VENUE_PRICE"
        assert snap_new["vp_short"]["payout"] == pytest.approx(0.37)
        nf = snap_new["nofill"]
        assert (nf["outcome"], nf["basis"], nf["filled"], nf["pnl"]) == (
            "NO_FILL", CA.BASIS_EXEC_BOOK, 0.0, 0.0)
        assert snap_new["pair_long"]["outcome"] == "WON"
        assert snap_new["pair_short"]["outcome"] == "LOST"
        for row in snap_new.values():
            assert row["evidence_class"] == CA.EVIDENCE_CLASS
            assert row["pnl_class"] == CA.PNL_CLASS
            assert row["evidence"]["pnl_class"] == CA.PNL_CLASS
        # the evidence is the settlement's own rows
        assert len(snap_new["won"]["evidence"]["settlement"]) == 2
        assert snap_new["vp_long"]["evidence"]["settlement"][0][
            "settlement_read"] == "0.63"
        # a second run finds only the pending three, with either read, and
        # writes nothing more
        again_old = await CA.settle_shadows(
            conn, now=AT + 60, account_id=a_old["account_id"],
            settlement_fn=base_settlement_outcome)
        again_new = await CA.settle_shadows(conn, now=AT + 60,
                                            account_id=a_new["account_id"])
        assert again_old == again_new == {"examined": 3, "settled": 0,
                                          "pending": 3, "errors": 0}
        assert await outcomes_by_kind(conn, k_new) == snap_new
    finally:
        await purge_shadows(conn, accounts)
        await purge(conn, [SYN])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · 200 PENDING SHADOWS: TWO READS, UNDER 2 s (FAILS ON THE BASE)
# ═════════════════════════════════════════════════════════════════════

PENDING = 200
TABLE_ROWS = 40_000
STEP_BOUND_S = 2.0


@pg
async def test_two_hundred_pending_shadows_are_read_in_two_statements_under_two_seconds():
    """200 pending shadows (SETTLE_PER_PASS) on contracts with no evidence
    yet, over an external_valuations table of 40,000 rows with no index on
    us_market_slug. The base ran 400 scans (the reviewer's probe: 10.3 s;
    5.985 s on this machine); the step runs two."""
    conn = await H.connect()
    pre, fill = SYN + "pend-", SYN + "fill-" + uuid.uuid4().hex[:6] + "-"
    accounts = []
    try:
        a = await H.new_account(conn, "shscale", now=AT - 60_000.0)
        accounts = [a["account_id"]]
        slugs = await bulk_pending_shadows(conn, a, PENDING, prefix=pre)
        await filler_valuations(conn, TABLE_ROWS, prefix=fill)
        await conn.execute("ANALYZE external_valuations")
        total = await conn.fetchval("SELECT count(*) FROM external_valuations")
        assert total >= TABLE_ROWS
        # THE STEP
        reads = CountReads(conn)
        t0 = time.monotonic()
        res = await CA.settle_shadows(reads, now=AT, account_id=a["account_id"])
        step_s = time.monotonic() - t0
        print("SCALE pending=%d table_rows=%d step=%.3fs outcome_reads=%d "
              "venue_reads=%d scans=%d statements=%d digest=%s" % (
                  PENDING, total, step_s, reads.outcome_reads,
                  reads.venue_reads, reads.ev_scans, reads.statements,
                  json.dumps(res)))
        assert res == {"examined": PENDING, "settled": 0, "pending": PENDING,
                       "errors": 0}
        # the base ran one outcome read and one venue read PER SHADOW
        assert reads.outcome_reads == 1, reads.outcome_reads
        assert reads.venue_reads == 1, reads.venue_reads
        assert reads.ev_scans == 2, reads.ev_scans
        assert step_s < STEP_BOUND_S, "shadow settlement took %.3fs" % step_s
        # the one read saw every contract, each with no rows
        by = await PX.outcome_rows_by_slug(conn, slugs)
        vby = await PX.venue_price_rows_by_slug(conn, slugs)
        assert len(by) == len(vby) == PENDING
        assert all(v == [] for v in by.values())
        assert all(v == [] for v in vby.values())
        # THE BASE'S PER-CONTRACT LOOP on the same shadows and the same
        # table, for the record: the same digest, 400 scans
        reads_b = CountReads(conn)
        t0 = time.monotonic()
        res_b = await CA.settle_shadows(reads_b, now=AT,
                                        account_id=a["account_id"],
                                        settlement_fn=base_settlement_outcome)
        base_s = time.monotonic() - t0
        print("SCALE per_contract_loop=%.3fs scans=%d batched=%.3fs scans=%d"
              % (base_s, reads_b.ev_scans, step_s, reads.ev_scans))
        assert res_b == res
        assert reads_b.ev_scans == 2 * PENDING
    finally:
        await purge_shadows(conn, accounts)
        await purge(conn, [pre, fill])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE STEP HONOURS ITS DEADLINE AND NAMES THE CUT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_step_stops_at_its_deadline_names_the_cut_and_resumes_next_run():
    conn = await H.connect()
    pre = SYN + "cut-"
    accounts = []
    try:
        a = await H.new_account(conn, "shcut", now=AT - 60_000.0)
        acct = a["account_id"]
        accounts = [acct]
        await bulk_pending_shadows(conn, a, 50, prefix=pre)
        # no time left: nothing examined, no read spent, the cut named
        reads = CountReads(conn)
        res = await CA.step(reads, {"account_id": acct, "now": AT,
                                    "step_deadline": time.monotonic() - 5.0})
        print("CUT", json.dumps(res))
        assert res["ran"] is True
        assert res["examined"] == 0 and res["not_examined"] == 50
        assert res["cut"] == CA.R_SETTLE_CUT_AT_STEP_DEADLINE
        assert res["why"].startswith(CA.R_SETTLE_CUT_AT_STEP_DEADLINE)
        assert "50 of 50" in res["why"]
        assert reads.ev_scans == 0
        assert (res["settled"], res["pending"], res["errors"]) == (0, 0, 0)
        # within RUN_EVERY_S the step does not run again
        assert (await CA.step(conn, {"account_id": acct, "now": AT + 1,
                                     "step_deadline": time.monotonic() + 30}))[
            "ran"] is False
        # the next run resumes with the same shadows
        res2 = await CA.step(conn, {"account_id": acct,
                                    "now": AT + CA.RUN_EVERY_S + 1,
                                    "step_deadline": time.monotonic() + 30})
        assert res2["examined"] == 50 and res2["pending"] == 50
        assert "cut" not in res2 and "not_examined" not in res2
        # a deadline that falls mid-way: the loop stops with the counts whole
        res3 = await CA.settle_shadows(
            conn, now=AT, account_id=acct,
            step_deadline=(time.monotonic() + CA.SETTLE_STOP_BEFORE_DEADLINE_S
                           + 0.02))
        assert res3["examined"] + res3.get("not_examined", 0) == 50, res3
        if "cut" in res3:
            assert res3["examined"] < 50
            assert res3["cut"] == CA.R_SETTLE_CUT_AT_STEP_DEADLINE
        # no deadline given (a direct caller): every shadow is examined
        res4 = await CA.settle_shadows(conn, now=AT, account_id=acct)
        assert res4 == {"examined": 50, "settled": 0, "pending": 50,
                        "errors": 0}
        # the code is a registered refusal
        assert CA.R_SETTLE_CUT_AT_STEP_DEADLINE in TT.TABLE
        assert CA.SETTLE_STOP_BEFORE_DEADLINE_S == 1.0
    finally:
        await purge_shadows(conn, accounts)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE STEP ORDER: SHADOW SETTLEMENT IS PRE-DEREK TIME
# ═════════════════════════════════════════════════════════════════════

def test_shadow_settlement_is_the_capital_authority_step_and_precedes_derek(
        monkeypatch):
    """Under N2 an ENTER-owing step is not started with less than 33 s of
    the 80 s steps' bound left, so every second a pre-derek step takes counts
    against the decision steps' start; and every pre-derek second past the
    pass budget (pass_budget_s, 20 s) leaves derek's loop budget_exhausted
    before its first candidate. shadow_settlement is such a step."""
    for flag in ("", "on"):
        monkeypatch.setenv("PAPER_BENCHMARK", flag)
        steps = PRT.default_steps()
        names = [n for n, _ in steps]
        assert dict(steps)["shadow_settlement"] is CA.step
        i_shadow, i_derek = names.index("shadow_settlement"), names.index(
            "derek")
        assert i_shadow < i_derek < names.index("settle")
        pre = names[:i_derek]
        assert "books" in pre and "shadow_settlement" in pre
        for n in PRT.ENTER_OWING_STEPS:
            assert n not in pre
    d = PRT.describe()
    assert d["enter_owing_steps_bound_s"] == 48.0
    assert CA.SETTLE_PER_PASS == 200 and CA.RUN_EVERY_S == 600.0
