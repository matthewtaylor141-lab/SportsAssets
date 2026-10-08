"""THE PAIR SETTLEMENT-DIFFERENCE POLICY: what a PAIR of contracts pays, per
settlement outcome, priced explicitly -- and refused by name where a payout
rule is unknown.

OWNER DIRECTIVE (RC5, 2026-10-08): "Implement the explicit priced
settlement-difference policy; do not assume cancelled-game fair prices across
separate markets sum to $1. Unknown payout compatibility stays refused."

THE ASSUMPTION THIS FORBIDS. Every venue listing of the leagues we map says,
in its own words, that a game "delayed, postponed, or suspended and not
rescheduled to a date within two weeks ... will settle to the last fair
market price" (tests/fixtures/pmus_basketball_hockey_winner_listings_
2026_10_06.json; bettor_nfl_settlement.Q_VENUE_POSTPONED), and the Kalshi
game rulebooks state the same for a game not completed in their window
(kalshi_contract_terms, golden source record KT01: Yankees YES and Rays NO
"differ only in the fair-price states"). That price is a property of ONE
market's order book at an instant nobody names in advance. A market's YES
and its own NO complement it exactly (FP[x] + (1 - FP[x]) = $1), but the
fair prices of two SEPARATE markets -- Kalshi's Yankees market and its Rays
market, or a Kalshi market and a Polymarket US one -- are two different
numbers in [0, 1] each. Nothing any venue publishes says they sum to $1,
so a pair holding one of each is worth anything in [0, 2] in that state,
and its GUARANTEED value there is the lower end.

WHAT THE POLICY IS. For one contract pair over the canonical settlement
states (canonical_claims.states_for: results x delay bands x never
completed), each leg's payout RULE in each state is read off its token
(canonical_claims: "0" / "1" / "0.5", FP[m], 1-FP[m], SB[m], IMPOSSIBLE,
None) and every state is assigned one OUTCOME CLASS:

    NORMAL             the game completes ordinarily (<RESULT>_WIN, DRAW,
                       TIE, PUSH): the two legs must sum to exactly the
                       complement payout, or the pair is no complement at
                       all (a payoff difference, never a settlement one);
    POSTPONED          completed after a delay, inside or beyond a venue's
                       window (<RESULT>@COMPLETED_AFTER_DELAY_<band>);
    CANCELLED_OR_VOID  never completed (NEVER_COMPLETED / VOID);
    PARTIAL            started and stopped, graded on partial play or a
                       scalar: either enumerated as its own states, or
                       FOLDED into the states above ONLY where both legs
                       name the same official resolution source (that
                       source alone decides whether a shortened game is
                       official, so both legs see the same state) -- else
                       refused: its grading is unknown.

Per state the pair's payout is PRICED as an interval [lo, hi] per matched
set:

    fixed + fixed                    exact sum
    FP[m] + 1-FP[m], one market m    exactly $1 (the market's own pool)
    any fair price of a separate     each such term in [0, 1]: lo = the
      market (FP[m] + FP[n], FP[m]     fixed part, hi = fixed part + one
      + 1-FP[n], FP[m] + fixed)        per fair-price term -- NEVER $1
    stake back SB[m]                 the leg's own entry price when given,
                                     else refused (no constant exists)
    unknown (None) on either leg     REFUSED, named by leg and state

and the verdict is

    EXACT_COMPLEMENT              every state pays exactly the complement
                                  payout: no settlement difference;
    PRICED_SETTLEMENT_DIFFERENCE  ordinary completion is exact, every
                                  exceptional state is priced, at least one
                                  differs: `guaranteed_floor` (the minimum
                                  lo over every state) is the only payout a
                                  guarantee may use, and each difference is
                                  itemised by state and class;
    REFUSED                       any payout rule unknown, any class not
                                  enumerated, ordinary completion not a
                                  complement, or partial play ungraded.

A priced difference can also carry an EXPECTED lower bound, never a
guarantee: with q_hi the exceptional-state rate bound of
bettor_settlement_difference_policy (the measured venue settlements, floored
at the 0-of-40 exact upper bound), E[pair] >= target - q_hi x (target -
min exceptional lo), whatever the fair prices turn out to be.

PURE: standard library and the pure rate table of
bettor_settlement_difference_policy. No order, cancel, sizing, capital or
authority path; it prices and refuses, nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

POLICY_ID = "PAIR_SETTLEMENT_DIFFERENCE_POLICY"
VERSION = "PAIR_SETTLEMENT_DIFFERENCE_POLICY_V1"

C_NORMAL = "NORMAL"
C_POSTPONED = "POSTPONED"
C_CANCELLED = "CANCELLED_OR_VOID"
C_PARTIAL = "PARTIAL"
CLASSES = (C_NORMAL, C_POSTPONED, C_CANCELLED, C_PARTIAL)
#: the classes every pair's state space must enumerate (PARTIAL may instead
#: be folded on a shared official source)
REQUIRED_CLASSES = (C_NORMAL, C_POSTPONED, C_CANCELLED)

EXACT = "EXACT_COMPLEMENT"
PRICED = "PRICED_SETTLEMENT_DIFFERENCE"
REFUSED = "REFUSED"

RULE_FIXED = "FIXED"
RULE_FAIR_PRICE = "FAIR_PRICE"
RULE_FAIR_PRICE_COMPLEMENT = "FAIR_PRICE_COMPLEMENT"
RULE_STAKE_BACK = "STAKE_BACK"
RULE_IMPOSSIBLE = "IMPOSSIBLE"
RULE_UNKNOWN = "UNKNOWN"

B_FIXED = "FIXED_PAYOUTS"
B_SAME_MARKET = "ONE_MARKETS_FAIR_PRICE_AND_ITS_OWN_COMPLEMENT"
B_SEPARATE = "SEPARATE_MARKET_FAIR_PRICE_BOUNDED_NEVER_ASSUMED_COMPLEMENTARY"
B_IMPOSSIBLE = "IMPOSSIBLE_ON_BOTH_LEGS"
B_PARTIAL_FOLDED = "PARTIAL_PLAY_GRADED_BY_THE_SAME_OFFICIAL_SOURCE"

R_RULE_UNKNOWN = "SETTLEMENT_PAIR_PAYOUT_RULE_UNKNOWN"
R_TOKEN_UNREADABLE = "SETTLEMENT_PAIR_PAYOUT_TOKEN_UNREADABLE"
R_IMPOSSIBLE_ONE_LEG = "SETTLEMENT_PAIR_STATE_IMPOSSIBLE_ON_ONE_LEG_ONLY"
R_STAKE_BACK_UNPRICED = "SETTLEMENT_PAIR_STAKE_BACK_WITHOUT_AN_ENTRY_PRICE"
R_NOT_COMPLEMENT = "SETTLEMENT_PAIR_NOT_COMPLEMENTARY_IN_ORDINARY_COMPLETION"
R_CLASS_NOT_ENUMERATED = "SETTLEMENT_PAIR_OUTCOME_CLASS_NOT_ENUMERATED"
R_PARTIAL_UNKNOWN = "SETTLEMENT_PAIR_PARTIAL_PLAY_GRADING_NOT_ESTABLISHED"
#: NOT a payout-rule refusal: the pair WAS priced, and its guaranteed floor
#: is below the complement payout -- no structure may treat it as one
R_PRICED_NOT_A_COMPLEMENT = \
    "SETTLEMENT_PAIR_PRICED_FLOOR_BELOW_THE_COMPLEMENT_PAYOUT"
REFUSALS = (R_RULE_UNKNOWN, R_TOKEN_UNREADABLE, R_IMPOSSIBLE_ONE_LEG,
            R_STAKE_BACK_UNPRICED, R_NOT_COMPLEMENT, R_CLASS_NOT_ENUMERATED,
            R_PARTIAL_UNKNOWN)

ONE = Decimal(1)
ZERO = Decimal(0)

_CANCELLED_NAMES = frozenset({"NEVER_COMPLETED", "VOID", "CANCELLED",
                              "CANCELED", "ABANDONED"})
_PARTIAL_WORDS = ("PARTIAL", "SUSPENDED", "CALLED_GAME", "SHORTENED")
_POSTPONED_NAMES = frozenset({"POSTPONED"})


@dataclass(frozen=True)
class Leg:
    """One leg of a pair: its executable identity, its payout token in every
    canonical state, the official source its rules grade by (None or an
    UNSTATED:... placeholder = not stated) and, for a stake-back state, the
    price it was bought at."""
    venue: str
    market_id: str
    side: str
    vector: dict
    resolution_source: str | None = None
    entry_price: Decimal | None = None

    @property
    def key(self) -> tuple:
        return (self.venue, self.market_id, self.side)


def outcome_class(state) -> str:
    """The settlement-outcome class of one canonical state name. Pure."""
    s = str(state or "").upper()
    if s in _CANCELLED_NAMES:
        return C_CANCELLED
    if any(w in s for w in _PARTIAL_WORDS):
        return C_PARTIAL
    if "@COMPLETED_AFTER_DELAY" in s or s in _POSTPONED_NAMES:
        return C_POSTPONED
    return C_NORMAL


def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool) or isinstance(v, float):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def leg_rule(leg: Leg, state) -> dict:
    """{rule, lo, hi, market, token, refusal?} for one leg in one state."""
    tok = (leg.vector or {}).get(state)
    out = {"token": tok, "market": None, "lo": None, "hi": None}
    if tok is None:
        return dict(out, rule=RULE_UNKNOWN, refusal=R_RULE_UNKNOWN)
    t = str(tok)
    if t == "IMPOSSIBLE":
        return dict(out, rule=RULE_IMPOSSIBLE)
    for prefix, rule in (("1-FP[", RULE_FAIR_PRICE_COMPLEMENT),
                         ("FP[", RULE_FAIR_PRICE)):
        if t.startswith(prefix) and t.endswith("]"):
            # a fair price is some number in [0, 1]: its bounds, never a
            # point -- whichever side of its market this leg holds
            return dict(out, rule=rule, market=t[len(prefix):-1],
                        lo=ZERO, hi=ONE)
    if t.startswith("SB[") and t.endswith("]"):
        px = _dec(leg.entry_price)
        if px is None or not ZERO <= px <= ONE:
            return dict(out, rule=RULE_STAKE_BACK, market=t[3:-1],
                        refusal=R_STAKE_BACK_UNPRICED)
        return dict(out, rule=RULE_STAKE_BACK, market=t[3:-1], lo=px, hi=px)
    d = _dec(t)
    if d is None or not ZERO <= d <= ONE:
        return dict(out, rule=RULE_UNKNOWN, refusal=R_TOKEN_UNREADABLE)
    return dict(out, rule=RULE_FIXED, lo=d, hi=d)


def _fair(r) -> bool:
    return r["rule"] in (RULE_FAIR_PRICE, RULE_FAIR_PRICE_COMPLEMENT)


def price_state(a: Leg, b: Leg, state) -> dict:
    """The pair's payout in one state: {state, class, a, b, lo, hi, exact,
    basis, refusals}. Pure."""
    ra, rb = leg_rule(a, state), leg_rule(b, state)
    row = {"state": state, "class": outcome_class(state), "a": ra, "b": rb,
           "lo": None, "hi": None, "exact": False, "basis": None,
           "refusals": []}
    for leg, r in (("a", ra), ("b", rb)):
        if r.get("refusal"):
            row["refusals"].append({
                "code": r["refusal"], "leg": leg, "state": state,
                "detail": "%s:%s:%s token %r" % (
                    (a if leg == "a" else b).key + (r["token"],))})
    if row["refusals"]:
        return row
    imp = [r["rule"] == RULE_IMPOSSIBLE for r in (ra, rb)]
    if all(imp):
        return dict(row, lo=None, hi=None, exact=True, basis=B_IMPOSSIBLE)
    if any(imp):
        row["refusals"].append({
            "code": R_IMPOSSIBLE_ONE_LEG, "leg": "a" if imp[0] else "b",
            "state": state,
            "detail": "one leg's rules say %s cannot happen, the other's "
                      "pay in it" % state})
        return row
    same_pool = (a.venue == b.venue and a.market_id == b.market_id)
    if _fair(ra) and _fair(rb) and same_pool and ra["market"] == \
            rb["market"] == str(a.market_id) and ra["rule"] != rb["rule"]:
        # FP[x] + (1 - FP[x]): the one market's own pool pays exactly $1
        return dict(row, lo=ONE, hi=ONE, exact=True, basis=B_SAME_MARKET)
    lo = ra["lo"] + rb["lo"]
    hi = ra["hi"] + rb["hi"]
    if _fair(ra) or _fair(rb):
        return dict(row, lo=lo, hi=hi, exact=False, basis=B_SEPARATE)
    return dict(row, lo=lo, hi=hi, exact=(lo == hi), basis=B_FIXED)


def _stated(src) -> str | None:
    s = str(src or "").strip()
    return None if not s or s.upper().startswith("UNSTATED") else s


def evaluate_pair(a: Leg, b: Leg, states, *, target=ONE,
                  sport_family: str | None = None) -> dict:
    """THE POLICY for one contract pair over `states`. Pure.

    Returns {policy_id, version, verdict, payout_verdict, refusal, refusals,
    classes, by_state, guaranteed_floor, differences, expected_floor,
    target}. `payout_verdict` judges the enumerated states only (PARTIAL
    folding aside), for a caller that enforces the source rule itself."""
    tgt = _dec(target) or ONE
    states = list(states or [])
    rows = [price_state(a, b, s) for s in states]
    refusals = [r for row in rows for r in row["refusals"]]
    present = {row["class"] for row in rows}
    for c in REQUIRED_CLASSES:
        if c not in present:
            refusals.append({"code": R_CLASS_NOT_ENUMERATED, "class": c,
                             "detail": "no %s state is enumerated: its "
                                       "payout is unknown, not absent" % c})
    priced = [row for row in rows if not row["refusals"]
              and row["basis"] != B_IMPOSSIBLE]
    off_normal = [row["state"] for row in priced
                  if row["class"] == C_NORMAL
                  and not (row["exact"] and row["lo"] == tgt)]
    if off_normal:
        refusals.append({"code": R_NOT_COMPLEMENT, "states": off_normal,
                         "detail": "the legs do not sum exactly to %s when "
                                   "the game completes ordinarily (%s): an "
                                   "ordinary-completion difference is never "
                                   "priced (the bound covers exceptional "
                                   "states only, as bettor_settlement_"
                                   "difference_policy refuses ORDINARY_"
                                   "COMPLETION_DIFFERS)" % (tgt,
                                                            off_normal[:6])})
    # PARTIAL: enumerated, or folded on one stated official source
    partial = {"enumerated": C_PARTIAL in present}
    if not partial["enumerated"]:
        sa, sb = _stated(a.resolution_source), _stated(b.resolution_source)
        if sa and sa == sb:
            partial.update(folded=True, basis=B_PARTIAL_FOLDED, source=sa)
        else:
            partial.update(folded=False, source_a=sa, source_b=sb)
    payout_refusals = list(refusals)
    if not partial["enumerated"] and not partial.get("folded"):
        refusals.append({
            "code": R_PARTIAL_UNKNOWN,
            "detail": ("a shortened or suspended game may be official on one "
                       "leg and not the other: the legs name %s and %s as "
                       "their official source" % (
                           partial.get("source_a") or "no source",
                           partial.get("source_b") or "no source"))})
    classes = {}
    for c in CLASSES:
        cr = [row for row in priced if row["class"] == c]
        if not cr:
            continue
        classes[c] = {"states": [row["state"] for row in cr],
                      "exact": all(row["exact"] for row in cr),
                      "pair_lo": str(min(row["lo"] for row in cr)),
                      "pair_hi": str(max(row["hi"] for row in cr)),
                      "bases": sorted({row["basis"] for row in cr})}
    if C_PARTIAL not in classes:
        classes[C_PARTIAL] = dict(partial)
    floor = min((row["lo"] for row in priced), default=None)
    diffs = [{"state": row["state"], "class": row["class"],
              "pair_lo": str(row["lo"]), "pair_hi": str(row["hi"]),
              "difference_lo": str(row["lo"] - tgt), "basis": row["basis"]}
             for row in priced if not (row["exact"] and row["lo"] == tgt)]

    def verdict_of(refs):
        if refs:
            return REFUSED
        return PRICED if diffs else EXACT
    verdict = verdict_of(refusals)
    out = {"policy_id": POLICY_ID, "version": VERSION, "target": str(tgt),
           "verdict": verdict, "payout_verdict": verdict_of(payout_refusals),
           "refusal": refusals[0]["code"] if refusals else None,
           "refusals": refusals, "classes": classes,
           "guaranteed_floor": None if floor is None else str(floor),
           "differences": diffs,
           "assumes_separate_market_fair_prices_sum_to_one": False,
           "legs": {"a": list(a.key), "b": list(b.key)},
           "by_state": [{"state": row["state"], "class": row["class"],
                         "a": {"rule": row["a"]["rule"],
                               "token": row["a"]["token"]},
                         "b": {"rule": row["b"]["rule"],
                               "token": row["b"]["token"]},
                         "pair_lo": None if row["lo"] is None
                         else str(row["lo"]),
                         "pair_hi": None if row["hi"] is None
                         else str(row["hi"]),
                         "exact": row["exact"], "basis": row["basis"]}
                        for row in rows],
           "expected_floor": None}
    if out["payout_verdict"] == PRICED and sport_family:
        out["expected_floor"] = expected_floor(
            tgt, [row for row in priced if row["class"] != C_NORMAL],
            sport_family)
    return out


def expected_floor(target: Decimal, exceptional_rows: list,
                   sport_family: str) -> dict:
    """E[pair payout] >= target - q_hi x (target - min exceptional lo): the
    exceptional states happen at most q_hi of the time (the venue-measured,
    floored rate bound), and pay at least their lo. NOT A GUARANTEE."""
    from . import bettor_settlement_difference_policy as SDP
    rt = SDP.rate(sport_family)
    if not rt.get("held") or not exceptional_rows:
        return {"held": False, "why": rt.get("why") or
                "no exceptional state is priced"}
    q = Decimal(str(rt["q_hi"]))
    lo = min(row["lo"] for row in exceptional_rows)
    return {"held": True, "is": "EXPECTED_LOWER_BOUND_NOT_A_GUARANTEE",
            "value": str(target - q * (target - lo)),
            "q_hi": str(q), "q_hi_is": rt.get("q_hi_is"),
            "family": rt.get("family"), "min_exceptional_lo": str(lo),
            "formula": "target - q_hi * (target - min exceptional lo)",
            "rate_policy": SDP.VERSION}


def slim(policy: dict) -> dict:
    """The policy as a record carries it (no per-state rows)."""
    p = dict(policy or {})
    return {k: p.get(k) for k in (
        "policy_id", "version", "verdict", "payout_verdict", "refusal",
        "guaranteed_floor", "classes", "expected_floor",
        "assumes_separate_market_fair_prices_sum_to_one")} | {
        "refusal_codes": [r.get("code") for r in p.get("refusals") or []],
        "differences": (p.get("differences") or [])[:8]}


def describe() -> dict:
    return {"policy_id": POLICY_ID, "version": VERSION,
            "classes": list(CLASSES), "required": list(REQUIRED_CLASSES),
            "verdicts": [EXACT, PRICED, REFUSED],
            "refusals": list(REFUSALS) + [R_PRICED_NOT_A_COMPLEMENT],
            "separate_market_fair_prices": "BOUNDED_[0,1]_EACH_NEVER_SUMMED_"
                                           "TO_ONE"}
