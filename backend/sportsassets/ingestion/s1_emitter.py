"""S1 — the quiesced chain emitter (flip contract, backend/docs/s1_flip_contract.md).

Promotes the shadow's proven decode (decode_shadow_views +
classify_mints + the venue-shaped per-market selection) to a LIVE
second chain emitter for exactly the fill shapes `_handle_v3`'s
receipt reconstruction cannot produce: events 2..N of a tx, bundles,
mint-matched complements — the classes that today die at the
executor's staleness cap because only the poller carries them.

This module is the ONLY place that imports both the pipeline's ingest
and the shadow's pure decode functions. shadow_v2 itself is untouched
and keeps measuring independently (contract C2): the instrument that
certifies the flip is never contaminated by the flip.

DESIGN (survived a 3-design adversarial panel; kills and grafts are
recorded in the flip contract):

- Emission timing: buffer raw logs per tx; a tx finalizes only after a
  3s debounce AND the chain head has advanced CONFIRM_DEPTH past the
  fill block (the head is self-advanced by polling when the logs-only
  WS goes quiet). Timestamps come from a STRICT resolver — never
  wall-clock, never a cached guess; the same RPC's returned blockHash
  must equal the buffered log's blockHash (reorg check for free).
- THE EMIT SET: per (tx, wallet) group after classify_mints, only
  (a) agg records and (b) exec_owner records whose asset has no agg
  view. exec_counter / exec_mint records are tie-out evidence, never
  rows: a counter-only group means the wallet's OWN event was lost,
  and emitting the raw complement leg would buy the wrong outcome
  (the panel's critical kill). One untrusted member abstains the
  whole group; the poller carries everything abstained.
- Collision protocol with _handle_v3: the receipt path claims
  (tx, wallet) first (claim_registry); the emitter proceeds only on
  outcome 'refused' or no-claim, and both paths run a trades-table
  pre-probe immediately before ingest — the DB is the cross-restart
  authority.
- Certification gate: emits only while the env flag is on, the
  persisted `armed` flag is set, AND the shadow's own state row is
  GREEN (7-day window, 24h health, matching decoder fingerprint,
  volume floor). Arming requests (S1_ARM env) are honoured only while
  GREEN; a window reset after arming auto-disarms; a key self-check
  failure or observed key divergence trips STICKY (manual re-arm
  only). Unarmed with the flag on = burn-in: the full pipeline runs
  and counts s1.would_emit, writing nothing.

Money safety: everything downstream of ingest_trade_result — dedupe,
fan-out, every executor gate — is byte-untouched (contract C1). The
executor's one-copy-per-asset guard remains the final backstop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import weakref
from collections import OrderedDict
from decimal import Decimal
from typing import Any

import httpx

from .claim_registry import claim as _claim
from .claim_registry import get as _claim_get
from .shadow_v2 import (
    DECODER_FP,
    classify_mints,
    agg_tieout,
    decode_shadow_views,
    rec_keys,
    rec_prices,
)

log = logging.getLogger(__name__)

PENDING_CAP = 4096
DEBOUNCE_S = 3.0
CONFIRM_DEPTH = 3
TS_BUDGET_S = 30.0
EMIT_MAX_AGE_S = 45.0
V3_WAIT_S = 10.0
RPC_PER_TICK = 4
RPC_PER_MIN = 30
RPC_BACKOFF_S = 120.0
TICK_S = 1.0
# The head poll exists for when the logs-only WS goes QUIET — round 6
# measured the un-gated version burning 60 tokens/min under live
# traffic (there is always a tx younger than CONFIRM_DEPTH) and
# starving timestamp resolution 30-to-1. observe() already advances
# the head from every WS log; the poll fires only after the WS has
# not advanced it for HEAD_QUIET_S, at most once per HEAD_POLL_MIN_S.
HEAD_QUIET_S = 6.0
HEAD_POLL_MIN_S = 5.0
TS_CACHE_CAP = 1024                 # block ts is per-BLOCK, not per-tx
COUNTED_CAP = 8192                  # burn-in marks survive entry pop
CONTESTED_CAP = 4096                # proven two-hash heights (r32)
CONTESTED_FLUSH_CAP = 512           # marks one flush doc carries (r34)
BLOCKS_PER_TX_CAP = 8               # heights one entry may record (r33)
REMOVED_TX_CAP = 8192               # proven-orphan tx marks (r39)
RECON_VENUE_LAG_S = 600.0           # data-api indexing lag margin
RECON_TS_MARGIN_S = 300.0           # venue ts vs block ts skew margin
SUSPECT_HOLD_S = 3 * 3600.0         # r42: >= 3 further hourly walks
                                    # and 3 full poller passes
SUSPECT_BURST_WALLETS = 3           # r42: a feed degradation is per-
                                    # wallet; a wrong decode is
                                    # wallet-agnostic
SUSPECT_BURST_WINDOW_S = 6 * 3600.0  # r43: burst evidence must be
                                     # contemporaneous, never debris
FLUSH_EVERY_S = 60.0
CERT_EVERY_S = 60.0
CERT_WINDOW_S = 7 * 86400.0
CERT_HEALTH_S = 86400.0
CERT_MIN_VEN = 500
CERT_MIN_AGG = 50
STATE_KEY = "s1_emitter"
SHADOW_KEY = "shadow_v2_fill"

# Scoped per FILL (asset included): a (tx, whale)-only probe finds the
# row the emitter itself just wrote for the FIRST market of a bundle
# and silently forfeits every later market — the exact class S1 exists
# for (fleet round 1, confirmed major). Both chain sources count: a
# receipt row or an earlier emitter row equally forbids a second view.
SQL_PROBE = (
    "SELECT source, dedupe_key FROM trades "
    "WHERE lower(tx_hash) = $1 AND whale_id = $2 "
    "AND asset = $3 AND source IN ('chain', 's1')"
)
# The corroboration sweep: judgment anchors to the durable rows, not
# process memory (fleet round 3) — restart-proof, exactly-once via the
# s1_checked_at stamp, and the consequence of a venue-refuted emission
# lands on the responsible component: this emitter trips STICKY. A row
# whose whale left the roster is unjudgeable (no poll carrier remains)
# — counted, never alarmed (round 3: stamp starvation via roster ban).
# 45min -> 75min (fleet round 4): the sole backstop is the HOURLY
# reconciler, and the codebase's own sizing rule for exactly this
# window is ORPHAN_FINAL_S=4200 ("one deferral window covering the
# hourly reconciler"). 2700s sat INSIDE the backstop's worst case and
# falsely tripped correct emissions the /trades window slid past.
CORROBORATE_S = 75 * 60
# The pollable filter lives in the SQL (fleet round 5): unjudgeable
# rows re-entering the window at the FRONT of the oldest-first order
# could pin the LIMIT forever and starve every judgable row behind
# them of its alarm. Departed-whale rows stay unstamped and invisible
# to this query until the whale returns; the backlog gauge counts them
# separately.
# ROTATING windows (fleet round 7): deterministic oldest-first order
# rebuilt the starvation one tier up TWICE — 20 permanently-deferring
# wallets owned the wallet list forever, and a wallet's own 10 oldest
# deferring rows owned its window forever, so a younger judgable
# wrong emission behind either was never examined. Both windows now
# order by a per-sweep salted hash: every wallet and every row has
# equal probability of entering each 60s sweep, so nothing can be
# starved indefinitely — deferral defers, it no longer blocks.
SQL_SWEEP_WALLETS = """
SELECT w.id AS whale_id, w.address
FROM trades t JOIN whales w ON w.id = t.whale_id
WHERE t.source = 's1' AND t.s1_checked_at IS NULL
  AND t.detected_at < now() - make_interval(secs => $1)
  AND w.active AND NOT w.banned
GROUP BY w.id, w.address
ORDER BY md5(w.address || $2::text)
LIMIT 200
"""
# PER-WALLET windows (fleet round 6): rows DEFERRING at the coverage
# check defer only their own whale; rotation (round 7) stops them
# pinning even that.
SQL_SWEEP = """
SELECT t.id, t.dedupe_key, t.detected_at, t.ts, t.s1_suspect_at,
       (t.venue_seen_at IS NOT NULL) AS ok
FROM trades t
WHERE t.whale_id = $2 AND t.source = 's1' AND t.s1_checked_at IS NULL
  AND t.detected_at < now() - make_interval(secs => $1)
ORDER BY md5(t.id::text || $3::text)
LIMIT 10
"""
SQL_BACKLOG = """
SELECT count(*) FROM trades
WHERE source = 's1' AND s1_checked_at IS NULL
  AND detected_at < now() - make_interval(secs => $1)
"""
# The stamp is a TRANSITION, not an overwrite (fleet round 8): only
# rows still unstamped move, and the winners come back — counting from
# the returned set is what makes judgment exactly-once across
# concurrent processes, not merely across restarts.
SQL_MARK = ("UPDATE trades SET s1_checked_at = now() "
            "WHERE id = ANY($1::bigint[]) AND s1_checked_at IS NULL "
            "RETURNING id")
# The JUDGED stamp is conditional on the row STILL being venue-unseen
# at the stamp instant (fleet round 16, major): the round-15 last look
# read the row before the trip, but a venue stamp committing between
# the recheck and SQL_TRIP still became a permanent false verdict —
# no lock spans the gap. This WHERE makes verdict-time and
# permanence-time the same atomic instant: a stamp that lands proves
# the row was venue-unseen when the verdict became permanent; a stamp
# refused because venue_seen_at filled in means the trip just
# persisted brands a corroborated row — the sweep self-clears it and
# leaves the row unstamped for the next sweep to CONFIRM.
SQL_MARK_JUDGED = (
    "UPDATE trades SET s1_checked_at = now() "
    "WHERE id = ANY($1::bigint[]) AND s1_checked_at IS NULL "
    "AND venue_seen_at IS NULL RETURNING id")
# The SUSPECT stamp is a TRANSITION, exactly like SQL_MARK_JUDGED,
# and conditional on the row still being venue-unseen: a row the
# venue stamped inside the gap refuses and confirms via the ok path
# (round 42 design round: DETECTION splits from ARREST — a first
# qualification records a visible, counted, NON-disarming suspect).
SQL_SUSPECT = ("UPDATE trades SET s1_suspect_at = now() "
               "WHERE id = ANY($1::bigint[]) AND s1_suspect_at IS NULL "
               "AND s1_checked_at IS NULL AND venue_seen_at IS NULL "
               "RETURNING id")
# Positive proof the wallet's venue index moved PAST this fill, from
# a carrier independent of the reconciler's own testimony (r42).
SQL_INDEX_LIVE = """
SELECT EXISTS (
  SELECT 1 FROM trades
  WHERE whale_id = $1 AND source = 'poll'
    AND detected_at > $2::timestamptz + make_interval(secs => $3)
    AND ts > $4::timestamptz) AS live
"""
# r43 (major x2): the census was unpruned and roster-blind — three
# zombie suspects on banned/departed whales (whose rows SQL_SWEEP_
# WALLETS can never re-judge) armed the bypass FOREVER, converting
# the next single-wallet suspect into an instant false sticky trip.
# The burst premise is a CONTEMPORANEOUS wallet-agnostic decode bug:
# the census now counts only live-roster wallets with suspects
# younger than the window.
SQL_SUSPECT_WALLETS = (
    "SELECT count(DISTINCT t.whale_id) FROM trades t "
    "JOIN whales w ON w.id = t.whale_id "
    "WHERE t.source = 's1' AND t.s1_checked_at IS NULL "
    "AND t.venue_seen_at IS NULL AND t.s1_suspect_at IS NOT NULL "
    "AND t.s1_suspect_at > now() - make_interval(secs => $1) "
    "AND w.active AND NOT w.banned")
# R2 (round 42, prove-first): the venue's OWN row for the identical
# (whale, tx, asset) under a DIFFERENT dedupe key is not venue
# silence — it is a C6 key-fidelity failure and a C4-class duplicate
# already sitting in the table the executor reads. Immediate sticky,
# correctly named; venue_seen_at can never stamp across a key
# mismatch, so no hold and no healer applies.
# r43 (major): the twin test fired on the design's OWN same-tx
# same-asset second leg (r13/r14: S1 emits leg 1, the poller carries
# leg 2 — different price/size, hence a different key, and BOTH
# correct). The R2 class this alarm exists for is an IDENTICAL fill
# whose key shifted on ts drift alone — so the twin must match the
# row's economics exactly; a different-priced or different-sized
# sibling is a second fill, not a duplicate.
SQL_KEY_TWIN = (
    "SELECT v.id, v.dedupe_key FROM trades v "
    "JOIN trades s ON s.id = $1 "
    "WHERE v.id <> s.id AND v.whale_id = s.whale_id "
    "AND v.source = 'poll' AND lower(v.tx_hash) = lower(s.tx_hash) "
    "AND v.asset = s.asset AND v.size = s.size "
    "AND v.price = s.price AND v.dedupe_key <> s.dedupe_key LIMIT 1")
# The verdict is conditional on the backstop having actually had its
# chance AT THIS FILL (fleet rounds 5+6). Round 5 required the wallet
# present as a success key and absent as a failure key; round 6 proved
# two ways a "covering" run never actually saw the fill:
#   - DEPTH: a busy whale does 500+ venue fills between the s1 fill
#     and the run, the /trades sweep fetches only the newest `depth`
#     rows, and no future run reaches deeper — a permanent false trip
#     on a venue-visible fill. The run now records cov:<addr> with
#     either complete=true (feed exhausted) or the oldest venue ts it
#     actually reached; coverage requires that window to provably span
#     the fill's own timestamp (with a margin for venue-vs-block skew).
#   - LAG: a run STARTED seconds after detection sits inside the venue
#     data-api's indexing lag and cannot possibly contain the fill.
#     The covering run must start RECON_VENUE_LAG_S after detection.
# A run without the cov key (old format) never covers; rows defer.
# $1 is CAST (fleet round 7, CRITICAL): untyped, Postgres resolved
# '$1 + interval' as interval+interval at prepare and the statement
# could never execute — the alarm was structurally silent, which only
# a real-Postgres execution pin catches (tests/test_s1_sql_real_pg).
# complete NO LONGER waives the span (fleet round 8, major): a venue
# whose per-wallet index degrades to its newest slice ends the walk
# early with an ordinary short/empty page, records complete=true, and
# the waiver false-tripped STICKY on a correct emission. Coverage now
# always requires the walk to have provably reached at or below the
# fill's own timestamp; a phantom fill older than the whale's entire
# walked history therefore DEFERS instead of alarming — the safe
# direction, and the poller/venue-stamp path remains primary. The
# oldest cast is shape-guarded (round 8: an unguarded tombstone cast
# in SQL_TRIP wedged persists — same lesson applies here).
# A DIRTY walk never covers (fleet round 11, major): round 9 stopped an
# unusable row testifying FOR coverage, but a walk that skipped the
# fill's OWN degraded stub still claimed clean span coverage off its
# healthy neighbors and false-tripped STICKY on a correct emission.
# The reconciler now counts skipped-unusable rows into cov->dirty; a
# run is covering only when that key is absent (pre-round-11 format)
# or the number 0 — any skip, and any malformed shape, DEFERS.
# TWO independent covering runs (fleet round 20, major): rounds 17-19
# armored one walk's pagination against feed shifts, and round 20
# proved the arms race unwinnable — content identity can never prove
# positional continuity when the venue serves raw-identical rows, so
# a sufficiently aligned twin run + shift always exists that masks any
# K-row witness. The firewall is statistical instead: the verdict
# needs TWO distinct clean covering runs. New fills re-seat the feed
# between hourly walks, so the masking coincidence must recur with
# fresh, decorrelated geometry — and the failure direction is pure
# deferral (a true alarm arrives one run later; it still arrives).
# Round 38 (major): coverage tested only the LOWER bound — a frozen
# venue index's walk spanned far below a fill it could never have
# served, its byte-identical geometry defeated the round-20 two-run
# decorrelation, and the sweep STICKY-tripped a CORRECT emission.
# A covering run must testify that its NEWEST served row reached the
# fill's own timestamp — the fill sits INSIDE the walked span, both
# bounds. Fail-closed on missing 'newest' (pre-round-38 runs defer).
# Round 39 (major x3): newest is a TRADE timestamp on served rows —
# it cannot prove the fill was served. One future-dated head row
# (valid, no ascending adjacency → dirty=0) inflated newest above
# every fill for hours; a same-second bundle sibling satisfied the
# equality edge for a leg the walk never saw; ordinary publication
# lag put newest above an unserved in-flight fill. All three defeat
# the two-run rule the same way the round-38 shape did: a
# frozen/poisoned index serves BYTE-IDENTICAL geometry to both
# walks. The round-20 decorrelation premise is now explicit in the
# statement: coverage requires at least two covering runs whose
# newest testimony DIFFERS — a live feed re-seats between hourly
# walks; a feed that cannot show two different heads cannot testify
# that it would have shown this fill. Deferral is the honest cost.
# Round 42 (design round): newest is TRADE-ts testimony and can never
# observe INDEXING time, and distinct-newest proves only that two
# walks saw two DIFFERENT snapshots — not that either was FRESH. Two
# executed honest-degradation shapes forged counted coverage over
# walks that never served the fill: (a) desynced replicas frozen at
# slightly different depths just past the fill's trade-ts (the fill's
# own row still in the indexing queue at both freezes) hand two
# distinct stale heads; (b) a stale head re-served with its ts
# jittered under ORDER_TOL_S between walks slips the r41 mutant probe
# (needs > ORDER_TOL_S) and forges distinctness from ONE frozen
# snapshot. The missing witness is INDEXING-TIME evidence, and the
# feed already carries it: a served row whose TRADE time postdates
# the fill's DETECTION wall-clock proves the venue indexed activity
# that happened after the fill existed. A covering run's newest must
# therefore ALSO reach detected_at + RECON_VENUE_LAG_S — sound under
# the honest skew bound because a snapshot frozen before detection
# cannot contain a trade-ts more than FUTURE_SKEW_S (300) past its
# freeze, and RECON_VENUE_LAG_S (600) clears that with margin. The
# residual is owned honestly: a PER-ROW indexing hole/straggler under
# a live head (venue indexes the fill's neighbors but not the fill,
# for longer than the judgment window) still counts coverage — that
# absence is observationally IDENTICAL to a venue refuting a phantom,
# for any observer limited to venue data, so no rule closes it. The
# failure direction stays immediate STICKY + the r17 healer: no
# persistence window can discriminate a hole from a refuted phantom
# (waiting delays true and false alarms equally), the healer already
# auto-releases the false case the moment the straggler lands and
# stamps, and every deferred hour of a GENUINE wrong emission is live
# money copying phantom fills — over-alarm + auto-heal beats blind
# emission. Quiet wallets defer (unchanged since r38/r39: no post-
# fill activity means no inside-span, no distinct heads) — deferral,
# never a false verdict.
SQL_RECON_SINCE = """
SELECT count(DISTINCT q.nv) AS n FROM (
  SELECT (details->'per_wallet'->('cov:' || $2)->>'newest')::float8 AS nv
  FROM reconciliation_runs
  WHERE started_at > $1::timestamptz + make_interval(secs => $4)
    AND finished_at IS NOT NULL
    AND details->'per_wallet' ? $2
    AND NOT (details->'per_wallet' ? ('failed:' || $2))
    AND (CASE WHEN jsonb_typeof(
                details->'per_wallet'->('cov:' || $2)->'oldest') = 'number'
              THEN (details->'per_wallet'->('cov:' || $2)->>'oldest')::float8
              ELSE 'Infinity'::float8 END) <= $3
    AND (CASE WHEN jsonb_typeof(
                details->'per_wallet'->('cov:' || $2)->'newest') = 'number'
              THEN (details->'per_wallet'->('cov:' || $2)->>'newest')::float8
              ELSE '-Infinity'::float8 END) >= $5
    AND (CASE WHEN jsonb_typeof(
                details->'per_wallet'->('cov:' || $2)->'newest') = 'number'
              THEN (details->'per_wallet'->('cov:' || $2)->>'newest')::float8
              ELSE '-Infinity'::float8 END) >= $6
    AND (CASE
          WHEN NOT (COALESCE(details->'per_wallet'->('cov:' || $2),
                             '{}'::jsonb) ? 'dirty') THEN true
          WHEN jsonb_typeof(
                details->'per_wallet'->('cov:' || $2)->'dirty') = 'number'
          THEN (details->'per_wallet'->('cov:' || $2)->>'dirty')::float8 = 0
          ELSE false END)
  ORDER BY started_at DESC
  LIMIT 64
) q
"""
# The verdict's LAST look at the row (fleet round 15, major): the
# sweep's SQL_SWEEP snapshot read ok=false, then a covering reconcile
# run finished INSIDE the sweep's awaited gap — stamping the row's
# venue_seen_at AND becoming the very evidence SQL_RECON_SINCE found.
# Judging off the stale snapshot branded a venue-corroborated row
# uncorroborated, permanently. The covering run is FINISHED before it
# testifies (its stamps are committed), so a re-read here is
# authoritative: stamped now = confirmed, whatever the snapshot said.
SQL_RECHECK = ("SELECT (venue_seen_at IS NOT NULL) AS ok "
               "FROM trades WHERE id = $1")
SQL_READ = "SELECT value FROM ingestion_state WHERE key = $1"
# The judgment-site refresh clock (fleet round 11, minor): a refresh
# past an operator tombstone compared an APP-clock timestamp against
# the tombstone's PG now() — with the PG clock ahead, the refresh was
# refused every cycle, and each flush's tombstone merge then released
# the in-memory trip so the next sweep re-bumped the same verdict's
# counter durably, once per cycle for the life of the skew. The
# refresh timestamp now comes from the same clock the tombstone was
# written with; falling back to the app clock on error keeps the
# refused path degraded-but-alive rather than wedged.
SQL_NOW = "SELECT extract(epoch from now())::float8"
# THE FLUSH NEVER TOUCHES TRIP STATE (fleet round 6). The round-2 CAS
# guarded only the scalar 'tripped' — the OLDEST reason — so once trips
# became a dict (round 5), two processes sharing the oldest reason
# passed each other's CAS while their trip SETS diverged, and a full-
# document overwrite silently erased a concurrent process's sticky
# trip (and any operator clear committed mid-flush). Trips now live
# under server-side atomic jsonb operations only (SQL_TRIP/SQL_CLEAR);
# this write merges the payload's top-level keys, adds counter DELTAS
# server-side (concurrent flushes can no longer clobber each other's
# counts), and structurally cannot name 'trips'/'trips_cleared'. If
# the stored trips are non-empty after the merge, armed is forced
# false in the same statement.
# Hostile-shape guards (fleet round 7, minor): a stored value or a
# stored counters field that is valid JSON but not an OBJECT made
# jsonb_set/jsonb_each raise on every flush forever — a silent gauge
# blackhole, because the failure counter's own persist is what fails.
# A non-object doc is replaced by the payload (fail-visible under
# 'state_repaired'); a non-object counters field is treated as empty.
# Round 35 (major x2): contested/contested_floor rode the top-level
# || overwrite — a concurrent process that never saw the flood
# flushed floor=0/marks={} over a proven floor, and a later boot
# adopted the clobber and re-emitted a proven-contested height. Both
# fields now fold SERVER-SIDE under the row lock, exactly like the
# counter deltas: the floor takes GREATEST(stored, payload), the
# marks union (payload wins duplicate keys — both are timestamps),
# and marks at or below the folded floor are pruned in the same
# expression (the floor already covers them).
SQL_WRITE = """
INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
ON CONFLICT (key) DO UPDATE SET value = (
  SELECT CASE WHEN COALESCE(nv.v->'trips', '{}'::jsonb) <> '{}'::jsonb
              THEN jsonb_set(nv.v, '{armed}', 'false'::jsonb)
              ELSE nv.v END
  FROM (SELECT CASE
          WHEN jsonb_typeof(ingestion_state.value) <> 'object'
          THEN $2::jsonb || jsonb_build_object('state_repaired', true)
          ELSE jsonb_set(jsonb_set(jsonb_set(
          ingestion_state.value
            || ($2::jsonb - 'counters' - 'contested' - 'contested_floor'),
          '{counters}',
          (SELECT COALESCE(jsonb_object_agg(m.k, to_jsonb(m.a + m.b)),
                           '{}'::jsonb)
           FROM (SELECT COALESCE(s.k, d.k) AS k,
                        CASE WHEN jsonb_typeof(s.v) = 'number'
                             THEN (s.v)::numeric ELSE 0 END AS a,
                        CASE WHEN jsonb_typeof(d.v) = 'number'
                             THEN (d.v)::numeric ELSE 0 END AS b
                 FROM jsonb_each(CASE
                        WHEN jsonb_typeof(
                          ingestion_state.value->'counters') = 'object'
                        THEN ingestion_state.value->'counters'
                        ELSE '{}'::jsonb END) s(k, v)
                 FULL OUTER JOIN jsonb_each(COALESCE(
                        $2::jsonb->'counters', '{}'::jsonb)) d(k, v)
                   ON s.k = d.k) m)),
          '{contested_floor}',
          to_jsonb(GREATEST(
            CASE WHEN jsonb_typeof(
                   ingestion_state.value->'contested_floor') = 'number'
                 THEN (ingestion_state.value->>'contested_floor')::numeric
                 ELSE 0 END,
            CASE WHEN jsonb_typeof($2::jsonb->'contested_floor') = 'number'
                 THEN ($2::jsonb->>'contested_floor')::numeric
                 ELSE 0 END))),
          '{contested}',
          (SELECT COALESCE(jsonb_object_agg(t.k, t.v), '{}'::jsonb)
           FROM jsonb_each(
             (CASE WHEN jsonb_typeof(
                     ingestion_state.value->'contested') = 'object'
                   THEN ingestion_state.value->'contested'
                   ELSE '{}'::jsonb END)
             || (CASE WHEN jsonb_typeof($2::jsonb->'contested') = 'object'
                      THEN $2::jsonb->'contested'
                      ELSE '{}'::jsonb END)) t(k, v)
           WHERE t.k ~ '^[0-9]+$' AND (t.k)::numeric > GREATEST(
            CASE WHEN jsonb_typeof(
                   ingestion_state.value->'contested_floor') = 'number'
                 THEN (ingestion_state.value->>'contested_floor')::numeric
                 ELSE 0 END,
            CASE WHEN jsonb_typeof($2::jsonb->'contested_floor') = 'number'
                 THEN ($2::jsonb->>'contested_floor')::numeric
                 ELSE 0 END))) END AS v) nv)
"""
# A trip is durable the moment it fires: one atomic union (existing
# timestamp wins), disarming in the same statement. The WHERE refuses
# a stale re-persist of a reason an operator already cleared (the
# tombstone is newer than the trip) — the round-6 resurrection class.
# Round 7: a non-object stored doc is replaced wholesale (the trip
# must land — fail-visible via 'state_repaired'); a non-object trips
# field is treated as empty; and when the legacy 'tripped' scalar
# names THIS reason it is stripped in the same statement, completing
# the scalar's migration into the dict.
# Round 29 (minor): the statement now carries a TRANSITION signal,
# exactly as SQL_CLEAR has since round 26 — 'had' reports whether the
# reason already stood (in the trips dict OR as the legacy scalar)
# before this write, under the same row lock as the write itself.
# The s1.trip.* firing counter used to bump in _trip, guarded only by
# the in-process trips dict: every process adopting or re-judging the
# SAME standing verdict re-counted one firing, and the server-side
# delta merge made the inflation durable (N bumps for one trip across
# N processes — the round-8 count-only-what-THIS-process-transitioned
# law, violated on the firing side). The bump now lives on the
# persist, gated on had=false. 'wrote' distinguishes a landed union
# from a tombstone refusal in the same row. A missing state row
# returns 0 rows; _persist_trip then creates it via SQL_TRIP_INIT
# (ON CONFLICT DO NOTHING) and retries the union on an insert race —
# the standard upsert loop, chosen over an INSERT..ON CONFLICT CTE
# because locking (prev FOR UPDATE) and upserting one row in a single
# statement has undefined ordering between the CTEs.
SQL_TRIP = """
WITH prev AS (
  SELECT COALESCE(
           (CASE WHEN jsonb_typeof(value->'trips') = 'object'
                 THEN value->'trips' ? $2::text ELSE false END)
           OR value->>'tripped' = $2::text, false) AS had,
         (jsonb_typeof(value) <> 'object'
          OR (CASE WHEN jsonb_typeof(
                     value->'trips_cleared'->$2) = 'number'
                   THEN (value->'trips_cleared'->>$2)::float8
                   ELSE -1 END) < $3::float8) AS admit
  FROM ingestion_state WHERE key = $1 FOR UPDATE
)
UPDATE ingestion_state SET value = CASE WHEN prev.admit THEN (
  SELECT CASE WHEN nv.v->>'tripped' = $2::text
              THEN nv.v #- '{tripped}' ELSE nv.v END
  FROM (SELECT CASE
          WHEN jsonb_typeof(ingestion_state.value) <> 'object'
          THEN jsonb_build_object(
            'trips', jsonb_build_object($2::text, $3::float8),
            'armed', false, 'state_repaired', true)
          ELSE jsonb_set(
            jsonb_set(ingestion_state.value, '{trips}',
              jsonb_build_object($2::text, $3::float8)
                || CASE WHEN jsonb_typeof(
                          ingestion_state.value->'trips') = 'object'
                        THEN ingestion_state.value->'trips'
                        ELSE '{}'::jsonb END),
            '{armed}', 'false'::jsonb) END AS v) nv)
  ELSE ingestion_state.value END
FROM prev WHERE ingestion_state.key = $1
RETURNING prev.had AS had, prev.admit AS wrote
"""
# First-ever write for the state key: the row is born already tripped
# and disarmed. DO NOTHING on conflict — the caller retries the union
# above, which then sees whatever the race winner wrote.
SQL_TRIP_INIT = """
INSERT INTO ingestion_state (key, value)
VALUES ($1, jsonb_build_object(
    'trips', jsonb_build_object($2::text, $3::float8), 'armed', false))
ON CONFLICT (key) DO NOTHING
RETURNING true AS wrote
"""
# The operator clear: atomic removal of exactly one reason plus a
# PER-REASON tombstone (round 6: the single trip_cleared_* slot forgot
# every clear but the last, so a late-merging process resurrected an
# already-cleared trip). The legacy 'tripped' scalar is removed ONLY
# when it names the cleared reason (fleet round 7: the unconditional
# strip destroyed a DIFFERENT uncleared sticky trip's only durable
# record — a clear must never clear more than the reason it names).
# Non-object docs are refused (0 rows) rather than raising.
# The '#-' delete raises on an ARRAY trips field ("path element not
# an integer") — the operator's only release path 500ing forever with
# no healer (fleet round 10, minor). A non-object trips field is
# healed to '{}' inside the clear itself.
SQL_CLEAR = """
WITH prev AS (
  SELECT ((CASE WHEN jsonb_typeof(value->'trips') = 'object'
                THEN value->'trips' ? $2::text ELSE false END)
          OR value->>'tripped' = $2::text) AS had
  FROM ingestion_state WHERE key = $1 FOR UPDATE
)
UPDATE ingestion_state SET value = jsonb_set(
  (SELECT CASE WHEN x.v->>'tripped' = $2::text
               THEN x.v #- '{tripped}' ELSE x.v END
   FROM (SELECT CASE
           WHEN jsonb_typeof(ingestion_state.value->'trips') = 'object'
           THEN ingestion_state.value #- ARRAY['trips', $2::text]
           ELSE jsonb_set(ingestion_state.value, '{trips}',
                          '{}'::jsonb) END AS v) x),
  '{trips_cleared}',
  (CASE WHEN jsonb_typeof(value->'trips_cleared') = 'object'
        THEN value->'trips_cleared' ELSE '{}'::jsonb END)
    || jsonb_build_object($2::text,
         to_jsonb(extract(epoch from now())::float8)))
FROM prev
WHERE key = $1 AND jsonb_typeof(value) = 'object'
RETURNING prev.had AS removed,
          value->'trips' AS trips, value->'trips_cleared' AS cleared
"""


def _lix_key(log_entry: dict) -> str:
    """Frame-dedupe identity for one delivered log: (block, hash,
    logIndex). Round 15 (CRITICAL): index alone conflated a re-mined
    copy at a new height with the old frame whenever the venue reused
    the logIndex — a duplicate frame is the SAME log from the SAME
    block version, nothing looser."""
    lix = str(log_entry.get("logIndex", ""))
    if not lix:
        return ""
    return "%s|%s|%s" % (
        str(log_entry.get("blockNumber", "")),
        str(log_entry.get("blockHash", "")).lower(), lix)


class S1Emitter:
    def __init__(self) -> None:
        # BURN-IN BY DEFAULT (owner directive 2026-08-28): the full
        # pipeline runs against live events and counts s1.would_emit,
        # writing NOTHING — emission stays quadruple-gated behind cert
        # GREEN + the 7-day window + the persisted arm + S1_ARM. Set
        # S1_EMITTER=off to silence the emitter entirely.
        self.enabled = os.getenv("S1_EMITTER", "on").lower() not in ("off", "0", "false")
        self.listener: Any = None                    # weakref.ref
        self.http_url = ""
        # tx -> {logs, first_seen, last_seen, replay, evicted,
        #        blocks: {blk -> blockHash}, ts: {blk -> int},
        #        ts_started, v3_wait_started}
        self.pending: OrderedDict[str, dict] = OrderedDict()
        self.deltas: dict[str, int] = {}
        self.counters: dict[str, int] = {}           # last flushed view
        self.head = 0                                 # chain-head watermark
        self.armed = False
        self.armed_at = 0.0
        # STICKY TRIPS are a merged, row-specific SET (fleet round 5):
        # a single-slot cell let two live trips CAS-overwrite each
        # other and let one operator clear release BOTH processes. Each
        # reason (e.g. 'uncorroborated:1234') is cleared individually.
        self.trips: dict[str, float] = {}
        self.unjudged_backlog = 0
        self.cert_green = False
        self.cert_metrics: dict = {}
        self.cert_reason = "never_checked"
        self.cert_checked_at = 0.0
        self.last_flush_at = 0.0
        self.last_emit_at = 0.0
        self.rpc_tokens = float(RPC_PER_MIN)
        self.rpc_token_at = time.time()
        self.rpc_backoff_until = 0.0
        self.head_advanced_at = 0.0          # last WS-driven head move
        self.last_head_poll_at = 0.0
        self._ts_cache: OrderedDict[tuple[int, str], int] = OrderedDict()
        # burn-in / armed (tx, wallet, asset) marks OUTLIVE the pending
        # entry (round 6: the per-entry set died at pop, so a post-
        # finalize redelivery double-counted would_emit)
        self.counted_marks: OrderedDict[tuple, bool] = OrderedDict()
        self.contested: dict[int, float] = {}  # proven two-hash heights
        self.removed_txs: OrderedDict[str, float] = OrderedDict()
        self._suspect_wallets = 0     # r42 burst census (per sweep)
        # Heights at or below this floor abstain unconditionally
        # (fleet round 34): an evicted mark FORGOT a proven verdict —
        # PENDING_CAP overflow discarded the buffered entries before
        # the round-33 eviction pop could apply it, and a lone
        # redelivery re-earned the orphaned side off a stale replica.
        # Forgetting a specific height now WIDENS abstention instead
        # of narrowing it: eviction raises the floor to the evicted
        # height, permanently.
        self.contested_floor: int = 0
        self._unpersisted: set[str] = set()  # trips awaiting SQL_TRIP
        # DB clock anchor, learned from SQL_NOW (fleet round 12,
        # minor): trip timestamps are compared against tombstones
        # written with PG now(), so they must be stamped on PG's clock
        # in BOTH skew directions — an app-ahead trip outran every
        # tombstone (clears reported success but never released, and a
        # queued retry resurrected a cleared trip durably). Round 13:
        # the anchor pairs the PG reading with time.MONOTONIC, not the
        # wall clock — a wall-clock STEP (NTP/VM migration) landing
        # between the sweep's SQL_NOW read and a between-sweeps trip
        # was re-stamping trips into PG's future, a stamp no tombstone
        # could ever outrun and no healer could repair.
        self._db_anchor: tuple[float, float] | None = None
        self._client: httpx.AsyncClient | None = None
        self._state_loaded = False
        self._pending_arm_at: float | None = None
        self._exch_cache: tuple[tuple, set[str]] | None = None

    # ── tiny helpers ────────────────────────────────────────────────
    def bump(self, key: str, n: int = 1) -> None:
        if n:
            self.deltas[key] = self.deltas.get(key, 0) + n

    def _mark_contested(self, blk: int) -> None:
        """Record a height the buffer has PROVEN contested — two
        different hashes seen for one height (a sibling-frame
        conflict, or a resolver response whose parentHash contradicts
        a recorded hash). Fleet round 32 (major): the round-30/31
        recovery was purge-and-RE-EARN, which arbitrates the conflict
        by whichever replica answers first — an eventually-consistent
        provider re-verified the ORPHANED side and the armed path
        ingested it next to the canonical twin. Round 12's law
        (\"a second hash at one height IS the reorg verdict; the
        whole tx abstains, the poller carries whatever was real\")
        extends across sibling entries: every tx buffered at a
        contested height abstains at finalize, unconditionally.
        Bounded — and eviction APPLIES the verdict before forgetting
        it (fleet round 33, major): dropping the oldest mark while an
        entry still pended at that height downgraded a PROVEN
        contested height back to re-earnable, and a stale replica
        then emitted the orphaned side — the fleet seeded 4096+ marks
        off one fat entry to force exactly that eviction. A height's
        contested verdict is final for everything buffered there, so
        the entries die with the mark: popped and counted under
        s1.abstain.contested, the poller carrying whatever was real.
        A flood now costs the attacker mass VISIBLE deferral, never
        an emission."""
        if blk <= 0:
            return
        self.contested[blk] = time.time()
        while len(self.contested) > CONTESTED_CAP:
            old = min(self.contested)
            self.contested.pop(old, None)
            # Round 34 (major): the pop below is a no-op when
            # PENDING_CAP overflow already discarded the buffered
            # entries — a lone REDELIVERY of the orphaned frame then
            # found no mark, no sibling, and a stale replica, and the
            # armed path emitted. Eviction must never forget a proven
            # verdict: the floor rises to the evicted height and the
            # finalize gate abstains everything at or below it,
            # buffered now or redelivered later.
            self.contested_floor = max(self.contested_floor, old)
            for otx in [t for t, oe in self.pending.items()
                        if old in (oe.get("blocks") or {})]:
                self.pending.pop(otx, None)
                self.bump("s1.abstain.contested")

    def _purge_ts_cache(self, from_blk: int) -> None:
        """Reorg evidence ANYWHERE voids EVERY earned timestamp.

        Fleet round 7 dropped the suffix at or above the evidence
        height; round 8 extended that to the copies already resolved
        into pending entries. Fleet round 31 (major) killed the
        remaining assumption: evidence AT height C only proves the
        fork point is AT OR BELOW C — a sibling entry whose fill sat
        at B < C kept its strictly-earned old-chain timestamp, passed
        every post-await self-comparison, and the armed path ingested
        a fill existing only on the orphaned chain (executed repro,
        both the conflict-at-C+1 shape and the discarded-parentHash
        shape). The fork point is never delivered, so nothing below
        the evidence height is provably safe: every cached timestamp
        and every entry-local copy is dropped, and re-resolution
        re-earns each height against the recorded hash — the strict
        resolver refuses whatever the live chain has rewritten and
        re-confirms what it has not. Pure deferral, RPC-bounded by
        the existing token bucket. `from_blk` remains the evidence
        height for the caller's log/counter context only."""
        self._ts_cache.clear()
        for e in self.pending.values():
            had = bool(e.get("ts"))
            e["ts"] = {}
            e["ts_src"] = {}
            # REORG GENERATION (fleet round 11): the resolution loop's
            # post-await write-back guard compares the earned-against
            # hash to the entry's current one — but a NEW-height
            # re-mine and a sibling's removed notice void timestamps
            # WITHOUT changing this entry's buffered hash at the old
            # height, so the hash compare alone would let the write-
            # back silently restore a voided, pre-reorg-earned ts.
            # Any purge advances the generation of every entry that
            # holds (or could be mid-earning) a timestamp; a write-
            # back is valid only when the generation it captured
            # before the await is still current. Round 31: gating the
            # bump on blocks >= from_blk left a lower-height entry's
            # in-flight write-back valid across the purge, so the
            # bump is now unconditional for entries with any buffered
            # block — the write-back race does not care where the
            # evidence height sat, and `had` alone cannot see a ts
            # still awaited.
            if had or e.get("blocks"):
                e["reorg_gen"] = e.get("reorg_gen", 0) + 1

    def _mark_counted(self, tx: str, wallet: str, asset: str) -> bool:
        """True the first time a (tx, wallet, asset) is handled. Lives
        OUTSIDE the pending entry (round 6): the per-entry set died at
        pop, so a post-finalize redelivery — deep-reorg re-add or a
        late duplicate frame — re-counted would_emit for the same fill
        and the burn-in gauge diverged from what armed would do."""
        k = (tx, wallet, asset)
        if k in self.counted_marks:
            self.counted_marks.move_to_end(k)
            return False
        self.counted_marks[k] = True
        while len(self.counted_marks) > COUNTED_CAP:
            self.counted_marks.popitem(last=False)
        return True

    def exchange_set(self, listener: Any) -> set[str]:
        addrs = getattr(listener, "_addresses", None) or []
        key = tuple(addrs)
        if self._exch_cache and self._exch_cache[0] == key:
            return self._exch_cache[1]
        s = {str(a).lower() for a in addrs}
        self._exch_cache = (key, s)
        return s

    # ── observe (sync, zero I/O, never raises) ──────────────────────
    def observe(self, listener: Any, log_entry: dict[str, Any]) -> None:
        try:
            if not self.enabled:
                return
            if bool(getattr(listener, "_shadow_replay", False)):
                # rule 7: replayed logs never emit — cheapest at the door
                self.bump("s1.abstain.replay")
                return
            if log_entry.get("removed"):
                tx0 = str(log_entry.get("transactionHash", "")).lower()
                if tx0 and self.pending.pop(tx0, None) is not None:
                    self.bump("s1.abstain.reorged")
                # ROUND 39 (CRITICAL): the pop FORGOT the verdict — a
                # lagging WS backend redelivered the orphaned frame
                # after the pop, it re-buffered into a fresh entry
                # with no mark and no sibling, and a stale replica
                # re-earned it into an armed key-divergent ingest of
                # a fill existing only on the orphaned chain. A
                # removed notice is a PROVEN orphan verdict for the
                # tx it names: the tx is remembered (bounded, oldest
                # out first) and a redelivered frame refuses to
                # re-buffer; a deep-reorg re-add defers to the poller
                # ("when in doubt, don't emit"). The height, when it
                # parses, is contested ground too — durable via the
                # round-34 floor persistence, covering sibling
                # entries and post-restart redelivery. Residual,
                # stated honestly: a BLOCKLESS removed notice followed
                # by a restart forgets the tx mark and marks no
                # height; the corroboration sweep backstops there.
                if tx0:
                    self.removed_txs[tx0] = time.time()
                    self.removed_txs.move_to_end(tx0)
                    while len(self.removed_txs) > REMOVED_TX_CAP:
                        self.removed_txs.popitem(last=False)
                # a removed notice rewrites the chain suffix — every
                # cached block timestamp at or above it is now suspect
                # (fleet round 7: a sibling tx borrowing a stale
                # (blk, oldhash) entry skipped the reorg check).
                # Round 32 (major): the purge was gated `if rblk:` —
                # a notice with an ABSENT or null blockNumber (parsed
                # to 0) popped the named tx as reorg evidence on the
                # line above yet skipped the purge entirely, and a
                # sibling's old-chain timestamp armed-ingested an
                # orphaned fill. The round-31 purge is height-
                # agnostic, so the height's parseability cannot gate
                # whether evidence counts: ANY removed notice purges.
                try:
                    rblk = int(str(log_entry.get("blockNumber", "0x0")), 16)
                except (TypeError, ValueError):
                    rblk = 0
                if rblk:
                    self._mark_contested(rblk)
                self._purge_ts_cache(rblk)
                return
            tx = str(log_entry.get("transactionHash", "")).lower()
            if not tx:
                return
            if tx in self.removed_txs:
                # round 39 (CRITICAL): a frame for a tx the venue has
                # REMOVED is a replay of a proven orphan — it must
                # never re-buffer toward emission. The poller carries
                # whatever a deep re-org later made real.
                self.bump("s1.abstain.removed_replay")
                return
            blk = int(str(log_entry.get("blockNumber", "0x0")), 16)
            bh = str(log_entry.get("blockHash", "")).lower()
            if not blk or not bh:
                # no block number = no timestamp = no key ever; no block
                # hash = no reorg check — either way this log can never
                # emit safely, and buffering it would crash or blind the
                # finalize path (fleet r1: wedged-loop + silent-reorg)
                self.bump("s1.abstain.no_block")
                return
            now = time.time()
            if blk > self.head:
                self.head = blk
                self.head_advanced_at = now   # the WS is our head feed
            # SIBLING-FRAME HASH CONFLICT IS REORG EVIDENCE (fleet
            # round 30, major): two hashes at one height cannot both
            # be canonical, and the emitter's own round-15 law says
            # reorg evidence is recorded the moment it is seen — but
            # this frame was filed only under its OWN tx entry, so a
            # SIBLING entry's strictly-earned timestamp at the same
            # height survived untouched, every post-await guard
            # compared that entry to itself, and the armed path
            # ingested a fill that exists only on the orphaned chain
            # (~75 min before the corroboration sweep could trip).
            # The removed channel already treats the identical
            # information as a purge; the frame channel now does too:
            # the suffix is voided and strict re-resolution refuses
            # whichever side the live chain refutes. Pure deferral —
            # the canonical side re-earns and proceeds.
            for sib_tx, sib in self.pending.items():
                sh = sib.get("blocks", {}).get(blk)
                if sib_tx != tx and sh is not None and sh != bh:
                    self.bump("s1.sibling_hash_conflict")
                    # Round 32 (major): purge-and-RE-EARN arbitrated
                    # a PROVEN two-hash height by whichever replica
                    # answered first — a stale one re-verified the
                    # orphaned side and the armed path ingested it.
                    # Round 12's law extends across entries: a height
                    # the buffer proves contested is never decided by
                    # a single RPC response. Both sides abstain at
                    # finalize; the poller carries whatever was real.
                    self._mark_contested(blk)
                    self._purge_ts_cache(blk)
                    break
            e = self.pending.get(tx)
            if e is None:
                e = {"logs": [], "first_seen": now, "last_seen": now,
                     "evicted": False, "blocks": {}, "ts": {},
                     "ts_started": None, "v3_wait_started": None}
                self.pending[tx] = e
                while len(self.pending) > PENDING_CAP:
                    _k, victim = self.pending.popitem(last=False)
                    victim["evicted"] = True
                    self.bump("s1.abstain.overflow")
            if e["blocks"] and blk not in e["blocks"]:
                # ONE tx lives in ONE canonical block — a second block
                # number for the same tx is reorg evidence in itself
                # (fleet round 9: a re-mined fill joined the still-
                # pending entry and the strictly-earned old timestamp
                # was never re-verified). Every earned timestamp is
                # void — and the entry now holds two heights, which
                # _finalize_tx abstains outright (round 12: trusting
                # the old block's re-resolution to fail its hash check
                # broke under an eventually-consistent provider that
                # verified each height against a different chain).
                e["ts"] = {}
                e["ts_src"] = {}
                self._purge_ts_cache(min(list(e["blocks"]) + [blk]))
                # RECORD THE SECOND HEIGHT NOW (fleet round 15,
                # CRITICAL): the lix dup check below returned early
                # for a re-mined copy that reused the buffered
                # logIndex, so the new height never landed in
                # e['blocks'] — the entry stayed single-block, the
                # round-12 two-heights gate never fired, and a stale
                # replica re-verified the orphaned block and emitted
                # it. Reorg evidence is recorded the moment it is
                # seen; no later dedupe may swallow it.
                # …BUT NOT AN UNBOUNDED NUMBER OF THEM (fleet round
                # 33, major): the verdict seals at the SECOND height
                # (multi-height abstains at finalize), yet the dict
                # kept growing — one fat tx served frames at
                # thousands of heights and became the amplifier that
                # flooded the contested registry into evicting a
                # live mark. The purge above already banked this
                # frame's evidence value; past the cap the frame
                # itself is dropped, visibly.
                if len(e["blocks"]) >= BLOCKS_PER_TX_CAP:
                    self.bump("s1.frames_capped")
                    return
                e["blocks"][blk] = bh
            elif e["blocks"].get(blk) not in (None, bh):
                # SAME-height re-mine (fleet round 10): a new hash for
                # a known block number is the same reorg evidence — the
                # earned timestamps from this height up are void.
                # Round 37 (major): purge-and-RE-EARN here was the
                # exact arbitration round 32 declared unsound for the
                # sibling channel — an A→B→A flap re-flipped this
                # entry's belief back to the orphaned hash, the buffer
                # had literally observed BOTH hashes at the height,
                # and a lagging replica re-verified the orphaned side
                # into an armed ingest. Two hashes at one height are
                # contested WHICHEVER entry delivered them: the height
                # is marked, both sides abstain at finalize, the
                # poller carries.
                self._mark_contested(blk)
                self._purge_ts_cache(blk)
                # THE ORPHANED VERSION'S LOGS ARE VOID TOO (fleet round
                # 11, CRITICAL): voiding only the timestamps left the
                # old block-version's log in the buffer, where a re-
                # mined copy with a shifted logIndex slipped the lix-
                # only dup check and the single (blk, new-hash)
                # resolution vouched for BOTH — the armed path emitted
                # the canonical fill AND a phantom that exists only on
                # the orphaned chain (key-divergent double emission).
                # Only logs carrying the new canonical hash survive
                # this height; lix_seen rebuilds from the survivors so
                # the re-mined copy is admitted whatever its new index,
                # and the decode cache is invalidated with the buffer.
                kept = [l for l in e["logs"]
                        if int(str(l.get("blockNumber", "0x0")), 16)
                        != blk
                        or str(l.get("blockHash", "")).lower() == bh]
                if len(kept) != len(e["logs"]):
                    e["logs"] = kept
                    e["lix_seen"] = {_lix_key(l) for l in kept
                                     if _lix_key(l)}
                    e.pop("recs", None)
                    e.pop("recs_n", None)
            # the dup check dedupes DUPLICATED FRAMES of one log —
            # same block, same hash, same index (fleet r5). Round 15
            # (CRITICAL): keyed by index alone, it also swallowed a
            # RE-MINED copy at a NEW height that happened to reuse the
            # buffered logIndex, returning before the second height
            # was recorded — the round-12 gate never fired and the
            # orphaned block emitted. A different (block, hash) is
            # never a duplicate frame.
            lk = _lix_key(log_entry)
            if lk and lk in e.setdefault("lix_seen", set()):
                self.bump("s1.dup_event")
                return
            if lk:
                e["lix_seen"].add(lk)
            e["logs"].append(log_entry)
            e["last_seen"] = now
            e["blocks"][blk] = bh
        except Exception:  # noqa: BLE001 — the wall
            try:
                self.deltas["s1.errors"] = self.deltas.get("s1.errors", 0) + 1
            except Exception:  # noqa: BLE001
                pass

    # ── strict ts resolver (never wall-clock; reorg check included) ─
    async def _resolve_block(self, blk: int, want_hash: str) -> int | str | None:
        """int ts on success; 'reorged' on hash mismatch; None = retry."""
        self.bump("s1.rpc_calls")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=4)
            resp = await self._client.post(self.http_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
                "params": [hex(blk), False]})
            if resp.status_code == 429 or resp.status_code >= 500:
                self.rpc_backoff_until = time.time() + RPC_BACKOFF_S
                self.bump("s1.rpc_backoffs")
                return None
            result = (resp.json() or {}).get("result") or {}
            raw = result.get("timestamp")
            if raw is None:
                self.bump("s1.rpc_empty")
                return None
            got_hash = str(result.get("hash", "")).lower()
            if want_hash and got_hash and got_hash != want_hash:
                return "reorged"
            # PARENT-HASH EVIDENCE (fleet round 32, major): a
            # SUCCESSFUL resolution's response body can carry the
            # ONLY delivered proof of a reorg — parentHash naming a
            # different hash for blk-1 than a sibling entry (or the
            # cache) has recorded. It was discarded, no purge channel
            # ever fired, and the sibling's old-chain fill armed-
            # ingested. The proof is a two-hash height: mark it
            # contested (both sides abstain; the poller carries) and
            # void every earned timestamp. This resolution's own ts
            # is still returned — the caller's generation guard
            # refuses the write-back after our purge, so it re-earns
            # on a later pass if its height stays uncontested.
            ph = str(result.get("parentHash", "")).lower()
            if ph and blk > 1:
                prev = blk - 1
                recorded = {k[1] for k in self._ts_cache
                            if k[0] == prev}
                for e2 in self.pending.values():
                    h2 = (e2.get("blocks") or {}).get(prev)
                    if h2:
                        recorded.add(h2)
                if any(h != ph for h in recorded):
                    self.bump("s1.parent_hash_conflict")
                    self._mark_contested(prev)
                    self._purge_ts_cache(prev)
            return int(str(raw), 16)
        except Exception:  # noqa: BLE001
            self.bump("s1.rpc_errors")
            return None

    async def _poll_head(self) -> None:
        self.bump("s1.head_polls")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=4)
            resp = await self._client.post(self.http_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber",
                "params": []})
            raw = (resp.json() or {}).get("result")
            if raw is not None:
                blk = int(str(raw), 16)
                if blk > self.head:
                    self.head = blk
        except Exception:  # noqa: BLE001
            self.bump("s1.rpc_errors")

    def _take_token(self) -> bool:
        now = time.time()
        if now < self.rpc_backoff_until:
            return False
        self.rpc_tokens = min(
            float(RPC_PER_MIN),
            self.rpc_tokens + (now - self.rpc_token_at) * RPC_PER_MIN / 60.0)
        self.rpc_token_at = now
        if self.rpc_tokens < 1:
            return False
        self.rpc_tokens -= 1
        return True

    # ── persisted state (read-only load; fail-closed until loaded) ──
    async def _load_state(self, pool: Any, now: float) -> None:
        """Adopt the persisted trip/arm BEFORE anything can certify or
        emit. Until this succeeds the emitter stays disarmed with
        cert_reason='state_unloaded' — a restarted sticky-tripped
        process must never arm in the window before its first flush
        (fleet round 2, confirmed major x3)."""
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
        except Exception:  # noqa: BLE001 — stay unloaded, stay closed
            self.bump("s1.errors")
            return
        try:
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
            lt = doc.get("trips")
            tc = doc.get("trips_cleared")
            cleared = tc if isinstance(tc, dict) else {}
            adopted: dict[str, float] = {}
            if isinstance(lt, dict):
                for k, v in lt.items():
                    at = v if isinstance(v, (int, float)) else 0.0
                    ca = cleared.get(str(k))
                    if isinstance(ca, (int, float)) and ca > at:
                        continue
                    adopted[str(k)] = at
            # The legacy scalar folds IN, it never merely falls back
            # (fleet round 7): the first round-6 trip created the dict
            # and silently shadowed an uncleared pre-round-6 sticky
            # trip out of existence. A live scalar is adopted alongside
            # the dict — tombstone-aware — and queued for durable
            # migration into the dict via _persist_trip.
            legacy = doc.get("tripped")
            if legacy:
                reason = self._trip_str(legacy)
                ca = cleared.get(reason)
                if reason not in adopted and not (
                        isinstance(ca, (int, float)) and ca > 0.0):
                    adopted[reason] = 0.0
                    self._unpersisted.add(reason)
            if adopted:
                self.trips = adopted
                self.armed = False
            elif doc.get("armed"):
                aa = doc.get("armed_at")
                self._pending_arm_at = (float(aa) if isinstance(
                    aa, (int, float)) else now)
            c = doc.get("counters")
            self.counters = c if isinstance(c, dict) else {}
            # round 34: contested verdicts and the eviction floor are
            # adopted before anything can finalize — a restart must
            # not downgrade a proven two-hash height to re-earnable
            cf = doc.get("contested_floor")
            if isinstance(cf, (int, float)) and cf > self.contested_floor:
                self.contested_floor = int(cf)
            cm = doc.get("contested")
            if isinstance(cm, dict):
                for k, v in cm.items():
                    try:
                        h = int(k)
                    except (TypeError, ValueError):
                        continue
                    if h > 0:
                        self.contested[h] = (v if isinstance(
                            v, (int, float)) else 0.0)
                while len(self.contested) > CONTESTED_CAP:
                    old = min(self.contested)
                    self.contested.pop(old, None)
                    self.contested_floor = max(self.contested_floor,
                                               old)
        except Exception:  # noqa: BLE001 — a corrupt row must not wedge
            # the loop; fail-closed with nothing adopted (round 3)
            self.bump("s1.state_corrupt")
            self.trips.setdefault("state_corrupt", 0.0)
        self._state_loaded = True

    # ── certification ───────────────────────────────────────────────
    def _judge_cert(self, doc: dict | None, now: float) -> tuple[bool, str]:
        # CERT METRICS (owner ask 2026-08-28: "can we get S1 live
        # right now?"): the reason string alone could not answer WHEN
        # — every judged quantity is now stashed for the status
        # surface, so the ops row and the probe print the window age,
        # the floors' progress, and the exact epoch the gate goes
        # green if the window stays clean. Observability only: the
        # judgment order and every bar are byte-identical.
        m: dict = {"window_needs_d": round(CERT_WINDOW_S / 86400.0, 1)}
        self.cert_metrics = m
        if not isinstance(doc, dict):
            return False, "no_shadow_state"
        ws, hs = doc.get("window_start"), doc.get("health_start")
        if isinstance(ws, (int, float)):
            m["window_age_d"] = round((now - ws) / 86400.0, 2)
            m["green_at_epoch"] = round(ws + CERT_WINDOW_S)
        if isinstance(hs, (int, float)):
            m["health_age_d"] = round((now - hs) / 86400.0, 2)
        c = doc.get("counters") or {}
        aw = doc.get("at_window") or {}

        def _delta(key: str) -> int:
            cv, wv = c.get(key), aw.get(key)
            cv = cv if isinstance(cv, (int, float)) else 0
            wv = wv if isinstance(wv, (int, float)) else 0
            return int(cv - wv)

        m["ven_suppressed"] = _delta("sim_ven_suppressed")
        m["ven_floor"] = CERT_MIN_VEN
        m["agg_decoded"] = _delta("decoded_agg")
        m["agg_floor"] = CERT_MIN_AGG
        if not isinstance(ws, (int, float)) or now - ws < CERT_WINDOW_S:
            return False, "window_young"
        if not isinstance(hs, (int, float)) or now - hs < CERT_HEALTH_S:
            return False, "health_young"
        if doc.get("decoder_fp") != DECODER_FP:
            # the emitter refuses to run a decode the window did not
            # certify — version skew in either direction is RED
            return False, "decoder_fp_mismatch"
        if m["ven_suppressed"] < CERT_MIN_VEN:
            return False, "volume_floor_ven"
        if m["agg_decoded"] < CERT_MIN_AGG:
            return False, "volume_floor_agg"
        return True, "green"

    async def _check_cert(self, pool: Any, now: float) -> None:
        self.cert_checked_at = now
        if not self._state_loaded:
            self.cert_green = False
            self.cert_reason = "state_unloaded"
            return
        try:
            raw = await pool.fetchval(SQL_READ, SHADOW_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else None)
        except Exception:  # noqa: BLE001
            # a transient READ failure is not evidence of anything: it
            # must not consume a pending arm, disarm a live one, or
            # fabricate trip counters (fleet round 2). Emission is
            # fail-closed anyway via cert_green.
            self.bump("s1.errors")
            self.cert_green = False
            self.cert_reason = "cert_read_failed"
            return
        green, reason = self._judge_cert(doc, now)
        self.cert_green, self.cert_reason = green, reason
        pend = getattr(self, "_pending_arm_at", None)
        if pend is not None:
            # a persisted arm survives a restart only if the window it
            # was granted under is still standing
            self._pending_arm_at = None
            ws = (doc or {}).get("window_start")
            if green and not self.trips and \
                    isinstance(ws, (int, float)) and ws <= pend:
                self.armed = True
                self.armed_at = pend
            else:
                self.bump("s1.trip.window_reset")
                log.warning("S1 persisted arm NOT adopted: %s",
                            reason if not green else "window_reset_while_down")
        if self.armed:
            ws = (doc or {}).get("window_start")
            if not green or (isinstance(ws, (int, float)) and ws > self.armed_at):
                # ratchet: the evidence moved out from under the arm
                self.armed = False
                self.bump("s1.trip.window_reset")
                log.warning("S1 auto-disarmed: %s", reason if not green
                            else "window_reset_after_arm")
        elif (green and not self.trips
              and os.getenv("S1_ARM", "").lower() in ("on", "1", "true")):
            self.armed = True
            self.armed_at = now
            log.warning("S1 ARMED at %s (cert green)", now)

    @property
    def tripped(self) -> str | None:
        """First live trip reason, oldest first (beat/back-compat)."""
        if not self.trips:
            return None
        return min(self.trips, key=self.trips.__getitem__)

    def _trip(self, reason: str) -> None:
        self.armed = False
        if reason in self.trips:
            return                    # idempotent: a re-judged row must
                                      # not double-bump (round 6)
        self.trips[reason] = self._now_db()   # tombstone clock (r12)
        # round 29: the s1.trip.* firing bump moved to _persist_trip,
        # gated on the statement's transition signal — the in-process
        # guard above could not see a sibling process's standing trip,
        # so every process counted the same firing once each.
        log.error("S1 STICKY TRIP: %s — manual clear of THIS reason "
                  "required", reason)

    async def _db_now(self, pool: Any) -> float:
        """The tombstone clock. A judgment-site refresh must outrun a
        tombstone written with PG's now() — refreshing from the app
        clock loses forever when PG runs ahead (fleet round 11). Every
        successful read also refreshes the learned offset that lets
        the SYNCHRONOUS trip path stamp PG's clock (round 12)."""
        try:
            v = await pool.fetchval(SQL_NOW, timeout=6)
            if isinstance(v, (int, float)):
                self._db_anchor = (float(v), time.monotonic())
                return float(v)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
        return time.time()

    def _now_db(self) -> float:
        """Best-known PG-clock 'now' without an await — the last
        SQL_NOW reading plus MONOTONIC elapsed time since it (round
        12: trips stamped on the raw app clock outran PG tombstones
        under app-ahead skew; round 13: monotonic elapsed makes the
        stamp immune to wall-clock steps after the anchor is learned).
        Before the first successful read this degrades to the app
        clock — the anchor lands with the first sweep."""
        if self._db_anchor is not None:
            db0, m0 = self._db_anchor
            return db0 + (time.monotonic() - m0)
        return time.time()

    async def _persist_trip(self, pool: Any, reason: str) -> str:
        """One atomic server-side union — durable the moment the trip
        fires, never carried by the flush (round 6). Returns one of
        three OUTCOMES (round 10: collapsing them let a sweep stamp a
        re-judged row while SQL_TRIP's tombstone WHERE had refused the
        write — an uncorroborated verdict with no trip anywhere):
        'landed'  — the trip is durable;
        'refused' — the tombstone is newer than this trip's timestamp,
                    a DECISION, not a fault (a judgment site holding
                    fresh evidence may refresh and re-persist; the
                    migration retry must NOT — that would resurrect);
        'error'   — transient; queued for retry at every flush."""
        # `or` would rewrite a falsy 0.0 — the legacy-scalar migration
        # timestamp — to now(), which outruns any tombstone and durably
        # resurrects an operator-cleared trip (fleet round 9, major)
        at = self.trips.get(reason)
        if not isinstance(at, (int, float)):
            at = self._now_db()
        try:
            row = await pool.fetchrow(SQL_TRIP, STATE_KEY, reason,
                                      float(at), timeout=6)
            if row is None:
                # no state row yet: create it born-tripped; on an
                # insert race the union retry sees the winner's doc
                row = await pool.fetchrow(SQL_TRIP_INIT, STATE_KEY,
                                          reason, float(at), timeout=6)
                if row is None:
                    row = await pool.fetchrow(SQL_TRIP, STATE_KEY,
                                              reason, float(at),
                                              timeout=6)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            self._unpersisted.add(reason)
            return "error"
        self._unpersisted.discard(reason)
        if row is None or not row.get("wrote"):
            return "refused"           # no write: the tombstone won
        if not row.get("had"):
            # round 29: the firing counter counts only the transition
            # THIS write made durable — a reason already standing on
            # disk (another process's trip, an adopted copy, a legacy
            # scalar migration) is one firing, already counted by the
            # process that landed it.
            self.bump("s1.trip." + reason.split(":")[0])
        return "landed"

    # ── finalize one tx ─────────────────────────────────────────────
    async def _finalize_tx(self, pool: Any, tx: str, e: dict,
                           now: float) -> bool:
        """True = done with this tx (emitted/abstained); False = retry."""
        lst = self.listener() if self.listener else None
        if lst is None:
            return False
        if e["evicted"]:
            return True                       # counted at eviction time
        if len(e.get("blocks", {})) > 1:
            # ONE tx lives in ONE canonical block (round 9). Round 12
            # (CRITICAL): keeping both heights and trusting per-block
            # strict re-resolution let an eventually-consistent HTTP
            # provider verify the OLD block against the old chain on
            # one finalize pass and the NEW block against the new
            # chain on the next — two strictly-earned, hash-checked
            # timestamps from OPPOSITE sides of the reorg in one
            # entry, and the armed path emitted a key-divergent twin
            # of one fill (per-block checks earned at different times
            # do not describe one chain). A second height in the
            # buffer IS the reorg verdict: the whole tx abstains here,
            # unconditionally, and the poller carries whatever was
            # real.
            self.bump("s1.abstain.reorged")
            self._purge_ts_cache(min(e["blocks"]))
            return True
        if any(b in self.contested for b in e.get("blocks", {})):
            # A CONTESTED HEIGHT NEVER EMITS (fleet round 32, major):
            # the buffer has proven two hashes at this height — the
            # round-12 verdict, seen across sibling entries — and a
            # single replica's answer must not arbitrate which side
            # was real (an eventually-consistent provider re-verified
            # the ORPHANED side and the armed path ingested it, next
            # to the canonical twin). Every tx buffered at the height
            # abstains, canonical side included; the poller carries
            # whatever was real.
            self.bump("s1.abstain.contested")
            return True
        if self.contested_floor and any(
                b <= self.contested_floor for b in e.get("blocks", {})):
            # BELOW THE FLOOR IS FORGOTTEN CONTESTED GROUND (fleet
            # round 34, major): a mark evicted from the bounded
            # registry can no longer prove its height clean, and the
            # one executed counterexample was an armed orphaned-chain
            # ingest off a redelivered frame. Everything at or below
            # the floor abstains; the poller carries.
            self.bump("s1.abstain.contested_floor")
            return True
        # DECODE FIRST — it is pure and free (round 6): the buffer is
        # overwhelmingly foreign txs (the WS subscribes by exchange
        # address, not wallet), and resolving their timestamps before
        # the decode discarded them starved the RPC budget 30-to-1 at
        # observed production rates. A foreign tx now costs zero RPC.
        if e.get("recs_n") != len(e["logs"]):
            roster = getattr(lst, "_roster", {}) or {}
            exch = self.exchange_set(lst)
            recs: list[dict] = []
            for le in e["logs"]:
                got, reason, _inv = decode_shadow_views(le, roster, exch)
                if reason is not None:
                    self.bump("s1.abstain.decode_refusal")
                    return True               # one bad event = whole tx
                tps = le.get("topics") or []
                if len(tps) >= 4 and str(tps[2]).lower()[-40:] == \
                        str(tps[3]).lower()[-40:]:
                    self.bump("s1.abstain.self_trade")
                    return True
                for r in got:
                    if not r["block"]:
                        self.bump("s1.abstain.decode_refusal")
                        return True
                    recs.append(r)
            e["recs"], e["recs_n"] = recs, len(e["logs"])
        if not e["recs"]:
            return True                       # foreign tx — zero RPC
        # ts resolution for every block the tx touched (usually one);
        # the block->ts cache means N roster txs in one block cost one
        # RPC, and the cache key includes the blockHash so a reorged
        # sibling can never borrow a stale timestamp
        if e["ts_started"] is None:
            e["ts_started"] = now
        for blk, want_hash in list(e["blocks"].items()):
            if blk in e["ts"]:
                continue
            cached = self._ts_cache.get((blk, want_hash))
            if cached is not None:
                # a cached ts skipped the LIVE canonical-hash check, so
                # the entry is marked: burn-in may use it freely, the
                # ARMED path refuses it structurally (fleet round 7 —
                # a sibling tx borrowing a stale entry after a deep
                # reorg would emit a fill from an orphaned block)
                e["ts"][blk] = cached
                e.setdefault("ts_src", {})[blk] = want_hash
                e["ts_cached"] = True
                continue
            if not self._take_token():
                break
            gen = e.get("reorg_gen", 0)
            got = await self._resolve_block(blk, want_hash)
            if got == "reorged":
                self.bump("s1.abstain.reorged")
                self._purge_ts_cache(blk)
                return True
            if got is None:
                break
            if (e.get("reorg_gen", 0) != gen
                    or e["blocks"].get(blk) != want_hash):
                # the await interleaved reorg evidence on this loop —
                # a same-height re-mine (hash moved), a new-height
                # re-mine, or a sibling's removed notice (generation
                # advanced without moving THIS height's hash): this
                # iteration still holds the PRE-await snapshot, and
                # writing ts back would silently undo observe()'s
                # purge — the armed path then emits with the orphaned
                # block's timestamp (fleet round 11, major). The earn
                # is void; the block stays unresolved and re-earns
                # against the CURRENT chain on the next pass.
                continue
            e["ts"][blk] = got
            e.setdefault("ts_src", {})[blk] = want_hash
            self._ts_cache[(blk, want_hash)] = got
            while len(self._ts_cache) > TS_CACHE_CAP:
                self._ts_cache.popitem(last=False)
        unresolved = [b for b in e["blocks"] if b not in e["ts"]]
        if unresolved:
            if now - e["ts_started"] > TS_BUDGET_S:
                self.bump("s1.abstain.ts_unresolved")
                return True
            return False
        if not e["ts"]:
            # cannot happen while observe refuses block-less logs, but a
            # crash here wedges the whole run loop — guard, never trust
            self.bump("s1.abstain.no_block")
            return True
        # the awaits above can interleave a WS reorg burst on the same
        # loop: a removed notice pops the entry, and continuing with
        # the raw dict would emit an orphaned-block fill (fleet r8) —
        # membership is re-checked after every awaited section
        if self.pending.get(tx) is not e:
            return True                # the removed branch counted it
        # freshness on BLOCK time, never first_seen
        newest_ts = max(e["ts"].values())
        if now - newest_ts > EMIT_MAX_AGE_S:
            self.bump("s1.abstain.too_old")
            return True
        for r in e["recs"]:
            r["ts"] = e["ts"].get(r["block"])
        groups = classify_mints(e["recs"])
        emitted_any = False
        for wallet, g in groups.items():
            if wallet in e.setdefault("done_wallets", set()):
                continue                      # a retry never re-runs a
                                              # completed group (fleet r2:
                                              # straggler re-representation)
            done = await self._emit_group(pool, tx, wallet, g, e, now)
            if done is None:
                return False                  # v3 outcome pending — retry
            e["done_wallets"].add(wallet)
            emitted_any = emitted_any or done
        return True

    async def _emit_group(self, pool: Any, tx: str, wallet: str,
                          g: dict, e: dict, now: float) -> bool | None:
        """True/False = group finished (emitted at least one / none);
        None = retry the tx later (v3 outcome pending)."""
        flags = dict(g.get("flags") or {})
        # bookkeeping flags, not verdicts: agg_missing marks the
        # counter-stays-raw arm (the counter-only rule below names that
        # abstention properly), mint_transformed marks a transform the
        # tie-out branch still has to PROVE. Everything else abstains.
        flags.pop("agg_missing", None)
        flags.pop("mint_transformed", None)
        if g.get("dropped") or flags:
            self.bump("s1.abstain.flags")
            return False
        aggs, execs = g.get("aggs") or [], g.get("execs") or []
        if any(r.get("mint_unresolved") for r in execs):
            self.bump("s1.abstain.mint_unresolved")
            return False
        owners = [r for r in execs if r["view"] == "exec_owner"]
        if not aggs and not owners:
            # counter-only: the wallet's own event was lost — emitting
            # the complement leg buys the wrong outcome (panel kill 1)
            self.bump("s1.abstain.counter_only")
            return False
        tie = agg_tieout(g)
        if aggs:
            legs = [r for r in execs
                    if r["view"] in ("exec_counter", "exec_mint")
                    and r["asset"] == aggs[0]["asset"]]
            if len(aggs) > 1:
                self.bump("s1.abstain.flags")
                return False
            if legs and tie != "ok":
                self.bump("s1.abstain.tieout_fail")
                return False
            if not legs and tie == "fail":
                self.bump("s1.abstain.tieout_fail")
                return False
        # THE EMIT SET: aggs, plus exec_owner records for markets
        # without an agg view. exec_counter/exec_mint are never rows.
        agg_assets = {a["asset"] for a in aggs}
        emit_recs = list(aggs) + [r for r in owners
                                  if r["asset"] not in agg_assets]
        if not emit_recs:
            self.bump("s1.abstain.counter_only")
            return False
        # v3 collision protocol
        cl = _claim_get(tx, wallet)
        if cl is not None and cl["owner"] == "receipt":
            if not cl["done"]:
                if e["v3_wait_started"] is None:
                    e["v3_wait_started"] = now
                if now - e["v3_wait_started"] < V3_WAIT_S:
                    return None               # wait a tick
                self.bump("s1.abstain.v3_unknown")
                return False
            if cl["outcome"] == "ingested":
                self.bump("s1.abstain.v3_ingested")
                return False
            # outcome 'refused': the class is ours — proceed
        roster = (self.listener() and self.listener()._roster) or {}
        whale = roster.get(wallet)
        if whale is None:
            self.bump("s1.abstain.decode_refusal")
            return False
        emitted = False
        for rec in emit_recs:
            keys = rec_keys(rec)
            if len(keys) > 1:
                # the venue might store the HALF_UP price; a one-key
                # emit can diverge from the poll row's key (panel kill 2)
                self.bump("s1.abstain.price_variant")
                continue
            if not self.armed or not self.cert_green or self.trips:
                # burn-in: idempotent per (tx, wallet, asset) — no view
                # in the mark (a straggler can flip the representative
                # record's view across a retry), the armed path marks
                # too (a disarm mid-retry cannot re-count an emitted
                # fill, fleet r2), and the mark OUTLIVES the entry so a
                # post-finalize redelivery cannot re-count (fleet r6)
                if self._mark_counted(tx, wallet, rec["asset"]):
                    self.bump("s1.would_emit")
                emitted = True                # burn-in counts as handled
                continue
            if e.get("ts_cached"):
                # ARMED emission never trusts a borrowed timestamp: the
                # cache exists for the burn-in gauge's RPC economics;
                # a real emission re-earns its reorg check or abstains
                # (fleet round 7 — the poller carries what this skips)
                self.bump("s1.abstain.ts_cached")
                continue
            if (wallet, rec["asset"]) in e.get("ingested_assets", set()):
                # THIS ENTRY already emitted this (wallet, asset) on an
                # earlier retry pass (fleet round 13, CRITICAL): a
                # bundle whose second market hit a transient probe
                # error retried per r5's design, a benign same-height
                # re-mine landed between the passes, and pass 2's
                # re-earned block ts shifted the first market's key —
                # emit_dup missed on the new key and the r6 own-sibling
                # allowance (built for DIFFERENT-asset bundle legs)
                # whitelisted the entry's own earlier SAME-asset row.
                # One economic fill must be one row regardless of how
                # the timestamp moved between passes: per-asset
                # exclusion is structural, never the executor's job.
                #
                # DESIGN DECISION (fleet round 14, documented): this
                # cap also defers a GENUINE second distinct same-asset
                # exec_owner fill of one tx (a taker sweep filling two
                # resting orders of one whale in one market). No
                # re-mine-stable identity exists that separates that
                # rare shape from the round-13 twin (logIndex, ts and
                # even amounts can all shift across a re-mine), and
                # admitting it re-opens the key-divergent double
                # emission class — so the second fill DEFERS to the
                # poller, counted honestly under its own reason, and
                # burn-in parity holds (counted_marks is per-asset
                # too).
                self.bump("s1.abstain.same_asset_entry")
                continue
            # CLAIM BEFORE ANY AWAIT and honor refusal: awaiting the
            # probe between the registry read and the claim opened a
            # window where the receipt path could claim-and-ingest a
            # key-divergent row (fleet r1, confirmed CRITICAL)
            if not _claim(tx, wallet, "emitter"):
                self.bump("s1.abstain.v3_ingested")
                continue
            try:
                rows = await pool.fetch(SQL_PROBE, tx, rec["whale_id"],
                                        rec["asset"], timeout=6)
            except Exception:  # noqa: BLE001
                # RETRY, not forfeit (fleet r5): one transient DB error
                # mid-group was permanently losing every remaining
                # market of an armed bundle. Already-ingested records
                # skip via emit_dup on the retry pass.
                self.bump("s1.errors")
                return None
            # a receipt-path row for this (tx, whale, asset) is the
            # key-divergent-twin risk — abstain. Our own earlier s1 row
            # with THIS key is a dup — skip. An s1 row with a DIFFERENT
            # key is a sibling ONLY if THIS entry wrote it (round 2's
            # conflation forfeited the whale's second fill in a bundle;
            # e['ingested_keys'] keeps that case working). Any OTHER s1
            # row is a post-finalize re-entry — a deep-reorg re-add
            # whose new block ts shifts the key, or a straggler leg of
            # an already-emitted tx judged in isolation (fleet round 6,
            # both CRITICAL: key-divergent double emission) — and per
            # this probe's own rule an earlier emitter row equally
            # forbids a second view. The poller carries anything real
            # that this refuses.
            srcs = {str(r["source"]) for r in (rows or [])}
            stored_keys = {str(r["dedupe_key"]) for r in (rows or [])}
            if "chain" in srcs:
                self.bump("s1.abstain.chain_row_preexists")
                continue
            if keys[0][1] in stored_keys:
                self.bump("s1.emit_dup")
                continue
            own_keys = e.setdefault("ingested_keys", set())
            if any(str(r["source"]) == "s1"
                   and str(r["dedupe_key"]) not in own_keys
                   for r in (rows or [])):
                self.bump("s1.abstain.s1_row_preexists")
                continue
            from .pipeline import TradeEvent, ingest_trade_result
            size = float(Decimal(rec["size_units"]) / Decimal(10 ** 6))
            price = rec_prices(rec)[0]
            # source='s1', not 'chain': the shadow buckets evidence rows
            # strictly by 'chain'/'poll', so the instrument never sees
            # the emitter's own rows as coverage — a wrong emission can
            # no longer silence the orphan GATING alarms that would
            # catch it (fleet r1: self-certification kill). Downstream
            # the pipeline/executor are source-agnostic.
            ev = TradeEvent(
                whale_id=rec["whale_id"],
                whale_username=rec.get("username"),
                tx_hash=tx, asset=rec["asset"], side=rec["side"],
                size=size, price=price, ts_epoch=rec["ts"],
                source="s1")
            if ev.dedupe_key != keys[0][1]:
                # the built event does not reproduce the certified key —
                # the one condition that must never be guessed around
                self.bump("s1.abstain.key_selfcheck")
                self._trip("key_selfcheck")
                if await self._persist_trip(
                        pool, "key_selfcheck") == "refused":
                    # a NEW self-check failure after a clear is fresh
                    # evidence — refresh past the tombstone (round 10)
                    # from the tombstone's OWN clock (round 11)
                    self.trips["key_selfcheck"] = await self._db_now(pool)
                    await self._persist_trip(pool, "key_selfcheck")
                return False
            if self.pending.get(tx) is not e:
                # a removed notice popped this entry during the probe
                # await — the fill is off the canonical chain (r8)
                self.bump("s1.abstain.reorged")
                return False
            if (e["ts"].get(rec["block"]) != rec["ts"]
                    or e.get("ts_src", {}).get(rec["block"])
                    != e["blocks"].get(rec["block"])):
                # a re-mine never pops the entry, so the membership
                # check above cannot see it: rec['ts'] was copied out
                # of e['ts'] BEFORE the probe await, and a same-height
                # re-mine landing during that await purges e['ts'] and
                # moves e['blocks'] to the new hash while the copy
                # survives (fleet round 11, major — armed emission
                # with the orphaned block's timestamp, key-divergent
                # twin of the venue's poll row). The timestamp under
                # this record must still be the one in the entry AND
                # must have been earned against the hash the entry
                # currently believes canonical — anything else re-earns
                # or abstains; the poller carries whatever was real.
                self.bump("s1.abstain.reorged")
                return False
            try:
                _tid, was_new = await ingest_trade_result(ev)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return None                  # retry; emit_dup skips any
                                             # record that did commit
            self._mark_counted(tx, wallet, rec["asset"])
            e.setdefault("ingested_keys", set()).add(ev.dedupe_key)
            e.setdefault("ingested_assets", set()).add(
                (wallet, rec["asset"]))
            emitted = True
            self.last_emit_at = now
            if was_new:
                self.bump("s1.emitted")
                self.bump("s1.emitted_agg" if rec["view"] == "agg"
                          else "s1.emitted_exec_owner")
                age = now - rec["ts"]
                b = ("le5s" if age <= 5 else "le10s" if age <= 10
                     else "le15s" if age <= 15 else "gt15s")
                self.bump("s1.emit_age." + b)
                log.info("S1 fill: %s %s %s %.2f @ %.4f (+%.1fs)",
                         rec.get("username") or wallet[:10], rec["side"],
                         rec["asset"][:12], size, price, age)
            else:
                self.bump("s1.emit_dup")
        return emitted

    # ── the corroboration sweep ─────────────────────────────────────
    async def _sweep_wallet(self, pool: Any, whale_id: int,
                            address: str, salt: str) -> None:
        try:
            rows = await pool.fetch(SQL_SWEEP, float(CORROBORATE_S),
                                    whale_id, salt, timeout=10)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            return
        confirmed_ids, judged = [], []
        new_suspects: list = []
        key_divergent: list = []
        for r in rows or []:
            if r["ok"]:
                confirmed_ids.append(r["id"])
                continue
            ts = r["ts"]
            ts_epoch = (ts.timestamp() if hasattr(ts, "timestamp")
                        else float(ts or 0))
            try:
                det = r["detected_at"]
                det_epoch = (det.timestamp()
                             if hasattr(det, "timestamp")
                             else float(det or 0))
                ran = await pool.fetchrow(
                    SQL_RECON_SINCE, r["detected_at"], address,
                    ts_epoch - RECON_TS_MARGIN_S,
                    float(RECON_VENUE_LAG_S), ts_epoch,
                    det_epoch + RECON_VENUE_LAG_S, timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return
            if ran is None or int(ran["n"] or 0) < 2:
                continue               # fewer than TWO runs provably
                                       # covered THIS fill's depth and
                                       # lag (round 20: one walk's
                                       # pagination can be masked by
                                       # aligned twins + a shift; two
                                       # walks see different feed
                                       # geometry) — defer
            # R2 KEY-DIVERGENT TWIN (round 42): before any deferred
            # judgment, ask whether the venue in fact served this
            # very (whale, tx, asset) under a DIFFERENT dedupe key —
            # that is not silence, it is a key-fidelity failure with
            # a duplicate already in the executor's table, and no
            # amount of holding or healing can ever stamp across a
            # key mismatch. Immediate sticky under its honest name.
            try:
                twin = await pool.fetchrow(SQL_KEY_TWIN, r["id"],
                                           timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                continue
            if twin is not None:
                key_divergent.append(r)
                # falls through to the trip path below via judged —
                # tagged so the reason carries the true diagnosis
                r = dict(r)
                r["_key_divergent"] = True
            # DEFERRED VERDICT (round 42, design round; judged winner
            # 'direction-first'). Coverage testimony cannot be made
            # airtight against an honest-but-degraded feed: rounds
            # 38-41 each broke it one shape further, and the fill
            # still sitting in the venue's indexing backlog while the
            # head advances is observationally unreachable through
            # /trades. Fifteen fleet rounds of realized alarms were
            # ALL false positives on correct emissions; the caps
            # bound a late TRUE trip to pocket change while a false
            # trip disarms the priced latency edge for hours. The
            # verdict is therefore two-phase: a first qualification
            # records SUSPECT — visible, counted, NOT disarming —
            # and only a suspicion that SURVIVES the hold with fresh
            # covering evidence and a LIVE index becomes the sticky
            # trip. A multi-wallet suspect burst bypasses the hold:
            # a wrong decode is wallet-agnostic and still alarms at
            # today's latency. The r17 healer already releases the
            # false shapes post-thaw; the hold just moves that
            # release BEFORE the alarm.
            if not r.get("_key_divergent"):
                if r["s1_suspect_at"] is None:
                    new_suspects.append(r)
                    continue
                burst = (self._suspect_wallets
                         >= SUSPECT_BURST_WALLETS)
                sa = r["s1_suspect_at"]
                sa_ts = (sa.timestamp() if hasattr(sa, "timestamp")
                         else float(sa))
                held = (await self._db_now(pool)) - sa_ts
                if not burst:
                    if held < SUSPECT_HOLD_S:
                        continue
                    # the hold must BEAR EVIDENCE, not merely
                    # elapse: one clean covering run finished after
                    # the suspicion was recorded (the venue lag was
                    # already paid against detected_at)
                    try:
                        # r43 (major): the r42 indexing-time floor
                        # rode $1/$4 and this call inherited
                        # newest >= suspect_at — a phantom whose
                        # wallet went quiet AFTER qualification could
                        # never re-cover and the promised alarm was
                        # silenced forever. The floor is explicit
                        # now: this recheck asks only for one clean
                        # covering run RECORDED after the suspicion
                        # that still spans the fill.
                        ran2 = await pool.fetchrow(
                            SQL_RECON_SINCE, sa, address,
                            ts_epoch - RECON_TS_MARGIN_S, 0.0,
                            ts_epoch, ts_epoch, timeout=6)
                    except Exception:  # noqa: BLE001
                        self.bump("s1.errors")
                        return
                    if ran2 is None or int(ran2["n"] or 0) < 1:
                        continue
                    try:
                        live = await pool.fetchval(
                            SQL_INDEX_LIVE, whale_id,
                            r["detected_at"],
                            float(RECON_VENUE_LAG_S), r["ts"],
                            timeout=6)
                    except Exception:  # noqa: BLE001
                        self.bump("s1.errors")
                        continue
                    if not live:
                        continue   # the wallet's index has not moved
                                   # past this fill at all — defer,
                                   # never alarm
            # LAST LOOK before the verdict (round 15): the covering
            # run may BE the run that just stamped this row — judge
            # the row as it is now, not as the sweep snapshot saw it.
            try:
                cur = await pool.fetchrow(SQL_RECHECK, r["id"],
                                          timeout=6)
            except Exception:  # noqa: BLE001 — cannot re-look: defer
                self.bump("s1.errors")
                continue
            if cur is not None and bool(cur["ok"]):
                confirmed_ids.append(r["id"])
                continue
            judged.append(r)
            if r.get("_key_divergent"):
                log.error("S1 KEY-DIVERGENT row id=%s key=%s — the "
                          "venue booked this very fill under a "
                          "DIFFERENT dedupe key (C6 fidelity / C4 "
                          "duplicate)", r["id"],
                          str(r["dedupe_key"])[:16])
            else:
                log.error("S1 UNCORROBORATED row id=%s key=%s — no "
                          "venue row corroborates this fill after a "
                          "%.0fs suspect hold over live coverage",
                          r["id"], str(r["dedupe_key"])[:16],
                          SUSPECT_HOLD_S)
        # SUSPECT STAMPING (round 42): first-qualification rows are
        # recorded durably — visible, counted, NOT disarming — via a
        # venue-unseen-conditional transition (a row the venue stamps
        # inside the gap refuses and confirms next sweep).
        if new_suspects:
            try:
                got = await pool.fetch(SQL_SUSPECT,
                                       [r["id"] for r in new_suspects],
                                       timeout=10)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                got = []
            self.bump("s1.suspect", len(got or []))
            for g in got or []:
                log.warning("S1 SUSPECT row id=%s — coverage says the "
                            "venue should have shown this fill and "
                            "has not; holding %.0fs before the "
                            "verdict", g["id"], SUSPECT_HOLD_S)
        # TRIP BEFORE STAMP (round 6): the stamp makes the verdict
        # permanent (the row is never re-judged), so a durable trip
        # must exist first — a crash between stamp and trip silenced
        # the alarm forever. The reverse order is idempotent: if the
        # stamp then fails, the row re-judges and the trip unions.
        ok_judged = []
        for r in judged:
            reason = (("key_divergent:%s" if r.get("_key_divergent")
                       else "uncorroborated:%s") % r["id"])
            self._trip(reason)
            out = await self._persist_trip(pool, reason)
            if out == "refused":
                # the tombstone predates THIS judgment: the row on the
                # table right now is fresh evidence, and the pinned
                # promise is that a post-clear re-trip STANDS — refresh
                # the timestamp past the tombstone and re-persist
                # (round 10: the idempotency-pinned stale timestamp
                # kept losing to the tombstone and the row was stamped
                # with no durable trip anywhere). The refresh reads the
                # tombstone's OWN clock (round 11: an app-clock refresh
                # under PG-ahead skew was refused every cycle, and the
                # flush's tombstone release then let the next sweep
                # re-bump the same verdict's counter durably each time)
                self.trips[reason] = await self._db_now(pool)
                out = await self._persist_trip(pool, reason)
            if out == "landed":
                ok_judged.append(r)
        # STAMP BEFORE COUNTING (fleet r4): a failed stamp must not
        # inflate s1.confirmed forever. Round 16: confirmed and judged
        # stamp through DIFFERENT statements — the judged stamp is
        # atomic-conditional on the row still being venue-unseen, so
        # the verdict can never outlive its own evidence.
        won: set = set()
        if confirmed_ids:
            try:
                rows_won = await pool.fetch(SQL_MARK, confirmed_ids,
                                            timeout=10)
                won = {r["id"] for r in rows_won or []}
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return                 # nothing counted; rows re-judge
        judged_won: set = set()
        if ok_judged:
            try:
                rows_jw = await pool.fetch(
                    SQL_MARK_JUDGED, [r["id"] for r in ok_judged],
                    timeout=10)
                judged_won = {r["id"] for r in rows_jw or []}
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return                 # trip stands; rows re-judge
        for r in ok_judged:
            if r["id"] in judged_won:
                continue
            # the judged stamp was refused. Either another sweep won
            # the transition (they counted it), or the VENUE stamped
            # the row inside our recheck→trip gap — in which case the
            # trip we just persisted brands a corroborated row (round
            # 16): release it ourselves, leave the row unstamped, and
            # the next sweep confirms it through the ok path.
            # r44 (minor x3): this branch hardcoded 'uncorroborated:'
            # while the trip persisted for a key-divergent row was
            # 'key_divergent:<id>' — the self-clear popped a reason
            # that never tripped (spurious durable tombstone, wrong
            # operator log) and the refuted kd trip survived the very
            # sweep that proved it false. The reason is the row's own.
            reason = (("key_divergent:%s" if r.get("_key_divergent")
                       else "uncorroborated:%s") % r["id"])
            try:
                cur = await pool.fetchrow(SQL_RECHECK, r["id"],
                                          timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                continue           # trip stands; re-examined next sweep
            if cur is not None and bool(cur["ok"]):
                try:
                    rel = await pool.fetchrow(SQL_CLEAR, STATE_KEY, reason,
                                              timeout=6)
                except Exception:  # noqa: BLE001
                    self.bump("s1.errors")
                    continue       # the trip stays in self.trips, and
                                   # the row is venue-stamped — exactly
                                   # what _heal_corroborated_trips keys
                                   # on: it releases the reason on the
                                   # next sweep (round 17: the old
                                   # comment claimed a race-lost retry
                                   # that was unreachable — the row
                                   # confirms via the ok path, which
                                   # never touched trip state)
                self.trips.pop(reason, None)
                self._unpersisted.discard(reason)
                if rel is not None and bool(rel["removed"]):
                    # round 26 (minor): count only a clear THIS call
                    # actually transitioned — the round-8 law. Every
                    # process holding an adopted copy of the trip used
                    # to bump on its own redundant clear and the
                    # delta-merge made the inflation durable (N bumps
                    # for one release across N processes).
                    self.bump("s1.trip_self_cleared")
                log.warning("S1 trip '%s' self-cleared — the venue "
                            "stamped the row inside the judgment gap",
                            reason)
        # count only rows THIS process transitioned (fleet round 8:
        # two overlapping sweeps both counted the same judgment and
        # the server-side delta merge made the inflation durable)
        self.bump("s1.confirmed",
                  len([i for i in confirmed_ids if i in won]))
        self.bump("s1.uncorroborated",
                  len([r for r in ok_judged if r["id"] in judged_won
                       and not r.get("_key_divergent")]))
        # r43 (minor): the kd counter bumped per judgment PASS — one
        # transient stamp error re-judged the row and durably double-
        # counted one twin. Stamp winners are the transition, exactly
        # like s1.uncorroborated one line up (the round-8 law).
        self.bump("s1.key_divergent",
                  len([r for r in ok_judged if r["id"] in judged_won
                       and r.get("_key_divergent")]))

    async def _heal_corroborated_trips(self, pool: Any) -> None:
        """Release any 'uncorroborated:<id>' trip whose row the venue
        has since corroborated (fleet round 17, major): the round-16
        self-clear could fail on one transient error, and the claimed
        next-sweep retry was unreachable — the row confirms through
        the ok path, which never touches trip state, and the false
        trip stood forever against a database that contradicts it.
        This healer runs every sweep over the live trip set (normally
        empty), so EVERY orphan path — failed self-clear, crash
        windows, foreign trips adopted from other processes — heals
        the moment the evidence says the alarm's premise is false."""
        for reason in [k for k in self.trips
                       if k.startswith(("uncorroborated:",
                                        "key_divergent:"))]:
            # r43: key_divergent heals too — its premise ("venue_seen_
            # at can never stamp across a key mismatch") was falsified
            # executed: the straggling IDENTICAL-key venue row lands
            # later and stamps the s1 row through the pipeline
            # conflict branch, refuting the trip. venue_seen_at is
            # the shared release evidence for both reasons.
            try:
                rid = int(reason.split(":", 1)[1])
            except (TypeError, ValueError):
                continue
            try:
                cur = await pool.fetchrow(SQL_RECHECK, rid, timeout=6)
            except Exception:  # noqa: BLE001 — heal again next sweep
                self.bump("s1.errors")
                continue
            if cur is None or not bool(cur["ok"]):
                continue               # genuinely unseen: trip stands
            try:
                rel = await pool.fetchrow(SQL_CLEAR, STATE_KEY, reason,
                                          timeout=6)
            except Exception:  # noqa: BLE001 — heal again next sweep
                self.bump("s1.errors")
                continue
            self.trips.pop(reason, None)
            self._unpersisted.discard(reason)
            if rel is not None and bool(rel["removed"]):
                # round 26 (minor): transition-gated — see the race-
                # lost branch. A redundant clear releases only the
                # local memory, never the counter.
                self.bump("s1.trip_self_cleared")
            log.warning("S1 trip '%s' healed — the venue has "
                        "corroborated the row", reason)

    async def _corroboration_sweep(self, pool: Any) -> None:
        # refresh the DB clock offset once per sweep so every trip this
        # cycle stamps PG's clock, not the app host's (round 12)
        await self._db_now(pool)
        # trips-vs-evidence reconciliation BEFORE new judgments (r17)
        await self._heal_corroborated_trips(pool)
        # r42: the burst bypass reads the durable suspect census once
        # per sweep — a failed probe never ARMS the bypass, it defers
        try:
            self._suspect_wallets = int(await pool.fetchval(
                SQL_SUSPECT_WALLETS, float(SUSPECT_BURST_WINDOW_S),
                timeout=6) or 0)
        except Exception:  # noqa: BLE001
            self._suspect_wallets = 0
        # per-sweep rotation salt (round 7): every wallet and row gets
        # an equal shot at each sweep — deferral can defer, not block
        salt = str(int(time.time()))
        try:
            wallets = await pool.fetch(SQL_SWEEP_WALLETS,
                                       float(CORROBORATE_S), salt,
                                       timeout=10)
            backlog = await pool.fetchval(SQL_BACKLOG,
                                          float(CORROBORATE_S), timeout=10)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            return
        self.unjudged_backlog = int(backlog or 0)
        for w in wallets or []:
            # per-wallet windows (round 6): a whale whose reconciler
            # sweep keeps failing defers only its OWN rows
            await self._sweep_wallet(pool, w["whale_id"], w["address"],
                                     salt)

    # ── flush (own state key; observability, not evidence) ──────────
    @staticmethod
    def _trip_str(v: Any) -> str:
        # must reproduce Postgres ->> for the CAS comparison: text as
        # itself, other JSON scalars in JSON form (round 3: a boolean
        # trip made str(True) != 'true' and starved every flush)
        if v is None:
            return ""
        return v if isinstance(v, str) else json.dumps(v)

    async def _flush(self, pool: Any, now: float) -> None:
        self.last_flush_at = now
        if not self._state_loaded:
            # writing before the persisted state is known can erase a
            # pending arm or a foreign trip (round 3) — deltas simply
            # accumulate until the loader succeeds
            return
        # trips that failed their immediate SQL_TRIP retry here first —
        # durability is never left to chance across flush cycles
        for reason in list(self._unpersisted):
            await self._persist_trip(pool, reason)
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
        except Exception:  # noqa: BLE001
            self.bump("s1.flush_failures")
            return
        # round 35: a sibling process's contested verdicts are global
        # — adopt the stored floor and marks at every flush read, so
        # this process's OWN finalize gate learns them within one
        # cycle (the SQL fold below already protects the disk copy;
        # this protects the next 60s of local emission decisions)
        scf = doc.get("contested_floor")
        if isinstance(scf, (int, float)) and scf > self.contested_floor:
            self.contested_floor = int(scf)
        scm = doc.get("contested")
        if isinstance(scm, dict):
            for k, v in scm.items():
                try:
                    h = int(k)
                except (TypeError, ValueError):
                    continue
                if h > 0 and h not in self.contested:
                    self.contested[h] = (v if isinstance(
                        v, (int, float)) else 0.0)
            while len(self.contested) > CONTESTED_CAP:
                old = min(self.contested)
                self.contested.pop(old, None)
                self.contested_floor = max(self.contested_floor, old)
        # READ-ONLY trip reconciliation (round 6): the disk trip set is
        # maintained solely by SQL_TRIP/SQL_CLEAR server-side; this
        # merge only decides what THIS process believes. An operator
        # clear names exactly one reason (r5) and its PER-REASON
        # tombstone releases stale in-memory copies in every process —
        # a second clear can no longer forget the first (r6).
        lt = doc.get("trips")
        stored_trips = dict(lt) if isinstance(lt, dict) else {}
        # the legacy scalar FOLDS IN beside the dict (fleet round 7:
        # falling back only when the dict was absent let the first
        # round-6 trip shadow an uncleared pre-round-6 trip)
        if doc.get("tripped"):
            stored_trips.setdefault(self._trip_str(doc["tripped"]), 0.0)
        tc = doc.get("trips_cleared")
        cleared = tc if isinstance(tc, dict) else {}
        merged_trips: dict[str, float] = {}
        for src in (stored_trips, self.trips):
            for reason, at in src.items():
                if not isinstance(at, (int, float)):
                    at = 0.0
                ca = cleared.get(reason)
                if isinstance(ca, (int, float)) and ca > at:
                    continue
                cur = merged_trips.get(reason)
                merged_trips[reason] = at if cur is None else min(cur, at)
        if merged_trips != self.trips:
            adopted = set(merged_trips) - set(self.trips)
            released = set(self.trips) - set(merged_trips)
            if adopted:
                # a foreign trip is global — adopt, never clobber
                self.armed = False
            for reason in released:
                self._unpersisted.discard(reason)
                log.warning("S1 trip '%s' cleared by operator", reason)
            self.trips = merged_trips
        c = doc.get("counters")
        stored_counters = c if isinstance(c, dict) else {}
        snap, self.deltas = self.deltas, {}
        # a pending (not-yet-validated) arm is still an arm on disk —
        # persisting armed=false before validation erased legitimate
        # arms across boot blips (round 3)
        armed_out = bool((self.armed or self._pending_arm_at is not None)
                         and not self.trips)
        # counters ship as DELTAS — the server adds them under the row
        # lock, so concurrent flushes compose instead of clobbering,
        # and the payload structurally cannot name trip state (r6)
        # CONTESTED VERDICTS SURVIVE A RESTART (fleet round 34): the
        # registry was in-memory only, so a boot between the conflict
        # and the orphaned frame's redelivery forgot the proof — the
        # same forgetting the eviction floor closes. The flush carries
        # the most recent marks plus the floor; marks the flush cap
        # cannot carry raise the PERSISTED floor instead (the adopted
        # state over-abstains rather than forgets). Residual window:
        # a crash before the first flush after a conflict — the
        # corroboration sweep remains the backstop there.
        marks = sorted(self.contested.items())
        floor_out = self.contested_floor
        if len(marks) > CONTESTED_FLUSH_CAP:
            floor_out = max([floor_out]
                            + [h for h, _ in marks[:-CONTESTED_FLUSH_CAP]])
            marks = marks[-CONTESTED_FLUSH_CAP:]
        payload = {"counters": snap, "armed": armed_out,
                   "armed_at": (self.armed_at if self.armed
                                else (self._pending_arm_at or 0.0)),
                   "unjudged_backlog": self.unjudged_backlog,
                   "cert_green": self.cert_green,
                   "cert_reason": self.cert_reason,
                   "contested": {str(h): t for h, t in marks},
                   "contested_floor": floor_out,
                   "decoder_fp": DECODER_FP,
                   "updated_at_epoch": now}
        try:
            await pool.execute(SQL_WRITE, STATE_KEY, json.dumps(payload),
                               timeout=6)
        except Exception:  # noqa: BLE001
            # AMBIGUOUS: the write may have committed and lost its ack.
            # Restoring the snap would double-count on the next flush,
            # so the snap is DROPPED — undercount-only, the same choice
            # the shadow's ack protocol makes (round 3)
            self.bump("s1.flush_failures")
            self.bump("s1.snap_dropped_ambiguous")
            return
        # local mirror for the beat: stored view + what we just added
        merged_view = dict(stored_counters)
        for k, v in snap.items():
            base = merged_view.get(k)
            merged_view[k] = (base if isinstance(base, (int, float))
                              else 0) + v
        self.counters = merged_view

    def _should_poll_head(self, now: float) -> bool:
        """The poll is for when the logs-only WS goes QUIET (round 6:
        the un-gated version fired every tick under live traffic —
        there is always a tx younger than CONFIRM_DEPTH — and starved
        ts resolution of the shared RPC budget). Someone must be
        waiting on confirmation, the WS must not have advanced the
        head recently, and polls are spaced HEAD_POLL_MIN_S apart."""
        if now - self.head_advanced_at <= HEAD_QUIET_S:
            return False
        if now - self.last_head_poll_at < HEAD_POLL_MIN_S:
            return False
        return any(
            max(e["blocks"], default=0) + CONFIRM_DEPTH > self.head
            for e in self.pending.values() if e["blocks"])

    # ── the task loop ───────────────────────────────────────────────
    async def run(self) -> None:
        from ..db import get_pool
        last_cert = 0.0
        while True:
            try:
                await asyncio.sleep(TICK_S)
                if not self.enabled:
                    continue
                lst = self.listener() if self.listener else None
                if lst is not None:
                    self.http_url = getattr(lst, "_http_url", "") or self.http_url
                if not self.http_url:
                    continue
                pool = await get_pool()
                now = time.time()
                if not self._state_loaded:
                    await self._load_state(pool, now)
                if now - last_cert >= CERT_EVERY_S:
                    last_cert = now
                    await self._check_cert(pool, now)
                    if self._state_loaded:
                        await self._corroboration_sweep(pool)
                if self._should_poll_head(now) and self._take_token():
                    self.last_head_poll_at = now
                    await self._poll_head()
                now = time.time()
                for tx in list(self.pending.keys())[:64]:
                    e = self.pending.get(tx)
                    if e is None:
                        continue
                    if now - e["last_seen"] < DEBOUNCE_S:
                        continue
                    if e["blocks"] and \
                            max(e["blocks"]) + CONFIRM_DEPTH > self.head:
                        if now - e["first_seen"] > EMIT_MAX_AGE_S:
                            self.bump("s1.abstain.confirm_timeout")
                            self.pending.pop(tx, None)
                        continue
                    done = await self._finalize_tx(pool, tx, e, now)
                    if done and self.pending.get(tx) is e:
                        # ENTRY IDENTITY, not tx key (fleet round 21,
                        # major): _finalize_tx awaits, and a removed-
                        # notice arriving mid-finalize pops THIS entry
                        # while the canonical re-mined log creates a
                        # FRESH entry under the same tx key. A key-only
                        # pop here destroyed that fresh entry with zero
                        # accounting — the canonical fill silently
                        # never finalized. Pop only the exact entry we
                        # finalized; a successor entry is someone
                        # else's work. (The orphaned entry we just ran
                        # cannot arm-emit stale state: its post-await
                        # ts write-backs are gen/hash-guarded and the
                        # r7 armed path refuses cached-ts emission.)
                        self.pending.pop(tx, None)
                now = time.time()
                if now - self.last_flush_at >= FLUSH_EVERY_S:
                    await self._flush(pool, now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                await asyncio.sleep(2)


_STATE = S1Emitter()
_TASK: asyncio.Task | None = None


def emitter_observe(listener: Any, log_entry: dict[str, Any]) -> None:
    """Sync, zero-I/O, never raises. Called from chain._handle_log for
    every FILL_V3_TOPIC log, before _handle_v3 dispatch."""
    try:
        _STATE.observe(listener, log_entry)
    except Exception:  # noqa: BLE001
        try:
            _STATE.deltas["s1.errors"] = _STATE.deltas.get("s1.errors", 0) + 1
        except Exception:  # noqa: BLE001
            pass


def ensure_emitter_task(listener: Any) -> None:
    """Idempotent; one task per process. Mirror of ensure_shadow_task."""
    global _TASK
    try:
        _STATE.listener = weakref.ref(listener)
        _STATE.http_url = getattr(listener, "_http_url", "") or _STATE.http_url
        if not _STATE.enabled:
            return
        if _TASK is None or _TASK.done():
            _TASK = asyncio.get_running_loop().create_task(_STATE.run())
    except Exception:  # noqa: BLE001
        pass


def emitter_beat() -> dict:
    try:
        st = _STATE
        return {"enabled": st.enabled, "armed": st.armed,
                "cert": st.cert_reason,
                "cert_metrics": getattr(st, "cert_metrics", {}),
                "tripped": st.tripped,
                "pending": len(st.pending),
                "emitted": st.counters.get("s1.emitted", 0)
                + st.deltas.get("s1.emitted", 0),
                "would": st.counters.get("s1.would_emit", 0)
                + st.deltas.get("s1.would_emit", 0),
                "unjudged": st.unjudged_backlog,
                "suspects": st._suspect_wallets,
                "last_emit_age_s": (round(time.time() - st.last_emit_at)
                                    if st.last_emit_at else None),
                "err_unflushed": st.deltas.get("s1.errors", 0)}
    except Exception:  # noqa: BLE001
        return {"enabled": False}
