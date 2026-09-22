"""The RUNTIME half of the shared policy. Same functions, live inputs.

WHAT THIS IS FOR. `bettor_policy` holds the rules as pure functions of
decision-time state. The replay calls them from
`research/beta48/bettor_episodes.py`. This module calls the SAME
functions from the live side, so the thing that was measured is the
thing that runs. Nothing here re-decides anything: every branch below
is a translation of live state into the policy's vocabulary, or a
translation of the policy's answer back into something the loop can
journal.

────────────────────────────────────────────────────────────────────
THE POLICY PROPOSES. THE ENGINE DISPOSES. This is the part that must
not be got wrong.

`bettor_decision_engine` is the module that knows what may not be
claimed: an undated book, an unverified fee schedule, a venue
capability nobody has observed, a maker EV that is NOT_IDENTIFIED
because P_FILL is. Those refusals exist because each one was a real
defect once. A policy that returned QUOTE_BOTH and got quoted would be
routing straight past them.

So `evaluate()` runs BOTH and reports both. The engine's verdict is
authoritative: where it refuses, the proposal is recorded as
PROPOSED_BUT_REFUSED together with the engine's own reason, and the
effective action is the engine's. The policy never upgrades a refusal
and it never supplies a number the engine declined to identify.
────────────────────────────────────────────────────────────────────

NOTHING HERE PLACES AN ORDER. This module computes and returns. It
holds no adapter, no credentials, no pool and no order path, and
`max_contracts` defaults to 0.0 -- which the engine reads as "no
executable size", so even the actions it CAN identify come back sized
zero. Standing restrictions are unchanged: mirror_live=false, no real
orders, no capital authorization.

Run:  python -m pytest backend/tests/test_bettor_policy_runtime.py
"""
from __future__ import annotations

from . import bettor_decision_engine as de
from . import bettor_policy as bp

ADAPTER_VERSION = "BETTOR_POLICY_RUNTIME_V1"

# The lifecycle stage a live market is in, decided from live state
# alone. These are not new rules; they are the order in which the
# existing ones are asked, and it is the SAME order the replay loop
# walks: fills first, then the pair, then the horizon, then recovery.
S_FLAT = "FLAT"                       # nothing held, nothing working
S_WORKING = "WORKING"                 # quotes resting, no fill yet
S_UNMATCHED = "UNMATCHED"             # a leg filled, the pair is open
S_MATCHED = "MATCHED"                 # a pair exists, nothing unmatched

# What the engine said about the policy's proposal.
OK = "PROPOSAL_STANDS"
REFUSED = "PROPOSED_BUT_REFUSED"
UNSCORED = "PROPOSED_BUT_NOT_SCORED"


def book_from_engine(book: de.Book, *, tick: float = 0.01) -> bp.Book:
    """A live engine Book in the policy's terms.

    ONE-SIDED BY CONSTRUCTION, on purpose. The policy reasons about a
    single instrument's ladder -- the bid to buy YES on and the ask to
    sell it at -- because that is what the market stream delivers. The
    engine's `no_bid`/`no_ask` are a SEPARATE instrument whose book was
    usually never read; `complement_source` records which. Folding them
    in here would invent a two-sided view the venue did not give us.
    """
    return bp.Book(bid=book.yes_bid, ask=book.yes_ask, tick=tick,
                   state=book.venue_state,
                   depth_bid=(book.yes_bid_size or None),
                   depth_ask=(book.yes_ask_size or None))


def inventory_from_engine(inv: de.Inventory, *, clip: float,
                          open_yes: float = 0.0, open_no: float = 0.0,
                          elapsed_s: float = 0.0,
                          unmatched_since_s: float | None = None
                          ) -> bp.Inventory:
    """Live position keeping in the policy's terms.

    `open_yes`/`open_no` are the UNFILLED remainder of our own resting
    quotes -- which is how partial fills reach the policy. A partial
    fill is not a special case here: it is simply a state where `yes`
    is positive and `open_yes` still is too, and every rule below
    already reads both.
    """
    return bp.Inventory(yes=inv.yes_contracts, no=inv.no_contracts,
                        open_yes=open_yes, open_no=open_no,
                        clip=float(clip), elapsed_s=float(elapsed_s),
                        unmatched_since_s=unmatched_since_s)


def stage(inv: bp.Inventory) -> str:
    if inv.unmatched > 1e-9:
        return S_UNMATCHED
    if inv.matched > 1e-9:
        return S_MATCHED
    if inv.open_yes > 1e-9 or inv.open_no > 1e-9:
        return S_WORKING
    return S_FLAT


def propose(policy: bp.Policy, book: bp.Book, inv: bp.Inventory, *,
            base_size: float) -> dict:
    """What the POLICY wants to do now. No engine, no venue, no fees.

    THE ORDER IS THE REPLAY'S ORDER, and it is asked one branch at a
    time so the answer always names which rule produced it:

      UNMATCHED   -> on_fill (does the other side stay up?), the
                     inventory cap, then recovery
      MATCHED     -> release or hold
      WORKING     -> the horizon, else keep resting
      FLAT        -> admit, and if admitted, price and size the quotes

    Nothing is skipped because an earlier branch "probably" covers it.
    """
    st = stage(inv)
    out = {"adapter": ADAPTER_VERSION, "policy": bp.POLICY_VERSION,
           "policy_name": policy.name, "stage": st,
           "inventory": {"yes": inv.yes, "no": inv.no,
                         "matched": inv.matched,
                         "unmatched": inv.unmatched,
                         "open_yes": inv.open_yes, "open_no": inv.open_no},
           "steps": []}

    def _step(name, d):
        out["steps"].append({"asked": name, **d})
        return d

    if st == S_UNMATCHED:
        _step("on_fill", bp.on_fill(policy, inv))
        cap = _step("inventory_cap", bp.inventory_cap_breached(policy, inv))
        rec = _step("recovery", bp.recovery_action(policy, inv, book))
        out.update({"action": rec["decision"], "why": rec["why"],
                    "rule": rec["rule"], "provenance": rec["provenance"],
                    "alternatives": rec["alternatives"],
                    "size": inv.unmatched,
                    "cap_breached": cap["breached"]})
        return out

    if st == S_MATCHED:
        rel = _step("release", bp.release_action(policy, inv, book))
        out.update({"action": rel["decision"], "why": rel["why"],
                    "rule": rel["rule"], "provenance": rel["provenance"],
                    "alternatives": rel["alternatives"],
                    "size": inv.matched})
        return out

    if st == S_WORKING:
        expired = bp.horizon_expired(policy, inv)
        out["steps"].append({"asked": "horizon",
                             "expired": expired,
                             "elapsed_s": inv.elapsed_s,
                             "horizon_s": policy.quote_horizon_s})
        if expired:
            out.update({"action": bp.D_CANCEL_OTHER,
                        "why": "the %.0fs quoting horizon expired with "
                               "nothing filled; capital resting behind an "
                               "unfilled quote earns nothing and is the "
                               "single largest cost in the measured corpus"
                               % policy.quote_horizon_s,
                        "rule": "inventory",
                        "provenance": bp.RULES["inventory"]["provenance"],
                        "alternatives": [bp.D_HOLD_BOTH],
                        "size": inv.open_yes + inv.open_no})
        else:
            out.update({"action": bp.D_HOLD_BOTH,
                        "why": "quotes are working inside the horizon",
                        "rule": "inventory",
                        "provenance": bp.RULES["inventory"]["provenance"],
                        "alternatives": [bp.D_CANCEL_OTHER], "size": 0.0})
        return out

    adm = _step("admit", bp.admit(policy, book))
    if adm["decision"] != bp.D_QUOTE_BOTH:
        out.update({"action": adm["decision"], "why": adm["why"],
                    "rule": adm["rule"], "provenance": adm["provenance"],
                    "alternatives": adm["alternatives"], "size": 0.0})
        return out
    px = _step("quote_prices", bp.quote_prices(policy, book))
    sz = _step("quote_size", bp.quote_size(policy, book, base_size))
    out.update({"action": bp.D_QUOTE_BOTH, "why": adm["why"],
                "rule": adm["rule"], "provenance": adm["provenance"],
                "alternatives": adm["alternatives"],
                "quote_bid": px["bid"], "quote_offer": px["offer"],
                "placement_why": px["why"],
                "size": sz["size"], "size_mult": sz["mult"],
                "size_rule": sz["rule"], "size_why": sz["why"]})
    return out


# ── which engine candidate carries a policy action ───────────────────
#
# A policy action is a claim about what to do; the engine scores
# ACTIONS. This maps one to the other so the engine's verdict can be
# attached to the proposal rather than merely printed beside it. A
# policy action with no engine counterpart maps to None and is reported
# as NOT_SCORED -- never as approved.
_ENGINE_ACTION = {
    bp.D_QUOTE_BOTH: (de.MAKE_YES, de.MAKE_NO),
    bp.D_COMPLETE_PAIR: (de.PAIR_BUY,),
    bp.D_EXIT_TAKER: (de.SELL_YES, de.SELL_NO),
    bp.D_RELEASE: (de.PAIR_SELL,),
    bp.D_HOLD: (de.HOLD,),
    bp.D_HOLD_BOTH: (de.HOLD,),
    bp.D_STAND_ASIDE: (de.NO_TRADE,),
    bp.D_WAIT: (de.NO_TRADE,),
    bp.D_REST_EXIT: None,
    bp.D_CANCEL_OTHER: None,
}


def evaluate(book: de.Book, *, policy: bp.Policy,
             inventory: de.Inventory | None = None,
             base_size: float = 0.0, clip: float = 100.0,
             open_yes: float = 0.0, open_no: float = 0.0,
             elapsed_s: float = 0.0,
             unmatched_since_s: float | None = None,
             tick: float = 0.01,
             fees: de.Fees | None = None,
             max_contracts: float = 0.0,
             venue: str = "polymarket-us",
             account_class: str = "institutional") -> dict:
    """One live market state in, one auditable decision record out.

    The record carries BOTH verdicts and says which one governs. A
    reader can always see what the strategy wanted, what the engine
    permitted, and -- where those differ -- exactly why.
    """
    einv = inventory or de.Inventory()
    pbook = book_from_engine(book, tick=tick)
    pinv = inventory_from_engine(einv, clip=clip, open_yes=open_yes,
                                 open_no=open_no, elapsed_s=elapsed_s,
                                 unmatched_since_s=unmatched_since_s)

    proposal = propose(policy, pbook, pinv, base_size=base_size)
    engine = de.decide(book, inventory=einv, fees=fees,
                       max_contracts=max_contracts, venue=venue,
                       account_class=account_class)

    by_action = {c["action"]: c for c in engine.get("candidates", [])}
    want = _ENGINE_ACTION.get(proposal["action"])

    if engine.get("data_quality") == "REJECTED":
        verdict, detail = REFUSED, engine.get("reason")
        scored = []
    elif want is None:
        # AN ORDER-MANAGEMENT ACTION, not a position-changing one. The
        # engine has no candidate for "cancel" or "rest an exit"
        # because neither moves the position on its own, so there is
        # nothing for it to score -- and saying NOT_SCORED is the
        # honest answer, not a quiet approval.
        verdict, detail, scored = UNSCORED, (
            "no engine candidate corresponds to %r: it manages resting "
            "orders rather than changing the position, so the engine "
            "has nothing to price" % proposal["action"]), []
    else:
        scored = [by_action[a] for a in want if a in by_action]
        blocked = [c for c in scored
                   if c["status"] in (de.BLOCKED, de.UNSUPPORTED)]
        unident = [c for c in scored if c["status"] == de.NOT_IDENTIFIED]
        if blocked:
            verdict = REFUSED
            detail = "; ".join(
                "%s %s -- %s" % (c["action"], c["status"],
                                 c.get("blocker") or c.get("why"))
                for c in blocked)
        elif unident or not scored:
            verdict = UNSCORED
            detail = "; ".join(
                "%s NOT_IDENTIFIED -- %s" % (c["action"],
                                             c.get("blocker") or c.get("why"))
                for c in unident) or (
                    "the engine produced no candidate for %r"
                    % proposal["action"])
        else:
            verdict, detail = OK, None

    # THE EFFECTIVE ACTION IS THE ENGINE'S, ALWAYS. The proposal is
    # recorded whatever it was; it governs nothing on its own.
    return {
        "adapter": ADAPTER_VERSION,
        "market_id": book.market_id,
        "proposal": proposal,
        "engine_verdict": verdict,
        "engine_detail": detail,
        "engine_candidates_for_proposal": scored,
        "effective_action": engine.get("selected"),
        "effective_size_contracts": engine.get("size_contracts", 0.0),
        "effective_reason": engine.get("reason"),
        "data_quality": engine.get("data_quality"),
        "executable": False,
        "executable_why": (
            "this module has no order path and max_contracts is %.1f; "
            "execution sits behind the separate execution gate with "
            "mirror_live=false" % max_contracts),
        "engine": engine,
    }


def describe() -> dict:
    return {
        "adapter": ADAPTER_VERSION,
        "policy": bp.POLICY_VERSION,
        "engine": de.ENGINE_VERSION,
        "shared_with": "research/beta48/bettor_episodes.py -- both import "
                       "sportsassets.bettor_policy and call the same "
                       "functions; neither holds a copy of a rule",
        "authority": "the engine's verdict governs. The policy proposes "
                     "and is recorded; it never upgrades a refusal and "
                     "never supplies a number the engine declined to "
                     "identify",
        "places_orders": False,
        "stages": [S_FLAT, S_WORKING, S_UNMATCHED, S_MATCHED],
        "lifecycle_covered": {
            "decision_time_sizing": "quote_size, from spread ticks only",
            "partial_fills": "open_yes/open_no carry the unfilled "
                             "remainder; UNMATCHED is reached with "
                             "quotes still working",
            "inventory": "on_fill + inventory_cap_breached",
            "completion": "recovery COMPLETE_PAIR",
            "exits": "recovery REST_EXIT / EXIT_TAKER",
            "residual_exposure": "inventory.unmatched, reported on every "
                                 "record",
            "capital_recycling": "release_action -- sell both legs back, "
                                 "since no merge call has been observed",
        },
    }
