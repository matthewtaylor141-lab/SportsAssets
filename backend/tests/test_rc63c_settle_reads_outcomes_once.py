"""CAPITAL-CRITICAL: THE SETTLE STEP READS EVERY POSITION'S OUTCOME ROWS IN ONE
STATEMENT (RC6.3c pass-hardening S).

PRODUCTION, release 4534b43f deployed 15:53Z 2026-10-10: the paper pass now
records every pass, and its heartbeat showed where the time went --
step_elapsed_s settle 35.374 s with the step's own digest settled 0, waiting 0.
paper_xavier.step_settle loads L.positions(conn, acct, include_closed=True)
(about 517 positions, almost all closed; a closed position is still read
because its settlement can be corrected) and ran ONE statement PER POSITION:

    SELECT id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
      FROM external_valuations WHERE us_market_slug=$1
       AND outcome_basis IS NOT NULL ORDER BY id

(a 2 s trace of the pass connection, research-sql 38066812843, shows hundreds
of these back to back). external_valuations has no index on us_market_slug,
so each one scanned the table. One pass at 16:10:53Z took 80.2 s and hit the
step bound: agent_work_queues was cut, lesson_usage and agent_memory skipped.

THE FIX: the outcome rows of all the positions' contracts are read in ONE
statement (paper_xavier.OUTCOME_ROWS_SQL: `us_market_slug = ANY($1::text[])
... ORDER BY id`) and grouped per contract in Python
(paper_xavier.outcome_rows_by_slug), each contract's rows in the same id
order; every branch of the step is unchanged -- the open settle, the
completed-game venue-price settlement, the conflicting-evidence finding, the
correction of a closed position's settlement, the release of open orders on a
settled market.

WHAT IS PROVED HERE, on a real Postgres:
  * row for row, the batched read returns for every contract exactly the rows
    the per-slug statement returns (interleaved ids across contracts, rows
    without an outcome basis excluded, a contract with no rows, a contract
    named twice);
  * the step books the SAME outcomes over seeded positions of every kind --
    open and waiting, open and settled WON, a LONG and a SHORT on one contract,
    exited (sold out), settled and agreeing, settled and CORRECTED by later
    evidence, CONFLICTING evidence, a completed-game position settled at the
    venue's price, a completed-game position whose price settlement the
    contract text does not state, and a resting protection released on
    settlement -- whether the rows come from the former per-slug loop or the
    batched read: the step's digest, the settlements (versions, outcomes,
    payouts), the ledger, the findings, the order states, the positions;
  * FAILS ON THE BASE (ae5d3f84 and before): over 600 positions the step runs
    exactly ONE statement against external_valuations' outcome rows, not one
    per position;
  * FAILS ON THE BASE: at production scale (600 positions, an
    external_valuations table of 40,000 rows with no index on us_market_slug)
    the step ends in under 2 s on the migrated test database (the per-slug
    loop scanned the table 600 times);
  * the N2 design question: every step that can owe an ENTER runs BEFORE the
    settle step in the pass order (with and without PAPER_BENCHMARK), so the
    settle step's duration -- 35 s before this fix, under 2 s after -- can
    never decide whether a decision step is started; the ENTER-owing bound
    (steps' bound 80 s less the 32 s owed-ENTER holdback = 48 s; not started
    with less than 33 s left) is pinned.

SYNTHETIC rows on a scratch test database under fresh paper accounts; no
venue, no order authority, nothing of the live paper account.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_runtime as PRT
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

AT = H.T0 + 80_000.0
EXP = "rc63c-settle-proof"
SYN = "rc63c-s-"
QTY = 10.0
PRICE = 0.40
#: the statement the base ran once per position, verbatim
PER_SLUG_SQL = (
    "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
    "       outcome_at FROM external_valuations "
    " WHERE us_market_slug=$1 AND outcome_basis IS NOT NULL "
    " ORDER BY id")
NO_PRICE_STATED = ("VENUE_SETTLED_AT_A_PRICE_BUT_THE_CONTRACT_TEXT_HELD_"
                   "STATES_NO_PRICE_SETTLEMENT")


def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


async def per_slug_reference(conn, slugs) -> dict:
    """THE FORMER READ: one PER_SLUG_SQL statement per distinct contract, the
    rows as dicts -- what step_settle fed outcome_for on the base."""
    out = {}
    for s in sorted({s for s in slugs if s}):
        out[s] = [dict(r) for r in await conn.fetch(PER_SLUG_SQL, s)]
    return out


# ═════════════════════════════════════════════════════════════════════
# SEEDING: outcome rows and paper positions, written straight into the tables
# ═════════════════════════════════════════════════════════════════════

#: a valuation row is recorded PROSPECTIVE (the table's trigger refuses an
#: outcome on insert); the outcome is joined afterwards by UPDATE, as the
#: outcome join does in production
EV_SQL = (
    "INSERT INTO external_valuations (experiment_id, version, source_class, "
    " provider, book, devig_method, venue, us_market_slug, contract_selection,"
    " sport_family, market, raw_odds, outcomes_priced, expected_outcomes, "
    " decision, admissible, decided_at, record_purpose, buy_intent, "
    " settlement_read, settlement_read_at, settlement_comparison) "
    "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION','test',"
    " 'pinnacle','power','PMUS',$2,'HOME','baseball','h2h','{}'::jsonb,2,2,"
    " 'NO_TRADE',false,to_timestamp($3::float8),'ENTRY_DECISION',$4::text,"
    " $5::text,"
    " CASE WHEN $5::text IS NULL THEN NULL"
    "      ELSE to_timestamp($3::float8 + 3600) END,"
    " $6::jsonb) RETURNING id")
EV_OUTCOME_SQL = (
    "UPDATE external_valuations SET outcome_known = $2::boolean, "
    " outcome = $3::int, outcome_basis = $4::text, "
    " outcome_at = CASE WHEN $2::boolean THEN decided_at + interval '1 hour'"
    "                   END WHERE id = $1")


async def ev_row(conn, slug, *, buy_intent=PL.LONG, outcome=None,
                 basis=None, settlement_read=None, rules=None,
                 decided_at=AT - 7200.0) -> int:
    """One external_valuations row for `slug`: a settled 0/1 (outcome +
    basis), a CONFIRMED_VOID (basis alone), a venue PRICE settlement
    (settlement_read + the contract text), or an unsettled row."""
    comp = None if rules is None else json.dumps({"venue_rules_text": rules})
    vid = await conn.fetchval(EV_SQL, EXP, slug, float(decided_at),
                              buy_intent, settlement_read, comp)
    if basis is not None or outcome is not None:
        await conn.execute(EV_OUTCOME_SQL, vid, outcome is not None, outcome,
                           basis)
    return vid


ORDER_SQL = (
    "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
    " session_id, group_id, role, direction, holding_side, intent, "
    " us_market_slug, fixture, order_type, time_in_force, allow_partial, "
    " qty, limit_price, wire_price, filled_qty, state, decided_at, "
    " eligible_at, expires_at, simulator_version, strategy, terminal_at) "
    "VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,'fx-1',$10,$11,true,$12,$13,$14,"
    " $15,$16,to_timestamp($17),to_timestamp($17),to_timestamp($17 + 90),"
    " 'PAPER_SIM_V1',$18,CASE WHEN $16 = 'FILLED' THEN to_timestamp($17 + 2)"
    " END)")
FILL_SQL = (
    "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, account_id,"
    " session_id, group_id, role, direction, holding_side, us_market_slug, "
    " fixture, qty, price, wire_price, fee_usd, gross_usd, filled_at, basis, "
    " simulator_version, strategy) VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,"
    " 'fx-1',$10,$11,$12,0,$13,to_timestamp($14),'DEPTH_WALK_WITHIN_LIMIT',"
    " 'PAPER_SIM_V1',$15)")
SETTLEMENT_SQL = (
    "INSERT INTO paper_settlements (settlement_id, account_id, position_key, "
    " settlement_event_key, version, group_id, us_market_slug, holding_side, "
    " qty, outcome, payout_per_contract, payout_usd, evidence, "
    " evidence_source, settled_at) VALUES ($1,$2,$3,$4,1,$5,$6,$7,$8,$9,$10,"
    " $11,'{}'::jsonb,'external_valuations.outcome_basis',to_timestamp($12))")


def _intent(direction, side):
    if direction == "BUY":
        return ("ORDER_INTENT_BUY_SHORT" if side == "SHORT"
                else "ORDER_INTENT_BUY_LONG")
    return ("ORDER_INTENT_SELL_SHORT" if side == "SHORT"
            else "ORDER_INTENT_SELL_LONG")


def _order_args(a, *, group_id, slug, role, direction, side, qty, price,
                state, at, strategy, order_type="MARKETABLE", tif="IOC"):
    oid = "paperord:" + uuid.uuid4().hex[:24]
    wire = price if side == "LONG" else round(1 - price, 6)
    return (oid, a["account_id"], a["session_id"], group_id, role, direction,
            side, _intent(direction, side), slug, order_type, tif, qty, price,
            wire, qty if state == "FILLED" else 0, state, float(at), strategy)


def _fill_args(a, *, order_id, group_id, slug, role, direction, side, qty,
               price, at, strategy):
    fid = "paperfill:" + uuid.uuid4().hex[:24]
    wire = price if side == "LONG" else round(1 - price, 6)
    return (fid, order_id, a["account_id"], a["session_id"], group_id, role,
            direction, side, slug, qty, price, wire, round(qty * price, 6),
            float(at), strategy)


async def entry(conn, a, *, group_id, slug, side="LONG", qty=QTY,
                price=PRICE, strategy=L.DEFAULT_STRATEGY, at=AT - 6000.0):
    """A FILLED entry order and its fill: an open paper position."""
    args = _order_args(a, group_id=group_id, slug=slug, role="ENTRY",
                       direction="BUY", side=side, qty=qty, price=price,
                       state="FILLED", at=at, strategy=strategy)
    await conn.execute(ORDER_SQL, *args)
    await conn.execute(FILL_SQL, *_fill_args(
        a, order_id=args[0], group_id=group_id, slug=slug, role="ENTRY",
        direction="BUY", side=side, qty=qty, price=price, at=at + 2,
        strategy=strategy))
    return args[0]


async def sale(conn, a, *, group_id, slug, side="LONG", qty=QTY, price=0.55,
               at=AT - 3000.0):
    """A FILLED EXIT order and its fill: the position is sold out."""
    args = _order_args(a, group_id=group_id, slug=slug, role="EXIT",
                       direction="SELL", side=side, qty=qty, price=price,
                       state="FILLED", at=at, strategy=L.DEFAULT_STRATEGY)
    await conn.execute(ORDER_SQL, *args)
    await conn.execute(FILL_SQL, *_fill_args(
        a, order_id=args[0], group_id=group_id, slug=slug, role="EXIT",
        direction="SELL", side=side, qty=qty, price=price, at=at + 2,
        strategy=L.DEFAULT_STRATEGY))


async def resting_protection(conn, a, *, group_id, slug, side="LONG",
                             qty=QTY, price=0.38, at=AT - 2000.0):
    args = _order_args(a, group_id=group_id, slug=slug,
                       role="STANDING_PROTECTION", direction="SELL",
                       side=side, qty=qty, price=price, state="RESTING",
                       at=at, strategy=L.DEFAULT_STRATEGY,
                       order_type="RESTING", tif="GTD")
    await conn.execute(ORDER_SQL, *args)
    return args[0]


def _key(slug):
    return "venue-final:%s" % slug


async def settled_through_the_ledger(conn, a, *, group_id, slug, side,
                                     outcome, at):
    """The real settlement path (L.settle) with the step's own event key, so
    a later correction finds it."""
    got = await L.settle(conn, account_id=a["account_id"], group_id=group_id,
                         slug=slug, holding_side=side,
                         settlement_event_key=_key(slug), outcome=outcome,
                         evidence={"seeded": True},
                         evidence_source="external_valuations.outcome_basis",
                         at=at, session_id=a["session_id"])
    assert got["ok"] and not got.get("duplicate"), got
    return got


KINDS = ("waiting", "won", "pair_long", "pair_short", "exited", "agree",
         "corrected", "conflict", "cg_price", "cg_pending")


async def seed_kinds(conn, tag):
    """One paper account holding a position of every kind the settle step
    branches on. Returns (account, {kind: {group_id, slug, side}})."""
    a = await H.new_account(conn, tag, now=AT - 10_000.0)
    tail = a["account_id"][-10:]
    k: dict = {}

    def mk(kind, slug=None, side="LONG"):
        k[kind] = {"group_id": "paper_g_%s_%s" % (tail, kind),
                   "slug": slug or "%s%s-%s" % (SYN, uuid.uuid4().hex[:8],
                                                kind),
                   "side": side}
        return k[kind]

    # open, no evidence: waits
    w = mk("waiting")
    await entry(conn, a, group_id=w["group_id"], slug=w["slug"])
    # open, unanimous WON evidence (two rows), a resting protection released
    won = mk("won")
    await entry(conn, a, group_id=won["group_id"], slug=won["slug"])
    await resting_protection(conn, a, group_id=won["group_id"],
                             slug=won["slug"])
    await ev_row(conn, won["slug"], outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await ev_row(conn, won["slug"], outcome=1, basis="VENUE_REPORTED_OUTCOME",
                 decided_at=AT - 7100.0)
    # ONE contract held LONG in one group and SHORT in another: WON / LOST
    pl = mk("pair_long")
    ps = mk("pair_short", slug=pl["slug"], side="SHORT")
    await entry(conn, a, group_id=pl["group_id"], slug=pl["slug"])
    await entry(conn, a, group_id=ps["group_id"], slug=ps["slug"],
                side="SHORT", price=0.60)
    await ev_row(conn, pl["slug"], outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    # sold out before settlement; evidence arrived later: nothing to book
    ex = mk("exited")
    await entry(conn, a, group_id=ex["group_id"], slug=ex["slug"])
    await sale(conn, a, group_id=ex["group_id"], slug=ex["slug"])
    await ev_row(conn, ex["slug"], outcome=0, basis="VENUE_SETTLEMENT_PRICE")
    # settled WON through the ledger; evidence agrees
    ag = mk("agree")
    await entry(conn, a, group_id=ag["group_id"], slug=ag["slug"])
    await ev_row(conn, ag["slug"], outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await settled_through_the_ledger(conn, a, group_id=ag["group_id"],
                                     slug=ag["slug"], side="LONG",
                                     outcome="WON", at=AT - 1000.0)
    # settled WON through the ledger; the evidence now says LOST: corrected
    co = mk("corrected")
    await entry(conn, a, group_id=co["group_id"], slug=co["slug"])
    vid = await ev_row(conn, co["slug"], outcome=1,
                       basis="VENUE_SETTLEMENT_PRICE")
    await settled_through_the_ledger(conn, a, group_id=co["group_id"],
                                     slug=co["slug"], side="LONG",
                                     outcome="WON", at=AT - 1000.0)
    await conn.execute("UPDATE external_valuations SET outcome=0 WHERE id=$1",
                       vid)
    # open; one row says WON, another CONFIRMED_VOID: conflict, a finding
    cf = mk("conflict")
    await entry(conn, a, group_id=cf["group_id"], slug=cf["slug"])
    await ev_row(conn, cf["slug"], outcome=1, basis="VENUE_SETTLEMENT_PRICE")
    await ev_row(conn, cf["slug"], basis=PX.VOID_BASIS, decided_at=AT - 7100.0)
    # completed-game policy, open, no outcome basis, the venue settled at a
    # price and the contract's text states the last-fair-price settlement
    cp = mk("cg_price")
    await entry(conn, a, group_id=cp["group_id"], slug=cp["slug"],
                strategy=PB.CG_STRATEGY)
    await ev_row(conn, cp["slug"], settlement_read="0.63",
                 rules=PL.RECORDED_PHI_ATL_VENUE_PROSE)
    # the same, but the contract's text states no price settlement: pending
    cq = mk("cg_pending")
    await entry(conn, a, group_id=cq["group_id"], slug=cq["slug"],
                strategy=PB.CG_STRATEGY)
    await ev_row(conn, cq["slug"], settlement_read="0.63",
                 rules="This market settles to the winner. Outcome sourced "
                       "from MLB.")
    return a, k


async def snapshot(conn, a, k) -> dict:
    """The account's state after the step, keyed by position kind (never by
    an id), so two accounts seeded alike compare equal."""
    by_group = {v["group_id"]: kind for kind, v in k.items()}
    out: dict = {"settlements": {}, "ledger": [], "findings": {},
                 "orders": {}, "positions": {}}
    for r in await conn.fetch(
            "SELECT group_id, version, outcome, payout_per_contract, qty, "
            "       payout_usd, evidence_source, supersedes IS NOT NULL AS sup "
            "  FROM paper_settlements WHERE account_id=$1 "
            " ORDER BY group_id, version", a["account_id"]):
        out["settlements"].setdefault(by_group[r["group_id"]], []).append(
            (r["version"], r["outcome"], float(r["payout_per_contract"]),
             float(r["qty"]), float(r["payout_usd"]), r["evidence_source"],
             r["sup"]))
    for r in await conn.fetch(
            "SELECT kind, cash_delta_usd, group_id FROM paper_ledger "
            " WHERE account_id=$1 ORDER BY seq", a["account_id"]):
        out["ledger"].append((r["kind"], float(r["cash_delta_usd"]),
                              by_group.get(r["group_id"])))
    for r in await conn.fetch(
            "SELECT kind, severity, subject, detail FROM paper_audrey_findings "
            " WHERE account_id=$1 ORDER BY kind, subject", a["account_id"]):
        kind = next(kd for g, kd in by_group.items() if g in r["subject"])
        d = H.j(r["detail"])
        out["findings"][kind] = (r["kind"], r["severity"], d.get("why"),
                                 len(d.get("evidence") or []))
    for r in await conn.fetch(
            "SELECT group_id, role, state FROM paper_orders WHERE account_id=$1"
            " ORDER BY group_id, role", a["account_id"]):
        out["orders"].setdefault(by_group[r["group_id"]], []).append(
            (r["role"], r["state"]))
    for p in await L.positions(conn, a["account_id"], include_closed=True):
        s = p.get("settlement")
        out["positions"][(by_group[p["group_id"]], p["holding_side"])] = (
            round(p["open_qty"], 6), round(p["settled_qty"], 6),
            None if s is None else (s["outcome"], s["version"],
                                    round(s["payout_usd"], 6)))
    return out


class Counting:
    """The pass connection, with every statement that touches
    external_valuations' outcome rows counted."""

    def __init__(self, conn):
        self._c = conn
        self.outcome_reads = 0
        self.statements = 0

    def _see(self, sql):
        self.statements += 1
        if "FROM external_valuations" in sql and \
                "outcome_basis IS NOT NULL" in sql:
            self.outcome_reads += 1

    async def fetch(self, sql, *a, **k):
        self._see(sql)
        return await self._c.fetch(sql, *a, **k)

    async def fetchrow(self, sql, *a, **k):
        self._see(sql)
        return await self._c.fetchrow(sql, *a, **k)

    async def fetchval(self, sql, *a, **k):
        self._see(sql)
        return await self._c.fetchval(sql, *a, **k)

    async def execute(self, sql, *a, **k):
        self._see(sql)
        return await self._c.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


async def bulk_closed_positions(conn, a, n, *, prefix):
    """`n` positions settled WON (the step's own event key) whose contracts
    each carry one agreeing outcome row -- production's shape: the step has
    nothing to book for any of them. Returns the slugs."""
    slugs = ["%s%s-%05d" % (prefix, uuid.uuid4().hex[:6], i)
             for i in range(n)]
    tail = a["account_id"][-10:]
    orders, fills, setts, evs = [], [], [], []
    at = AT - 50_000.0
    for i, slug in enumerate(slugs):
        g = "paper_g_%s_b%05d" % (tail, i)
        o = _order_args(a, group_id=g, slug=slug, role="ENTRY",
                        direction="BUY", side="LONG", qty=QTY, price=PRICE,
                        state="FILLED", at=at + i, strategy=L.DEFAULT_STRATEGY)
        orders.append(o)
        fills.append(_fill_args(a, order_id=o[0], group_id=g, slug=slug,
                                role="ENTRY", direction="BUY", side="LONG",
                                qty=QTY, price=PRICE, at=at + i + 2,
                                strategy=L.DEFAULT_STRATEGY))
        pk = L.position_key(account_id=a["account_id"], group_id=g, slug=slug,
                            holding_side="LONG")
        setts.append(("paperset:" + uuid.uuid4().hex[:24], a["account_id"],
                      pk, _key(slug), g, slug, "LONG", QTY, "WON", 1.0, QTY,
                      at + i + 7200.0))
        evs.append((EXP, slug, at + i, PL.LONG, None, None))
    await conn.executemany(ORDER_SQL, orders)
    await conn.executemany(FILL_SQL, fills)
    await conn.executemany(SETTLEMENT_SQL, setts)
    await conn.executemany(EV_SQL.replace(" RETURNING id", ""), evs)
    await conn.execute(
        "UPDATE external_valuations SET outcome_known = true, outcome = 1, "
        " outcome_basis = 'VENUE_SETTLEMENT_PRICE', "
        " outcome_at = decided_at + interval '1 hour' "
        " WHERE experiment_id = $1 AND us_market_slug LIKE $2", EXP,
        prefix + "%")
    return slugs


async def filler_valuations(conn, n, *, prefix):
    """`n` more external_valuations rows on other contracts (half with an
    outcome basis, half without): the table a per-contract read scans."""
    await conn.execute(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, us_market_slug, "
        " contract_selection, sport_family, market, raw_odds, outcomes_priced,"
        " expected_outcomes, decision, admissible, decided_at, "
        " record_purpose, buy_intent) "
        "SELECT $1, 'PINNACLE_DEVIG_V1', 'EXTERNAL_BOOKMAKER_VALUATION', "
        "       'test', 'pinnacle', 'power', 'PMUS', "
        "       $2 || CASE WHEN i % 2 = 0 THEN 'o' ELSE 'n' END || i::text, "
        "       'HOME', 'baseball', 'h2h', '{}'::jsonb, 2, 2, 'NO_TRADE', "
        "       false, to_timestamp($3::float8 + i), 'ENTRY_DECISION', $4 "
        "  FROM generate_series(1, $5::int) AS i",
        EXP, prefix, AT - 90_000.0, PL.LONG, int(n))
    await conn.execute(
        "UPDATE external_valuations SET outcome_known = true, outcome = 1, "
        " outcome_basis = 'VENUE_SETTLEMENT_PRICE', "
        " outcome_at = decided_at + interval '1 hour' "
        " WHERE experiment_id = $1 AND us_market_slug LIKE $2", EXP,
        prefix + "o%")


async def purge(conn, prefixes):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for p in prefixes:
            await conn.execute(
                "DELETE FROM external_valuations WHERE us_market_slug LIKE $1",
                p + "%")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE BATCHED READ IS THE PER-SLUG READ, ROW FOR ROW
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_batched_read_returns_each_contracts_rows_as_the_per_slug_read_did():
    conn = await H.connect()
    pre = SYN + "rows-" + uuid.uuid4().hex[:6] + "-"
    try:
        slugs = [pre + "a", pre + "b", pre + "c", pre + "none"]
        a_, b_, c_, none_ = slugs
        # ids interleave across contracts; rows without a basis are excluded
        await ev_row(conn, a_, outcome=1, basis="VENUE_SETTLEMENT_PRICE")
        await ev_row(conn, b_, outcome=0, basis="VENUE_REPORTED_OUTCOME",
                     decided_at=AT - 7100.0)
        await ev_row(conn, a_, basis=PX.VOID_BASIS, decided_at=AT - 7000.0)
        await ev_row(conn, c_)                              # no basis: excluded
        await ev_row(conn, b_, outcome=1, basis="VENUE_SETTLEMENT_PRICE",
                     buy_intent="ORDER_INTENT_BUY_SHORT",
                     decided_at=AT - 6900.0)
        await ev_row(conn, a_, outcome=1, basis="VENUE_REPORTED_OUTCOME",
                     decided_at=AT - 6800.0)
        await ev_row(conn, c_, settlement_read="0.5",
                     decided_at=AT - 6700.0)                # no basis: excluded
        ref = await per_slug_reference(conn, slugs)
        got = await PX.outcome_rows_by_slug(conn, slugs + [a_, none_, None])
        assert set(got) == set(slugs)
        for s in slugs:
            assert got[s] == ref[s], s
        assert len(got[a_]) == 3 and len(got[b_]) == 2
        assert got[c_] == [] and got[none_] == []
        # the same keys, and id order within each contract
        for s in (a_, b_):
            assert all(set(r) == {"id", "buy_intent", "outcome",
                                  "outcome_known", "outcome_basis",
                                  "outcome_at"} for r in got[s])
            ids = [r["id"] for r in got[s]]
            assert ids == sorted(ids)
        # and outcome_for sees the same verdicts from either
        for s in slugs:
            for side in ("LONG", "SHORT"):
                assert PX.outcome_for(got[s], holding_side=side) == \
                    PX.outcome_for(ref[s], holding_side=side)
        assert await PX.outcome_rows_by_slug(conn, []) == {}
        assert await PX.outcome_rows_by_slug(conn, [None, ""]) == {}
    finally:
        await purge(conn, [pre])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE STEP BOOKS THE SAME OUTCOMES WITH EITHER READ, EVERY BRANCH
# ═════════════════════════════════════════════════════════════════════

EXPECTED_DIGEST = {"settled": 4, "corrected": 1, "conflicts": 1, "waiting": 2,
                   "settled_at_venue_price": 1,
                   "pending_reasons": {NO_PRICE_STATED: 1}}


@pg
async def test_the_settle_step_books_the_same_outcomes_with_the_batched_read_as_with_the_per_slug_loop(
        monkeypatch):
    conn = await H.connect()
    try:
        # A: the base's read, one statement per contract, through the same
        # step (the batched read swapped for the per-slug reference)
        a1, k1 = await seed_kinds(conn, "s1old")
        monkeypatch.setattr(PX, "outcome_rows_by_slug", per_slug_reference)
        reads = Counting(conn)
        res_old = await PX.step_settle(reads, _ctx(a1, AT))
        old_reads = reads.outcome_reads
        monkeypatch.undo()
        snap_old = await snapshot(conn, a1, k1)
        # B: the batched read
        a2, k2 = await seed_kinds(conn, "s2new")
        reads = Counting(conn)
        res_new = await PX.step_settle(reads, _ctx(a2, AT))
        snap_new = await snapshot(conn, a2, k2)
        print("OLD", old_reads, "outcome reads", json.dumps(res_old))
        print("NEW", reads.outcome_reads, "outcome reads",
              json.dumps(res_new))
        # the per-slug loop read once per contract (10 positions on 9
        # contracts); the batched step reads once
        assert old_reads == 9 and reads.outcome_reads == 1
        # the step's digest, and what it is expected to be
        assert res_old == res_new == EXPECTED_DIGEST, (res_old, res_new)
        # the state, kind by kind
        assert snap_new == snap_old, json.dumps(
            {"old": snap_old, "new": snap_new}, default=str, indent=1)
        # and every branch did what it should
        s = snap_new["settlements"]
        assert s["won"] == [(1, "WON", 1.0, QTY, QTY,
                             "external_valuations.outcome_basis", False)]
        assert s["pair_long"][0][1:3] == ("WON", 1.0)
        assert s["pair_short"][0][1:3] == ("LOST", 0.0)
        assert s["agree"] == [(1, "WON", 1.0, QTY, QTY,
                               "external_valuations.outcome_basis", False)]
        assert [x[:3] for x in s["corrected"]] == [(1, "WON", 1.0),
                                                   (2, "LOST", 0.0)]
        assert s["corrected"][1][6] is True            # supersedes version 1
        assert s["cg_price"][0][1:3] == ("SETTLED_AT_VENUE_PRICE", 0.63)
        assert s["cg_price"][0][5] == "external_valuations.settlement_read"
        for kind in ("waiting", "exited", "conflict", "cg_pending"):
            assert kind not in s, kind
        assert snap_new["findings"] == {
            "conflict": ("CONFLICTING_SETTLEMENT_EVIDENCE", "WARNING",
                         "CONFLICTING_SETTLEMENT_EVIDENCE", 2)}
        assert ("STANDING_PROTECTION", "CANCELED") in snap_new["orders"]["won"]
        pos = snap_new["positions"]
        assert pos[("waiting", "LONG")][0] == QTY
        assert pos[("cg_pending", "LONG")][0] == QTY
        assert pos[("conflict", "LONG")][0] == QTY
        assert pos[("exited", "LONG")] == (0.0, 0.0, None)
        assert pos[("won", "LONG")][2] == ("WON", 1, QTY)
        assert pos[("corrected", "LONG")][2] == ("LOST", 2, 0.0)
        assert pos[("cg_price", "LONG")][2] == ("SETTLED_AT_VENUE_PRICE", 1,
                                                round(QTY * 0.63, 6))
        kinds = [x[0] for x in snap_new["ledger"]]
        assert kinds.count("SETTLEMENT") == 6 and kinds.count(
            "CORRECTION") == 1, kinds
        # a second pass books nothing more, with either read
        monkeypatch.setattr(PX, "outcome_rows_by_slug", per_slug_reference)
        again_old = await PX.step_settle(conn, _ctx(a1, AT + 60))
        monkeypatch.undo()
        again_new = await PX.step_settle(conn, _ctx(a2, AT + 60))
        assert again_old == again_new
        assert again_new["settled"] == 0 and again_new["corrected"] == 0
        assert again_new["conflicts"] == 1 and again_new["waiting"] == 2
        assert await snapshot(conn, a2, k2) == snap_new
    finally:
        await purge(conn, [SYN])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · ONE STATEMENT FOR 600 POSITIONS (FAILS ON THE BASE)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_settle_step_reads_outcome_rows_in_one_statement_for_six_hundred_positions():
    conn = await H.connect()
    pre = SYN + "bulk-"
    try:
        a = await H.new_account(conn, "s3bulk", now=AT - 60_000.0)
        slugs = await bulk_closed_positions(conn, a, 600, prefix=pre)
        assert len(await L.positions(conn, a["account_id"],
                                     include_closed=True)) == 600
        reads = Counting(conn)
        res = await PX.step_settle(reads, _ctx(a, AT))
        print("600 positions:", reads.outcome_reads, "outcome reads,",
              reads.statements, "statements", json.dumps(res))
        assert res["settled"] == 0 and res["corrected"] == 0
        assert res["conflicts"] == 0 and res["waiting"] == 0
        # the base ran 600 of these; the step runs one
        assert reads.outcome_reads == 1, reads.outcome_reads
        # and the one read saw every contract
        by = await PX.outcome_rows_by_slug(conn, slugs)
        assert len(by) == 600 and all(len(v) == 1 for v in by.values())
    finally:
        await purge(conn, [pre])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · UNDER 2 s AT PRODUCTION SCALE (FAILS ON THE BASE)
# ═════════════════════════════════════════════════════════════════════

POSITIONS = 600
TABLE_ROWS = 40_000
STEP_BOUND_S = 2.0


@pg
async def test_the_settle_step_is_under_two_seconds_at_production_scale():
    """600 positions (production had ~517) over an external_valuations table
    of 40,000 rows with no index on us_market_slug, on the migrated test
    database. The per-slug loop scanned the table once per position (the
    production pass connection showed hundreds of these back to back, 35 s a
    pass); the batched read scans it once."""
    conn = await H.connect()
    pre, fill = SYN + "scale-", SYN + "fill-" + uuid.uuid4().hex[:6] + "-"
    try:
        a = await H.new_account(conn, "s4scale", now=AT - 60_000.0)
        slugs = await bulk_closed_positions(conn, a, POSITIONS, prefix=pre)
        await filler_valuations(conn, TABLE_ROWS - POSITIONS, prefix=fill)
        await conn.execute("ANALYZE external_valuations")
        total = await conn.fetchval("SELECT count(*) FROM external_valuations")
        assert total >= TABLE_ROWS
        # THE STEP
        reads = Counting(conn)
        t0 = time.monotonic()
        res = await PX.step_settle(reads, _ctx(a, AT))
        step_s = time.monotonic() - t0
        print("SCALE positions=%d table_rows=%d step=%.3fs outcome_reads=%d "
              "statements=%d digest=%s" % (POSITIONS, total, step_s,
                                           reads.outcome_reads,
                                           reads.statements, json.dumps(res)))
        assert res["settled"] == 0 and res["waiting"] == 0
        assert reads.outcome_reads == 1
        assert step_s < STEP_BOUND_S, "settle step took %.3fs" % step_s
        # the former read and the batched read, measured on the same table
        # for the record, and equal
        t0 = time.monotonic()
        ref = await per_slug_reference(conn, slugs)
        per_slug_s = time.monotonic() - t0
        t0 = time.monotonic()
        batched = await PX.outcome_rows_by_slug(conn, slugs)
        batched_s = time.monotonic() - t0
        print("SCALE per_slug_read=%.3fs batched_read=%.3fs" % (per_slug_s,
                                                                 batched_s))
        assert batched == ref
    finally:
        await purge(conn, [pre, fill])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE N2 DESIGN QUESTION: THE DECISION STEPS RUN BEFORE SETTLE
# ═════════════════════════════════════════════════════════════════════

def test_every_enter_owing_step_runs_before_the_settle_step(monkeypatch):
    """N2 cuts an ENTER-owing step 32 s earlier than other steps (at 48 s of
    the 80 s steps' bound) and does not start one with less than 33 s of
    step time left. The settle step -- 35 s a pass before this fix -- comes
    AFTER every ENTER-owing step in the order, so its duration never decides
    whether a decision step starts; it only ate the steps after it
    (production 16:10:53Z: agent_work_queues cut, lesson_usage and
    agent_memory skipped, derek 5.7 s had run)."""
    for flag in ("", "on"):
        monkeypatch.setenv("PAPER_BENCHMARK", flag)
        names = [n for n, _ in PRT.default_steps()]
        i_settle = names.index("settle")
        owing = [n for n in names if PRT.owes_enter(n)]
        assert "derek" in owing
        if flag == "on":
            assert set(owing) == set(PRT.ENTER_OWING_STEPS), owing
        for n in owing:
            assert names.index(n) < i_settle, (n, names)
        # the steps before derek are the book reads and the bounded
        # bookkeeping steps; none of them is the settle step
        before = names[:names.index("derek")]
        assert "settle" not in before and "books" in before
    d = PRT.describe()
    assert d["hard_timeout_s"] == 90.0 and d["record_reserve_s"] == 10.0
    assert d["steps_bound_s"] == 80.0
    assert d["enter_overrun_holdback_s"] == 32.0
    assert d["enter_owing_steps_bound_s"] == 48.0
    assert PRT.STEP_MIN_START_S == 1.0        # not started with < 33 s left
