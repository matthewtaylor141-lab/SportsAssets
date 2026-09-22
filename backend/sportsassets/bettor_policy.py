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

    # THE FLOW GATE. Zero leaves it off, which is every policy
    # evaluated before it existed. See RULES["flow"] for why it exists
    # and what it is aimed at.
    min_flow_cover: float = 0.0
    flow_window_s: float = 1800.0

    # THE VOLATILITY GATE. Zero leaves it off. Where the flow gate asks
    # whether a quote will be REACHED, this asks whether being reached
    # is worth anything. See RULES["volatility"].
    min_vol_cover: float = 0.0
    vol_window_s: float = 1800.0

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
    # TRAILING traded shares per second, measured over a window that
    # ENDS AT THIS INSTANT. Never forward-looking: it is the flow that
    # has already happened, which is what a live engine would have.
    flow_per_s: float | None = None
    # TRAILING realised volatility of the mid, expressed per root
    # second, measured over a window ENDING at this instant. Like
    # `flow_per_s` it is strictly backward-looking.
    vol_per_sqrt_s: float | None = None

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


def is_open(state) -> bool:
    """TWO VOCABULARIES MEET HERE, so the translation is in one place.

    The venue and the market stream say `MARKET_STATE_OPEN`.
    `bettor_decision_engine` and `bettor_observation_adapter` normalise
    that to `OPEN`/`ACTIVE` before the engine sees it. This module is
    called from BOTH sides -- the replay feeds it tape rows in the
    venue's spelling, the runtime adapter feeds it engine books in the
    normalised one -- so a rule that recognised only one of them would
    silently stand aside on every book from the other. That is exactly
    how a live engine ends up quoting nothing and reporting no defect.

    `None` means the source did not carry a state, which is the state
    of the captured corpus; the engine refuses an unstated state on its
    own terms and this module does not second-guess it.
    """
    if state is None:
        return True
    s = str(state).upper()
    if s.startswith("MARKET_STATE_"):
        s = s[len("MARKET_STATE_"):]
    return s in ("OPEN", "ACTIVE")


def _d(decision, why, rule, inputs, alternatives=(), **kw):
    """Every decision carries its own reason and provenance."""
    return {"decision": decision, "why": why, "rule": rule,
            "provenance": RULES[rule]["provenance"],
            "inputs_read": inputs,
            "alternatives": list(alternatives), **kw}


# ═══ THE DECISIONS ═══════════════════════════════════════════════════

def flow_cover(policy: Policy, book: Book, *, clip: float,
               horizon_s: float | None = None) -> dict:
    """How many times over can the trailing flow reach our quote?

    THE QUANTITY THE WHOLE STRATEGY TURNS ON. To be filled on a side we
    must first see the depth already resting at that price traded
    through, and then our own clip. Over the quoting horizon the volume
    that could do that is the trailing flow rate times the horizon. So

        cover = flow_per_s * horizon / (depth_ahead + clip)

    and a cover below 1 says, from decision-time information alone,
    that this quote is not expected to be reached at all.

    TWO ASSUMPTIONS, BOTH CONSERVATIVE IN THE SAME DIRECTION AND BOTH
    STATED. `flow_per_s` is TOTAL traded volume, not the half that
    would lift our particular side, so the cover is optimistic by
    roughly a factor of two; and `depth_ahead` takes the WORSE of the
    two sides, because a pair needs both. The first overstates cover
    and the second understates it, and neither is tuned -- a threshold
    picked to make the arithmetic come out is a threshold fitted to the
    answer.
    """
    horizon = policy.quote_horizon_s if horizon_s is None else horizon_s
    if book.flow_per_s is None:
        return {"cover": None, "known": False,
                "why": "no trailing flow was supplied, so the fill "
                       "constraint cannot be evaluated from this book"}
    depth = max(book.depth_bid or 0.0, book.depth_ask or 0.0)
    need = depth + max(0.0, float(clip))
    if need <= 0:
        return {"cover": None, "known": False,
                "why": "nothing to clear and no clip: undefined"}
    return {"cover": book.flow_per_s * horizon / need, "known": True,
            "flow_per_s": book.flow_per_s, "depth_ahead": depth,
            "need": need, "horizon_s": horizon,
            "why": "%.1f shares of trailing flow per second over %.0fs "
                   "against %.0f to clear" % (book.flow_per_s, horizon,
                                              need)}


def vol_cover(policy: Policy, book: Book, *,
              horizon_s: float | None = None) -> dict:
    """How many times does the spread cover the move we expect to eat?

    THE ECONOMICS OF A PASSIVE QUOTE, in one ratio. Resting on both
    sides earns the spread when both legs fill at rest. What it loses
    is the price move between the two fills -- we are hit on the side
    the market is leaving and left holding the side it is going to. So

        cover = spread / (expected absolute move over the horizon)

    and a cover below 1 says the move we expect to be adversely
    selected by is larger than the spread we are quoting to capture.
    Fill rate does not enter it: this is a statement about the value of
    the fills, not their number, which is exactly what the flow gate
    got wrong.

    THE SCALING ASSUMPTION IS A RANDOM WALK and is stated because it is
    an assumption: a trailing move measured over W seconds is scaled to
    the horizon H by sqrt(H/W), which is what `vol_per_sqrt_s` already
    carries. A price that trends rather than diffuses moves further
    than this, so the ratio is optimistic in a trending market -- which
    is the market this gate exists to refuse.
    """
    horizon = policy.quote_horizon_s if horizon_s is None else horizon_s
    if book.vol_per_sqrt_s is None:
        return {"cover": None, "known": False,
                "why": "no trailing volatility was supplied, so the "
                       "adverse-selection cost cannot be evaluated from "
                       "this book"}
    if book.bid is None or book.ask is None:
        return {"cover": None, "known": False, "why": "one-sided book"}
    move = book.vol_per_sqrt_s * (horizon ** 0.5)
    spread = book.ask - book.bid
    if move <= 0:
        return {"cover": float("inf"), "known": True, "move": 0.0,
                "spread": spread, "horizon_s": horizon,
                "why": "the mid did not move over the trailing window"}
    return {"cover": spread / move, "known": True, "move": move,
            "spread": spread, "horizon_s": horizon,
            "why": "spread %.4f against an expected %.4f move over %.0fs"
                   % (spread, move, horizon)}


def admit(policy: Policy, book: Book, *, clip: float | None = None) -> dict:
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
    if not is_open(book.state):
        return _d(D_STAND_ASIDE, "market state %r is not open" % book.state,
                  "entry", ["state"], [D_QUOTE_BOTH])
    if policy.min_flow_cover > 0:
        # FAIL CLOSED. A gate that passes when its input is missing is
        # not a gate, and this one is missing on every historical row
        # that predates the ladder join.
        if clip is None:
            return _d(D_STAND_ASIDE,
                      "the flow gate is armed but no clip was supplied, so "
                      "the cover cannot be computed; refusing rather than "
                      "quoting past an unevaluated gate",
                      "flow", ["flow_per_s", "depth_bid", "depth_ask"],
                      [D_QUOTE_BOTH])
        fc = flow_cover(policy, book, clip=clip)
        if not fc["known"]:
            return _d(D_STAND_ASIDE, fc["why"], "flow",
                      ["flow_per_s", "depth_bid", "depth_ask"],
                      [D_QUOTE_BOTH])
        if fc["cover"] < policy.min_flow_cover:
            return _d(D_STAND_ASIDE,
                      "flow cover %.2f is below the %.2f minimum -- %s. A "
                      "quote that is not expected to be reached commits "
                      "capital and earns nothing"
                      % (fc["cover"], policy.min_flow_cover, fc["why"]),
                      "flow", ["flow_per_s", "depth_bid", "depth_ask"],
                      [D_QUOTE_BOTH], flow_cover=round(fc["cover"], 4))
    if policy.min_vol_cover > 0:
        vc = vol_cover(policy, book)
        if not vc["known"]:
            return _d(D_STAND_ASIDE, vc["why"], "volatility",
                      ["vol_per_sqrt_s", "bid", "ask"], [D_QUOTE_BOTH])
        if vc["cover"] < policy.min_vol_cover:
            return _d(D_STAND_ASIDE,
                      "volatility cover %.2f is below the %.2f minimum -- "
                      "%s. Quoting into a move larger than the spread is "
                      "selling an option for less than it is worth"
                      % (vc["cover"], policy.min_vol_cover, vc["why"]),
                      "volatility", ["vol_per_sqrt_s", "bid", "ask"],
                      [D_QUOTE_BOTH], vol_cover=round(vc["cover"], 4))
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
    "flow": {
        "provenance": HYPOTHESIS,
        "case_study": None,
        "inputs": ["flow_per_s", "depth_bid", "depth_ask", "clip"],
        "rationale": "the measured constraint is the FILL RATE, not the "
                     "netting call: 88% of committed capital-hours rest "
                     "behind quotes that never fill, and 358 of 420 "
                     "episodes never fill at all. Spread, price band and "
                     "sizing all allocate capital among books; none of "
                     "them asks whether a book trades enough for the "
                     "quote to be reached. This one does, from "
                     "decision-time information only.",
        "evidence": "DIAGNOSTIC, not yet a validated improvement. Derived "
                    "from the fill-rate breakdown of the development "
                    "split; its effect is reported in "
                    "acceptance/evaluation.json. A hypothesis aimed at "
                    "the measured constraint is still a hypothesis.",
    },
    "volatility": {
        "provenance": HYPOTHESIS,
        "case_study": "adverse selection -- the reason a market maker "
                      "widens or stops quoting, present in every market "
                      "making study but not previously implemented here",
        "inputs": ["vol_per_sqrt_s", "bid", "ask"],
        "rationale": "a passive two-sided quote earns the spread and "
                     "loses the price move between its two fills. When "
                     "the expected move over the quoting horizon exceeds "
                     "the spread, being filled is worth less than not "
                     "being filled, whatever the fill rate.",
        "evidence": "DIRECTLY FROM THE EVALUATION FAILURE. On the "
                    "evaluation split the selected policy lost 74.29 "
                    "over 197 episodes, of which TWO episodes -- both "
                    "opened during live college football play on "
                    "2026-09-19 -- accounted for -79.82. The other 195 "
                    "netted +5.53. The loss was the adverse move, not "
                    "the fee and not the fill rate. NOT YET EVALUATED: "
                    "the evaluation split has been spent, so this rule "
                    "is a prespecified candidate for fresh data, not a "
                    "validated improvement.",
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
