"""Pure behavioral regressions; no network, DB, keys, or order calls."""
import unittest
import ast
from pathlib import Path
from dataclasses import replace
from sportsassets import pinnapi_census as C
from sportsassets import market_comparison as M
from sportsassets.pinnapi_market_inventory import inventory


def rows(title='Game 1: ATL Braves vs. LA Dodgers', slug='mlb-atl-lad-2026-10-03'):
    return [dict(identifier=str(i),event_slug=slug,event_title=title,
                 team_name=t,team_id=3001+i,team_league='mlb',
                 sports_type='baseball_team_full_game_winner',
                 kind='side',line='',game_start=10000)
            for i,t in enumerate(['atlanta braves','los angeles dodgers'])]


def feed():
    return [{'id':1,'home':'Atlanta Braves','away':'Los Angeles Dodgers',
             'start':10000,'live':False}]


def census(rs, fs=None):
    return C.census(rs,{6:feed() if fs is None else fs},
                    subscribed_sports={6},synced=True,now=9000)


class CensusTests(unittest.TestCase):
    def test_clock_proof_matches_existing_worker_ast(self):
        import sportsassets
        root=Path(sportsassets.__file__).parent
        def parts(path):
            tree=ast.parse(path.read_text())
            names={'_LINE_CTX','_canon_line','_lines_of','_clock_artifact'}
            return [ast.dump(n,include_attributes=False) for n in tree.body if
                    (isinstance(n,ast.FunctionDef) and n.name in names) or
                    (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and
                                                    t.id in names for t in n.targets))]
        self.assertEqual(parts(root/'workers/premap.py'),parts(root/'market_clock_artifact.py'))

    def test_observed_game_prefix_and_city_abbreviations(self):
        out=census(rows())
        self.assertEqual(out['states'],{'MATCHED_SUPPORTED':2})
        self.assertFalse(out['decision_eligibility_proven'])

    def test_second_observed_fixture(self):
        rs=rows('Game 1: SD Padres vs. MIL Brewers','mlb-sd-mil-2026-10-03')
        for r,t in zip(rs,['san diego padres','milwaukee brewers']): r['team_name']=t
        fs=[dict(id=2,home='Milwaukee Brewers',away='San Diego Padres',start=10000)]
        self.assertEqual(census(rs,fs)['states'],{'MATCHED_SUPPORTED':2})

    def test_titles_alone_never_repair_missing_structured_names(self):
        rs=rows()
        for r in rs: r['team_name']=None
        self.assertEqual(census(rs)['states'],{'STRUCTURED_PARTICIPANTS_NOT_TWO':2})

    def test_wrong_meeting_and_doubleheader_ambiguity(self):
        fs=feed()+[dict(feed()[0],id=2,start=10100)]
        self.assertEqual(census(rows(),fs)['states'],{'AMBIGUOUS_FEED_EVENT':2})
        self.assertEqual(census(rows(),[dict(feed()[0],start=10000+86400)])['states'],
                         {'NO_FEED_EVENT':2})

    def test_nonfinite_or_missing_start_never_matches(self):
        for value in (None,float('nan'),float('inf')):
            self.assertEqual(census(rows(),[dict(feed()[0],start=value)])['states'],
                             {'NO_FEED_EVENT':2})
            rs=rows(); rs[0]['game_start']=value
            self.assertEqual(census(rs)['states'],{'VENUE_EVENT_START_CONFLICT':2})

    def test_qualifiers_and_city_collisions_fail(self):
        for name in ('Atlanta Braves Women','Atlanta Braves U21','Atlanta United'):
            self.assertEqual(census(rows(),[dict(feed()[0],home=name)])['states'],
                             {'NO_FEED_EVENT':2})

    def test_structured_league_conflict(self):
        rs=rows(); rs[1]['team_league']='different-competition'
        self.assertEqual(census(rs)['states'],{'VENUE_EVENT_LEAGUE_CONFLICT':2})

    def test_player_and_period_never_become_moneyline(self):
        for st in ('baseball_player_home_runs','baseball_team_first_five_winner',
                   'baseball_team_inning1_winner','baseball_game_extra_innings'):
            rs=rows(); rs[0]['sports_type']=st; rs[1]['sports_type']=st
            out=census(rs)
            self.assertEqual(out['states'],{'MATCHED_UNSUPPORTED_FAMILY':2})
            self.assertTrue(out['reconciled'])

    def test_empty_type_never_becomes_moneyline(self):
        self.assertFalse(C.family_of('side','')[1])
        self.assertFalse(C.family_of('side','0','baseball_team_full_game_winner')[1])

    def test_observed_clock_artifact_and_real_line_veto(self):
        rs=rows()
        for r in rs:
            r.update(line='00',question='Who wins on October 3, 2026 at 5:00 AM UTC?',
                     signed='',side_norm=r['team_name'])
        out=census(rs)
        self.assertEqual(out['states'],{'MATCHED_SUPPORTED':2})
        self.assertEqual(out['proved_clock_artifact_rows'],2)
        self.assertTrue(out['reconciled'])
        rs[0]['signed']='-00'; rs[1]['side_norm']='team 00'
        self.assertEqual(census(rs)['states'],{'MATCHED_UNSUPPORTED_FAMILY':2})
        rs[0]['sports_type']='baseball_team_full_game_spread'
        self.assertFalse(C.family_of('side','00',rs[0]['sports_type'],rs[0])[1])


def contract(**kw):
    return replace(M.Contract('fixture:1','baseball','mlb','full_game','moneyline',
                              'team:atl',None,'reviewed-rules:1','evidence:pm'),**kw)


def quote(venue='polymarket_us', price='.50', fee='0', **kw):
    q=M.Quote(venue,venue+':1',contract(),venue+':book1',100,'OPEN',
              ((price,'2000'),),(), 'fee-v1',lambda action,levels:fee,True,True)
    return replace(q,**kw)


class ComparisonTests(unittest.TestCase):
    def compare(self, *quotes, **kw):
        return M.compare_entries(contract(),quotes,quantity='1000',now=101,max_age_s=5,**kw)

    def test_fee_reverses_displayed_best_price(self):
        out=self.compare(quote(price='.50'),quote('kalshi','.49','20'))
        self.assertEqual(out['best']['venue'],'polymarket_us')
        self.assertFalse(out['execution_authorized'])

    def test_kalshi_wins_when_net_cost_is_lower(self):
        self.assertEqual(self.compare(quote(price='.53'),quote('kalshi','.49','10'))
                         ['best']['venue'],'kalshi')

    def test_depth_walk_not_best_level_multiplication(self):
        q=quote(asks=(('.4','10'),('.6','990')))
        self.assertEqual(self.compare(q)['best']['gross_usd'],'598.0')
        self.assertIsNone(self.compare(quote(asks=(('.4','10'),)))['best'])

    def test_stale_future_suspended_and_no_authority_rejected(self):
        for q in (quote(observed_at=102),quote(observed_at=90),quote(status='SUSPENDED'),
                  quote(authority=False),quote(account_ready=False)):
            self.assertIsNone(self.compare(q)['best'])

    def test_different_payoff_period_line_and_side_rejected(self):
        for c in (contract(period='first_five'),contract(selection='team:lad'),
                  contract(rules_class='regulation_only'),contract(line='1.5'),
                  contract(rules_evidence=''),contract(competition='npb')):
            self.assertIsNone(self.compare(quote(contract=c))['best'])

    def test_duplicates_and_unknown_fees_cannot_win(self):
        self.assertIsNone(self.compare(quote(),quote())['best'])
        for q in (quote(fee=None),quote(fee_version=''),quote(fee='-1')):
            self.assertIsNone(self.compare(q)['best'])

    def test_cross_venue_buy_is_not_sale(self):
        out=M.position_action(holding_venue='polymarket_us',action_venue='kalshi',action='BUY')
        self.assertFalse(out['closes_original'])
        self.assertEqual(M.position_action(holding_venue='polymarket_us',action_venue='kalshi',
                                           action='SELL')['kind'],'REFUSE')

    def management(self, candidates, probabilities=None):
        return M.compare_management_actions(holding_payoffs={'win':100,'lose':0,'void':50},
                    scenarios=['win','lose','void'],candidates=candidates,
                    probabilities=probabilities,evidence_id='joint-reviewed-proof')

    def hedge(self, cost='-40', kind='DIRECT_PAIR'):
        return dict(id='kalshi-buy',kind=kind,cash_delta=cost,
                    payoff_delta={'win':0,'lose':100,'void':30},
                    evidence_id='joint-reviewed-proof',quote_evidence='kalshi:book-fee-v1')

    def test_hedge_wins_or_loses_based_on_economics(self):
        p={'win':'.5','lose':'.4','void':'.1'}
        self.assertEqual(self.management([self.hedge()],p)['ranked_by_expected_value'][0]['id'],
                         'kalshi-buy')
        self.assertEqual(self.management([self.hedge('-60')],p)['ranked_by_expected_value'][0]['id'],
                         'HOLD')

    def test_unknown_probabilities_not_zero_and_missing_scenarios_refused(self):
        out=self.management([self.hedge()])
        self.assertEqual(out['ranked_by_expected_value'],[])
        self.assertEqual(out['probability_status'],'UNKNOWN_NOT_ZERO')
        c=self.hedge(); del c['payoff_delta']['void']
        self.assertFalse(self.management([c])['assessments'][1]['eligible_for_comparison'])

    def test_indirect_hedge_preserves_scenario_specific_downside(self):
        c=self.hedge(kind='INDIRECT_PAIR'); c['payoff_delta']['void']=0
        out=self.management([c])['assessments'][1]
        self.assertEqual(out['terminal_values']['void'],'10')
        self.assertEqual(out['additional_cash_required'],'40')


class InventoryTests(unittest.TestCase):
    def event(self):
        return dict(event_id=1,parent_id=None,sport_id=1,league_id=10,event_type='live',
                    last=99999999,home='A',away='B',periods={'num_0':dict(number=0,
                    money_line={'home':'2','away':'3','draw':'4'},
                    spreads={'-1.5':dict(hdp='-1.5',home='2.1',away='1.9')},
                    totals={'2.5':dict(points='2.5',over='2',under='2')},
                    team_totals={'home':{'1.5':dict(points='1.5',over='2',under='2')}},
                    team_total={'home':dict(points='1.5',over='2',under='2')})})

    def test_every_standard_family_and_draw_is_retained(self):
        out=inventory({'last':123,'events':[self.event()]})
        self.assertEqual(len(out['rows']),9)
        self.assertEqual(set(r['family'] for r in out['rows']),
                         {'moneyline','spread','total','team_total'})
        self.assertTrue(all(r['price_changed_at'] is None for r in out['rows']))
        self.assertTrue(all(not r['eligible_for_decision'] for r in out['rows']))
        self.assertEqual(out['cursor'],123)

    def test_parent_period_and_specials_not_silently_collapsed(self):
        e=self.event(); e['parent_id']=100
        e['periods']['num_1']={'number':1,'money_line':{'home':'2','away':'2'}}
        e['special_markets']={'unknown':{}}
        out=inventory({'events':[e]})
        self.assertEqual(len(out['rows']),11)
        self.assertEqual({r['period'] for r in out['rows']},{0,1})
        self.assertTrue(all(r['parent_id']==100 for r in out['rows']))
        self.assertEqual(out['gaps'][0]['reason'],'SPECIAL_SCHEMA_REQUIRES_VERIFIED_ADAPTER')

    def test_no_american_odds_autodetection_and_line_conflicts(self):
        e=self.event(); e['periods']['num_0']['money_line']['home']=-164
        e['periods']['num_0']['totals']['2.5']['points']='3.5'
        out=inventory({'events':[e]})
        self.assertIn('INVALID_DECIMAL_ODDS_OR_LINE',{g['reason'] for g in out['gaps']})
        self.assertIn('LINE_KEY_CONFLICT',{g['reason'] for g in out['gaps']})


if __name__ == '__main__': unittest.main()
