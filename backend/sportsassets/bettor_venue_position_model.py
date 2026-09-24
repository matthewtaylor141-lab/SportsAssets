"""WHAT AN ECONOMIC ACTION ACTUALLY DOES TO THE POSITION, PER VENUE.

Owner directive, "MAKE THE FULL EXIT POLICY OPERATIONAL" §2:

    "Translate each economic action into the venue's actual position
    model. Where opposite-side trading reduces or reverses a net
    position, account for that behavior; do not manufacture two held
    legs, a merge or capital release."

WHY THIS LAYER HAS TO EXIST, AND WHY ITS ABSENCE WAS NOT COSMETIC.

`bettor_exit_engine`, `bettor_ev_actions` and `bettor_inventory` are all
written in the TWO-TOKEN vocabulary: YES and NO are separate holdings,
`min(YES, NO)` is a MATCHED PAIR carrying locked P&L, and completing a
pair is an action that INCREASES gross exposure (`EXPOSURE_EFFECT
["COMPLETE_PAIR"] = (INCREASE, DECREASE)`). That is exactly right on
Polymarket's global CLOB, where the two legs are distinct ERC-1155
tokens and an account genuinely holds both.

IT IS NOT RIGHT ON POLYMARKET US, WHICH IS WHERE THE VALUATION PATH
PRICES. The venue keeps ONE SIGNED `netPosition` PER MARKET SLUG:

    docs/rn1-two-sided-design.md §1
        "Polymarket US keeps one signed netPosition per market slug...
         So you cannot hold both sides of a market here. Buying the
         complement of something you hold is not a second position; it
         is a sale of the first."

    live_executor.classify_exit
        "The venue holds ONE SIGNED net position per market. There is no
         way to be long and short the same market at once -- so buying
         the complementary leg of something you already hold does not
         open a second bet, it retires the first, share for share."

    pmus._norm_order
        "a BUY_SHORT reads ORDER_SIDE_SELL, short-truth 6/6" -- six
        venue receipts, not an inference.

So on PMUS the actions the exit engine prices as DISTINCT are the SAME
ORDER seen from two sides:

    DIRECT_EXIT       sell the long      -> net falls
    TAKE_COMPLEMENT   buy the short      -> net falls, by the same amount

They are still worth pricing separately, because they consume DIFFERENT
LADDERS at different prices -- that is the SwissTony lesson and it
survives intact. What does NOT survive is the INVENTORY consequence: a
filled TAKE_COMPLEMENT on PMUS leaves the book FLATTER, not holding two
legs. Recording it as a matched pair would book a locked P&L on a
position the venue says does not exist, and would then invite a MERGE
and a capital release for inventory that was never there.

AND THE CAPITAL QUESTION ANSWERS ITSELF ON THAT VENUE, WHICH IS NOT THE
SAME AS BEING ANSWERED IN OUR FAVOUR. There is no matched inventory to
release, because the reducing fill already left the book flat and
returned the capital as the ordinary consequence of a reduction. So
`capital_release` is NOT_APPLICABLE with that reason -- it is neither
claimed as available nor recorded as refused, because the action it
would apply to cannot arise. `bettor_merge` stays the authority wherever
matched inventory CAN exist, and there it still says
INSTITUTIONAL_NATIVE_MERGE_AVAILABLE = NOT_IDENTIFIED.

A REDUCTION MAY NEVER BECOME A REVERSAL. Buying 150 short against 100
long does not "exit harder"; it exits and then opens 50 of the opposite
exposure, which is a new directional bet nobody decided to take. Every
translation is CAPPED at the held quantity and the cap is reported.

THIS MODULE HOLDS NO STATE, PRICES NOTHING AND PLACES NOTHING. It maps
(action, venue, position) -> what the venue would do, and refuses by
name when the venue is not one whose model has been established.
"""

from __future__ import annotations

VERSION = "BETTOR_VENUE_POSITION_MODEL_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"

# ── the two models ───────────────────────────────────────────────────

ONE_SIGNED_NET = "ONE_SIGNED_NET_POSITION_PER_MARKET"
TWO_TOKEN = "TWO_SEPARATE_TOKENS_BOTH_HOLDABLE"

#: Venue -> model. A venue that is not here gets a REFUSAL, never a
#: default: guessing the model is how an action gets recorded as
#: creating inventory the venue would have netted away.
VENUE_MODEL = {
    "PMUS": ONE_SIGNED_NET,
    "POLYMARKET_US": ONE_SIGNED_NET,
    "PMX": ONE_SIGNED_NET,
    "POLYMARKET": TWO_TOKEN,
    "CLOB": TWO_TOKEN,
    "CHAIN": TWO_TOKEN,
}

EVIDENCE = {
    ONE_SIGNED_NET: (
        "docs/rn1-two-sided-design.md §1 ('one signed netPosition per "
        "market slug ... you cannot hold both sides of a market here'); "
        "live_executor.classify_exit ('buying the complementary leg ... "
        "retires the first, share for share'); pmus._norm_order "
        "('a BUY_SHORT reads ORDER_SIDE_SELL, short-truth 6/6' -- six "
        "venue receipts); pmus.position_side reads the signed "
        "netPosition the venue itself returns"),
    TWO_TOKEN: (
        "Polymarket's global CLOB holds YES and NO as distinct ERC-1155 "
        "tokens and an account holds both, which is what "
        "bettor_inventory's MATCHED_QTY and LOCKED_PNL describe and why "
        "DO_NOT_NET exists"),
}

R_VENUE_MODEL_NOT_ESTABLISHED = "VENUE_POSITION_MODEL_NOT_ESTABLISHED"
R_ACTION_NOT_TRANSLATABLE = "ACTION_HAS_NO_VENUE_TRANSLATION"
R_NO_HELD_QUANTITY = "NO_HELD_QUANTITY_TO_ACT_ON"

# ── what the intent does, and the one thing it decides ───────────────

BUY_LONG = "ORDER_INTENT_BUY_LONG"
BUY_SHORT = "ORDER_INTENT_BUY_SHORT"
SELL_LONG = "ORDER_INTENT_SELL_LONG"

INTENT_SELECTS = (
    "the ladder that supplies acquisition cost, and nothing else. It "
    "does NOT name the payout event -- reading the payout event off the "
    "intent is the defect migration 108 holds every earlier valuation "
    "row for")

# The economic actions this translates. Named from
# `bettor_ev_actions.CANONICAL_ACTIONS` so no second vocabulary is
# created; REDUCE is the partial form of DIRECT_EXIT and is listed
# because §2 requires "sell SOME or all of the residual".
TRANSLATABLE = (
    "HOLD",
    "HOLD_TO_SETTLEMENT",
    "DIRECT_EXIT",
    "REDUCE",
    "TAKE_COMPLEMENT",
    "POST_COMPLEMENT",
    "COMPLETE_PAIR",
    "MERGE",
    "WAIT_REQUOTE",
)

REDUCING = ("DIRECT_EXIT", "REDUCE", "TAKE_COMPLEMENT", "POST_COMPLEMENT",
            "COMPLETE_PAIR")
NON_ORDER = ("HOLD", "HOLD_TO_SETTLEMENT", "WAIT_REQUOTE", "MERGE")


def model_for(venue) -> dict:
    """Which position model this venue uses, or a named refusal."""
    key = str(venue or "").strip().upper()
    mdl = VENUE_MODEL.get(key)
    if mdl is None:
        return {"ok": False, "venue": key or None,
                "refusal": R_VENUE_MODEL_NOT_ESTABLISHED,
                "model": NOT_IDENTIFIED,
                "why": ("no position model is established for %r. It is "
                        "not defaulted: assuming the two-token model on "
                        "a netting venue records a matched pair the "
                        "venue would have flattened, and assuming the "
                        "netting model on a two-token venue reports a "
                        "capital release that never happened"
                        % (venue,)),
                "known_venues": sorted(VENUE_MODEL)}
    return {"ok": True, "venue": key, "model": mdl,
            "evidence": EVIDENCE[mdl],
            "can_hold_both_sides": mdl == TWO_TOKEN,
            "opposite_side_reduces": mdl == ONE_SIGNED_NET}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def translate(action, *, venue, held_qty, us_market_slug=None,
              requested_qty=None, held_is_long=True) -> dict:
    """What this venue would actually do, for this action and quantity.

    `held_qty` is the quantity of the exposure we hold (unsigned).
    `held_is_long` says which side of the venue's net that exposure is,
    because on a netting venue the reducing intent depends on it.

    Returns the order the venue would take (slug, intent, ladder),
    the effect on the net position, whether a second leg comes into
    existence, whether any capital is released, and every cap applied.
    """
    act = str(action or "").upper()
    vm = model_for(venue)
    out = {"version": VERSION, "action": act, "venue_model": vm,
           "intent_selects": INTENT_SELECTS}
    if not vm["ok"]:
        out.update(ok=False, refusal=vm["refusal"], why=vm["why"])
        return out
    if act not in TRANSLATABLE:
        out.update(ok=False, refusal=R_ACTION_NOT_TRANSLATABLE,
                   why=("%r is not one of the actions this layer "
                        "translates. It is refused rather than aliased "
                        "onto the nearest similar name" % (action,)),
                   translatable=list(TRANSLATABLE))
        return out

    mdl = vm["model"]
    held = abs(_f(held_qty))
    want = held if requested_qty is None else abs(_f(requested_qty))

    # ── actions that touch no book ───────────────────────────────────
    if act in NON_ORDER:
        if act == "MERGE":
            if mdl == ONE_SIGNED_NET:
                out.update(
                    ok=True, places_order=False, qty=0.0,
                    net_effect="NONE",
                    creates_second_leg=False,
                    capital_release=NOT_APPLICABLE,
                    capital_release_why=(
                        "there is no matched inventory on this venue to "
                        "merge. A reducing fill already leaves the book "
                        "flat and returns the capital as the ordinary "
                        "consequence of the reduction, so MERGE has no "
                        "object here. This is NOT a claim that the "
                        "venue offers a merge"),
                    why=("MERGE does not arise under %s" % mdl))
                return out
            out.update(
                ok=False, places_order=False, qty=0.0,
                refusal="MERGE_MECHANISM_NOT_IDENTIFIED",
                net_effect="NONE", creates_second_leg=False,
                capital_release=NOT_IDENTIFIED,
                capital_release_why=(
                    "bettor_merge: INSTITUTIONAL_NATIVE_MERGE_AVAILABLE "
                    "is NOT_IDENTIFIED. 'No mechanism has been observed' "
                    "and 'the venue has no mechanism' are different "
                    "claims and only the first is supported, so matched "
                    "capital stays shown as occupied"),
                why="no merge mechanism is established on a two-token venue")
            return out
        out.update(ok=True, places_order=False, qty=0.0,
                   net_effect="NONE", creates_second_leg=False,
                   capital_release=NOT_APPLICABLE,
                   capital_release_why=("%s touches no book, so nothing "
                                        "is released" % act),
                   why=("%s is a decision to leave the venue position "
                        "exactly as it is" % act))
        return out

    # ── reducing actions ─────────────────────────────────────────────
    if held <= 1e-9:
        out.update(ok=False, refusal=R_NO_HELD_QUANTITY, qty=0.0,
                   why=("%s reduces an exposure and none is held" % act))
        return out

    # THE CAP. A reduction may never become a reversal.
    qty = min(want, held)
    capped = qty < want - 1e-12

    if mdl == ONE_SIGNED_NET:
        # Selling the long and buying the short are the SAME reduction.
        # Which intent the venue needs depends only on which side of the
        # net we are on -- and it is the ONLY thing the intent decides.
        if act in ("DIRECT_EXIT", "REDUCE"):
            intent = SELL_LONG if held_is_long else BUY_LONG
            ladder = "BID" if held_is_long else "ASK"
            mechanism = ("sell the held side back into its own book")
        else:
            intent = BUY_SHORT if held_is_long else BUY_LONG
            ladder = "BID" if held_is_long else "ASK"
            mechanism = (
                "buy the opposite side. ON THIS VENUE THAT IS THE SAME "
                "REDUCTION as selling the held side -- 'buying NO at "
                "0.51 IS selling YES at 0.49' -- reached through the "
                "other intent, so it consumes a different ladder at a "
                "different price and produces the same inventory result")
        out.update(
            ok=True, places_order=True,
            venue_order={"us_market_slug": us_market_slug,
                         "intent": intent, "ladder_consumed": ladder,
                         "qty": qty,
                         "note": ("both sides of an aec- market share "
                                  "ONE identifier; CreateOrderParams "
                                  "carries no side field and the side "
                                  "travels only on the intent")},
            qty=qty, capped=capped, cap_basis="HELD_QUANTITY",
            requested_qty=want, held_qty=held,
            net_effect="REDUCE",
            net_after=(held - qty) * (1.0 if held_is_long else -1.0),
            fully_closes=abs(held - qty) <= 1e-9,
            creates_second_leg=False,
            second_leg_why=(
                "the venue keeps ONE signed netPosition per market, so "
                "there is no second holding to create. Recording one "
                "would book a matched pair the venue says cannot exist, "
                "and then invite a merge and a capital release for "
                "inventory that was never there"),
            matched_pair_created=False,
            locked_pnl_claimable=False,
            capital_release="AT_THE_REDUCING_FILL",
            capital_release_why=(
                "the reduction itself returns the capital, because the "
                "venue nets at the fill and the book is left flatter. "
                "This is the reduction's own consequence and NOT a "
                "merge: no redemption step is invoked and none is "
                "claimed to exist"),
            realised_at="THE_FILL, not settlement",
            mechanism=mechanism,
            reversal_refused=(
                "a request above the held quantity is capped rather than "
                "filled, because exiting past zero opens the opposite "
                "directional bet and nobody decided to take it"))
        return out

    # TWO_TOKEN: the exit engine's own vocabulary applies unchanged.
    if act in ("DIRECT_EXIT", "REDUCE"):
        out.update(
            ok=True, places_order=True,
            venue_order={"us_market_slug": us_market_slug,
                         "intent": SELL_LONG, "ladder_consumed": "BID",
                         "qty": qty},
            qty=qty, capped=capped, cap_basis="HELD_QUANTITY",
            requested_qty=want, held_qty=held,
            net_effect="REDUCE", creates_second_leg=False,
            matched_pair_created=False, locked_pnl_claimable=False,
            capital_release="AT_THE_SALE",
            capital_release_why="a sale returns the basis in cash",
            mechanism="sell the held token into its own bid")
        return out
    out.update(
        ok=True, places_order=True,
        venue_order={"us_market_slug": us_market_slug,
                     "intent": BUY_LONG, "ladder_consumed": "ASK",
                     "qty": qty,
                     "note": "the complement is a DIFFERENT token here"},
        qty=qty, capped=capped, cap_basis="UNPAIRED_REMAINDER",
        requested_qty=want, held_qty=held,
        net_effect="GROSS_UP_DIRECTIONALLY_NEUTRALISE",
        creates_second_leg=True,
        matched_pair_created=True,
        locked_pnl_claimable=True,
        capital_release=NOT_IDENTIFIED,
        capital_release_why=(
            "a matched pair is not cash on a two-token venue. Releasing "
            "it needs a merge or net the venue has not been shown to "
            "offer for this account, so the capital stays committed "
            "until settlement"),
        mechanism=("buy the complementary token, which is a SECOND "
                   "holding and occupies MORE capital while removing "
                   "outcome risk"))
    return out


def describe() -> dict:
    return {
        "version": VERSION,
        "models": {ONE_SIGNED_NET: EVIDENCE[ONE_SIGNED_NET],
                   TWO_TOKEN: EVIDENCE[TWO_TOKEN]},
        "venues": dict(VENUE_MODEL),
        "translatable": list(TRANSLATABLE),
        "intent_selects": INTENT_SELECTS,
        "why": ("the exit engine's action vocabulary is written in the "
                "two-token model. On a venue that keeps one signed net "
                "position, TAKE_COMPLEMENT and DIRECT_EXIT are the same "
                "reduction reached through different ladders -- still "
                "worth pricing separately, never worth recording as two "
                "held legs"),
        "refuses": [R_VENUE_MODEL_NOT_ESTABLISHED,
                    R_ACTION_NOT_TRANSLATABLE, R_NO_HELD_QUANTITY],
    }
