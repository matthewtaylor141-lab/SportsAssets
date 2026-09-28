"""The three free latency levers exist in the code, and are measured.

Owner requirement: "Skipping candidates already stale on arrival,
prioritization and within-cycle deduplication are proposed remedies until
wired and observed. Implement the supported changes without moving
freshness limits. Deduplicate only equivalent requests; retain correct
instrument identity and observation times. Recheck freshness at the actual
decision. Measure provider age, our added delay, request counts, valid
evaluations and refusals on the deployed path."

── WHAT THESE TESTS CAN AND CANNOT ESTABLISH ────────────────────────
They establish that the levers are WIRED: the code path exists, the
refusal has its own name, the dedup key is the instrument and not
something broader, the freshness recheck is still at the decision, and the
measurement block carries every figure the owner asked for.

They do NOT establish that the levers REDUCED anything in production.
That needs a deployed cycle and a readback, and it is the one claim this
file must not be mistaken for. `research/latency_effect_readback.sql`
answers it from the heartbeat, and until that has been run against a build
carrying this code the effect is UNMEASURED rather than improved.

That distinction is the entire reason this file's name says "wired and
measured" and not "improved".
"""

import inspect

import pytest

from sportsassets.workers import ext_pinnacle_loop as L


SRC = inspect.getsource(L)
CYCLE_SRC = inspect.getsource(L._cycle) if hasattr(L, "_cycle") else SRC


# ═════════════════════════════════════════════════════════════════════
# 1 · THE LIMIT DID NOT MOVE. Checked first, because every lever below
#     would be worthless -- or worse, dishonest -- if it had.
# ═════════════════════════════════════════════════════════════════════

def test_the_freshness_limit_is_unchanged():
    """PINNACLE_MAX_AGE_S is still the odds source's own 30 s rule.

    THE ONE THING THAT MUST NOT HAVE HAPPENED. Every "latency
    improvement" has a trivial implementation: relax the limit and watch
    the stale count fall. The owner ruled it out explicitly ("without
    moving freshness limits") and this is the assertion that makes the
    prohibition enforceable rather than aspirational.
    """
    assert L.PINNACLE_MAX_AGE_S == 30.0


def test_lever_a_compares_against_that_same_limit_and_no_other():
    """The skip uses PINNACLE_MAX_AGE_S, not a tighter derived bound.

    A margin -- "skip anything over 25 s, it will never make it" -- would
    be moving the limit by another name: candidates the decision gate
    WOULD have admitted would be refused. So the comparison is against the
    published limit exactly, and a candidate is skipped only when it is
    already outside it.
    """
    assert "(_arr - _pe) > PINNACLE_MAX_AGE_S" in SRC
    # AND NO SECOND, TIGHTER CONSTANT WAS INTRODUCED ALONGSIDE IT.
    for bad in ("MAX_AGE_MARGIN", "STALE_MARGIN_S", "PREDICTED_AGE",
                "AGE_HEADROOM_S"):
        assert bad not in SRC, bad


# ═════════════════════════════════════════════════════════════════════
# 2 · LEVER A · SKIP WHAT IS ALREADY PAST THE LIMIT
# ═════════════════════════════════════════════════════════════════════

def test_lever_a_refuses_by_its_own_name():
    """QUOTE_STALE_ON_ARRIVAL, distinct from QUOTE_STALE.

    WHY THE SEPARATE CODE MATTERS MORE THAN IT LOOKS. If the skip reused
    `QUOTE_STALE`, the levers could appear to work by moving cases between
    two indistinguishable buckets: the self-inflicted count would fall
    because those candidates now refuse earlier under the same name, with
    no actual improvement. Two codes make the shift visible.
    """
    assert L.R_QUOTE_STALE_ON_ARRIVAL == "QUOTE_STALE_ON_ARRIVAL"
    assert L.R_QUOTE_STALE_ON_ARRIVAL != L.R_VENUE_QUOTE_STALE
    assert "LIMIT IS UNCHANGED" in L.WHY_SKIPPED_ON_ARRIVAL


def test_lever_a_runs_before_the_venue_read_not_after():
    """The saving is the read, so the check must precede it.

    Placed after `venue_quote` this lever would still refuse the candidate
    and would save NOTHING -- the paced read, its gap, and the delay it
    adds to every later candidate would all already have been spent. The
    ordering IS the mechanism, so it is asserted by position.
    """
    skip = CYCLE_SRC.index("lat[\"skipped_stale_on_arrival\"] += 1")
    read = CYCLE_SRC.index("vq = await venue_quote(")
    assert skip < read, "lever A must precede the venue read"


def test_lever_a_separates_provider_staleness_from_our_own():
    """Two counters, because the remedies differ and only one is ours.

    A quote the provider handed us ALREADY past 30 s is a coverage fact we
    cannot fix by being faster. One that arrived inside the limit and aged
    out on our clock is ours. Folding them together would credit the
    levers for provider lag.
    """
    assert "lat[\"provider_stale_on_arrival\"] += 1" in CYCLE_SRC
    assert "lat[\"skipped_stale_on_arrival\"] += 1" in CYCLE_SRC
    assert "(float(received_at) - _pe) > PINNACLE_MAX_AGE_S" in CYCLE_SRC


# ═════════════════════════════════════════════════════════════════════
# 3 · LEVER B · FRESHEST FIRST
# ═════════════════════════════════════════════════════════════════════

def test_lever_b_orders_by_the_providers_own_timestamp():
    """Newest first, and an unreadable timestamp sorts LAST.

    THE `None` ORDERING IS THE POINT. An event whose age cannot be read
    must not be handed the freshest slot -- that is the favourable
    assumption, and it would spend the best slots on the candidates we
    know least about. The sort key puts `is None` first in the tuple, so
    those events sort to the end.
    """
    assert "events.sort(key=lambda e: (_ages[id(e)] is None," in CYCLE_SRC
    assert "-(_ages[id(e)] or 0.0)))" in CYCLE_SRC


def test_lever_b_drops_nothing():
    """Reordering only. The same events are still considered.

    A "prioritisation" that also truncated would be a coverage change
    wearing a latency change's clothes: fewer candidates evaluated, a
    better-looking stale rate, and markets silently unexamined. The loop
    still iterates the full length and `MAX_PER_CYCLE` is the only bound.
    """
    assert "for _i in range(len(events)):" in CYCLE_SRC
    assert "if evaluated >= MAX_PER_CYCLE:" in CYCLE_SRC
    # No slicing of the event list anywhere near the sort.
    seg = CYCLE_SRC[CYCLE_SRC.index("events.sort("):
                    CYCLE_SRC.index("for _i in range(len(events)):")]
    assert "events = events[" not in seg
    assert "[:MAX" not in seg


def test_the_sort_is_not_an_economic_preference():
    """It prefers fresher DATA, never better opportunities.

    Sorting by edge, price, liquidity or expected value would be a
    selection rule smuggled in as a latency fix -- it would change WHICH
    trades the lane finds, not merely how stale they are when it finds
    them. The key reads a timestamp and nothing else.
    """
    seg = CYCLE_SRC[CYCLE_SRC.index("LEVER B"):
                    CYCLE_SRC.index("LEVER C")]
    for economic in ("edge", "expected_value", "ev_", "price", "liquidity",
                     "depth", "spread"):
        assert economic not in seg.lower().replace("last_update", ""), economic


# ═════════════════════════════════════════════════════════════════════
# 4 · LEVER C · DEDUPLICATE EQUIVALENT REQUESTS ONLY
# ═════════════════════════════════════════════════════════════════════

def test_lever_c_keys_on_the_instrument_and_the_side():
    """`(us_market_slug, intent)` -- one contract, one ladder.

    NARROWER THAN THE OBVIOUS KEYS ON PURPOSE. Keying on `condition_id`
    alone would merge the two SIDES of a market, which are two different
    ladders with different prices; keying on the event would merge several
    markets. Either would return one book as the answer for a different
    instrument, which is not deduplication -- it is a wrong read.
    """
    assert '_ck = (ident["us_market_slug"], ident["intent"])' in CYCLE_SRC
    # AND NOT THE BROADER KEYS.
    for wrong in ('_ck = (ident["condition_id"]',
                  '_ck = ident["condition_id"]',
                  '_ck = (quote["event_id"]'):
        assert wrong not in CYCLE_SRC, wrong


def test_lever_c_does_not_cache_a_refusal():
    """A transient venue error must not become the cycle's answer.

    Caching a failed read would suppress every retry on that instrument
    for the rest of the cycle: one timeout would refuse every candidate on
    that contract, and the cause would be an optimisation rather than the
    venue. So only `ok` reads enter the cache.
    """
    assert "if vq.get(\"ok\"):\n                    vq_cache[_ck] = vq" \
        in CYCLE_SRC


def test_lever_c_rechecks_freshness_at_the_actual_decision():
    """A shared read cannot lend its freshness to a later decision.

    THE FAILURE MODE THIS RULES OUT IS THE DANGEROUS ONE: candidate 2
    reuses candidate 1's book and inherits candidate 1's age, so a book
    read 40 s ago is judged as though it were read now. `_entry_freshness`
    is still called with `now` -- the decision instant taken AFTER the
    reads -- so the second candidate is judged on how old the book
    actually is when IT decides, which is strictly older.
    """
    assert "freshness=_entry_freshness(quote, vq, now)" in CYCLE_SRC
    # AND THE DECISION INSTANT IS STILL TAKEN AFTER THE READS.
    dedup = CYCLE_SRC.index("_ck = (ident[")
    decide = CYCLE_SRC.index("now = time.time()", dedup)
    fresh = CYCLE_SRC.index("_entry_freshness(quote, vq, now)", decide)
    assert dedup < decide < fresh


def test_lever_c_retains_each_candidates_own_observation_time():
    """Each provider quote keeps its own `observed_at`.

    The venue BOOK is shared; the provider QUOTE is not. `quote` is rebuilt
    per event from that event's own `last_update`, so two candidates
    sharing a venue read still carry two different provider observation
    times -- which is what "retain correct instrument identity and
    observation times" requires.
    """
    assert "quote = pinnacle_h2h(event, received_at=received_at)" in CYCLE_SRC
    # The cache holds venue quotes only -- never the provider quote.
    assert "vq_cache[_ck] = vq" in CYCLE_SRC
    assert "vq_cache[_ck] = quote" not in CYCLE_SRC


# ═════════════════════════════════════════════════════════════════════
# 5 · THE MEASUREMENT REACHES THE HEARTBEAT
# ═════════════════════════════════════════════════════════════════════

REQUIRED = ("provider_lag_s", "our_processing_s", "valid_evaluations",
            "stale_refusals", "self_inflicted_stale",
            "skipped_stale_on_arrival", "deduplicated_requests",
            "venue_requests")


def test_every_figure_the_owner_asked_for_is_in_the_cycle_return():
    """Provider age, our delay, request counts, valid evaluations, refusals."""
    for k in REQUIRED:
        assert '"%s":' % k in CYCLE_SRC, k


def test_the_digest_persists_all_of_them():
    """AND THE HEARTBEAT CARRIES THEM, which is where the defect was.

    `odds_freshness` was returned by the cycle and dropped by
    `_heartbeat`, whose persisted key set is explicit. So the telemetry for
    the latency work existed for one function call and was unreadable
    afterwards -- a readback saw `field_present f` and it looked like the
    build predated the change. This asserts the digest and the key.
    """
    dsrc = inspect.getsource(L._freshness_digest)
    for k in REQUIRED:
        assert '"%s"' % k in dsrc, k
    hsrc = inspect.getsource(L._heartbeat)
    assert '"odds_freshness": _freshness_digest(out)' in hsrc


def test_an_empty_sample_reports_none_and_never_zero():
    """A cycle that measured nothing has no delay, not a delay of zero.

    0.0 would be the best-looking number in the table and would be
    produced by measuring nothing at all -- the same class of error as
    reporting an unmarked position as worth its cost.
    """
    assert L._median([]) is None
    assert L._maxof([]) is None
    assert L._median([1.0, 3.0]) == 2.0
    assert L._maxof([1.0, 9.0, 3.0]) == 9.0


def test_the_median_is_used_rather_than_the_mean():
    """One stalled read must not hide a real improvement, or be hidden.

    A mean over a sample containing one 400-second timeout moves far
    enough to swamp the signal. The median answers "what did a typical
    candidate experience"; the max is carried beside it so the outlier is
    still visible rather than smoothed away.
    """
    csrc = CYCLE_SRC
    assert "_median(lat[\"provider_lag_samples\"])" in csrc
    assert "_maxof(lat[\"provider_lag_samples\"])" in csrc
    assert "statistics.mean" not in SRC
    assert "sum(lat[" not in SRC


def test_a_valid_evaluation_is_not_a_buy():
    """Freshness, not profitability.

    Counting only candidates that produced a BUY would conflate "we had
    usable data" with "we found an edge", and a cycle with no edge would
    be indistinguishable from a cycle with no data. The counter increments
    on an age inside the limit.
    """
    assert "lat[\"valid_evaluations\"] += 1" in CYCLE_SRC
    seg = CYCLE_SRC[CYCLE_SRC.index("_fr = _entry_freshness"):
                    CYCLE_SRC.index("rec[\"venue_quote\"] = vq")]
    assert "else:" in seg and "lat[\"valid_evaluations\"] += 1" in seg
    for economic in ("admissible", "decision ==", "order_submitted"):
        assert economic not in seg, economic


def test_self_inflicted_is_a_conjunction_of_two_measured_numbers():
    """Not inferred from a refusal code, which could be renamed.

    `self_inflicted_stale` increments only when the total age exceeded the
    limit AND the provider's own lag did not. Both come from
    `_entry_freshness`, which derives them from clocks on the record. A
    definition that keyed off a refusal string would silently change
    meaning the next time a code was renamed.
    """
    seg = CYCLE_SRC[CYCLE_SRC.index("_fr = _entry_freshness"):
                    CYCLE_SRC.index("rec[\"venue_quote\"] = vq")]
    assert "if _ag > PINNACLE_MAX_AGE_S:" in seg
    assert "if _pl is not None and _pl <= PINNACLE_MAX_AGE_S:" in seg
    assert "lat[\"self_inflicted_stale\"] += 1" in seg


# ═════════════════════════════════════════════════════════════════════
# 6 · THE CROSS-SERVICE BUDGET IS NOT ASSUMED TO EXIST
# ═════════════════════════════════════════════════════════════════════

def test_the_pacer_is_still_declared_process_local():
    """Two pacers are not a shared budget, and the code must say so.

    Owner requirement: "The discovered process-local pacer also needs a
    concrete cross-service request-budget plan that preserves the
    protected worker. Do not assume the two services coordinate because
    both have a pacer."

    `venue_pace` is a process-local serial gate. The protected worker runs
    in a DIFFERENT Render service, so its gate and this one bound nothing
    jointly: two processes each honouring 0.35 s can still present ~5.7
    req/s to an account limited near 3. Nothing in this change alters
    that, and nothing in it may imply otherwise.
    """
    import sportsassets.venue_pace as VP
    psrc = inspect.getsource(VP)
    assert "process" in psrc.lower()
    # THE PLAN IS A DOCUMENT, NOT A CLAIM IN THE CODE. What this test
    # enforces is the absence of a coordination claim the code cannot back.
    for overclaim in ("cross_service_budget_enforced",
                      "shared_with_the_collector",
                      "account_wide_rate_limit_is_enforced"):
        assert overclaim not in psrc, overclaim


def test_the_levers_do_not_increase_venue_requests():
    """Every lever reduces or holds the venue read count. None adds one.

    This matters because the venue read is the shared resource with the
    protected worker: a "latency improvement" that issued MORE reads would
    consume the protected worker's headroom, which is a restriction the
    owner requires preserved. Lever A removes reads, C shares them, B
    reorders without adding any, and the counter proves it.
    """
    assert "lat[\"venue_requests\"] += 1" in CYCLE_SRC
    # The counter increments in exactly one place: the real read.
    assert CYCLE_SRC.count("lat[\"venue_requests\"] += 1") == 1
    assert "lat[\"deduplicated_requests\"] += 1" in CYCLE_SRC
