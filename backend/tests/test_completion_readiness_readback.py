"""THE COMPLETION READINESS READBACK (GET /api/command/completion-readiness).

  §1  pure sections: runtime (process age, RSS high-water against the
      workers' own cgroup limit, the plane's state from its heartbeat),
      market data (numerator / denominator, never a rate without both),
      PMUS venue confirmation (venue-confirmed, or OWNER_CREDENTIAL_REQUIRED
      naming the Ed25519 retail key -- never a workaround)
  §2  the verdict: PAPER_SHADOW_ONLY with named blockers; a governor saying
      CASH alone keeps it there; unmeasured headroom fails closed; nothing is
      granted and nothing auto-activates
  §3  owner blockers carry the exact action, and the dedicated service's is
      the four allowed names only
  §4  against Postgres: the whole read runs inside a READ ONLY transaction,
      returns every section, and changes no row
  §5  the route: GET only, COMMAND auth, no-store, registered in the app;
      the modules reach no order or venue-write path
"""
from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path

import pytest

from sportsassets.completion import read as CR

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1] / "sportsassets"
NOW = 1_800_000_000.0


# ── §1 pure sections ─────────────────────────────────────────────────

def _boot(**kw):
    b = {"commit_sha": "a" * 40, "at": "2027-01-15T07:00:00+00:00",
         "memory_limit_mb": 2048.0, "dedicated_only": ["universal_market_plane"],
         "started": ["premap", "institutional_md"]}
    b.update(kw)
    return b


def test_runtime_reads_the_workers_own_figures_and_the_planes_heartbeat():
    from datetime import datetime
    start = datetime.fromisoformat("2027-01-15T07:00:00+00:00").timestamp()
    rt = CR.runtime_block(
        _boot(), {"detail": {"rss_mb": 900.0, "peak_mb": 1200.0},
                  "beat_at": start + 600},
        {"detail": {"runtime": "DEDICATED_READ_ONLY",
                    "subscription_mode": "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST",
                    "market_data_streams": 1}, "beat_at": start + 3590,
         "status": "ok"}, now=start + 3600)
    sw = rt["shared_workers"]
    assert sw["minutes_since_process_start"] == 60.0
    assert sw["rss_highwater_fraction"] == pytest.approx(1200 / 2048, abs=1e-4)
    assert sw["universal_market_plane_started_here"] is False
    mp = rt["market_plane"]
    assert mp["state"] == "DEDICATED_RUNNING" and mp["market_data_streams"] == 1
    # no heartbeat / a stale one / a non-dedicated label are named
    assert CR.runtime_block(_boot(), None, None, now=start)[
        "market_plane"]["state"] == "NOT_RUNNING"
    assert CR.runtime_block(_boot(), None, {"detail": {}, "beat_at": start},
                            now=start + 9999)["market_plane"]["state"].startswith(
        "HEARTBEAT_STALE")
    other = CR.runtime_block(_boot(), None, {"detail": {"runtime": "X"},
                                             "beat_at": start}, now=start)
    assert other["market_plane"]["state"].startswith("RUNNING_OUTSIDE")
    # an unknown limit is an unknown fraction, never a guess
    assert CR.runtime_block(_boot(memory_limit_mb=None),
                            {"detail": {"peak_mb": 1.0}}, None, now=start)[
        "shared_workers"]["rss_highwater_fraction"] is None


def test_market_data_states_numerator_and_denominator():
    snap = {"subscription": {"subscription_mode": "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST",
                             "market_data_streams": 1},
            "freshness": {"priority_universe": {
                "denominator": 200, "current_pmx_stream": 150,
                "current_rest_fallback": 40, "external_unavailable": 0}},
            "universe": {"registry": {"active": 1000, "pmx_listed": 900,
                                      "pmx_unlisted": 50,
                                      "refdata_pending": 50}}}
    md = CR.market_data_block(snap, None, {})
    pf = md["priority_freshness"]
    assert (pf["numerator"], pf["denominator"], pf["rate"]) == (190, 200, 0.95)
    assert md["refdata"]["coverage_rate"] == 0.95
    none = CR.market_data_block(None, "NO_MARKET_PLANE_SNAPSHOT", None)
    assert none["snapshot"] == "NO_MARKET_PLANE_SNAPSHOT"
    assert none["priority_freshness"]["rate"] is None


def test_pmus_confirmation_names_the_owner_credential_never_a_workaround():
    v = CR.venue_positions_block({"detail": {"positions_source": {
        "source": "FUNDED_ACCOUNT_LEDGER_DERIVED",
        "authority": "LEDGER_DERIVED_NOT_VENUE_CONFIRMED",
        "primary_refusal": CR.R_PMUS_NOT_ED25519}}, "beat_at": NOW}, now=NOW)
    assert v["status"] == "OWNER_CREDENTIAL_REQUIRED"
    assert "Ed25519" in v["credential_type"] and v["venue_confirmed"] is False
    assert v["workaround"].startswith("NONE")
    ok = CR.venue_positions_block({"detail": {"positions_source": {
        "source": CR.VENUE_CONFIRMED_SOURCE}}, "beat_at": NOW}, now=NOW + 10)
    assert ok["status"] == "VENUE_CONFIRMED" and ok["venue_confirmed"]
    assert CR.venue_positions_block(None, now=NOW)["venue_confirmed"] is False


# ── §2 the verdict ───────────────────────────────────────────────────

def _inputs(**over):
    rt = CR.runtime_block(_boot(at="2027-01-15T06:00:00+00:00"),
                          {"detail": {"peak_mb": 1000.0}}, None,
                          now=NOW)
    md = {"priority_freshness": {"rate": 0.99}}
    gates = {"software_reds_zero": {"value": True, "evidence": {"software": 0}},
             "xavier_complete": {"value": True},
             "production_canary_clean": {"value": True},
             "small_live_shadow": {"value": True}}
    kw = dict(runtime=rt, market_data=md,
              management={"fresh_rate": 0.99}, gates=gates,
              probability={"authority": "MARKET_PRIOR_ONLY",
                           "improvement_lower_bound": 0.02,
                           "independent_events": 500},
              ev={"verdict": "CASH"},
              twin={"certified": True, "fill_agreement_rate": 0.98},
              revenue={"data": {"strategies": {},
                                "daily_revenue_readiness": {
                                    "overall_status": "CASH"},
                                "strategy_tournament": {"selected": "CASH"}}},
              settlement={"settlement_proven": True},
              venue={"venue_confirmed": True})
    kw.update(over)
    return kw


def test_the_verdict_is_paper_shadow_only_and_a_cash_governor_keeps_it_there():
    d = CR.readiness_block(**_inputs())
    assert d["status"] == "PAPER_SHADOW_ONLY"
    assert "GOVERNOR_SAYS_CASH" in d["blockers"]
    # market prior only: no positive probability edge is claimed
    assert "POSITIVE_PROBABILITY_EDGE_NOT_PROVEN" in d["blockers"]
    assert "POSITIVE_CAPACITY_NOT_PROVEN" in d["blockers"]
    assert d["auto_activation"] is False
    assert d["capital_authority_granted"] is False


def test_unmeasured_inputs_fail_closed():
    rt = CR.runtime_block(_boot(memory_limit_mb=None), None, None, now=NOW)
    d = CR.readiness_block(**_inputs(
        runtime=rt, market_data={"priority_freshness": {"rate": None}},
        gates={}, twin={"certified": False, "fill_agreement_rate": 0.99},
        venue={"venue_confirmed": False}))
    for b in ("WORKER_MEMORY_HEADROOM_INSUFFICIENT",
              "PRIORITY_FRESHNESS_BELOW_TARGET", "CODE_CONTROLLED_REDS_REMAIN",
              "XAVIER_PACKET_COMPLETENESS_BELOW_TARGET",
              "PRODUCTION_CANARY_NOT_GREEN", "DIGITAL_TWIN_NOT_CERTIFIED",
              "VENUE_POSITION_CONFIRMATION_NOT_PROVEN", "SMALL_LIVE_NOT_SHADOW"):
        assert b in d["blockers"], b


# ── §3 owner blockers ────────────────────────────────────────────────

def test_owner_blockers_carry_the_exact_action():
    rt = CR.runtime_block(_boot(), None, None, now=NOW)
    venue = CR.venue_positions_block({"detail": {"positions_source": {
        "source": "FUNDED_ACCOUNT_LEDGER_DERIVED",
        "primary_refusal": CR.R_PMUS_NOT_ED25519}}, "beat_at": NOW}, now=NOW)
    ob = CR.owner_blockers(rt, venue, None)
    items = {o["item"]: o for o in ob}
    svc = items["DEDICATED_MARKET_PLANE_SERVICE"]
    assert svc["code"] == "OWNER_ACTION_REQUIRED"
    for name in ("PMX_CLIENT_ID", "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64",
                 "DATABASE_URL", "sportsassets-market-plane",
                 "ops/render_market_plane_service.yaml"):
        assert name in svc["action"], name
    assert "no PMUS key" in svc["action"]
    assert items["PMUS_RETAIL_POSITION_CONFIRMATION"]["code"] == \
        "OWNER_CREDENTIAL_REQUIRED"
    assert items["INSTRUMENT_STATE_CHANGE_SUBSCRIPTION_SCHEMA"]["state"] == \
        "VENUE_SCHEMA_NOT_PUBLISHED"
    running = CR.runtime_block(_boot(), None, {
        "detail": {"runtime": "DEDICATED_READ_ONLY"}, "beat_at": NOW}, now=NOW)
    assert "DEDICATED_MARKET_PLANE_SERVICE" not in {
        o["item"] for o in CR.owner_blockers(running, {}, None)}


# ── §4 against Postgres ──────────────────────────────────────────────

@pg
def test_the_whole_read_runs_read_only_and_returns_every_section():
    import asyncpg

    async def go():
        c = await asyncpg.connect(DSN)
        try:
            before = await c.fetchval(
                "SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) "
                "  FROM pg_stat_user_tables")
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = 60000")
                got = await CR.read(c)
            after = await c.fetchval(
                "SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) "
                "  FROM pg_stat_user_tables")
            return got, before, after
        finally:
            await c.close()
    got, before, after = asyncio.run(go())
    for k in ("readiness", "runtime", "market_data", "management_freshness",
              "gates", "probability", "executable_ev", "digital_twin",
              "arbitrage", "venue_positions", "settlement", "owner_blockers"):
        assert k in got, k
    assert got["small_live"] == "SHADOW" and got["authority_changed"] is False
    assert got["readiness"]["status"] == "PAPER_SHADOW_ONLY"
    assert got["readiness"]["capital_authority_granted"] is False
    assert got["arbitrage"]["verdict"] in (
        "NO_ELIGIBLE_ARB", "GUARANTEED_AFTER_COSTS_FOUND_SHADOW_ONLY")
    assert got["executable_ev"]["authority_granted"] is False
    assert before == after


@pg
def test_a_failing_section_is_unavailable_evidence_never_a_failed_read(
        monkeypatch):
    """Production 088af82: one slow statement cancelled the WHOLE readback
    (QueryCanceledError). Each section now runs in its own savepoint with
    its own budget; a failing section is named, timed, and fails the
    readiness closed while every other section still reads."""
    import asyncpg
    from sportsassets.completion import evidence as EVM

    async def boom(conn, **_k):
        await conn.execute("SELECT pg_sleep(2)")
    monkeypatch.setattr(EVM, "read_twin", boom)
    monkeypatch.setattr(CR, "SECTION_TIMEOUT_MS", 500)

    async def go():
        c = await asyncpg.connect(DSN)
        try:
            async with c.transaction(readonly=True):
                return await CR.read(c)
        finally:
            await c.close()
    got = asyncio.run(go())
    t = got["section_timings"]
    assert t["digital_twin"]["ok"] is False
    assert "QueryCanceled" in t["digital_twin"]["why"]
    assert got["sections_unavailable"] == ["digital_twin"]
    assert "SECTION_UNAVAILABLE:digital_twin" in got["readiness"]["blockers"]
    assert got["readiness"]["status"] == "PAPER_SHADOW_ONLY"
    assert got["digital_twin"]["status"] == "SECTION_UNAVAILABLE"
    # every later section still read (the transaction was not aborted)
    for k in ("revenue", "venue_positions", "arbitrage", "settlement"):
        assert t[k]["ok"] is True, (k, t[k])


# ── §5 the route and its reach ───────────────────────────────────────

def test_the_route_is_get_only_command_auth_no_store_and_registered():
    from sportsassets.api import command_completion_readiness as R
    routes = [r for r in R.router.routes if getattr(r, "path", "") == R.PATH]
    assert len(routes) == 1 and routes[0].methods == {"GET"}
    deps = [d.call.__name__ for d in routes[0].dependant.dependencies]
    assert "require_read" in deps
    src = (ROOT / "api" / "command_completion_readiness.py").read_text()
    assert '"private, no-store"' in src and "readonly=True" in src
    app_src = (ROOT / "api" / "app.py").read_text()
    assert "command_completion_readiness" in app_src


def test_the_completion_modules_reach_no_order_or_venue_write_path():
    banned = {"pmus", "pmx", "execution_gate", "live_executor",
              "bettor_funded", "execmirror", "kalshi_live"}
    calls = {"submit", "submit_fok", "place", "cancel_order", "post_order",
             "create_order", "insert_order"}
    for f in sorted((ROOT / "completion").glob("*.py")) + [
            ROOT / "api" / "command_completion_readiness.py"]:
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                mod = (n.module or "").split(".")[-1]
                names = {a.name for a in n.names}
                assert mod not in banned and not names & banned, (f.name, mod)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                assert n.func.attr not in calls, (f.name, n.func.attr)
        text = f.read_text()
        for w in ("INSERT INTO", "UPDATE market", "DELETE FROM"):
            assert w not in text, (f.name, w)
