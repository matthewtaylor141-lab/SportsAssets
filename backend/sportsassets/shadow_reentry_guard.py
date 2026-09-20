"""NO RE-ENTRY INSIDE THE FROZEN HORIZON, ENFORCED BEFORE CREATION.

Owner directive 2026-09-20 §10/§11:

    "X1C has 41 re-entries, 1 inside the frozen 60S horizon, minimum
    gap 41.6s. Do not alter that historical control row... Fix
    enforcement prospectively so NO RE-ENTRY INSIDE THE FROZEN HORIZON
    is enforced before position creation."

    "X1 has 3 re-entries, 0 inside 60S, minimum gap 64.7s. Do not 'fix'
    X1 based on this. However, the rule must become actual enforcement
    rather than accidental compliance caused by the ~65s tick cadence."

THE POINT OF THIS MODULE IS THE SECOND SENTENCE. X1 has never violated
the rule -- but it has never been STOPPED from violating it either. It
complied because the sampler happens to tick every ~65s, and a polling
interval is not a risk control. Slow the tick, or land two ticks close
together after a backlog, and the same code would re-enter at 40s with
nothing to prevent it. That is exactly what X1C did once.

THE RULE IS READ FROM THE FROZEN DECLARATION, NOT RETYPED. Any lane
whose `exit_rule` contains the clause is governed, and the horizon
comes from that lane's own `horizon` field. §10: "Apply the same
literal frozen rule to any lane whose policy declares it."

THE BOUNDARY IS EXPLICIT. "inside the horizon" excludes the boundary:
a gap of exactly 60.000s is NOT inside 60s. The comparison is
`gap < horizon`, pinned in tests at 59.999 / 60.000 / 60.001, so the
semantics can never drift into an off-by-one that silently permits or
forbids a re-entry.

NOTHING HISTORICAL IS TOUCHED. The one X1C violation stays exactly as
written; this guard runs before a NEW position is created.
"""

from __future__ import annotations

from datetime import timedelta

PERMITTED = "PERMITTED"
REFUSED = "REFUSED_REENTRY_INSIDE_FROZEN_HORIZON"
NO_RULE = "NO_REENTRY_CLAUSE_IN_FROZEN_POLICY"

# The clause, verbatim from EXIT_RULE_HORIZON. A lane is governed when
# its frozen exit_rule contains it -- read, never assumed.
REENTRY_CLAUSE = "no re-entry inside the horizon"

_HORIZON_SECONDS = {"30S": 30, "60S": 60, "300S": 300}


def horizon_seconds(declaration) -> int | None:
    """The lane's own frozen horizon, in seconds."""
    raw = (declaration or {}).get("horizon")
    if raw in _HORIZON_SECONDS:
        return _HORIZON_SECONDS[raw]
    return None


def governed(declaration) -> bool:
    """Does this lane's frozen policy declare the re-entry clause?"""
    return REENTRY_CLAUSE in ((declaration or {}).get("exitRule") or "")


def check(*, declaration, market_id, at, last_opened_at) -> dict:
    """May a new position be created on this market at this instant?

    `last_opened_at` is the most recent OPEN on the same market for the
    same experiment, or None. Pure -- the caller supplies the clock and
    the prior instant, so the decision is reproducible from the ledger.

    THE KEYS ARE THE DECLARATION'S OWN. shadow_experiments.declare()
    emits camelCase (`exitRule`, `experimentId`), and reading snake_case
    here would make this guard silently inert -- it would find no
    clause, govern nothing, and refuse nothing, while every test that
    passed a hand-built dict still went green. Pinned in the tests.
    """
    experiment = (declaration or {}).get("experimentId")
    if not governed(declaration):
        return {"decision": PERMITTED, "status": NO_RULE,
                "experimentId": experiment, "marketId": market_id,
                "why": ("this lane's frozen policy does not declare the "
                        "re-entry clause, so the guard does not apply")}

    seconds = horizon_seconds(declaration)
    if seconds is None:
        # FAIL CLOSED. A lane that declares the clause but whose horizon
        # cannot be read is refused rather than waved through: the rule
        # exists and we cannot evaluate it.
        return {"decision": REFUSED, "status": REFUSED,
                "experimentId": experiment, "marketId": market_id,
                "horizonSeconds": None, "gapSeconds": None,
                "why": ("the frozen policy declares 'no re-entry inside "
                        "the horizon' but its horizon is not a "
                        "recognised value, so the rule cannot be "
                        "evaluated and the entry is refused")}

    if last_opened_at is None:
        return {"decision": PERMITTED, "status": PERMITTED,
                "experimentId": experiment, "marketId": market_id,
                "horizonSeconds": seconds, "gapSeconds": None,
                "why": "no prior position on this market"}

    gap = (at - last_opened_at).total_seconds()

    # THE BOUNDARY. "inside the horizon" is strictly less than. A gap
    # of exactly the horizon is the first permitted instant.
    inside = gap < seconds

    return {
        "decision": REFUSED if inside else PERMITTED,
        "status": REFUSED if inside else PERMITTED,
        "experimentId": experiment,
        "marketId": market_id,
        "horizonSeconds": seconds,
        "gapSeconds": round(gap, 3),
        "boundary": "gap < horizon is inside; gap == horizon is permitted",
        "earliestPermittedAt": last_opened_at + timedelta(seconds=seconds),
        "why": (
            "the frozen policy says 'no re-entry inside the horizon'; "
            "this entry is %.3fs after the last position on the same "
            "market, inside the %ds horizon" % (gap, seconds)
            if inside else None),
        "frozenClause": REENTRY_CLAUSE,
    }


LAST_OPEN_SQL = """
    SELECT experiment_id, market_id, max(opened_at) AS last_opened_at
      FROM bettor_experimental_positions
     WHERE experiment_id = $1
       AND market_id = ANY($2::text[])
     GROUP BY experiment_id, market_id
"""


async def last_opens(pool, experiment, markets) -> dict:
    if not markets:
        return {}
    rows = await pool.fetch(LAST_OPEN_SQL, experiment,
                            sorted(set(markets)))
    return {r["market_id"]: r["last_opened_at"] for r in rows}


# ── the historical record, never rewritten ───────────────────────────

HISTORICAL_VIOLATIONS_SQL = """
    WITH gaps AS (
        SELECT p.experiment_id, p.market_id, p.position_id, p.opened_at,
               EXTRACT(EPOCH FROM (p.opened_at - lag(p.opened_at) OVER (
                   PARTITION BY p.experiment_id, p.market_id
                    ORDER BY p.opened_at))) AS gap_s
          FROM bettor_experimental_positions p
    )
    SELECT experiment_id, market_id, position_id, opened_at, gap_s
      FROM gaps
     WHERE gap_s IS NOT NULL AND gap_s < 60
     ORDER BY opened_at
"""


async def historical_violations(pool) -> list:
    """Re-entries inside the horizon that already happened.

    Reported, never repaired. §10: "Do not alter that historical
    control row. Record it as the historical truth."
    """
    rows = await pool.fetch(HISTORICAL_VIOLATIONS_SQL)
    return [{"experimentId": r["experiment_id"],
             "marketId": r["market_id"],
             "positionId": r["position_id"],
             "openedAt": r["opened_at"],
             "gapSeconds": round(float(r["gap_s"]), 3),
             "preserved": True,
             "why": ("opened before the re-entry guard existed; recorded "
                     "as historical truth and never rewritten")}
            for r in rows]
