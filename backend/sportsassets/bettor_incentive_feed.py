"""Feed health and book age are DIFFERENT QUESTIONS, answered apart.

THE ERROR THIS EXISTS TO CORRECT. An earlier draft of the collection
spec said a scoring instant resolves only from a frame no more than
five seconds old. That number came from `MAX_RECEIPT_AGE_S`, which is
a TRADING gate: before quoting into a book you want recent evidence
that the book is what you think it is. Applied to research sampling it
is simply wrong, because this feed is CHANGE-DRIVEN. A market that
nobody touches for ten minutes sends nothing for ten minutes, and its
book is CURRENT the whole time. Scoring that as stale would throw away
exactly the quiet books whose qualifying uptime the experiment exists
to measure, and would do it silently.

So there are two verdicts on every sample and they are never merged:

  TRADING ELIGIBILITY   `MarketStream.book_at()`, unchanged, with its
                        own bounds. Nothing here relaxes it, widens it
                        or re-implements it. It is called and its
                        answer is recorded verbatim.

  RESEARCH VALIDITY     whether the ladder we hold is a faithful
                        picture of the venue's book AT THIS INSTANT.
                        Book age does not enter it at all.

WHAT RESEARCH VALIDITY ACTUALLY DEPENDS ON -- continuity, not age:

  1. the socket is connected NOW;
  2. this slug has delivered a FULL LADDER IN THE CURRENT EPOCH. A
     disconnect voids every cached book whatever its age (the stream
     already stamps epochs for this reason), and the missed interval
     cannot be replayed, so the first frame after a reconnect is an
     INITIAL LADDER and the slug is unscorable until it arrives;
  3. the CONNECTION is demonstrably alive at this instant.

POINT 3 IS THE HARD ONE AND IT IS NOT FUDGED. On a change-driven feed,
silence from the whole universe is ambiguous: ten quiet markets and a
dead socket look identical from the application side. The protocol's
`MarketMessage` union does include `Heartbeat`, so the stream now
registers a handler for it; where heartbeats actually arrive, silence
is resolved and a quiet book stays valid indefinitely. Where they do
NOT arrive -- because the venue does not send them, or the SDK does not
surface them -- silence beyond `FEED_SILENCE_MAX_S` is recorded as
LIVENESS_UNDETERMINED, which is a THIRD verdict and not a quiet
promotion to either of the other two. An undetermined instant is a
GAP: something we did not observe. It is never counted as a qualifying
second and never counted as a non-qualifying one.

BOOK SEMANTICS ARE STILL ASSUMED, AND SAYING SO IS PART OF THE OUTPUT.
`bettor_market_stream` records ASSUMED_FULL_REPLACEMENT: the payload
carries whole `bids`/`offers` arrays and the SDK declares no delta
type, which is consistent with full replacement and is not proof of
it. This module carries that status into every run report rather than
letting it fade.

Run:  python -m pytest backend/tests/test_bettor_incentive_feed.py
"""
from __future__ import annotations

import os
import threading
import time

FEED_VERSION = "BETTOR_INCENTIVE_FEED_V1"

# How long the CONNECTION may say nothing at all -- no frame on any
# slug, no heartbeat -- before this stops claiming to know it is alive.
# Generous, because it is a bound on the whole universe rather than on
# one market: with ten subscribed books, a full minute of total silence
# is already unusual.
FEED_SILENCE_MAX_S = 60.0
SILENCE_ENV = "BETTOR_INCENTIVE_FEED_SILENCE_S"

VALID = "RESEARCH_VALID"
G_DISCONNECTED = "GAP_DISCONNECTED"
G_NO_INITIAL = "GAP_NO_INITIAL_LADDER_THIS_EPOCH"
G_STALE_EPOCH = "GAP_FRAME_FROM_A_PREVIOUS_EPOCH"
G_LIVENESS = "GAP_LIVENESS_UNDETERMINED"
G_NOT_OPEN = "GAP_MARKET_NOT_OPEN"

INITIAL_LADDER = "INITIAL_LADDER"
UPDATE = "UPDATE"

# A market-date needs this fraction of its instants OBSERVED before it
# is usable as a research unit. It is a coverage rule and says nothing
# about whether anything was tradable.
MIN_OBSERVED_FRACTION = 0.95
OBSERVED_ENV = "BETTOR_INCENTIVE_MIN_OBSERVED"


def _f_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


class FeedHealth:
    """Connection epochs, initial ladders, and the two verdicts.

    Driven by the stream's own counters -- `epoch`, `connected`,
    `last_frame_at`, `reconnects` -- plus a book callback. It reads the
    stream; it does not reach into it.
    """

    def __init__(self, stream, *, silence_max_s: float | None = None) -> None:
        self.stream = stream
        self.silence_max_s = (_f_env(SILENCE_ENV, FEED_SILENCE_MAX_S)
                              if silence_max_s is None else silence_max_s)
        self._lock = threading.Lock()
        # (epoch, slug) -> {"first_at": t, "frames": n, "last_at": t}
        self._ladders: dict = {}
        self.epochs: list = []              # ordered connection epochs
        self._epoch_seen = None
        self.heartbeats_seen = 0
        self.last_heartbeat_at = None
        self.frames = 0
        self.resubscribe_batches = 0

    # ── ingest ───────────────────────────────────────────────────────

    def on_book(self, slug: str, rec: dict) -> dict:
        """Classify one frame: INITIAL LADDER for its epoch, or UPDATE.

        Returned rather than only recorded, so the journal writes the
        same classification this module reasons with.
        """
        now = rec.get("received_at") or time.time()
        epoch = rec.get("epoch")
        with self._lock:
            self.frames += 1
            self._note_epoch_locked(epoch, now)
            key = (epoch, slug)
            ent = self._ladders.get(key)
            if ent is None:
                self._ladders[key] = {"first_at": now, "frames": 1,
                                      "last_at": now}
                cls = INITIAL_LADDER
            else:
                ent["frames"] += 1
                ent["last_at"] = now
                cls = UPDATE
            n = self._ladders[key]["frames"]
        book = rec.get("book") or {}
        return {"slug": slug, "epoch": epoch, "ladder_class": cls,
                "ladder_seq": n, "received_at": now,
                "source_ts": rec.get("source_ts"),
                "venue_state": rec.get("state"),
                "bid_levels": len(book.get("bids") or []),
                "ask_levels": len(book.get("offers") or []),
                "replacement": rec.get("replacement")}

    def on_heartbeat(self, _msg=None) -> None:
        """The one signal that resolves silence. May never fire."""
        with self._lock:
            self.heartbeats_seen += 1
            self.last_heartbeat_at = time.time()

    def note_resubscribe(self, batches: int = 1) -> None:
        with self._lock:
            self.resubscribe_batches += int(batches)

    def _note_epoch_locked(self, epoch, now) -> None:
        if epoch == self._epoch_seen:
            return
        if self.epochs:
            self.epochs[-1]["closed_at"] = now
            self.epochs[-1]["close_inferred"] = (
                "closed by the arrival of a later epoch; the stream's own "
                "socket_closed_at is the authoritative close stamp")
        self.epochs.append({"epoch": epoch, "opened_at": now,
                            "closed_at": None, "frames": 0})
        self._epoch_seen = epoch

    # ── the two verdicts ─────────────────────────────────────────────

    def liveness(self, now: float | None = None) -> dict:
        """Is the CONNECTION demonstrably alive? Three answers, not two."""
        now = time.time() if now is None else now
        s = self.stream
        connected = bool(getattr(s, "connected", False))
        last_frame = getattr(s, "last_frame_at", None)
        with self._lock:
            last_hb = self.last_heartbeat_at
            hb_seen = self.heartbeats_seen
        # THE STREAM'S OWN COUNTER WINS WHERE IT HAS ONE. The listener
        # we registered may not have been accepted by the SDK, and the
        # stream records that separately; reading both means a heartbeat
        # that reached the transport but not our callback still counts
        # as evidence the socket is alive.
        s_hb = getattr(s, "heartbeats", 0) or 0
        if s_hb > hb_seen:
            hb_seen = s_hb
            last_hb = getattr(s, "last_heartbeat_at", None) or last_hb
        if not connected:
            return {"alive": False, "why": G_DISCONNECTED,
                    "connected": False, "heartbeats_seen": hb_seen}
        signals = [t for t in (last_frame, last_hb) if t is not None]
        quiet_for = (now - max(signals)) if signals else None
        if quiet_for is not None and quiet_for <= self.silence_max_s:
            return {"alive": True, "why": "ALIVE", "connected": True,
                    "quiet_for_s": round(quiet_for, 4),
                    "heartbeats_seen": hb_seen}
        # SILENT FOR LONGER THAN THE BOUND. With heartbeats this means
        # the connection is in trouble; WITHOUT them it means we cannot
        # tell a quiet universe from a dead socket, and that is what is
        # reported -- not a guess in either direction.
        return {"alive": False, "why": G_LIVENESS, "connected": True,
                "quiet_for_s": round(quiet_for, 4) if quiet_for is not None
                else None,
                "heartbeats_seen": hb_seen,
                "heartbeat_available": hb_seen > 0,
                "detail": ("silence beyond %.0fs with %s heartbeat evidence; "
                           "a quiet universe and a dead socket are not "
                           "distinguishable here"
                           % (self.silence_max_s,
                              "no" if hb_seen == 0 else "prior"))}

    def sample(self, slug: str, *, now: float | None = None,
               max_source_age_s: float, max_receipt_age_s: float) -> dict:
        """One scoring instant for one market: BOTH verdicts, side by side.

        The trading answer comes from `book_at()` and is copied through
        without interpretation. The research answer is computed here
        and NEVER consults book age.
        """
        now = time.time() if now is None else now
        s = self.stream
        # The trading gate, verbatim. Not relaxed, not re-derived.
        trading = s.book_at(slug, max_source_age_s=max_source_age_s,
                            max_receipt_age_s=max_receipt_age_s)
        epoch = getattr(s, "epoch", None)
        live = self.liveness(now)
        with self._lock:
            ent = self._ladders.get((epoch, slug))
            ladder_seq = ent["frames"] if ent else 0
            ladder_first = ent["first_at"] if ent else None
            ladder_last = ent["last_at"] if ent else None

        if not live["alive"]:
            why = live["why"]
        elif ent is None:
            # The connection is up but this market has not spoken since
            # the reconnect, so we hold no ladder we may trust for it.
            why = G_NO_INITIAL
        elif trading.get("venue_state") not in (None, "MARKET_STATE_OPEN"):
            why = G_NOT_OPEN
        else:
            why = VALID

        return {
            "slug": slug, "at": now, "epoch": epoch,
            # ── research ──
            "research_valid": why == VALID,
            "research_why": why,
            "ladder_seq": ladder_seq,
            "initial_ladder_at": ladder_first,
            "last_frame_at": ladder_last,
            # REPORTED, NEVER USED AS A RESEARCH GATE. A large value
            # here on a valid sample is a quiet market, which is a
            # measurement and not a defect.
            "book_age_s": (round(now - ladder_last, 4)
                           if ladder_last is not None else None),
            "feed": live,
            # ── trading, verbatim ──
            "trading_eligible": bool(trading.get("eligible")),
            "trading_reason": trading.get("reason"),
            "venue_state": trading.get("venue_state"),
            "source_ts": trading.get("source_ts"),
            "book": trading.get("book"),
            "verdicts_are_separate": (
                "research validity is about connection continuity; "
                "trading eligibility is about data age. Neither is "
                "derived from the other."),
        }

    def report(self) -> dict:
        with self._lock:
            epochs = [dict(e) for e in self.epochs]
            ladders = {"%s@%s" % (slug, ep): dict(v)
                       for (ep, slug), v in self._ladders.items()}
            hb = self.heartbeats_seen
            resub = self.resubscribe_batches
        s = self.stream
        return {
            "feed": FEED_VERSION,
            "epochs": epochs, "epoch_count": len(epochs),
            "current_epoch": getattr(s, "epoch", None),
            "connected": bool(getattr(s, "connected", False)),
            "reconnects": getattr(s, "reconnects", None),
            "resubscribe_batches": resub,
            "frames": self.frames,
            "heartbeats_seen": hb,
            "heartbeat_evidence": ("OBSERVED" if hb else "NONE OBSERVED -- "
                                   "connection silence is therefore "
                                   "ambiguous and is reported as "
                                   "LIVENESS_UNDETERMINED"),
            "initial_ladders": ladders,
            "book_semantics": "ASSUMED_FULL_REPLACEMENT -- the payload "
                              "carries whole bids/offers arrays and the SDK "
                              "declares no delta type. Consistent with full "
                              "replacement; not proof of it.",
            "silence_max_s": self.silence_max_s,
        }


# ── coverage, and what a gap does to a reward estimate ───────────────

COVER_OK = "COVERAGE_MET"
COVER_SHORT = "COVERAGE_BELOW_THRESHOLD"


def coverage_verdict(*, instants_expected: int, instants_valid: int,
                     gaps_by_reason: dict | None = None,
                     min_observed: float | None = None) -> dict:
    """Whether a market-date is usable as a RESEARCH unit.

    THIS IS NOT A TRADING VERDICT AND MUST NOT BE READ AS ONE. A
    market-date voided here was not refused by any rule about what may
    be traded; it is a market we did not watch well enough to score.
    The two live in separate fields for the whole run and are never
    summed, compared or substituted.

    WHAT A GAP DOES TO THE REWARD ESTIMATE. A gap is an UNOBSERVED
    interval. We do not know whether the side qualified during it, so
    the estimate covers the OBSERVED instants only and is a statement
    about those. It is NOT divided by the observed fraction to
    "recover" a full day: scaling assumes the unobserved interval
    resembled the observed one, which is exactly the assumption a gap
    destroys -- disconnects cluster with venue trouble, and venue
    trouble is when books thin out. So the observed figure is reported
    as a figure FOR THE OBSERVED PORTION, with the fraction beside it,
    and a full-day payment is never inferred from a partial day.
    """
    thr = (_f_env(OBSERVED_ENV, MIN_OBSERVED_FRACTION)
           if min_observed is None else min_observed)
    frac = (instants_valid / instants_expected) if instants_expected else 0.0
    ok = frac >= thr
    return {
        "instants_expected": instants_expected,
        "instants_valid": instants_valid,
        "instants_gapped": max(0, instants_expected - instants_valid),
        "observed_fraction": round(frac, 6),
        "min_observed_fraction": thr,
        "research_coverage": COVER_OK if ok else COVER_SHORT,
        "usable_as_research_unit": ok,
        # SAID IN WORDS ON EVERY UNIT, so no downstream table can quote
        # the number without the caveat.
        "reward_scope": (
            "any reward figure for this market-date covers the %d OBSERVED "
            "instants (%.4f of the window). It is NOT scaled to the full "
            "window and is NOT a full-day payment estimate."
            % (instants_valid, frac)),
        "gaps_by_reason": dict(gaps_by_reason or {}),
        "not_a_trading_verdict": (
            "research coverage is independent of trading eligibility; "
            "neither field may be substituted for the other"),
    }


def describe() -> dict:
    return {
        "feed": FEED_VERSION,
        "separates": ["research_validity (connection continuity)",
                      "trading_eligibility (data age, via book_at)"],
        "research_validity_ignores_book_age": True,
        "trading_gate_unchanged": "bettor_market_stream.book_at",
        "silence_max_s": FEED_SILENCE_MAX_S,
        "third_verdict": G_LIVENESS,
        "min_observed_fraction": MIN_OBSERVED_FRACTION,
        "gaps_are_not_extrapolated": True,
    }
