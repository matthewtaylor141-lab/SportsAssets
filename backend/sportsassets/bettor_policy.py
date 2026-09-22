"""THE policy. One definition, used by the replay AND by the runtime.

WHY THIS MODULE EXISTS. The strategy lived in
`research/beta48/bettor_episodes.py`, which the runtime cannot import:
the image copies `research/beta48/shadow` and the top-level JSON, not
the replay engine. So every economic result came from one
implementation and every live decision would have come from another,
and nothing would have caught them drifting apart. A backtest of a
policy the runtime does not run is a description of a program nobody
executes.

Everything a decision needs is here, as PURE FUNCTIONS of
decision-time state. No clock, no I/O, no venue client, no randomness.
That is what lets the replay and the live loop call the same code and
lets a test assert they reach the SAME decision on the SAME book.

THE PROVENANCE OF EVERY RULE IS CARRIED WITH IT. `RULES` records, for
each decision, the inputs it may read, the economic rationale, the
evidence behind it, and -- separately -- whether it is DERIVED from a
case study or is a HYPOTHESIS this programme introduced. A rule with
no case-study support is not forbidden; it is labelled, so a reader
never has to guess which is which.

WHAT IS DELIBERATELY NOT HERE: anything that needs an outcome. No
realised fill rate, no later price, no settlement result. A rule that
needed one could not be evaluated by a live engine at the moment it
must decide, and a backtest that used one would be reading its own
answer.

Run:  python -m pytest backend/tests/test_bettor_policy.py
"""
from __future__ import annotations

import dataclasses

POLICY_VERSION = "BETTOR_POLICY_V1"

DERIVED = "DERIVED_FROM_CASE_STUDY"
HYPOTHESIS = "HYPOTHESIS_INTRODUCED_BY_THIS_PROGRAMME"

# ── decisions ────────────────────────────────────────────────────────
D_STAND_ASIDE = "STAND_ASIDE"
D_QUOTE_BOTH = "QUOTE_BOTH_SIDES"
D_CANCEL_OTHER = "CANCEL_OTHER_SIDE"
D_HOLD_BOTH = "HOLD_BOTH_SIDES"
D_COMPLETE_PAIR = "COMPLETE_PAIR_AS_TAKER"
D_EXIT_TAKER = "EXIT_LEG_AS_TAKER"
D_REST_EXIT = "REST_AN_EXIT_QUOTE"
D_HOLD = "HOLD_TO_SETTLEMENT"
D_RELEASE = "SELL_THE_PAIR_BACK"
D_WAIT = "WAIT"

# ── placement ────────────────────────────────────────────────────────
AT_TOUCH = "AT_TOUCH"
DEEPER_1 = "DEEPER_1"
ENGINE = "ENGINE"

# ── sizing ───────────────────────────────────────────────────────────
SIZE_FIXED = "FIXED"
SIZE_EDGE_SCALED = "EDGE_SCALED"

# ── recovery ─────────────────────────────────────────────────────────
R_MAKER_THEN_TAKER = "MAKER_THEN_TAKER"
R_TAKER_NOW = "TAKER_NOW"
R_COMPLETE_PAIR = "COMPLETE_PAIR"
R_HOLD = "HOLD"


@dataclasses.dataclass(frozen=True)
class Policy:
    """Every knob, frozen. A named combination, never a swept one."""
    name: str = "BASE"

    # ENTRY
    min_spread_ticks: int = 1
    max_mid: float = 1.0
    min_mid: float = 0.0

    # QUOTE PLACEMENT
    placement: str = ENGINE

    # SIZING
    size_rule: str = SIZE_FIXED
    size_min_mult: float = 0.5
    size_max_mult: float = 2.0

    # INVENTORY
    cancel_other_on_fill: bool = False
    max_unmatched_mult: float = 1.0

    # TIME, in seconds -- never observation counts, which meant 37
    # minutes in one market and 25 hours in another.
    quote_horizon_s: float = 2400.0
    recovery_wait_s: float = 1200.0

    # RECOVERY / RELEASE
    recovery: str = R_MAKER_THEN_TAKER
    release_matched: bool = False
    hard_flatten: bool = False


@dataclasses.dataclass(frozen=True)
class Book:
    """The decision-time book. Prices only -- depth is optional.

    `depth_*` are None when the source does not carry them, and every
    rule below must work without them. The captured corpus has no
    depth; the observation release will.
    """
    bid: float | None
    ask: float | None
    tick: float = 0.01
    state: str | None = None
    depth_bid: float | None = None
    depth_ask: float | None = None

    @property
    def spread_ticks(self) -> int | None:
        if self.bid is None or self.ask is None:
            return None
        return max(0, int(round((self.ask - self.bid) / self.tick)))

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0


@dataclasses.dataclass(frozen=True)
class Inventory:
    """What we hold and what is still working. All decision-time."""
    yes: float = 0.0
    no: float = 0.0
    open_yes: float = 0.0          # unfilled size resting on the YES bid
    open_no: float = 0.0
    clip: float = 100.0            # the size this episode quoted
    elapsed_s: float = 0.0
    unmatched_since_s: float | None = None

    @property
    def matched(self) -> float:
        return min(self.yes, self.no)

    @property
    def unmatched(self) -> float:
        return abs(self.yes - self.no)


def _d(decision, why, rule, inputs, alternatives=(), **kw):
    """Every decision carries its own reason and provenance."""
    return {"decision": decision, "why": why, "rule": rule,
            "provenance": RULES[rule]["provenance"],
            "inputs_read": inputs,
            "alternatives": list(alternatives), **kw}


# ═══ THE DECISIONS ═══════════════════════════════════════════════════

def admit(policy: Policy, book: Book) -> dict:
    """Is this book worth quoting at all?"""
    if book.bid is None or book.ask is None:
        return _d(D_STAND_ASIDE, "one-sided book: a single-sided quote is "
                  "a directional bet, which is a different mandate",
                  "entry", ["bid", "ask"],
                  [D_QUOTE_BOTH])
    st = book.spread_ticks
    if st is None or st < policy.min_spread_ticks:
        return _d(D_STAND_ASIDE,
                  "spread %s ticks is below the %d-tick minimum; the "
                  "edge does not cover two fees and the queue risk"
                  % (st, policy.min_spread_ticks),
                  "entry", ["bid", "ask"], [D_QUOTE_BOTH])
    mid = book.mid
    if mid is None or not (policy.min_mid <= mid <= policy.max_mid):
        return _d(D_STAND_ASIDE,
                  "mid %.4f outside the [%.2f, %.2f] band" % (
                      mid if mid is not None else float("nan"),
                      policy.min_mid, policy.max_mid),
                  "entry_band", ["bid", "ask"], [D_QUOTE_BOTH])
    if book.state not in (None, "MARKET_STATE_OPEN"):
        return _d(D_STAND_ASIDE, "market state %r is not open" % book.state,
                  "entry", ["state"], [D_QUOTE_BOTH])
    return _d(D_QUOTE_BOTH,
              "spread %d ticks clears the minimum and the book is "
              "two-sided, so a paired fill is worth the spread less fees"
              % st, "entry", ["bid", "ask", "state"], [D_STAND_ASIDE])


def quote_prices(policy: Policy, book: Book) -> dict:
    """Where in the book do the two quotes sit?"""
    if book.bid is None or book.ask is None:
        return {"bid": None, "offer": None, "why": "one-sided book",
                "rule": "placement",
                "provenance": RULES["placement"]["provenance"]}
    if policy.placement == DEEPER_1:
        b, o = book.bid - book.tick, book.ask + book.tick
        why = ("one tick behind the touch: better price conditional on "
               "filling, worse odds of filling")
    elif policy.placement == AT_TOUCH:
        b, o = book.bid, book.ask
        why = ("join the touch. Queue position is NOT OBSERVABLE, so "
               "paying a tick to improve buys something unmeasurable; "
               "joining is the only placement whose cost is known")
    else:
        # ENGINE: improve by one tick where the spread has room for it.
        if (book.spread_ticks or 0) >= 2:
            b, o = book.bid + book.tick, book.ask - book.tick
            why = "improve by one tick; the spread has room for it"
        else:
            b, o = book.bid, book.ask
            why = "no room to improve; join the touch"
    return {"bid": round(b, 6), "offer": round(o, 6), "why": why,
            "rule": "placement", "inputs_read": ["bid", "ask", "tick"],
            "provenance": RULES["placement"]["provenance"]}


def quote_size(policy: Policy, book: Book, base_size: float) -> dict:
    """How many contracts, from decision-time inputs only.

    SIZING IS AN ALLOCATION RULE, NOT A PROFITABILITY LEVER, and the
    arithmetic says so: edge and capital both scale with the clip, so
    per-capital-hour is invariant to a uniform change. What it can
    change is WHICH books receive the capital.
    """
    if policy.size_rule == SIZE_FIXED or book.bid is None \
            or book.ask is None:
        return {"size": float(base_size), "mult": 1.0, "rule": SIZE_FIXED,
                "why": "fixed clip",
                "inputs_read": [],
                "provenance": RULES["sizing"]["provenance"]}
    if policy.size_rule == SIZE_EDGE_SCALED:
        ticks = max(1, book.spread_ticks or 1)
        floor = max(1, policy.min_spread_ticks)
        mult = max(policy.size_min_mult,
                   min(policy.size_max_mult, ticks / float(floor)))
        return {"size": float(base_size) * mult, "mult": round(mult, 4),
                "rule": SIZE_EDGE_SCALED,
                "why": "%d ticks against a %d-tick floor: a wider spread "
                       "pays more for the same capital and the same queue "
                       "risk, so it earns more size" % (ticks, floor),
                "inputs_read": ["bid", "ask", "tick"],
                "provenance": RULES["sizing"]["provenance"]}
    return {"size": float(base_size), "mult": 1.0, "rule": SIZE_FIXED,
            "why": "unknown size rule %r; falling back to the clip"
                   % policy.size_rule, "inputs_read": [],
            "provenance": RULES["sizing"]["provenance"]}


def on_fill(policy: Policy, inv: Inventory) -> dict:
    """A leg filled. Does the other side stay up?"""
    if not policy.cancel_other_on_fill:
        return _d(D_HOLD_BOTH,
                  "the policy leaves both sides working; a second fill "
                  "completes the pair passively",
                  "inventory", ["yes", "no"], [D_CANCEL_OTHER])
    return _d(D_CANCEL_OTHER,
              "a leg filled and the other side is still working. Letting "
              "it rest while a leg is unmatched is how a market maker "
              "becomes a directional trader by accident",
              "inventory", ["yes", "no", "open_yes", "open_no"],
              [D_HOLD_BOTH], unmatched=inv.unmatched)


def inventory_cap_breached(policy: Policy, inv: Inventory) -> dict:
    """Has unmatched inventory reached the point of stopping entirely?"""
    cap = policy.max_unmatched_mult * inv.clip
    breached = inv.unmatched >= cap - 1e-9
    return {"breached": breached, "cap": cap, "unmatched": inv.unmatched,
            "rule": "inventory_cap",
            "why": ("unmatched %.4f reached the %.2f x clip cap"
                    % (inv.unmatched, policy.max_unmatched_mult))
            if breached else "within the cap",
            "inputs_read": ["yes", "no", "clip"],
            "provenance": RULES["inventory_cap"]["provenance"]}


def recovery_action(policy: Policy, inv: Inventory, book: Book) -> dict:
    """Unmatched inventory exists. What closes it?"""
    if inv.unmatched <= 1e-9:
        return _d(D_WAIT, "nothing unmatched", "recovery", [])
    waited = (inv.unmatched_since_s is not None
              and inv.elapsed_s - inv.unmatched_since_s
              >= policy.recovery_wait_s)
    alts = [D_COMPLETE_PAIR, D_EXIT_TAKER, D_REST_EXIT, D_HOLD]
    if policy.recovery == R_COMPLETE_PAIR:
        return _d(D_COMPLETE_PAIR,
                  "a matched pair is worth exactly 1 at settlement, which "
                  "is certain. Paying the taker fee converts an uncertain "
                  "directional position into a certain one",
                  "recovery", ["yes", "no", "bid", "ask"], alts)
    if policy.recovery == R_TAKER_NOW:
        return _d(D_EXIT_TAKER,
                  "cross out now: a bounded loss taken on time is cheaper "
                  "than an unbounded carry",
                  "recovery", ["yes", "no", "bid", "ask"], alts)
    if policy.recovery == R_HOLD:
        return _d(D_HOLD, "ride it to settlement",
                  "recovery", ["yes", "no"], alts)
    # MAKER_THEN_TAKER
    if not waited:
        return _d(D_REST_EXIT,
                  "rest an exit first and earn the rebate rather than pay "
                  "the taker fee immediately",
                  "recovery", ["yes", "no", "elapsed_s"], alts)
    return _d(D_EXIT_TAKER,
              "the %.0fs recovery window expired; waiting longer is an "
              "unbounded commitment of capital at an unknown probability"
              % policy.recovery_wait_s,
              "recovery", ["yes", "no", "elapsed_s"], alts)


def release_action(policy: Policy, inv: Inventory, book: Book) -> dict:
    """A matched pair exists and nothing is unmatched. Hold or release?

    THE MERGE CALL IS NOT AVAILABLE. Nothing like it has been
    demonstrated at this venue, so the only way to turn a pair into cash
    early is to sell both legs back and pay the spread for it.
    """
    if inv.matched <= 1e-9:
        return _d(D_WAIT, "no matched pair", "release", [])
    if not policy.release_matched:
        return _d(D_HOLD,
                  "hold to settlement: a matched pair pays exactly 1 and "
                  "selling it back costs the spread plus two taker fees",
                  "release", ["yes", "no"], [D_RELEASE],
                  matched=inv.matched)
    cost = None
    if book.bid is not None and book.ask is not None:
        cost = round((book.ask - book.bid) * inv.matched, 6)
    return _d(D_RELEASE,
              "sell both legs back to free the capital now, at a market "
              "cost of the spread plus two taker fees",
              "release", ["yes", "no", "bid", "ask"], [D_HOLD],
              matched=inv.matched, spread_cost_usd=cost)


def horizon_expired(policy: Policy, inv: Inventory) -> bool:
    return inv.elapsed_s >= policy.quote_horizon_s


# ═══ PROVENANCE ══════════════════════════════════════════════════════
#
# Each rule: the inputs it may read, why it is economically sound, the
# evidence, and whether it is DERIVED from a case study or a
# HYPOTHESIS this programme introduced. A hypothesis is not forbidden.
# It is labelled, so nobody has to guess.

RULES = {
    "entry": {
        "provenance": DERIVED,
        "case_study": "two-sided market making -- quote both sides, earn "
                      "the spread and the maker rebate, carry no "
                      "direction",
        "inputs": ["bid", "ask", "tick", "state"],
        "rationale": "a paired fill earns the spread less two fees. Below "
                     "the minimum spread that difference is negative "
                     "before any queue risk is priced.",
        "evidence": "fee engine validated on 3,285 real executions; "
                    "maker -0.012495 vs published -0.0125, taker "
                    "+0.059971 vs +0.06, exact to the cent on all 345 "
                    "maker fills",
    },
    "entry_band": {
        "provenance": HYPOTHESIS,
        "case_study": None,
        "inputs": ["bid", "ask"],
        "rationale": "a mid band keeps the quote away from the tails, "
                     "where a one-tick move is a large proportional move "
                     "and adverse selection is worst.",
        "evidence": "introduced for C4 to shape a liquidity-provision "
                    "quote. NOT derived from a case study, and it has "
                    "never been shown to improve a measured result.",
    },
    "placement": {
        "provenance": DERIVED,
        "case_study": "passive liquidity provision at the touch",
        "inputs": ["bid", "ask", "tick"],
        "rationale": "queue position is not observable, so paying a tick "
                     "to improve buys something unmeasurable. Joining is "
                     "the only placement whose cost is known.",
        "evidence": "the venue publishes aggregate ladder quantity, not "
                    "per-order queues; the replay sweeps a queue-ahead "
                    "fraction rather than asserting one",
    },
    "sizing": {
        "provenance": HYPOTHESIS,
        "case_study": None,
        "inputs": ["bid", "ask", "tick"],
        "rationale": "a wider spread pays more for the same capital and "
                     "the same queue risk, so it earns more size. "
                     "ALLOCATION, not a profitability lever: edge and "
                     "capital both scale with the clip.",
        "evidence": "measured in acceptance/strategy_v2.json. Cut held "
                    "capital-hours at every queue fraction; net P&L moved "
                    "BOTH ways (+14.20 to -18.08) over four clustered "
                    "observations. Not an established improvement.",
    },
    "inventory": {
        "provenance": DERIVED,
        "case_study": "inventory control -- the mechanism that separates "
                      "market making from position taking",
        "inputs": ["yes", "no", "open_yes", "open_no"],
        "rationale": "leaving the second side working while a leg is "
                     "unmatched doubles exposure before the first is "
                     "matched.",
        "evidence": "partial fills are the NORMAL case in the corpus, not "
                    "the exception",
    },
    "inventory_cap": {
        "provenance": DERIVED,
        "case_study": "position limits",
        "inputs": ["yes", "no", "clip"],
        "rationale": "a hard cap on unmatched size bounds the directional "
                     "exposure a market-making policy can accumulate.",
        "evidence": "C3 runs at half a clip; the cap is a declared "
                    "combination, never swept",
    },
    "recovery": {
        "provenance": DERIVED,
        "case_study": "pair completion and loss-taking discipline",
        "inputs": ["yes", "no", "bid", "ask", "elapsed_s"],
        "rationale": "a matched pair is worth exactly 1, which is "
                     "certain; an unmatched leg is not. Completing "
                     "converts uncertainty into a known cost.",
        "evidence": "60 of 60 filled episodes reached FLAT_PAIRED under "
                    "COMPLETE_PAIR; the corpus contains no material "
                    "loss-taking event, so that path is exercised by "
                    "labelled scenario",
    },
    "release": {
        "provenance": HYPOTHESIS,
        "case_study": "capital recycling -- the case study assumes a "
                      "merge/netting call this venue has not been shown "
                      "to have",
        "inputs": ["yes", "no", "bid", "ask"],
        "rationale": "with no merge call, the only early exit from a "
                     "matched pair is to sell both legs and pay the "
                     "spread.",
        "evidence": "measured: fired twice in 470 episodes, cost $1.75, "
                    "freed 26 of 3,696 held capital-hours. NOT the "
                    "binding constraint -- 88% of committed capital-hours "
                    "rest behind quotes that never fill.",
    },
}


def describe() -> dict:
    return {
        "policy": POLICY_VERSION,
        "shared_by": ["research/beta48/bettor_episodes.py (replay)",
                      "sportsassets.bettor_policy_runtime (live loop)"],
        "pure_functions": True,
        "reads_no_outcomes": "no realised fill rate, no later price, no "
                             "settlement result",
        "rules": {k: {"provenance": v["provenance"],
                      "case_study": v["case_study"]}
                  for k, v in RULES.items()},
        "derived": sorted(k for k, v in RULES.items()
                          if v["provenance"] == DERIVED),
        "hypotheses": sorted(k for k, v in RULES.items()
                             if v["provenance"] == HYPOTHESIS),
    }
