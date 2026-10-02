"""Pure cross-venue comparison; no network, credentials, or order placement.

Adapters must provide a verified canonical contract and current executable
book. A displayed listing price is not an executable quote. Rule identifiers
are approved equivalence classes, NOT raw text hashes: identical-looking
titles do not establish identical payoffs. Rule evidence IDs remain attached.
This module does not grant execution authority or qualify a probability model.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a price/quantity')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('invalid number') from exc
    if not result.is_finite():
        raise ValueError('nonfinite number')
    return result


@dataclass(frozen=True)
class Contract:
    fixture_id: str       # canonical identity, including meeting/game number
    sport: str
    competition: str
    period: str           # full-game, regulation, innings, sets kept distinct
    family: str
    selection: str        # canonical participant ID / over / under / draw
    line: str | None
    rules_class: str      # reviewed payoff-equivalence class; unknown is blank
    rules_evidence: str
    currency: str = 'USD'
    payout_unit: str = '1'

    def key(self):
        fields = (self.fixture_id, self.sport, self.competition, self.period,
                  self.family, self.selection, self.rules_class,
                  self.rules_evidence, self.currency)
        if not all(isinstance(x, str) and x.strip() for x in fields):
            raise ValueError('identity or rules evidence missing')
        payout = number(self.payout_unit)
        if payout <= 0:
            raise ValueError('invalid payout unit')
        line = None if self.line is None else number(self.line)
        if self.family in ('spread', 'total', 'team_total') and line is None:
            raise ValueError('line missing')
        # Evidence IDs differ across venues; the reviewed rules class is shared.
        return (self.fixture_id, self.sport, self.competition, self.period,
                self.family, self.selection, line, self.rules_class,
                self.currency, payout)


@dataclass(frozen=True)
class Quote:
    venue: str
    market_id: str
    contract: Contract
    book_id: str
    observed_at: float
    status: str
    asks: tuple            # ((USD price, quantity), ...)
    bids: tuple
    fee_version: str
    fee: object            # callable(action, consumed_levels) -> USD total
    authority: bool = False
    account_ready: bool = False


def compare_entries(contract, quotes, *, quantity, now, max_age_s):
    """Rank BUY costs at equal filled quantity; keep every rejection visible.

    Freshness is raw, nonnegative age of a validated book observation, not
    price-change age and not a listing updated_at. Fees include order/fill
    rounding in the venue adapter; no maker rebate is assumed.
    """
    qty = number(quantity)
    if qty <= 0 or not math.isfinite(now) or not (0 < max_age_s < float('inf')):
        raise ValueError('invalid comparison request')
    expected = contract.key()
    results = []
    seen = set()
    for quote in quotes:
        row = dict(venue=quote.venue, market_id=quote.market_id,
                   book_id=quote.book_id, eligible=False)
        results.append(row)
        try:
            if (quote.venue, quote.market_id) in seen:
                raise ValueError('DUPLICATE_VENUE_MARKET')
            seen.add((quote.venue, quote.market_id))
            if quote.contract.key() != expected:
                raise ValueError('CONTRACT_OR_SETTLEMENT_MISMATCH')
            if not quote.venue or not quote.market_id or not quote.book_id:
                raise ValueError('QUOTE_PROVENANCE_MISSING')
            if quote.authority is not True or quote.account_ready is not True:
                raise ValueError('VENUE_OR_ACCOUNT_NOT_READY')
            if quote.status != 'OPEN':
                raise ValueError('MARKET_NOT_OPEN')
            age = now - float(quote.observed_at)
            if not math.isfinite(age) or not 0 <= age <= max_age_s:
                raise ValueError('BOOK_STALE_OR_FUTURE')
            if not quote.fee_version or not callable(quote.fee):
                raise ValueError('FEE_SCHEDULE_UNAVAILABLE')
            levels = [(number(p), number(q)) for p, q in quote.asks]
            payout = number(contract.payout_unit)
            if not levels or any(not 0 < p < payout or q <= 0 for p,q in levels):
                raise ValueError('INVALID_BOOK')
            if any(levels[i][0] > levels[i+1][0] for i in range(len(levels)-1)):
                raise ValueError('UNSORTED_BOOK')
            remaining, gross, consumed = qty, Decimal(0), []
            for price, available in levels:
                take = min(available, remaining)
                if take <= 0:
                    break
                gross += price * take
                consumed.append((price, take))
                remaining -= take
            if remaining:
                raise ValueError('INSUFFICIENT_EXECUTABLE_DEPTH')
            fee = number(quote.fee('BUY', tuple(consumed)))
            if fee < 0:
                raise ValueError('UNPROVEN_REBATE')
            row.update(eligible=True, quantity=str(qty), gross_usd=str(gross),
                       fee_usd=str(fee), fee_version=quote.fee_version,
                       total_cost_usd=str(gross+fee), book_age_s=age,
                       rules_evidence=quote.contract.rules_evidence)
        except (ValueError, TypeError, ArithmeticError) as exc:
            row['reason'] = str(exc)
        except Exception:
            row['reason'] = 'FEE_OR_ADAPTER_FAILED'
    # Duplicate snapshots cannot compete against themselves; reject all copies.
    counts = {}
    for r in results:
        k = r['venue'], r['market_id']
        counts[k] = counts.get(k, 0) + 1
    for r in results:
        if counts[r['venue'], r['market_id']] > 1:
            r.update(eligible=False, reason='DUPLICATE_VENUE_MARKET')
    ranked = sorted((r for r in results if r['eligible']),
                    key=lambda r:(number(r['total_cost_usd']),r['venue'],r['market_id']))
    return {'best': ranked[0] if ranked else None, 'ranked': ranked,
            'assessments': results, 'execution_authorized': False,
            'basis': 'EQUAL_QUANTITY_EXECUTABLE_BUY_COST_AFTER_FEES'}


def position_action(*, holding_venue, action_venue, action):
    """Cross-venue buying creates another leg; it never closes the first."""
    if action == 'SELL':
        if action_venue != holding_venue:
            return {'kind':'REFUSE', 'reason':'CANNOT_SELL_HOLDING_ON_OTHER_VENUE'}
        return {'kind':'OWN_VENUE_EXIT', 'requires':'OWNED_QUANTITY_AND_CURRENT_BIDS'}
    if action == 'BUY' and action_venue != holding_venue:
        return {'kind':'CROSS_VENUE_ADDITIONAL_LEG', 'closes_original':False,
                'requires':'VERIFIED_JOINT_PAYOFFS_FEES_LIQUIDITY_AND_CAPITAL',
                'execution_authorized':False}
    return {'kind':'ADDITIONAL_EXPOSURE', 'closes_original':False}


def compare_management_actions(*, holding_payoffs, scenarios, candidates,
                               probabilities=None, evidence_id):
    """Compare HOLD, own-venue exits and direct/indirect hedge proposals.

    Each candidate supplies net cash_delta (fees included) and payoff_delta
    over the SAME audited scenario domain, including exceptional settlements.
    Selling removes payoffs; buying elsewhere adds them. No binary complement
    is inferred from team names, and a hedge never edits the original holding.
    Unknown scenario probabilities remain unknown: no EV ranking then.
    The caller must verify the joint payoff matrix and fresh executable quotes.
    This pure proposal analysis cannot authorize an order.
    """
    if not evidence_id or not scenarios or len(set(scenarios)) != len(scenarios):
        raise ValueError('unique scenarios and evidence required')
    domain = set(scenarios)
    if set(holding_payoffs) != domain:
        raise ValueError('holding scenario domain mismatch')
    holding = {s:number(holding_payoffs[s]) for s in scenarios}
    probs = None
    if probabilities is not None:
        if set(probabilities) != domain:
            raise ValueError('probability scenario domain mismatch')
        probs = {s:number(probabilities[s]) for s in scenarios}
        if any(p < 0 or p > 1 for p in probs.values()) or sum(probs.values()) != 1:
            raise ValueError('probabilities must sum to one')
    proposals = [dict(id='HOLD', kind='HOLD', cash_delta='0',
                      payoff_delta={s:'0' for s in scenarios},
                      evidence_id=evidence_id)] + list(candidates)
    out = []
    ids = [c.get('id') for c in proposals]
    for c in proposals:
        row = {'id':c.get('id'), 'eligible_for_comparison':False}
        out.append(row)
        try:
            if not c.get('id') or ids.count(c['id']) != 1:
                raise ValueError('ACTION_ID_MISSING_OR_DUPLICATED')
            if c.get('evidence_id') != evidence_id:
                raise ValueError('JOINT_PAYOFF_EVIDENCE_MISMATCH')
            if set(c.get('payoff_delta', {})) != domain:
                raise ValueError('SCENARIO_COVERAGE_INCOMPLETE')
            if c.get('kind') not in ('HOLD','OWN_VENUE_EXIT','DIRECT_PAIR','INDIRECT_PAIR'):
                raise ValueError('UNKNOWN_ACTION_KIND')
            cash = number(c['cash_delta'])
            terminal = {s:holding[s]+number(c['payoff_delta'][s])+cash for s in scenarios}
            if c['kind'] != 'HOLD' and not c.get('quote_evidence'):
                raise ValueError('EXECUTABLE_QUOTE_EVIDENCE_MISSING')
            row.update(eligible_for_comparison=True, kind=c['kind'],
                       terminal_values={s:str(v) for s,v in terminal.items()},
                       minimum_over_declared_scenarios=str(min(terminal.values())),
                       additional_cash_required=str(max(Decimal(0),-cash)),
                       expected_terminal_value=(str(sum(terminal[s]*probs[s] for s in scenarios))
                                                if probs is not None else None))
        except (ValueError,TypeError,KeyError) as exc:
            row['reason'] = str(exc)
    ranked = sorted((r for r in out if r['eligible_for_comparison']),
                    key=lambda r:(-number(r['expected_terminal_value']),r['id'])) if probs else []
    return {'assessments':out, 'ranked_by_expected_value':ranked,
            'probability_status':'SUPPLIED' if probs else 'UNKNOWN_NOT_ZERO',
            'execution_authorized':False,
            'note':'Scenario evidence and execution constraints require independent validation.'}
