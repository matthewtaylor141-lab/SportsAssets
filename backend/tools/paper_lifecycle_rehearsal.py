"""THE THREE-AGENT PAPER LIFECYCLE REHEARSAL: DEREK -> SIMULATOR -> XAVIER ->
SETTLEMENT -> AUDREY, THROUGH THE PRODUCTION CODE PATHS, ON A LOCAL TEST
DATABASE.

    REHEARSAL -- SYNTHETIC -- NOT LIVE EXECUTION

WHAT THIS IS. One deterministic scenario, driven ONLY through the paper
engine's production entry points -- `paper_runtime.decide_valuation` (the
per-valuation hook the collection cycle calls) and `paper_runtime.paper_pass`
(the scheduled pass, with its default steps and its enablement check) --
so every write below is made by the production code: Derek's completed-game
decision (`paper_benchmark` with `CG_POLICY`), the paper order
(`bettor_paper_ledger.submit_order`), the simulated fills
(`bettor_paper_simulator`), the one ledger, Xavier's handoff, reviews,
standing protection, exit and `step_settle`, Audrey's monitor and her event
audits, and the linked chain read (`agents.paper_learning.group_chain`).

WHAT IS SYNTHETIC. The valuations (`tests/paper_live_fixture.valuation`,
written with the venue's RECORDED MLB wording for the 2026-10-01 Phillies v
Braves listing), the books (the fixture transport behind the REAL
`PaperMarketDataClient`, which refuses and counts every venue mutation), and
the venue's last-fair-market-price publication (`settlement_read` on the
valuation row, as the venue outcome join records it). Nothing is fetched
from a venue; nothing is sent to one.

WHERE IT RUNS -- AND WHERE IT REFUSES TO.
  * the DSN host must be 127.0.0.1 or localhost (a local test database);
  * the account it creates must start with "paper_rehearsal_" and is never
    the production paper account `paper_acct_main`;
  * DATABASE_URL and RN1X_TEST_DSN are pointed at the same local DSN in this
    process BEFORE any production module is imported, so no code path can
    reach another database;
  * the background fill pass the hook would schedule is replaced by a
    no-op: every pass is the explicit `paper_pass` call below, on this
    connection.
The production account, its session and its single $500,000 funding entry
are not touched (the rehearsal reads their ledger count in the local
database before and after and asserts it unchanged).

THE SCENARIO, IN ORDER (each step asserted; evidence recorded):
  1 ENTRY        three completed-game valuations (positions A, B, C), each
                 12 pp over the book: Derek's CG decision ENTER -> one order.
  2 PARTIAL FILL position A's fill-time book is thinner than its order: two
                 simulated fills (two levels), IOC remainder canceled; the
                 cash debit is sum(qty x price) + fees on the one ledger.
  3 HANDOFF      Xavier owns A with confirmed_qty == the ACTUAL filled qty.
  4 PROTECTION   Xavier's ONE resting protective sale for exactly that qty.
  5 EXIT         a later book whose bid beats the measure: Xavier recommends
                 EXIT -> the protection is canceled (confirmed terminal) ->
                 the exit sale -> SALE ledger entries, realized P&L.
  6 SETTLEMENT   position B: postponed, nothing published -> stays open;
                 the venue publishes its last fair market price (0.43,
                 outcome_basis NULL) -> SETTLED_AT_VENUE_PRICE at 0.43, never
                 an assumed refund. Position C has no published price and
                 stays open and pending.
  7 RESTART      caches dropped, a new connection and a new market-data
                 client, mid-lifecycle (twice) and at the end: the hooks and
                 passes rerun and nothing is duplicated.
  8 AUDIT        Audrey's event audits (first fills, handoffs, the exit
                 sale, the settlement incl. SETTLED_AT_VENUE_PRICE), the
                 ledger reconciliation, and the linked chain of each closed
                 position complete with no MISSING link.

OUTPUT (into --out): rehearsal_evidence.json (every id, timestamp, quantity,
price, fee and ledger delta, and every assertion) and REHEARSAL_SUMMARY.md.

    cd backend && python -m tools.paper_lifecycle_rehearsal \\
        --dsn postgresql://postgres:postgres@127.0.0.1:5432/<local_test_db> \\
        --out <dir>

Exit status 0 when every assertion passed, 1 when one failed (the evidence
is still written, status FAILED, with the failing step named), 2 when the
guards refused to run.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import decimal
import json
import os
import pathlib
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

LABEL = "REHEARSAL — SYNTHETIC — NOT LIVE EXECUTION"
VERSION = "PAPER_LIFECYCLE_REHEARSAL_V1"
ACCOUNT_PREFIX = "paper_rehearsal_"
PRODUCTION_ACCOUNT = "paper_acct_main"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
#: valuations carry this slug prefix: the paper live fixtures' own prefix
#: (so `paper_live_fixture.purge_everything` removes them too) plus ours
SLUG_PREFIX = "paper-live-syn-rehearsal-"
EVIDENCE_FILE = "rehearsal_evidence.json"
SUMMARY_FILE = "REHEARSAL_SUMMARY.md"

#: THE SCENARIO'S NUMBERS (synthetic). p is the de-vigged Pinnacle
#: probability stored on each valuation; every decision book offers 0.50,
#: so the best-level edge is 12 pp (the CG policy's threshold is 5 pp).
P_PIN = 0.62
DECISION_BOOKS = {
    "A": {"offers": [(0.50, 300)], "bids": [(0.48, 300)]},
    "B": {"offers": [(0.50, 200)], "bids": [(0.48, 200)]},
    "C": {"offers": [(0.50, 150)], "bids": [(0.48, 150)]},
}
#: A's FILL-TIME BOOK: 120 contracts within the 0.50 limit over two levels
#: (an order of 300 -> partial), and 5,000 displayed at 0.51 -- outside the
#: entry's limit, but AHEAD of Xavier's protective sale in the queue, so a
#: later crossing bid serves that queue first (the simulator's resting rule).
A_FILL_BOOK = {"offers": [(0.49, 50), (0.50, 70), (0.51, 5000)],
               "bids": [(0.47, 300)]}
A_EXIT_BOOK_1 = {"bids": [(0.75, 200), (0.74, 100)], "offers": [(0.77, 500)]}
A_EXIT_BOOK_2 = {"bids": [(0.74, 200), (0.73, 100)], "offers": [(0.76, 500)]}
VENUE_PRICE = "0.43"


class Refused(Exception):
    """A guard refused to run (exit status 2)."""


class StepFailed(Exception):
    """An assertion failed (exit status 1)."""


# ═════════════════════════════════════════════════════════════════════
# THE GUARDS (pure)
# ═════════════════════════════════════════════════════════════════════

def guard_dsn(dsn: str) -> dict:
    """THE DSN MUST NAME A LOCAL DATABASE: host 127.0.0.1 or localhost, a
    database name, and no host list. Anything else is refused by name."""
    if not dsn or not isinstance(dsn, str):
        raise Refused("NO_DSN")
    u = urlsplit(dsn)
    if u.scheme not in ("postgres", "postgresql"):
        raise Refused("NOT_A_POSTGRES_DSN")
    if "," in (u.netloc or ""):
        raise Refused("HOST_LISTS_ARE_REFUSED")
    host = (u.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise Refused("DSN_HOST_IS_NOT_LOCAL: %r (allowed: %s)"
                      % (host or None, ", ".join(LOCAL_HOSTS)))
    db = (u.path or "").lstrip("/")
    if not db:
        raise Refused("DSN_NAMES_NO_DATABASE")
    return {"host": host, "port": u.port or 5432, "database": db}


def guard_account(account_id: str) -> str:
    if not isinstance(account_id, str) or \
            not account_id.startswith(ACCOUNT_PREFIX):
        raise Refused("ACCOUNT_ID_MUST_START_WITH_%s" % ACCOUNT_PREFIX)
    if account_id == PRODUCTION_ACCOUNT:
        raise Refused("THE_PRODUCTION_PAPER_ACCOUNT_IS_NEVER_USED")
    return account_id


def new_account_id(at: float) -> str:
    stamp = _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    return guard_account("%s%s_%s" % (ACCOUNT_PREFIX, stamp,
                                      uuid.uuid4().hex[:8]))


# ═════════════════════════════════════════════════════════════════════
# RECORDING
# ═════════════════════════════════════════════════════════════════════

def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, _dt.datetime):
        return v.astimezone(_dt.timezone.utc).isoformat()
    if isinstance(v, (_dt.date,)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r) -> dict | None:
    return None if r is None else {k: _plain(v) for k, v in dict(r).items()}


async def _rows(conn, sql: str, *args) -> list:
    return [_row(r) for r in await conn.fetch(sql, *args)]


class Recorder:
    def __init__(self):
        self.assertions: list = []
        self.steps: list = []
        self.current = None

    def step(self, name: str, title: str) -> dict:
        s = {"step": name, "title": title, "assertions": [], "evidence": {}}
        self.steps.append(s)
        self.current = s
        return s

    def check(self, name: str, cond, **detail) -> bool:
        ok = bool(cond)
        a = {"step": self.current["step"] if self.current else None,
             "check": name, "passed": ok,
             "detail": _plain(detail) if detail else {}}
        self.assertions.append(a)
        if self.current is not None:
            self.current["assertions"].append(a)
        if not ok:
            raise StepFailed("%s: %s %s" % (a["step"], name,
                                            json.dumps(a["detail"],
                                                       default=str)[:800]))
        return ok

    def put(self, key: str, value) -> None:
        self.current["evidence"][key] = _plain(value)


# ═════════════════════════════════════════════════════════════════════
# THE REHEARSAL
# ═════════════════════════════════════════════════════════════════════

async def _nosleep(_):
    return None


def _no_background_pass():
    # The hook would schedule a background pass on the process pool; here
    # every pass is the explicit paper_pass call on this connection.
    return {"scheduled": False, "why": "REHEARSAL_RUNS_EACH_PASS_EXPLICITLY"}


def _transport(PL, t0: float):
    """THE FIXTURE TRANSPORT, its receipt clock kept AT the pass clock: each
    read is stamped 10 ms after the previous one, so a book is never
    observed "in the future" of a later decision (in production a book's
    observed_at is its real receipt instant). With the fixture's default
    1 s per read, a pass of a dozen reads would stamp observations ahead of
    the next pass's decision clock and the simulator -- correctly -- would
    use that earlier-read book as "the first book at or after the eligible
    instant"."""
    t = PL.Transport(t0)
    t.step = 0.01
    return t


async def _count(conn, acct: str) -> dict:
    """The records a duplicate would show up in, for this account."""
    q = {
        "decisions": "SELECT count(*) FROM paper_decisions WHERE "
                     " account_id=$1",
        "decisions_cg": "SELECT count(*) FROM paper_decisions WHERE "
                        " account_id=$1 AND strategy="
                        "'PINNACLE_COMPLETED_GAME_PAPER'",
        "orders": "SELECT count(*) FROM paper_orders WHERE account_id=$1",
        "entry_orders": "SELECT count(*) FROM paper_orders WHERE "
                        " account_id=$1 AND role='ENTRY'",
        "fills": "SELECT count(*) FROM paper_fills WHERE account_id=$1",
        "handoffs": "SELECT count(*) FROM paper_handoffs WHERE "
                    " account_id=$1",
        "ledger": "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
        "funding": "SELECT count(*) FROM paper_ledger WHERE account_id=$1 "
                   " AND kind='INITIAL_FUNDING'",
        "settlements": "SELECT count(*) FROM paper_settlements WHERE "
                       " account_id=$1",
        "event_audits": "SELECT count(*) FROM paper_audrey_findings WHERE "
                        " account_id=$1 AND kind LIKE 'PAPER_EVENT_%'",
        "open_standing": "SELECT count(*) FROM paper_orders WHERE "
                         " account_id=$1 AND role='STANDING_PROTECTION' AND "
                         " state IN ('PENDING_SIMULATION','RESTING',"
                         " 'PARTIALLY_FILLED','CANCEL_PENDING')",
    }
    return {k: int(await conn.fetchval(s, acct)) for k, s in q.items()}


DUP_KEYS = ("decisions", "decisions_cg", "orders", "entry_orders", "fills",
            "handoffs", "ledger", "funding", "settlements", "event_audits")


async def _set_control(conn, key: str, enabled: bool):
    prev = await conn.fetchrow("SELECT enabled FROM paper_control WHERE "
                               " control_key=$1", key)
    if prev is None:
        await conn.execute(
            "INSERT INTO paper_control (control_key, enabled, why, "
            " updated_by) VALUES ($1,$2,'local rehearsal','rehearsal')",
            key, bool(enabled))
    else:
        # the switch alone: the row's provenance is left as it was
        await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                           " control_key=$1", key, bool(enabled))
    return None if prev is None else bool(prev["enabled"])


async def _restore_control(conn, key: str, prev) -> None:
    if prev is None:
        await conn.execute("DELETE FROM paper_control WHERE control_key=$1 "
                           " AND updated_by='rehearsal'", key)
    else:
        await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                           " control_key=$1", key, bool(prev))


async def purge_synthetic_valuations(dsn: str) -> int:
    """REMOVE THE REHEARSAL'S OWN SYNTHETIC VALUATIONS (slug prefix
    SLUG_PREFIX) from the LOCAL test database -- and nothing else -- so a
    later rehearsal or proof in the same database does not decide them.
    The paper records that cite them are kept (decision provenance carries
    the valuation inputs as read, with their SHA-256)."""
    guard_dsn(dsn)
    import asyncpg
    c = await asyncpg.connect(dsn)
    try:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            got = await c.execute(
                "DELETE FROM external_valuations WHERE us_market_slug "
                " LIKE $1", SLUG_PREFIX + "%")
        return int(str(got).split()[-1])
    finally:
        await c.close()


async def rehearse(dsn: str, out_dir, *, t0: float | None = None) -> dict:
    """RUN THE SCENARIO; write the evidence and the summary into `out_dir`.
    Returns the evidence dict (status PASSED or FAILED)."""
    target = guard_dsn(dsn)
    prev_env = {k: os.environ.get(k) for k in (
        "DATABASE_URL", "RN1X_TEST_DSN", "PAPER_SESSION", "PAPER_BENCHMARK")}
    # EVERY CONNECTION THIS PROCESS COULD OPEN POINTS AT THE LOCAL DSN,
    # set before any production module is imported (and put back after).
    os.environ["DATABASE_URL"] = dsn
    os.environ["RN1X_TEST_DSN"] = dsn
    os.environ["PAPER_SESSION"] = "on"
    os.environ["PAPER_BENCHMARK"] = "on"
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = Recorder()
    ev: dict[str, Any] = {
        "label": LABEL, "version": VERSION, "status": "RUNNING",
        "target": target, "started_at": _dt.datetime.now(
            _dt.timezone.utc).isoformat(),
        "what_is_synthetic": [
            "valuations (tests/paper_live_fixture.valuation; the venue's "
            "RECORDED MLB wording, Phillies v Braves 2026-10-01)",
            "books (fixture transport behind the real "
            "PaperMarketDataClient)",
            "the venue's last-fair-market-price publication "
            "(external_valuations.settlement_read)"],
        "what_is_production_code": [
            "paper_runtime.decide_valuation", "paper_runtime.paper_pass "
            "(default steps, enablement checked, force=False)",
            "paper_benchmark CG_POLICY decide_one / step_completed_game",
            "bettor_paper_simulator", "bettor_paper_ledger",
            "paper_xavier step_handoff / step / step_settle",
            "paper_audrey.step", "paper_learning.step_audit_events / "
            "group_chain / fill_chain"],
        "never": ["no venue call", "no venue order", "no funded table write",
                  "not the production paper account"]}
    controls: dict = {}
    conn = None
    try:
        conn, ev = await _scenario(dsn, rec, ev, controls, t0=t0)
        ev["status"] = "PASSED"
    except StepFailed as exc:
        ev["status"] = "FAILED"
        ev["failure"] = str(exc)
    except Refused:
        raise
    except Exception as exc:
        ev["status"] = "FAILED"
        ev["failure"] = "%s: %s" % (type(exc).__name__, str(exc)[:1000])
        import traceback
        ev["traceback"] = traceback.format_exc()[-4000:]
    finally:
        try:
            import asyncpg
            c = await asyncpg.connect(dsn)
            try:
                for k, prev in controls.items():
                    await _restore_control(c, k, prev)
            finally:
                await c.close()
        except Exception:
            pass
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ev["controls_restored"] = {k: v for k, v in controls.items()}
        ev["steps"] = rec.steps
        ev["assertions_total"] = len(rec.assertions)
        ev["assertions_passed"] = sum(1 for a in rec.assertions
                                      if a["passed"])
        ev["finished_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        (out_dir / EVIDENCE_FILE).write_text(
            json.dumps(_plain(ev), indent=2, default=str, sort_keys=False))
        (out_dir / SUMMARY_FILE).write_text(summary_markdown(ev))
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
    return ev


async def _scenario(dsn, rec: Recorder, ev: dict, controls: dict, *,
                    t0=None):
    import asyncpg

    from sportsassets import bettor_external_shadow as EXT
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_session as S
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    from sportsassets.agents import paper_learning as PLRN
    from sportsassets.agents import paper_runtime as PR
    from tests import paper_live_fixture as PL

    CG = PB.CG_STRATEGY
    conn = await asyncpg.connect(dsn)
    T0 = float(t0 if t0 is not None else time.time())
    acct = new_account_id(T0)
    tag = acct[len(ACCOUNT_PREFIX):].replace("_", "-").lower()
    ev.update(account_id=acct, t0=T0, t0_iso=_dt.datetime.fromtimestamp(
        T0, _dt.timezone.utc).isoformat())

    # ── PRECONDITIONS ────────────────────────────────────────────────
    rec.step("0_setup", "Local database, isolated account, paper controls")
    db = await conn.fetchrow("SELECT current_database() AS db, "
                             " inet_server_addr()::text AS addr, "
                             " version() AS pg")
    rec.put("database", _row(db))
    rec.check("dsn_host_is_local", ev["target"]["host"] in LOCAL_HOSTS,
              host=ev["target"]["host"])
    rec.check("account_is_a_rehearsal_account",
              acct.startswith(ACCOUNT_PREFIX) and acct != L.ACCOUNT_ID,
              account_id=acct)
    rec.check("schema_has_the_learning_record",
              await PLRN.has_schema(conn))
    prod_before = int(await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
        L.ACCOUNT_ID))
    # THE SAME CANDIDATE WINDOW THE PASS READS: anything else of the entry
    # experiment in it would be decided by this account too.
    lookback = float(PL.config()["entry"]["valuation_lookback_s"])
    foreign = int(await conn.fetchval(
        "SELECT count(*) FROM external_valuations WHERE experiment_id=$1 "
        "   AND decided_at > to_timestamp($2) "
        "   AND decided_at <= to_timestamp($3)", EXT.EXPERIMENT_ID,
        T0 - lookback - 120.0, T0 + 120.0))
    rec.check("no_foreign_candidate_valuations_in_the_window", foreign == 0,
              foreign=foreign,
              why=("another entry-experiment valuation in the pass's "
                   "lookback would be decided by this account and make the "
                   "rehearsal non-deterministic; wait %.0f s, use a fresh "
                   "local database, or (an earlier rehearsal's own rows "
                   "only) pass --purge-synthetic-valuations-first"
                   % lookback))
    for key, on in ((S.CONTROL_KEY, True), (PB.CG_POLICY["control_key"], True),
                    (PB.CONTROL_KEY, False), (PL.TWO_MODEL_ENTRIES_KEY,
                                              False)):
        controls[key] = await _set_control(conn, key, on)
    rec.put("controls_set", {S.CONTROL_KEY: True,
                             PB.CG_POLICY["control_key"]: True,
                             PB.CONTROL_KEY: False,
                             PL.TWO_MODEL_ENTRIES_KEY: False})
    rec.put("controls_previous", controls)
    en = await S.enablement(conn)
    rec.check("paper_session_enabled_env_and_row", en["enabled"] is True,
              enablement=en)
    rec.check("completed_game_policy_enabled",
              (await PB.enablement(conn, PB.CG_POLICY))["enabled"] is True)
    rec.check("strict_benchmark_off",
              (await PB.enablement(conn))["enabled"] is False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    cfg = PL.config()
    sess = await S.ensure_session(conn, account_id=acct, config=cfg, now=T0)
    rec.check("session_created", sess["ok"] and not sess.get("resumed"),
              session_id=sess.get("session_id"))
    sid = sess["session_id"]
    ev["session_id"] = sid
    fund = await _rows(conn, "SELECT seq, kind, cash_delta_usd, "
                       " committed_at FROM paper_ledger WHERE account_id=$1",
                       acct)
    rec.check("funded_once_with_500000",
              len(fund) == 1 and fund[0]["kind"] == "INITIAL_FUNDING"
              and abs(fund[0]["cash_delta_usd"] - 500000.0) < 1e-9,
              ledger=fund)
    rec.put("initial_funding", fund)
    rec.put("session", {k: sess.get(k) for k in (
        "session_id", "account_id", "started_at", "config_sha",
        "simulator_version")})

    transport = _transport(PL, T0)
    state = {"transport": transport, "client": PL.client(transport),
             "conn": conn}
    passes: list = []
    ev["passes"] = passes

    def set_book(slug, book):
        state["transport"].set(slug, bids=book.get("bids", ()),
                               offers=book.get("offers", ()))

    async def run_pass(at: float, label: str) -> dict:
        t = state["transport"]
        t.t = max(t.t, float(at))
        res = await PR.paper_pass(state["conn"], now=at, account_id=acct,
                                  market_data=state["client"], config=cfg,
                                  force=False, fee_fn=None, sleep=_nosleep,
                                  trigger="REHEARSAL:%s" % label)
        digest = {"label": label, "at": at, "offset_s": round(at - T0, 3),
                  "ran": res.get("ran"), "refusal": res.get("refusal"),
                  "errors": res.get("errors"),
                  "mutation_attempts": res.get("mutation_attempts"),
                  "books_read": res.get("books_read"),
                  "fills": res.get("fills"),
                  "decisions_recorded": res.get("decisions_recorded"),
                  "orders_submitted": res.get("orders_submitted"),
                  "reviews": res.get("reviews"),
                  "steps": {k: (v if not isinstance(v, dict) else {
                      kk: vv for kk, vv in v.items()
                      if kk not in ("obs", "results")})
                      for k, v in (res.get("steps") or {}).items()}}
        passes.append(_plain(digest))
        rec.check("pass_%s_ran_without_errors" % label,
                  res.get("ran") and not res.get("errors"),
                  refusal=res.get("refusal"), errors=res.get("errors"))
        rec.check("pass_%s_no_venue_mutation" % label,
                  res.get("mutation_attempts") == 0,
                  mutation_attempts=res.get("mutation_attempts"))
        return res

    async def hook(vid: int, at: float) -> dict:
        t = state["transport"]
        t.t = max(t.t, float(at))
        return await PR.decide_valuation(
            state["conn"], valuation_id=vid, now=at,
            market_data=state["client"], account_id=acct, fee_fn=None,
            schedule_fill=_no_background_pass)

    async def restart(label: str) -> None:
        """A NEW RUNTIME CONTEXT: every in-process cache dropped, the
        connection closed and reopened, a new market-data client over a
        new transport carrying the same synthetic books."""
        await state["conn"].close()
        PB._CONTEXT_CACHE.clear()
        PD._CONTEXT_CACHE.clear()
        PR._CLIENT["client"] = None
        PR._LOCK.update(lock=None, loop=None)
        PR._TASK.update(task=None)
        old = state["transport"]
        t = _transport(PL, old.t)
        t.books = json.loads(json.dumps(old.books))
        state.update(transport=t, client=PL.client(t),
                     conn=await asyncpg.connect(dsn))
        rec.put("restart_%s" % label, {
            "caches_cleared": ["paper_benchmark._CONTEXT_CACHE",
                               "paper_derek._CONTEXT_CACHE",
                               "paper_runtime._CLIENT",
                               "paper_runtime._LOCK", "paper_runtime._TASK"],
            "new_connection": True, "new_market_data_client": True})

    # ═════════════════════════════════════════════════════════════════
    # 1 · ENTRY
    # ═════════════════════════════════════════════════════════════════
    rec.step("1_entry", "Completed-game valuations -> Derek's CG decision "
             "ENTER -> paper orders (decide_valuation, the in-cycle hook)")
    vals = {}
    for k in ("A", "B", "C"):
        v = await PL.valuation(conn, slug="%s%s-%s" % (SLUG_PREFIX, tag,
                                                       k.lower()),
                               decided_at=T0 - 10.0, p_pin=P_PIN,
                               compatibility="INCOMPATIBLE")
        vals[k] = v
        set_book(v["slug"], DECISION_BOOKS[k])
    rec.put("valuations", [_row(await conn.fetchrow(
        "SELECT id, us_market_slug, probability, observed_at, decided_at, "
        " payout_event, buy_intent, sport_family, market, period, "
        " settlement_comparison->>'venue_rules_text' AS venue_rules_text, "
        " settlement_comparison->>'compatibility' AS compatibility "
        " FROM external_valuations WHERE id=$1", v["valuation_id"]))
        for v in vals.values()])
    hooks = {}
    for k, v in vals.items():
        got = await hook(v["valuation_id"], T0)
        hooks[k] = got
        cg = got.get("benchmark_completed_game") or {}
        rec.check("%s_cg_decision_enter" % k,
                  cg.get("verdict") == "ENTER" and cg.get("order_id"),
                  hook=got)
        rec.check("%s_no_venue_mutation_in_hook" % k,
                  got.get("mutation_attempts") == 0)
    rec.put("hook_results", hooks)
    decisions = {}
    orders = {}
    for k, v in vals.items():
        d = _row(await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", sid, v["valuation_id"], CG))
        decisions[k] = d
        chk = {c["check"]: c for c in
               d["pinnacle"]["contract_match"]["checks"]}
        gp = chk.get("ordinary_completion_grading_period") or {}
        econ = d["economics"]
        rec.check("%s_decision_is_cg_policy" % k,
                  d["verdict"] == "ENTER" and d["policy_version"]
                  == PB.CG_VERSION and d["decision_id"].startswith(
                      "papercg:"), decision_id=d["decision_id"])
        rec.check("%s_recorded_mlb_venue_wording_graded" % k,
                  gp.get("passed") is True and (gp.get("venue") or {}).get(
                      "period") == PB.GP_BASEBALL, check=gp)
        rec.check("%s_edge_at_least_5pp" % k,
                  econ["best_level_edge_pp"] >= 5.0
                  and abs(econ["best_level_edge_pp"] - 12.0) < 1e-6,
                  edge_pp=econ["best_level_edge_pp"],
                  threshold_pp=econ["threshold_edge_pp"])
        rec.check("%s_conditional_economics_label" % k,
                  econ.get("label") == PB.ECONOMICS_LABEL
                  and econ.get("conditional_on") == "ORDINARY_COMPLETION")
        o = _row(await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE decision_id=$1 AND "
            " role='ENTRY'", d["decision_id"]))
        orders[k] = o
        want_qty = DECISION_BOOKS[k]["offers"][0][1]
        rec.check("%s_one_entry_order_naming_the_decision" % k,
                  o is not None and o["strategy"] == CG
                  and o["state"] == "PENDING_SIMULATION"
                  and abs(o["qty"] - want_qty) < 1e-9
                  and abs(o["limit_price"] - 0.50) < 1e-9
                  and o["allow_partial"] is True
                  and o["order_type"] == "MARKETABLE"
                  and o["time_in_force"] == "IOC",
                  order={kk: (o or {}).get(kk) for kk in (
                      "order_id", "qty", "limit_price", "state",
                      "allow_partial", "order_type", "time_in_force")})
    rec.put("decisions", {k: {kk: d.get(kk) for kk in (
        "decision_id", "decided_at", "valuation_id", "us_market_slug",
        "verdict", "strategy", "policy_version", "p_pinnacle", "book_obs_id",
        "proposed_qty", "limit_price")} | {
        "best_level_edge_pp": d["economics"]["best_level_edge_pp"],
        "threshold_edge_pp": d["economics"]["threshold_edge_pp"],
        "expected_net_profit_usd": d["economics"]["acquisition"][
            "expected_net_profit_usd"],
        "decided_via": d["pinnacle"].get("decided_via"),
        "pinnacle_age_s": d["pinnacle"].get("age_s"),
        "grading_check": next(
            (c for c in d["pinnacle"]["contract_match"]["checks"]
             if c["check"] == "ordinary_completion_grading_period"), None),
        "exceptional_terms": d["economics"].get("exceptional_terms"),
        "parameters": (d["policy_decision"] or {}).get("parameters")}
        for k, d in decisions.items()})
    rec.put("entry_orders", {k: {kk: o.get(kk) for kk in (
        "order_id", "group_id", "decision_id", "role", "direction", "qty",
        "limit_price", "reserved_usd", "order_type", "time_in_force",
        "allow_partial", "state", "decided_at", "eligible_at", "expires_at",
        "strategy")} for k, o in orders.items()})
    groups = {k: o["group_id"] for k, o in orders.items()}
    ev["groups"] = groups
    ev["slugs"] = {k: v["slug"] for k, v in vals.items()}
    ev["valuation_ids"] = {k: v["valuation_id"] for k, v in vals.items()}

    # ═════════════════════════════════════════════════════════════════
    # 2 · PARTIAL FILL  (+ 3 HANDOFF, 4 STANDING PROTECTION in the same pass)
    # ═════════════════════════════════════════════════════════════════
    rec.step("2_partial_fill", "A's fill-time book is thinner than its "
             "order: partial simulated fills; cash debit on the one ledger")
    set_book(vals["A"]["slug"], A_FILL_BOOK)
    await run_pass(T0 + 3.0, "T+3_fill")
    oa = _row(await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                  " order_id=$1", orders["A"]["order_id"]))
    fa = await _rows(conn, "SELECT fill_id, order_id, group_id, role, "
                     " direction, qty, price, wire_price, fee_usd, gross_usd,"
                     " book_obs_id, book_observed_at, filled_at, basis, "
                     " event_source, strategy FROM paper_fills WHERE "
                     " order_id=$1 ORDER BY filled_at, price, fill_id",
                     oa["order_id"])
    filled = sum(x["qty"] for x in fa)
    rec.check("A_partial_fills_on_two_levels",
              len(fa) == 2 and [(x["price"], x["qty"]) for x in fa]
              == [(0.49, 50.0), (0.50, 70.0)],
              fills=[(x["price"], x["qty"]) for x in fa])
    rec.check("A_filled_less_than_ordered", abs(filled - 120.0) < 1e-9
              and filled < oa["qty"], filled=filled, ordered=oa["qty"])
    rec.check("A_order_terminal_ioc_remainder_canceled",
              oa["state"] == "CANCELED" and abs(oa["filled_qty"] - 120.0)
              < 1e-9 and oa["terminal_reason"] in (
                  "IOC_REMAINDER_CANCELED",
                  "NO_DISPLAYED_LIQUIDITY_WITHIN_THE_LIMIT"),
              state=oa["state"], terminal_reason=oa["terminal_reason"])
    rec.check("A_fills_are_simulator_fills_of_the_cg_strategy",
              {x["event_source"] for x in fa} == {"SIMULATOR"}
              and {x["strategy"] for x in fa} == {CG})
    rec.check("A_fees_charged_per_fill", all(x["fee_usd"] > 0 for x in fa),
              fees=[x["fee_usd"] for x in fa])
    led_a = await _rows(conn, "SELECT seq, kind, cash_delta_usd, "
                        " reserved_delta_usd, cash_after_usd, "
                        " reserved_after_usd, order_id, fill_id, group_id, "
                        " committed_at, detail FROM paper_ledger WHERE "
                        " account_id=$1 AND order_id=$2 ORDER BY seq",
                        acct, oa["order_id"])
    debit = -sum(e["cash_delta_usd"] for e in led_a if e["kind"] == "FILL")
    cost = sum(x["qty"] * x["price"] for x in fa)
    fees = sum(x["fee_usd"] for x in fa)
    rec.check("A_cash_debit_equals_qty_x_price_plus_fees",
              abs(debit - (cost + fees)) < 1e-6, debit=debit,
              sum_qty_x_price=cost, fees=fees)
    by_fill = {e["fill_id"]: e for e in led_a if e["kind"] == "FILL"}
    rec.check("A_one_fill_ledger_entry_per_fill",
              all(abs(by_fill[x["fill_id"]]["cash_delta_usd"]
                      + x["qty"] * x["price"] + x["fee_usd"]) < 1e-6
                  for x in fa) and len(by_fill) == len(fa))
    rec.check("A_unfilled_reservation_released",
              sum(1 for e in led_a if e["kind"] == "RESERVATION_RELEASED")
              == 1 and abs(sum(e["reserved_delta_usd"] for e in led_a))
              < 1e-6, ledger=[(e["kind"], e["reserved_delta_usd"])
                              for e in led_a])
    bal = await L.balances(conn, acct, now=T0 + 4.0)
    all_buys = await _rows(conn, "SELECT qty, price, fee_usd FROM "
                           " paper_fills WHERE account_id=$1 AND "
                           " direction='BUY'", acct)
    spend = sum(x["qty"] * x["price"] + x["fee_usd"] for x in all_buys)
    rec.check("one_ledger_cash_is_funding_minus_all_buys",
              bal["ledger_consistent"] is True
              and abs(bal["cash_usd"] - (500000.0 - spend)) < 1e-6,
              cash=bal["cash_usd"], expected=500000.0 - spend)
    for k in ("B", "C"):
        o = _row(await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                     " order_id=$1", orders[k]["order_id"]))
        rec.check("%s_filled_completely" % k, o["state"] == "FILLED"
                  and abs(o["filled_qty"] - o["qty"]) < 1e-9,
                  state=o["state"], filled_qty=o["filled_qty"])
    rec.put("A_entry_order_after_fill", {k: oa.get(k) for k in (
        "order_id", "qty", "filled_qty", "state", "terminal_at",
        "terminal_reason", "reserved_usd", "reserved_remaining_usd")})
    rec.put("A_fills", fa)
    rec.put("A_ledger_entries", led_a)
    rec.put("A_cash_debit_usd", {"debit": debit, "sum_qty_x_price": cost,
                                 "fees": fees})
    rec.put("balances_after_entries", {k: bal.get(k) for k in (
        "cash_usd", "reserved_usd", "available_usd", "ledger_consistent",
        "last_sequence")})

    # ── 3 · HANDOFF ──────────────────────────────────────────────────
    rec.step("3_handoff", "Xavier owns each group; confirmed_qty == the "
             "ACTUAL filled quantity")
    hands = {}
    for k in ("A", "B", "C"):
        h = _row(await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                     " group_id=$1", groups[k]))
        f_qty = float(await conn.fetchval(
            "SELECT coalesce(sum(qty),0) FROM paper_fills WHERE order_id=$1",
            orders[k]["order_id"]))
        first = await conn.fetchval(
            "SELECT fill_id FROM paper_fills WHERE order_id=$1 ORDER BY "
            " filled_at, fill_id LIMIT 1", orders[k]["order_id"])
        rec.check("%s_handoff_owner_xavier" % k, h is not None
                  and h["owner"] == "XAVIER" and h["strategy"] == CG)
        rec.check("%s_confirmed_qty_is_the_actual_filled_qty" % k,
                  abs(h["confirmed_qty"] - f_qty) < 1e-9
                  and abs(h["outstanding_qty"]) < 1e-9,
                  confirmed_qty=h["confirmed_qty"], filled_qty=f_qty,
                  ordered_qty=orders[k]["qty"],
                  outstanding_qty=h["outstanding_qty"])
        rec.check("%s_handoff_from_the_first_fill" % k,
                  h["first_fill_id"] == first)
        hands[k] = h
    rec.check("A_confirmed_qty_is_not_the_ordered_qty",
              hands["A"]["confirmed_qty"] < orders["A"]["qty"])
    rec.put("handoffs", hands)

    # ── 4 · STANDING PROTECTION ──────────────────────────────────────
    rec.step("4_standing_protection", "Xavier's ONE resting protective sale "
             "per group, for the actual quantity")
    standing = {}
    for k in ("A", "B", "C"):
        rows = await _rows(conn, "SELECT order_id, group_id, role, "
                           " direction, order_type, time_in_force, qty, "
                           " filled_qty, limit_price, state, queue_ahead_qty,"
                           " decided_at, eligible_at, expires_at, strategy "
                           " FROM paper_orders WHERE group_id=$1 AND "
                           " role='STANDING_PROTECTION' ORDER BY created_at",
                           groups[k])
        live = [r for r in rows if r["state"] in L.OPEN_STATES]
        rec.check("%s_exactly_one_live_standing_order" % k, len(live) == 1,
                  standing=rows)
        rec.check("%s_standing_qty_is_the_actual_qty" % k,
                  abs(live[0]["qty"] - hands[k]["confirmed_qty"]) < 1e-9
                  and live[0]["direction"] == "SELL"
                  and live[0]["order_type"] == "RESTING",
                  qty=live[0]["qty"], confirmed=hands[k]["confirmed_qty"])
        pos = next(p for p in await L.positions(conn, acct)
                   if p["group_id"] == groups[k])
        rec.check("%s_protective_price_recovers_cost_and_fees" % k,
                  live[0]["qty"] * live[0]["limit_price"]
                  > pos["cost_basis_usd"],
                  limit=live[0]["limit_price"],
                  cost_basis=pos["cost_basis_usd"])
        standing[k] = rows
    reviews_a = await _rows(conn, "SELECT review_id, reviewed_at, trigger, "
                            " recommendation, action, measure, exposure "
                            " FROM paper_xavier_reviews WHERE group_id=$1 "
                            " ORDER BY reviewed_at", groups["A"])
    rec.check("A_first_review_holds_and_places_protection",
              reviews_a and reviews_a[0]["trigger"] == "FIRST_FILL"
              and reviews_a[0]["recommendation"] == "HOLD"
              and reviews_a[0]["action"]["taken"] == "PLACE_STANDING",
              review=reviews_a[:1])
    await run_pass(T0 + 6.0, "T+6_steady")
    c = await _count(conn, acct)
    rec.check("single_live_hedge_invariant_after_a_steady_pass",
              c["open_standing"] == 3, counts=c)
    rec.put("standing_orders", standing)
    rec.put("A_first_review", reviews_a[:1])

    # ═════════════════════════════════════════════════════════════════
    # 7a · RESTART AFTER PROTECTION (mid-lifecycle)
    # ═════════════════════════════════════════════════════════════════
    rec.step("7a_restart_mid_lifecycle", "New runtime context after the "
             "protection is resting: hooks and passes rerun, nothing "
             "duplicated")
    before = await _count(state["conn"], acct)
    await restart("after_protection")
    conn = state["conn"]
    rerun = {k: await hook(v["valuation_id"], T0 + 8.0)
             for k, v in vals.items()}
    rec.check("hook_reruns_are_duplicates",
              all((r.get("benchmark_completed_game") or {}).get("duplicate")
                  for r in rerun.values()), reruns=rerun)
    r8 = await run_pass(T0 + 8.0, "T+8_after_restart")
    rec.check("session_resumed_not_recreated", r8.get("resumed") is True
              and r8.get("session_id") == sid)
    await run_pass(T0 + 9.0, "T+9_after_restart")
    after = await _count(conn, acct)
    rec.check("no_duplicates_after_restart",
              all(before[k] == after[k] for k in DUP_KEYS)
              and after["open_standing"] == before["open_standing"],
              before=before, after=after)
    rec.put("counts_before", before)
    rec.put("counts_after", after)

    # ═════════════════════════════════════════════════════════════════
    # 5 · EXIT
    # ═════════════════════════════════════════════════════════════════
    rec.step("5a_exit_recommendation", "A later book makes Xavier's EXIT "
             "fire; the resting protection is canceled first")
    set_book(vals["A"]["slug"], A_EXIT_BOOK_1)
    await run_pass(T0 + 12.0, "T+12_exit_book")
    rv = await _rows(conn, "SELECT review_id, reviewed_at, trigger, "
                     " recommendation, action, measure, selection, "
                     " alternatives FROM paper_xavier_reviews WHERE "
                     " group_id=$1 ORDER BY reviewed_at, review_id",
                     groups["A"])
    last = rv[-1]
    rec.check("A_exit_recommended_on_the_market_event",
              last["trigger"] == "MARKET_EVENT"
              and last["recommendation"] == "EXIT"
              and last["action"]["taken"] == "CANCEL_STANDING_BEFORE_EXIT",
              review={k: last.get(k) for k in ("review_id", "trigger",
                                               "recommendation", "action")})
    cands = {c["action"]: c for c in last["alternatives"]["candidates"]}
    rec.check("A_exit_value_beats_hold_on_the_same_measure",
              cands["EXIT"]["value_usd"] > cands["HOLD"]["value_usd"],
              exit_value=cands["EXIT"]["value_usd"],
              hold_value=cands["HOLD"]["value_usd"],
              measure=last["measure"])
    st = _row(await conn.fetchrow(
        "SELECT order_id, state, filled_qty, queue_ahead_qty FROM "
        " paper_orders WHERE group_id=$1 AND role='STANDING_PROTECTION'",
        groups["A"]))
    rec.check("A_protection_not_filled_queue_ahead_served_first",
              st["state"] == "CANCEL_PENDING" and st["filled_qty"] == 0.0,
              standing=st)
    rec.put("A_exit_review_1", {k: last.get(k) for k in (
        "review_id", "reviewed_at", "trigger", "recommendation", "action",
        "measure", "selection")} | {"candidates": last["alternatives"][
            "candidates"]})

    # 7b · A RESTART WITH THE CANCEL STILL PENDING
    rec.step("7b_restart_cancel_pending", "New runtime context while the "
             "protection's cancel is pending")
    before_b = await _count(state["conn"], acct)
    await restart("cancel_pending")
    conn = state["conn"]
    rerun_b = {k: await hook(v["valuation_id"], T0 + 13.0)
               for k, v in vals.items()}
    after_b = await _count(conn, acct)
    rec.check("hook_reruns_are_duplicates_with_cancel_pending",
              all((r.get("benchmark_completed_game") or {}).get("duplicate")
                  for r in rerun_b.values())
              and all(before_b[k] == after_b[k] for k in DUP_KEYS),
              before=before_b, after=after_b)

    rec.step("5b_exit_sale", "Cancel confirmed terminal, the exit sale, "
             "SALE ledger entries, realized P&L")
    set_book(vals["A"]["slug"], A_EXIT_BOOK_2)
    await run_pass(T0 + 14.0, "T+14_exit_submit")
    st = _row(await conn.fetchrow(
        "SELECT order_id, state, terminal_reason, terminal_at FROM "
        " paper_orders WHERE group_id=$1 AND role='STANDING_PROTECTION'",
        groups["A"]))
    rec.check("A_protection_cancel_confirmed_terminal",
              st["state"] == "CANCELED"
              and st["terminal_reason"] == "CANCEL_CONFIRMED_BY_SIMULATOR",
              standing=st)
    ex = _row(await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE group_id=$1 AND role='EXIT'",
        groups["A"]))
    rec.check("A_exit_order_for_the_whole_actual_qty",
              ex is not None and ex["direction"] == "SELL"
              and abs(ex["qty"] - hands["A"]["confirmed_qty"]) < 1e-9
              and ex["strategy"] == CG,
              exit_order={k: (ex or {}).get(k) for k in (
                  "order_id", "qty", "limit_price", "state")})
    rec.check("no_second_live_order_while_exiting",
              int(await conn.fetchval(
                  "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND "
                  " direction='SELL' AND state = ANY($2::text[])",
                  groups["A"], list(L.OPEN_STATES))) == 1)
    await run_pass(T0 + 17.0, "T+17_exit_fill")
    ex = _row(await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                  " order_id=$1", ex["order_id"]))
    sf = await _rows(conn, "SELECT fill_id, order_id, role, direction, qty, "
                     " price, fee_usd, gross_usd, book_obs_id, "
                     " book_observed_at, filled_at, basis, event_source "
                     " FROM paper_fills WHERE order_id=$1 ORDER BY "
                     " filled_at, fill_id", ex["order_id"])
    sl = await _rows(conn, "SELECT seq, kind, cash_delta_usd, "
                     " reserved_delta_usd, cash_after_usd, fill_id, "
                     " committed_at, detail FROM paper_ledger WHERE "
                     " account_id=$1 AND order_id=$2 ORDER BY seq", acct,
                     ex["order_id"])
    rec.check("A_exit_filled", ex["state"] == "FILLED" and sf
              and abs(sum(x["qty"] for x in sf) - 120.0) < 1e-9,
              state=ex["state"], fills=sf)
    rec.check("A_sale_ledger_entries_proceeds_minus_fees",
              len([e for e in sl if e["kind"] == "SALE"]) == len(sf)
              and all(abs({e["fill_id"]: e for e in sl}[x["fill_id"]][
                  "cash_delta_usd"] - (x["qty"] * x["price"] - x["fee_usd"]))
                  < 1e-6 for x in sf), sales=sl)
    pa = next(p for p in await L.positions(conn, acct, include_closed=True)
              if p["group_id"] == groups["A"])
    proceeds = sum(x["qty"] * x["price"] - x["fee_usd"] for x in sf)
    acq = sum(x["qty"] * x["price"] + x["fee_usd"] for x in fa)
    rec.check("A_closed_and_realized_pnl_is_proceeds_minus_cost",
              abs(pa["open_qty"]) < 1e-9
              and abs(pa["realized_pnl_usd"] - (proceeds - acq)) < 1e-6,
              realized=pa["realized_pnl_usd"], proceeds_net=proceeds,
              acquisition_cost=acq)
    rec.put("A_exit_order", {k: ex.get(k) for k in (
        "order_id", "role", "direction", "qty", "filled_qty", "limit_price",
        "state", "decided_at", "eligible_at", "terminal_at",
        "terminal_reason")})
    rec.put("A_exit_fills", sf)
    rec.put("A_sale_ledger", sl)
    rec.put("A_position_closed", pa)

    # ═════════════════════════════════════════════════════════════════
    # 6 · EXCEPTIONAL SETTLEMENT
    # ═════════════════════════════════════════════════════════════════
    rec.step("6_exceptional_settlement", "B: nothing published -> open; "
             "the venue's last fair market price -> SETTLED_AT_VENUE_PRICE. "
             "C: no published price -> stays open")
    r20 = await run_pass(T0 + 20.0, "T+20_postponed_unpublished")
    rec.check("nothing_settles_before_the_venue_publishes",
              int(await conn.fetchval(
                  "SELECT count(*) FROM paper_settlements WHERE "
                  " account_id=$1", acct)) == 0,
              settle_step=r20["steps"].get("settle"))
    rec.check("pending_reason_is_no_venue_price",
              (r20["steps"]["settle"].get("pending_reasons") or {}).get(
                  "NO_VENUE_PRICE_SETTLEMENT_RECORDED") == 2,
              settle_step=r20["steps"].get("settle"))
    cash_b0 = (await L.balances(conn, acct, now=T0 + 21.0))["cash_usd"]
    # THE VENUE PUBLISHES ITS LAST-FAIR-MARKET-PRICE SETTLEMENT (synthetic):
    # the outcome join records the price; neither side was paid in full, so
    # outcome_basis stays NULL.
    await conn.execute(
        "UPDATE external_valuations SET settlement_read=$2, "
        " settlement_read_at=now() WHERE id=$1",
        vals["B"]["valuation_id"], VENUE_PRICE)
    vb = _row(await conn.fetchrow(
        "SELECT id, settlement_read, settlement_read_at, outcome_basis, "
        " outcome_known FROM external_valuations WHERE id=$1",
        vals["B"]["valuation_id"]))
    rec.check("venue_publication_has_no_outcome_basis",
              vb["outcome_basis"] is None and vb["settlement_read"]
              == VENUE_PRICE, valuation=vb)
    r23 = await run_pass(T0 + 23.0, "T+23_venue_price_published")
    sb = _row(await conn.fetchrow("SELECT * FROM paper_settlements WHERE "
                                  " group_id=$1", groups["B"]))
    qb = hands["B"]["confirmed_qty"]
    rec.check("B_settled_at_the_venue_price",
              sb is not None and sb["outcome"] == "SETTLED_AT_VENUE_PRICE"
              and abs(sb["payout_per_contract"] - 0.43) < 1e-9
              and abs(sb["payout_usd"] - 0.43 * qb) < 1e-6
              and sb["evidence_source"]
              == "external_valuations.settlement_read",
              settlement=sb)
    led_b = _row(await conn.fetchrow(
        "SELECT seq, kind, cash_delta_usd, settlement_key, committed_at, "
        " detail FROM paper_ledger WHERE account_id=$1 AND "
        " kind='SETTLEMENT' AND detail->>'settlement_id'=$2", acct,
        sb["settlement_id"]))
    cash_b1 = (await L.balances(conn, acct, now=T0 + 24.0))["cash_usd"]
    rec.check("B_settlement_ledger_credit_is_the_venue_payout",
              led_b is not None
              and abs(led_b["cash_delta_usd"] - 0.43 * qb) < 1e-6
              and abs((cash_b1 - cash_b0) - 0.43 * qb) < 1e-6,
              ledger=led_b, cash_delta=cash_b1 - cash_b0)
    rec.check("B_never_an_assumed_refund",
              abs((cash_b1 - cash_b0) - 0.50 * qb) > 1e-6,
              refund_would_be=0.50 * qb, paid=cash_b1 - cash_b0)
    stb = _row(await conn.fetchrow(
        "SELECT order_id, state, terminal_reason FROM paper_orders WHERE "
        " group_id=$1 AND role='STANDING_PROTECTION'", groups["B"]))
    rec.check("B_protection_released_on_the_settled_market",
              stb["state"] == "CANCELED"
              and stb["terminal_reason"] == "MARKET_SETTLED", standing=stb)
    pc = next((p for p in await L.positions(conn, acct)
               if p["group_id"] == groups["C"]), None)
    rec.check("C_no_published_price_stays_open_and_pending",
              pc is not None and abs(pc["open_qty"] - 150.0) < 1e-9
              and pc["settlement"] is None
              and int(await conn.fetchval(
                  "SELECT count(*) FROM paper_settlements WHERE "
                  " group_id=$1", groups["C"])) == 0
              and (r23["steps"]["settle"].get("pending_reasons") or {}).get(
                  "NO_VENUE_PRICE_SETTLEMENT_RECORDED") == 1,
              position=pc, settle_step=r23["steps"].get("settle"))
    pb = next(p for p in await L.positions(conn, acct, include_closed=True)
              if p["group_id"] == groups["B"])
    rec.put("B_venue_publication", vb)
    rec.put("B_settlement", sb)
    rec.put("B_settlement_ledger", led_b)
    rec.put("B_position_closed", pb)
    rec.put("B_standing_after_settlement", stb)
    rec.put("C_position_open", pc)

    # ═════════════════════════════════════════════════════════════════
    # 7c · RESTART AT THE END: EVERYTHING RERUN, NOTHING DUPLICATED
    # ═════════════════════════════════════════════════════════════════
    rec.step("7c_restart_at_the_end", "New runtime context after the exit "
             "and the settlement: hooks and passes rerun")
    before_c = await _count(state["conn"], acct)
    await restart("end")
    conn = state["conn"]
    rerun_c = {k: await hook(v["valuation_id"], T0 + 26.0)
               for k, v in vals.items()}
    await run_pass(T0 + 26.0, "T+26_after_restart")
    await run_pass(T0 + 27.0, "T+27_after_restart")
    after_c = await _count(conn, acct)
    rec.check("no_duplicates_after_the_final_restart",
              all(before_c[k] == after_c[k] for k in DUP_KEYS)
              and all((r.get("benchmark_completed_game") or {}).get(
                  "duplicate") for r in rerun_c.values()),
              before=before_c, after=after_c)
    dup_audits = await _rows(conn, "SELECT kind, subject, count(*) AS n "
                             " FROM paper_audrey_findings WHERE "
                             " account_id=$1 AND kind LIKE 'PAPER_EVENT_%' "
                             " GROUP BY 1,2 HAVING count(*) > 1", acct)
    rec.check("each_event_audited_exactly_once", not dup_audits,
              duplicates=dup_audits)
    rec.put("counts_before", before_c)
    rec.put("counts_after", after_c)

    # ═════════════════════════════════════════════════════════════════
    # 8 · AUDIT
    # ═════════════════════════════════════════════════════════════════
    rec.step("8_audit", "Audrey's event audits, the ledger reconciliation, "
             "the linked chain of each closed position")
    findings = await _rows(conn, "SELECT finding_id, found_at, kind, "
                           " severity, subject, detail FROM "
                           " paper_audrey_findings WHERE account_id=$1 AND "
                           " kind LIKE 'PAPER_EVENT_%' ORDER BY found_at, "
                           " finding_id", acct)
    have = {(f["kind"], f["subject"]): f for f in findings}
    all_fills = await _rows(conn, "SELECT fill_id, order_id, group_id, role,"
                            " direction, qty, price, fee_usd, gross_usd, "
                            " filled_at FROM paper_fills WHERE account_id=$1"
                            " ORDER BY filled_at, fill_id", acct)
    for k in ("A", "B", "C"):
        first = next(x for x in all_fills
                     if x["order_id"] == orders[k]["order_id"])
        f = have.get((PLRN.EV_FIRST_FILL, first["fill_id"]))
        rec.check("%s_first_fill_audited_and_passed" % k,
                  f is not None and f["detail"].get("passed") is True
                  and f["detail"].get("ledger_debit_matches") is True,
                  finding=f)
        f = have.get((PLRN.EV_HANDOFF, hands[k]["handoff_id"]))
        rec.check("%s_handoff_audited_and_passed" % k,
                  f is not None and f["detail"].get("passed") is True,
                  finding=f)
    for x in sf:
        f = have.get((PLRN.EV_MGMT_FILL, x["fill_id"]))
        rec.check("A_exit_sale_audited_and_passed",
                  f is not None and f["detail"].get("passed") is True
                  and f["detail"].get("ledger_kind") == "SALE", finding=f)
    for kind in (PLRN.EV_SETTLEMENT, PLRN.EV_VENUE_PRICE,
                 PLRN.EV_EXCEPTIONAL):
        f = have.get((kind, sb["settlement_id"]))
        rec.check("B_%s_audited_and_passed" % kind,
                  f is not None and f["detail"].get("passed") is True,
                  finding=f)
    vp = have[(PLRN.EV_VENUE_PRICE, sb["settlement_id"])]["detail"]
    rec.check("venue_price_audit_says_never_an_assumed_refund",
              vp.get("venue_price_checks_passed") is True
              and vp.get("never_an_assumed_refund") is True)
    ledger_alerts = [f for f in findings if f["kind"] == PLRN.EV_LEDGER]
    rec.check("no_ledger_inconsistency_found_by_audrey", not ledger_alerts,
              findings=ledger_alerts)
    rec.check("every_event_audit_info_severity",
              all(f["severity"] == "INFO" for f in findings),
              severities=sorted({f["severity"] for f in findings}))

    # THE RECONCILIATION OF LEDGER EFFECTS, recomputed independently from
    # the fills and settlements and compared with the one ledger
    ledger = await _rows(conn, "SELECT seq, kind, cash_delta_usd, "
                         " reserved_delta_usd, cash_after_usd, "
                         " reserved_after_usd, order_id, fill_id, group_id, "
                         " settlement_key, committed_at FROM paper_ledger "
                         " WHERE account_id=$1 ORDER BY seq", acct)
    setts = await _rows(conn, "SELECT settlement_id, group_id, outcome, qty,"
                        " payout_per_contract, payout_usd, settled_at FROM "
                        " paper_settlements WHERE account_id=$1", acct)
    buys = sum(x["qty"] * x["price"] + x["fee_usd"] for x in all_fills
               if x["direction"] == "BUY")
    sells = sum(x["qty"] * x["price"] - x["fee_usd"] for x in all_fills
                if x["direction"] == "SELL")
    pays = sum(s["payout_usd"] for s in setts)
    expected_cash = 500000.0 - buys + sells + pays
    bal = await L.balances(conn, acct, now=T0 + 28.0)
    ledger_sum = sum(e["cash_delta_usd"] for e in ledger)
    rec.check("ledger_reconciles_with_fills_and_settlements",
              abs(ledger_sum - expected_cash) < 1e-6
              and abs(bal["cash_usd"] - expected_cash) < 1e-6
              and bal["ledger_consistent"] is True,
              ledger_sum=ledger_sum, expected_cash=expected_cash,
              balances_cash=bal["cash_usd"], buys=buys, sells=sells,
              settlements=pays)
    rec.check("no_reservation_left_on_terminal_orders",
              abs(bal["reserved_usd"]) < 1e-6, reserved=bal["reserved_usd"])
    rec.check("running_balance_agrees_at_every_entry",
              all(abs(e["cash_after_usd"] - sum(
                  x["cash_delta_usd"] for x in ledger[:i + 1])) < 1e-6
                  for i, e in enumerate(ledger)))
    rec.check("one_funding_entry_only", sum(
        1 for e in ledger if e["kind"] == "INITIAL_FUNDING") == 1)

    # THE LINKED CHAIN, as management reads it
    chains = {}
    for k in ("A", "B", "C"):
        chains[k] = await PLRN.group_chain(conn, groups[k])
    for k in ("A", "B"):
        ch = chains[k]
        rec.check("%s_chain_complete_no_missing_links" % k,
                  ch["complete"] is True and not ch["missing"]
                  and not ch["defects"],
                  missing=ch["missing"], defects=ch["defects"],
                  links=[(x["link"], x["status"]) for x in ch["links"]])
    want_links = ["DEREK_DECISION", "ENTRY_ORDER", "ENTRY_FILLS",
                  "LEDGER_CASH_DEBIT", "XAVIER_HANDOFF", "XAVIER_REVIEWS",
                  "XAVIER_ACTIONS", "EXIT_OR_SETTLEMENT", "LEDGER_RESULT",
                  "AUDREY_AUDIT"]
    for k in ("A", "B"):
        rec.check("%s_chain_has_every_link_in_order" % k,
                  [x["link"] for x in chains[k]["links"]] == want_links)
    rec.check("A_chain_closed_by_the_exit_sale",
              next(x for x in chains["A"]["links"]
                   if x["link"] == "EXIT_OR_SETTLEMENT")["detail"].get(
                  "closed_by") == "SALES")
    rec.check("C_chain_open_position_pending_not_defective",
              set(chains["C"]["missing"]) == {"EXIT_OR_SETTLEMENT",
                                              "LEDGER_RESULT"}
              and not chains["C"]["defects"]
              and set(chains["C"]["pending"]) == {"EXIT_OR_SETTLEMENT",
                                                  "LEDGER_RESULT"},
              missing=chains["C"]["missing"], pending=chains["C"]["pending"])
    fc = await PLRN.fill_chain(conn, fa[0]["fill_id"])
    rec.check("fill_chain_of_the_first_partial_fill_reads_the_same_group",
              fc["found"] and fc["group_id"] == groups["A"]
              and fc["complete"] is True)
    prod_after = int(await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
        L.ACCOUNT_ID))
    rec.check("production_account_untouched_in_this_database",
              prod_before == prod_after, before=prod_before,
              after=prod_after)
    health = _row(await conn.fetchrow(
        "SELECT session_id, mutation_attempts FROM paper_session_health "
        " WHERE session_id=$1", sid))
    rec.check("session_health_zero_venue_mutations",
              health is not None and int(health["mutation_attempts"]) == 0,
              health=health)
    funded = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE "
        " table_name LIKE 'bettor_funded%' AND column_name='account_id'")
    rec.put("funded_tables_with_account_column", int(funded or 0))
    rec.put("event_audits", findings)
    rec.put("ledger", ledger)
    rec.put("settlements", setts)
    rec.put("fills", all_fills)
    rec.put("reconciliation", {
        "starting_cash_usd": 500000.0, "buys_cost_plus_fees_usd": buys,
        "sales_proceeds_minus_fees_usd": sells,
        "settlement_payouts_usd": pays, "expected_cash_usd": expected_cash,
        "ledger_sum_usd": ledger_sum, "balances": {k: bal.get(k) for k in (
            "cash_usd", "reserved_usd", "available_usd",
            "ledger_consistent", "last_sequence")}})
    rec.put("chains", chains)
    rec.put("all_reviews", await _rows(
        conn, "SELECT review_id, group_id, reviewed_at, trigger, "
        " recommendation, action->>'taken' AS action, "
        " action->>'order_id' AS action_order_id, "
        " measure->>'source' AS measure_source, "
        " (measure->>'p')::float8 AS measure_p FROM paper_xavier_reviews "
        " WHERE account_id=$1 ORDER BY reviewed_at, review_id", acct))
    rec.put("all_orders", await _rows(
        conn, "SELECT order_id, group_id, role, direction, order_type, "
        " time_in_force, qty, filled_qty, limit_price, reserved_usd, state, "
        " decided_at, eligible_at, terminal_at, terminal_reason, strategy "
        " FROM paper_orders WHERE account_id=$1 ORDER BY created_at, "
        " order_id", acct))
    rec.put("all_decisions", await _rows(
        conn, "SELECT decision_id, decided_at, valuation_id, strategy, "
        " verdict, refusal FROM paper_decisions WHERE account_id=$1 "
        " ORDER BY decided_at, decision_id", acct))
    ev["positions"] = _plain(await L.positions(conn, acct,
                                               include_closed=True))
    return conn, ev


# ═════════════════════════════════════════════════════════════════════
# THE SUMMARY
# ═════════════════════════════════════════════════════════════════════

def _usd(v) -> str:
    return "—" if v is None else "%.6f" % float(v)


def summary_markdown(ev: dict) -> str:
    steps = {s["step"]: s for s in ev.get("steps") or []}

    def evd(step, key, default=None):
        return (steps.get(step) or {}).get("evidence", {}).get(key, default)

    out = ["# %s" % LABEL, "",
           "**Paper lifecycle rehearsal (%s)** — status **%s** — %s/%s "
           "assertions passed." % (ev.get("version"), ev.get("status"),
                                   ev.get("assertions_passed"),
                                   ev.get("assertions_total")), "",
           "Synthetic valuations and books, driven through the production "
           "paper paths (`paper_runtime.decide_valuation`, "
           "`paper_runtime.paper_pass`) on a LOCAL test database. No venue "
           "call, no venue order, not the production paper account.", "",
           "| | |", "|---|---|",
           "| database | `%s` on `%s` |" % (
               (ev.get("target") or {}).get("database"),
               (ev.get("target") or {}).get("host")),
           "| account | `%s` |" % ev.get("account_id"),
           "| session | `%s` |" % ev.get("session_id"),
           "| T0 | %s (%s) |" % (ev.get("t0_iso"), ev.get("t0")),
           "| started / finished | %s / %s |" % (ev.get("started_at"),
                                                 ev.get("finished_at")), ""]
    if ev.get("failure"):
        out += ["**FAILED:** `%s`" % ev["failure"], ""]
    dec = evd("1_entry", "decisions") or {}
    ords = evd("1_entry", "entry_orders") or {}
    if dec:
        out += ["## 1 · Entry (Derek, completed-game policy)", "",
                "| pos | decision_id | decided_at | valuation | edge pp "
                "(threshold) | order_id | qty | limit |",
                "|---|---|---|---|---|---|---|---|"]
        for k, d in dec.items():
            o = ords.get(k) or {}
            out.append("| %s | `%s` | %s | %s | %s (%s) | `%s` | %s | %s |"
                       % (k, d.get("decision_id"), d.get("decided_at"),
                          d.get("valuation_id"), d.get("best_level_edge_pp"),
                          d.get("threshold_edge_pp"), o.get("order_id"),
                          o.get("qty"), o.get("limit_price")))
        out.append("")
    fa = evd("2_partial_fill", "A_fills") or []
    if fa:
        deb = evd("2_partial_fill", "A_cash_debit_usd") or {}
        out += ["## 2 · Partial fill (position A)", "",
                "| fill_id | filled_at | qty | price | fee | gross |",
                "|---|---|---|---|---|---|"]
        for x in fa:
            out.append("| `%s` | %s | %s | %s | %s | %s |" % (
                x["fill_id"], x["filled_at"], x["qty"], x["price"],
                _usd(x["fee_usd"]), _usd(x["gross_usd"])))
        out += ["", "Cash debit on the one ledger: **%s** = Σ qty·price %s "
                "+ fees %s." % (_usd(deb.get("debit")),
                                _usd(deb.get("sum_qty_x_price")),
                                _usd(deb.get("fees"))), ""]
    hands = evd("3_handoff", "handoffs") or {}
    if hands:
        out += ["## 3 · Handoff to Xavier", "",
                "| pos | handoff_id | first_fill_at | confirmed_qty | "
                "outstanding |", "|---|---|---|---|---|"]
        for k, h in hands.items():
            out.append("| %s | `%s` | %s | %s | %s |" % (
                k, h["handoff_id"], h["first_fill_at"], h["confirmed_qty"],
                h["outstanding_qty"]))
        out.append("")
    so = evd("4_standing_protection", "standing_orders") or {}
    if so:
        out += ["## 4 · Standing protection (one live order per group)", "",
                "| pos | order_id | qty | limit | state at placement pass |",
                "|---|---|---|---|---|"]
        for k, rows in so.items():
            for r in rows:
                out.append("| %s | `%s` | %s | %s | %s |" % (
                    k, r["order_id"], r["qty"], r["limit_price"],
                    r["state"]))
        out.append("")
    xo = {}
    for s in ev.get("steps") or []:
        if s["step"].startswith("5"):
            xo.update(s.get("evidence") or {})
    if xo.get("A_exit_fills"):
        pa = xo.get("A_position_closed") or {}
        r1 = xo.get("A_exit_review_1") or {}
        out += ["## 5 · Exit (position A)", "",
                "Review `%s` (%s): **%s**, action %s." % (
                    r1.get("review_id"), r1.get("trigger"),
                    r1.get("recommendation"),
                    (r1.get("action") or {}).get("taken")), "",
                "Exit order `%s`: SELL %s at limit %s, decided %s, %s (%s)."
                % ((xo.get("A_exit_order") or {}).get("order_id"),
                   (xo.get("A_exit_order") or {}).get("qty"),
                   (xo.get("A_exit_order") or {}).get("limit_price"),
                   (xo.get("A_exit_order") or {}).get("decided_at"),
                   (xo.get("A_exit_order") or {}).get("state"),
                   (xo.get("A_exit_order") or {}).get("terminal_reason")),
                "",
                "| fill_id | filled_at | qty | price | fee | SALE ledger "
                "seq / delta |", "|---|---|---|---|---|---|"]
        led = {e["fill_id"]: e for e in xo.get("A_sale_ledger") or []}
        for x in xo["A_exit_fills"]:
            e = led.get(x["fill_id"]) or {}
            out.append("| `%s` | %s | %s | %s | %s | %s / %s |" % (
                x["fill_id"], x["filled_at"], x["qty"], x["price"],
                _usd(x["fee_usd"]), e.get("seq"),
                _usd(e.get("cash_delta_usd"))))
        out += ["", "Realized P&L (A): **%s** (acquisition %s incl. fees)."
                % (_usd(pa.get("realized_pnl_usd")),
                   _usd(pa.get("acquisition_cost_usd"))), ""]
    sb = evd("6_exceptional_settlement", "B_settlement")
    if sb:
        lb = evd("6_exceptional_settlement", "B_settlement_ledger") or {}
        pb = evd("6_exceptional_settlement", "B_position_closed") or {}
        pc = evd("6_exceptional_settlement", "C_position_open") or {}
        out += ["## 6 · Exceptional settlement (position B) and a pending "
                "case (C)", "",
                "B: `%s` outcome **%s** at **%s**/contract × %s = %s "
                "(evidence `%s`); ledger seq %s delta %s; realized P&L %s. "
                "Never an assumed refund." % (
                    sb["settlement_id"], sb["outcome"],
                    sb["payout_per_contract"], sb["qty"],
                    _usd(sb["payout_usd"]), sb["evidence_source"],
                    lb.get("seq"), _usd(lb.get("cash_delta_usd")),
                    _usd(pb.get("realized_pnl_usd"))), "",
                "C: no published price — open qty %s, settlement %s "
                "(pending)." % (pc.get("open_qty"), pc.get("settlement")),
                ""]
    rs = [s for s in ev.get("steps") or [] if s["step"].startswith("7")]
    if rs:
        out += ["## 7 · Restart recovery", ""]
        for s in rs:
            out.append("- **%s** — %s: %s" % (
                s["step"], s["title"], ", ".join(
                    "%s=%s" % (a["check"], "PASS" if a["passed"] else
                               "FAIL") for a in s["assertions"])))
        out.append("")
    a8 = (steps.get("8_audit") or {}).get("evidence", {})
    if a8.get("ledger"):
        rcn = a8.get("reconciliation") or {}
        out += ["## 8 · Audit", "",
                "Audrey's event audits:", "",
                "| kind | subject | severity | passed | found_at |",
                "|---|---|---|---|---|"]
        for f in a8.get("event_audits") or []:
            out.append("| %s | `%s` | %s | %s | %s |" % (
                f["kind"], f["subject"], f["severity"],
                (f.get("detail") or {}).get("passed"), f["found_at"]))
        out += ["", "Ledger (every entry):", "",
                "| seq | kind | cash Δ | reserved Δ | cash after | ref |",
                "|---|---|---|---|---|---|"]
        for e in a8["ledger"]:
            out.append("| %s | %s | %s | %s | %s | `%s` |" % (
                e["seq"], e["kind"], _usd(e["cash_delta_usd"]),
                _usd(e["reserved_delta_usd"]), _usd(e["cash_after_usd"]),
                e.get("fill_id") or e.get("settlement_key")
                or e.get("order_id") or ""))
        out += ["", "Reconciliation: 500,000 − buys %s + sales %s + "
                "settlements %s = **%s**; ledger sum %s; consistent %s." % (
                    _usd(rcn.get("buys_cost_plus_fees_usd")),
                    _usd(rcn.get("sales_proceeds_minus_fees_usd")),
                    _usd(rcn.get("settlement_payouts_usd")),
                    _usd(rcn.get("expected_cash_usd")),
                    _usd(rcn.get("ledger_sum_usd")),
                    (rcn.get("balances") or {}).get("ledger_consistent")),
                "", "Linked chains:", ""]
        for k, ch in (a8.get("chains") or {}).items():
            out.append("- **%s** `%s`: complete=%s, missing=%s, pending=%s, "
                       "defects=%s" % (k, ch.get("group_id"),
                                       ch.get("complete"), ch.get("missing"),
                                       ch.get("pending"), ch.get("defects")))
            for x in ch.get("links") or []:
                out.append("    - %s: %s %s" % (x["link"], x["status"],
                                                ", ".join(str(i) for i in
                                                          x["ids"][:4])))
        out.append("")
    out += ["## Assertions", ""]
    for s in ev.get("steps") or []:
        for a in s["assertions"]:
            out.append("- [%s] %s · %s" % ("x" if a["passed"] else " ",
                                           s["step"], a["check"]))
    out += ["", "_%s_" % LABEL, ""]
    return "\n".join(out)


# ═════════════════════════════════════════════════════════════════════
# THE COMMAND
# ═════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.paper_lifecycle_rehearsal",
        description="%s: the three-agent paper lifecycle through the "
                    "production paths, on a LOCAL test database." % LABEL)
    ap.add_argument("--dsn", required=True,
                    help="local test database DSN (host 127.0.0.1 or "
                         "localhost only)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--purge-synthetic-valuations-first",
                    action="store_true",
                    help="first delete an earlier rehearsal's synthetic "
                         "valuations (slug prefix %s) from the local "
                         "database" % SLUG_PREFIX)
    args = ap.parse_args(argv)
    try:
        guard_dsn(args.dsn)
    except Refused as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    try:
        if args.purge_synthetic_valuations_first:
            n = asyncio.run(purge_synthetic_valuations(args.dsn))
            print("purged %d earlier synthetic rehearsal valuation(s)" % n)
        ev = asyncio.run(rehearse(args.dsn, args.out))
    except Refused as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    print("%s\nstatus %s, %s/%s assertions passed; account %s; evidence %s"
          % (LABEL, ev["status"], ev.get("assertions_passed"),
             ev.get("assertions_total"), ev.get("account_id"),
             pathlib.Path(args.out) / EVIDENCE_FILE))
    if ev.get("failure"):
        print("failure: %s" % ev["failure"], file=sys.stderr)
    return 0 if ev["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
