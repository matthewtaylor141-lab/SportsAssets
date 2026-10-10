"""THE RESEARCH REGISTRY: PREREGISTRATION, THE EVENT-CLUSTERED PARTITION AND
THE MULTIPLE-TESTING MEASUREMENTS OVER BETTOR'S OWN PAPER STRATEGIES.

WHY THIS EXISTS (RC6 lane ev-audit). Migration 315 created
red_team_holdout_registry and controls.holdout_registry reads it, but NOTHING
in the repository ever wrote a row, and nothing computed a probability of
backtest overfitting or a deflated Sharpe ratio: the reader never produced a
`pbo_ok` / `dsr_ok` key at all. So, in production RC5 (red_team.json
2026-10-08T20:03Z), SAMPLE_INTEGRITY was RED on
NO_EVENT_CLUSTERED_PARTITION_REGISTERED and MULTIPLE_TESTING on
NO_PREREGISTERED_CANDIDATE_SET / PBO_NOT_ACCEPTABLE / DSR_NOT_ACCEPTABLE
whatever the evidence -- two controls with no producer, so no amount of
forward evidence could ever move them. Meanwhile the strategy tournament
(revenue_reliability) ranks three PAPER strategies on every readback, which IS
a multiple test, and the control said `candidates_tested: 0`.

THE STUDY, FROZEN HERE (PLAN, hashed into PLAN_SHA; a changed plan is a new
sha and a NEW preregistration row, never an edit of the old one):

  candidates   every PAPER strategy BETTOR has declared
               (bettor_strategy_lifecycle.KNOWN_STRATEGIES), none excluded --
               the losing ones included, because a registry that lists only
               survivors is the selection bias it exists to prevent. CASH is
               the incumbent benchmark, not a mined candidate.
  independence the EVENT: the fixture of the position's group
               (paper_fills.fixture, the key the profit breakers cluster by).
  partition    a keyed hash of the event key, fixed before any measurement:
               u = int(sha256(STUDY | event)[:8], 16) / 2^32;
               train u < 0.6, test 0.6 <= u < 0.8, holdout u >= 0.8. Outcome
               blind and deterministic, so every future event lands in the
               same slice it would have at registration; the registration
               also records the snapshot of the events known then.
  holdout      NEVER read by any measurement in this module. Opening it is
               a HOLDOUT_OPEN row, at most once (the sample gate refuses a
               second).
  observation  realized PAPER P&L per settled position (intel.attribution,
               identity claimed), summed per UTC entry day per candidate,
               over the train + test events only.
  tested       a candidate is TESTED when it has at least one such
               observation; a strategy seen in the data that is not in the
               registered set is still counted as tested (and so trips
               UNREGISTERED_CANDIDATES_TESTED).
  PBO          combinatorially symmetric cross-validation (Bailey, Borwein,
               Lopez de Prado, Zhu, "The Probability of Backtest Overfitting",
               J. Computational Finance 2017): the days split into S
               contiguous blocks, S = the largest even number <= min(days,
               16) and at least 4; for every half of the blocks as
               in-sample, the in-sample best Sharpe candidate's out-of-sample
               rank; PBO = the share of splits where it falls at or below the
               out-of-sample median (logit <= 0). Needs >= 2 tested
               candidates and >= 4 days.
  DSR          the deflated Sharpe ratio (Bailey & Lopez de Prado, "The
               Deflated Sharpe Ratio", J. Portfolio Management 2014) of the
               candidate with the best full-sample Sharpe, deflated for the
               number of TESTED candidates and the variance of their Sharpe
               ratios, with the selected series' own skewness and kurtosis.
  acceptance   PBO <= 0.05 and DSR >= 0.95: the 95% confidence the rest of
               this system uses for every lower bound. THESE ARE NEW: no
               threshold existed because nothing was measured. They are
               frozen in PLAN_SHA and named for the owner to confirm or
               amend; they never move with a result.

WHAT THIS DOES NOT DO. It selects nothing, promotes nothing and grants no
authority: the tournament keeps its own rule and CASH stays the incumbent.
It writes ONE kind of row (the PREREGISTER entry, once per plan sha, in the
red-team runner's write transaction); every measurement is pure over rows
the readiness interlock already read. No order, venue, size or limit.

Pure, stdlib only, except the two async helpers at the bottom.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import datetime, timezone
from statistics import NormalDist

from .. import bettor_strategy_lifecycle as LC

VERSION = "RESEARCH_REGISTRY_V1"
STUDY = "BETTOR_PAPER_STRATEGY_SELECTION_V1"
TRAIN, TEST, HOLDOUT = "train", "test", "holdout"
PARTITION_CUTS = ((TRAIN, 0.0, 0.6), (TEST, 0.6, 0.8), (HOLDOUT, 0.8, 1.0))
CSCV_MAX_BLOCKS = 16
CSCV_MIN_BLOCKS = 4
PBO_MAX = 0.05
DSR_MIN = 0.95
EULER_GAMMA = 0.5772156649015329

PLAN = {
    "version": VERSION,
    "study": STUDY,
    "candidates": sorted(LC.KNOWN_STRATEGIES),
    "incumbent": "CASH",
    "independence_unit": ("the fixture of the position's group "
                          "(paper_fills.fixture)"),
    "partition": {"method": "sha256(study|event)[:8] / 2^32",
                  "salt": STUDY,
                  "cuts": [list(c) for c in PARTITION_CUTS]},
    "holdout_rule": ("never read by a measurement of this study; opening it "
                     "is a HOLDOUT_OPEN row, at most once"),
    "observation": ("realized PAPER P&L per settled position "
                    "(intel.attribution identity), summed per UTC entry day "
                    "per candidate, train + test events only"),
    "pbo": {"method": "CSCV", "metric": "Sharpe of daily P&L",
            "blocks": "largest even <= min(days, %d), >= %d"
                      % (CSCV_MAX_BLOCKS, CSCV_MIN_BLOCKS),
            "sharpe_compared_at_decimals": 10,
            "in_sample_tie": "first in candidate order",
            "acceptable": "pbo <= %s" % PBO_MAX},
    "dsr": {"method": "Bailey & Lopez de Prado 2014",
            "trials": "tested candidates", "acceptable": "dsr >= %s"
                                                          % DSR_MIN},
}
PLAN_SHA = hashlib.sha256(json.dumps(PLAN, sort_keys=True).encode()
                          ).hexdigest()

U_NOT_REGISTERED = "NO_PREREGISTERED_CANDIDATE_SET"
U_FEW_CANDIDATES = "FEWER_THAN_2_TESTED_CANDIDATES"
U_FEW_DAYS = "FEWER_THAN_%d_DAYS" % CSCV_MIN_BLOCKS
U_NO_VARIANCE = "NO_TESTED_CANDIDATE_HAS_VARIANCE"
U_MOMENTS = "SHARPE_VARIANCE_TERM_NOT_POSITIVE"


# ── the partition ────────────────────────────────────────────────────────

def unit_interval(event_key: str, *, salt: str = STUDY) -> float:
    h = hashlib.sha256(("%s|%s" % (salt, event_key)).encode()).hexdigest()
    return int(h[:8], 16) / float(2 ** 32)


def slice_of(event_key: str, *, salt: str = STUDY) -> str:
    u = unit_interval(str(event_key), salt=salt)
    for name, lo, hi in PARTITION_CUTS:
        if lo <= u < hi:
            return name
    return HOLDOUT


def partition(events) -> dict:
    """{train, test, holdout}: sorted event keys, disjoint by construction."""
    out = {TRAIN: [], TEST: [], HOLDOUT: []}
    for e in sorted({str(e) for e in events if e}):
        out[slice_of(e)].append(e)
    return out


def preregister_entry(*, events, implementation_sha: str, at: float) -> dict:
    """The PREREGISTER row (append-only; one per plan sha)."""
    parts = partition(events)
    return {"entry_id": "prereg:%s:%s" % (STUDY, PLAN_SHA[:16]),
            "study": STUDY, "kind": "PREREGISTER",
            "candidates": list(PLAN["candidates"]),
            "candidate_count": len(PLAN["candidates"]),
            "detail": {"plan": PLAN, "plan_sha": PLAN_SHA,
                       "partitions": parts,
                       "partition_counts": {k: len(v) for k, v in
                                            parts.items()},
                       "snapshot_at": float(at),
                       "snapshot_source": "DISTINCT paper_fills.fixture",
                       "future_events": "assigned by the same keyed hash"},
            "implementation_sha": implementation_sha or "UNKNOWN"}


# ── the statistics ───────────────────────────────────────────────────────

def sharpe(xs: list) -> float:
    """mean / sample sd; 0 for a flat zero series, +-inf for a flat non-zero
    one (a constant gain has no risk to divide by)."""
    n = len(xs)
    if n == 0:
        return 0.0
    m = sum(xs) / n
    if n < 2:
        return 0.0 if m == 0 else math.copysign(math.inf, m)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return 0.0 if m == 0 else math.copysign(math.inf, m)
    return m / math.sqrt(var)


def _ranks(vals: list) -> list:
    """Ascending ranks 1..N, ties averaged."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def blocks_for(days: int) -> int:
    s = min(int(days), CSCV_MAX_BLOCKS)
    s -= s % 2
    return s if s >= CSCV_MIN_BLOCKS else 0


#: Sharpe ratios are COMPARED at this many decimals: two candidates whose
#: ratios are mathematically equal (a common shape on sparse daily P&L) tie,
#: whatever the float rounding of the route that computed them (in PLAN).
SHARPE_TIE_DECIMALS = PLAN["pbo"]["sharpe_compared_at_decimals"]


def _tie(x: float) -> float:
    return round(x, SHARPE_TIE_DECIMALS) if math.isfinite(x) else x


def _sharpe_from_sums(cnt: int, s: float, q: float) -> float:
    """sharpe() from a count, a sum and a sum of squares (the same
    conventions: 0 for a flat zero series, +-inf for a flat non-zero one).
    A variance within float noise of zero is zero."""
    if cnt == 0:
        return 0.0
    m = s / cnt
    if cnt < 2:
        return 0.0 if m == 0 else math.copysign(math.inf, m)
    var = (q - s * s / cnt) / (cnt - 1)
    if var <= 1e-12 * max(1.0, q / cnt):
        return 0.0 if m == 0 else math.copysign(math.inf, m)
    return m / math.sqrt(var)


def pbo_cscv(matrix: list, *, blocks: int) -> dict:
    """CSCV over `matrix` (rows = time, columns = candidates, in a declared
    order). The in-sample best is the first maximum in column order.

    Each split's Sharpe ratios come from per-block sums and sums of squares
    (exactly the sample mean and variance of the concatenated rows), so a
    16-block run (12,870 splits) costs O(splits x blocks x candidates), not
    O(splits x days x candidates): it runs inside the API's readiness read."""
    t = len(matrix)
    n = len(matrix[0]) if matrix else 0
    if n < 2:
        return {"pbo": None, "why": U_FEW_CANDIDATES}
    if blocks < CSCV_MIN_BLOCKS or blocks % 2 or t < blocks:
        return {"pbo": None, "why": U_FEW_DAYS}
    base, extra = divmod(t, blocks)
    edges, at = [], 0
    for b in range(blocks):
        size = base + (1 if b < extra else 0)
        edges.append((at, at + size))
        at += size
    cnt = [e - s for s, e in edges]
    bs = [[sum(matrix[r][j] for r in range(s, e)) for j in range(n)]
          for s, e in edges]
    bq = [[sum(matrix[r][j] ** 2 for r in range(s, e)) for j in range(n)]
          for s, e in edges]
    tot_s = [sum(bs[b][j] for b in range(blocks)) for j in range(n)]
    tot_q = [sum(bq[b][j] for b in range(blocks)) for j in range(n)]
    logits, below = [], 0
    combos = list(itertools.combinations(range(blocks), blocks // 2))
    for ins in combos:
        ic = sum(cnt[b] for b in ins)
        i_s = [sum(bs[b][j] for b in ins) for j in range(n)]
        i_q = [sum(bq[b][j] for b in ins) for j in range(n)]
        is_sr = [_tie(_sharpe_from_sums(ic, i_s[j], i_q[j]))
                 for j in range(n)]
        oos_sr = [_tie(_sharpe_from_sums(t - ic, tot_s[j] - i_s[j],
                                         tot_q[j] - i_q[j]))
                  for j in range(n)]
        best = max(range(n), key=lambda j: (is_sr[j], -j))
        w = _ranks(oos_sr)[best] / (n + 1.0)
        lam = math.log(w / (1.0 - w))
        logits.append(lam)
        if lam <= 0:
            below += 1
    return {"pbo": below / len(combos), "splits": len(combos),
            "blocks": blocks, "days": t, "candidates": n,
            "median_logit": sorted(logits)[len(logits) // 2],
            "why": None}


def _moments(xs: list) -> tuple:
    n = len(xs)
    m = sum(xs) / n
    m2 = sum((x - m) ** 2 for x in xs) / n
    if m2 <= 0:
        return None, None
    m3 = sum((x - m) ** 3 for x in xs) / n
    m4 = sum((x - m) ** 4 for x in xs) / n
    return m3 / m2 ** 1.5, m4 / m2 ** 2


def expected_max_sharpe(*, trials: int, sharpe_variance: float) -> float:
    """SR0: the expected maximum Sharpe of `trials` skill-less trials whose
    Sharpe ratios vary by `sharpe_variance` (Bailey & Lopez de Prado 2014,
    eq. for SR0 with the Euler-Mascheroni constant). 0 for one trial."""
    if trials < 2 or sharpe_variance <= 0:
        return 0.0
    nd = NormalDist()
    return math.sqrt(sharpe_variance) * (
        (1 - EULER_GAMMA) * nd.inv_cdf(1 - 1.0 / trials)
        + EULER_GAMMA * nd.inv_cdf(1 - 1.0 / (trials * math.e)))


def deflated_sharpe_from_moments(*, sharpe_hat: float, observations: int,
                                 skew: float, kurtosis: float, trials: int,
                                 sharpe_variance: float) -> dict:
    """DSR = Phi((SR - SR0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2))
    with Pearson (non-excess) kurtosis g4; non-annualized SR."""
    sr0 = expected_max_sharpe(trials=trials, sharpe_variance=sharpe_variance)
    den = 1 - skew * sharpe_hat + (kurtosis - 1) / 4.0 * sharpe_hat ** 2
    if den <= 0 or observations < 2:
        return {"dsr": None, "why": U_MOMENTS, "sharpe": sharpe_hat,
                "sr0": sr0}
    z = (sharpe_hat - sr0) * math.sqrt(observations - 1) / math.sqrt(den)
    return {"dsr": NormalDist().cdf(z), "sharpe": sharpe_hat, "sr0": sr0,
            "trials": trials, "sharpe_variance": sharpe_variance,
            "skew": skew, "kurtosis": kurtosis, "days": observations,
            "why": None}


def deflated_sharpe(selected: list, *, trial_sharpes: list) -> dict:
    """DSR of the selected series given every tested candidate's Sharpe
    (the variance across trials from the finite ones)."""
    t = len(selected)
    sr = sharpe(selected)
    skew, kurt = _moments(selected) if t >= 2 else (None, None)
    if skew is None or not math.isfinite(sr):
        return {"dsr": None, "why": U_NO_VARIANCE, "sharpe": None}
    k = len(trial_sharpes)
    fin = [s for s in trial_sharpes if math.isfinite(s)]
    v = 0.0
    if len(fin) >= 2:
        mu = sum(fin) / len(fin)
        v = sum((s - mu) ** 2 for s in fin) / (len(fin) - 1)
    return deflated_sharpe_from_moments(
        sharpe_hat=sr, observations=t, skew=skew, kurtosis=kurt, trials=k,
        sharpe_variance=v)


# ── the measurement over the readiness interlock's own rows ─────────────

def _day(at) -> str | None:
    try:
        return datetime.fromtimestamp(float(at), timezone.utc).strftime(
            "%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


#: WHEN EACH OBSERVATION WAS MADE, AGAINST THE REGISTRATION (RC6.2
#: p-evcontrols; a label, no rule). The registry's PREREGISTER row is written
#: when a release carrying this module first runs, so every observation made
#: before it -- in production, all of 2026-10-01..06, the holdout events
#: included, which the lifecycle quarantines already reacted to -- is
#: RETROSPECTIVE under a "preregistered" study. The measurement keeps every
#: train + test observation (nothing leaves the population); the evidence
#: says how many were made after registration. Whether acceptance must rest
#: on those alone is the owner's decision, not added here.
BEFORE, AFTER, UNKNOWN = ("before_registration", "after_registration",
                          "registration_time_unknown")


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _when(at, reg_at: float | None) -> str:
    a = _epoch(at)
    if reg_at is None or a is None:
        return UNKNOWN
    return BEFORE if a < reg_at else AFTER


def provenance(prov: dict, reg_at: float | None) -> dict:
    n_b = prov[BEFORE]["observations"]
    n_a = prov[AFTER]["observations"]
    n_u = prov[UNKNOWN]["observations"]
    # an observation whose instant (or the registration's) is unknown is
    # neither: the share is then unknown too, never guessed
    known = n_u == 0 and (n_a + n_b) > 0
    return {
        "registered_at": reg_at,
        "basis": ("each observation's decided_at against the PREREGISTER "
                  "row's at"),
        "observations": {w: prov[w]["observations"] for w in prov},
        "days": {w: len(prov[w]["days"]) for w in prov},
        "events": {w: len(prov[w]["events"]) for w in prov},
        "holdout_events": {w: len(prov[w]["holdout_events"]) for w in prov},
        "prospective_share": (n_a / (n_a + n_b)) if known else None,
        "all_retrospective": known and n_a == 0,
        "acceptance_rule": ("UNCHANGED: PBO and DSR are measured over every "
                            "train + test observation; whether acceptance "
                            "must rest on observations made after "
                            "registration is an owner decision")}


def measure(registry: dict, attributed: list, fixtures: dict) -> dict:
    """PBO / DSR of the registered study over train + test events.

    `attributed`: intel.attribution PAPER rows (strategy, decided_at,
    group_id, realized_pnl_usd, identity_claimed); `fixtures`: group_id ->
    fixture. The holdout slice is excluded before anything is computed."""
    reg = list(registry.get("registered_candidates") or [])
    base = {"study": registry.get("study"), "plan_sha": registry.get(
        "plan_sha"), "registered": len(reg), "pbo": None, "dsr": None,
        "pbo_ok": None, "dsr_ok": None, "acceptance": {
            "pbo_max": PBO_MAX, "dsr_min": DSR_MIN}}
    if not reg:
        return dict(base, tested=[], candidates_tested=0,
                    pbo_why=U_NOT_REGISTERED, dsr_why=U_NOT_REGISTERED)
    cells: dict = {}
    excluded = {HOLDOUT: 0, "NO_EVENT": 0, "NO_DAY": 0}
    events_used: set = set()
    reg_at = _epoch(registry.get("registered_at"))
    prov = {w: {"observations": 0, "days": set(), "events": set(),
                "holdout_events": set()} for w in (BEFORE, AFTER, UNKNOWN)}
    for r in attributed or []:
        if not r.get("identity_claimed") or r.get("realized_pnl_usd") is None:
            continue
        ev = fixtures.get(r.get("group_id")) or r.get("us_market_slug")
        if not ev:
            excluded["NO_EVENT"] += 1
            continue
        when = _when(r.get("decided_at"), reg_at)
        if slice_of(str(ev)) == HOLDOUT:
            excluded[HOLDOUT] += 1
            prov[when]["holdout_events"].add(str(ev))
            continue
        d = _day(r.get("decided_at"))
        if d is None:
            excluded["NO_DAY"] += 1
            continue
        s = LC.strategy_of(r)
        try:
            pnl = float(r["realized_pnl_usd"])
        except (TypeError, ValueError):
            continue
        cells[(d, s)] = cells.get((d, s), 0.0) + pnl
        events_used.add(str(ev))
        prov[when]["observations"] += 1
        prov[when]["days"].add(d)
        prov[when]["events"].add(str(ev))
    tested = sorted({s for _d, s in cells})
    days = sorted({d for d, _s in cells})
    unregistered = [s for s in tested if s not in reg]
    out = dict(base, tested=tested, candidates_tested=len(tested),
               unregistered_tested=unregistered, days=len(days),
               events=len(events_used), excluded=excluded,
               provenance=provenance(prov, reg_at))
    matrix = [[cells.get((d, s), 0.0) for s in tested] for d in days]
    p = pbo_cscv(matrix, blocks=blocks_for(len(days)))
    out["pbo"], out["pbo_why"] = p.get("pbo"), p.get("why")
    out["pbo_detail"] = {k: p.get(k) for k in ("splits", "blocks",
                                               "median_logit")}
    if len(tested) >= 2 and days:
        cols = {s: [row[j] for row in matrix] for j, s in enumerate(tested)}
        srs = {s: sharpe(v) for s, v in cols.items()}
        live = [s for s in tested if _moments(cols[s])[0] is not None
                and math.isfinite(srs[s])]
        if live:
            best = max(live, key=lambda s: (srs[s], -tested.index(s)))
            dd = deflated_sharpe(cols[best],
                                 trial_sharpes=[srs[s] for s in tested])
            out["dsr"], out["dsr_why"] = dd.get("dsr"), dd.get("why")
            out["dsr_detail"] = dict({k: v for k, v in dd.items()
                                      if k not in ("dsr", "why")},
                                     selected=best)
        else:
            out["dsr_why"] = U_NO_VARIANCE
    else:
        out["dsr_why"] = U_FEW_CANDIDATES
    out["pbo_ok"] = (None if out["pbo"] is None
                     else bool(out["pbo"] <= PBO_MAX))
    out["dsr_ok"] = (None if out["dsr"] is None
                     else bool(out["dsr"] >= DSR_MIN))
    return out


# ── the one write, and its census ────────────────────────────────────────

async def registry_events(conn) -> list:
    return [r["fixture"] for r in await conn.fetch(
        "SELECT DISTINCT fixture FROM paper_fills WHERE fixture IS NOT NULL")]


async def ensure_preregistered(conn, *, implementation_sha: str,
                               now: float) -> dict:
    """Append this plan's PREREGISTER row once (ON CONFLICT DO NOTHING on
    its entry id). Never updates or deletes: the table refuses both."""
    if not await conn.fetchval(
            "SELECT to_regclass('red_team_holdout_registry') IS NOT NULL"):
        return {"status": "MIGRATION_315_NOT_APPLIED"}
    entry_id = "prereg:%s:%s" % (STUDY, PLAN_SHA[:16])
    if await conn.fetchval("SELECT 1 FROM red_team_holdout_registry WHERE "
                           "entry_id = $1", entry_id):
        # registered already: no census read on every pass
        return {"status": "OK", "entry_id": entry_id, "inserted": False}
    e = preregister_entry(events=await registry_events(conn),
                          implementation_sha=implementation_sha, at=now)
    got = await conn.execute(
        "INSERT INTO red_team_holdout_registry (entry_id, study, kind, "
        " candidates, candidate_count, detail, implementation_sha, at) "
        "VALUES ($1, $2, 'PREREGISTER', $3::jsonb, $4, $5::jsonb, $6, "
        " to_timestamp($7)) ON CONFLICT (entry_id) DO NOTHING",
        e["entry_id"], e["study"], json.dumps(e["candidates"]),
        e["candidate_count"], json.dumps(e["detail"]),
        e["implementation_sha"], float(now))
    return {"status": "OK", "entry_id": e["entry_id"],
            "inserted": got.endswith(" 1"),
            "partition_counts": e["detail"]["partition_counts"]}
