"""THE DEDICATED MARKET PLANE HOLDS ONE PAGE, NOT THE UNIVERSE (RC5,
2026-10-08: sportsassets-market-plane OOM-killed at 2 GiB from 06:10:01Z,
~14 kills by 12:50Z, after RC4 deployed at 05:16:49Z).

tools/market_plane_memory.py drove the plane's real run loop at production
cardinality (synthetic rows: 146,450 active contracts, 113,431 catalogue
markets, 32,948 PMX-listed, 75,169 Kalshi markets, the subscribe-all stream's
~74k instruments) and named the holders: coverage_pass +1.0-1.5 GB VmHWM,
populate(full) +444 MB, the Kalshi walk +232 MB and its persist +318 MB, the
assignment pass's parsed refdata +165 MB alive between passes, and a
heartbeat detail of 1,401,121 characters (production) every 2 s.

These tests pin, for each holder:
  (1) PARITY -- the RC4 implementation (tests/market_plane_rc4_reference.py,
      verbatim) and the paged one return the same output and write the same
      rows, on inputs that drive every branch, across page boundaries;
  (2) THE BOUND -- the paged pass's traced peak does not grow with the
      universe (O(page)), where RC4's grew with it (O(N));
  (3) THE READERS -- the heartbeat keeps every field its readers read, the
      shard plan carries counts, and the plane reports its own RSS by step.
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import gc
import importlib.util
import json
import os
import pathlib
import time
import tracemalloc

import pytest

from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import registry as R
from sportsassets.market_plane import rules as RULES
from sportsassets.market_plane import settlement as S
from sportsassets.workers import universal_market_plane as W

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "market_plane_rc4_reference", HERE / "market_plane_rc4_reference.py")
REF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REF)

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
NOW = 1_800_000_000.0
EXT = "SYNTHETIC_EXTERNAL_PROVIDER_CODE"
UTC = _dt.timezone.utc


def run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════
# a recording connection for coverage_pass (every read it makes, by SQL)
# ═════════════════════════════════════════════════════════════════════

class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def start(self):
        self.conn.depth += 1
        self.conn.log.append(("BEGIN", self.conn.depth))

    async def commit(self):
        self.conn.log.append(("COMMIT", self.conn.depth))
        self.conn.depth -= 1

    async def rollback(self):
        self.conn.log.append(("ROLLBACK", self.conn.depth))
        self.conn.depth -= 1

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, et, e, tb):
        if et is None:
            await self.commit()
        else:
            await self.rollback()
        return False


class CovConn:
    """The tables coverage_pass reads, in memory. Rows are produced fresh
    per read (as asyncpg would), so a pass that keeps them pays for them."""

    def __init__(self, contracts, *, vals=None, cands=None, rest=None,
                 priced=None, rules=None, fail=(), keep_writes=True):
        self.keep_writes = keep_writes
        self.contracts = contracts
        self.vals, self.cands = vals or {}, cands or {}
        self.rest, self.priced = rest or {}, priced or {}
        self.rules = rules or {}
        self.fail = set(fail)
        self.writes = {"coverage": [], "settlement": []}
        self.log, self.depth, self.reads = [], 0, []

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self):
        return _Tx(self)

    def _order(self):
        return sorted((c for c in self.contracts.values() if c["active"]),
                      key=lambda c: (c["priority"], c["contract_id"]))

    @staticmethod
    def _row(c):
        return {k: c.get(k) for k in (
            "contract_id", "venue", "sport", "competition", "event_id",
            "family", "period", "ontology", "coverage_state", "coverage_why",
            "priority", "settlement_state", "settlement_why",
            "settlement_basis")} | {"settlement_rules_sha":
                                    c.get("settlement_rules_sha")}

    def _maybe_fail(self, kind, ids):
        self.reads.append((kind, len(ids)))
        if (kind, ids[0] if ids else None) in self.fail:
            raise RuntimeError("synthetic read failure: %s" % kind)

    async def fetch(self, sql, *args):
        lim = None
        if " LIMIT " in sql and "ORDER BY priority" in sql:
            lim = int(sql.rsplit(" LIMIT ", 1)[1])
        if "ORDER BY priority, contract_id" in sql and "ontology" in sql:
            rows = [self._row(c) for c in self._order()]          # RC4
            return rows[:lim] if lim else rows
        if sql.strip().startswith("SELECT contract_id FROM "
                                  "market_plane_registry"):
            rows = [{"contract_id": c["contract_id"]} for c in self._order()]
            return rows[:lim] if lim else rows
        ids = list(args[-1])
        if "settlement_rules_sha" in sql:          # a page, any order
            return [self._row(self.contracts[i]) for i in reversed(ids)
                    if i in self.contracts]
        for kind, table, src in (
                ("val", "external_valuations", self.vals),
                ("cand", "ext_candidate_outcomes", self.cands),
                ("rest", "paper_book_observations", self.rest),
                ("priced", "paper_decisions", self.priced)):
            if "FROM %s" % table in sql:
                self._maybe_fail(kind, ids)
                return [dict(src[i], slug=i) for i in ids if i in src]
        if "rules_published, rules_field" in sql:
            self._maybe_fail("rules", ids)
            return [{k: v for k, v in self.rules[i].items()
                     if k != "rules_text"} | {"contract_id": i}
                    for i in ids if i in self.rules]
        if "SELECT contract_id, rules_text FROM market_plane_rules" in sql:
            self._maybe_fail("text", ids)
            return [{"contract_id": i,
                     "rules_text": self.rules[i].get("rules_text")}
                    for i in ids if i in self.rules]
        raise AssertionError("unexpected read: %s" % sql[:80])

    async def fetchval(self, sql, *args):
        assert "market_plane_rules" in sql
        return 1

    async def executemany(self, sql, rows):
        kind = "settlement" if "settlement_state" in sql else "coverage"
        assert self.depth > 0, "a coverage write outside a transaction"
        if self.keep_writes:
            self.writes[kind].extend(tuple(r) for r in rows)


NFL_DESC = ("This market will settle to the winner of the {a} vs {b} NFL "
            "game scheduled for Oct 12, 2026. Overtime counts. If the game "
            "is postponed and not played within 48 hours, or cancelled, the "
            "market resolves to the last fair price. Outcome sourced from "
            "NFL.")
NBA_DESC = ("This market will settle to the winner of the {a} vs {b} NBA "
            "game. Overtime counts. If the game is not played the market "
            "resolves 50-50. Outcome sourced from NBA.")


def universe(n: int, *, seed_states: bool = True):
    """`n` contracts driving every branch of classify / state_for: mapped
    and gapped ontologies, h2h winners with shared and unique rules texts,
    CONFLICT / unpublished / Kalshi rules, attested and refused valuations,
    a priced settlement difference, an EXTERNAL-class candidate, REST books,
    stream-fresh symbols, priority tiers, prior states equal and different.
    SYNTHETIC."""
    from sportsassets import bettor_settlement_difference_policy as SDP
    cs, vals, cands, rest, priced, rules = {}, {}, {}, {}, {}, {}
    fresh = set()
    obs = _dt.datetime.fromtimestamp(NOW - 30, UTC)
    for i in range(n):
        k = i % 23
        kalshi = k == 22
        cid = ("kalshi:KXSYN-%05d" % i) if kalshi else "aec-syn-%05d" % i
        sport, fam = [("football", "WINNER"), ("basketball", "WINNER"),
                      ("hockey", "MARGIN"), ("soccer", "WINNER"),
                      ("tennis", "WINNER")][i % 5]
        gaps = ["METRIC_NOT_NORMALIZED"] if k == 3 else []
        cs[cid] = {
            "contract_id": cid, "venue": "KALSHI" if kalshi else
            "POLYMARKET_US", "sport": None if kalshi else sport,
            "competition": ["nfl", "nba", "nhl", "epl", "atp"][i % 5],
            "event_id": None if k == 4 else "ev-%d" % (i // 7),
            "family": None if kalshi else fam,
            "period": "FULL_EVENT" if k != 5 else "FIRST_HALF",
            "ontology": json.dumps({"gaps": (["KALSHI_ONTOLOGY_NOT_MAPPED"]
                                             if kalshi else gaps),
                                    "question": "synthetic %d " % i * 8}),
            "priority": [0, 10, 20, 50, 80][i % 5],
            "active": i % 41 != 40,
            "coverage_state": None, "coverage_why": None,
            "settlement_state": None, "settlement_why": None,
            "settlement_basis": None, "settlement_rules_sha": None}
        if i % 6 == 0:
            fresh.add(cid)
        if i % 9 == 1:
            rest[cid] = {"observed_at": obs}
        if k in (6, 7, 8, 9):
            vals[cid] = {"has_probability": k != 9,
                         "settlement_verdict": {6: "COMPATIBLE",
                                                7: "INCOMPATIBLE",
                                                8: None, 9: None}[k],
                         "decision_rules_fingerprint": ("fp-old" if i % 2
                                                        else None),
                         "refusals": (["SETTLEMENT_TERMS_UNPROVEN"]
                                      if k == 8 else ["PRICE_STALE"]),
                         "record_purpose": "ENTRY_DECISION",
                         "decided_at": obs}
        if k == 7:
            priced[cid] = {"eligibility": json.dumps({"eligible": True}),
                           "policy": json.dumps({
                               "policy_id": SDP.POLICY_ID,
                               "version": SDP.VERSION, "p": 0.51,
                               "q_hi": 0.6}),
                           "decided_at": obs}
        if k in (10, 11):
            cands[cid] = {"first_refusal": EXT if k == 10 else "OTHER_CODE",
                          "stage": "MODEL"}
        if k == 12:
            continue                                   # no rules captured
        venue = "KALSHI" if kalshi else "POLYMARKET_US"
        if k == 13:
            text = None                                # venue publishes none
        elif sport in ("football", "basketball"):
            # shared texts (one per event, 7 contracts each) and unique ones
            d = NFL_DESC if sport == "football" else NBA_DESC
            text = d.format(a="Alpha %d" % (i // (7 if i % 3 else 1)),
                            b="Beta")
        else:
            text = "Synthetic %s rules %d." % (sport, i // 11)
        sha = RULES.SRR.fingerprint(text) if text else None
        rules[cid] = {"venue": venue, "rules_published": text is not None,
                      "rules_field": "description" if text else None,
                      "rules_sha256": sha,
                      "parse_status": "CONFLICT" if k == 14 else
                      ("ESTABLISHED" if text else "ABSENT"),
                      "evidence": json.dumps({
                          "matched": ["OVERTIME_INCLUDED"] if text else [],
                          "special_conditions": (["TIE_SCALAR_0_50"]
                                                 if k == 15 else []),
                          "verification_sources": ["NFL"] if text else [],
                          "settlement": {"draw_rule": "SCALAR_0_50"}
                          if k == 15 else {},
                          "conflicts": {"void_rule": ["A", "B"]}
                          if k == 14 else {}}),
                      "parser_version": RULES.SRR.PARSER_VERSION,
                      "source": RULES.SOURCE_PMUS_BOARD,
                      "rules_text": text}
    if seed_states:
        # a prior pass's states: some equal to this pass's, some not
        for j, c in enumerate(cs.values()):
            if j % 4 == 0:
                c["coverage_state"] = "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
                c["settlement_state"] = S.NOT_PROVEN
            elif j % 4 == 1:
                c["settlement_state"] = S.COMPATIBLE
                c["settlement_basis"] = S.BASIS_DECISION_ATTEST
    return cs, dict(vals=vals, cands=cands, rest=rest, priced=priced,
                    rules=rules), fresh


async def _cov(fn, cs, ev, fresh, **kw):
    conn = CovConn(copy.deepcopy(cs), **copy.deepcopy(ev),
                   fail=kw.pop("fail", ()))
    out = await fn(conn, fresh_symbols=fresh, now=NOW, **kw)
    return out, conn


@pytest.fixture
def ext_codes(monkeypatch):
    monkeypatch.setattr(POP, "external_codes", lambda: {EXT})
    monkeypatch.setattr(REF, "external_codes", lambda: {EXT})


@pytest.mark.parametrize("page", [1, 7, 64, 5000])
def test_coverage_pass_paged_is_the_single_pass_result(monkeypatch, page,
                                                       ext_codes):
    cs, ev, fresh = universe(460)
    # a terms comparison cached by an EARLIER pass (text never re-read) and
    # keys this pass loads that later pages find in the cache
    S._TERMS_CACHE.clear()
    run(_cov(REF.coverage_pass, cs, ev, fresh))
    warm = {k: v for j, (k, v) in enumerate(S._TERMS_CACHE.items())
            if j % 2 == 0}
    S._TERMS_CACHE.clear()
    S._TERMS_CACHE.update(warm)
    old, oc = run(_cov(REF.coverage_pass, cs, ev, fresh))
    S._TERMS_CACHE.clear()
    S._TERMS_CACHE.update(warm)
    monkeypatch.setattr(POP, "COVERAGE_PAGE", page, raising=False)
    new, nc = run(_cov(POP.coverage_pass, cs, ev, fresh))
    assert old["settlement"]["terms_text_loaded"] > 0
    assert json.dumps(new, default=str) == json.dumps(old, default=str)
    assert nc.writes == oc.writes                   # row for row, in order
    assert nc.writes["coverage"] and nc.writes["settlement"]
    # one write transaction per pass, opened only when there are changes
    assert [x[0] for x in nc.log if x[1] == 1] == ["BEGIN", "COMMIT"]
    S._TERMS_CACHE.clear()


def test_coverage_pass_limit_and_unchanged_states(monkeypatch, ext_codes):
    cs, ev, fresh = universe(200, seed_states=False)
    S._TERMS_CACHE.clear()
    old, oc = run(_cov(REF.coverage_pass, cs, ev, fresh, limit=77))
    S._TERMS_CACHE.clear()
    monkeypatch.setattr(POP, "COVERAGE_PAGE", 10, raising=False)
    new, nc = run(_cov(POP.coverage_pass, cs, ev, fresh, limit=77))
    assert new == old and nc.writes == oc.writes and new["active"] == 77
    # a second pass over the written states changes nothing and opens no
    # transaction (as RC4)
    for cid, st, why, at in nc.writes["coverage"]:
        cs[cid].update(coverage_state=st, coverage_why=why)
    for cid, st, why, basis, evj, at in nc.writes["settlement"]:
        cs[cid].update(settlement_state=st, settlement_why=why,
                       settlement_basis=basis,
                       settlement_rules_sha=json.loads(evj).get(
                           "rules_sha256"))
    again, ac = run(_cov(POP.coverage_pass, cs, ev, fresh, limit=77))
    assert again["changed"] == 0 and again["settlement"]["changed"] == 0
    assert ac.log == [] and ac.writes == {"coverage": [], "settlement": []}
    S._TERMS_CACHE.clear()


def test_a_failed_evidence_read_drops_that_chunk_in_both(monkeypatch,
                                                         ext_codes):
    cs, ev, fresh = universe(120)
    first = sorted((c for c in cs.values() if c["active"]),
                   key=lambda c: (c["priority"], c["contract_id"]))[0]
    fail = {("priced", first["contract_id"]), ("val", first["contract_id"])}
    S._TERMS_CACHE.clear()
    old, oc = run(_cov(REF.coverage_pass, cs, ev, fresh, fail=fail))
    S._TERMS_CACHE.clear()
    # one page: the failing chunk is the same chunk in both
    monkeypatch.setattr(POP, "COVERAGE_PAGE", 5000, raising=False)
    new, nc = run(_cov(POP.coverage_pass, cs, ev, fresh, fail=fail))
    assert new == old and nc.writes == oc.writes
    S._TERMS_CACHE.clear()


def test_a_pass_that_raises_writes_nothing(monkeypatch, ext_codes):
    cs, ev, fresh = universe(60)
    monkeypatch.setattr(POP, "COVERAGE_PAGE", 10, raising=False)
    real = S.state_for
    seen = {"n": 0}

    def boom(*a, **k):
        seen["n"] += 1
        if seen["n"] == 45:                     # page 5, after 4 flushes
            raise RuntimeError("synthetic")
        return real(*a, **k)
    monkeypatch.setattr(S, "state_for", boom)
    conn = CovConn(copy.deepcopy(cs), **copy.deepcopy(ev))
    with pytest.raises(RuntimeError):
        run(POP.coverage_pass(conn, fresh_symbols=fresh, now=NOW))
    assert conn.writes["coverage"]              # flushed into the txn ...
    assert conn.log[0] == ("BEGIN", 1) and conn.log[-1] == ("ROLLBACK", 1)
    assert ("COMMIT", 1) not in conn.log                # ... rolled back


def _peak(coro_fn):
    gc.collect()
    tracemalloc.start()
    try:
        run(coro_fn())
        return tracemalloc.get_traced_memory()[1] / 1048576.0
    finally:
        tracemalloc.stop()


def test_coverage_pass_peak_does_not_grow_with_the_universe(monkeypatch,
                                                            ext_codes):
    """O(page), not O(universe): 4x the contracts, about the same traced
    peak. RC4 held every row, rules row and classified result to the end
    of the pass (VmHWM +1,081-1,531 MB at 146,450 contracts)."""
    monkeypatch.setattr(POP, "COVERAGE_PAGE", 500, raising=False)
    S._TERMS_CACHE.clear()
    sizes = {}
    for n in (1500, 6000):
        cs, ev, fresh = universe(n, seed_states=False)
        # the written rows are the table's, not the pass's: dropped here
        conn = CovConn(cs, **ev, keep_writes=False)
        # a steady-state pass: the process's terms-comparison cache (a
        # bounded process cache, S._TERMS_CACHE_MAX) already warm, as on
        # every pass after the first
        run(POP.coverage_pass(conn, fresh_symbols=fresh, now=NOW))
        sizes[n] = _peak(lambda conn=conn, fresh=fresh: POP.coverage_pass(
            conn, fresh_symbols=fresh, now=NOW))
        S._TERMS_CACHE.clear()
        del cs, ev, conn
    # the inputs themselves (the fake tables) are allocated before tracing
    assert sizes[6000] < 1.25 * sizes[1500] + 1.0, sizes


# ═════════════════════════════════════════════════════════════════════
# the Kalshi walk keeps the persisted fields; the persist is paged
# ═════════════════════════════════════════════════════════════════════

def _kalshi_market(j: int) -> dict:
    """A Kalshi GET /markets object with production's field set
    (SYNTHETIC values; tools/market_plane_memory.kalshi_market)."""
    from tools import market_plane_memory as H
    m = H.kalshi_market(j, "KXSYN%03dGAME" % (j % 5))
    m["settlement_value_dollars"] = "1.0000" if j % 2 else None
    m["settlement_ts"] = "2026-10-12T23:00:00Z"
    return m


def _series(j):
    return {"ticker": "KXSYN%03dGAME" % (j % 5), "title": "S", "tags": ["x"],
            "category": "Sports"}


def test_every_field_the_kalshi_persisters_do_not_keep_is_unread():
    """Every output of the persisters is a function of KALSHI_PERSIST_KEYS
    and _series: rewriting every OTHER field of a full venue object changes
    nothing, and the slim object gives the full object's outputs."""
    from sportsassets import kalshi_catalogue as KC
    for j in range(12):
        full = dict(_kalshi_market(j), _series=_series(j))
        slim = POP.kalshi_slim(_kalshi_market(j), _series(j))
        assert set(slim) <= set(POP.KALSHI_PERSIST_KEYS) | {"_series"}
        noise = {k: ("CHANGED", 7, [1], {"x": 1})[n % 4]
                 for n, k in enumerate(full)
                 if k not in POP.KALSHI_PERSIST_KEYS and k != "_series"}
        assert len(noise) > 40                    # production's ~60 fields
        mutated = dict(full, **noise)
        for m in (slim, mutated):
            assert POP.kalshi_contract_row(m, now=NOW) == \
                POP.kalshi_contract_row(full, now=NOW)
            assert RULES.kalshi_row(m) == RULES.kalshi_row(full)
        res = {"markets": [full] * 3}
        assert KC.summary({"markets": [slim] * 3}) == KC.summary(res)


def test_the_plane_walk_keeps_only_the_persisted_fields(monkeypatch):
    from sportsassets import kalshi_catalogue as KC
    from tools import market_plane_memory as H
    tx = H.FakeKalshiGet(240, 4)
    got = {}

    def walk(**kw):
        got["kw"] = kw
        return KC.__dict__["_real_walk"](tx, sleep=lambda s: None, **kw)
    monkeypatch.setitem(KC.__dict__, "_real_walk", KC.walk)
    monkeypatch.setattr(KC, "walk", walk)
    res = W.kalshi_walk()
    assert got["kw"] == {"project": POP.kalshi_slim}
    assert res["complete"] and len(res["markets"]) == 240
    for m in res["markets"]:
        assert set(m) <= set(POP.KALSHI_PERSIST_KEYS) | {"_series"}
        assert m["rules_primary"] and m["_series"]["ticker"]
    # unprojected, the venue objects are whole (other consumers unchanged)
    whole = KC.__dict__["_real_walk"](H.FakeKalshiGet(10, 2),
                                      sleep=lambda s: None)
    assert len(whole["markets"][0]) > 40


class KalshiConn:
    """registry + rules + events for populate_kalshi / rules.upsert."""

    def __init__(self):
        self.reg, self.rules, self.events = {}, {}, {}
        self.depth, self.log = 0, []

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self):
        return _Tx(self)

    async def fetch(self, sql, *args):
        if "FROM market_plane_registry" in sql:
            return [{"contract_id": k, "content_sha": v[16]}
                    for k, v in self.reg.items()]
        if "FROM market_plane_rules" in sql:
            return [{"contract_id": k, "rules_sha256": v[4],
                     "parser_version": v[9], "rules_published": v[2]}
                    for k, v in self.rules.items() if k in set(args[0])]
        raise AssertionError(sql[:60])

    async def executemany(self, sql, rows):
        assert self.depth > 0
        for r in rows:
            if "INSERT INTO market_plane_registry" in sql:
                self.reg[r[0]] = r
            elif "INSERT INTO market_plane_rules" in sql:
                was = self.rules.get(r[0])
                if was is None or (was[4], was[9], was[2]) != (r[4], r[9],
                                                               r[2]):
                    self.rules[r[0]] = r
            elif "INSERT INTO market_plane_events" in sql:
                self.events.setdefault(r[0], r)
            self.log.append(sql.split()[2])


def _walk_result(n):
    """What the plane's walk hands the persister (kalshi_slim objects; the
    whole venue objects where there is no projection, as RC4 walked)."""
    slim = getattr(POP, "kalshi_slim", None) or (
        lambda m, ser: dict(m, _series=ser))
    return {"complete": True, "stopped": None,
            "markets": [slim(_kalshi_market(j), _series(j))
                        for j in range(n)]}


@pytest.mark.parametrize("page", [1, 3, 1000])
def test_populate_kalshi_paged_writes_the_single_batch_rows(monkeypatch,
                                                            page):
    res = _walk_result(25)
    full = {"complete": True, "stopped": None, "markets": [
        dict(_kalshi_market(j), _series=_series(j)) for j in range(25)]}
    RULES._SEEN.clear()
    a = KalshiConn()
    old = run(REF.populate_kalshi(a, full, now=NOW))
    RULES._SEEN.clear()
    b = KalshiConn()
    monkeypatch.setattr(POP, "KALSHI_PAGE", page, raising=False)
    new = run(POP.populate_kalshi(b, res, now=NOW))
    assert new == old
    assert b.reg == a.reg and b.rules == a.rules and b.events == a.events
    # a second walk: nothing changed, nothing evented, rules cached
    old2 = run(REF.populate_kalshi(a, full, now=NOW + 60))
    new2 = run(POP.populate_kalshi(b, res, now=NOW + 60))
    assert new2 == old2 and new2["changed"] == 0
    RULES._SEEN.clear()


def test_populate_kalshi_peak_does_not_grow_with_the_walk(monkeypatch):
    monkeypatch.setattr(POP, "KALSHI_PAGE", 200, raising=False)
    sizes = {}
    for n in (1000, 4000):
        res = _walk_result(n)
        RULES._SEEN.clear()
        conn = KalshiConn()
        # the persisted rows themselves are the fake table's, not the
        # pass's: measure the pass against a table that drops them
        conn.executemany = _drop_writes(conn)
        sizes[n] = _peak(lambda conn=conn, res=res: POP.populate_kalshi(
            conn, res, now=NOW))
        del res, conn
    RULES._SEEN.clear()
    assert sizes[4000] < 1.5 * sizes[1000] + 3.0, sizes


def _drop_writes(conn):
    async def em(sql, rows):
        assert conn.depth > 0
    return em


# ═════════════════════════════════════════════════════════════════════
# the heartbeat: bounded, every reader field kept; the plan by counts
# ═════════════════════════════════════════════════════════════════════

def _plan_at_production_size():
    from sportsassets.market_plane import sharding as SH
    syms = ["aec-syn-%06d-production-width-slug" % i for i in range(32_937)]
    plan = dict(SH.assign_stable(syms, {}, max_per_stream=100_000,
                                 max_streams=1, order=syms))
    return plan


class _AssignConn:
    def __init__(self, n):
        self.rows = [{"contract_id": "aec-syn-%06d-production-width-slug"
                      % i, "subscription_shard": 0} for i in range(n)]
        self.writes = []

    async def fetch(self, sql, *a):
        return self.rows

    async def executemany(self, sql, rows):
        self.writes.append(len(rows))

    async def execute(self, sql, *a):
        return "UPDATE 0"


def test_the_shard_plan_carries_counts_never_its_member_list():
    plan = run(R.assign_missing_shards(_AssignConn(32_937),
                                       max_per_stream=100_000, max_streams=1))
    assert plan["shards"] == [{"shard": 0, "count": 32_937}]
    assert plan["subscribable"] == 32_937 and plan["overflow_count"] == 0
    assert plan["assignments"] is None
    assert len(json.dumps(plan)) < 2_000          # RC4: ~1.4 MB


def test_stable_assignment_is_rc4s_result():
    import random
    from sportsassets.market_plane import sharding as SH
    rnd = random.Random(11)
    for _ in range(40):
        n = rnd.randrange(0, 60)
        syms = ["s%03d" % rnd.randrange(0, 90) for _ in range(n)] + ["", " "]
        existing = {"s%03d" % rnd.randrange(0, 90): rnd.randrange(-1, 5)
                    for _ in range(rnd.randrange(0, 40))}
        order = rnd.sample(syms, k=len(syms)) if rnd.random() < .7 else None
        kw = dict(max_per_stream=rnd.randrange(1, 12),
                  max_streams=rnd.randrange(1, 5), order=order)
        assert SH.assign_stable(syms, existing, **kw) == \
            REF.assign_stable(syms, existing, **kw)


def test_stable_assignment_is_linear_at_production_size():
    """RC4 checked each existing assignment against the sorted symbol LIST:
    5.85 s of CPU at 32,948 assigned symbols (measured), on every 30 s
    assignment pass, the plane's event loop and stream thread held."""
    from sportsassets.market_plane import sharding as SH
    syms = ["aec-syn-%06d-production-width-slug" % i for i in range(32_948)]
    t0 = time.process_time()
    p = SH.assign_stable(syms, {s: 0 for s in syms}, max_per_stream=100_000,
                         max_streams=1, order=syms)
    assert time.process_time() - t0 < 1.5
    assert p["symbols"] == 32_948 and p["complete"]


class _Mgr:
    def stream_count(self):
        return 1


def _detail(plan, **kw):
    state = {"plan": dict(plan, subscription_mode=W.MODE_SUBSCRIBE_ALL),
             "refdata": {"action": None, "pending_priority": 0},
             "populate": {"read": 6551, "excluded": {"btc": 3}},
             "kalshi": {"enabled": True, "markets": 75_169}}
    return W.heartbeat_detail(
        arming={"armed": True, "why": None}, state=state,
        plan_cfg=W.subscription_plan({}), mgr=_Mgr(),
        sync={"ok": True, "added": 0, "failures": []}, fresh=set(range(10)),
        memory=W.StepMemory(rss=lambda: 500.0).digest(), **kw)


def test_the_heartbeat_detail_is_bounded_at_production_cardinality():
    from sportsassets.db import heartbeat_json
    plan = run(R.assign_missing_shards(_AssignConn(32_937),
                                       max_per_stream=100_000, max_streams=1))
    d = _detail(plan)
    n = len(heartbeat_json(d))
    assert n < 12_000, n                          # RC4: 1,401,121 (prod)
    # a section that grows back is replaced by its size, by name
    big = _detail(_plan_at_production_size())
    assert big["plan"]["omitted"] == W.R_SECTION_OMITTED
    assert big["plan"]["chars"] > W.HEARTBEAT_SECTION_MAX_CHARS
    assert len(heartbeat_json(big)) < 12_000


def test_every_field_the_heartbeat_readers_read_is_kept():
    """completion.read.runtime_block (the Command readback, pm_bind's
    dedicated_market_plane_present and redteam's runtime) reads runtime,
    subscription_mode, market_data_streams and resources; the generic
    readers (/api/health/services, the notification monitor, command
    snapshot) read status / error. The block is the same from the new
    detail as from RC4's."""
    from sportsassets.completion import read as CR
    plan = run(R.assign_missing_shards(_AssignConn(32_937),
                                       max_per_stream=100_000, max_streams=1))
    new = _detail(plan)
    rc4 = dict(new, plan=dict(_plan_at_production_size(),
                              subscription_mode=W.MODE_SUBSCRIBE_ALL))
    rc4.pop("memory")
    for k in ("arming", "subscription_mode", "market_data_streams",
              "runtime", "resources", "sync", "refdata", "populate",
              "kalshi", "fresh"):
        assert k in new, k
    ump = {"status": "ok", "beat_at": NOW - 5}
    a = CR.runtime_block({}, None, dict(ump, detail=new), now=NOW)
    b = CR.runtime_block({}, None, dict(ump, detail=rc4), now=NOW)
    assert a["market_plane"] == b["market_plane"]
    assert a["market_plane"]["subscription_mode"] == W.MODE_SUBSCRIBE_ALL
    assert a["market_plane"]["market_data_streams"] == 1
    m = CR.market_data_block({"subscription": {"plan": plan}}, None, new)
    m4 = CR.market_data_block({"subscription": {"plan": rc4["plan"]}},
                              None, rc4)
    assert m == m4
    assert new["memory"]["by_step"] == {} and "rss_mb" in new["memory"]


def test_the_plane_reports_its_rss_by_step():
    seq = iter([100.0, 140.0, 141.0, 300.0, 150.0, 150.0, 160.0])
    m = W.StepMemory(rss=lambda: next(seq))
    m.begin()
    m.mark("populate_full")
    m.mark("assign")
    m.mark("coverage")
    m.begin()
    m.mark("coverage")
    m.mark("snapshot")
    d = m.digest()
    assert set(d) >= {"rss_mb", "peak_mb", "limit_mb", "by_step"}
    bs = d["by_step"]
    assert bs["populate_full"] == {"rss_mb": 140.0, "delta_mb": 40.0,
                                   "max_delta_mb": 40.0, "passes": 1}
    assert bs["coverage"]["rss_mb"] == 150.0          # the last pass
    assert bs["coverage"]["delta_mb"] == 0.0
    assert bs["coverage"]["max_delta_mb"] == 159.0    # the worst since boot
    assert bs["snapshot"]["delta_mb"] == 10.0
    assert m.log_due(1000.0) and not m.log_due(1000.0 + 1)
    assert m.log_due(1000.0 + W.MEMORY_LOG_EVERY_S)


def test_discovery_counts_and_samples_without_copying():
    from sportsassets.market_plane.sharded_stream import Manager

    class T:
        filtered_updates = 9
        subscription_mode = "S"

        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def subscribe(self, x):
            pass

        def stop(self):
            pass
    m = Manager(token_fn=lambda: "x", max_per_stream=10, subscribe_all=True,
                transport_factory=lambda b, tok, **kw: T(), clock=lambda: 1)
    m.sync({"a": 0}, {})
    seen = m.shards[0]["books"].seen
    for s in ("zz", "b-new", "c-new", "a-new"):
        seen[s] = (1.0, None, None)
    d = m.discovery(limit=3)
    assert d["instruments_seen_outside_books"] == 4
    assert d["sample"] == sorted(seen)[:3] == ["a-new", "b-new", "c-new"]
    assert d["filtered_updates"] == 9
    assert m.discovery()["sample"] == []


# ═════════════════════════════════════════════════════════════════════
# pg: populate, the assignment read, certify -- RC4 and paged, same rows
# ═════════════════════════════════════════════════════════════════════

async def _db():
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    return c, tr


async def _seed_premap(c, n, *, prefix="mpm", leagues=("nfl", "nba", "btc"),
                       age_s=0.0):
    recs = []
    now = _dt.datetime.now(UTC)
    for i in range(n):
        lg = leagues[i % len(leagues)]
        slug = "%s-%s-%05d" % (prefix, lg, i)
        ev = "aec-%s-syn%d-syn%d-2026" % (lg, i // 4, i // 4)
        st = ["football_team_full_game_spread", "futures",
              "basketball_team_full_game_moneyline",
              "weird_magic_market"][i % 4]
        upd = now - _dt.timedelta(seconds=age_s + (i % 17))
        for k, (intent, side) in enumerate((("ORDER_INTENT_BUY_LONG", "a"),
                                            ("ORDER_INTENT_BUY_SHORT", "b"))):
            recs.append(("0x%s-%d-%d" % (prefix, i, k), ev, "Title %d" % i,
                         slug, "Question %d?" % i, "side", str(i % 9 + .5),
                         side, intent, upd, 1000 + 2 * i + k, lg,
                         now + _dt.timedelta(hours=1 + i % 70), st,
                         ("PREGAME", "LIVE", "ENDED")[i % 3],
                         "VENUE_LIVE_FLAG"))
    await c.copy_records_to_table(
        "us_premap", records=recs, columns=[
            "identifier", "event_slug", "event_title", "market_slug",
            "question", "kind", "line", "side_norm", "intent", "updated_at",
            "team_id", "team_league", "game_start", "sports_type",
            "listing_state", "listing_state_source"])


async def _registry_state(c, like):
    rows = await c.fetch(
        "SELECT contract_id, venue, sport, competition, event_id, "
        " market_type, ontology::text AS ontology, active, "
        " desired_subscription, priority, required_reason, family, period, "
        " extract(epoch FROM event_start) AS event_start, "
        " extract(epoch FROM last_seen_at) AS last_seen_at, content_sha, "
        " extract(epoch FROM updated_at) AS updated_at, coverage_state, "
        " coverage_why, settlement_state, settlement_why, settlement_basis, "
        " settlement_evidence::text AS se "
        " FROM market_plane_registry WHERE contract_id LIKE $1 "
        " ORDER BY contract_id", like)
    ev = await c.fetch(
        "SELECT event_key, contract_id, kind, payload::text AS p "
        "  FROM market_plane_events WHERE contract_id LIKE $1 "
        " ORDER BY event_key", like)
    return [dict(r) for r in rows], [dict(r) for r in ev]


async def _candidates(c, slugs):
    at = _dt.datetime.now(UTC)
    for q, s in enumerate(slugs):
        await c.execute(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
            " sport_key, queue_position, outcome, us_market_slug) VALUES "
            " ('mpm-cycle', $1, 'mpm', $2, 'ADMITTED', $3)", at, q, s)


@pg
@pytest.mark.parametrize("page", [3, 1000])
def test_populate_paged_writes_the_single_batch_rows(monkeypatch, page):
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await _seed_premap(c, 37)
            # an evaluated candidate the catalogue lists, one it does not
            await _candidates(c, ["mpm-nfl-00000", "mpm-gone-required"])
            now = time.time()
            got = {}
            for name, fn in (("rc4", REF.populate), ("new", POP.populate)):
                sp = c.transaction()
                await sp.start()
                monkeypatch.setattr(POP, "POPULATE_PAGE", page, raising=False)
                outs = [await fn(c, since=0.0, now=now, full=True)]
                # an incremental pass after the catalogue changed
                await c.execute(
                    "UPDATE us_premap SET question = question || ' (edit)', "
                    " updated_at = now() WHERE market_slug IN "
                    " ('mpm-nfl-00003', 'mpm-nba-00004')")
                outs.append(await fn(c, since=now - 1.0, now=now + 5,
                                     full=False))
                outs.append(await fn(c, since=0.0, now=now + 10, full=True))
                got[name] = (outs, await _registry_state(c, "mpm-%"))
                await sp.rollback()
            assert got["new"][0] == got["rc4"][0]          # outputs
            assert got["new"][1] == got["rc4"][1]          # rows + events
            outs = got["new"][0]
            assert outs[0]["read"] == 37 and outs[0]["excluded"] == \
                {"btc": 12}
            # the required market the catalogue does not list is added
            # (the shared test database may hold other proofs' held and
            # candidate markets too: counted, never assumed absent)
            assert outs[0]["required_added"] >= 1
            rows = {r["contract_id"]: r for r in got["new"][1][0]}
            assert rows["mpm-nfl-00000"]["required_reason"] == \
                "EVALUATED_CANDIDATE"
            assert "NOT_IN_CURRENT_CATALOGUE" in \
                rows["mpm-gone-required"]["ontology"]
            assert outs[1]["read"] >= 2 and outs[1]["changed"] == 2
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_populate_peak_does_not_grow_with_the_catalogue(monkeypatch):
    """RC4 read the whole catalogue, the registry's whole sha map and every
    upsert tuple before writing (+444 MB on a full pass at production
    size); paged, 4x the catalogue costs about the same traced peak."""
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            monkeypatch.setattr(POP, "POPULATE_PAGE", 250, raising=False)
            sizes = {}
            for n in (1000, 4000):
                await c.execute("DELETE FROM us_premap")
                await _seed_premap(c, n, prefix="mpb%d" % n, leagues=("nfl",))
                sp = c.transaction()
                await sp.start()
                gc.collect()
                tracemalloc.start()
                await POP.populate(c, since=0.0, now=time.time(), full=True)
                sizes[n] = tracemalloc.get_traced_memory()[1] / 1048576.0
                tracemalloc.stop()
                await sp.rollback()
            assert sizes[4000] < 1.5 * sizes[1000] + 2.0, sizes
        finally:
            await tr.rollback()
            await c.close()
    run(go())


REFDATA_SHAPES = [
    {"symbol": "s", "priceScale": "1000", "fractionalQtyScale": "100",
     "state": "INSTRUMENT_STATE_OPEN", "productId": "p",
     "metadata": {"instrument_rules": "x" * 900}},
    {"priceScale": 100, "fractionalQtyScale": 1, "state": 3},
    {"priceScale": " 100 ", "fractionalQtyScale": "10", "productId": None},
    {"priceScale": None, "fractionalQtyScale": "10", "state": {"a": 1}},
    {"fractionalQtyScale": "10"},
    {},
    {"priceScale": "0", "fractionalQtyScale": "-1", "state": "OPEN"},
    {"priceScale": 100.0, "fractionalQtyScale": True, "state": ["x"]},
    {"price_scale": 1000, "qty_scale": 100},
    [1, 2, 3],
    "not-an-object",
    None,                                     # JSON null, not SQL NULL
]


async def _seed_assigned(c):
    for i, rd in enumerate(REFDATA_SHAPES):
        await c.execute(
            "INSERT INTO market_plane_registry (contract_id, venue, active, "
            " desired_subscription, updated_at, priority, refdata, "
            " subscription_shard) VALUES ($1, 'POLYMARKET_US', true, true, "
            " now(), 80, $2::jsonb, 0)", "mpa-%02d" % i, json.dumps(rd))


@pg
def test_the_assignment_read_gives_the_books_what_rc4_gave_them():
    from sportsassets import institutional_stream as IS
    from sportsassets.market_plane.sharded_stream import Manager

    class T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def subscribe(self, x):
            pass

        def stop(self):
            pass

    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await _seed_assigned(c)
            got = {}
            for name, fn in (("rc4", REF.run_loop_assignment_sync),
                             ("new", W.sync_books)):
                m = Manager(token_fn=lambda: "x", max_per_stream=1000,
                            subscribe_all=True, clock=lambda: 5.0,
                            transport_factory=lambda b, tok, **kw: T())
                out = fn(c, m)
                out = await out
                b = m.shards[0]["books"]
                got[name] = (out, {k: {x: y for x, y in v.items()
                                       if x != "at"}
                                   for k, v in b._instruments.items()},
                             sorted(b.wanted()),
                             {s: b.current(s, now=6.0)["refusal"]
                              for s in b.wanted()})
            assert got["new"] == got["rc4"]
            inst = got["new"][1]
            assert inst["mpa-00"] == {"price_scale": 1000, "qty_scale": 100,
                                      "refdata_state":
                                          "INSTRUMENT_STATE_OPEN",
                                      "product_id": "p"}
            assert "mpa-11" not in inst          # JSON null: never set
            assert len(got["new"][2]) == len(REFDATA_SHAPES)
            # the slim read carries the four fields, never the record
            rows = await R.assigned_instruments(c)
            for r in rows:
                v = json.loads(r["refdata"])
                assert not isinstance(v, dict) or set(v) == set(
                    R.INSTRUMENT_KEYS)
            assert IS.R_NO_SCALE in got["new"][3].values()
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_certify_reads_the_scales_only_and_certifies_as_rc4():
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            for i, rd in enumerate(REFDATA_SHAPES):
                if not isinstance(rd, dict):
                    continue               # RC4's certify reads objects
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " active, desired_subscription, updated_at, priority, "
                    " refdata, subscription_shard) VALUES ($1, "
                    " 'POLYMARKET_US', true, true, now(), 80, $2::jsonb, 0)",
                    "mpc-%02d" % i, json.dumps(rd))
            got = {}
            for name, fn in (("rc4", REF.certify), ("new", W.certify)):
                sp = c.transaction()
                await sp.start()
                out = await fn(c, None)
                rows = [dict(r) for r in await c.fetch(
                    "SELECT contract_id, fingerprint, status, comparable, "
                    " agreeing, agreement, detail::text AS d FROM "
                    " market_plane_certification WHERE contract_id LIKE "
                    " 'mpc-%' ORDER BY 1, 2")]
                got[name] = (out, rows)
                await sp.rollback()
            assert got["new"] == got["rc4"] and got["new"][0]["evaluated"] \
                == sum(isinstance(x, dict) for x in REFDATA_SHAPES)
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_coverage_pass_on_postgres_writes_what_rc4_wrote(monkeypatch):
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await _seed_premap(c, 23, prefix="mpv")
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            await RULES.upsert(c, [RULES.pmus_row({
                "slug": "mpv-nfl-%05d" % i, "sportsMarketType":
                    "football_team_full_game_winner",
                "description": NFL_DESC.format(a="A%d" % (i // 2), b="B")})
                for i in range(0, 23, 3)] + [RULES.pmus_row(
                    {"slug": "mpv-nba-00001"})], now=now)
            got = {}
            for name, fn in (("rc4", REF.coverage_pass),
                             ("new", POP.coverage_pass)):
                S._TERMS_CACHE.clear()
                sp = c.transaction()
                await sp.start()
                monkeypatch.setattr(POP, "COVERAGE_PAGE", 4, raising=False)
                out = await fn(c, fresh_symbols={"mpv-nfl-00000"}, now=now)
                got[name] = (out, await _registry_state(c, "mpv-%"))
                await sp.rollback()
            assert got["new"] == got["rc4"]
            assert got["new"][0]["changed"] >= 15
        finally:
            S._TERMS_CACHE.clear()
            await tr.rollback()
            await c.close()
    run(go())


# ═════════════════════════════════════════════════════════════════════
# the run loop itself: the heartbeat carries memory by step
# ═════════════════════════════════════════════════════════════════════

@pg
def test_the_run_loop_heartbeat_carries_memory_by_step(monkeypatch):
    """The REAL run loop (streams not armed: no credential here) for two
    passes on a rolled-back connection: every step that ran is in
    heartbeat.memory.by_step, and the detail is bounded."""
    from sportsassets.db import heartbeat_json

    async def go():
        c, tr = await _db()

        # a pool of ONE connection: acquire waits for its release, as a
        # real pool never hands one connection to two users at once
        # (RC6 D1: the plane's freshness task runs beside the pass)
        one = asyncio.Lock()

        class Pool:
            def acquire(self):
                class A:
                    async def __aenter__(self_):
                        await one.acquire()
                        return c

                    async def __aexit__(self_, *a):
                        one.release()
                        return False
                return A()

        async def get_pool():
            return Pool()
        beats = []

        async def hb(service, status="ok", detail=None, con=None):
            beats.append((service, status, detail))
            if len(beats) >= 2:
                raise asyncio.CancelledError()
        monkeypatch.setattr(W, "get_pool", get_pool)
        monkeypatch.setattr(W, "heartbeat", hb)
        monkeypatch.setattr(W, "INTERVAL_S", 0.0)
        monkeypatch.setenv("INSTITUTIONAL_MD_STREAM", "off")
        monkeypatch.setenv("KALSHI_CATALOGUE", "off")
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await _seed_premap(c, 9, prefix="mpr")
            with pytest.raises(asyncio.CancelledError):
                await W.run()
        finally:
            await tr.rollback()
            await c.close()
        (s1, st1, d1), (s2, st2, d2) = beats
        assert s1 == s2 == W.SERVICE and st1 != "error", d1
        by = d2["memory"]["by_step"]
        assert {"populate_full", "coverage", "snapshot"} <= set(by), by
        assert by["coverage"]["passes"] >= 1
        assert len(heartbeat_json(d2)) < 12_000
        assert d2["runtime"] and "resources" in d2
    run(go())
