"""A MEASURED VOID RATE, RECORDED THROUGH THE PRODUCTION OBSERVATION PATH.

A helper, not a test module. EVERY OBSERVATION HERE IS SYNTHETIC, labelled
so in its price basis; it proves the machinery and is never a production
substitute.

WHY FIXTURES NEED THIS SINCE 880377f. The common valuation values every
action over the void rate's range: [0, 1] when no rate is measured, [0, the
Wilson upper 95% bound] when `bettor_pair_observations.void_rate` answers.
A decision whose premise is honest only for a plausible void rate -- a loss
taken at 0.41 when HOLD is worth 0.30 but a void would pay 0.50, or any
acquisition (which the valuation refuses on an unmeasured rate) -- needs the
rate MEASURED, from at least MIN_VOID_RATE_FIXTURES settled fixtures. This
records them with the production recorder (`PO.record`) and labels them with
the production labeller (`PO.label_pending`); only the settlement read is
substituted.
"""
from __future__ import annotations

import time

from sportsassets import bettor_pair_observations as PO
from tests import test_the_hedge_given_primary_model_prices_the_scheduled_pair as D2
from tests import test_the_pairing_model_bootstraps_from_non_funded_observations as BOOT


async def seed(conn, *, prefix: str, n_settled: int = 58, n_void: int = 2,
               structure=None, at: float | None = None) -> dict:
    """`n_settled + n_void` fixtures, each settled (the void ones declared
    void by the venue), labelled a day before `at`. Returns the rate as the
    production reader states it."""
    now = time.time() if at is None else float(at)
    st = structure if structure is not None else D2._middle()
    rows = [D2._row(st, 1.0, 0.0) for _ in range(n_settled)]
    rows += [D2._row(st, 1.0, D2.VOID_PX) for _ in range(n_void)]
    t0 = now - 2 * 86400
    prices = await D2._cohort(conn, rows, prefix=prefix, first_at=t0)
    await D2._label(conn, prices, at=t0 + 86400)
    got = await PO.void_rate(conn, through=now)
    assert got["ok"] is True, got
    return got


async def purge(conn, *, prefix: str) -> None:
    """Remove this helper's observations (append-only; triggers suspended
    for the purge's own transaction) and any candidate model a scheduled
    observation pass generated from them."""
    await BOOT._purge(conn, "fixture LIKE $1", prefix + "-fx-%")
    await conn.execute("DELETE FROM bettor_funded_models WHERE "
                       " model_id LIKE 'fmc:%:obs-%' AND state <> 'APPROVED'")
