"""KALSHI <-> BETTOR CONTRACT EQUIVALENCE, FROM STRUCTURED EVIDENCE ONLY.

A Kalshi market ticker and a BETTOR contract (a Polymarket US
`us_market_slug`, its provider event, outcome, period, line and market
family) are the SAME BET only when every element of the bet is stated on
both sides as structured data and agrees:

    event      league, home team, away team, scheduled start (within a
               stated tolerance)
    market     market type (MONEYLINE / SPREAD / TOTAL), period (FULL_GAME,
               FIRST_HALF, ...), line
    outcome    the outcome the position is paid on (team / side + line)
    settlement overtime inclusion, draw, void / cancellation, postponement
               window and payout, push -- compared as PAYOFF VECTORS

TITLES ARE NEVER EVIDENCE. `title`, `subtitle`, `yes_sub_title` and every
other prose field are not read by this module at all; two records whose
titles are identical and whose structure is absent are NOT_ESTABLISHED,
and the verdict names exactly what is missing. (edge-engine's discovery
joins on outcome-name strings and fuzzy scores, kalshi.py:171-204; that is
exactly what this module refuses to do on a money path.)

SETTLEMENT AS A PAYOFF VECTOR. For each terminal state a fixture can end in
-- decided in regulation, tied then decided in overtime, a final draw, an
exact push on the line, completed after a delay (banded by every
postponement window either side states), never completed -- each side's
rules produce what the HELD position pays: 1, 0, STAKE_BACK,
LAST_FAIR_PRICE, or None (unknown). Two positions are compatible only when
the vectors are equal state by state; an unknown anywhere is
NOT_ESTABLISHED. A short is compared as the complement of the long vector,
against the Kalshi market whose YES outcome is the EXPLICITLY declared
complement outcome (Kalshi's V2 book is YES-only, kalshi.py:461-469, so a
short is held as YES on the complementary ticker -- never as a naked sale).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

VERSION = "KALSHI_MAPPING_V1"
ESTABLISHED = "ESTABLISHED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

MARKET_TYPES = ("MONEYLINE", "SPREAD", "TOTAL")
DEFAULT_START_TOLERANCE_S = 15 * 60
EVENT_FIELDS = ("league", "home_team", "away_team", "start_time")
SETTLEMENT_FIELDS = ("overtime_included", "void_rule",
                     "postponement_window_hours", "postponement_payout")
# A rule stated as IMPOSSIBLE (e.g. draw_rule for a league whose games
# cannot end level) must be stated so by BOTH sides to compare equal.
# SCALAR_0_50 (a tie settled at 0.50 per contract, stated in the contract's
# own rules) is its own payout "0.5": never equal to STAKE_BACK,
# LAST_FAIR_PRICE, YES ("1") or NO ("0").
RULE_PAYOFF = {"STAKE_BACK": "STAKE_BACK", "RESOLVES_NO": "0",
               "RESOLVES_YES": "1", "LAST_FAIR_PRICE": "LAST_FAIR_PRICE",
               "IMPOSSIBLE": "IMPOSSIBLE", "SCALAR_0_50": "0.5"}
PROSE_FIELDS = ("title", "subtitle", "yes_sub_title", "no_sub_title",
                "event_title", "name", "label", "description")

# terminal states, from the HELD outcome's point of view
S_REG_WIN, S_REG_LOSS = "REG_WIN", "REG_LOSS"
S_OT_WIN, S_OT_LOSS = "REG_TIE_THEN_OT_WIN", "REG_TIE_THEN_OT_LOSS"
S_DRAW = "FINAL_DRAW"
S_TT, S_FF = "REG_TRUE_FINAL_TRUE", "REG_FALSE_FINAL_FALSE"
S_FT, S_TF = "REG_FALSE_OT_FLIPS_TRUE", "REG_TRUE_OT_FLIPS_FALSE"
S_PUSH = "EXACT_PUSH"
S_CANCELLED = "NEVER_COMPLETED"
# the same state seen from the OPPOSITE outcome
MIRROR = {S_REG_WIN: S_REG_LOSS, S_REG_LOSS: S_REG_WIN, S_OT_WIN: S_OT_LOSS,
          S_OT_LOSS: S_OT_WIN, S_TT: S_FF, S_FF: S_TT, S_FT: S_TF, S_TF: S_FT}


def _dec(v) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _ts(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.timestamp() if v.tzinfo else None   # naive time is not evidence
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.timestamp() if d.tzinfo else None


def _norm(v) -> str | None:
    return None if v in (None, "") else str(v).strip().upper()


def norm_outcome(o: Any, market_type: str | None) -> tuple | None:
    """A structured outcome as a comparable tuple, or None if incomplete.
    MONEYLINE {team}; SPREAD {team, line}; TOTAL {side OVER|UNDER, line}."""
    if not isinstance(o, dict):
        return None
    if market_type == "MONEYLINE":
        t = _norm(o.get("team"))
        return ("TEAM", t) if t else None
    if market_type == "SPREAD":
        t, ln = _norm(o.get("team")), _dec(o.get("line"))
        return ("SPREAD", t, ln) if t and ln is not None else None
    if market_type == "TOTAL":
        s, ln = _norm(o.get("side")), _dec(o.get("line"))
        return ("TOTAL", s, ln) if s in ("OVER", "UNDER") and ln is not None else None
    return None


# ── settlement compatibility ─────────────────────────────────────────

def _band_states(windows: list) -> list[tuple[str, float | None, float | None]]:
    ts = sorted({float(w) for w in windows if w is not None})
    out, lo = [], 0.0
    for t in ts:
        out.append(("COMPLETED_AFTER_DELAY_%gH_TO_%gH" % (lo, t), lo, t))
        lo = t
    out.append(("COMPLETED_AFTER_DELAY_OVER_%gH" % lo, lo, None))
    return out


def payoff_vector(market_type: str, outcome: dict, settlement: dict | None,
                  windows: list) -> dict:
    """state -> what the LONG holder of `outcome` is paid, under these rules.
    `windows` is every postponement window stated by EITHER side, so both
    vectors are evaluated over the same delay bands."""
    s = settlement or {}
    ot = s.get("overtime_included")
    ot = ot if isinstance(ot, bool) else None
    v: dict = {}
    if market_type == "MONEYLINE":
        draw = RULE_PAYOFF.get(_norm(s.get("draw_rule")) or "")
        v[S_REG_WIN], v[S_REG_LOSS] = "1", "0"
        v[S_OT_WIN] = None if ot is None else ("1" if ot else draw)
        v[S_OT_LOSS] = None if ot is None else ("0" if ot else draw)
        v[S_DRAW] = draw
    else:
        line = _dec((outcome or {}).get("line"))
        v[S_TT], v[S_FF] = "1", "0"
        v[S_FT] = None if ot is None else ("1" if ot else "0")
        v[S_TF] = None if ot is None else ("0" if ot else "1")
        if line is None:
            v[S_PUSH] = None
        elif line != line.to_integral_value():
            v[S_PUSH] = "IMPOSSIBLE"
        else:
            v[S_PUSH] = RULE_PAYOFF.get(_norm(s.get("push_rule")) or "")
    w = _dec(s.get("postponement_window_hours"))
    beyond = RULE_PAYOFF.get(_norm(s.get("postponement_payout")) or "")
    for name, lo, hi in _band_states(windows):
        if w is None:
            v[name] = None
        elif hi is not None and Decimal(str(hi)) <= w:
            v[name] = "FOLLOWS_RESULT"
        else:
            v[name] = beyond
    v[S_CANCELLED] = RULE_PAYOFF.get(_norm(s.get("void_rule")) or "")
    return v


def complement(vec: dict) -> dict:
    flip = {"1": "0", "0": "1"}
    return {k: flip.get(p, p) for k, p in vec.items()}


def mirrored(vec: dict) -> dict:
    """Re-key a vector computed for one outcome to the opposite outcome's
    states (Team B's REG_WIN is Team A's REG_LOSS)."""
    return {MIRROR.get(k, k): p for k, p in vec.items()}


@dataclass
class SettlementVerdict:
    verdict: str
    bettor_vector: dict = field(default_factory=dict)
    kalshi_vector: dict = field(default_factory=dict)
    unknown_states: list = field(default_factory=list)
    unequal_states: list = field(default_factory=list)
    missing_fields: list = field(default_factory=list)


def settlement_compatibility(bettor: dict, kalshi: dict, *,
                             holding: str = "LONG") -> SettlementVerdict:
    """Compare what the BETTOR position and the Kalshi YES position pay in
    every terminal state. LONG: same outcome. SHORT: the complement of the
    BETTOR long vector vs Kalshi YES on the declared complement outcome
    (re-keyed to the BETTOR outcome's states).

    RULE EVIDENCE (settlement_rule_registry, integration): the CURRENT
    contract's own rule fields fill ONLY missing structured settlement
    fields -- the BETTOR side from `rules_text` alone (the per-contract
    bettor_live_read.read_rules_text read; title / description stay
    non-evidence here), the Kalshi side from the market object's
    `rules_primary` / `rules_secondary`. An explicit structured field the
    parsed rules contradict is never overwritten: the comparison refuses
    NOT_ESTABLISHED with `settlement.rule_evidence_conflict`."""
    from . import settlement_rule_registry as _SRR
    bettor = _SRR.enrich_contract(bettor, venue=_SRR.POLYMARKET_US,
                                  text_fields=("rules_text",))
    kalshi = _SRR.enrich_contract(kalshi, venue=_SRR.KALSHI)
    be = bettor.get("settlement_rule_evidence") or {}
    ke = kalshi.get("settlement_rule_evidence") or {}
    if be.get("status") == _SRR.CONFLICT or ke.get("status") == _SRR.CONFLICT:
        return SettlementVerdict(
            NOT_ESTABLISHED,
            missing_fields=["settlement.rule_evidence_conflict"])
    mt = _norm(bettor.get("market_type"))
    bs, ks = bettor.get("settlement") or {}, kalshi.get("settlement") or {}
    missing = []
    for side, s in (("bettor", bs), ("kalshi", ks)):
        for f in SETTLEMENT_FIELDS:
            if s.get(f) is None:
                missing.append("%s.settlement.%s" % (side, f))
        if mt == "MONEYLINE" and s.get("draw_rule") is None:
            missing.append("%s.settlement.draw_rule" % side)
    windows = [bs.get("postponement_window_hours"),
               ks.get("postponement_window_hours")]
    bvec = payoff_vector(mt, bettor.get("outcome") or {}, bs, windows)
    kvec = payoff_vector(mt, kalshi.get("outcome") or {}, ks, windows)
    if holding == "SHORT":
        bvec, kvec = complement(bvec), mirrored(kvec)
    states = sorted(set(bvec) | set(kvec))
    unknown = [s for s in states if bvec.get(s) is None or kvec.get(s) is None]
    unequal = [s for s in states if s not in unknown and bvec.get(s) != kvec.get(s)]
    ok = not (unknown or unequal or missing) and mt in MARKET_TYPES
    return SettlementVerdict(ESTABLISHED if ok else NOT_ESTABLISHED, bvec, kvec,
                             unknown, unequal, missing)


# ── identity ─────────────────────────────────────────────────────────

@dataclass
class MappingVerdict:
    verdict: str
    holding: str
    kalshi_ticker: str | None
    us_market_slug: str | None
    missing: list = field(default_factory=list)
    mismatched: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    settlement: SettlementVerdict | None = None

    @property
    def established(self) -> bool:
        return self.verdict == ESTABLISHED


def establish(bettor: dict, kalshi: dict, *, holding: str = "LONG",
              start_tolerance_s: float = DEFAULT_START_TOLERANCE_S) -> MappingVerdict:
    """Is holding `holding` (LONG / SHORT) of this BETTOR contract the same
    bet as holding YES on this Kalshi market? Structured evidence only."""
    missing, mismatched, notes = [], [], []
    if holding not in ("LONG", "SHORT"):
        mismatched.append("holding")
    if not bettor.get("us_market_slug"):
        missing.append("bettor.us_market_slug")
    if not kalshi.get("ticker"):
        missing.append("kalshi.ticker")
    if any(bettor.get(f) or kalshi.get(f) for f in PROSE_FIELDS):
        notes.append("TITLES_IGNORED: prose fields are never equivalence evidence")
    be, ke = bettor.get("event") or {}, kalshi.get("event") or {}
    for f in EVENT_FIELDS:
        bv, kv = be.get(f), ke.get(f)
        if f == "start_time":
            bt, kt = _ts(bv), _ts(kv)
            if bt is None:
                missing.append("bettor.event.start_time")
            if kt is None:
                missing.append("kalshi.event.start_time")
            if bt is not None and kt is not None and abs(bt - kt) > start_tolerance_s:
                mismatched.append("event.start_time")
            continue
        if _norm(bv) is None:
            missing.append("bettor.event.%s" % f)
        if _norm(kv) is None:
            missing.append("kalshi.event.%s" % f)
        if _norm(bv) and _norm(kv) and _norm(bv) != _norm(kv):
            mismatched.append("event.%s" % f)
    bmt, kmt = _norm(bettor.get("market_type")), _norm(kalshi.get("market_type"))
    for side, mt in (("bettor", bmt), ("kalshi", kmt)):
        if mt is None:
            missing.append("%s.market_type" % side)
        elif mt not in MARKET_TYPES:
            mismatched.append("%s.market_type_unsupported" % side)
    if bmt and kmt and bmt != kmt:
        mismatched.append("market_type")
    for side, rec in (("bettor", bettor), ("kalshi", kalshi)):
        if _norm(rec.get("period")) is None:
            missing.append("%s.period" % side)
    if _norm(bettor.get("period")) and _norm(kalshi.get("period")) and \
            _norm(bettor.get("period")) != _norm(kalshi.get("period")):
        mismatched.append("period")
    want_key = "outcome" if holding == "LONG" else "complement_outcome"
    bo = norm_outcome(bettor.get(want_key), bmt)
    ko = norm_outcome(kalshi.get("outcome"), kmt)
    if bo is None:
        missing.append("bettor.%s" % want_key)
    if ko is None:
        missing.append("kalshi.outcome")
    if holding == "SHORT" and norm_outcome(bettor.get("outcome"), bmt) is None:
        missing.append("bettor.outcome")
    if bo is not None and ko is not None and bo != ko:
        mismatched.append("outcome")
    sv = settlement_compatibility(bettor, kalshi, holding=holding)
    if sv.verdict != ESTABLISHED:
        missing.extend(f for f in sv.missing_fields if f not in missing)
        if sv.unequal_states:
            mismatched.append("settlement:" + ",".join(sv.unequal_states))
        if sv.unknown_states:
            notes.append("settlement_unknown:" + ",".join(sv.unknown_states))
    ok = not missing and not mismatched and sv.verdict == ESTABLISHED
    return MappingVerdict(ESTABLISHED if ok else NOT_ESTABLISHED, holding,
                          kalshi.get("ticker"), bettor.get("us_market_slug"),
                          missing, mismatched, notes, sv)


def find_target(bettor: dict, candidates: list, *, holding: str = "LONG",
                start_tolerance_s: float = DEFAULT_START_TOLERANCE_S) -> MappingVerdict:
    """Exactly one ESTABLISHED candidate, or NOT_ESTABLISHED with the reason
    (none established / more than one established)."""
    verdicts = [establish(bettor, c, holding=holding,
                          start_tolerance_s=start_tolerance_s) for c in candidates]
    good = [v for v in verdicts if v.established]
    if len(good) == 1:
        return good[0]
    v = MappingVerdict(NOT_ESTABLISHED, holding, None, bettor.get("us_market_slug"))
    if not good:
        v.missing = ["no candidate established"]
        v.notes = ["%s: missing=%s mismatched=%s" % (c.kalshi_ticker, c.missing,
                                                      c.mismatched)
                   for c in verdicts][:10]
    else:
        v.mismatched = ["AMBIGUOUS: %d candidates established" % len(good)]
        v.notes = [c.kalshi_ticker for c in good]
    return v
