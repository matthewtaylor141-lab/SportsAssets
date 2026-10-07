"""ADRIANA, CLAIM-FIRST: SAME-VENUE AND CROSS-VENUE ARBITRAGE OVER CANONICAL
CLAIM CLASSES -- SHADOW ONLY, NO AUTHORITY (Kalshi Canonical Venue V1).

    venue instruments -> structured mapping -> payoff vectors
      -> canonical claim classes (canonical_claims)
      -> complementary claim PAIRS (payouts sum to $1 in every state)
      -> every alias of each claim offered to the engine as a payoff-
         equivalent alternative leg (adriana_arb.evaluate_structure: the
         cheapest fillable alias is used at each size, fees per order per
         venue, slippage buffer, freshness and cross-book skew, settlement
         source / window / void / tie rules compared, size solved)
      -> topology read off the aliases the engine actually used
         (SAME_VENUE / CROSS_VENUE), never decided before pricing
      -> an engine record, recorded through adriana.record into migration
         265 (the arb system of record): GUARANTEED_AFTER_COSTS or REFUSED

Same-venue arbitrage is first class: a Kalshi pair, a Polymarket US pair
and a cross-venue pair are found by the same arithmetic.

FAIR-PRICE TERMS. Where a venue settles a cancelled game at "the last fair
market price as determined by the Exchange", that price is a per-MARKET
symbol: it complements only its own NO side. The engine needs numbers, so
for a pair whose only symbolic states are FP[x] / 1-FP[x] of the SAME
market x the engine is run with FP[x] = 0.5 -- without loss of generality,
because the pair pays exactly $1 in that state for EVERY value of FP[x]; the
substitution is named on the record. Any other symbolic state is not a
complement and never reaches the engine.

NO AUTHORITY: no order, cancel, credential or capital path; it imports the
pure engine and the pure claim layer only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations

from .. import canonical_claims as CC
from . import adriana_arb as A

VERSION = "ADRIANA_CLAIMS_V1"
AUTHORITY = dict(A.AUTHORITY)
FP_SUBSTITUTE = Decimal("0.5")
R_NOT_COMPLEMENT = "CLAIMS_NOT_COMPLEMENTARY"


def _aware(ts) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc)


def adriana_states(states: list) -> tuple:
    """Canonical states -> the engine's outcome names: NEVER_COMPLETED is
    VOID; every '<result>@<band beyond all windows>' collapses to POSTPONED
    (beyond every window each leg pays its postponement payout whatever the
    result, so those states carry one payout per leg)."""
    over = [s for s in states if "@COMPLETED_AFTER_DELAY_OVER_" in s]
    mapping = {}
    for s in states:
        if s == CC.S_NEVER:
            mapping[s] = A.OUTCOME_VOID
        elif s in over:
            mapping[s] = A.OUTCOME_POSTPONED
        else:
            mapping[s] = s
    names = []
    for s in states:
        if mapping[s] not in names:
            names.append(mapping[s])
    return mapping, tuple(names)


def numeric_payoff(i: CC.Instrument, mapping: dict, *,
                   reciprocal: bool = False) -> tuple:
    """(payoff over engine outcomes, substitution note) or (None, why).
    `reciprocal` (a market's YES against its own NO): an unknown state is
    run at 0.5 on both sides -- the pair pays $1 there whatever the value."""
    out, subs = {}, []
    for s, tok in i.vector.items():
        name = mapping[s]
        if tok is None:
            if not reciprocal:
                return None, "UNKNOWN_STATE:%s" % s
            out[name] = FP_SUBSTITUTE
            subs.append("UNKNOWN[%s]" % s)
            continue
        if tok.startswith("FP["):
            val = FP_SUBSTITUTE
            subs.append(tok)
        elif tok.startswith("1-FP["):
            val = Decimal(1) - FP_SUBSTITUTE
            subs.append(tok)
        elif tok == "IMPOSSIBLE":
            continue
        else:
            try:
                val = Decimal(tok)
            except Exception:                                 # noqa: BLE001
                return None, "NON_NUMERIC_STATE:%s=%s" % (s, tok)
        if name in out and out[name] != val:
            return None, "POSTPONED_PAYOUT_DEPENDS_ON_RESULT:%s" % s
        out[name] = val
    return out, sorted(set(subs))


def contract_of(fx: CC.Fixture, i: CC.Instrument, mapping: dict,
                *, tie_rule: str, source: str,
                reciprocal: bool = False) -> tuple:
    pay, note = numeric_payoff(i, mapping, reciprocal=reciprocal)
    if pay is None:
        return None, note
    start = _aware(fx.start_epoch)
    w = float((i.settlement or {}).get("postponement_window_hours") or 0)
    spec = A.SettlementSpec(
        event_key=fx.event_key, family=A.MONEYLINE, period="FULL_GAME",
        subject=fx.event_key, line=None, resolution_source=source,
        settle_window=(start.isoformat(),
                       (start + timedelta(hours=w)).isoformat()),
        # the rule's MEANING (two venues stating the same rule pass the
        # engine's void-rule check; a per-market fair price never reaches
        # it across markets -- not a complement)
        void_rule=str((i.settlement or {}).get("void_rule") or "UNSTATED"),
        tie_rule=tie_rule)
    side = A.YES if i.side == "YES" else A.NO
    return A.Contract(i.venue, i.market_id, side, spec, pay,
                      sport=i.sport), note


def _source(i: CC.Instrument) -> str:
    s = i.settlement or {}
    src = s.get("verification_sources") or s.get("settlement_sources")
    if isinstance(src, (list, tuple)):
        src = "+".join(sorted(str(x) for x in src))
    # the OFFICIAL source the rules name (MLB, NHL ...), not the venue: two
    # venues reading the same source pass the engine's source rule; a leg
    # whose rules name none is UNSTATED and never equals another
    return str(src) if src else "UNSTATED:%s:%s" % (i.venue, i.market_id)


def scan_fixture(fx: CC.Fixture, built: dict, *, now: float,
                 max_age_s: float = A.DEFAULT_MAX_AGE_S,
                 max_skew_s: float = A.DEFAULT_MAX_SKEW_S,
                 max_scan_qty: int = 2000) -> dict:
    """Every complementary claim pair of one fixture, evaluated. Returns
    {records, pairs_considered, pairs_not_complementary, by_topology}."""
    states = built["states"]
    classes = built["classes"]
    mapping, names = adriana_states(states)
    draw = "DRAW" in names
    tie_rule = "DRAW_SETTLES_PER_CONTRACT_RULES" if draw else A.TIE_IMPOSSIBLE
    space = A.OutcomeSpace(
        event_key=fx.event_key, outcomes=names, exhaustive=True,
        basis=("CANONICAL_FIXTURE %s %s: results x delay bands x "
               "never-completed, from both venues' parsed rules"
               % (fx.league, fx.outcome_kind)),
        tie_outcomes=("DRAW",) if draw else ())
    now_dt = _aware(now)
    out = {"records": [], "pairs_considered": 0,
           "pairs_not_complementary": 0, "by_topology": {}}
    keys = sorted(classes)
    pairs = []
    for fa, fb in combinations(keys, 2):
        ma, mb = classes[fa], classes[fb]
        ok, _bad = CC.complement_states(ma[0].vector, mb[0].vector, states)
        if not ok:
            out["pairs_not_complementary"] += 1
            continue
        pairs.append((fa, fb, ma, mb, "CLAIM_CLASSES"))
    seen_markets = {(i.venue, i.market_id) for fa, fb, ma, mb, _ in pairs
                    for i in ma + mb}
    for y, n in built.get("reciprocal") or []:
        # a reciprocal pair already inside a claim-class pair is not
        # evaluated twice
        if (y.venue, y.market_id) in seen_markets and y.fingerprint and \
                n.fingerprint:
            continue
        pairs.append((y.fingerprint or "RECIPROCAL:%s:YES" % y.market_id,
                      n.fingerprint or "RECIPROCAL:%s:NO" % n.market_id,
                      [y], [n], "SAME_MARKET_RECIPROCAL"))
    for fa, fb, ma, mb, basis in pairs:
        out["pairs_considered"] += 1
        recip = basis == "SAME_MARKET_RECIPROCAL"
        legs, books, notes, dropped = [], [], [], []
        for members in (ma, mb):
            alts = []
            for i in members:
                c, note = contract_of(fx, i, mapping, tie_rule=tie_rule,
                                      source=_source(i), reciprocal=recip)
                if c is None:
                    dropped.append({"venue": i.venue,
                                    "market_id": i.market_id,
                                    "side": i.side, "why": note})
                    continue
                if note:
                    notes.extend(note)
                alts.append(c)
                if i.observed_at is not None:
                    books.append(A.Book(i.venue, i.market_id, c.side,
                                        tuple(i.asks), _aware(i.observed_at)))
            legs.append(alts)
        if not legs[0] or not legs[1]:
            rec = A._record("STRUCTURE", A.COMPLEMENT, [{
                "code": "CLAIM_LEG_HAS_NO_EVALUABLE_ALIAS",
                "detail": str(dropped)[:400]}],
                {"event_key": fx.event_key, "legs": [
                    [{"venue": c.venue, "market_id": c.market_id,
                      "side": c.side} for c in g] for g in legs],
                 "books": [], "skew_s": None, "now": now_dt.isoformat()},
                None, None)
        else:
            rec = A.evaluate_structure(
                legs, books, space, now_dt, expect_kind=A.COMPLEMENT,
                max_age_s=max_age_s, max_skew_s=max_skew_s,
                max_scan_qty=max_scan_qty)
        used = {u.get("venue") for u in (
            (rec.get("economics") or {}).get("legs") or [])
            if isinstance(u, dict)}
        if not used:
            used = {c.venue for g in legs for c in g[:1]}
        topology = "SAME_VENUE" if len(used) == 1 else "CROSS_VENUE"
        rec["claim_pair"] = {"claim_a": fa, "claim_b": fb,
                             "basis": basis, "topology": topology,
                             "aliases_a": len(ma), "aliases_b": len(mb),
                             "fair_price_substitution":
                                 sorted(set(notes)) or None,
                             "aliases_dropped": dropped}
        out["by_topology"][topology] = out["by_topology"].get(topology,
                                                              0) + 1
        out["records"].append(rec)
    return out


def census_result(scans: list, *, markets_read: int, books_fresh: int,
                  skipped: dict) -> dict:
    """Shape the claim scans as an adriana.census result, so adriana.record
    writes them into the 265 tables unchanged (opportunities only for a
    GUARANTEED_AFTER_COSTS engine verdict)."""
    opps, refs = [], []
    for s in scans:
        for rec in s["records"]:
            (opps if rec.get("verdict") == A.GUARANTEED_AFTER_COSTS
             else refs).append(rec)
    summary = A.census_of(opps + refs)
    summary.update(markets_read=markets_read, skipped=skipped,
                   conditional_candidates=0,
                   claim_engine=VERSION,
                   by_topology=_sum_topology(scans),
                   pairs_not_complementary=sum(
                       s["pairs_not_complementary"] for s in scans))
    return {"opportunities": opps, "refusals": refs, "census": summary,
            "books_fresh": books_fresh}


def _sum_topology(scans) -> dict:
    out: dict = {}
    for s in scans:
        for k, v in s["by_topology"].items():
            out[k] = out.get(k, 0) + v
    return out


def assert_no_authority() -> bool:
    return A.assert_no_authority() and AUTHORITY == A.AUTHORITY
