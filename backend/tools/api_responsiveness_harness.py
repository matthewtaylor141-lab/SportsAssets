"""THE API RESPONSIVENESS HARNESS (RC6 api-responsive, 2026-10-09).

WHAT IT ANSWERS. Does the API keep answering /healthz inside the platform's
5 s deadline -- and its ordinary Command reads promptly -- while it carries
the work production runs beside them? RC5 (release 69a8a07e) did not: Render
restarted it twice on 2026-10-08 ("HTTP health check failed (timed out after
5 seconds)", 16:34:42Z and 18:39:26Z), the loop watchdog recorded ~33 stalls
>= 2 s an hour (2.3-3.5 s each in full), and the pool was saturated in the
minutes before each restart.

HOW. The REAL FastAPI app (sportsassets.api.app) is served by uvicorn on
uvloop -- production's server and loop -- in THIS process, against a REAL
Postgres (DATABASE_URL; the process's own asyncpg pool, max 10 as in
production), with the lifespan's own loops off and the loop-holders the
production watchdog named run on the same event loop instead, each at its
production size (research-sql rc6_api-responsive_workload_sizes.sql,
2026-10-09) and through its own code:

  intel          intel.calibration.load_records, 20,000 valuations
                 (intel.reads.MAX_ROWS)
  pair_obs       bettor_pair_observations.labelled, 60,000 labelled rows
  desk_sweep     pmus.list_desk_events in a worker thread, as the API runs
                 it: 15 pages, 1,430 events x 55 markets, through the real
                 venue SDK client on an in-process transport
  pinnapi_resync the feed owner's work for one resync: the 1,300-event x 25-
                 market snapshot frame (7 MB) decoded in a worker thread as
                 pinnapi_owner decodes it, then FeedCache.apply, then a
                 stream of live frames
  derek_context  paper_derek.research_model + its provenance check: the
                 newest research model (28,303 records, a 16 MB provenance)
  census         agents.coverage.census, 72,533 catalogue listings
  blockers       bettor_capital_authority.blocker_census, 83,477 refusals
  reactive       one discovery refresh's batch -- one competition's events
                 in horizon, REACTIVE_SEEDS (40) seeds, one in
                 REACTIVE_MISS_EVERY (10) unmatched -- against a 2,600-event
                 feed cache, as the ext_pinnacle cycle registers them. (The
                 production stall from this path was 2.5 s on the production
                 CPU; 40 seeds with 4 misses is ~1.6 s of the pre-RC6 scan
                 here, so the batch overstates it, if anything)
  trim           the API's periodic malloc_trim
  heartbeats     db.heartbeat x 3 services, every second (the real pool)

Each loads its rows from an in-process stand-in for the one read it makes,
so the CPU work -- where it runs and for how long -- is the code's own, and
the harness runs against any tree (--backend: 1c874c1f, 412c4962, now).

PHASES. A (load): every workload cycling. B (slow dependencies): A plus the
pool saturated by 12 sessions in pg_sleep, a CPU-heavy query on the database
and a venue page that takes 3 s. C (recovery): the work stops; the pool
frees. A client in its own thread polls /healthz every 0.25 s (10 s timeout)
and two Command reads every second (/api/command/loop-health,
/api/command/floor, 30 s timeout); a 2 ms ticker on the server loop records
every gap and which workloads were running.

BOOT. The lifespan is off (its loops would compete with the measured ones),
so what it does to the PROCESS is done here, the tree's own way, when the
tree has it: the modules it imports, api.app._share_the_gil() (the GIL
switch interval) and _install_cpu_lane() first, _freeze_boot_heap() last.
A tree without them runs as it boots.

NAMING. A sampler thread records the loop thread's innermost frames during
every gap over 50 ms (and how late its own wake-up was: a late sampler means
the GIL was held outside Python bytecode), and a gc.callbacks clock how much
of the gap the collector held. The report lists the longest gaps with both,
and the slowest /healthz answers with the loop gaps and collector time
inside them.

VERDICT (any failure fails the run):
  * HEALTHZ_DEADLINE_S 5 s -- production's own deadline, never exceeded and
    never timed out, in any phase;
  * HEALTHZ_BOUND_S 0.75 s -- every answer's time LESS the database probe's
    own wait (db_probe_s), i.e. the API's share of it. The probe's wait is
    the database's: wall clock, under its own unchanged 2 s ceiling (a new
    pooled connection to a loaded database may take most of it), so it is
    bounded separately --
  * PROBE_CEILING_S 2.5 s -- no probe past its 2 s ceiling by more than
    0.5 s. A tree without the probe's timing (before RC6) has its whole
    answer counted as the API's;
  * LOOP_HOLD_BOUND_S 0.5 s -- the longest event-loop gap;
  * RECOVERY -- after the slow dependencies clear, /healthz reads db_ok true
    and the Command reads answer (no error, p99 <= 2 s).

THE TWO LOCAL BOUNDS ARE PRODUCTION'S, SCALED BY A MEASURED FACTOR. Under
this harness's load the same code at the same sizes ran K = 1.2-3.8x slower
in production (the pre-RC6 tree 412c4962 here, against the production loop
watchdog ring of 2026-10-09 for the same functions: the coverage census of
72,533 listings held this loop 1.6-2.6 s, production's 3.0-3.5 s; the
capital blocker census of 83,477 refusals 0.7-1.2 s here, 2.5 s there;
Derek's provenance check of 28,303 records 0.7-1.0 s here, 2.3-2.5 s
there). At the worst K a 0.5 s hold here is 1.9 s there -- under the
watchdog's 2 s stall threshold -- and a 0.75 s share of an answer here is
2.85 s there: with the probe's whole 2 s ceiling on top, 4.85 s, under the
5 s deadline. (A quiet core is 6-8x faster than production -- intel
normalisation 0.42 s alone, 2.7-3.4 s there -- but this harness is never a
quiet core: it runs every workload at once on a shared machine, which is
the factor measured above. Holds of 0.1 s and 0.25 s or more are counted in
the report either way.)

USAGE (from backend/):
    DATABASE_URL=postgresql://... python -m tools.api_responsiveness_harness \\
        [--backend PATH] [--phase-a 30 --phase-b 20 --phase-c 12] [--json F]
Exit status 0 on PASS, 1 on FAIL. Nothing here calls a venue, a provider or
anything outside 127.0.0.1 and the given database; it writes only
service_heartbeats rows named harness_hb_*.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import sys
import threading
import time

HEALTHZ_DEADLINE_S = 5.0
HEALTHZ_BOUND_S = 0.75
PROBE_CEILING_S = 2.5
LOOP_HOLD_BOUND_S = 0.5
READ_P99_RECOVERY_S = 2.0
ADMIN_TOKEN = "harness-" + "h" * 40
READ_PATHS = ("/api/command/loop-health", "/api/command/floor")
REACTIVE_SEEDS = 40
REACTIVE_MISS_EVERY = 10


# ── the loop's own clock ─────────────────────────────────────────────────

class LoopSampler:
    """A 2 ms ticker on the server loop: every gap, and the longest."""

    def __init__(self):
        self.gaps: list = []          # (start, end) of every gap >= 20 ms
        self.max_gap = 0.0
        self.n = 0
        self.stop = False
        self.last = time.perf_counter()
        self.thread_id = None

    async def run(self):
        self.thread_id = threading.get_ident()
        last = self.last = time.perf_counter()
        while not self.stop:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            g = now - last
            self.n += 1
            if g > self.max_gap:
                self.max_gap = g
            if g >= 0.02:
                self.gaps.append((last, now))
            last = self.last = now


class HoldSampler(threading.Thread):
    """Names what the loop thread was running during a gap: every 5 ms, when
    the ticker's stamp is older than STALE_S, the loop thread's innermost
    frames of our code (and the innermost of any code) are sampled -- the
    production watchdog's method, at a finer grain. The sampler needs the
    GIL too: a sample that lands late (its own overrun) says something
    outside Python bytecode -- a C-level parse, a collection -- held it."""

    STALE_S = 0.05

    def __init__(self, sampler):
        super().__init__(name="harness-holds", daemon=True)
        self.sampler = sampler
        self.samples: list = []       # (gap start, loop frames, overrun)
        self.halt = threading.Event()

    @staticmethod
    def _frames(f, n=6) -> tuple:
        ours, inner = [], None
        while f is not None and len(ours) < n:
            co = f.f_code
            where = "%s:%s" % (co.co_filename.rsplit("/", 1)[-1], co.co_name)
            if inner is None:
                inner = where
            if "/sportsassets/" in co.co_filename:
                ours.append(where)
            f = f.f_back
        return (inner, tuple(ours))

    def run(self):
        last = time.perf_counter()
        while not self.halt.wait(0.005):
            now = time.perf_counter()
            overrun, last = now - last - 0.005, now
            start = self.sampler.last
            if now - start < self.STALE_S or self.sampler.thread_id is None:
                continue
            f = sys._current_frames().get(self.sampler.thread_id)
            self.samples.append((start, self._frames(f), overrun))

    def name_gap(self, a) -> dict:
        got = [s for s in self.samples if abs(s[0] - a) < 1e-9]
        if not got:
            return {"samples": 0}
        counts: dict = {}
        for _s, fr, _o in got:
            counts[fr] = counts.get(fr, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
        return {"samples": len(got),
                "max_sampler_overrun_s": round(max(o for *_x, o in got), 3),
                "loop_thread": [{"n": n, "innermost": fr[0],
                                 "ours": list(fr[1])} for fr, n in top]}


class GcClock:
    """Every garbage collection's pause (gc.callbacks), so a loop gap that
    a full collection explains is named as one -- a collection stops every
    thread, whatever code runs where."""

    def __init__(self):
        self.pauses: list = []        # (start, end, generation)
        self.full: list = []          # (pause, objects collected), gen 2
        self._t0 = None

    def __call__(self, phase, info):
        if phase == "start":
            self._t0 = time.perf_counter()
        elif self._t0 is not None:
            self.pauses.append((self._t0, time.perf_counter(),
                                info.get("generation")))
            if info.get("generation") == 2:
                self.full.append((round(self.pauses[-1][1] - self._t0, 4),
                                  info.get("collected")))
            self._t0 = None

    def within(self, a, b) -> float:
        return sum(max(0.0, min(b, e) - max(a, s))
                   for s, e, _g in self.pauses)


class Marks:
    """When each workload ran, so a gap names who was running."""

    def __init__(self):
        self.runs: dict = {}
        self.errors: dict = {}

    def add(self, name, t0, t1):
        self.runs.setdefault(name, []).append((t0, t1))

    def during(self, a, b) -> list:
        return sorted(n for n, iv in self.runs.items()
                      if any(s < b and e > a for s, e in iv))


# ── the client, in its own thread ────────────────────────────────────────

class Client(threading.Thread):
    def __init__(self, port):
        super().__init__(name="harness-client", daemon=True)
        self.base = "http://127.0.0.1:%d" % port
        self.phase = "warmup"
        self.stop = threading.Event()
        self.healthz: list = []
        self.reads: list = []

    def run(self):
        import httpx
        reader = threading.Thread(target=self._reads, daemon=True)
        reader.start()
        with httpx.Client(timeout=10.0) as c:
            while not self.stop.is_set():
                ph, t0 = self.phase, time.perf_counter()
                rec = {"phase": ph, "t": t0}
                try:
                    r = c.get(self.base + "/healthz")
                    rec.update(s=time.perf_counter() - t0,
                               status=r.status_code)
                    body = r.json()
                    rec.update(db_ok=body.get("db_ok"),
                               db_probe=body.get("db_probe"),
                               db_probe_s=body.get("db_probe_s"),
                               pool=body.get("pool"))
                except Exception as exc:                        # noqa: BLE001
                    rec.update(s=time.perf_counter() - t0, status=None,
                               error=type(exc).__name__)
                self.healthz.append(rec)
                self.stop.wait(max(0.0, 0.25 - (time.perf_counter() - t0)))
        reader.join(timeout=35)

    def _reads(self):
        import httpx
        hdr = {"X-Admin-Token": ADMIN_TOKEN}
        with httpx.Client(timeout=30.0, headers=hdr) as c:
            while not self.stop.is_set():
                for path in READ_PATHS:
                    ph, t0 = self.phase, time.perf_counter()
                    rec = {"phase": ph, "t": t0, "path": path}
                    try:
                        r = c.get(self.base + path)
                        rec.update(s=time.perf_counter() - t0,
                                   status=r.status_code)
                    except Exception as exc:                    # noqa: BLE001
                        rec.update(s=time.perf_counter() - t0, status=None,
                                   error=type(exc).__name__)
                    self.reads.append(rec)
                self.stop.wait(1.0)


# ── stand-ins for the one read each workload makes ───────────────────────

class _Rows:
    """`fetch` answers `rows` for SQL containing `needle` (else [])."""

    def __init__(self, rows, needle=None, val=None):
        self.rows, self.needle, self.val = rows, needle, val

    async def fetch(self, sql, *a):
        if self.needle is None or self.needle in sql:
            return self.rows
        return []

    async def fetchval(self, sql, *a):
        if "to_regclass" in sql:
            return True
        return self.val


def _intel_rows(n=20000):
    return [{"id": i, "version": "v1", "devig_method": "power",
             "sport_family": "baseball", "market": "h2h",
             "event_key": "ev%d" % (i % 3000), "payout_event": "HOME",
             "probability": 0.2 + (i % 60) / 100.0,
             "outcome_known": bool(i % 2), "outcome": (i % 2) or None,
             "outcome_basis": "VENUE_SETTLEMENT_PRICE",
             "buy_intent": "ORDER_INTENT_BUY_LONG",
             "us_market_slug": "s%d" % (i % 3000),
             "execution_estimate": '{"depth_usd": 120.0}',
             "executable_price": 0.5, "observed_at_epoch": 1.79e9 - i,
             "decided_at_epoch": 1.79e9 - i} for i in range(n)]


def _pair_rows(n=60000):
    feats = json.dumps({"f%02d" % i: 0.01 * i for i in range(24)})
    out = []
    for i in range(n):
        pw, hw = bool(i % 2), bool(i % 3)
        out.append({"observation_id": "obs%06d" % i, "features": feats,
                    "structure": "{}", "middle_occurred": pw and hw,
                    "fixture": "fx%d" % (i // 3), "obs_epoch": 1.0e9 + i,
                    "feature_sha": "sha%d" % i, "avail_epoch": 1.0e9 + i + 9,
                    "primary_slug": "a%d" % i,
                    "primary_side": "ORDER_INTENT_BUY_LONG",
                    "primary_settlement_price": 1.0 if pw else 0.0,
                    "hedge_slug": "b%d" % i,
                    "hedge_side": "ORDER_INTENT_BUY_LONG",
                    "hedge_settlement_price": 1.0 if hw else 0.0,
                    "primary_won": pw, "hedge_won": hw, "label_version": 1})
    return out


def _research_rows(n=28303):
    """Research label rows, each vector stored with ITS OWN identity (as
    every production writer stores it: bettor_funded_model.feature_sha; the
    provenance check refuses a vector that does not hash to it). The random
    draw the stand-in identity used to take is still drawn, so every other
    value is the one it always was."""
    from sportsassets import bettor_funded_model as FM
    rng = random.Random(7)
    out = []
    for i in range(n):
        feats = {"acquisition_price": rng.random(),
                 "payout_is_complement": float(i % 2),
                 "pinnacle_p": rng.random()}
        rng.getrandbits(64)
        out.append({
            "observation_id": "obs:%06d" % i, "fixture": "fx:%d" % (i // 3),
            "features": json.dumps(feats), "feature_sha": FM.feature_sha(feats),
            "price": rng.random(), "price_basis": "DISPLAYED",
            "cohort": "C%d" % (i % 4), "pinnacle_p": rng.random(),
            "record_purpose": "CALIBRATION_ONLY",
            "evidence_class": "RESEARCH_OBSERVATION",
            "recorded_epoch": 1.79e9 + i, "decided_epoch": 1.79e9 + i,
            "valuation_id": 1000 + i, "outcome": i % 2,
            "outcome_basis": "VENUE_SETTLEMENT",
            "outcome_epoch": 1.79e9 + 7200 + i})
    return out


class _ModelConn:
    """The model registry read (either shape: RC5's SELECT * or RC6's
    REGISTRY_SQL) and the research label read."""

    def __init__(self, labels, model_row, prov):
        self.labels, self.model_row, self.prov = labels, model_row, prov
        self.full_json = json.dumps(prov)
        slim = {k: v for k, v in prov.items()
                if k not in ("records", "decision_ids")}
        self.slim_json = json.dumps(slim)

    async def fetch(self, sql, *a):
        if "derek_research_observations" in sql:
            return self.labels
        if "bettor_funded_models" in sql:
            row = dict(self.model_row)
            if "_provenance_slim" in sql:
                row.update(_provenance_slim=self.slim_json,
                           _provenance_ids_type="array",
                           _provenance_ids=list(self.prov["decision_ids"]),
                           _provenance_ids_raw=None)
            else:
                row["training_provenance"] = self.full_json
            return [row]
        return []

    async def fetchval(self, sql, *a):
        return None


def _catalogue(n=72533):
    rng = random.Random(11)
    types = ["baseball_team_full_game_moneyline",
             "soccer_team_full_time_winner", "basketball_team_spread",
             "football_team_full_game_total", "futures_outright",
             "hockey_team_full_game_moneyline", ""]
    lg = ["mlb", "epl", "nba", "nfl", "cfb", "nhl", "atp"]
    out = []
    for i in range(n):
        k = rng.randrange(len(types))
        ev = "%s-t%d-t%d-2026-10-%02d" % (lg[k], i % 97, i % 89, 10 + i % 9)
        out.append({"market_slug": "aec-%s-%d" % (ev, i), "event_slug": ev,
                    "event_title": "Team %d vs Team %d" % (i % 97, i % 89),
                    "question": "Team %d vs Team %d" % (i % 97, i % 89),
                    "sports_type": types[k], "game_start": None,
                    "updated_at": None})
    return out


def _refusals(n=83477):
    rng = random.Random(5)
    rf = ["EDGE_BELOW_THRESHOLD", "NO_DEPTH_AT_THE_BEST_LEVEL", "STALE_BOOK",
          "PAYOFF_FLOOR_BELOW_COST"]
    return [{"strategy": "S%d" % (i % 3), "stage": "DECISION",
             "refusal": rf[i % 4], "us_market_slug": "aec-mlb-x-%d" % (
                 i % 1465), "holding_side": "LONG" if i % 2 else "SHORT",
             "fixture": "fx:%d" % (i % 700), "line": None, "scope": "FULL",
             "gross_edge_pp": rng.random(), "edge_shortfall_pp": rng.random(),
             "expected_fees_usd": 0.01, "slippage_usd": 0.0,
             "adverse_selection_usd": 0.0,
             "total_executable_ev_usd": rng.random() - 0.5,
             "refused_at": None} for i in range(n)]


def _feed_record(rng, eid, n=25, live=False):
    return {"id": eid, "type": "matchup", "isLive": live,
            "participants": [{"alignment": "home", "name": "Home %d" % eid},
                             {"alignment": "away", "name": "Away %d" % eid}],
            "startTime": "2026-10-09T18:00:00Z", "units": "Regular",
            "league": {"id": 1, "name": "L"}, "version": 3,
            "markets": [{"key": "s;0;m" if i == 0 else "s;%d;s;%d"
                         % (i % 3, i), "type": "moneyline" if i == 0
                         else "spread", "period": i % 3, "status": "open",
                         "version": 3,
                         "prices": [{"designation": "home", "points": 1.5,
                                     "price": rng.choice([-150, -120, 105])},
                                    {"designation": "away", "points": -1.5,
                                     "price": rng.choice([-140, 110, 125])}]}
                        for i in range(n)]}


def _desk_board(n_events=1430, n_markets=55, seed=21):
    rng = random.Random(seed)
    out = []
    for i in range(n_events):
        slug = "%s-t%d-t%d-2026-10-%02d" % (
            rng.choice(["mlb", "nfl", "epl", "nba"]), i, i + 1, 10 + i % 9)
        ev = {"slug": slug, "id": 100_000 + i,
              "title": "Team %d vs Team %d: O/U 3.5" % (i, i + 1),
              "startTime": "2026-10-10T18:00:00Z", "volume": rng.random(),
              "markets": []}
        for m in range(n_markets):
            ev["markets"].append({
                "question": "Team %d vs Team %d: Q%d (x)" % (i, i, m),
                "closed": False, "sportsMarketTypeV2": "MONEYLINE",
                "description": "lorem ipsum dolor sit amet " * 8,
                "marketSides": [{"identifier": "aec-%s-%d-%d" % (slug, m, s),
                                 "description": "Side %d" % s,
                                 "price": str(rng.random()),
                                 "team": {"name": "Team %d" % s},
                                 "teamId": s} for s in range(2)]})
        out.append(json.dumps(ev))
    return out


# ── the run ──────────────────────────────────────────────────────────────

def _has_module(name) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 4)


async def _main(args) -> dict:
    import uvicorn

    from sportsassets import bettor_capital_authority as CA
    from sportsassets import bettor_pair_observations as PO
    from sportsassets import db
    from sportsassets import pinnapi_feed as F
    from sportsassets import pinnapi_primary as P
    from sportsassets import pinnapi_reactive as RX
    from sportsassets import pmus
    from sportsassets import venue_pace as VP
    from sportsassets.agents import coverage as COV
    from sportsassets.agents import derek_research as DR
    from sportsassets.agents import paper_derek as PD
    from sportsassets.api import app as appmod
    from sportsassets.api import track_record as TR
    from sportsassets import bettor_funded_model as FM
    from sportsassets.intel import calibration as CAL
    # the modules the API's lifespan imports at boot (its loops are off
    # here): in production a census's `from ..workers import
    # ext_pinnacle_loop` is a dictionary lookup, never a first import
    import importlib
    for mod in ("ext_pinnacle_loop", "rn1x_shadow", "rn1x_learn_loop",
                "rn1x_model_loop"):
        try:
            importlib.import_module("sportsassets.workers." + mod)
        except Exception:                                       # noqa: BLE001
            pass

    # what the lifespan's boot does to the process, the tree's own way
    # (nothing, on a tree whose boot does nothing): the GIL switch interval
    # first, the heap freeze at the end
    switch = (appmod._share_the_gil() if hasattr(appmod, "_share_the_gil")
              else sys.getswitchinterval())
    lane = (appmod._install_cpu_lane()
            if hasattr(appmod, "_install_cpu_lane") else [])
    frozen = (appmod._freeze_boot_heap()
              if hasattr(appmod, "_freeze_boot_heap") else 0)

    tree = {"backend": args.backend,
            "desk_child_parse": hasattr(pmus, "_desk_parse_body"),
            "snapshot_off_loop": hasattr(F, "SNAPSHOT_OFFLOOP_MIN_EVENTS"),
            "health_probe_not_queued": hasattr(db, "health_probe"),
            "reactive_batch_index": hasattr(RX, "batch_index"),
            "cpu_lane": _has_module("sportsassets.cpu_lane"),
            "boot_heap_frozen": frozen, "gil_switch_interval_s": switch,
            "cpu_lane_installed_for": lane}

    # ── data, built before anything is measured ──────────────────────
    loop = asyncio.get_running_loop()
    intel_rows = _intel_rows()
    pair_rows = _pair_rows()
    research = _research_rows()

    async def _yes(conn):
        return True
    PO.has_schema = _yes
    lab = await DR.labelled_observations(_Rows(research), decision_ids=None)
    recs = FM._training_records(lab)
    prov = {"kind": FM.PROVENANCE_RECORDS,
            "decision_ids": [r["observation_id"] for r in research],
            "records_sha": FM._records_sha(recs),
            "source": FM.SOURCE_RESEARCH_OBSERVATIONS, "records": recs,
            "weighting": "EVENT_BALANCED", "n_events": lab["n_events"]}
    import datetime as _d
    model_row = {"model_id": "harness-model", "model_key": FM.KEY_ENTRY_PAYOUT,
                 "model_version": "h1", "state": FM.STATE_CANDIDATE,
                 "kernel": "k", "estimator": "e",
                 "features": ["acquisition_price"], "params": '{"w": [1]}',
                 "fit_through": _d.datetime.now(_d.timezone.utc),
                 "train_rows": len(research), "train_base_rate": None,
                 "evaluation": None, "approved_at": None, "approved_by": None,
                 "retired_at": None, "retired_reason": None,
                 "superseded_by": None,
                 "created_at": _d.datetime.now(_d.timezone.utc),
                 "trained_through": None, "outcomes_available_through": None}
    model_conn = _ModelConn(research, model_row, prov)
    catalogue = _catalogue()
    refusals = _refusals()
    rng = random.Random(1)
    snapshot = {"type": "snapshot", "stream": "prematch", "sport_id": 1,
                "ts": 1_000_000, "events": [_feed_record(rng, 1000 + i)
                                            for i in range(1300)]}
    snapshot_raw = json.dumps(snapshot)
    del snapshot
    live_frames = [json.dumps({"type": "live", "sport_id": 1, "op": "upd",
                               "ts": 1_000_100 + k,
                               "rec": _feed_record(rng, 1000 + k % 1300, n=4,
                                                   live=True)})
                   for k in range(200)]
    react = F.FeedCache()
    if hasattr(react, "offload_snapshots"):
        react.offload_snapshots = False
    rep = react.new_connection([("prematch", 1)])
    react.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
                 "ts": 1_000, "events": [_feed_record(rng, 50_000 + i, n=1)
                                         for i in range(2600)]},
                epoch=rep, received_ms=1_000)
    seeds = []
    for i in range(REACTIVE_SEEDS):
        j = 50_000 + i * 7
        hit = i % REACTIVE_MISS_EVERY
        seeds.append({"id": "ev%d" % i,
                      "home_team": ("Home %d" % j) if hit else "Nobody",
                      "away_team": ("Away %d" % j) if hit else "Noone",
                      "commence_time": "2026-10-09T18:00:00Z",
                      "sport_key": "soccer_epl"})
    board = _desk_board()
    slow = {"on": False}

    import httpx
    from polymarket_us import PolymarketUS

    def venue(request):
        q = dict(request.url.params)
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
        if slow["on"] and off and 450 <= off <= 480:
            time.sleep(3.0)               # one slow venue page (phase B)
        return httpx.Response(200, content=(
            "{\"events\": [%s]}" % ", ".join(board[off:off + lim])).encode())
    client = PolymarketUS(max_retries=0)
    client._http = httpx.Client(transport=httpx.MockTransport(venue))
    pmus._get_client = lambda: client
    VP.pace = lambda *a, **k: 0.0
    if hasattr(pmus, "_desk_parse_body"):
        await asyncio.to_thread(pmus._desk_parse_body, b"{}")  # the child

    pool = await db.get_pool()

    # ── the server ───────────────────────────────────────────────────
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        appmod.app, host="127.0.0.1", port=port, lifespan="off",
        log_level="warning", access_log=False))
    serving = loop.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    sampler = LoopSampler()
    marks = Marks()
    import gc
    gcc = GcClock()
    gc.callbacks.append(gcc)
    sampling = loop.create_task(sampler.run())
    holds = HoldSampler(sampler)
    holds.start()
    cli = Client(port)
    cli.start()
    await asyncio.sleep(1.0)

    # ── the workloads ────────────────────────────────────────────────
    async def intel():
        await CAL.load_records(_Rows(intel_rows, "FROM external_valuations"),
                               now=1.8e9)

    async def pair_obs():
        await PO.labelled(_Rows(pair_rows))

    async def desk_sweep():
        pmus._desk_cache.update(ts=0.0, retry_at=0.0)
        await asyncio.to_thread(pmus.list_desk_events)

    async def pinnapi_resync():
        msg = await asyncio.to_thread(json.loads, snapshot_raw)  # the owner
        c = F.FeedCache()
        ep = c.new_connection([("prematch", 1)])
        c.apply(msg, epoch=ep, received_ms=time.time() * 1000.0)
        for k, raw in enumerate(live_frames):
            c.apply(json.loads(raw), epoch=ep,
                    received_ms=time.time() * 1000.0)
            if k % 10 == 0:
                await asyncio.sleep(0)
        while getattr(c, "_pending", None) is not None:
            await asyncio.sleep(0.01)

    async def derek_context():
        got = await PD.research_model(model_conn, at=time.time(),
                                      verify=True)
        if not got.get("ok"):
            raise RuntimeError(str(got.get("refusal")))

    async def census():
        await COV.census(_Rows(catalogue, "FROM us_premap"), now=time.time())

    async def blockers():
        await CA.blocker_census(_Rows(refusals), "paper_acct_main")

    class _Scheduler:                     # the active scheduler's cache
        cache = react

    async def reactive():
        # as ext_pinnacle_loop registers a discovery batch, the tree's own
        # way: its cold names folded first (RC6 warm_names), ONE index
        # (RC6 batch_index), every register with it; before RC6, a scan each
        idx = None
        if hasattr(RX, "batch_index"):
            RX.ACTIVE = _Scheduler
            try:
                if hasattr(RX, "warm_names"):
                    await RX.warm_names()
                idx = RX.batch_index()
            finally:
                RX.ACTIVE = None
        for e in seeds:
            P.match_event(react, e, "soccer", index=idx)

    async def trim():
        await asyncio.to_thread(TR._malloc_trim)

    work = {"intel": (intel, 2.0), "pair_obs": (pair_obs, 2.0),
            "desk_sweep": (desk_sweep, 1.0),
            "pinnapi_resync": (pinnapi_resync, 3.0),
            "derek_context": (derek_context, 3.0), "census": (census, 2.0),
            "blockers": (blockers, 2.0), "reactive": (reactive, 1.0),
            "trim": (trim, 5.0)}
    stop_work = asyncio.Event()

    async def cycling(name, fn, pause):
        await asyncio.sleep(random.random())
        while not stop_work.is_set():
            t0 = time.perf_counter()
            try:
                await fn()
            except Exception as exc:                            # noqa: BLE001
                marks.errors.setdefault(name, []).append(
                    "%s: %s" % (type(exc).__name__, str(exc)[:120]))
            marks.add(name, t0, time.perf_counter())
            try:
                await asyncio.wait_for(stop_work.wait(), pause)
            except asyncio.TimeoutError:
                pass

    hb = {"ok": 0, "failed": 0, "s": []}

    async def heartbeats(i):
        n = 0
        while not stop_work.is_set():
            t0 = time.perf_counter()
            try:
                await db.heartbeat("harness_hb_%d" % i, "ok", {"n": n})
                hb["ok"] += 1
            except Exception:                                   # noqa: BLE001
                hb["failed"] += 1
            hb["s"].append(time.perf_counter() - t0)
            n += 1
            try:
                await asyncio.wait_for(stop_work.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    phases = {}
    cli.phase = "A"
    phases["A"] = time.perf_counter()
    tasks = [loop.create_task(cycling(n, f, p)) for n, (f, p) in work.items()]
    tasks += [loop.create_task(heartbeats(i)) for i in range(3)]
    await asyncio.sleep(args.phase_a)

    # B: the slow dependencies, on top of the load
    cli.phase = "B"
    phases["B"] = time.perf_counter()
    slow["on"] = True
    stop_sat = asyncio.Event()

    async def saturate(q):
        while not stop_sat.is_set():
            try:
                await pool.fetchval(q)
            except Exception:                                   # noqa: BLE001
                await asyncio.sleep(0.1)
    sat = [loop.create_task(saturate("SELECT pg_sleep(2.5)"))
           for _ in range(12)]
    sat.append(loop.create_task(saturate(
        "SELECT count(*) FROM generate_series(1, 3000000)")))
    await asyncio.sleep(args.phase_b)

    # C: everything stops; the pool frees
    cli.phase = "C"
    phases["C"] = time.perf_counter()
    stop_work.set()
    stop_sat.set()
    slow["on"] = False
    await asyncio.gather(*tasks, *sat, return_exceptions=True)
    phases["C_settled"] = time.perf_counter()
    await asyncio.sleep(args.phase_c)
    cli.stop.set()
    await asyncio.to_thread(cli.join, 40)
    sampler.stop = True
    await sampling
    holds.halt.set()
    server.should_exit = True
    await serving
    if hasattr(pmus, "shutdown_desk_parse"):
        pmus.shutdown_desk_parse()
    await db.close_pool()
    gc.callbacks.remove(gcc)
    return _report(args, tree, cli, sampler, marks, hb, phases, gcc, holds)


def _report(args, tree, cli, sampler, marks, hb, phases, gcc,
            namer=None) -> dict:
    out = {"tree": tree, "bounds": {
        "healthz_deadline_s": HEALTHZ_DEADLINE_S,
        "healthz_bound_s": HEALTHZ_BOUND_S,
        "probe_ceiling_s": PROBE_CEILING_S,
        "loop_hold_bound_s": LOOP_HOLD_BOUND_S,
        "read_p99_recovery_s": READ_P99_RECOVERY_S},
        "phases": {}, "workloads": {}, "reasons": []}
    for ph in ("A", "B", "C"):
        hz = [r for r in cli.healthz if r["phase"] == ph]
        ok = [r["s"] for r in hz]
        rd = {}
        for path in READ_PATHS:
            xs = [r for r in cli.reads if r["phase"] == ph
                  and r["path"] == path]
            rd[path] = {"n": len(xs), "p50_s": _q([r["s"] for r in xs], .5),
                        "p99_s": _q([r["s"] for r in xs], .99),
                        "max_s": _q([r["s"] for r in xs], 1.0),
                        "errors": sum(1 for r in xs
                                      if r.get("status") != 200)}
        probes = {}
        for r in hz:
            probes[str(r.get("db_probe"))] = probes.get(
                str(r.get("db_probe")), 0) + 1
        out["phases"][ph] = {
            "healthz": {"n": len(hz), "p50_s": _q(ok, .5),
                        "p99_s": _q(ok, .99), "max_s": _q(ok, 1.0),
                        "errors": sum(1 for r in hz
                                      if r.get("status") != 200),
                        "db_ok_false": sum(1 for r in hz
                                           if r.get("db_ok") is False),
                        "db_probe": probes},
            "reads": rd}
    holds = sorted(((b - a, a, b) for a, b in sampler.gaps), reverse=True)
    out["loop"] = {"ticks": sampler.n, "max_hold_s": round(sampler.max_gap, 4),
                   "holds_ge_100ms": sum(1 for h in holds if h[0] >= 0.1),
                   "holds_ge_250ms": sum(1 for h in holds if h[0] >= 0.25),
                   "top": [{"s": round(h[0], 4),
                            "gc_s": round(gcc.within(h[1], h[2]), 4),
                            "during": marks.during(h[1], h[2]),
                            "holder": (namer.name_gap(h[1]) if namer
                                       else None)}
                           for h in holds[:12]]}
    # the slowest answers, and what the loop and the collector did meanwhile
    slow = sorted((r for r in cli.healthz if r["phase"] in ("A", "B", "C")),
                  key=lambda r: -r["s"])[:6]
    out["slowest_healthz"] = [{
        "phase": r["phase"], "s": round(r["s"], 4),
        "db_probe": r.get("db_probe"), "db_probe_s": r.get("db_probe_s"),
        "pool": r.get("pool"),
        "loop_gaps_s": round(sum(max(0.0, min(b, r["t"] + r["s"])
                                     - max(a, r["t"]))
                                 for a, b in sampler.gaps), 4),
        "gc_s": round(gcc.within(r["t"], r["t"] + r["s"]), 4),
        "during": marks.during(r["t"], r["t"] + r["s"])} for r in slow]
    t0 = phases["A"]
    gp = [(e - s, g) for s, e, g in gcc.pauses if s >= t0]
    out["gc"] = {"collections": len(gp),
                 "total_s": round(sum(d for d, _ in gp), 3),
                 "max_pause_s": round(max((d for d, _ in gp), default=0.0), 4),
                 "gen2": sum(1 for _, g in gp if g == 2),
                 "gen2_max_s": round(max((d for d, g in gp if g == 2),
                                         default=0.0), 4),
                 "gen2_pause_and_collected": gcc.full[-24:]}
    for n, iv in marks.runs.items():
        d = [b - a for a, b in iv]
        out["workloads"][n] = {"runs": len(iv), "mean_s": _q(d, .5),
                               "max_s": _q(d, 1.0),
                               "errors": (marks.errors.get(n) or [])[:3]}
    out["heartbeats"] = {"ok": hb["ok"], "failed": hb["failed"],
                         "p99_s": _q(hb["s"], .99), "max_s": _q(hb["s"], 1.0)}
    # ── verdict ──────────────────────────────────────────────────────
    allz = [r for r in cli.healthz if r["phase"] in ("A", "B", "C")]
    worst = max((r["s"] for r in allz), default=None)
    if not allz:
        out["reasons"].append("NO_HEALTHZ_SAMPLES")
    if any(r.get("status") != 200 for r in allz):
        out["reasons"].append("HEALTHZ_UNANSWERED:%d" % sum(
            1 for r in allz if r.get("status") != 200))
    if worst is not None and worst > HEALTHZ_DEADLINE_S:
        out["reasons"].append("HEALTHZ_OVER_THE_5S_DEADLINE:%.2fs" % worst)
    share = max((r["s"] - float(r.get("db_probe_s") or 0.0)
                 for r in allz), default=None)
    out["healthz_api_share_max_s"] = None if share is None else round(
        share, 4)
    if share is not None and share > HEALTHZ_BOUND_S:
        out["reasons"].append("HEALTHZ_OVER_BOUND:%.2fs" % share)
    probe = max((float(r.get("db_probe_s") or 0.0) for r in allz),
                default=0.0)
    if probe > PROBE_CEILING_S:
        out["reasons"].append("PROBE_PAST_ITS_CEILING:%.2fs" % probe)
    if sampler.max_gap > LOOP_HOLD_BOUND_S:
        out["reasons"].append("LOOP_HELD:%.3fs" % sampler.max_gap)
    settled = phases["C_settled"] + 2.0
    late = [r for r in cli.healthz if r["phase"] == "C" and r["t"] > settled]
    if not late or any(r.get("db_ok") is not True for r in late):
        out["reasons"].append("NO_RECOVERY:db_ok")
    rec = [r for r in cli.reads if r["phase"] == "C" and r["t"] > settled]
    if not rec or any(r.get("status") != 200 for r in rec):
        out["reasons"].append("NO_RECOVERY:reads")
    elif (_q([r["s"] for r in rec], .99) or 0) > READ_P99_RECOVERY_S:
        out["reasons"].append("SLOW_RECOVERY:reads")
    missing = [n for n in ("intel", "pair_obs", "desk_sweep",
                           "pinnapi_resync", "derek_context", "census",
                           "blockers", "reactive", "trim")
               if not out["workloads"].get(n, {}).get("runs")]
    if missing:
        out["reasons"].append("WORKLOAD_NEVER_RAN:%s" % ",".join(missing))
    failed = [n for n, w in out["workloads"].items() if w["errors"]]
    if failed:
        out["reasons"].append("WORKLOAD_RAISED:%s" % ",".join(sorted(failed)))
    out["verdict"] = "PASS" if not out["reasons"] else "FAIL"
    return out


def main(argv=None) -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--backend", default=here)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--phase-a", type=float, default=30.0)
    p.add_argument("--phase-b", type=float, default=20.0)
    p.add_argument("--phase-c", type=float, default=12.0)
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    if not args.dsn.startswith("postgres"):
        print("refused: DATABASE_URL (a real Postgres) is required")
        return 2
    args.backend = os.path.abspath(args.backend)
    os.environ["DATABASE_URL"] = args.dsn
    os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
    sys.path.insert(0, args.backend)
    try:
        import uvloop
        loop = uvloop.new_event_loop()
    except ImportError:                                         # pragma: no cover
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        report = loop.run_until_complete(_main(args))
    finally:
        loop.close()
    text = json.dumps(report, indent=1, default=str)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    print(text)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
