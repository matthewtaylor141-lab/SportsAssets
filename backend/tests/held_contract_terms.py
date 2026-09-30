"""THE HELD CONTRACT'S VENUE SETTLEMENT TERMS, FOR TESTS THAT DISPATCH.

A helper, not a test module. SYNTHETIC TERMS, labelled as such.

WHY FIXTURES NEED THIS SINCE 880377f. A funded order goes out only when the
common (one-measure) valuation, `bettor_common_valuation.value_actions`,
permits it and selects the same fixed action the limits-constrained ranking
chose. That valuation reads the held contract's OWN payout in every
settlement state from its settlement terms. Without them the void payout is
valued over [0, 100] cents and -- with the void rate unmeasured, i.e. over
[0, 1] -- no exit is robust: a void paying 100 cents would make HOLD best. So
a fixture whose premise is "this exit is sent" has to supply the terms
production supplies: the contract's venue prose, as the held-leg supplier
reads it.

Two forms, one set of terms (a baseball moneyline including extra innings,
so it cannot end level, whose void resolves 50-50):

  * `substitute_held_leg_read(monkeypatch, ...)` -- for tests driven through
    `ext_pinnacle_loop.cycle` / `_funded_service`: the production held-leg
    supplier's venue-prose read is replaced by these terms (the same
    substitution as `test_an_incident_stop_keeps_protective_exits._venue_terms`).
  * `held_leg(...)` + `SPORT_PERMITS_TIE` -- for tests that play
    `pass_once`'s supplier themselves: the facts a production supplier
    returns (`held_leg`, `sport_permits_tie`).

Nothing here touches a ranking, a candidate, a decision or a valuation.
"""
from __future__ import annotations

import dataclasses

from sportsassets import bettor_funded_hedge_supply as HSUP
from sportsassets import bettor_indirect_structures as IS

TERMS_LABEL = "SYNTHETIC_VENUE_TERMS"
#: A baseball moneyline including extra innings cannot end level.
SPORT_PERMITS_TIE = False


def held_leg(*, cost_cents: int = 60, qty: int = 10, condition_id=None):
    """The held contract as the supplier builds it from the venue's prose:
    extra innings included, void resolves 50-50 (SYNTHETIC)."""
    kw = dict(quantity=qty, cost_cents_per_unit=int(cost_cents),
              overtime=IS.OT_INCLUDED)
    if condition_id is not None:
        kw["condition_id"] = condition_id
    return dataclasses.replace(IS.BEARS_MONEYLINE, **kw)


def substitute_held_leg_read(monkeypatch, *, slug: str, event: str,
                             cost_cents: int = 60, qty: int = 10) -> None:
    """Replace ONLY the held-leg supplier's venue read with these terms."""
    leg = held_leg(cost_cents=cost_cents, qty=qty)

    async def _held(conn, *, position, prose_reader=None, now=None):
        return {"ok": True, "leg": leg, "us_market_slug": slug,
                "row": {"sports_type": "baseball", "event_slug": event},
                "built_from": TERMS_LABEL}
    monkeypatch.setattr(HSUP, "held_leg_for", _held)
