"""RC6.3c COLL-1 phase a: THE COLLECTOR'S VENUE-BOOK LATENCY -- EARLIEST
DEADLINE FIRST, NO READ THAT CANNOT FINISH, AND WHERE EVERY READ'S TIME WENT.

THE MEASURED DEFECT (RC6.3b software-reds audit; production ledger
ext_candidate_outcomes, packet window 08:54-09:54Z 2026-10-10; research-sql
runs 38056211192 Q2, 38056927459 G3/G4, 38057231614 H1, 38057645735 I1 and
38078680376 K1/I1b/K2/K3 -- the rows below are those runs' rows, to the
millisecond). QUOTE_STALE_ON_ARRIVAL was the largest SOFTWARE first loss of
the approved-judge packet (38 of 84 events). 83 of those events' cycles were
quotes the metered provider delivered INSIDE the 30 s rule -- NCAAF 10.1-22.0 s
old, MLS 16.9-24.9 s, Liga MX 2.1-25.9 s -- that OUR serial, paced venue reads
then carried past it: the candidates at queue positions 6-41 reached the
arrival check 10.5-29 s after receipt, one paced read per position ahead of
them, and in every one of the 83 rows our processing exceeded the slack the
quote arrived with. One read at the head of the queue costs ~6 s (K2b: the
step in our processing from queue position 0 to 1 averages 6.58 s, median
5.17 s, over 305 fetches in 24 h); a read whose candidate had less slack than
that could not have finished before its deadline, and was made anyway.

WHAT THE HEAD DOES (ext_pinnacle_loop.COLLECTOR_READ_ORDER_RULE): within each
sport's fetch the candidates are judged EARLIEST DEADLINE FIRST (least slack
first: the provider's own stamp ascending, an unreadable stamp last, the event
id the tiebreak) inside the deferral and in-play classes of
collector_coverage.candidate_order_key; the deadline is re-tested at the
instant before every read; a candidate whose remaining slack is then below
the measured head-of-queue floor (VENUE_READ_HEAD_OF_QUEUE_FLOOR_S) is skipped
WITHOUT a read and refused by its own name
(QUOTE_SLACK_BELOW_THE_MEASURED_VENUE_READ_FLOOR -- the same SOFTWARE /
FRESHNESS_PLUMBING / 2_FRESHNESS loss as QUOTE_STALE_ON_ARRIVAL, named more
precisely, not moved); every candidate's ledger row carries where its read
spent its time at the transport (venue_request_gate.READ_TELEMETRY_RULE:
queue_wait_s, gate_wait_s, cooldown_wait_s, http_s; migration 368) and the
source that served its book; the heartbeat carries the on-demand refresh
budget's counters (capped / budget_spent / fired) for the owner's C2 decision.

WHAT THESE TESTS ESTABLISH, AND WHAT THEY DO NOT. They establish the rule's
arithmetic against the production rows (replayed with the module's own order
key, slack and floor functions, at the spec's 5.5 s per read), that the
below-floor skip and the telemetry are WIRED through the real scheduled cycle
and the real transport, and that the 30 s rule, its clock, its instant,
MIN_GAP_S and the refresh bound are unchanged. They do NOT establish the
production effect: the readback (QUOTE_STALE_ON_ARRIVAL events 38 -> <= 5
across three hourly censuses; stale rows with >= 13 s of slack 75% -> < 10%;
no row with processing > slack at q <= 5) is a deployed-cycle measurement and
until it is read the effect is UNMEASURED, not improved.
"""
from __future__ import annotations

import collections
import datetime as _dt
import inspect
import json
import os
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import collector_coverage as cov
from sportsassets import coverage_first_loss as CFL
from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as grt
from sportsassets.workers import ext_pinnacle_loop as L

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

LIMIT = 30.0
#: the audit's per-read cost for the replay ("5.5 s/read"); K2b measured the
#: first read's step at 6.58 s avg / 5.17 s median
READ_S = 5.5
CODE = "QUOTE_SLACK_BELOW_THE_MEASURED_VENUE_READ_FLOOR"

# ═════════════════════════════════════════════════════════════════════
# THE PRODUCTION ROWS (research-sql run 38078680376, read only)
# ═════════════════════════════════════════════════════════════════════
#
# K1: every ledger row of the packet-window fetches that held at least one
# delivered-inside-30 QUOTE_STALE_ON_ARRIVAL row -- 244 rows, 8 fetches
# (NCAAF 08:58:44 and 09:13:45, Liga MX 08:58:44 / 09:13:45 / 09:28:46, MLS
# 09:13:45 / 09:28:46 / 09:43:46). queue_position is the order the base judged
# them in; a None provider lag is a row refused before a price existed (it
# spent no read). I1b: the 83 inside-limit cycles of the 38 first-loss events,
# one row each.
K1_ROWS = (
    # (cycle, cycle_at, sport_key, queue_position, provider_lag_s, our_processing_s, outcome, first_refusal)
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 0, 22.045, 0.178, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 1, 21.045, 1.119, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 2, 22.045, 1.888, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 3, 22.045, 2.86, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 4, 22.045, 4.026, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 5, 22.045, 4.882, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 6, 22.045, 13.563, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 7, 22.045, 13.755, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 8, 21.045, 14.324, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 9, 22.045, 14.536, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 10, 21.045, 14.652, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 11, 22.045, 14.807, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 12, 22.045, 14.976, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 13, 22.045, 15.079, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 14, 22.045, 15.182, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 15, 22.045, 15.292, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 16, 22.045, 15.415, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 17, 22.045, 15.537, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 18, 22.045, 15.744, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 19, 22.045, 16.158, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 20, 21.045, 16.351, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 21, 22.045, 16.463, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 22, 22.045, 16.635, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 23, 22.045, 16.843, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 24, 22.045, 17.038, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 25, 22.045, 17.232, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 26, 22.045, 17.354, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 27, 21.045, 17.462, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 28, 22.045, 17.638, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 29, 22.045, 17.854, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 30, 22.045, 18.055, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 31, 21.045, 18.254, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 32, 22.045, 18.454, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 33, 21.045, 18.745, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 34, 22.045, 19.343, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 35, 22.045, 19.639, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 36, 22.045, 19.922, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 37, 21.045, 20.131, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 38, 22.045, 20.136, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 39, 22.045, 20.449, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 40, 22.045, 20.634, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 41, 22.045, 20.822, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 42, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 43, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 44, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 45, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 46, None, None, 'REFUSED', 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 47, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 48, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 49, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 50, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 51, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 52, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 53, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 54, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 55, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 56, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 57, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 58, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 59, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 60, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'americanfootball_ncaaf', 61, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 0, 25.911, 0.332, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 1, 25.911, 10.344, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 2, 25.911, 10.535, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 3, 25.911, 10.738, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 4, 25.911, 11.206, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 5, 25.911, 11.359, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 6, 25.911, 11.558, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 7, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 8, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('bebc8ef8', '08:58:44', 'soccer_mexico_ligamx', 9, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 0, 10.145, 0.187, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 1, 11.145, 5.576, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 2, 10.145, 6.77, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 3, 10.145, 8.078, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 4, 11.145, 9.167, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 5, 10.145, 11.04, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 6, 10.145, 20.247, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 7, 10.145, 20.343, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 8, 10.145, 20.441, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 9, 11.145, 20.544, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 10, 10.145, 20.659, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 11, 9.145, 20.773, 'REFUSED', 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 12, 10.145, 20.963, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 13, 11.145, 21.068, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 14, 11.145, 21.172, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 15, 10.145, 21.286, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 16, 11.145, 21.447, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 17, 11.145, 21.563, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 18, 10.145, 21.674, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 19, 10.145, 21.86, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 20, 11.145, 21.974, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 21, 10.145, 22.491, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 22, 10.145, 22.781, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 23, 10.145, 22.983, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 24, 11.145, 23.191, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 25, 11.145, 23.686, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 26, 11.145, 23.827, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 27, 11.145, 23.934, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 28, 10.145, 24.044, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 29, 10.145, 24.139, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 30, 11.145, 24.244, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 31, 10.145, 24.346, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 32, 11.145, 24.444, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 33, 10.145, 24.543, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 34, 10.145, 24.674, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 35, 9.145, 24.679, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 36, 10.145, 24.946, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 37, 10.145, 25.06, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 38, 10.145, 25.281, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 39, 11.145, 25.382, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 40, 11.145, 25.473, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 41, 11.145, 25.567, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 42, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 43, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 44, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 45, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 46, None, None, 'REFUSED', 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 47, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 48, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 49, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 50, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 51, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 52, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 53, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 54, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 55, None, None, 'REFUSED', 'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 56, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 57, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 58, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 59, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 60, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'americanfootball_ncaaf', 61, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 0, 3.115, 0.115, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 1, 3.115, 17.095, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 2, 2.115, 28.526, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 3, 3.115, 28.973, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 4, 3.115, 29.126, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 5, 3.115, 29.289, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 6, 3.115, 29.473, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 7, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 8, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_mexico_ligamx', 9, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 0, 24.897, 0.102, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 1, 24.897, 9.237, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 2, 24.897, 9.426, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 3, 24.897, 9.621, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 4, 24.897, 10.066, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 5, 24.897, 10.221, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 6, 24.897, 10.393, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 7, 24.897, 10.488, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 8, 24.897, 10.628, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 9, 24.897, 10.803, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 10, 24.897, 10.925, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 11, 24.897, 10.935, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 12, 24.897, 11.144, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 13, 24.897, 11.531, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 14, 24.897, 11.834, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 15, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 16, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 17, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 18, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 19, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 20, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 21, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 22, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 23, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 24, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 25, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 26, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 27, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 28, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('80c74094', '09:13:45', 'soccer_usa_mls', 29, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 0, 29.782, 0.142, 'REFUSED', 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 1, 29.782, 0.332, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 2, 29.782, 0.442, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 3, 28.782, 0.626, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 4, 29.782, 10.143, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 5, 28.782, 10.3, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 6, 29.782, 10.46, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 7, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 8, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_mexico_ligamx', 9, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 0, 17.97, 0.531, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 1, 17.97, 8.923, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 2, 17.97, 11.227, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 3, 17.97, 12.61, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 4, 17.97, 12.815, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 5, 17.97, 12.918, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 6, 17.97, 13.023, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 7, 17.97, 13.131, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 8, 17.97, 13.229, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 9, 17.97, 13.328, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 10, 17.97, 13.436, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 11, 17.97, 13.548, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 12, 17.97, 13.651, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 13, 17.97, 13.987, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 14, 17.97, 14.134, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 15, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 16, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 17, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 18, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 19, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 20, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 21, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 22, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 23, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 24, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 25, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 26, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 27, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 28, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('35452521', '09:28:46', 'soccer_usa_mls', 29, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 0, 16.932, 0.177, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 1, 16.932, 5.458, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 2, 16.932, 7.651, 'REFUSED', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 3, 16.932, 17.362, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 4, 16.932, 17.482, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 5, 16.932, 17.639, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 6, 16.932, 17.736, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 7, 16.932, 17.834, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 8, 16.932, 17.932, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 9, 16.932, 18.027, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 10, 16.932, 18.129, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 11, 16.932, 18.213, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 12, 16.932, 18.308, 'REFUSED', 'QUOTE_STALE_ON_ARRIVAL'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 13, 16.932, 18.314, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 14, 16.932, 18.402, 'REFUSED', 'NO_VENUE_CONTRACT_FOR_EVENT'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 15, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 16, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 17, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 18, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 19, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 20, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 21, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 22, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 23, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 24, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 25, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 26, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 27, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 28, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('44aece20', '09:43:46', 'soccer_usa_mls', 29, None, None, 'REFUSED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
)
I1B_ROWS = (
    # (sport_key, cycle_at, queue_position, provider_lag_s, our_processing_s, the code beside)
    ('americanfootball_ncaaf', '08:58:44', 12, 22.045, 14.976, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 13, 22.045, 15.079, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 14, 22.045, 15.182, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 15, 22.045, 15.292, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '08:58:44', 16, 22.045, 15.415, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 17, 22.045, 15.537, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 18, 22.045, 15.744, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 19, 22.045, 16.158, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 20, 21.045, 16.351, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 21, 22.045, 16.463, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 22, 22.045, 16.635, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 23, 22.045, 16.843, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 24, 22.045, 17.038, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 25, 22.045, 17.232, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 26, 22.045, 17.354, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 27, 21.045, 17.462, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('americanfootball_ncaaf', '08:58:44', 28, 22.045, 17.638, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 29, 22.045, 17.854, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '08:58:44', 30, 22.045, 18.055, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 31, 21.045, 18.254, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 32, 22.045, 18.454, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 33, 21.045, 18.745, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '08:58:44', 34, 22.045, 19.343, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 35, 22.045, 19.639, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 36, 22.045, 19.922, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '08:58:44', 37, 21.045, 20.131, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 39, 22.045, 20.449, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 40, 22.045, 20.634, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '08:58:44', 41, 22.045, 20.822, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('soccer_mexico_ligamx', '08:58:44', 3, 25.911, 10.738, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('soccer_mexico_ligamx', '08:58:44', 4, 25.911, 11.206, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_mexico_ligamx', '08:58:44', 5, 25.911, 11.359, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('americanfootball_ncaaf', '09:13:45', 6, 10.145, 20.247, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 7, 10.145, 20.343, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 8, 10.145, 20.441, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 9, 11.145, 20.544, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '09:13:45', 10, 10.145, 20.659, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 12, 10.145, 20.963, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 13, 11.145, 21.068, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 14, 11.145, 21.172, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 15, 10.145, 21.286, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 16, 11.145, 21.447, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 17, 11.145, 21.563, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 18, 10.145, 21.674, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 19, 10.145, 21.86, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 20, 11.145, 21.974, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 21, 10.145, 22.491, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('americanfootball_ncaaf', '09:13:45', 22, 10.145, 22.781, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 23, 10.145, 22.983, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '09:13:45', 24, 11.145, 23.191, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 25, 11.145, 23.686, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 26, 11.145, 23.827, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 27, 11.145, 23.934, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '09:13:45', 28, 10.145, 24.044, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 29, 10.145, 24.139, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 30, 11.145, 24.244, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('americanfootball_ncaaf', '09:13:45', 31, 10.145, 24.346, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 32, 11.145, 24.444, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 33, 10.145, 24.543, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('americanfootball_ncaaf', '09:13:45', 34, 10.145, 24.674, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('soccer_mexico_ligamx', '09:13:45', 2, 2.115, 28.526, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('soccer_mexico_ligamx', '09:13:45', 3, 3.115, 28.973, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_mexico_ligamx', '09:13:45', 4, 3.115, 29.126, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:13:45', 7, 24.897, 10.488, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:13:45', 8, 24.897, 10.628, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:13:45', 9, 24.897, 10.803, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:13:45', 10, 24.897, 10.925, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:13:45', 12, 24.897, 11.144, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:13:45', 14, 24.897, 11.834, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_mexico_ligamx', '09:28:46', 1, 29.782, 0.332, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_mexico_ligamx', '09:28:46', 2, 29.782, 0.442, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:28:46', 6, 17.97, 13.023, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:28:46', 7, 17.97, 13.131, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:28:46', 8, 17.97, 13.229, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:28:46', 9, 17.97, 13.328, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:28:46', 10, 17.97, 13.436, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:28:46', 11, 17.97, 13.548, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:43:46', 3, 16.932, 17.362, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:43:46', 4, 16.932, 17.482, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:43:46', 5, 16.932, 17.639, 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'),
    ('soccer_usa_mls', '09:43:46', 6, 16.932, 17.736, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
    ('soccer_usa_mls', '09:43:46', 7, 16.932, 17.834, 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'),
    ('soccer_usa_mls', '09:43:46', 8, 16.932, 17.932, 'FEED_QUOTE_OLDER_THAN_LIMIT'),
)


def _fetches():
    """(cycle_at, sport_key) -> [((cycle_at, sport_key, queue_position),
    provider_lag_s)] in the base's judged order, for every candidate that had
    a price and reached the arrival check."""
    out = collections.OrderedDict()
    for cyc, at, sport, q, lag, proc, outcome, first in K1_ROWS:
        if lag is None:
            continue
        out.setdefault((at, sport), []).append(((at, sport, q), lag))
    return out


def _k1_forty():
    """The spec's 40-candidate fixture, built from the K1 table: the five
    lowest queue positions with a measured lag from each of the 8 fetches,
    listed fetch by fetch -- slacks 0.2 .. 27.9 s."""
    forty = []
    for cands in _fetches().values():
        forty += sorted(cands, key=lambda c: c[0][2])[:5]
    assert len(forty) == 40
    return forty


RX = 1_000_000.0          # the receipt instant of a replayed fetch, epoch s
AS_LISTED, FRESHEST_FIRST, EARLIEST_DEADLINE = ("AS_LISTED", "FRESHEST_FIRST",
                                                "EARLIEST_DEADLINE")


def _order(cands, how):
    if how == AS_LISTED:
        return list(cands)
    if how == FRESHEST_FIRST:
        # the base's lever B: newest stamp first, the event id the tiebreak
        return sorted(cands, key=lambda c: (False, -(RX - c[1]), str(c[0])))
    # the head's rule, through the module's own key
    return sorted(cands, key=lambda c: L.collector_read_order_key(
        RX - c[1], c[0]))


def _serial_fetch(cands, *, how, read_s=READ_S, floor=True):
    """ONE FETCH, REPLAYED: `cands` [(id, provider_lag_s)] received at t=0,
    judged in `how` order, one paced read of `read_s` per candidate. Before
    every read the deadline is re-tested with the module's own slack and floor
    functions: already past the rule -> lever A's refusal, no read; below the
    floor (when `floor`) -> skipped, no read; else the read is made and the
    decision is inside the rule iff it ends before the deadline. Returns
    {id: PRICED | STALE_ON_ARRIVAL | SKIPPED_BELOW_FLOOR | READ_PAST_DEADLINE}
    and the number of reads made."""
    t, out, reads = 0.0, {}, 0
    for cid, lag in _order(cands, how):
        slack = L.candidate_slack_s(RX - lag, RX + t)
        assert slack == pytest.approx(LIMIT - (lag + t))
        if slack <= 0:
            out[cid] = "STALE_ON_ARRIVAL"
            t += 0.05
            continue
        if floor and L.below_the_read_floor(slack):
            out[cid] = "SKIPPED_BELOW_FLOOR"
            t += 0.05
            continue
        reads += 1
        t += read_s
        out[cid] = ("PRICED" if L.candidate_slack_s(RX - lag, RX + t) >= 0
                    else "READ_PAST_DEADLINE")
    return out, reads


def _count(out):
    return collections.Counter(out.values())


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RULE, THE FLOOR AND THE CODE ARE STATED; NOTHING ELSE MOVED
# ═════════════════════════════════════════════════════════════════════

def test_the_rule_the_floor_and_the_code_are_stated_and_nothing_else_moved():
    # THE LIMIT, ITS CLOCK, THE PACING AND THE REFRESH BOUND ARE UNCHANGED
    assert L.PINNACLE_MAX_AGE_S == 30.0
    assert L.probability_deadline(1000.0) == 1030.0
    assert VP.MIN_GAP_S == 0.35 and VP.PENALTY_MULT == 2.0
    assert L.ADAPTIVE_ODDS_REFETCH_MAX_PER_SPORT == 0
    assert L.VENUE_TIMEOUT_S == 10.0
    # THE FLOOR IS THE MEASURED HEAD-OF-QUEUE READ, cited at its definition
    assert L.VENUE_READ_HEAD_OF_QUEUE_FLOOR_S == 6.0
    src = inspect.getsource(L)
    i = src.index("VENUE_READ_HEAD_OF_QUEUE_FLOOR_S = 6.0")
    assert "6.58 s" in src[i - 600:i] and "38078680376" in src[i - 600:i]
    assert "earliest-deadline-first" in L.COLLECTOR_READ_ORDER_RULE
    assert "re-tested before every read" in L.COLLECTOR_READ_ORDER_RULE
    assert "unchanged" in L.COLLECTOR_READ_ORDER_RULE
    assert "LIMIT IS UNCHANGED" in L.WHY_SLACK_BELOW_THE_FLOOR
    # THE CODE: its own name, the same class / family / stage as lever A's
    # refusal (it names the loss more precisely, it does not move it)
    assert L.R_SLACK_BELOW_THE_READ_FLOOR == CODE
    assert CODE != L.R_QUOTE_STALE_ON_ARRIVAL
    assert CODE != L.R_READ_PAST_PROBABILITY_DEADLINE
    assert TT.TABLE[CODE] == TT.TABLE["QUOTE_STALE_ON_ARRIVAL"] == (
        "SOFTWARE", "FRESHNESS_PLUMBING", "FRESHNESS")
    k = RT.classify(CODE)
    assert k["classified"] and k["class"] == RT.SOFTWARE
    assert ext.STAGE_OF[CODE] == "2_FRESHNESS"
    assert ext.EVALUABILITY_OF[CODE] == ext.DECIDED
    # ...and the census reads it exactly as it reads QUOTE_STALE_ON_ARRIVAL:
    # a SOFTWARE loss at FAIR_VALUE -- a skipped candidate keeps the census
    # outcome it had
    assert CFL.classify(CODE)["class"] == RT.SOFTWARE
    assert (CFL.chain_stage(CODE, lane_stage="2_FRESHNESS", mapped=True)
            == CFL.chain_stage("QUOTE_STALE_ON_ARRIVAL",
                               lane_stage="2_FRESHNESS", mapped=True)
            == "FAIR_VALUE")
    # a skip made no read: no calibration-only record claims a book for it
    assert CODE not in L.CALIBRATION_ONLY_AFTER_READ_FAILURE
    # the ledger columns the telemetry is written to (migration 368)
    assert L.CANDIDATE_OUTCOME_368_COLUMNS == (
        "queue_wait_s", "gate_wait_s", "cooldown_wait_s", "http_s",
        "book_source")
    assert grt.READ_TELEMETRY_KEYS == ("queue_wait_s", "gate_wait_s",
                                       "cooldown_wait_s", "http_s")


def test_earliest_deadline_first_is_the_providers_stamp_ascending_and_total():
    """Least slack first; an unreadable stamp LAST; the event id the final
    tiebreak; reads a stamp and an identity and nothing else."""
    k = L.collector_read_order_key
    oldest, newer, newest = k(1000.0, "c"), k(1010.0, "b"), k(1020.0, "a")
    assert sorted([newest, oldest, newer]) == [oldest, newer, newest]
    # the deadline is the stamp plus the unchanged rule, so the earliest
    # deadline IS the oldest stamp
    assert L.probability_deadline(1000.0) < L.probability_deadline(1020.0)
    # None (and a non-finite or unreadable stamp) sorts last, never first
    for bad in (None, "x", float("nan"), float("inf")):
        assert k(bad, "z") > newest
        assert k(bad, "z")[0] is True
    # a total order: equal stamps fall through to the event id
    assert k(1000.0, "a") < k(1000.0, "b")
    assert k(1000.0, "a") == k(1000.0, "a")
    # THE R30A CLASSES STAND ABOVE IT: a deferred event with a fresher stamp
    # is still judged before an undeferred one with less slack
    now = 2000.0
    deferred = cov.candidate_order_key(
        "fresh-but-deferred", commence_epoch=None, now=now,
        deferred_since={"fresh-but-deferred": 1900.0},
        freshness_key=k(1020.0, "fresh-but-deferred"))
    plain = cov.candidate_order_key(
        "old-undeferred", commence_epoch=None, now=now, deferred_since={},
        freshness_key=k(1000.0, "old-undeferred"))
    assert deferred < plain
    # ...and inside one class the earliest deadline goes first
    a = cov.candidate_order_key("a", commence_epoch=None, now=now,
                                deferred_since={}, freshness_key=k(1020.0, "a"))
    b = cov.candidate_order_key("b", commence_epoch=None, now=now,
                                deferred_since={}, freshness_key=k(1000.0, "b"))
    assert b < a
    # THE CYCLE PASSES THIS KEY TO THE R30A SORT
    csrc = inspect.getsource(L.cycle)
    assert "freshness_key=collector_read_order_key(_ages[id(e)]," in csrc


def test_slack_and_the_floor_are_the_rules_arithmetic():
    assert L.candidate_slack_s(1000.0, 1025.5) == pytest.approx(4.5)
    assert L.candidate_slack_s(1000.0, 1031.0) == pytest.approx(-1.0)
    assert L.candidate_slack_s(None, 1000.0) is None
    assert L.candidate_slack_s("x", 1000.0) is None
    # below the floor: still INSIDE the rule, with less slack than one read
    assert L.below_the_read_floor(5.99) is True
    assert L.below_the_read_floor(0.001) is True
    # at or above the floor: read, exactly as before
    assert L.below_the_read_floor(6.0) is False
    assert L.below_the_read_floor(13.0) is False
    # already past the rule: lever A's, never this
    assert L.below_the_read_floor(0.0) is False
    assert L.below_the_read_floor(-2.0) is False
    # an unknown slack bounds nothing
    assert L.below_the_read_floor(None) is False
    assert L.below_the_read_floor("x") is False
    # the floor is a parameter of the rule, not a second constant somewhere
    assert L.below_the_read_floor(7.0, 8.0) is True


def test_the_floor_skip_sits_after_lever_a_and_before_the_read():
    """Lever A refuses a quote ALREADY past the rule first (by its own name);
    the floor skip follows it and precedes the venue read, which is the
    saving; both precede the read clock."""
    csrc = inspect.getsource(L.cycle)
    lever_a = csrc.index('lat["skipped_stale_on_arrival"] += 1')
    skip = csrc.index('lat["skipped_below_the_read_floor"] += 1')
    read = csrc.index("vq = await venue_quote(")
    assert lever_a < skip < read
    # the skip is the module's own rule, not an inline constant
    seg = csrc[lever_a:read]
    assert "below_the_read_floor(_slack)" in seg
    assert "candidate_slack_s(_pe, _arr)" in seg
    # ...names its refusal, ledgers it at the freshness stage, requeues it
    # when our queue ate a read's slack, and never reads
    assert "code = R_SLACK_BELOW_THE_READ_FLOOR" in seg
    assert '"stage": "2_FRESHNESS"' in seg
    assert "REQUEUE_AFTER_OUR_DELAY_RULE" in seg
    assert "_requeued[str(event[\"id\"])]" in seg
    # the telemetry is taken from the read's own result, right after it
    after = csrc[read:read + 4000]
    assert "_rtf = read_telemetry_fields(vq)" in after
    assert "_event_fields(_rtf)" in after


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REPLAY: THE K1 TABLE, THE 8 FETCHES AND THE 83 CYCLES
# ═════════════════════════════════════════════════════════════════════

def test_the_k1_fixture_prices_more_than_the_listed_order_and_starts_no_read_it_cannot_finish():
    """THE SPEC'S FIXTURE: 40 candidates whose slacks are the K1 table's,
    5.5 s per read. In the listed order the base prices the first 2 of 40
    and spends 3 reads on candidates whose deadline passed during the read;
    earliest-deadline-first with the floor prices 4, skips 36 without a read
    and starts NO read it cannot finish. The order alone (without the floor)
    would read the doomed candidates first and price none -- the floor is
    part of the rule, not an option."""
    forty = _k1_forty()
    slacks = sorted(round(LIMIT - lag, 1) for _, lag in forty)
    assert slacks[0] == pytest.approx(0.2) and slacks[-1] == pytest.approx(27.9)

    listed, listed_reads = _serial_fetch(forty, how=AS_LISTED, floor=False)
    base, base_reads = _serial_fetch(forty, how=FRESHEST_FIRST, floor=False)
    edf, edf_reads = _serial_fetch(forty, how=EARLIEST_DEADLINE, floor=True)
    edf_bare, _ = _serial_fetch(forty, how=EARLIEST_DEADLINE, floor=False)
    c_listed, c_base, c_edf, c_bare = map(_count, (listed, base, edf,
                                                   edf_bare))
    # the base's figure, to the candidate
    assert c_listed["PRICED"] == 2
    assert c_listed["READ_PAST_DEADLINE"] == 3
    # the head prices more than the listed order, no fewer than the base's
    # freshest-first, and never starts a read that cannot finish
    assert c_edf["PRICED"] == 4 > c_listed["PRICED"]
    assert c_edf["PRICED"] >= c_base["PRICED"] == 4
    assert c_edf["READ_PAST_DEADLINE"] == 0
    assert c_base["READ_PAST_DEADLINE"] == 1
    assert c_edf["SKIPPED_BELOW_FLOOR"] == 36
    assert c_edf["STALE_ON_ARRIVAL"] == 0
    # ...with fewer requests to a venue that rate-limits us
    assert edf_reads == 4 < base_reads == 5 == listed_reads
    # EVERY candidate was judged (nothing dropped) and every skipped one was
    # still inside the rule at its instant -- the limit did not move
    assert sum(c_edf.values()) == 40
    # the order alone, without the floor, is worse than the base: it reads
    # the doomed candidates first
    assert c_bare["PRICED"] == 0 and c_bare["READ_PAST_DEADLINE"] >= 1


def test_every_production_fetch_of_the_packet_window_replays_with_no_doomed_read():
    """THE 8 FETCHES OF THE PACKET WINDOW, candidate by candidate, in the
    order the base judged them (queue_position) and in the head's order. The
    provider's stamps inside one fetch are near-uniform (one snapshot), so
    the order itself changes little there; what changes is that the base made
    one read per fetch whose candidate's deadline passed during the read --
    and every candidate behind it waited for it -- and the head makes none."""
    fetches = _fetches()
    assert len(fetches) == 8
    # 42 + 42 NCAAF, 7 + 7 + 7 Liga MX, 15 + 15 + 15 MLS candidates had a
    # price and reached the arrival check (the other 94 K1 rows were refused
    # before a price existed and spent no read)
    assert sum(len(c) for c in fetches.values()) == 150
    assert len(K1_ROWS) == 244
    base_doomed = edf_doomed = base_reads_total = edf_reads_total = 0
    for key, cands in fetches.items():
        base, base_reads = _serial_fetch(cands, how=AS_LISTED, floor=False)
        edf, edf_reads = _serial_fetch(cands, how=EARLIEST_DEADLINE,
                                       floor=True)
        cb, ce = _count(base), _count(edf)
        assert ce["PRICED"] >= cb["PRICED"], key
        assert ce["READ_PAST_DEADLINE"] == 0, key
        assert cb["READ_PAST_DEADLINE"] >= 1, key
        assert edf_reads <= base_reads, key
        # nothing dropped, every candidate judged once
        assert sum(ce.values()) == len(cands) == sum(cb.values())
        base_doomed += cb["READ_PAST_DEADLINE"]
        edf_doomed += ce["READ_PAST_DEADLINE"]
        base_reads_total += base_reads
        edf_reads_total += edf_reads
    assert base_doomed == 8 and edf_doomed == 0
    assert edf_reads_total < base_reads_total


def test_the_83_inside_limit_cycles_replay_as_lag_and_queue_position_pairs():
    """I1: the 38 first-loss events' 83 cycles whose quote arrived INSIDE the
    rule. The rows themselves show the defect -- in every one our processing
    exceeded the slack the quote arrived with -- and the replay shows the
    repair: under the head's order none of the 83 is read past its deadline;
    each is read inside its slack or skipped without a read, by name."""
    assert len(I1B_ROWS) == 83
    by_sport = collections.Counter(r[0] for r in I1B_ROWS)
    assert by_sport == {"americanfootball_ncaaf": 57, "soccer_usa_mls": 18,
                        "soccer_mexico_ligamx": 8}
    # THE DEFECT, FROM THE ROWS: delivered inside the limit, and our queue
    # took longer than the slack every time; positions 1..41
    for sport, at, q, lag, proc, beside in I1B_ROWS:
        assert 0 < lag <= LIMIT, (sport, at, q)
        assert proc > LIMIT - lag, (sport, at, q, lag, proc)
    assert min(r[2] for r in I1B_ROWS) == 1
    assert max(r[2] for r in I1B_ROWS) == 41
    # THE REPLAY, inside each row's own fetch
    ids = {(at, sport, q) for sport, at, q, lag, proc, beside in I1B_ROWS}
    base_c, edf_c = collections.Counter(), collections.Counter()
    for key, cands in _fetches().items():
        base, _ = _serial_fetch(cands, how=AS_LISTED, floor=False)
        edf, _ = _serial_fetch(cands, how=EARLIEST_DEADLINE, floor=True)
        for cid, lag in cands:
            if cid in ids:
                base_c[base[cid]] += 1
                edf_c[edf[cid]] += 1
    assert sum(base_c.values()) == sum(edf_c.values()) == 83
    assert edf_c["READ_PAST_DEADLINE"] == 0
    assert edf_c["STALE_ON_ARRIVAL"] == 0
    assert edf_c["PRICED"] >= base_c["PRICED"]
    assert edf_c["PRICED"] + edf_c["SKIPPED_BELOW_FLOOR"] == 83
    # the base: the same 83 refused after waiting, or read past the deadline
    assert base_c["STALE_ON_ARRIVAL"] + base_c["READ_PAST_DEADLINE"] >= 80


# ═════════════════════════════════════════════════════════════════════
# 3 · WHERE THE READ'S TIME WENT, AT THE TRANSPORT AND ON THE ROW
# ═════════════════════════════════════════════════════════════════════

def _fake_venue_clock(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(VP, "_clock", lambda: clock["t"])
    monkeypatch.setattr(VP, "_rand", lambda: 0.0)
    monkeypatch.setattr(
        grt, "_cooldown_sleep",
        lambda s: clock.__setitem__("t", clock["t"] + float(s)))
    return clock


def test_a_gate_cooldown_stall_is_recorded_with_its_wait_at_the_transport(
        monkeypatch):
    """A deadlined collector read meets the escalating 429 cooldown and the
    hard not-before hold: it waits them out (inside its deadline, exactly as
    before) and the transport records EACH wait on the read -- the cooldown,
    the hold, the pacer's queue and gap, and the venue's answer time -- apart.
    Nothing waits longer or shorter for being measured."""
    httpx = pytest.importorskip("httpx")
    clock = _fake_venue_clock(monkeypatch)
    VP.reset_rate_limit_state()
    grt.clear_hold()
    sent = []

    class Inner(httpx.BaseTransport):
        def handle_request(self, request):
            sent.append(request)
            clock["t"] += 0.25                     # the venue's answer time
            return httpx.Response(200)

    def pace():
        clock["t"] += 0.35                         # the gap, claimed

    monkeypatch.setattr(grt, "_hold_sleep", lambda s: None)
    try:
        # the venue said 429 (Retry-After 2 s): the first rung arms 5 s
        armed = VP.note_rate_limited(retry_after_s=2.0)
        assert armed["escalated"] and armed["cooldown_s"] == 5.0
        # ...and a hard hold of ~0.4 s stands (hold_until, wall clock)
        grt.hold_until(until_epoch_s=time.time() + 0.4, reason="test")
        rid = grt.begin_read(slug="s", deadline_epoch_s=time.time() + 60)
        grt.bind_read(rid)
        try:
            t = grt.PacedTransport(Inner(), pace=pace)
            resp = t.handle_request(httpx.Request("GET", "https://x/v1/book"))
            assert resp.status_code == 200 and len(sent) == 1
            st = grt.read_state(rid)
            tele = grt.read_telemetry(rid)
        finally:
            grt.bind_read(None)
            grt.end_read(rid)
        assert st["cooldown_wait_s"] == pytest.approx(5.0)
        assert 0.3 <= st["gate_wait_s"] <= 0.45
        assert st["queue_wait_s"] == pytest.approx(0.35)
        assert st["http_s"] == pytest.approx(0.25)
        assert st["dispatched"] == 1 and st["gate_refusals"] == []
        assert tele == {"queue_wait_s": pytest.approx(0.35),
                        "gate_wait_s": st["gate_wait_s"],
                        "cooldown_wait_s": 5.0, "http_s": 0.25,
                        "dispatched": 1, "rate_limited": 0,
                        "gate_refusals": 0}
        # the cooldown itself counted the wait as a deadlined read's, as before
        assert VP.rate_limit_state()["deadline_waits"] == 1
        assert grt.read_telemetry("no-such-read") is None
    finally:
        VP.reset_rate_limit_state()
        grt.clear_hold()


def test_a_read_our_gate_refuses_still_carries_what_it_waited(monkeypatch):
    """The gate-refusal branch of the collector's reader carries the read's
    accounting (a stall recorded before the refusal), venue_quote carries it
    as `read_telemetry` -- with and without a probability deadline -- and the
    ledger fields read it with the book source."""
    from sportsassets import pmus

    class _Client:
        pass

    monkeypatch.setattr(pmus, "_get_client", lambda: _Client())

    def refused(client, slug):
        grt.note_wait(grt.current_read(), "cooldown", 2.5)
        grt.note_wait(grt.current_read(), "queue", 0.35)
        raise grt.VenueGateRefusal(grt.R_COOLDOWN_EXCEEDS_DEADLINE, {
            "refusal": grt.R_COOLDOWN_EXCEEDS_DEADLINE, "seconds_left": 12.0,
            "not_before_epoch_s": time.time() + 12.0})
    monkeypatch.setattr(pmus, "book_read", refused)
    out = L._read_book_blocking("aec-mls-a-b-2026-10-10")
    assert out["error"] == grt.R_COOLDOWN_EXCEEDS_DEADLINE
    assert out["refused_by"] == "OUR_REQUEST_GATE"
    acc = out["request_accounting"]
    assert acc["cooldown_wait_s"] == 2.5 and acc["queue_wait_s"] == 0.35
    assert acc["gate_wait_s"] == 0.0 and acc["http_s"] == 0.0
    assert len(acc["gate_refusals"]) == 0       # noted by the gate, not here
    # the read id is closed and unbound after the refusal, as always
    assert grt.current_read() is None

    monkeypatch.setattr(L, "_read_book_blocking", lambda slug: out)
    # without a probability deadline: the gate's own refusal, telemetry kept
    vq = __import__("asyncio").run(L.venue_quote(
        None, us_slug="aec-mls-a-b-2026-10-10", intent="ORDER_INTENT_BUY_LONG",
        pmx_allowed=False))
    assert vq["refusal"] == L.R_VENUE_READ_ERROR
    assert vq["book_source"] == "REST"
    assert vq["read_telemetry"]["cooldown_wait_s"] == 2.5
    assert vq["read_telemetry"]["queue_wait_s"] == 0.35
    fields = L.read_telemetry_fields(vq)
    assert fields == {"queue_wait_s": 0.35, "gate_wait_s": 0.0,
                      "cooldown_wait_s": 2.5, "http_s": 0.0,
                      "book_source": "REST"}
    # with the candidate's probability deadline (inside VENUE_TIMEOUT_S, so
    # it binds): the P1 refusal by name, the telemetry still on it
    vq2 = __import__("asyncio").run(L.venue_quote(
        None, us_slug="aec-mls-a-b-2026-10-10", intent="ORDER_INTENT_BUY_LONG",
        deadline_epoch_s=time.time() + 5.0, pmx_allowed=False))
    assert vq2["refusal"] == L.R_READ_PAST_PROBABILITY_DEADLINE
    assert vq2["bound"].startswith("REFUSED_BY_OUR_GATE")
    assert vq2["read_telemetry"]["cooldown_wait_s"] == 2.5
    assert L.read_telemetry_fields(vq2)["book_source"] == "REST"
    # a read never made leaves the fields None, never 0
    assert L.read_telemetry_fields({"ok": False, "refusal": "x"}) == {
        "queue_wait_s": None, "gate_wait_s": None, "cooldown_wait_s": None,
        "http_s": None, "book_source": None}
    assert L.read_telemetry_fields(None)["book_source"] is None
    # a PMX book made no request: measured as none, with its source
    pmx = L._book_read_telemetry({"observed_at": 1.0}, L._PMX_SOURCE)
    assert pmx == {"queue_wait_s": 0.0, "gate_wait_s": 0.0,
                   "cooldown_wait_s": 0.0, "http_s": 0.0, "dispatched": 0,
                   "rate_limited": 0, "gate_refusals": 0}
    assert L._book_read_telemetry({"marketData": None}, "REST") is None


def test_the_heartbeat_digest_carries_the_refresh_budget_and_the_read_telemetry():
    """C2's counters (capped / budget_spent / fired) and the read order's
    figures reach the persisted heartbeat, not only the cycle return."""
    fr = {"fired": 1, "saved": 0, "still_stale": 1, "failed": 0,
          "capped": 7, "budget_spent": 2}
    tele = L._read_telemetry_digest({
        "queue_wait_s": [0.35, 0.4], "gate_wait_s": [],
        "cooldown_wait_s": [5.0], "http_s": [0.25, 0.3, 0.2]})
    out = {"odds_freshness": {"adaptive_refetch": fr,
                              "adaptive_refetch_max_per_sport": 0,
                              "adaptive_refetch_proposed_max_per_sport": 4,
                              "events_per_odds_fetch": 40, "max_per_cycle": 40,
                              "is_the_default": True, "odds_refetches": 0,
                              "odds_refetch_failures": 0},
           "latency": {"skipped_below_the_read_floor": 3,
                       "read_floor_s": 6.0, "venue_read_telemetry": tele,
                       "provider_lag_s": 16.9, "our_processing_s": 2.1}}
    d = L._freshness_digest(out)
    assert d["adaptive_refetch"] == fr
    assert d["adaptive_refetch"]["capped"] == 7
    assert d["adaptive_refetch"]["budget_spent"] == 2
    assert d["adaptive_refetch"]["fired"] == 1
    assert d["adaptive_refetch_max_per_sport"] == 0
    assert d["adaptive_refetch_proposed_max_per_sport"] == 4
    assert d["skipped_below_the_read_floor"] == 3
    assert d["read_floor_s"] == 6.0
    assert d["venue_read_telemetry"]["cooldown_wait_s"] == {
        "median_s": 5.0, "max_s": 5.0, "sum_s": 5.0, "samples": 1}
    assert d["venue_read_telemetry"]["gate_wait_s"] == {
        "median_s": None, "max_s": None, "sum_s": None, "samples": 0}
    assert d["venue_read_telemetry"]["http_s"]["samples"] == 3
    # the heartbeat persists the digest
    assert '"odds_freshness": _freshness_digest(out)' in inspect.getsource(
        L._heartbeat)
    # and the cycle return names the floor, the rule and the telemetry
    csrc = inspect.getsource(L.cycle)
    for k in ('"skipped_below_the_read_floor":', '"read_floor_s":',
              '"read_order_rule":', '"venue_read_telemetry":'):
        assert k in csrc, k


# ═════════════════════════════════════════════════════════════════════
# 4 · THROUGH THE REAL CYCLE AND THE REAL LEDGER (real Postgres)
# ═════════════════════════════════════════════════════════════════════

async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


def _event(F, *, stamp_epoch):
    stamp = _dt.datetime.fromtimestamp(stamp_epoch, _dt.timezone.utc
                                       ).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = F.odds_event(time.time())
    for bk in ev["bookmakers"]:
        bk["last_update"] = stamp
        for m in bk["markets"]:
            if "last_update" in m:
                m["last_update"] = stamp
    return ev


async def _cycle_with(monkeypatch, *, stamp_off_s):
    """One real scheduled cycle over the production-shaped single-event
    fixture, the provider's HTTP substituted so the quote arrives
    `-stamp_off_s` old at receipt (received_at = the call instant), in
    production's state (book currency NOT established, every submission
    switch off). Returns (conn, F, venue, out, rows)."""
    from tests import _emptybook_fixture as F
    conn = await _connect()
    venue = F.Venue()
    await F.clean(conn)
    await F.seed(conn)
    real_currency = L.book_currency_evidence
    F.substitute(monkeypatch, venue)
    monkeypatch.setattr(L, "book_currency_evidence", real_currency)
    from sportsassets import bettor_entry_execution as EX
    from sportsassets import bettor_funded_execution as FX
    from sportsassets import bettor_funded_management as FM
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", False)

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": now,
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True, "events": [_event(F, stamp_epoch=now + stamp_off_s)],
                "received_at": now, "credits_used": "1",
                "credits_remaining": "9"}
    monkeypatch.setattr(L, "fetch_odds", fake_odds)
    handed: list = []

    async def paper_hook(conn_, valuation_id):
        handed.append(valuation_id)
    monkeypatch.setattr(L, "_paper_valuation", paper_hook)
    out = await L.cycle(conn)
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM ext_candidate_outcomes "
        " WHERE provider_event_id LIKE 'odds-emptybook%' "
        " ORDER BY id DESC LIMIT 5")]
    return conn, F, venue, out, rows, handed


@pg
@pytest.mark.asyncio
async def test_a_below_floor_candidate_is_skipped_without_a_read_through_the_real_cycle(
        monkeypatch):
    """The provider hands the quote over 26 s old (inside the rule, 4 s of
    slack: less than one read). The real cycle refuses it by name BEFORE the
    venue read -- the venue receives no book request -- ledgers it at the
    freshness stage with its slack and the floor, keeps it a SOFTWARE loss in
    the census, and hands nothing on. On the base the book was read."""
    conn, F, venue, out, rows, handed = await _cycle_with(
        monkeypatch, stamp_off_s=-26.0)
    try:
        assert out["ran"] is True, out.get("why")
        assert out["refusals"].get(CODE) == 1, out["refusals"]
        assert not out["refusals"].get(L.R_QUOTE_STALE_ON_ARRIVAL)
        assert out["latency"]["skipped_below_the_read_floor"] == 1
        assert out["latency"]["read_floor_s"] == 6.0
        assert out["latency"]["venue_requests"] == 0
        assert out["latency"]["venue_read_telemetry"]["http_s"]["samples"] == 0
        # NO BOOK WAS REQUESTED from the venue for this candidate
        assert [s for s in venue.sent if s[0] == "markets.book"] == []
        assert venue.creates_sent() == [] and handed == []
        # the mapped-candidate ledger line names the slack and the floor
        line = [r for r in out["mapped_candidate_ledger"]
                if r.get("first_refusal") == CODE]
        assert len(line) == 1
        assert line[0]["stage"] == "2_FRESHNESS"
        assert 0.0 < line[0]["slack_s"] < 6.0
        assert line[0]["read_floor_s"] == 6.0
        assert line[0]["limit_s"] == 30.0
        assert line[0]["attribution"] == L.SLACK_BASIS_AS_DELIVERED
        # the durable row: refused by name at the freshness stage, its
        # arrival clocks measured, no read so no telemetry (NULL, never 0)
        assert out["candidate_outcomes"]["persisted"]["columns_368"] is True
        assert len(rows) == 1
        r = rows[0]
        assert r["outcome"] == "REFUSED" and r["first_refusal"] == CODE
        assert r["stage"] == "2_FRESHNESS"
        codes = json.loads(r["codes"]) if isinstance(r["codes"], str) \
            else list(r["codes"])
        assert codes[0] == CODE
        assert 25.0 < r["provider_lag_s"] < 30.0
        assert r["our_processing_s"] is not None
        for k in ("queue_wait_s", "gate_wait_s", "cooldown_wait_s", "http_s",
                  "book_source"):
            assert r[k] is None, k
        # the census: a SOFTWARE first loss at FAIR_VALUE, as lever A's was
        fl = CFL.first_loss_of_event(
            {"first_refusal": CODE, "codes": codes, "outcome": "REFUSED",
             "stage": "2_FRESHNESS", "reach": 2}, [], [],
            valuations_read=True, decisions_read=True)
        assert fl["class"] == RT.SOFTWARE
        assert fl["code"] == CODE
    finally:
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_candidate_with_a_reads_worth_of_slack_is_read_and_its_row_carries_the_telemetry(
        monkeypatch):
    """The same quote 20 s old (10 s of slack): read exactly as before; the
    row carries the source that served its book and where the read spent its
    time (the fixture's venue answers at its transport boundary without the
    paced transport, so every wait measures 0.0 -- measured, not NULL)."""
    conn, F, venue, out, rows, handed = await _cycle_with(
        monkeypatch, stamp_off_s=-20.0)
    try:
        assert out["ran"] is True, out.get("why")
        assert not out["refusals"].get(CODE)
        assert out["latency"]["skipped_below_the_read_floor"] == 0
        assert [s for s in venue.sent if s[0] == "markets.book"] != []
        assert out["latency"]["venue_requests"] == 1
        assert out["latency"]["venue_book_sources"]["REST"] == 1
        tele = out["latency"]["venue_read_telemetry"]
        assert tele["http_s"]["samples"] == 1
        assert tele["cooldown_wait_s"] == {"median_s": 0.0, "max_s": 0.0,
                                           "sum_s": 0.0, "samples": 1}
        assert len(rows) == 1
        r = rows[0]
        assert r["book_source"] == "REST"
        for k in ("queue_wait_s", "gate_wait_s", "cooldown_wait_s", "http_s"):
            assert r[k] == 0.0, k
        assert r["first_refusal"] != CODE
        # NO VENUE ORDER; the candidate reached a recorded (calibration-only,
        # in production's state) valuation and was handed on, exactly as
        # before this lane
        assert venue.creates_sent() == []
        assert len(handed) >= 1
    finally:
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_ledger_row_carries_the_read_telemetry_columns():
    """The writer writes migration 368's five columns when the database has
    them, says so on its result, and reads them back to the millisecond."""
    conn = await _connect()
    cycle_at = time.time()
    row = {"sport_key": "soccer_usa_mls", "family": "soccer",
           "queue_position": 7, "provider_event_id": "odds-emptybook-tele-368",
           "home": "A", "away": "B", "commence_time": "2026-10-11T23:10:00Z",
           "global_slug": "mls-a-b-2026-10-11",
           "us_market_slug": "aec-mls-a-b-2026-10-11", "stage": "2_FRESHNESS",
           "outcome": "REFUSED",
           "first_refusal": "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
           "codes": ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"],
           "provider_lag_s": 16.932, "our_processing_s": 7.651,
           "quote_age_s": 24.583, "mapped_by": "VENUE_NATIVE",
           "global_refusal_replaced": "NO_VENUE_CONTRACT_FOR_EVENT",
           "queue_wait_s": 0.412, "gate_wait_s": 0.0,
           "cooldown_wait_s": 5.0, "http_s": 0.281, "book_source": "REST"}
    got = None
    try:
        got = await L._persist_candidate_outcomes(conn, cycle_at=cycle_at,
                                                  rows=[row])
        assert got["ok"] is True and got["rows"] == 1
        assert got["columns_143"] is True and got["columns_368"] is True
        assert "why_no_read_telemetry_columns" not in got
        back = await conn.fetchrow(
            "SELECT queue_position, queue_wait_s, gate_wait_s, "
            " cooldown_wait_s, http_s, book_source, provider_lag_s, "
            " our_processing_s, first_refusal FROM ext_candidate_outcomes "
            " WHERE cycle_id = $1", got["cycle_id"])
        assert back["queue_position"] == 7
        assert back["queue_wait_s"] == pytest.approx(0.412)
        assert back["gate_wait_s"] == 0.0
        assert back["cooldown_wait_s"] == pytest.approx(5.0)
        assert back["http_s"] == pytest.approx(0.281)
        assert back["book_source"] == "REST"
        assert back["provider_lag_s"] == pytest.approx(16.932)
        assert back["first_refusal"] == "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"
        # a row with no read writes NULL, never 0
        row2 = dict(row, queue_position=8, queue_wait_s=None,
                    gate_wait_s=None, cooldown_wait_s=None, http_s=None,
                    book_source=None, first_refusal=CODE, codes=[CODE])
        got2 = await L._persist_candidate_outcomes(conn, cycle_at=cycle_at,
                                                   rows=[row2])
        assert got2["ok"] is True
        back2 = await conn.fetchrow(
            "SELECT queue_wait_s, http_s, book_source, first_refusal "
            " FROM ext_candidate_outcomes WHERE cycle_id = $1",
            got2["cycle_id"])
        assert back2["queue_wait_s"] is None and back2["http_s"] is None
        assert back2["book_source"] is None
        assert back2["first_refusal"] == CODE
        await conn.execute("DELETE FROM ext_candidate_outcomes "
                           " WHERE cycle_id = $1", got2["cycle_id"])
    finally:
        if got and got.get("cycle_id"):
            await conn.execute("DELETE FROM ext_candidate_outcomes "
                               " WHERE cycle_id = $1", got["cycle_id"])
        await conn.execute("DELETE FROM ext_candidate_outcomes "
                           " WHERE provider_event_id = 'odds-emptybook-tele-368'")
        await conn.close()
