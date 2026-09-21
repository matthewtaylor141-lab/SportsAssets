"""REPLAY THE ACTUAL PRODUCTION SCHEDULER. Not a simulator.

Owner 2026-09-21: "Before deployment, exercise the actual production
scheduling code against recorded workloads and constrained budgets. A
separate simulator passing while the production implementation differs
is not sufficient."

That instruction exists because it already happened. bettor_schedule_sim
sorted follow-up candidates on-time-first while production's mids_due
ordered `ORDER BY o.observed_at` -- oldest first. The simulator was
validating a scheduler the system did not run, on exactly the axis it
was being used to predict, and it forecast on-time reads for W3 that
production did not deliver.

WHAT THIS RUNS. The real `workers.bettor_state.tick()`, the real
`bettor_state_store` SQL, against a real PostgreSQL with the real
migrations 088-091 applied in order. The scheduler, the queries, the
budget arithmetic and the telemetry writes are the deployed ones. No
scheduling logic is reimplemented here.

WHAT IS SUBSTITUTED, AND IT IS ONLY THESE THREE THINGS:

  1. THE VENUE. `_read_book` is replaced by a local function that
     returns a well-formed book immediately and counts the call. No
     network. Every request the scheduler decides to issue is counted
     exactly where it would have been issued.
  2. THE CLOCK SCALE. Horizons, tolerance, recovery window and early
     eligibility are divided by TIME_SCALE so a multi-hour workload
     replays in minutes. These are DATA, not logic: the same
     comparisons run on the same code paths, including inside the SQL,
     because the store passes these values as bind parameters.
  3. THE ARRIVAL SOURCE. us_premap is seeded from a recorded workload
     rather than the production mirror.

WHAT IS NOT SUBSTITUTED: the budget split, the rotation, the
per-horizon cap, the due-query ordering, the eligibility bound, the
timing classification, the expiry accounting, or any counter.

TIME_SCALE IS A FIDELITY COST AND IS STATED, NOT HIDDEN. Scaling
horizons changes which tick phases land inside a band, and phase is a
real effect in this system -- at 72s spacing the 900s band is
unreachable. So a scaled run is evidence about ALLOCATION AND RATES,
not about phase. `scale=1` runs the same harness unscaled for a short
window, which is where phase claims must come from.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

PG_BIN = "/usr/lib/postgresql/16/bin"
PG_DIR = "/tmp/pgreplay"
PG_PORT = 55432
PG_HOST = "/tmp"
PG_USER = "replay"
PG_DB = "bettor_replay"

DSN = "postgresql://%s@/%s?host=%s&port=%d" % (PG_USER, PG_DB, PG_HOST,
                                               PG_PORT)

MIGRATIONS = ("088_bettor_unselected_state.sql",
              "089_bettor_capture_telemetry.sql",
              "090_fu_selected.sql",
              "091_fu_rotation_head.sql",
              "092_admission_control.sql")

# The harness fixture for the arrival source. PREMAP_SQL selects these
# columns and sc.eligible() reads market_slug, event_slug, side_norm
# and kind; nothing here influences scheduling.
PREMAP_DDL = """
    CREATE TABLE IF NOT EXISTS us_premap (
        identifier   TEXT PRIMARY KEY,
        market_slug  TEXT,
        event_slug   TEXT,
        side_norm    TEXT,
        kind         TEXT,
        sports_type  TEXT,
        team_league  TEXT,
        game_start   TIMESTAMPTZ,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


def psql(sql: str, db: str = PG_DB) -> str:
    out = subprocess.run(
        ["psql", "-h", PG_HOST, "-p", str(PG_PORT), "-U", PG_USER,
         "-d", db, "-tAc", sql],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:400])
    return out.stdout.strip()


def server_running() -> bool:
    try:
        psql("select 1", db="postgres")
        return True
    except Exception:                                          # noqa: BLE001
        return False


def reset_schema(repo_root: str) -> None:
    """Drop and rebuild from the REAL migration files, in order."""
    psql("drop database if exists %s" % PG_DB, db="postgres")
    psql("create database %s" % PG_DB, db="postgres")
    for m in MIGRATIONS:
        path = os.path.join(repo_root, "backend", "migrations", m)
        out = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", str(PG_PORT), "-U", PG_USER,
             "-d", PG_DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", path],
            capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError("migration %s failed: %s"
                               % (m, out.stderr.strip()[:400]))
    psql(PREMAP_DDL)


def seed_premap(n_markets: int) -> None:
    psql("truncate us_premap")
    psql("""
        INSERT INTO us_premap (identifier, market_slug, event_slug,
                               side_norm, kind, sports_type,
                               team_league, game_start, updated_at)
        SELECT 'id_' || g, 'mkt-' || g, 'evt-' || (g / 2),
               CASE WHEN g %% 2 = 0 THEN 'yes' ELSE 'no' END,
               'sports_nba', 'basketball', 'nba',
               now() + interval '3 hours', now()
          FROM generate_series(1, %d) g
    """ % n_markets)


class Harness:
    """Drives the real tick() and records what it actually did."""

    def __init__(self, *, repo_root, time_scale=1.0, budget_cap=None,
                 rate_limit_every=0):
        self.repo_root = repo_root
        self.time_scale = float(time_scale)
        self.budget_cap = budget_cap
        self.rate_limit_every = rate_limit_every
        self.requests = 0
        self.requests_this_tick = 0
        self.per_tick_requests = []
        self._tick_no = 0

    # ── the only substitution that touches the request path ────────
    def fake_book(self, slug, pacing=None):
        self.requests += 1
        self.requests_this_tick += 1
        if self.rate_limit_every and (
                self.requests % self.rate_limit_every == 0):
            return {"marketData": None, "feed": None,
                    "error": "HTTPStatusError", "status": 429}
        return {"marketData": {"bestBid": "0.48", "bestAsk": "0.52"},
                "feed": "replay"}

    def scaled(self, seconds):
        return max(1, int(round(seconds / self.time_scale)))


def install(harness, sc, worker):
    """Patch the three substitutions. Returns a restore callable."""
    saved = {
        "horizons": sc.HORIZONS_OBSERVABLE_S,
        "tol": sc.HORIZON_TOLERANCE_S,
        "due": sc.HORIZON_DUE_WINDOW_S,
        "early": sc.HORIZON_EARLY_ELIGIBILITY_S,
        "cadence": sc.SAMPLING_CADENCE_S,
        "not_obs": sc.HORIZONS_NOT_OBSERVABLE_S,
        "read_book": worker._read_book,
    }
    # AND THE NOT-OBSERVABLE LIST SCALES, OR IT POISONS THE RESULT.
    # HORIZONS_NOT_OBSERVABLE_S is the literal (5, 15, 30): horizons too
    # short to have a read behind them at a 60s tick. At scale=10 the
    # 300s horizon scales to 30, collides with that literal, and every
    # one of its reads came back NOT_OBSERVABLE -- 11 attempts recorded
    # as neither on-time nor late. That was a harness artifact
    # masquerading as a production result, which is the exact failure
    # this harness exists to stop. The scale must not map a real
    # horizon onto a refused one, and the assertion below enforces it.
    sc.HORIZONS_NOT_OBSERVABLE_S = tuple(
        harness.scaled(h) for h in saved["not_obs"])
    # THE SAMPLING CADENCE SCALES TOO, and it must. observation_id is
    # deterministic in (universe, market, cycle), so with the cadence
    # left at 300s every compressed tick lands in the SAME cycle, every
    # write collides on the primary key, and intake freezes at one row
    # while the scheduler reports 34 attempts. The first run of this
    # harness did exactly that. Scaling it lets the rotation advance at
    # the same rate relative to the horizons.
    sc.SAMPLING_CADENCE_S = harness.scaled(saved["cadence"])
    sc.HORIZONS_OBSERVABLE_S = tuple(
        harness.scaled(h) for h in saved["horizons"])
    sc.HORIZON_TOLERANCE_S = harness.scaled(saved["tol"])
    sc.HORIZON_DUE_WINDOW_S = harness.scaled(saved["due"])
    sc.HORIZON_EARLY_ELIGIBILITY_S = harness.scaled(saved["early"])
    worker._read_book = harness.fake_book

    collide = set(sc.HORIZONS_OBSERVABLE_S) & set(
        sc.HORIZONS_NOT_OBSERVABLE_S)
    if collide:
        raise RuntimeError(
            "time_scale=%g maps observable horizon(s) %s onto the "
            "NOT_OBSERVABLE list %s; results would be invalid"
            % (harness.time_scale, sorted(collide),
               sc.HORIZONS_NOT_OBSERVABLE_S))

    def restore():
        sc.HORIZONS_OBSERVABLE_S = saved["horizons"]
        sc.HORIZON_TOLERANCE_S = saved["tol"]
        sc.HORIZON_DUE_WINDOW_S = saved["due"]
        sc.HORIZON_EARLY_ELIGIBILITY_S = saved["early"]
        sc.SAMPLING_CADENCE_S = saved["cadence"]
        sc.HORIZONS_NOT_OBSERVABLE_S = saved["not_obs"]
        worker._read_book = saved["read_book"]

    return restore
