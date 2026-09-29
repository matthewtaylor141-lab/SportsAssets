"""IS THE VENUE BOOK WE ARE HOLDING ESTABLISHED CURRENT? THREE ANSWERS.

    ESTABLISHED      a documented mechanism places this book's state inside
                     the bound. Admission may proceed.
    NOT_ESTABLISHED  no mechanism does. The default, and the honest one: it is
                     NOT a claim the book is stale, it is a statement that we
                     cannot show it is current. It refuses.
    CONTRADICTED     the contract itself says the payload is older than the
                     bound -- a cache age past its own max-age, an origin
                     generation instant minutes back. It refuses, and unlike
                     the middle verdict this one is evidence.

WHY THIS FILE EXISTS, AND WHAT IT REPLACES.

An earlier version of this lane read a clock determination out of a resampling
probe: six contracts read twice, twenty seconds apart, with `transactTime`
identical and the book unchanged on every pair. That was recorded as proof that
`transactTime` is a LAST_BOOK_CHANGE stamp, and the admission gate was moved off
it onto our own receipt instant.

THAT INFERENCE WAS NOT VALID AND THE CONCLUSION IS WITHDRAWN. At least two
mechanisms produce exactly the observation that was made:

  (a) the stamp is a last-change stamp and the six markets were genuinely quiet;
  (b) an intermediary served the same cached representation to both reads, in
      which case the stamp AND the book are identical because it is literally
      the same bytes, and nothing about the live book was observed at all.

Resampling cannot separate them, and no number of further unchanged samples can:
every additional identical pair is equally well explained by either. Neither the
timestamp's semantics nor the presence of caching was resolved. What the probe
established is one sentence long -- six contracts returned unchanged timestamps
and unchanged books across two reads -- and it is recorded as that.

SO WHAT ACTUALLY DECIDES IT. A mechanism with a published contract, not a
sample. Three exist, and this module names which one carried a verdict:

  M1  LIVE_SUBSCRIPTION.  The venue publishes book updates on an authenticated
      market-data socket. The idea is that on a subscription proven alive, the
      last update received for a market IS the venue's book, because any change
      since would have been pushed.

      M1 HAS NOW BEEN VERIFIED AGAINST THE SHIPPED CLIENT AND IT DOES NOT HOLD
      ON THIS FEED. `bettor_stream_currency` records the inspection. The
      mechanism needs four things and two are absent:

        * no SEQUENCE NUMBER anywhere in the market-data payload, so a dropped
          message leaves no trace and a gap cannot be detected -- only assumed
          absent, which is the assumption this gate exists to refuse;
        * nothing distinguishes a SNAPSHOT from an INCREMENT, so a received
          message is not known to be a whole book. The "full order book" claim
          is a docstring, and `obs/streamstate.DepthAuthority` already refuses
          that inference in this repository's own words.

      Liveness and instrument identity ARE available. They are not enough: a
      heartbeat proves a socket is open and a `marketSlug` proves which market a
      message was about. Neither proves the book we hold is the venue's.

      SO M1 IS NOT GRANTED BY PASSING A `subscription` ARGUMENT. The missing
      preconditions are properties of the feed and a caller cannot supply them.
      `bettor_stream_currency.evidence_for` is the production reader and it
      returns None, naming which guarantee is missing.
  M2  CONDITIONAL_REVALIDATION.  A `304 Not Modified` to an `If-None-Match` /
      `If-Modified-Since` on the book path is the origin affirming that the
      representation we hold is still the current one, as of that response's
      own `Date`. RFC 9110 §13, RFC 9111 §4.3.
  M3  ORIGIN_GENERATION.  RFC 9111 4.2.3 dates the instant the origin generated
      the stored representation (RFC 9111 §5.1, §6.1). Inside the bound, and
      with the response shown not to be cache-served, this establishes that THE
      RESPONSE is newly generated.

      M3 IS DELIBERATELY NOT SUFFICIENT ON ITS OWN. It bounds when the HTTP
      response was made, not when the matching engine's book was last true. An
      origin can generate a fresh response at this instant out of an internal
      snapshot it computed minutes ago, and admitting on M3 alone would certify
      exactly that. It is reported as a partial establishment and named.

WHAT THIS MODULE REFUSES TO DO. It will not derive currency from transport
latency (a fast answer can carry an old snapshot -- the faster the answer, the
stronger the false certificate), from our own receipt instant (which in a
read-then-decide loop makes every book fresh by construction), or from repeated
identical samples. Each of those is an assumption wearing a measurement's
clothes, and each has already been written into this gate once.
"""

from __future__ import annotations

import email.utils
import time

# ── the verdicts ────────────────────────────────────────────────────

ESTABLISHED = "BOOK_CURRENCY_ESTABLISHED"
NOT_ESTABLISHED = "BOOK_CURRENCY_NOT_ESTABLISHED"
CONTRADICTED = "BOOK_CURRENCY_CONTRADICTED_BY_THE_CONTRACT"

#: The named mechanisms. A verdict always says which one, or that none applied.
M1_LIVE_SUBSCRIPTION = "M1_LIVE_MARKET_DATA_SUBSCRIPTION"
M2_REVALIDATION = "M2_CONDITIONAL_REVALIDATION_304"
M3_ORIGIN_GENERATION = "M3_ORIGIN_GENERATION_INSTANT"
NO_MECHANISM = "NO_MECHANISM_AVAILABLE"

#: WHICH MECHANISM ESTABLISHES THE BOOK STATE -- AND M2 NO LONGER DOES.
#:
#: M2 WAS IN THIS TUPLE AND IT IS NOW OUT. I had M2 as an establishing mechanism
#: on the reasoning that a 304 affirms what we hold. It affirms the
#: REPRESENTATION, and the venue's own responses separate that from the book.
#:
#: THE ARGUMENT, RESTATED CAREFULLY, because my first version of it was sloppy in
#: the same way as the `transactTime` claim I withdrew.
#:
#: I wrote "last-modified equals date, exactly". That is true of the FIRST read of
#: each market and false of the second:
#:
#:     read 1   date: 16:24:27   last-modified: 16:24:27   cf-cache-status: EXPIRED
#:     read 2   date: 16:24:35   last-modified: 16:24:27   cf-cache-status: HIT
#:
#: So `last-modified` does not track `Date`. It tracks REPRESENTATION GENERATION
#: and survives caching unchanged -- which is ordinary, correct HTTP, and is also
#: the exact mechanism that destroys the "a stamp cannot precede its response"
#: argument I had built elsewhere.
#:
#: WHAT ACTUALLY RULES M2 OUT AS A BOOK CLOCK, and it does not need that argument:
#: the markets read were `MARKET_STATE_EXPIRED` with ZERO book levels and a
#: `transactTime` from 2026-02-20. Their books cannot have changed today. Yet
#: `last-modified` was TODAY. A validator that moves while the book provably does
#: not is not a book clock -- and that inference rests on the book being known
#: static, not on any claim about what a timestamp may precede.
#:
#: So an origin here really does validate a representation while its own
#: market-data source is arbitrarily delayed. Recorded in
#: research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md, runs 36332797806 and
#: 36333087522.
#:
#: THIS IS THE TRAP AVOIDED, NAMED. Having found that ETag was absent I was one
#: step from treating the validator's PRESENCE as the qualifying condition -- a
#: third false certificate after transport latency and our own receipt instant.
#: A present validator does not establish a numeric market-data age any more
#: than an absent one proves no data path exists.
#:
#: M1 REMAINS IN THIS TUPLE ON PURPOSE. It is the mechanism that WOULD establish
#: currency, its verdict is computed from the live feed state rather than
#: hard-coded, and `bettor_stream_currency` is what reports that the feed cannot
#: currently satisfy it -- now on a TIMING gap (P5) rather than on the
#: replacement-authority and delta-continuity gaps I wrongly asserted.
ESTABLISHING_MECHANISMS = (M1_LIVE_SUBSCRIPTION,)

#: M2 and M3 both bound the HTTP RESPONSE. Neither bounds the book, and neither
#: alone admits anything.
PARTIAL_MECHANISMS = (M2_REVALIDATION, M3_ORIGIN_GENERATION)

#: WITHDRAWN AS AN ESTABLISHING MECHANISM, with the evidence attached so the
#: change is auditable rather than a quiet edit.
M2_WITHDRAWN_AS_ESTABLISHING = {
    "withdrawn_on": "2026-09-27",
    "mechanism": M2_REVALIDATION,
    "what_I_claimed": ("a 304 within the bound establishes that the book we "
                       "hold is current"),
    "what_a_304_actually_affirms": (
        "that the stored REPRESENTATION is still the current representation "
        "(RFC 9110 §13, RFC 9111 §4.3). That is a statement about the "
        "response, not about the market data inside it"),
    # THE OBSERVATION, AND THE INFERENCE, AS SEPARATE KEYS.
    "the_contradicting_observation": {
        "read_1": {"date": "16:24:27", "last_modified": "16:24:27",
                   "cf_cache_status": "EXPIRED"},
        "read_2": {"date": "16:24:35", "last_modified": "16:24:27",
                   "cf_cache_status": "HIT"},
        "market_state": "MARKET_STATE_EXPIRED",
        "book_levels": "0 bids / 0 offers",
        "transact_time": "2026-02-20T03:07:30.947946180Z",
        "markets": 2,
        "source": "research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md",
    },
    "the_inference": (
        "an EXPIRED market with zero levels and a February transactTime cannot "
        "have had its book change today, yet last-modified was TODAY. A "
        "validator that moves while the book provably does not is not a book "
        "clock"),
    "what_this_inference_does_NOT_rely_on": (
        "any claim about what a timestamp may or may not precede. That argument "
        "was used elsewhere for transactTime and is withdrawn as false -- a "
        "stamp CAN denote representation generation, which precedes "
        "transmission, and read 2 above shows exactly that behaviour"),
    "and_last_modified_does_not_track_Date": (
        "read 2 has date 16:24:35 and last-modified 16:24:27 on a cache HIT. "
        "My first statement of this finding said last-modified equals date "
        "exactly; that holds only for the origin read"),
    "so": ("the exchange is AVAILABLE and it is a real signal about the "
           "representation. It is not a book clock, and it is no longer "
           "allowed to admit"),
    "what_would_make_it_establishing": (
        "the venue documenting that its validator changes when and only when "
        "the book changes. Nothing on the pages read says so, and the observed "
        "behaviour says the opposite"),
}

#: THE THREE AGES, WHICH ARE THREE DIFFERENT QUANTITIES. Collapsing any two of
#: them is how each false certificate got built.
DISTINCT_AGES = {
    "HTTP_CACHE_AGE": {
        "from": "the Age header",
        "bounds": "how long a cache has held this representation",
        "is_not": "a market-data age",
    },
    "ORIGIN_GENERATION_AGE": {
        "from": "Date minus Age (RFC 9111 §5.1, §6.1)",
        "bounds": "when the ORIGIN produced the response",
        "is_not": ("a market-data age. The origin can produce a fresh "
                   "response over a delayed source, and on this venue it "
                   "demonstrably does"),
    },
    "OUR_OBSERVATION_AGE": {
        "from": "our own receipt instant",
        "bounds": "how long WE have held it",
        "is_not": ("a market-data age, and it is the most dangerous of the "
                   "three: in a read-then-decide loop it is near zero by "
                   "construction, so it makes every book fresh"),
    },
    "UPSTREAM_MARKET_DATA_AGE": {
        "from": "NO HEADER STATES IT",
        "bounds": "when the BOOK was what it says -- the only one that matters",
        "the_only_candidate": ("transactTime, which is established to be a "
                               "market-data instant and whose exact "
                               "denotation is unresolved"),
    },
}

#: How current the book state must be. The SAME number the lane has always
#: applied to the venue side. It is not moved by this file in either direction:
#: what changes is that it is now applied to a quantity with a contract behind
#: it instead of to a field whose meaning is unresolved.
MAX_BOOK_STATE_AGE_S = 30.0

#: How recently a subscription must have proven itself alive for M1 to carry.
#: A socket that has gone quiet is not a socket that is delivering updates, and
#: silence on a dead connection is indistinguishable from a quiet market -- the
#: same confusion this file exists to refuse.
MAX_SUBSCRIPTION_SILENCE_S = 15.0

#: ── WHAT THE 2026-09-27 PROBE OBSERVED. OBSERVATION ONLY. ───────────
#:
#: Read this as data, not as a finding. It records what came back and states
#: plainly which questions it left open, because an earlier version of this
#: record stated a conclusion the method could not support.
PROBE_2026_09_27 = {
    "what_ran": ("six contracts read twice through "
                 "/api/admin/venue-clock-probe against the serving build, "
                 "the two reads about twenty seconds apart"),
    "observed": {
        "contracts": 6,
        "our_receipt_delta_s": "about 20 on all six",
        "transact_time_identical_across_the_two_reads": 6,
        "best_ask_identical_across_the_two_reads": 6,
        "response_headers_captured": 0,
    },
    "what_this_establishes": (
        "that on these six contracts, in this one window, two reads about "
        "twenty seconds apart returned the same transactTime value and the "
        "same best ask. That is all."),
    "what_this_does_NOT_establish": [
        "what marketData.transactTime denotes. A last-change stamp and a "
        "cached response produce the identical observation",
        "whether an intermediary cache is in the path. No response header was "
        "captured on either read, so caching was neither shown nor excluded",
        "that the six markets were quiet. Unchanged bytes are consistent with "
        "a quiet market and with the same bytes being replayed",
        "that any freshness bound may be moved. It supports no policy change",
    ],
    "why_more_samples_cannot_help": (
        "every further identical pair is explained equally well by a quiet "
        "market and by a cache. The two hypotheses make the same prediction, "
        "so the observation cannot discriminate between them however often it "
        "is repeated. Resolving it requires a mechanism with a published "
        "contract -- M1, M2 or M3 above -- not a larger sample"),
    "superseded": ("an earlier record asserted VENUE_STAMP_SEMANTICS = "
                   "LAST_BOOK_CHANGE from this probe. That conclusion is "
                   "withdrawn; the observation is kept"),
}

#: The semantics question, in the state it is actually in.
VENUE_STAMP_SEMANTICS = "UNRESOLVED"
VENUE_STAMP_SEMANTICS_MEANS = (
    "what marketData.transactTime denotes is not established. It is carried as "
    "provenance and reported as an age, and no admission decision rests on it "
    "in either direction: an old value is not treated as proof the book is "
    "stale, and a recent value is not treated as proof it is current")


# ── header reading, to the letter of the contract ────────────────────

def _http_date(value):
    """An HTTP-date to epoch seconds, or None. RFC 9110 §5.6.7."""
    if value is None:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    try:
        return dt.timestamp()
    except (OverflowError, ValueError):
        return None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _num(value):
    """An epoch-seconds instant as a float, or None.

    SEPARATE FROM `_int` BECAUSE THESE ARE INSTANTS, not header counts.
    `Age` is an integer by RFC 9111; a response/request instant is a float
    and truncating it to whole seconds would quantise the response-delay
    correction to the same granularity as the bound it feeds.

    A non-numeric or absent value is None, never 0.0 -- a missing instant
    must not become the epoch, which would report an age of decades and
    look like a refusal for the wrong reason.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    # NaN and infinities are not instants. `out != out` is the NaN test
    # without importing math into a header parser.
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _cache_control(value) -> dict:
    """`Cache-Control` as directives. Values kept where they are numeric."""
    out = {}
    for part in str(value or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "=" in part:
            name, _, raw = part.partition("=")
            out[name.strip()] = _int(raw.strip().strip('"'))
        else:
            out[part] = True
    return out


def read_contract(observation) -> dict:
    """What a response's own headers say about when it was generated.

    Pure reading. No verdict, no bound applied -- so that the verdict below can
    be read against the raw contract rather than against an interpretation.
    """
    obs = observation if isinstance(observation, dict) else {}
    hdrs = {str(k).lower(): v for k, v in (obs.get("headers") or {}).items()}
    date_epoch = _http_date(hdrs.get("date"))
    age_s = _int(hdrs.get("age"))
    cc = _cache_control(hdrs.get("cache-control"))
    # RFC 9111 §5.1: `Age` is the time since the origin generated the stored
    # response. `Date` is the origin's own generation instant. With both, the
    # generation instant of what we hold is Date - Age.
    # ── RFC 9111 §4.2.3, NOT `Date` MINUS `Age` ──────────────────────
    #
    # INDEPENDENT AUDIT FINDING (28 Sep 2026). This computed
    # `generated_at = Date - Age`, and the caller then aged it against now:
    #
    #     current_age = now - (Date - Age) = (now - Date) + Age
    #
    # which ADDS the two estimates. With `Date` 20 s ago and `Age: 20` it
    # reported 40 s and contradicted a 30 s bound on a response that was
    # 20 s old. The production refusal captured in the audit used exactly
    # this arithmetic.
    #
    # RFC 9111 §4.2.3 keeps them as TWO INDEPENDENT ESTIMATES of the same
    # quantity and takes their MAXIMUM, then adds local residence:
    #
    #     apparent_age           = max(0, response_time - date_value)
    #     corrected_age_value    = age_value + response_delay
    #     corrected_initial_age  = max(apparent_age, corrected_age_value)
    #     resident_time          = now - response_time
    #     current_age            = corrected_initial_age + resident_time
    #
    # `apparent_age` covers a cache that omits `Age`; `corrected_age_value`
    # covers a clock skewed against ours. The max is right because each is
    # a lower bound on the true age and neither dominates.
    #
    # WHAT THIS DOES NOT DO, and the audit is explicit about it: correcting
    # the arithmetic does NOT establish upstream book currency. A fresh
    # HTTP response says the transport was quick, not that the order book
    # behind it is current. This makes the number honest; it does not make
    # a trade admissible.
    req_at = _num(obs.get("request_at") or obs.get("requested_at")
                  or obs.get("request_started_at"))
    resp_at = _num(obs.get("received_at") or obs.get("response_at")
                   or obs.get("our_response_received_at"))
    age_parts = {}
    corrected_initial_age = None
    if resp_at is not None:
        apparent = (None if date_epoch is None
                    else max(0.0, float(resp_at) - float(date_epoch)))
        # RESPONSE DELAY NEEDS THE REQUEST INSTANT. Without it the delay is
        # UNKNOWN, and RFC 9111's correction cannot be completed -- so the
        # Age header is used uncorrected and the result is reported as a
        # LOWER BOUND rather than passed off as a complete measurement.
        delay = (None if req_at is None
                 else max(0.0, float(resp_at) - float(req_at)))
        corrected_age = (None if age_s is None
                         else float(age_s) + (delay or 0.0))
        cands = [c for c in (apparent, corrected_age) if c is not None]
        corrected_initial_age = (max(cands) if cands else None)
        age_parts = {
            "apparent_age_s": (None if apparent is None
                               else round(apparent, 3)),
            "response_delay_s": (None if delay is None else round(delay, 3)),
            "corrected_age_value_s": (None if corrected_age is None
                                      else round(corrected_age, 3)),
            "corrected_initial_age_s": (None if corrected_initial_age is None
                                        else round(corrected_initial_age, 3)),
            "response_time_epoch_s": float(resp_at),
            "request_time_epoch_s": (None if req_at is None
                                     else float(req_at)),
            "is_a_lower_bound": req_at is None,
            "why_a_lower_bound": (
                None if req_at is not None else
                "the request instant was not recorded, so RFC 9111's "
                "response_delay correction cannot be applied. The Age "
                "header is used uncorrected, which can only UNDERSTATE "
                "the age -- reported as a lower bound, never as a "
                "complete measurement"),
            "method": "RFC_9111_SECTION_4_2_3",
            "this_is_transport_age_not_book_currency": (
                "the age of the HTTP representation. A fresh response says "
                "the transport was quick; it does not establish that the "
                "order book behind it is current"),
        }
    # `generated_at` IS DERIVED FROM THE CORRECTED AGE, so the caller's
    # `now - generated_at` reproduces RFC 9111's `current_age` instead of
    # summing the two estimates. Falls back to the response instant when
    # neither estimate exists, and stays None when we have no instant at
    # all -- an unknown age must not become a favourable one.
    generated_at = None
    if resp_at is not None and corrected_initial_age is not None:
        generated_at = float(resp_at) - float(corrected_initial_age)
    elif date_epoch is not None and resp_at is None:
        # ── NO RESPONSE INSTANT: `Date` ALONE, NEVER `Date` MINUS `Age` ──
        #
        # SECOND AUDIT FINDING, AND MY REPAIR HAD LEFT THE BUG HERE. The
        # main path was corrected to RFC 9111 4.2.3 but this fallback still
        # read `Date - Age`, so the audit's own observation reported 20 s
        # WITH a receipt time and 40 s WITHOUT one. One arithmetic, two
        # answers, and the worse answer on the poorer evidence.
        #
        # WHY ADDING THEM IS WRONG, stated once so it is not reintroduced:
        # `Date` IS the origin's generation instant. `now - Date` therefore
        # ALREADY CONTAINS the cache residency that `Age` measures. Adding
        # `Age` counts that residency a second time.
        #
        # So the Date-based estimate stands alone here, and `Age` is carried
        # as an INDEPENDENT LOWER BOUND for the evaluator to take the
        # maximum of -- which is conservative, because a lower bound can
        # only ever support a refusal and never an admission.
        generated_at = date_epoch
    xc = str(hdrs.get("x-cache") or hdrs.get("cf-cache-status") or "").lower()
    cache_hit = None
    if xc:
        cache_hit = ("hit" in xc) and ("miss" not in xc)
    elif age_s is not None:
        # A non-zero Age can only come from a cache (RFC 9111 §5.1).
        cache_hit = age_s > 0
    return {
        "headers_present": sorted(hdrs.keys()),
        "date": hdrs.get("date"),
        "date_epoch_s": date_epoch,
        "age_header_s": age_s,
        "cache_control": cc or None,
        "etag": hdrs.get("etag"),
        "last_modified": hdrs.get("last-modified"),
        "cache_status_header": xc or None,
        "served_from_cache": cache_hit,
        "origin_generated_at_epoch_s": generated_at,
        "origin_generated_at_is": (
            "response_time minus RFC 9111 4.2.3 corrected_initial_age, so "
            "that re-ageing it against `now` reproduces `current_age`. It "
            "is NOT `Date` minus `Age` -- that summed two independent "
            "estimates of one quantity and double-counted, reporting 40 s "
            "for a 20-s-old response carrying `Age: 20`"),
        # ── WHEN THE TIMING EVIDENCE IS ONLY PARTIAL, SAY SO ─────────
        #
        # "Missing timing evidence must have an explicit partial or unknown
        # result; it must never become a freshness certificate." With no
        # response instant there is no apparent-age/corrected-age pair and
        # no residence term, so this is a PARTIAL reading -- usable to
        # refuse, never to admit.
        "http_age_is_partial": bool(resp_at is None and date_epoch is not None),
        "http_age_is_unknown": bool(resp_at is None and date_epoch is None),
        # `Age` AS AN INDEPENDENT LOWER BOUND, for the evaluator to max
        # against the Date-based estimate. Never added to it.
        "age_lower_bound_s": (None if age_s is None else float(age_s)),
        "why_partial": (
            None if resp_at is not None else
            "no response instant was observed, so RFC 9111 4.2.3 cannot be "
            "applied: there is no apparent-age/corrected-age pair and no "
            "residence term. The Date-based estimate stands alone and `Age` "
            "is carried as a separate lower bound. This is PARTIAL timing "
            "evidence and cannot certify freshness"),
        # THE COMPONENTS, so a refusal can be checked rather than believed.
        "http_age": age_parts or None,
        "http_age_method": (age_parts.get("method") if age_parts
                            else "NO_RESPONSE_INSTANT_RECORDED"),
        "revalidation_possible": bool(hdrs.get("etag")
                                      or hdrs.get("last-modified")),
        "max_age_s": cc.get("max-age") if isinstance(cc.get("max-age"), int)
                     else None,
        "uncacheable_declared": bool(cc.get("no-store") or cc.get("no-cache")
                                     or cc.get("private")
                                     or cc.get("max-age") == 0),
    }


# ── the verdict ─────────────────────────────────────────────────────

def evaluate(*, now, observation=None, subscription=None, revalidation=None,
             venue_ts=None, our_receipt_at=None,
             bound_s=MAX_BOOK_STATE_AGE_S) -> dict:
    """Which mechanism, if any, establishes this book's currency.

    `observation`   a `venue_http_observer` row for this read, or None.
    `subscription`  {"alive_at": epoch, "last_update_at": epoch, "slug": ...}
                    from a live market-data subscription, or None.
    `revalidation`  {"status": 304, "date_epoch_s": ...} from a conditional
                    re-request, or None.
    `venue_ts`      parsed `transactTime`, carried as provenance ONLY.
    `our_receipt_at` when WE received the response. Reported, and never a
                    mechanism: see the module docstring.

    Never raises. Returns a verdict dict whose `verdict` is one of the three
    module constants.
    """
    contract = read_contract(observation)
    out = {
        "verdict": NOT_ESTABLISHED,
        "mechanism": NO_MECHANISM,
        "bound_s": float(bound_s),
        # ── THE EVIDENCE THIS VERDICT WAS COMPUTED FROM, CARRIED FORWARD ──
        #
        # THE BROKEN LINK THIS CLOSES. The entry lane evaluates currency twice:
        # once at the READ, in `venue_quote`, and again at the DECISION instant
        # in `_entry_freshness`, which re-ages the same evidence so a book whose
        # currency was established at the read cannot inherit that
        # establishment through the further reads that follow it. The second
        # evaluation reads `subscription_input` and `revalidation_input` off
        # this dict -- and nothing wrote them. So the decision-time evaluation
        # always ran with NO mechanism, and STALE_DATA refused every entry at
        # the risk stage even when the read had ESTABLISHED currency.
        #
        # It stayed invisible because production supplies no mechanism at all,
        # so every candidate is refused at the read first; only an end-to-end
        # run with a qualifying mechanism reaches the second evaluation.
        #
        # THE INPUTS ARE CARRIED AS GIVEN, and re-ageing them can only make the
        # verdict stricter: silence and state age both grow with `now`.
        "subscription_input": (dict(subscription)
                               if isinstance(subscription, dict) else None),
        "revalidation_input": (dict(revalidation)
                               if isinstance(revalidation, dict) else None),
        "book_state_established_at_epoch_s": None,
        "book_state_age_s": None,
        "contract": contract,
        "mechanisms_considered": [M1_LIVE_SUBSCRIPTION, M2_REVALIDATION,
                                  M3_ORIGIN_GENERATION],
        "mechanisms_unavailable": [],
        "partial": None,
        "venue_stamp": {
            "semantics": VENUE_STAMP_SEMANTICS,
            "semantics_means": VENUE_STAMP_SEMANTICS_MEANS,
            "parsed_epoch_s": (None if venue_ts is None else float(venue_ts)),
            "age_s": (None if venue_ts is None
                      else round(float(now) - float(venue_ts), 3)),
            "decides_nothing": True,
            "probe": PROBE_2026_09_27,
        },
        "our_processing_delay_s": (
            None if our_receipt_at is None
            else round(float(now) - float(our_receipt_at), 3)),
        "our_processing_delay_is_not_currency": (
            "the interval between our receipt and this decision is a real "
            "interval and a real check, and it says nothing about how old the "
            "payload was when it reached us. It cannot establish currency and "
            "is never read as though it could"),
    }

    # ── CONTRADICTION FIRST. If the contract itself dates the payload
    # outside the bound, no mechanism can rescue it and the reason is
    # evidence rather than an absence.
    gen = contract.get("origin_generated_at_epoch_s")
    if gen is not None:
        gen_age = float(now) - float(gen)
        # ── THE MAXIMUM OF TWO LOWER BOUNDS, NOT THEIR SUM ───────────
        #
        # On the partial path (no response instant) the Date-based estimate
        # and the `Age` header are two INDEPENDENT lower bounds on the same
        # age. Taking the larger is conservative and correct; adding them is
        # the double-count this repair removed. On the complete path
        # `age_lower_bound_s` is already inside `corrected_age_value`, so
        # the max is a no-op there rather than a second application.
        _alb = contract.get("age_lower_bound_s")
        if contract.get("http_age_is_partial") and _alb is not None:
            # ── AND THE `Age` VALUE IS RE-AGED TO `now` ───────────────
            #
            # A DEFECT FOUND BY MEASURING THIS BRANCH (2026-09-28). `Age`
            # states how old the payload was WHEN THE RESPONSE LEFT THE
            # CACHE. It keeps ageing after that, and using the raw header
            # value silently treats our own residency as zero.
            #
            # WHAT THIS DOES *NOT* FIX, stated because I first wrote that it
            # did. It does not close an admission. Measured against
            # 4ef37a7: `Age: 29` with 5 s of residency and a 30 s bound
            # reported 29 s and returned NOT_ESTABLISHED with
            # `admits: False` -- this partial path does not admit anything,
            # so no stale book was reaching a decision through it.
            #
            # WHAT IT DOES FIX. Two things an operator reads. The AGE is now
            # right (34 s, not 29 s), and the REFUSAL REASON changes from
            # NOT_ESTABLISHED to CONTRADICTED -- from "we cannot tell" to
            # "the response's own headers say it is too old". Those are
            # different facts about the venue, and the candidate ledger is
            # the place where that difference decides what gets
            # investigated.
            #
            # THIS IS NOT THE DOUBLE-COUNT THE EARLIER REPAIR REMOVED, and
            # the distinction is the whole point. That one added `Age` to the
            # DATE-BASED ESTIMATE -- two measurements of the SAME interval
            # (origin -> now), so their sum counted it twice. This adds the
            # interval from OUR RECEIPT to now, which `Age` does not cover at
            # all: origin -> cache-exit, then receipt -> now. Disjoint
            # intervals, so the sum is the age. It is RFC 9111 4.2.3's
            # `resident_time`, and leaving it out was the omission.
            _resident = (None if our_receipt_at is None
                         else max(0.0, float(now) - float(our_receipt_at)))
            _alb_now = float(_alb) + (_resident or 0.0)
            if _alb_now > gen_age:
                out["origin_generation_age_from"] = "AGE_HEADER_LOWER_BOUND"
                out["origin_generation_age_why"] = (
                    "the `Age` header (%.0f s) re-aged by our residency "
                    "(%s s) gives %.1f s, which exceeds the Date-based "
                    "estimate (%.1f s). The two clocks disagree, so the "
                    "LARGER lower bound is used. The two ESTIMATES of the "
                    "same interval are never added; the residency is a "
                    "different interval and is"
                    % (float(_alb),
                       "unknown" if _resident is None
                       else "%.1f" % _resident,
                       _alb_now, gen_age))
                out["age_header_resident_time_s"] = (
                    None if _resident is None else round(_resident, 3))
                out["age_header_was_re_aged"] = bool(_resident is not None)
                if _resident is None:
                    # NO RECEIPT INSTANT MEANS NO RESIDENCY, and the figure
                    # is then a lower bound that is known to be short by an
                    # unmeasured amount. Said, rather than presented as the
                    # age.
                    out["origin_generation_age_understates_by"] = (
                        "an unknown residency: no receipt instant was "
                        "supplied, so the interval between the response "
                        "leaving the cache and this decision is not "
                        "measured and could not be added")
                gen_age = _alb_now
            else:
                out["origin_generation_age_from"] = "DATE_BASED_ESTIMATE"
        out["origin_generation_age_is_partial"] = bool(
            contract.get("http_age_is_partial"))
        out["origin_generation_age_s"] = round(gen_age, 3)
        if gen_age > float(bound_s):
            out["verdict"] = CONTRADICTED
            out["mechanism"] = M3_ORIGIN_GENERATION
            out["why"] = (
                "the response's own headers date this payload %.1f s old, past "
                "the %.0f s bound. Date minus Age is the origin's generation "
                "instant for the representation we are holding, so this is the "
                "contract stating the payload is old -- not an absence of "
                "evidence. It refuses." % (gen_age, float(bound_s)))
            # RENAMED (2026-09-28). It read DATE_MINUS_AGE_OUTSIDE_THE_BOUND,
            # which named the arithmetic the RFC 9111 §4.2.3 repair removed.
            # A refusal label that names a superseded algorithm sends the
            # next reader to the wrong place; the quantity is now the
            # representation's current age.
            out["contradicted_by"] = "HTTP_AGE_OUTSIDE_THE_BOUND"
            out["contradicted_by_was_called"] = (
                "DATE_MINUS_AGE_OUTSIDE_THE_BOUND until the RFC 9111 "
                "4.2.3 correction; the old name described a computation "
                "that double-counted Date offset against the Age header")
            return out
    max_age = contract.get("max_age_s")
    age_h = contract.get("age_header_s")
    if max_age is not None and age_h is not None and age_h > max_age:
        out["verdict"] = CONTRADICTED
        out["mechanism"] = M3_ORIGIN_GENERATION
        out["why"] = ("the cache served this response %s s after generation "
                      "against its own max-age of %s s: by the response's own "
                      "directive it is stale" % (age_h, max_age))
        out["contradicted_by"] = "AGE_EXCEEDS_THE_RESPONSE_MAX_AGE"
        return out

    # ── M1. A live subscription, proven alive.
    sub = subscription if isinstance(subscription, dict) else None
    if sub is None:
        out["mechanisms_unavailable"].append(
            {"mechanism": M1_LIVE_SUBSCRIPTION,
             "why": "no live market-data subscription was supplied for this "
                    "market, so the venue's push contract cannot be invoked"})
    else:
        alive_at = sub.get("alive_at")
        last_at = sub.get("last_update_at")
        silence = (None if alive_at is None
                   else float(now) - float(alive_at))
        out["subscription"] = {
            "alive_at_epoch_s": alive_at,
            "silence_s": (None if silence is None else round(silence, 3)),
            "silence_limit_s": MAX_SUBSCRIPTION_SILENCE_S,
            "last_update_at_epoch_s": last_at,
        }
        if silence is None or silence > MAX_SUBSCRIPTION_SILENCE_S:
            out["mechanisms_unavailable"].append(
                {"mechanism": M1_LIVE_SUBSCRIPTION,
                 "why": ("the subscription has not proven itself alive within "
                         "%.0f s. Silence on a dead socket and a quiet market "
                         "look identical, which is the confusion this gate "
                         "exists to refuse"
                         % MAX_SUBSCRIPTION_SILENCE_S)})
        elif last_at is None:
            out["mechanisms_unavailable"].append(
                {"mechanism": M1_LIVE_SUBSCRIPTION,
                 "why": "the subscription is alive but has delivered no update "
                        "for this market, so it establishes the connection and "
                        "not the book"})
        else:
            state_age = float(now) - float(last_at)
            if state_age <= float(bound_s):
                out["verdict"] = ESTABLISHED
                out["mechanism"] = M1_LIVE_SUBSCRIPTION
                out["book_state_established_at_epoch_s"] = float(last_at)
                out["book_state_age_s"] = round(state_age, 3)
                out["why"] = (
                    "a market-data subscription proven alive %.1f s ago "
                    "delivered this market's last update %.1f s ago. On the "
                    "venue's push contract any change since would have "
                    "arrived, so the book state is current within the %.0f s "
                    "bound" % (silence, state_age, float(bound_s)))
                return out
            out["mechanisms_unavailable"].append(
                {"mechanism": M1_LIVE_SUBSCRIPTION,
                 "why": ("the last update for this market arrived %.1f s ago, "
                         "past the %.0f s bound"
                         % (state_age, float(bound_s)))})

    # ── M2. A conditional revalidation the origin answered 304.
    rev = revalidation if isinstance(revalidation, dict) else None
    if rev is None:
        out["mechanisms_unavailable"].append(
            {"mechanism": M2_REVALIDATION,
             "why": "no conditional re-request was made, so the origin was "
                    "never asked to affirm the representation"})
    else:
        status = rev.get("status")
        r_date = rev.get("date_epoch_s")
        if r_date is None:
            r_date = _http_date((rev.get("headers") or {}).get("date"))
        affirmed_age = (None if r_date is None else float(now) - float(r_date))
        out["revalidation"] = {"status": status,
                               "affirmed_at_epoch_s": r_date,
                               "affirmed_age_s": (None if affirmed_age is None
                                                  else round(affirmed_age, 3))}
        if status == 304 and affirmed_age is not None \
                and affirmed_age <= float(bound_s):
            # AN IN-BOUND 304, AND IT STILL DOES NOT ADMIT.
            #
            # This branch used to return ESTABLISHED. It does not any more, and
            # the reason is the venue's own responses rather than caution: its
            # `last-modified` equals `date` over market data 219 days old, so
            # the validator it is affirming has no connection to the book. A
            # 304 here says "the representation you hold is still the
            # representation I would send" -- which is true, useful, and not a
            # market-data age.
            out["revalidation"]["affirmed_in_bound"] = True
            out["revalidation"]["but_this_does_not_establish_the_book"] = (
                "a 304 affirms the REPRESENTATION. On this venue the "
                "representation's validator tracks the response instant, not "
                "the book -- see M2_WITHDRAWN_AS_ESTABLISHING")
            out["mechanisms_unavailable"].append(
                {"mechanism": M2_REVALIDATION,
                 "exchange_succeeded": True,
                 "why": ("the origin DID answer 304 within the bound, and that "
                         "affirms the representation rather than the book. "
                         "Treating it as a book clock would be the third false "
                         "certificate, after transport latency and our own "
                         "receipt instant"),
                 "evidence": M2_WITHDRAWN_AS_ESTABLISHING[
                     "the_contradicting_observation"]["source"]})
        else:
            out["mechanisms_unavailable"].append(
                {"mechanism": M2_REVALIDATION,
                 "exchange_succeeded": False,
                 "why": ("the conditional request did not produce an in-bound "
                         "304 (status %s). NOTE this is not the reason M2 "
                         "cannot admit -- an in-bound 304 would not admit "
                         "either" % status)})

    # ── M3. Origin generation, which is a PARTIAL and never enough alone.
    if gen is None:
        out["mechanisms_unavailable"].append(
            {"mechanism": M3_ORIGIN_GENERATION,
             "why": ("no Date header was observed for this read, so the "
                     "origin's generation instant is unknown. "
                     + ("the response-metadata recorder produced no row for "
                        "this request" if not observation
                        else "the response carried no Date"))})
    elif contract.get("served_from_cache"):
        out["mechanisms_unavailable"].append(
            {"mechanism": M3_ORIGIN_GENERATION,
             "why": ("this response was served from a cache (%s), so it is a "
                     "stored representation. Its generation instant is inside "
                     "the bound but a cache hit is not the origin speaking"
                     % (contract.get("cache_status_header")
                        or "Age > 0"))})
    else:
        out["partial"] = {
            "mechanism": M3_ORIGIN_GENERATION,
            "response_generated_s_ago": out.get("origin_generation_age_s"),
            "establishes": "that THIS HTTP RESPONSE was generated at the "
                           "origin inside the bound",
            "does_not_establish": (
                "when the matching engine's book was last true. An origin can "
                "generate a response now from an internal snapshot it computed "
                "minutes ago, and admitting on this alone would certify "
                "exactly that"),
        }
        out["mechanisms_unavailable"].append(
            {"mechanism": M3_ORIGIN_GENERATION,
             "why": "sufficient for the response, not for the book state. "
                    "Recorded as a partial establishment"})

    out["why"] = (
        "no mechanism with a published contract places this book's state "
        "inside %.0f s. THIS IS NOT A FINDING THAT THE BOOK IS STALE -- it is "
        "the absence of evidence that it is current, and it refuses for that "
        "reason. What would change it: %s"
        % (float(bound_s),
           "a live market-data subscription for this market (M1), or a "
           "conditional revalidation against the book path (M2)"))
    out["not_established_is_not_stale"] = True
    return out


def admits(verdict) -> bool:
    """One place decides. ESTABLISHED admits; the other two do not."""
    v = (verdict or {}).get("verdict") if isinstance(verdict, dict) else verdict
    return v == ESTABLISHED


def describe() -> dict:
    return {
        "verdicts": [ESTABLISHED, NOT_ESTABLISHED, CONTRADICTED],
        "default": NOT_ESTABLISHED,
        "mechanisms": {
            M1_LIVE_SUBSCRIPTION: "the venue's push contract, on a "
                                  "subscription proven alive",
            M2_REVALIDATION: "a 304 from the origin to a conditional request",
            M3_ORIGIN_GENERATION: "Date minus Age. PARTIAL: bounds the "
                                  "response, not the book state",
        },
        "establishing": list(ESTABLISHING_MECHANISMS),
        "partial_only": list(PARTIAL_MECHANISMS),
        "bound_s": MAX_BOOK_STATE_AGE_S,
        "subscription_silence_limit_s": MAX_SUBSCRIPTION_SILENCE_S,
        "never_a_mechanism": [
            "transport latency, because a fast answer can carry an old "
            "snapshot and the faster it is the stronger the false certificate",
            "our own receipt instant, because in a read-then-decide loop it "
            "makes every book current by construction",
            "repeated identical samples, because a quiet market and a cache "
            "predict the same observation",
        ],
        "venue_stamp_semantics": VENUE_STAMP_SEMANTICS,
        "venue_stamp_semantics_means": VENUE_STAMP_SEMANTICS_MEANS,
        "probe_2026_09_27": PROBE_2026_09_27,
    }
