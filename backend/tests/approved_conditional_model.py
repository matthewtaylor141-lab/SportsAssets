"""AN APPROVED HEDGE-GIVEN-PRIMARY MODEL, A MEASURED VOID RATE AND A PASSING
CALIBRATION ROW -- THE INPUTS A DISTRIBUTION-PRICED ACQUISITION NEEDS.

A helper, not a test module. EVERYTHING HERE IS SYNTHETIC TEST EVIDENCE:
observations generated from a stated rule (given the held side wins, the
hedge wins 3 in 10), labelled synthetic in their price basis, recorded by the
production recorder (`bettor_pair_observations.record`), labelled by the
production labeller (`label_pending`) with only the settlement read
substituted, generated into a CANDIDATE by the registry
(`bettor_funded_model.generate_candidate`), evaluated on prospective
event-balanced evidence (`evaluate`) and promoted ONLY by `promote` with a
named approver. The calibration row is for a TEST source version, so it can
never open the gate for a real source. Nothing is a production substitute.

WHY FIXTURES NEED THIS SINCE 880377f. A funded acquisition is dispatched only
when the common valuation permits it, and that valuation admits an
acquisition only when it was priced through the payout-state distribution
(its candidate carries `cv_acquisition`) on a MEASURED void rate. A hedge
priced on a hand-written region measure is correctly refused
(THE_COMMON_VALUATION_...). The observations below also measure the void
rate (71 settled fixtures, 3 declared void).
"""
from __future__ import annotations

import time

from sportsassets import bettor_funded_model as FMD
from tests import test_the_hedge_given_primary_model_prices_the_scheduled_pair as D2
from tests import test_the_pairing_model_bootstraps_from_non_funded_observations as BOOT

KEY = FMD.KEY_HEDGE_GIVEN_PRIMARY
#: The TEST source version whose calibration row this seeds.
CAL_SOURCE = D2.CAL_SOURCE
APPROVER = "owner@test (SYNTHETIC APPROVAL OF SYNTHETIC EVIDENCE)"
LABEL = D2.LABEL


def primary_source(*, payout_event: str, valuation_row_id=1) -> dict:
    """The HOLD probability's source as `_primary_probability_for` states it
    (SYNTHETIC): stated for the outcome the held leg pays on, record checked,
    from the TEST source version the calibration row is for."""
    return {"from": "hold_ranking.HOLD.value_per_contract",
            "is": LABEL, "record_checked": True,
            "valuation_row_id": valuation_row_id,
            "version": CAL_SOURCE,
            "probability_event": payout_event,
            "payout_event_held": payout_event}


async def approve(conn, *, prefix: str = "xcacq") -> dict:
    """Train, evaluate prospectively and promote one KEY_HEDGE_GIVEN_PRIMARY
    model on the Bears moneyline + Panthers +4.5 middle (the
    `bettor_indirect_structures` fixture pair); seed the TEST source's
    calibration row. Returns the promoted model and the void rate."""
    from sportsassets import bettor_pair_observations as PO

    await purge(conn, prefix=prefix)
    mid, loseb = D2._middle(), D2._lose_binary()
    gen, _, _ = await D2._train_and_generate(
        conn, D2._training_rows(mid, loseb), prefix=prefix)
    assert gen.get("generated") is True, gen
    await D2._prospective(conn, D2._eval_rows(mid, loseb), prefix=prefix)
    ev = await FMD.evaluate(conn, model_id=gen["model_id"])
    assert ev["ok"] is True, ev
    prom = await FMD.promote(conn, model_id=gen["model_id"],
                             approved_by=APPROVER)
    assert prom["ok"] is True, prom
    await D2._seed_calibration(conn)
    void = await PO.void_rate(conn, through=time.time())
    assert void["ok"] is True, void
    return {"model": prom["model"], "void": void, "model_id": gen["model_id"]}


async def calibration(conn) -> dict:
    """The TEST source's calibration, read by the worker's own reader."""
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    return await LOOP.source_calibration(conn, CAL_SOURCE)


async def purge(conn, *, prefix: str = "xcacq") -> None:
    """This helper's observations, models and calibration row."""
    await BOOT._purge(conn, "fixture LIKE $1", prefix + "%")
    await conn.execute("DELETE FROM external_source_calibration "
                       " WHERE source_version=$1", CAL_SOURCE)
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key=$1 "
                       "   OR model_id LIKE 'fmc:%:obs-%'", KEY)
