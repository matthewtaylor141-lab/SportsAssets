"""CANONICAL ECONOMIC CLAIMS ACROSS VENUES (Kalshi Canonical Venue V1).

BETTOR reasons about the ECONOMIC OUTCOME bought, not `venue + ticker + YES`.
Two venue instruments are the SAME CLAIM only when their payoff vectors are
identical in EVERY settlement state of the canonical fixture; their quotes
stay separate executable paths, and the cheapest all-in path wins.

This module binds the package (sportsassets.canonical_venue, imported
unchanged: ClaimClass, Book, best_split_route, acquisition_cost,
kalshi_taker_fee) to the repo's settlement machinery:

  states    built from the venues' own structured settlement evidence
            (settlement_rule_registry: overtime, draw, void, postponement
            window and payout), over one canonical state set per fixture:
              <RESULT>                normal completion (OT as each rule says)
              <RESULT>@DELAY_<band>   completed after a delay in that band
                                      (bands cut by EVERY window either venue
                                      states -- kalshi_mapping._band_states)
              NEVER_COMPLETED         cancelled / abandoned
            RESULT is HOME_WIN / AWAY_WIN (two-way) plus DRAW where a draw
            is a real final result (three-way, or a two-way league whose
            rules settle a tie).
  payoffs   tokens, never guessed numbers: "1", "0", "0.5", and the
            per-market symbols FP[<market>] ("the last fair market price as
            determined by the venue" for THAT market), 1-FP[<market>] (its NO
            side) and SB[<market>] (stake back). A None anywhere is an
            UNKNOWN state and the instrument's settlement is NOT_PROVEN.
  identity  claim fingerprint = sha256 of {event_key, family, period, the
            ordered states, the token in each} -- exactly the package's
            canonical.claim_fingerprint payload (pinned by test: an
            all-numeric vector fingerprints identically). Venue, ticker,
            title and the YES/NO label are not in it.

SO, FAIL-CLOSED BY CONSTRUCTION:
  * Kalshi "Yankees YES" and "Rays NO" are the same claim only if their
    NEVER_COMPLETED payoffs agree: FP[NYY] vs 1-FP[TB] are different symbols
    (two markets' fair prices, never stated to sum to $1), so on Kalshi's
    published baseball terms they are NOT_ESTABLISHED -- named by state.
  * "Chelsea NO" pays on ARS_WIN and DRAW; "Arsenal YES" pays on ARS_WIN
    only -- different vectors, never one claim.
  * a YES and the NO of the SAME market always complement (FP[x] +
    1-FP[x] = 1 in every state), which is why same-market structures are
    evaluable while cross-market ones on fair-price terms are refused.

No order path, no credential, no network: pure.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from .canonical_venue.canonical import ClaimClass
from .canonical_venue.models import Book as PBook
from .canonical_venue.models import PROVEN, NOT_PROVEN, VenueInstrument
from .canonical_venue import quotes as Q

VERSION = "CANONICAL_CLAIMS_V1"


class KM:
    """The settlement vocabulary of kalshi_mapping (RULE_PAYOFF, the delay
    bands), restated here because tests/test_kalshi_isolation.py reserves
    importing the Kalshi modules to the Kalshi modules themselves; a test
    pins these to kalshi_mapping's own definitions."""
    RULE_PAYOFF = {"STAKE_BACK": "STAKE_BACK", "RESOLVES_NO": "0",
                   "RESOLVES_YES": "1", "LAST_FAIR_PRICE": "LAST_FAIR_PRICE",
                   "IMPOSSIBLE": "IMPOSSIBLE", "SCALAR_0_50": "0.5"}

    @staticmethod
    def _norm(v):
        return None if v in (None, "") else str(v).strip().upper()

    @staticmethod
    def _dec(v):
        if v is None or v == "" or isinstance(v, bool):
            return None
        try:
            return Decimal(str(v))
        except Exception:                                     # noqa: BLE001
            return None

    @staticmethod
    def _band_states(windows: list) -> list:
        ts = sorted({float(w) for w in windows if w is not None})
        out, lo = [], 0.0
        for t in ts:
            out.append(("COMPLETED_AFTER_DELAY_%gH_TO_%gH" % (lo, t), lo, t))
            lo = t
        out.append(("COMPLETED_AFTER_DELAY_OVER_%gH" % lo, lo, None))
        return out
FAMILY_MONEYLINE = "MONEYLINE"
PERIOD_FULL_GAME = "FULL_GAME"
HOME, AWAY, DRAW = "HOME", "AWAY", "DRAW"
S_NEVER = "NEVER_COMPLETED"
ONE = Decimal(1)
ZERO = Decimal(0)
#: the payout tokens a rule maps to (kalshi_mapping.RULE_PAYOFF vocabulary)
_RULE_TOKEN = {"0": "0", "1": "1", "0.5": "0.5"}


def _p(v) -> str:
    return str(Decimal(v).normalize())


def _num(tok):
    try:
        return Decimal(tok)
    except Exception:                                         # noqa: BLE001
        return None


def results_for(outcome_kind: str) -> tuple:
    return (HOME, AWAY, DRAW) if outcome_kind == "THREE_WAY" \
        else (HOME, AWAY)


def windows_of(settlements) -> list:
    return sorted({float(s["postponement_window_hours"]) for s in settlements
                   if s and s.get("postponement_window_hours") is not None})


def states_for(outcome_kind: str, windows: list, *, draw_state: bool) -> list:
    res = list(results_for(outcome_kind))
    if draw_state and DRAW not in res:
        res.append(DRAW)
    out = ["%s_WIN" % r if r != DRAW else DRAW for r in res]
    for name, _lo, _hi in KM._band_states(windows):
        out += ["%s@%s" % (s, name) for s in list(out[:len(res)])]
    return out + [S_NEVER]


def _rule_token(rule, market: str):
    t = KM.RULE_PAYOFF.get(KM._norm(rule) or "")
    if t is None:
        return None
    if t == "LAST_FAIR_PRICE":
        return "FP[%s]" % market
    if t == "STAKE_BACK":
        return "SB[%s]" % market
    if t == "IMPOSSIBLE":
        return "IMPOSSIBLE"
    return _RULE_TOKEN.get(t)


def yes_vector(*, subject: str, outcome_kind: str, settlement: dict | None,
               market: str, states: list, windows: list) -> dict:
    """What YES on `subject` (HOME / AWAY / DRAW) pays in every canonical
    state, from this venue's own settlement evidence. None = unknown.

    Delay bands: inside this venue's own postponement window a delayed game
    follows its result; past it the venue's postponement payout applies.
    Two-way results are final results, so they are priced only when this
    venue counts overtime (unknown or excluded overtime = unknown state)."""
    s = settlement or {}
    ot = s.get("overtime_included")
    w = KM._dec(s.get("postponement_window_hours"))
    band_hi = {n: h for n, _lo, h in KM._band_states(windows)}
    v: dict = {}
    for st in states:
        if st == S_NEVER:
            v[st] = _rule_token(s.get("void_rule"), market)
            continue
        base, _, band = st.partition("@")
        if band:
            if w is None:
                v[st] = None
                continue
            hi = band_hi.get(band)
            if not (hi is not None and Decimal(str(hi)) <= w):
                v[st] = _rule_token(s.get("postponement_payout"), market)
                continue
        res = DRAW if base == DRAW else base[:-4]
        if res == DRAW:
            if subject == DRAW:
                v[st] = "1"
            elif outcome_kind == "THREE_WAY":
                v[st] = "0"                   # a team strike loses a draw
            else:
                v[st] = _rule_token(s.get("draw_rule"), market)
            continue
        if outcome_kind != "THREE_WAY" and ot is not True:
            v[st] = None
            continue
        v[st] = "1" if res == subject else "0"
    return v


def no_vector(yes: dict) -> dict:
    out = {}
    for k, t in yes.items():
        if t is None:
            out[k] = None
        elif t.startswith("FP["):
            out[k] = "1-" + t
        elif t.startswith("1-FP["):
            out[k] = t[2:]
        elif t.startswith("SB[") or t == "IMPOSSIBLE":
            out[k] = t
        else:
            n = _num(t)
            out[k] = None if n is None else _p(ONE - n)
    return out


def fingerprint(event_key: str, family: str, period: str, states: list,
                vec: dict) -> str | None:
    """The package's claim_fingerprint payload over tokens; None when any
    state is unknown (nothing unknown is ever fingerprinted)."""
    if any(vec.get(s) is None for s in states):
        return None
    payload = {"event_key": event_key, "family": family, "period": period,
               "outcomes": list(states),
               "payoff": [[o, _p(vec[o]) if _num(vec[o]) is not None
                           else vec[o]] for o in states]}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def complement_states(a: dict, b: dict, states: list, *,
                      same_market: bool = False) -> tuple:
    """(complementary?, [(state, why)]) -- payouts sum to exactly $1 in
    every state: numerically, or FP[x] + 1-FP[x] on the SAME market x.
    `same_market` (a market's YES against its own NO): the NO pays one
    dollar minus whatever the YES pays, so even an unknown state sums to $1."""
    bad = []
    for s in states:
        x, y = a.get(s), b.get(s)
        if x is None or y is None:
            if same_market and x is None and y is None:
                continue
            bad.append((s, "UNKNOWN"))
            continue
        if x == "IMPOSSIBLE" and y == "IMPOSSIBLE":
            continue
        nx, ny = _num(x), _num(y)
        if nx is not None and ny is not None:
            if nx + ny != ONE:
                bad.append((s, "SUM_%s" % _p(nx + ny)))
            continue
        pair = {x, y}
        fps = [t for t in pair if t.startswith("FP[")]
        if len(fps) == 1 and ("1-" + fps[0]) in pair:
            continue
        bad.append((s, "SYMBOLIC_%s+%s" % (x, y)))
    return (not bad), bad


# ── instruments ────────────────────────────────────────────────────────

@dataclass
class Instrument:
    """One executable acquisition path: (venue, market, side) with the
    payoff of buying it, its book and its fee/settlement facts."""
    venue: str
    market_id: str
    side: str                      # YES / NO
    subject: str                   # HOME / AWAY / DRAW (of the YES side)
    settlement: dict | None
    settlement_status: str         # PROVEN when the venue's rules parsed
    mapping_status: str            # ESTABLISHED fixture mapping
    asks: tuple = ()               # ((price Decimal, qty int), ...)
    observed_at: float | None = None
    book_basis: str | None = None
    sport: str | None = None
    team_code: str | None = None
    rules_sha256: str | None = None
    #: KALSHI: the published fee terms in force (kalshi_fees.effective_terms)
    fee_terms: dict | None = None
    vector: dict = field(default_factory=dict)
    fingerprint: str | None = None
    refusals: list = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.venue, self.market_id, self.side)


@dataclass
class Fixture:
    event_key: str
    sport: str
    league: str
    start_epoch: float
    outcome_kind: str
    home: str
    away: str
    mapping_basis: str = ""


def build_claims(fx: Fixture, instruments: list) -> dict:
    """Payoff vectors, fingerprints and claim classes for one fixture.
    Every instrument lands in a class or in `refused` with its reasons."""
    windows = windows_of([i.settlement for i in instruments])
    # a final DRAW is a state unless EVERY venue states it impossible
    draw_state = not instruments or not all(
        KM._norm((i.settlement or {}).get("draw_rule")) == "IMPOSSIBLE"
        for i in instruments)
    states = states_for(fx.outcome_kind, windows, draw_state=draw_state)
    classes: dict = {}
    refused = []
    for i in instruments:
        yv = yes_vector(subject=i.subject, outcome_kind=fx.outcome_kind,
                        settlement=i.settlement, market=i.market_id,
                        states=states, windows=windows)
        i.vector = yv if i.side == "YES" else no_vector(yv)
        i.refusals = []
        if i.mapping_status != "ESTABLISHED":
            i.refusals.append("MAPPING_NOT_ESTABLISHED")
        if i.settlement_status != PROVEN:
            i.refusals.append("SETTLEMENT_RULES_NOT_PARSED")
        unknown = [s for s in states if i.vector.get(s) is None]
        if unknown:
            i.refusals.append("UNKNOWN_STATES:%s" % ",".join(unknown[:6]))
        if any(str(t).startswith("SB[") for t in i.vector.values() if t):
            i.refusals.append("STAKE_BACK_STATE_NOT_A_FIXED_PAYOUT")
        fp = None if i.refusals else fingerprint(
            fx.event_key, FAMILY_MONEYLINE, PERIOD_FULL_GAME, states, i.vector)
        i.fingerprint = fp
        if fp is None:
            refused.append(i)
            continue
        classes.setdefault(fp, []).append(i)
    # a market's YES and its own NO: complements in every state by the
    # venue's own definition, evaluable even where a rule is unread
    by_mkt: dict = {}
    for i in instruments:
        if i.mapping_status == "ESTABLISHED":
            by_mkt.setdefault((i.venue, i.market_id), {})[i.side] = i
    reciprocal = [(d["YES"], d["NO"]) for _k, d in sorted(by_mkt.items())
                  if "YES" in d and "NO" in d]
    return {"states": states, "windows": windows, "classes": classes,
            "refused": refused, "reciprocal": reciprocal}


def equivalence_receipt(fx: Fixture, a: Instrument, b: Instrument,
                        states: list) -> dict:
    """Are two instruments the same claim? Every state, both tokens."""
    rows = [{"state": s, "a": a.vector.get(s), "b": b.vector.get(s),
             "equal": a.vector.get(s) == b.vector.get(s)
             and a.vector.get(s) is not None} for s in states]
    unequal = [r["state"] for r in rows if not r["equal"]]
    same = bool(a.fingerprint and a.fingerprint == b.fingerprint)
    return {"event_key": fx.event_key,
            "a": {"venue": a.venue, "market_id": a.market_id, "side": a.side,
                  "subject": a.subject, "fingerprint": a.fingerprint,
                  "refusals": a.refusals},
            "b": {"venue": b.venue, "market_id": b.market_id, "side": b.side,
                  "subject": b.subject, "fingerprint": b.fingerprint,
                  "refusals": b.refusals},
            "verdict": "SAME_CLAIM" if same else "NOT_ESTABLISHED",
            "states_differing": unequal, "states": rows}


# ── routing ────────────────────────────────────────────────────────────

def package_book(i: Instrument, *, max_age_s: float) -> PBook | None:
    if i.observed_at is None:
        return None
    return PBook(venue=i.venue, instrument_id=i.market_id, side=i.side,
                 asks=tuple((Decimal(str(p)), int(q)) for p, q in i.asks),
                 observed_at=float(i.observed_at), max_age_s=float(max_age_s),
                 explicit=bool(i.book_basis))


def package_instrument(fx: Fixture, i: Instrument) -> VenueInstrument:
    """The package's instrument for routing (payoff carried as tokens; the
    router never reads payoffs, only the class it was grouped into)."""
    return VenueInstrument(
        venue=i.venue, instrument_id=i.market_id, market_id=i.market_id,
        side=i.side, event_key=fx.event_key, family=FAMILY_MONEYLINE,
        period=PERIOD_FULL_GAME, subject=i.subject, payoff=dict(i.vector),
        rules_status=PROVEN if i.settlement_status == PROVEN else NOT_PROVEN,
        mapping_status=PROVEN if i.mapping_status == "ESTABLISHED"
        else NOT_PROVEN,
        settlement_status=PROVEN if not i.refusals else NOT_PROVEN,
        book_is_explicit=bool(i.book_basis), sport=i.sport)


def route_claim(fx: Fixture, fp: str, members: list, *, qty: int,
                now: float, fee_by_venue: dict, max_age_s: float,
                max_opt_qty: int = 2000) -> dict:
    """The cheapest proven all-in acquisition of `qty` of one claim: every
    alias costed alone (with why it is ineligible), the exact fee-aware
    split across aliases, the chosen route and the runner-up."""
    insts = [package_instrument(fx, i) for i in members]
    books = {}
    for i, pi in zip(members, insts):
        b = package_book(i, max_age_s=max_age_s)
        if b is not None:
            books[pi.key] = b
    cand = []
    for i, pi in zip(members, insts):
        b = books.get(pi.key)
        fn = fee_by_venue.get(i.venue)
        row = {"venue": i.venue, "market_id": i.market_id, "side": i.side,
               "subject": i.subject, "book_basis": i.book_basis,
               "ask": None if not i.asks else str(min(
                   Decimal(str(p)) for p, _q in i.asks)),
               "depth": sum(int(q) for _p, q in i.asks)}
        if b is None:
            cand.append(dict(row, eligible=False, reason="NO_BOOK"))
            continue
        if fn is None:
            cand.append(dict(row, eligible=False, reason="FEE_UNKNOWN"))
            continue
        try:
            c = Q.acquisition_cost(pi, b, qty, now=now, fee_fn=fn)
        except Exception as exc:                              # noqa: BLE001
            cand.append(dict(row, eligible=False,
                             reason="FEE_UNKNOWN:%s" % type(exc).__name__))
            continue
        cand.append(dict(row, eligible=c.eligible, reason=c.reason,
                         principal=str(c.principal), fee=str(c.fee),
                         all_in=str(c.all_in) if c.eligible else None,
                         all_in_per_contract=(str(c.effective_per_contract)
                                              if c.eligible else None),
                         vwap=None if c.vwap is None else str(c.vwap),
                         book_age_s=round(now - b.observed_at, 3)))
    elig = sorted((x for x in cand if x["eligible"]),
                  key=lambda x: (Decimal(x["all_in"]), x["venue"],
                                 x["market_id"], x["side"]))
    cls = ClaimClass(fp, fx.event_key, (), tuple(
        pi for pi, i in zip(insts, members)
        if i.venue in fee_by_venue))
    try:
        split = Q.best_split_route(cls, books, qty, now=now,
                                   fee_by_venue=fee_by_venue,
                                   max_opt_qty=max_opt_qty)
    except Exception:                                         # noqa: BLE001
        split = None
    chosen = None
    if split is not None:
        chosen = {"topology": split.topology, "all_in": str(split.all_in),
                  "all_in_per_contract": str(split.effective_per_contract),
                  "allocations": list(split.allocations)}
    lost = []
    if elig:
        best_single = Decimal(elig[0]["all_in"])
        for x in elig[1:]:
            lost.append({"venue": x["venue"], "market_id": x["market_id"],
                         "side": x["side"],
                         "why": "ALL_IN_HIGHER_BY_%s" % _p(
                             Decimal(x["all_in"]) - best_single)})
    for x in cand:
        if not x["eligible"]:
            lost.append({"venue": x["venue"], "market_id": x["market_id"],
                         "side": x["side"], "why": x["reason"]})
    return {"claim_fingerprint": fp, "event_key": fx.event_key, "qty": qty,
            "aliases": len(members), "candidates": cand,
            "chosen": chosen,
            "best_single": elig[0] if elig else None,
            "runner_up": elig[1] if len(elig) > 1 else None,
            "lost": lost,
            "refusal": None if chosen else (
                "QTY_ABOVE_EXACT_OPTIMIZER_BOUND" if qty > max_opt_qty
                else "NO_ELIGIBLE_ROUTE"),
            "rule": "minimum TOTAL all-in cost (principal + fees per order + "
                    "depth); unknown fee / stale / no book are ineligible"}


def same_market(a: Instrument, b: Instrument) -> bool:
    """ONE market's YES and NO (Kalshi rep 2026-10-07): one book, one
    liquidity pool (NO ask = 1 - best YES bid), positions that NET. Never
    two routes of one pool, never a structural-arbitrage pair."""
    return a.venue == b.venue and a.market_id == b.market_id


def fee_functions(*, at, sport: str | None, kalshi_terms=None) -> dict:
    """{venue: fee_fn(count, price)} for the venues whose fee is KNOWN for
    this sport and date; a venue absent here is FEE_UNKNOWN (ineligible).
    KALSHI is present only when `kalshi_terms` (kalshi_fees.effective_terms:
    the published schedule x the series / event multiplier in force) price
    -- an unknown multiplier is an ineligible route, never a default."""
    from datetime import datetime, timezone

    from . import kalshi_fees as KF
    from .agents import adriana_arb as A
    when = at if isinstance(at, datetime) else datetime.fromtimestamp(
        float(at), timezone.utc)
    out = {}
    kf = KF.fee_fn(kalshi_terms) if kalshi_terms else None
    if kf is not None:
        out[A.KALSHI] = kf
    probe = A.order_fee(A.POLYMARKET_US, [(Decimal("0.5"), 1)], at=when,
                        sport=sport)
    if probe.known:
        def pmus(count, price, _w=when, _s=sport):
            f = A.order_fee(A.POLYMARKET_US, [(Decimal(price), int(count))],
                            at=_w, sport=_s)
            if not f.known:
                raise ValueError(f.code or "FEE_SCHEDULE_UNKNOWN")
            return f.fee
        out[A.POLYMARKET_US] = pmus
    return out
