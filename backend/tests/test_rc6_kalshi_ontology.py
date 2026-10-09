"""THE KALSHI ONTOLOGY (RC6 lane K): every listed Kalshi sports binary gets
a sport, family, period, line and subject from the venue's own series tag
and the contract's own rules_primary sentence -- or a named refusal.

Production RC5 (pm-acceptance 37836393458, market_plane.json
snapshot.coverage): by_venue.KALSHI = CODE_CONTROLLED_GAP 76,467, every one
ONTOLOGY_GAPS:KALSHI_ONTOLOGY_NOT_MAPPED (market_plane.populate.
kalshi_contract_row assigned no sport, family or period). On 2026-10-09 the
active Kalshi registry held 80,497 such rows (research
rc6_kalshi_ontology_explore_a, run 37869672219).

THE SAMPLES BELOW ARE REAL CATALOGUE ROWS: ticker, event ticker, series,
series tags and rules_primary exactly as the production registry / rules
tables held them on 2026-10-09 (read-only research
rc6_kalshi_ontology_corpus_0..3, runs 37870218671, 37870290788, 37870332832,
37870372988; public venue market text). Each expectation was checked by
hand against its sentence.

Fails on 412c4962: kalshi_ontology does not exist there, and the registry
row / coverage / settlement tests read KALSHI_ONTOLOGY_NOT_MAPPED,
CODE_CONTROLLED_GAP and KALSHI_CONTRACT_NOT_MAPPED_TO_A_BETTOR_FAMILY for
a mapped contract.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import time

import asyncpg
import pytest

from sportsassets import kalshi_market_data as KMD
from sportsassets import kalshi_ontology as KO
from sportsassets import settlement_rule_registry as SRR
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import rules as RULES
from sportsassets.market_plane import settlement as S

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
PKG = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
#: a real Kalshi rules_secondary (KXNBAGAME, 2026-10-07 capture,
#: research/kalshi_canonical_venue/CONTRACT_TERMS_2026-10-07)
SECONDARY = ("If the game is postponed but begins within 48 hours of its "
             "originally scheduled start time, the market will remain open "
             "and resolve based on the official final result. If the game is "
             "cancelled or not started within 48 hours of its originally "
             "scheduled start, all markets will resolve to a fair price.")


def run(coro):
    return asyncio.run(coro)


def market(ticker, event, series, tags, rp, **kw):
    return dict({"ticker": ticker, "event_ticker": event, "rules_primary": rp,
                 "rules_secondary": SECONDARY, "status": "active",
                 "market_type": "binary", "title": "display only",
                 "yes_sub_title": "display only",
                 "_series": {"ticker": series, "title": "Series title",
                             "tags": list(tags), "category": "Sports"}}, **kw)


# (ticker, event ticker, series, series tags, rules_primary, expected)
# expected: ("MAPPED", family, period, operator, line, unit, subject type)
#        or ("REFUSED", refusal code)
SAMPLES = [
    ('KXNFLFFPTS-26OCT11NYGWAS-NYGDZVADA34-7P3', 'KXNFLFFPTS-26OCT11NYGWAS', 'KXNFLFFPTS', ['Football'],
     'If Dominic Zvada records above 7.3 fantasy points in the Full game (including overtime) of the New York G vs Washington Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_PLAYER_PROP_NOT_IN_AGREED_UNIVERSE')),
    ('KXNCAAFBOWLGAME-26-PSU', 'KXNCAAFBOWLGAME-26', 'KXNCAAFBOWLGAME', ['Football'],
     'If Penn St. is selected to play in a bowl game or the College Football Playoff (CFP) in the 2026-27 college football season, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_OUTRIGHT_OR_SEASON_CONTRACT_NOT_IN_MAPPER')),
    ('KXEPLSCORE-26OCT10MUNTOT-MUN5TOT2', 'KXEPLSCORE-26OCT10MUNTOT', 'KXEPLSCORE', ['Soccer'],
     'If Manchester United wins 5-2 in the Manchester United vs Tottenham Hotspur professional EPL soccer game originally scheduled for Oct 10, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:EXACT_SCORE')),
    ('KXEFLL1GAME-26OCT15MATOXU-TIE', 'KXEFLL1GAME-26OCT15MATOXU', 'KXEFLL1GAME', ['Soccer'],
     'If Tie is the result of the Mansfield vs Oxford United professional EFL League One soccer game originally scheduled for Oct 15, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXNFLWINMARGIN-26OCT11HOUTEN-TEN7TO14', 'KXNFLWINMARGIN-26OCT11HOUTEN', 'KXNFLWINMARGIN', ['Football'],
     'If Tennessee has a positive point differential of between 7 and 14 points against Houston in the full game (including overtime) of the Houston vs Tennessee Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:WINNING_MARGIN_BAND')),
    ('KXNFL1HTOTAL-26OCT11DETARI-28', 'KXNFL1HTOTAL-26OCT11DETARI', 'KXNFL1HTOTAL', ['Football'],
     'If DET Lions and ARI Cardinals collectively score more than 27.5 points in the 1st half of the DET Lions vs ARI Cardinals Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FIRST_HALF', 'GT', '27.5', 'POINTS', 'EVENT')),
    ('KXNFL1HTOTAL-26OCT11LVNE-4', 'KXNFL1HTOTAL-26OCT11LVNE', 'KXNFL1HTOTAL', ['Football'],
     'If LV Raiders and NE Patriots collectively score more than 3.5 points in the 1st half of the LV Raiders vs NE Patriots Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FIRST_HALF', 'GT', '3.5', 'POINTS', 'EVENT')),
    ('KXNCAAF1QSPREAD-26OCT10BSUFRES-FRES4', 'KXNCAAF1QSPREAD-26OCT10BSUFRES', 'KXNCAAF1QSPREAD', ['Football'],
     'If Fresno St. wins by more than 3.5 points in the 1st quarter of the Boise St. vs Fresno St. college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'Q1', 'GT', '3.5', 'POINTS', 'TEAM')),
    ('KXCS2MAP-26OCT082100OTFAR-2-FAR', 'KXCS2MAP-26OCT082100OTFAR-2', 'KXCS2MAP', ['Esports'],
     'If FarmVille wins map 2 in the ESL Challenger League North America Cup #3 2026: Overtake vs. FarmVille CS2 match originally scheduled for Oct 8, 2026 at 9:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'MAP_2', None, None, None, 'TEAM')),
    ('KXNCAAFSPREAD-26OCT10ULLLT-ULL6', 'KXNCAAFSPREAD-26OCT10ULLLT', 'KXNCAAFSPREAD', ['Football'],
     'If Louisiana wins by more than 5.5 points in the Louisiana vs Louisiana Tech college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GT', '5.5', 'POINTS', 'TEAM')),
    ('KXNHL1P-26OCT08TORVGK-TOR', 'KXNHL1P-26OCT08TORVGK', 'KXNHL1P', ['Hockey'],
     'If Toronto wins the 1st period of the Toronto vs Vegas NHL game originally scheduled for Oct 8, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'P1', None, None, None, 'TEAM')),
    ('KXNFLTEAMSACK-26OCT11NYGWAS-WAS5', 'KXNFLTEAMSACK-26OCT11NYGWAS', 'KXNFLTEAMSACK', ['Football'],
     'If Washington records 5+ sacks in the New York G vs Washington Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_TEAM_STAT_PROP_NOT_IN_MAPPER')),
    ('KXNCAAF1QTOTAL-26OCT10MDOSU-6', 'KXNCAAF1QTOTAL-26OCT10MDOSU', 'KXNCAAF1QTOTAL', ['Football'],
     'If the teams collectively score more than 5.5 points in the 1st quarter of the Maryland vs Ohio St. college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'Q1', 'GT', '5.5', 'POINTS', 'EVENT')),
    ('KXNCAAF4QTOTAL-26OCT10ARIZWVU-20', 'KXNCAAF4QTOTAL-26OCT10ARIZWVU', 'KXNCAAF4QTOTAL', ['Football'],
     'If the teams collectively score more than 19.5 points in the 4th quarter (excluding overtime) of the Arizona vs West Virginia college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'Q4', 'GT', '19.5', 'POINTS', 'EVENT')),
    ('KXLIGUE1TOTAL-26OCT10PSGMAN-6', 'KXLIGUE1TOTAL-26OCT10PSGMAN', 'KXLIGUE1TOTAL', ['Soccer'],
     'If over 5.5 goals are scored in the PSG vs Le Mans professional Ligue 1 soccer game originally scheduled for Oct 10, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '5.5', 'GOALS', 'EVENT')),
    ('KXISRPLGAME-26OCT10IKSHTA-IKS', 'KXISRPLGAME-26OCT10IKSHTA', 'KXISRPLGAME', ['Soccer'],
     'If Kiryat Shmona wins the Kiryat Shmona vs Hapoel Tel Aviv professional Israeli Premier League soccer game originally scheduled for Oct 10, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXEKSTRAKLASAGAME-26OCT10JAGGOR-JAG', 'KXEKSTRAKLASAGAME-26OCT10JAGGOR', 'KXEKSTRAKLASAGAME', ['Soccer'],
     'If Jagiellonia wins the Jagiellonia vs Gornik Zabrze professional Ekstraklasa soccer game originally scheduled for Oct 10, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXUCLBTTS-26OCT14AVLFEN-BTTS', 'KXUCLBTTS-26OCT14AVLFEN', 'KXUCLBTTS', ['Soccer'],
     'If Aston Villa and Fenerbahce both score a goal in the Aston Villa vs Fenerbahce Champions League match originally scheduled for Oct 14, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:BOTH_TEAMS_TO_SCORE')),
    ('KXNHLFIRSTGOAL-26OCT08DALBUF-DALJHRYCKOWIAN49', 'KXNHLFIRSTGOAL-26OCT08DALBUF', 'KXNHLFIRSTGOAL', ['Hockey'],
     'If Justin Hryckowian scores the 1st goal in the Dallas vs Buffalo NHL game originally scheduled for Oct 8, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:FIRST_SCORER')),
    ('KXNCAAFTEAMTOTAL-26OCT10ARIZWVU-ARIZ21', 'KXNCAAFTEAMTOTAL-26OCT10ARIZWVU', 'KXNCAAFTEAMTOTAL', ['Football'],
     'If Arizona scores over 20.5 points in the Arizona vs West Virginia college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TEAM_SCORE', 'FULL_EVENT', 'GT', '20.5', 'POINTS', 'TEAM')),
    ('KXUFCMOV-26OCT10KARGAT-KARKOTKODQ', 'KXUFCMOV-26OCT10KARGAT', 'KXUFCMOV', ['MMA'],
     'If Ernesta Kareckaite wins by KO/TKO/DQ during the Ernesta Kareckaite vs Melissa Gatto UFC Fight Night: Allen vs. Duncan fight originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:METHOD_OF_VICTORY')),
    ('KXNCAAF1HTEAMTOTAL-26OCT09IOWAWASH-WASH8', 'KXNCAAF1HTEAMTOTAL-26OCT09IOWAWASH', 'KXNCAAF1HTEAMTOTAL', ['Football'],
     'If Washington scores over 7.5 points in the 1st half of the Iowa vs Washington college football game originally scheduled for Oct 9, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TEAM_SCORE', 'FIRST_HALF', 'GT', '7.5', 'POINTS', 'TEAM')),
    ('KXNCAAF1Q-26OCT10MDOSU-TIE', 'KXNCAAF1Q-26OCT10MDOSU', 'KXNCAAF1Q', ['Football'],
     'If neither team wins the 1st quarter of the Maryland vs Ohio St. college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'Q1', None, None, None, 'TIE')),
    ('KXKLEAGUEGAME-26OCT09DAJJEO-TIE', 'KXKLEAGUEGAME-26OCT09DAJJEO', 'KXKLEAGUEGAME', ['Soccer'],
     'If Tie is the result of the Daejeon Citizen vs Jeonbuk professional Korea K League 1 soccer game originally scheduled for Oct 9, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXNFLESCALATORREC-26OCT11PHIJAC-JACPWASHINGTON11', 'KXNFLESCALATORREC-26OCT11PHIJAC', 'KXNFLESCALATORREC', ['Football'],
     'If Each Yes contract pays per reception by Parker Washington in the Philadelphia vs Jacksonville Pro Football game originally scheduled for Oct 11, 2026. The Yes contracts pay based on this schedule: 0 receptions, $0.0000; 1, $0.0003; 2, $0.0029; 3, $0.0098; 4, $0.0233; 5, $0.0455; 6, $0.0787; 7, $0.1250; 8, $0.1865; 9, $0.2656; 10, $0.3644; 11, $0.4850; 12, $0.6297; 13, $0.8006; 14 or more, $1.0000. Each No contract pays $1.00 minus the Yes payout. Yes pays out at most $1.00. Then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_NON_BINARY_PAYOUT')),
    ('KXISRNLTOTAL-26OCT09IROHAF-3', 'KXISRNLTOTAL-26OCT09IROHAF', 'KXISRNLTOTAL', ['Soccer'],
     'If over 2.5 goals are scored in the Ironi Modiin vs Hapoel Afula professional Liga Leumit soccer game originally scheduled for Oct 9, 2026 after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '2.5', 'GOALS', 'EVENT')),
    ('KXNCAAF1HFT-26OCT09IOWAWASH-WASHWASH', 'KXNCAAF1HFT-26OCT09IOWAWASH', 'KXNCAAF1HFT', [],
     'If Washington wins the 1st half and Washington wins the full game in the Iowa vs Washington college football game originally scheduled for Oct 9, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:DOUBLE_RESULT')),
    ('KXEPL1HTOTAL-26OCT10IPSFUL-2', 'KXEPL1HTOTAL-26OCT10IPSFUL', 'KXEPL1HTOTAL', ['Soccer'],
     'If the total goals scored by Ipswich Town and Fulham is more than 1.5 goals in the 1st Half of the Ipswich Town vs Fulham professional EPL soccer game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FIRST_HALF', 'GT', '1.5', 'GOALS', 'EVENT')),
    ('KXNHL3P-26OCT10CARCHI-TIE', 'KXNHL3P-26OCT10CARCHI', 'KXNHL3P', ['Hockey'],
     'If neither team wins the 3rd period (excluding overtime) of the Carolina vs Chicago NHL game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'P3', None, None, None, 'TIE')),
    ('KXNFLRACE-26OCT11MINNO-14-MIN', 'KXNFLRACE-26OCT11MINNO-14', 'KXNFLRACE', ['Football'],
     'If Minnesota is the first to score 14 points in Minnesota vs New Orleans Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:RACE_TO')),
    ('KXWNBA2HTOTAL-26OCT09ATLNY-84', 'KXWNBA2HTOTAL-26OCT09ATLNY', 'KXWNBA2HTOTAL', ['Basketball'],
     "If the teams in the game collectively score more than 83.5 points in the 2nd half (not including overtime) of the Atlanta vs New York Women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'TOTAL', 'SECOND_HALF', 'GT', '83.5', 'POINTS', 'EVENT')),
    ('KXBUNDESLIGA2HTOTAL-26OCT10RBLSGE-3', 'KXBUNDESLIGA2HTOTAL-26OCT10RBLSGE', 'KXBUNDESLIGA2HTOTAL', ['Soccer'],
     'If the total goals scored by Leipzig and Frankfurt is more than 2.5 goals in the 2nd Half of the Leipzig vs Frankfurt professional Bundesliga soccer game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'SECOND_HALF', 'GT', '2.5', 'GOALS', 'EVENT')),
    ('KXWNBA1QWINNER-26OCT09GSLV-LV', 'KXWNBA1QWINNER-26OCT09GSLV', 'KXWNBA1QWINNER', ['Basketball'],
     "If Las Vegas is the winner of the 1st quarter of regulation time in the Golden State vs Las Vegas women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'WINNER', 'Q1', None, None, None, 'TEAM')),
    ('KXESOCCERGAME-26OCT082215PEDRFREN-TIE', 'KXESOCCERGAME-26OCT082215PEDRFREN', 'KXESOCCERGAME', ['Esports'],
     'If Leverkusen (Frenkie) and FSV Mainz 05 (Pedri) tie in the FSV Mainz 05 (Pedri) vs Leverkusen (Frenkie) professional Valhalla Cup 2026 Week #41 eSoccer match originally scheduled for Oct 8, 2026 at 10:15 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXNHLF10G-26OCT08TORVGK-Y', 'KXNHLF10G-26OCT08TORVGK', 'KXNHLF10G', ['Hockey'],
     'If at least 1 goal is scored by either Toronto or Vegas during the first 10 minutes of the first period (from 0:00 elapsed up to, but not including, 10:00 elapsed) in the Toronto vs Vegas NHL game originally scheduled for Oct 8, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:SCORING_EVENT')),
    ('KXMLBHIT-26OCT082000CLECWS-CWSRGRICHUK34-1', 'KXMLBHIT-26OCT081700CLECWS', 'KXMLBHIT', ['Baseball'],
     'If Randal Grichuk records 1+ hits in the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 8:00 PM EDT, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_TICKER_NOT_UNDER_ITS_EVENT')),
    ('KXESOCCERGAME-26OCT082146LUCYQUIN-TIE', 'KXESOCCERGAME-26OCT082146LUCYQUIN', 'KXESOCCERGAME', ['Esports'],
     'If Olympique de Marseille (Quinnie) and Montpellier (Lucy) tie in the Montpellier (Lucy) vs Olympique de Marseille (Quinnie) professional Valkyrie Cup 2026 Week #41 eSoccer match originally scheduled for Oct 8, 2026 at 9:46 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXLALIGACORNERS-26OCT09MCFESP-9', 'KXLALIGACORNERS-26OCT09MCFESP', 'KXLALIGACORNERS', ['Soccer'],
     'If Malaga and Espanyol combined record at least 9+ corners during the entire game (regulation, stoppage and any extra time periods) of the Malaga vs Espanyol professional La Liga soccer game originally scheduled for Oct 9, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:CORNERS')),
    ('KXATPGSPREAD-26OCT08VANMIC-MIC3', 'KXATPGSPREAD-26OCT08VANMIC', 'KXATPGSPREAD', ['Tennis'],
     'If the game differential in favor of Alex Michelsen across the full match is above 2.5 games in the Botic Van de Zandschulp vs Alex Michelsen professional tennis match in the 2026 ATP Shanghai Round Of 64, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GT', '2.5', 'GAMES', 'TEAM')),
    ('KXMLS2H-26OCT10NYRBSD-SD', 'KXMLS2H-26OCT10NYRBSD', 'KXMLS2H', ['Soccer'],
     'If San Diego FC is the winner of the second half in the New York RB vs San Diego FC professional MLS soccer game originally scheduled for Oct 10, 2026 after 45 minutes plus stoppage time, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'SECOND_HALF', None, None, None, 'TEAM')),
    ('KXMLBINNINGWIN-26OCT081700CLECWS-7-CWS', 'KXMLBINNINGWIN-26OCT081700CLECWS-7', 'KXMLBINNINGWIN', ['Baseball'],
     'If Chicago WS scores more runs in the 7th inning of the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'INNING_7', None, None, None, 'TEAM')),
    ('KXNFLWINMARGIN-26OCT11NYGWAS-NYG15PLUS', 'KXNFLWINMARGIN-26OCT11NYGWAS', 'KXNFLWINMARGIN', ['Football'],
     'If New York G has a positive point differential of at least 15 points against Washington in the full game (including overtime) of the New York G vs Washington Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GE', '15', 'POINTS', 'TEAM')),
    ('KXATPEXACTMATCH-26OCT09SHEALT-ALT20', 'KXATPEXACTMATCH-26OCT09SHEALT', 'KXATPEXACTMATCH', ['Tennis'],
     'If Daniel Altmaier wins the Ben Shelton vs Daniel Altmaier professional tennis match in the 2026 ATP Shanghai Round Of 64 by a set score of 2-0, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:SET_SCORE')),
    ('KXBUNDESLIGA2H-26OCT10PADVFB-VFB', 'KXBUNDESLIGA2H-26OCT10PADVFB', 'KXBUNDESLIGA2H', ['Soccer'],
     'If Stuttgart is the winner of the second half in the Paderborn vs Stuttgart professional Bundesliga soccer game originally scheduled for Oct 10, 2026 after 45 minutes plus stoppage time, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'SECOND_HALF', None, None, None, 'TEAM')),
    ('KXWNBA2QWINNER-26OCT09ATLNY-TIE', 'KXWNBA2QWINNER-26OCT09ATLNY', 'KXWNBA2QWINNER', ['Basketball'],
     "If Tie is the result of the 2nd quarter of regulation time in the Atlanta vs New York women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'WINNER', 'Q2', None, None, None, 'TIE')),
    ('KXNFLTIE-26OCT11LVNE-Y', 'KXNFLTIE-26OCT11LVNE', 'KXNFLTIE', ['Football'],
     'If neither team wins and the game ends in a tie in the Las Vegas vs New England Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXATPGTOTAL-26OCT09CERSAF-19', 'KXATPGTOTAL-26OCT09CERSAF', 'KXATPGTOTAL', ['Tennis'],
     'If the number of completed games in the full match is above 18.5 in the Francisco Cerundolo vs Roman Safiullin professional tennis match in the 2026 ATP Shanghai Round Of 64, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '18.5', 'GAMES', 'EVENT')),
    ('KXKBOTOTAL-26OCT090100SAMSSG-6', 'KXKBOTOTAL-26OCT090100SAMSSG', 'KXKBOTOTAL', ['Baseball'],
     'If Samsung Lions and SSG Landers collectively score more than 5.5 runs in the Samsung Lions and SSG Landers KBO game originally scheduled for Oct 9, 2026 at 1:00 AM EDT, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '5.5', 'RUNS', 'EVENT')),
    ('KXMLBTEAMTOTAL-26OCT081700CLECWS-CLE3', 'KXMLBTEAMTOTAL-26OCT081700CLECWS', 'KXMLBTEAMTOTAL', ['Baseball'],
     'If Cleveland scores 3+ runs in the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'TEAM_SCORE', 'FULL_EVENT', 'GE', '3', 'RUNS', 'TEAM')),
    ('KXWNBA1QTOTAL-26OCT09ATLNY-48', 'KXWNBA1QTOTAL-26OCT09ATLNY', 'KXWNBA1QTOTAL', ['Basketball'],
     "If the teams in the game collectively score more than 47.5 points in the 1st quarter of the Atlanta vs New York women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'TOTAL', 'Q1', 'GT', '47.5', 'POINTS', 'EVENT')),
    ('KXNCAAFOT-26OCT10USCPSU-1', 'KXNCAAFOT-26OCT10USCPSU', 'KXNCAAFOT', ['Football'],
     'If at least 1 overtime period is played in the USC vs Penn St. college football game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:OVERTIME')),
    ('KXNPBTOTAL-26OCT100100YOKYOM-10', 'KXNPBTOTAL-26OCT100100YOKYOM', 'KXNPBTOTAL', ['Baseball'],
     'If Yokohama DeNA BayStars and Yomiuri Giants collectively score more than 9.5 runs in the Yokohama DeNA BayStars and Yomiuri Giants NPB game originally scheduled for Oct 10, 2026 at 1:00 AM EDT, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '9.5', 'RUNS', 'EVENT')),
    ('KXATPGTOTAL-26OCT09MANCOB-18', 'KXATPGTOTAL-26OCT09MANCOB', 'KXATPGTOTAL', ['Tennis'],
     'If the number of completed games in the full match is above 17.5 in the Adrian Mannarino vs Flavio Cobolli professional tennis match in the 2026 ATP Shanghai Round Of 64, then the market resolves to Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '17.5', 'GAMES', 'EVENT')),
    ('KXWNBA4QWINNER-26OCT09ATLNY-NY', 'KXWNBA4QWINNER-26OCT09ATLNY', 'KXWNBA4QWINNER', ['Basketball'],
     "If New York is the winner of the 4th quarter (not including overtime) of regulation time in the Atlanta vs New York women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'WINNER', 'Q4', None, None, None, 'TEAM')),
    ('KXATPGSPREAD-26OCT08DJOHUR-DJO5', 'KXATPGSPREAD-26OCT08DJOHUR', 'KXATPGSPREAD', ['Tennis'],
     'If the game differential in favor of Novak Djokovic across the full match is above 4.5 games in the Novak Djokovic vs Hubert Hurkacz professional tennis match in the 2026 ATP Shanghai Round Of 64, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GT', '4.5', 'GAMES', 'TEAM')),
    ('KXRUGBYFRA14MATCH-26OCT10RCTRAC-RAC', 'KXRUGBYFRA14MATCH-26OCT10RCTRAC', 'KXRUGBYFRA14MATCH', ['Rugby'],
     'If Racing 92 is the winner in the RC Toulon vs Racing 92 professional France Top 14 match originally scheduled for Oct 10, 2026 at 10:35 AM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXWNBA1QWINNER-26OCT09GSLV-TIE', 'KXWNBA1QWINNER-26OCT09GSLV', 'KXWNBA1QWINNER', ['Basketball'],
     "If Tie is the result of the 1st quarter of regulation time in the Golden State vs Las Vegas women's Pro Basketball game originally scheduled for Oct 9, 2026, then the market resolves to Yes.",
     ('MAPPED', 'WINNER', 'Q1', None, None, None, 'TIE')),
    ('KXMLBINNINGWIN-26OCT081700CLECWS-4-CWS', 'KXMLBINNINGWIN-26OCT081700CLECWS-4', 'KXMLBINNINGWIN', ['Baseball'],
     'If Chicago WS scores more runs in the 4th inning of the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'INNING_4', None, None, None, 'TEAM')),
    ('KXNFLWINMARGIN-26OCT11PHIJAC-PHI15PLUS', 'KXNFLWINMARGIN-26OCT11PHIJAC', 'KXNFLWINMARGIN', ['Football'],
     'If Philadelphia has a positive point differential of at least 15 points against Jacksonville in the full game (including overtime) of the Philadelphia vs Jacksonville Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GE', '15', 'POINTS', 'TEAM')),
    ('KXUFCDISTANCE-26OCT10PRABON-DIST', 'KXUFCDISTANCE-26OCT10PRABON', 'KXUFCDISTANCE', ['MMA'],
     'If the Francisco Prado vs. Ismael Bonfim UFC Fight Night: Allen vs. Duncan fight originally scheduled for Oct 10, 2026 goes the distance, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:GOES_THE_DISTANCE')),
    ('KXNFLTIE-26OCT11CHIGB-Y', 'KXNFLTIE-26OCT11CHIGB', 'KXNFLTIE', ['Football'],
     'If neither team wins and the game ends in a tie in the Chicago vs Green Bay Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TIE')),
    ('KXMLBTEAMTOTAL-26OCT081700CLECWS-CLE6', 'KXMLBTEAMTOTAL-26OCT081700CLECWS', 'KXMLBTEAMTOTAL', ['Baseball'],
     'If Cleveland scores 6+ runs in the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'TEAM_SCORE', 'FULL_EVENT', 'GE', '6', 'RUNS', 'TEAM')),
    ('KXWNBATOTAL-26OCT09ATLNY-162', 'KXWNBATOTAL-26OCT09ATLNY', 'KXWNBATOTAL', ['Basketball'],
     "If the teams in the Atlanta vs New York women's Pro Basketball game originally scheduled for Oct 9, 2026 collectively score more than 161.5 points, then the market resolves to Yes.",
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '161.5', 'POINTS', 'EVENT')),
    ('KXWNBATOTAL-26OCT09GSLV-166', 'KXWNBATOTAL-26OCT09GSLV', 'KXWNBATOTAL', ['Basketball'],
     "If the teams in the Golden State vs Las Vegas women's Pro Basketball game originally scheduled for Oct 9, 2026 collectively score more than 165.5 points, then the market resolves to Yes.",
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '165.5', 'POINTS', 'EVENT')),
    ('KXMLBINNINGWIN-26OCT081700CLECWS-6-TIE', 'KXMLBINNINGWIN-26OCT081700CLECWS-6', 'KXMLBINNINGWIN', ['Baseball'],
     'If neither Cleveland nor Chicago WS scores more runs in the 6th inning of the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'INNING_6', None, None, None, 'TIE')),
    ('KXNFLDELAY-26OCT11JACPHI-Y', 'KXNFLDELAY-26OCT11JACPHI', 'KXNFLDELAY', ['Football'],
     'If there is any delay due to weather in the Philadelphia vs Jacksonville Pro Football game originally scheduled for Oct 11, 2026, then the market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER:WEATHER_DELAY')),
    ('KXMLBINNINGWIN-26OCT081700CLECWS-2-TIE', 'KXMLBINNINGWIN-26OCT081700CLECWS-2', 'KXMLBINNINGWIN', ['Baseball'],
     'If neither Cleveland nor Chicago WS scores more runs in the 2nd inning of the Cleveland vs Chicago WS professional baseball game originally scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'INNING_2', None, None, None, 'TIE')),
    ('KXRUGBYFRA14MATCH-26OCT10LOUSRO-SRO', 'KXRUGBYFRA14MATCH-26OCT10LOUSRO', 'KXRUGBYFRA14MATCH', ['Rugby'],
     'If Stade Rochelais is the winner in the Lyon OU vs Stade Rochelais professional France Top 14 match originally scheduled for Oct 10, 2026 at 10:35 AM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    # ── hand-picked real rows: the hard cases ──
    ('KXNFLGAME-26OCT11CHIGB-CHI', 'KXNFLGAME-26OCT11CHIGB', 'KXNFLGAME',
     ['Football'],
     'If Chicago wins the CHI Bears vs GB Packers Pro Football game '
     'originally scheduled for Oct 11, 2026, then the market resolves to '
     'Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXNHLGAME-26OCT08CHINYI-CHI', 'KXNHLGAME-26OCT08CHINYI', 'KXNHLGAME',
     ['Hockey'],
     'If Chicago wins the Chicago vs New York I NHL game originally '
     'scheduled for Oct 8, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXNBAGAME-26OCT08ATLSAS-SAS', 'KXNBAGAME-26OCT08ATLSAS', 'KXNBAGAME',
     ['Basketball'],
     'If San Antonio wins the Atlanta vs San Antonio Pro Basketball game '
     'originally scheduled for Oct 8, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXMLBGAME-26OCT112000LADMIL-MIL', 'KXMLBGAME-26OCT112000LADMIL',
     'KXMLBGAME', ['Baseball'],
     'If Milwaukee wins the Los Angeles D vs Milwaukee professional baseball '
     'game originally scheduled for Oct 11, 2026, then the market resolves '
     'to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXCFLGAME-26OCT09EDMHAM-HAM', 'KXCFLGAME-26OCT09EDMHAM', 'KXCFLGAME',
     ['Football'],
     'If the Hamilton Tiger-Cats win the Edmonton Elks vs Hamilton '
     'Tiger-Cats professional CFL football game originally scheduled for '
     'Oct 9, 2026, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXCZEFNLGAME-26OCT09SLAVLA-VLA', 'KXCZEFNLGAME-26OCT09SLAVLA',
     'KXCZEFNLGAME', ['Soccer'],
     'If Vlasim wins the Slavia Prague B vs Vlasim professional Czech '
     'National Football League soccer game originally scheduled for Oct 9, '
     '2026 after 90 minutes plus stoppage time (does not include extra time '
     'or penalties), then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'FULL_EVENT', None, None, None, 'TEAM')),
    ('KXKBOSPREAD-26OCT090100KIANCD-NCD2', 'KXKBOSPREAD-26OCT090100KIANCD',
     'KXKBOSPREAD', ['Baseball'],
     'If the NC Dinos win by more than 1.5 runs in the Kia Tigers vs NC '
     'Dinos Korea KBO game originally scheduled for Oct 9, 2026 at 1:00 AM '
     'EDT, then the market resolves to Yes.',
     ('MAPPED', 'MARGIN', 'FULL_EVENT', 'GT', '1.5', 'RUNS', 'TEAM')),
    ('KXMLBF5-26OCT081700CLECWS-CLE', 'KXMLBF5-26OCT081700CLECWS',
     'KXMLBF5', ['Baseball'],
     'If Cleveland wins the first 5 innings of the Cleveland vs Chicago WS '
     'professional baseball game originally scheduled for Oct 8, 2026 at '
     '5:00 PM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'INNINGS_1_TO_5', None, None, None, 'TEAM')),
    ('KXMLBTOTAL-26OCT081700CLECWS-6', 'KXMLBTOTAL-26OCT081700CLECWS',
     'KXMLBTOTAL', ['Baseball'],
     'If Cleveland and Chicago WS collectively score more 5.5 runs in the '
     'Cleveland vs Chicago WS professional baseball game originally '
     'scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves to '
     'Yes.',
     ('MAPPED', 'TOTAL', 'FULL_EVENT', 'GT', '5.5', 'RUNS', 'EVENT')),
    ('KXCS2MAP-26OCT090900NOTTU-2-TU', 'KXCS2MAP-26OCT090900NOTTU-2',
     'KXCS2MAP', ['Esports'],
     'If THE UNIT wins map 2 in the ESL Challenger League Asia-Pacific Cup #3 '
     '2026: Not A Squad Esports vs. THE UNIT CS2 match originally scheduled '
     'for Oct 9, 2026 at 9:00 AM EDT, then the market resolves to Yes.',
     ('MAPPED', 'WINNER', 'MAP_2', None, None, None, 'TEAM')),
    ('KXNFLREC-26OCT08TBDAL-DALCLAMB88-10', 'KXNFLREC-26OCT08TBDAL',
     'KXNFLREC', ['Football'],
     'If CeeDee Lamb records 10+ receptions in the Tampa Bay vs Dallas Pro '
     'Football game originally scheduled for Oct 8, 2026, then the market '
     'resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_PLAYER_PROP_NOT_IN_AGREED_UNIVERSE')),
    ('KXNFLTSPEC-27ARI-JLOVE4R75R75', 'KXNFLTSPEC-27ARI', 'KXNFLTSPEC',
     ['Football'],
     'If Jeremiyah Love records 75+ rushing yards and 75+ receiving yards in '
     'a single game in the 2026-27 Pro Football regular season, then the '
     'market resolves to Yes.',
     ('REFUSED', 'KALSHI_ONTOLOGY_OUTRIGHT_OR_SEASON_CONTRACT_NOT_IN_MAPPER')),
]


def _verdict(s):
    t, e, ser, tags, rp, _exp = s
    return KO.classify(market(t, e, ser, tags, rp))


@pytest.mark.parametrize("s", SAMPLES, ids=[x[0] for x in SAMPLES])
def test_each_real_catalogue_row_is_mapped_or_refused_by_name(s):
    v = _verdict(s)
    exp = s[5]
    if exp[0] == "MAPPED":
        assert v["status"] == KO.MAPPED, v
        got = (v["status"], v["family"], v["period"], v["operator"],
               v["line"], v["unit"], (v["subject"] or {}).get("type"))
        assert got == exp
        assert v["refusal"] is None and v["sport"]
        assert v["event_id"] == s[1] and v["series"] == s[2]
    else:
        assert v["status"] == KO.REFUSED and v["refusal"] == exp[1], v
        assert v["family"] is None and v["period"] is None


def test_the_samples_cover_every_template_and_every_refusal_family():
    seen = {_verdict(s)["template"] for s in SAMPLES
            if s[5][0] == "MAPPED"}
    assert seen == {t for t, _f, _n, _r in KO.TEMPLATES}
    codes = {(_verdict(s)["refusal"] or "").split(":")[0] for s in SAMPLES
             if s[5][0] == "REFUSED"}
    for code in (KO.R_PLAYER_PROP, KO.R_TEAM_STAT, KO.R_GAME_PROP,
                 KO.R_OUTRIGHT, KO.R_NON_BINARY, KO.R_EVENT):
        assert code in codes, code


def test_needles_only_bound_cost_never_change_the_template():
    """Every template is tried only when one of its needles is in the
    sentence: on every real sample, the first template whose sentence
    pattern matches WITHOUT the needle gate is the one classify chose."""
    for s in SAMPLES:
        v = _verdict(s)
        if v["status"] != KO.MAPPED:
            continue
        rp = KO._flat(s[4])
        first = next(t for t, _f, _n, rx in KO.TEMPLATES if rx.match(rp))
        assert first == v["template"], (s[0], first, v["template"])
        for t, _f, nd, rx in KO.TEMPLATES:
            if rx.match(rp):
                assert any(n in rp.lower() for n in nd), (s[0], t)


def test_the_subject_is_a_participant_the_sentence_names_or_the_ticker_codes():
    nhl = next(s for s in SAMPLES if s[0] == "KXNHLGAME-26OCT08CHINYI-CHI")
    v = _verdict(nhl)
    assert v["subject"] == {"type": "TEAM", "as_stated": "Chicago",
                            "ticker_code": "CHI",
                            "basis": "RULES_NAMES_A_PARTICIPANT"}
    # NFL game markets name the city, the event the club ("Chicago" vs "CHI
    # Bears"): the venue's own codes carry the identity
    nfl = next(s for s in SAMPLES if s[0] == "KXNFLGAME-26OCT11CHIGB-CHI")
    assert _verdict(nfl)["subject"]["basis"] == "TICKER_CODE_IN_EVENT"
    assert _verdict(nfl)["subject"]["ticker_code"] == "CHI"
    # a team the sentence does not name, with a code not in the event: refused
    bad = market("KXNBAGAME-26OCT08ATLSAS-DEN", "KXNBAGAME-26OCT08ATLSAS",
                 "KXNBAGAME", ["Basketball"],
                 "If Denver wins the Atlanta vs San Antonio Pro Basketball "
                 "game originally scheduled for Oct 8, 2026, then the market "
                 "resolves to Yes.")
    v = KO.classify(bad)
    assert v["status"] == KO.REFUSED and v["refusal"] == KO.R_SUBJECT


def test_titles_and_the_series_title_are_never_read():
    for s in SAMPLES:
        a = KO.classify(market(*s[:5]))
        m = market(*s[:5], title="Something else entirely",
                   yes_sub_title="Another team")
        m["_series"]["title"] = "A different product"
        assert KO.classify(m) == a, s[0]


def test_the_sport_is_the_series_tag_and_a_contradicting_sentence_refuses():
    s = next(x for x in SAMPLES if x[0] == "KXNBAGAME-26OCT08ATLSAS-SAS")
    assert _verdict(s)["sport"] == "basketball"
    for tags in ([], ["Other"], ["Basketball", "Football"]):
        v = KO.classify(market(s[0], s[1], s[2], tags, s[4]))
        assert v["status"] == KO.REFUSED
        assert v["refusal"].startswith(KO.R_SPORT_TAG), v
    v = KO.classify(market(s[0], s[1], s[2], ["Soccer"], s[4]))
    assert v["refusal"] == "%s:basketball" % KO.R_SPORT_CONFLICT
    # a league NAME holding another sport's word is not a conflict when the
    # sentence names the tag's own sport ("Czech National Football League
    # soccer game")
    cz = next(x for x in SAMPLES if x[0].startswith("KXCZEFNLGAME"))
    assert _verdict(cz)["status"] == KO.MAPPED
    assert _verdict(cz)["sport"] == "soccer"


def test_a_compound_sentence_is_never_read_as_a_simple_one():
    """Double results, first-N-innings and the like contain a simple
    sentence's words; they are never mapped as a full-game winner."""
    dbl = KO.classify(market(
        "KXNCAAF1HFT-26OCT09IOWAWASH-IOWAWASH", "KXNCAAF1HFT-26OCT09IOWAWASH",
        "KXNCAAF1HFT", ["Football"],
        "If Washington and Iowa tie in the 1st half and Iowa wins the full "
        "game in the Iowa vs Washington college football game originally "
        "scheduled for Oct 9, 2026, then the market resolves to Yes."))
    assert dbl["status"] == KO.REFUSED
    assert dbl["refusal"] == "%s:DOUBLE_RESULT" % KO.R_GAME_PROP
    f5 = next(x for x in SAMPLES if x[0].startswith("KXMLBF5"))
    assert _verdict(f5)["period"] == "INNINGS_1_TO_5"
    tie5 = KO.classify(market(
        "KXMLBF5-26OCT081700CLECWS-TIE", "KXMLBF5-26OCT081700CLECWS",
        "KXMLBF5", ["Baseball"],
        "If Cleveland and Chicago WS tie in the first 5 innings of the "
        "Cleveland vs Chicago WS professional baseball game originally "
        "scheduled for Oct 8, 2026 at 5:00 PM EDT, then the market resolves "
        "to Yes."))
    assert (tie5["family"], tie5["period"], tie5["subject"]["type"]) == (
        "WINNER", "INNINGS_1_TO_5", "TIE")


def test_the_stated_scope_is_recorded_and_nothing_unstated_is_filled():
    nba = _verdict(next(x for x in SAMPLES if x[0].startswith("KXNBAGAME")))
    assert nba["scope"]["overtime"] == "NOT_STATED_IN_MARKET_TEXT"
    soccer = _verdict(next(x for x in SAMPLES
                           if x[0].startswith("KXCZEFNLGAME")))
    assert soccer["scope"]["extra_time"] == "EXCLUDED"
    q4 = next(x for x in SAMPLES if "4th quarter (excluding overtime)" in x[4]
              and x[5][0] == "MAPPED")
    assert _verdict(q4)["scope"]["overtime"] == "EXCLUDED"
    reg = next(x for x in SAMPLES if "of regulation time" in x[4])
    assert _verdict(reg)["scope"]["overtime"] == "EXCLUDED"


def test_the_terms_are_bound_by_the_contract_s_own_rule_fingerprint():
    s = next(x for x in SAMPLES if x[0].startswith("KXNBAGAME"))
    m = market(*s[:5])
    t = KO.bind_terms(m, KO.classify(m))
    assert t["rules_sha256"] == SRR.kalshi_rules_sha256(m)
    assert t["rules_sha256"] == RULES.kalshi_row(m)["rules_sha256"]
    assert t["scope"] == {k: x for k, x in KO.classify(m)["scope"].items()
                          if x is not None}
    assert t["parsed_in"] == KO.TERMS_PARSED_IN
    assert t["rulebook"].startswith("NOT_APPLIED_HERE")
    m2 = dict(m, rules_secondary=SECONDARY + " Changed.")
    assert KO.bind_terms(m2, KO.classify(m2))["rules_sha256"] != \
        t["rules_sha256"]


# ── the registry row, coverage and settlement (each fails on 412c4962) ──

def _row(s):
    return POP.kalshi_contract_row(market(*s[:5]), now=1_791_500_000.0)


def test_a_mapped_kalshi_registry_row_carries_sport_family_period_and_terms():
    s = next(x for x in SAMPLES if x[0].startswith("KXNCAAF1QSPREAD"))
    c = _row(s)
    assert c["ontology"]["gaps"] == []
    assert (c["sport"], c["family"], c["period"]) == (
        "football", "MARGIN", "Q1")
    meaning = c["ontology"]["meaning"]
    assert meaning["metric"] == "MARGIN" and meaning["line"] == 3.5
    assert meaning["event_id"] == s[1] and meaning["venue"] == "KALSHI"
    # the stored verdict carries what the meaning does not, nothing twice
    k = c["ontology"]["kalshi"]
    assert k["status"] == KO.MAPPED and k["template"] == "SPREAD_WINS_BY"
    assert k["unit"] == "POINTS" and k["subject"]["type"] == "TEAM"
    assert not {"sport", "family", "period", "line", "operator"} & set(k)
    assert c["ontology"]["terms"]["rules_sha256"] == \
        SRR.kalshi_rules_sha256(market(*s[:5]))
    assert c["desired_subscription"] is False      # never on PMUS streams
    assert c["contract_id"] == "kalshi:" + s[0]


def test_a_refused_kalshi_row_names_its_gap_and_keeps_its_sport_tag():
    s = next(x for x in SAMPLES if x[0].startswith("KXNFLREC-"))
    c = _row(s)
    assert c["ontology"]["gaps"] == [KO.R_PLAYER_PROP]
    assert c["family"] is None and c["period"] is None
    assert c["sport"] == "football"
    assert c["ontology"]["terms"] is None
    cov = POP.classify(c)
    assert cov["state"] == "CODE_CONTROLLED_GAP"
    assert cov["why"] == "ONTOLOGY_GAPS:%s" % KO.R_PLAYER_PROP


def test_a_mapped_kalshi_contract_leaves_the_code_controlled_gap():
    s = next(x for x in SAMPLES if x[0].startswith("KXNBAGAME"))
    c = _row(s)
    cov = POP.classify(c)
    assert cov["evidence"]["mapped"] is True
    assert cov["state"] == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"


def test_a_mapped_kalshi_winner_is_judged_on_its_own_rules_not_refused():
    s = next(x for x in SAMPLES if x[0].startswith("KXNBAGAME"))
    m = market(*s[:5])
    c = _row(s)
    rr = RULES.kalshi_row(m)
    rr["rules_text"] = "%s\n\n%s" % (rr["rules_text"], rr["rules_secondary"])
    st = S.state_for(c, rules=rr, rules_looked_up=True)
    assert st["why"] != S.R_KALSHI_NOT_MAPPED
    assert st["basis"] == S.BASIS_RULES_TERMS
    assert st["state"] in S.STATES
    # a mapped non-winner: the bookmaker holds no terms for it, by name
    sp = next(x for x in SAMPLES if x[0].startswith("KXKBOSPREAD"))
    st = S.state_for(_row(sp), rules=RULES.kalshi_row(market(*sp[:5])),
                     rules_looked_up=True)
    assert st["why"] == "BOOKMAKER_TERMS_NOT_HELD:baseball/MARGIN/FULL_EVENT"
    # an UNMAPPED Kalshi contract still stops at the Kalshi refusal
    pp = next(x for x in SAMPLES if x[0].startswith("KXNFLREC-"))
    st = S.state_for(_row(pp), rules=RULES.kalshi_row(market(*pp[:5])),
                     rules_looked_up=True)
    assert st["why"] == S.R_KALSHI_NOT_MAPPED


def test_both_kalshi_walks_write_the_same_registry_row():
    """The plane's walk keeps KALSHI_PERSIST_KEYS (kalshi_slim); the
    workers' walk keeps kalshi_market_data.COMPACT_KEYS. Both persist
    through populate_kalshi, so the row (and its content sha) must be
    identical or every walk would rewrite every row."""
    for s in SAMPLES:
        full = market(*s[:5], close_time="2026-10-12T00:00:00Z",
                      custom_strike={"basketball_team": "x"},
                      yes_bid_dollars="0.40", floor_strike=3.5)
        series = dict(full.pop("_series"), fee_type="quadratic",
                      fee_multiplier=1, contract_terms_url=None)
        a = POP.kalshi_contract_row(POP.kalshi_slim(full, series),
                                    now=1.0)
        b = POP.kalshi_contract_row(KMD.compact_market(full, series),
                                    now=1.0)
        assert a == b, s[0]


def test_the_ontology_imports_nothing_that_can_trade():
    tree = ast.parse((PKG / "kalshi_ontology.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    for bad in ("kalshi_venue", "kalshi_orders", "kalshi_account",
                "kalshi_linkage", "small_live", "pmus", "requests", "httpx",
                "websockets"):
        assert not any(bad in m for m in mods), (bad, mods)


def test_the_classifier_is_cheap_enough_for_the_walk():
    """populate_kalshi classifies every market of every walk (~80k): the
    classifier stays a few string tests and anchored regexes (~25 us on the
    production catalogue; bounded here at 1 ms per call, generously)."""
    ms = [market(*s[:5]) for s in SAMPLES] * 20
    t0 = time.perf_counter()
    for m in ms:
        KO.classify(m)
    assert (time.perf_counter() - t0) / len(ms) < 0.001


# ── on PostgreSQL: the walk persisted, the coverage pass classifies ──────

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


class _Walk:
    """GET-only fake of the documented endpoints carrying the real rows."""

    def __init__(self, rows):
        self.rows = rows

    def get(self, url, *, params=None, timeout=None):
        p = params or {}
        if url.endswith("/series"):
            ser = {}
            for r in self.rows:
                ser.setdefault(r[2], {"ticker": r[2], "title": r[2],
                                      "tags": r[3], "category": "Sports"})
            return _Resp(200, {"series": list(ser.values())})
        return _Resp(200, {"cursor": "", "markets": [
            {"ticker": r[0], "event_ticker": r[1], "rules_primary": r[4],
             "rules_secondary": SECONDARY, "status": "active",
             "market_type": "binary"}
            for r in self.rows if r[2] == p.get("series_ticker")]})


@pg
def test_on_postgres_the_walk_maps_and_the_coverage_pass_reclassifies():
    from sportsassets import kalshi_catalogue as KC

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active = false")
            now = time.time()
            res = KC.walk(_Walk(SAMPLES), sleep=lambda s: None,
                          project=POP.kalshi_slim)
            assert res["complete"] and len(res["markets"]) == len(SAMPLES)
            out = await POP.populate_kalshi(c, res, now=now)
            assert out["upserted"] == len(SAMPLES)
            S._TERMS_CACHE.clear()
            cov = await POP.coverage_pass(c, fresh_symbols=set(), now=now)
            rows = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT contract_id, sport, family, period, coverage_state, "
                "       coverage_why, settlement_state, settlement_why, "
                "       settlement_basis FROM market_plane_registry "
                " WHERE venue = 'KALSHI' AND active")}
            return cov, rows
        finally:
            await tr.rollback()
            await c.close()
    cov, rows = run(go())
    mapped = [s for s in SAMPLES if s[5][0] == "MAPPED"]
    refused = [s for s in SAMPLES if s[5][0] == "REFUSED"]
    for s in mapped:
        r = rows["kalshi:" + s[0]]
        assert r["family"] == s[5][1] and r["period"] == s[5][2], s[0]
        assert r["coverage_state"] == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN", r
        assert r["settlement_why"] != S.R_KALSHI_NOT_MAPPED
    for s in refused:
        r = rows["kalshi:" + s[0]]
        assert r["coverage_state"] == "CODE_CONTROLLED_GAP", r
        assert r["coverage_why"] == "ONTOLOGY_GAPS:%s" % s[5][1], r
        assert r["settlement_why"] == S.R_KALSHI_NOT_MAPPED
    kv = cov["by_venue"]["KALSHI"]
    assert kv.get("MAPPED_BUT_SETTLEMENT_NOT_PROVEN") == len(mapped)
    assert kv.get("CODE_CONTROLLED_GAP") == len(refused)
    # the mapped full-event winners were compared on their whole rule block
    winners = [s for s in mapped if s[5][1] == "WINNER"
               and s[5][2] == "FULL_EVENT" and s[3][0] in (
                   "Football", "Basketball", "Baseball", "Hockey", "Soccer")]
    assert winners
    for s in winners:
        assert rows["kalshi:" + s[0]]["settlement_basis"] == \
            S.BASIS_RULES_TERMS, s[0]
    assert cov["settlement"]["terms_text_loaded"] >= len(winners)
