"""Deterministic report mathematics, refusal behavior and real tool dispatch."""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]/'sportsassets'
PKG='_intelligence_proof'
pkg=types.ModuleType(PKG);pkg.__path__=[str(ROOT)];sys.modules[PKG]=pkg
agents=types.ModuleType(PKG+'.agents');agents.__path__=[str(ROOT/'agents')];sys.modules[agents.__name__]=agents
R=importlib.import_module(PKG+'.agents.intelligence_reports')
C=importlib.import_module(PKG+'.agents.cross_venue_research')
T=importlib.import_module(PKG+'.agents.capability_tools')


import pytest


@pytest.fixture(autouse=True)
def _queue_account_pinned(monkeypatch):
    """THIS FILE'S CONNECTIONS ARE MOCKS WITH NO PAPER SELECTOR: the research
    queue is pinned to the legacy account (capability_work.ACCOUNT), exactly
    as the Postgres capability tests pin theirs to a scratch account. The
    unpinned default -- the queue follows the durable PAPER selector -- is
    proven against Postgres in tests/test_rc63_day_one_selected_account_defaults.py."""
    monkeypatch.setattr(T.W,'ACCOUNT',T.W.LEGACY_ACCOUNT)


def candidate(i,**kw):
    return dict(id=i,cycle_at=i,sport_key='soccer',family='moneyline',provider_event_id='g',
                us_market_slug='slug',outcome=kw.pop('outcome','REFUSED'),first_refusal=kw.pop('first_refusal','QUOTE_STALE_ON_ARRIVAL'),**kw)


def forecast(i=1,**kw):
    d=dict(id=i,experiment_id='forward',version='v1',provider='pinnapi',devig_method='multiplicative',record_purpose='ENTRY_DECISION',
           sport_family='soccer',market='moneyline',period='full',line=None,event_key='event'+str(i),condition_id='c',contract_selection='YES',
           probability=.8,decided_at=10,outcome_known=True,outcome=1,outcome_at=20)
    d.update(kw);return d


def snapshot():
    a=dict(action_id='poly',venue='POLYMARKET_US',side='BUY',contract_id='yes',book_id='b',book_at=95,rules_id='r',mapping_evidence_id='m',fee_evidence_id='f',
           mapping_verified=True,rules_verified=True,scenario_evidence_id='terms',market_state='OPEN',payoff_per_contract={'yes':1,'no':0},
           size_rules_id='sizes',quantity_step=1,minimum_quantity=1,minimum_notional=0,balance_evidence_id='balance',balance_at=95,available_cash=100,
           levels=[dict(price=.5,quantity=10,fee_upper_bound_per_contract=.01)])
    b=deepcopy(a);b.update(action_id='kalshi',venue='KALSHI');b['levels'][0]['price']=.4
    return dict(schema='CROSS_VENUE_RESEARCH_V1',snapshot_id='snap',scenarios=[{'id':'yes','probability':.6},{'id':'no','probability':.4}],
                exhaustive_mutually_exclusive=True,scenario_evidence_id='terms',forecast_version='v1',forecast_id='f1',forecast_at=95,
                held_payoff={'yes':0,'no':0},target_quantity=10,actions=[a,b])


class Reports(unittest.TestCase):
    def test_repeats_count_once_latest_recovery_not_lost(self):
        d=R.missed([candidate(1),candidate(2),candidate(3,outcome='ADMITTED',first_refusal=None)],100)
        self.assertEqual(d['recorded_attempts'],3);self.assertEqual(d['distinct_candidate_keys'],1)
        self.assertEqual(d['latest_refused_before_valuation'],0)
    def test_calibration_refusal_may_still_reach_derek(self):
        d=R.missed([candidate(1,first_refusal='VENUE_BOOK_CURRENCY_NOT_ESTABLISHED')],100)
        self.assertEqual(d['latest_refused_before_valuation'],0)
        self.assertEqual(d['refusals_with_unproven_decision_reach'],1)
    def test_mapping_change_does_not_double_count_event(self):
        a=candidate(1);a['us_market_slug']=None
        d=R.missed([a,candidate(2)],100)
        self.assertEqual(d['distinct_candidate_keys'],1)
    def test_unknown_identity_and_sample_not_zero_coverage(self):
        rows=[candidate(i) for i in range(501)];rows[0]['provider_event_id']=None
        d=R.missed(rows,1000);self.assertTrue(d['sample']['truncated']);self.assertEqual(d['unidentified_rows'],1)
    def test_versions_purposes_and_sports_separate(self):
        d=R.forecasts([forecast(),forecast(2,version='v2'),forecast(3,record_purpose='CALIBRATION_ONLY'),forecast(4,sport_family='baseball')],100)
        self.assertEqual(len(d['cohorts']),4)
    def test_forecast_scores_known_values(self):
        d=R.forecasts([forecast(),forecast(2,probability=.2,outcome=0)],100)['cohorts'][0]
        self.assertAlmostEqual(d['brier'],.04);self.assertAlmostEqual(d['log_loss'],.223143551314)
        self.assertEqual(sum(x['n'] for x in d['calibration_bins']),2)
    def test_unresolved_future_and_backdated_outcomes_not_scored(self):
        d=R.forecasts([forecast(),forecast(2,outcome_known=False),forecast(3,outcome_at=101),forecast(4,outcome_at=9)],100)['cohorts'][0]
        self.assertEqual((d['n_resolved'],d['n_pending'],d['n_invalid_outcomes']),(1,2,1))
    def test_repeated_predictions_use_earliest_in_sample(self):
        d=R.forecasts([forecast(2,event_key='event1',decided_at=15,probability=.1),forecast()],100)['cohorts'][0]
        self.assertEqual(d['n_forecasts'],1);self.assertAlmostEqual(d['brier'],.04)
    def test_extreme_probability_and_nan(self):
        d=R.forecasts([forecast(probability=0),forecast(2,probability=float('nan'))],100)
        self.assertTrue(d['cohorts'][0]['log_loss']>30);self.assertEqual(d['excluded']['INVALID_FORECAST_OR_IDENTITY'],1)
    def test_health_stale_is_not_authority_or_zero(self):
        d=R.connectors({'beat_at':1,'cache':{'markets':40}},[],100)
        self.assertEqual(d['status'],'STALE_OR_MISSING_TELEMETRY');self.assertFalse(d['execution_authority'])
        self.assertIsNone(d['connectors'][0]['unknown_change_age_markets'])
    def test_health_counters_allowlisted_no_secret_leak(self):
        d=R.connectors({'beat_at':99,'token':'secret','cache':{},'coverage_census':{'states':{'MATCHED':3,'token':'secret'}}},[],100)
        self.assertNotIn('secret',json.dumps(d));self.assertEqual(d['connectors'][0]['coverage_states'],{'MATCHED':3})
    def test_actual_tool_dispatch_account_and_readonly_transaction(self):
        class Tx:
            async def __aenter__(self): pass
            async def __aexit__(self,*args): pass
        class Conn:
            def transaction(self,**kwargs):
                assert kwargs==dict(readonly=True,isolation='repeatable_read');return Tx()
            async def fetch(self,sql,*args):
                assert sql==R.OPPORTUNITIES_SQL;return [candidate(1)]
        d=asyncio.run(T.Toolkit('DEREK',100).read(Conn(),'missed_opportunities'))
        self.assertEqual(d['status'],'OK');self.assertEqual(d['data']['latest_refused_before_valuation'],1)


class CrossVenue(unittest.TestCase):
    def test_cheaper_executable_venue_wins(self):
        d=C.compare(snapshot(),100);self.assertEqual(d['best_recorded_action_or_hold'],'kalshi')
        self.assertAlmostEqual(d['actions'][1]['expected_increment_lower_bound'],1.9)
        self.assertFalse(d['execution_authority'])
    def test_fees_can_reverse_headline_price(self):
        s=snapshot();s['actions'][1]['levels'][0]['fee_upper_bound_per_contract']=.2
        self.assertEqual(C.compare(s,100)['best_recorded_action_or_hold'],'poly')
    def test_partial_fill_visible_and_zero_fill_in_risk(self):
        s=snapshot();s['actions'][1]['levels'][0]['quantity']=2
        d=C.compare(s,100)['actions'][1]
        self.assertEqual(d['fill_fraction'],.2);self.assertEqual(d['unfilled_quantity'],8)
        self.assertAlmostEqual(d['worst_case_over_partial_fills'],-.82)
    def test_settlement_mismatch_refused(self):
        s=snapshot();s['actions'][1]['scenario_evidence_id']='different overtime rules'
        self.assertEqual(C.compare(s,100)['actions'][1]['reason'],'SETTLEMENT_SCENARIO_MISMATCH')
    def test_future_or_stale_quote_refused(self):
        for at in (101,69):
            s=snapshot();s['actions'][0]['book_at']=at
            self.assertEqual(C.compare(s,100)['actions'][0]['status'],'NOT_COMPARABLE')
    def test_missing_rules_not_invented(self):
        s=snapshot();s['actions'][0]['rules_verified']=False
        self.assertEqual(C.compare(s,100)['actions'][0]['reason'],'UNVERIFIED_MAPPING_OR_RULES')
    def test_cannot_sell_other_venue_holding(self):
        s=snapshot();s['actions'][1].update(side='SELL',inventory_venue='POLYMARKET_US')
        self.assertEqual(C.compare(s,100)['actions'][1]['reason'],'SELL_REQUIRES_SAME_VENUE_INVENTORY')
    def test_valid_sell_and_hedge_preserve_payoffs(self):
        s=snapshot();s['held_payoff']={'yes':10,'no':0}
        a=s['actions'][0];a.update(side='SELL',inventory_venue=a['venue'],inventory_contract_id=a['contract_id'],inventory_evidence_id='inventory',inventory_at=95,available_quantity=10)
        d=C.compare(s,100)['actions'][0]
        self.assertEqual(d['scenario_payoff_lower_bounds'],{'yes':4.9,'no':4.9})
    def test_hold_beats_negative_ev_purchase(self):
        s=snapshot()
        for a in s['actions']: a['levels'][0]['price']=.9
        self.assertEqual(C.compare(s,100)['best_recorded_action_or_hold'],'HOLD')
    def test_venue_size_rounding_and_minimum(self):
        s=snapshot();s['target_quantity']=10.7
        self.assertEqual(C.compare(s,100)['actions'][0]['rounded_quantity'],10)
        s['actions'][0]['minimum_quantity']=20
        self.assertEqual(C.compare(s,100)['actions'][0]['reason'],'BELOW_MINIMUM_SIZE')
    def test_insufficient_cash_not_executable(self):
        s=snapshot();s['actions'][0]['available_cash']=1
        self.assertEqual(C.compare(s,100)['actions'][0]['reason'],'INSUFFICIENT_VENUE_CASH')
    def test_invalid_probability_no_ranking(self):
        s=snapshot();s['scenarios'][0]['probability']=.9
        self.assertEqual(C.compare(s,100)['status'],'UNAVAILABLE')
    def test_zero_unknown_forecast_not_assumed(self):
        s=snapshot();s['forecast_at']=None
        self.assertEqual(C.compare(s,100)['status'],'UNAVAILABLE')
    def test_missing_snapshot_returns_explicit_blocker(self):
        class Conn:
            async def fetchval(self,sql,*args): return 1 if 'paper_orders' in sql else None
        d=asyncio.run(C.read(Conn(),'account','g',100))
        self.assertEqual(d['reason'],'NO_VERIFIED_CROSS_VENUE_SNAPSHOT')
    def test_scope_does_not_leak_another_account(self):
        class Conn:
            async def fetchval(self,sql,*args): return 1 if 'paper_orders' in sql else {'account_id':'other','group_id':'g'}
        self.assertEqual(asyncio.run(C.read(Conn(),'account','g',100))['reason'],'SNAPSHOT_SCOPE_MISMATCH')

if __name__=='__main__': unittest.main()
