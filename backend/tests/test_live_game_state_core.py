import copy
from datetime import datetime
import unittest
from sportsassets.live_game_state import core as C
from sportsassets.live_game_state.storage import fixture_from_row,validate_transition
from _bettor_live_game_state_test_helpers import fixture,espn,odds,parsed,raw,NOW,START

class IdentityTests(unittest.TestCase):
    def test_exact_match(self): self.assertIsNotNone(C.match_fixture(fixture(),[parsed()])[0])
    def test_venue_namespace_remains_separate(self):
        p=C.bind_game(fixture(venue="KALSHI"),parsed())
        self.assertEqual(C.display(p,venue="POLYMARKET_US",event_id=fixture().event_id,now=NOW)["status"],"UNAVAILABLE")
    def test_league_namespace_rejects_wnba_as_nba(self):
        self.assertIsNone(C.match_fixture(fixture(league="WNBA"),[dict(parsed(),league="NBA")])[0])
    def test_reversed_home_away_rejected(self):
        g=parsed();g["home"],g["away"]=g["away"],g["home"]
        self.assertIsNone(C.match_fixture(fixture(),[g])[0])
    def test_city_only_not_auto_matched(self):
        self.assertIsNone(C.match_fixture(fixture(home="Boston"),[parsed()])[0])
    def test_substring_not_auto_matched(self):
        self.assertIsNone(C.match_fixture(fixture(home="Red Sox"),[parsed()])[0])
    def test_explicit_alias_with_evidence(self):
        a=C.Aliases([dict(league="MLB",canonical="Boston Red Sox",alias="Boston",evidence_id="TEST_ALIAS")])
        self.assertIsNotNone(C.match_fixture(fixture(home="Boston"),[parsed()],a)[0])
    def test_alias_without_evidence_refused(self):
        with self.assertRaises(C.ScoreError): C.Aliases([dict(league="MLB",canonical="Boston Red Sox",alias="Boston")])
    def test_alias_collision_refused(self):
        with self.assertRaises(C.ScoreError): C.Aliases([
            dict(league="MLB",canonical="A team",alias="City",evidence_id="a"),
            dict(league="MLB",canonical="B team",alias="City",evidence_id="b")])
    def test_wrong_start_rejected(self): self.assertIsNone(C.match_fixture(fixture(start_at=START+7200),[parsed()])[0])
    def test_doubleheader_ambiguous_not_nearest_picked(self):
        g=parsed();self.assertEqual(C.match_fixture(fixture(),[g,dict(g,provider_event_id="other")])[1],"AMBIGUOUS_SCORE_FIXTURE")
    def test_doubleheader_separate_start_resolves(self):
        g=parsed();self.assertEqual(C.match_fixture(fixture(),[g,dict(g,provider_event_id="other",start_at=START+7200)])[0]["provider_event_id"],g["provider_event_id"])
    def test_game_number_required_when_declared(self): self.assertIsNone(C.match_fixture(fixture(game_number=2),[parsed()])[0])
    def test_pinned_provider_id_cannot_remap(self):
        r=raw();self.assertIsNone(C.match_fixture(fixture(),[dict(parsed(),provider_event_id="new")],binding=r["identity"])[0])
    def test_duplicate_provider_id_conflict(self):
        g=parsed();self.assertEqual(C.match_fixture(fixture(),[g,dict(g,home_score=7)])[1],"DUPLICATE_PROVIDER_ID_CONFLICT")
    def test_invalid_fixture_team_pair(self):
        with self.assertRaises(C.ScoreError): fixture(away="Boston Red Sox")
    def test_timestamp_naive_rejected(self): self.assertIsNone(C.epoch(datetime(2026,10,7)))
    def test_nonfinite_rejected(self):
        for v in (float('nan'),float('inf'),True,None): self.assertIsNone(C.finite(v))
    def test_fingerprint_does_not_change_on_new_evidence_receipt(self):
        self.assertEqual(fixture(evidence_id="a").fingerprint,fixture(evidence_id="b").fingerprint)
    def test_bind_cannot_assert_mismatched_identity(self):
        with self.assertRaises(C.ScoreError): C.bind_game(fixture(home="Different Team"),parsed())
    def test_provider_team_ids_required(self): self.assertIsNone(C.match_fixture(fixture(),[dict(parsed(),home_id="")])[0])

class ParserTests(unittest.TestCase):
    def test_espn_scores_and_bases(self):
        g=parsed();self.assertEqual((g["home_score"],g["away_score"],g["outs"]),(4,3,1));self.assertEqual(g["bases"],[True,False,False])
    def test_provider_logos_used(self):
        g=parsed();self.assertIn('500-dark',g['away_logo']);self.assertIn('bos.png',g['home_logo'])
    def test_espn_has_no_fabricated_source_time(self): self.assertIsNone(parsed()['source_at'])
    def test_clock_never_assumed_running(self):
        g=parsed();self.assertEqual(g['clock_seconds'],321);self.assertFalse(g['clock_running'])
    def test_competitor_order_irrelevant(self):
        b=espn();b['events'][0]['competitions'][0]['competitors'].reverse()
        g=C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertEqual(g['home_score'],4)
    def test_scheduled_zero_not_shown_as_live_score(self):
        g=C.parse_espn(espn(state="STATUS_SCHEDULED"),league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertIsNone(g['home_score']);self.assertIsNone(g['clock_seconds'])
    def test_unknown_game_status_is_not_live(self):
        g=C.parse_espn(espn(state="STATUS_NEW_UNDOCUMENTED"),league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertEqual(g['game_status'],"UNKNOWN")
    def test_malformed_status_detail_null_safe(self):
        b=espn();b['events'][0]['competitions'][0]['status']['type']['shortDetail']=None
        C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")
    def test_unknown_base_occupancy_not_empty_default(self):
        b=espn();del b['events'][0]['competitions'][0]['situation']['onSecond']
        g=C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertIsNone(g['bases'])
    def test_negative_score_unknown(self):
        b=espn();b['events'][0]['competitions'][0]['competitors'][0]['score']="-1"
        g=C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertIsNone(g['home_score'])
    def test_multiple_competitions_not_guessed(self):
        b=espn();b['events'][0]['competitions']*=2
        self.assertEqual(C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test"),[])
    def test_payload_league_mismatch(self):
        with self.assertRaises(C.ScoreError): C.parse_espn(espn(),league="NBA",received_at=NOW,observed_at=NOW,payload_hash="test")
    def test_missing_events_is_error_not_empty_success(self):
        with self.assertRaises(C.ScoreError): C.parse_espn({},league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")
    def test_empty_events_is_valid(self): self.assertEqual(C.parse_espn({'events':[]},league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test"),[])
    def test_odds_has_own_update_timestamp(self):
        g=C.parse_odds(odds(),league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertEqual(g['source_at'],NOW-5);self.assertEqual(g['home_score'],4)
    def test_fallback_has_no_invented_clock_play_or_logos(self):
        g=C.parse_odds(odds(),league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertIsNone(g['clock_seconds']);self.assertEqual(g['last_play'],'');self.assertIsNone(g['home_logo'])
    def test_odds_not_completed_does_not_prove_live(self):
        g=C.parse_odds(odds(),league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")[0]
        self.assertEqual(g['game_status'],'SCORES_REPORTED')
    def test_odds_wrong_sport_excluded(self): self.assertEqual(C.parse_odds(odds(),league="NBA",received_at=NOW,observed_at=NOW,payload_hash="test"),[])
    def test_odds_duplicate_scores_fail_closed(self):
        b=odds();b[0]['scores']*=2
        with self.assertRaises(C.ScoreError): C.parse_odds(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test")
    def test_summary_shape(self):
        b={'header':espn()['events'][0]}
        g=C.parse_espn(b,league="MLB",received_at=NOW,observed_at=NOW,payload_hash="test",summary=True)[0]
        self.assertEqual(g['detail_level'],'SUMMARY')
    def test_logo_allowlist(self):
        for url in ('javascript:alert(1)','https://a.espncdn.com.evil/i/teamlogos/a.png','https://evil.com/a.png',
                    'http://a.espncdn.com/i/teamlogos/a.png','https://a.espncdn.com/i/teamlogos/../a.png',
                    'https://x@a.espncdn.com/i/teamlogos/a.png','https://a.espncdn.com/i/teamlogos/a.png?secret=x'):
            with self.subTest(url=url): self.assertIsNone(C.logo_url(url))

class FreshnessTests(unittest.TestCase):
    def d(self,r=None,now=NOW): return C.display(r or raw(),venue=fixture().venue,event_id=fixture().event_id,now=now)
    def test_recent_espn_explicitly_retrieval_only(self):
        d=self.d();self.assertEqual(d['status'],'CURRENT');self.assertIsNone(d['provider_current']);self.assertEqual(d['freshness_basis'],'RETRIEVAL_ONLY')
    def test_stales_after_expiry(self): self.assertEqual(self.d(now=NOW+16)['status'],'STALE')
    def test_exact_freshness_boundary(self): self.assertEqual(self.d(now=NOW+15)['status'],'CURRENT')
    def test_http_age_preserved(self): self.assertEqual(self.d(raw(observed_at=NOW-60))['status'],'STALE')
    def test_future_receipt_invalid(self): self.assertEqual(self.d(raw(received_at=NOW+1))['status'],'UNAVAILABLE')
    def test_future_source_invalid(self): self.assertEqual(self.d(raw(source_at=NOW+1))['status'],'UNAVAILABLE')
    def test_reader_does_not_restamp(self):
        r=raw();d=self.d(r,now=NOW+200);self.assertEqual(d['received_at'],NOW);self.assertEqual(r['received_at'],NOW)
    def test_missing_identity_refused(self):
        r=raw();r['identity_verified']=False;self.assertEqual(self.d(r)['status'],'UNAVAILABLE')
    def test_tampered_binding_digest_refused(self):
        r=raw();r['identity']['provider_home_id']='tampered';self.assertEqual(self.d(r)['status'],'UNAVAILABLE')
    def test_legacy_payload_not_misread(self):
        self.assertEqual(C.display({'source_at':NOW},venue='POLYMARKET_US',event_id='a',now=NOW)['status'],'UNAVAILABLE')
    def test_final_is_display_only(self):
        d=self.d(raw(game_status='FINAL'));self.assertEqual(d['authority'],C.AUTHORITY);self.assertFalse(d['execution_authority'])
    def test_no_scores_in_live_state_refused(self): self.assertEqual(self.d(raw(home_score=None))['why'],'SCORE_VALUE_MISSING')
    def test_fallback_own_freshness(self):
        g=C.parse_odds(odds(),league='MLB',received_at=NOW,observed_at=NOW,payload_hash='test')[0]
        r=C.bind_game(fixture(),g)
        self.assertEqual(self.d(r,now=NOW+39)['status'],'CURRENT');self.assertEqual(self.d(r,now=NOW+41)['status'],'STALE')
    def test_fallback_missing_timestamp_not_current(self):
        g=C.parse_odds(odds(),league='MLB',received_at=NOW,observed_at=NOW,payload_hash='test')[0];g['source_at']=None
        self.assertEqual(self.d(C.bind_game(fixture(),g))['why'],'ODDS_UPDATE_TIMESTAMP_MISSING')
    def test_upstream_error_disclosed_not_restamped(self):
        d=C.display(raw(),venue=fixture().venue,event_id=fixture().event_id,now=NOW,issue='ESPN:HTTP_429')
        self.assertEqual(d['provider_issue'],'ESPN:HTTP_429');self.assertEqual(d['received_at'],NOW)
    def test_regressing_observation_refused(self):
        with self.assertRaises(C.ScoreError): validate_transition(raw(),raw(received_at=NOW+10,observed_at=NOW-10))
    def test_score_correction_new_timestamp_allowed(self): validate_transition(raw(source_at=NOW-5),raw(source_at=NOW,home_score=3))
    def test_same_update_different_score_refused(self):
        with self.assertRaises(C.ScoreError): validate_transition(raw(source_at=NOW),raw(source_at=NOW,home_score=5))
    def test_canonical_source_adapter_refuses_titles(self):
        with self.assertRaises(C.ScoreError): fixture_from_row({'premap':{'event_slug':'mlb:x','sport':'MLB','game_start':START,'title':'Yankees at Red Sox'}})
    def test_canonical_source_adapter_explicit_fields(self):
        f=fixture_from_row({'premap':{'event_slug':'mlb:x','sport':'MLB','game_start':START,'home_team':'Boston Red Sox','away_team':'New York Yankees'}})
        self.assertEqual(f.league,'MLB')
