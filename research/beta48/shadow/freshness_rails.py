"""Section E. Content freshness, which is not socket liveness.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE FAILURE THIS CATCHES
------------------------
A WebSocket that is delivering messages looks healthy. Heartbeats arrive, the
connection is up, no error is raised -- and the book has not changed in four
minutes because the upstream publisher wedged.

    MESSAGES ARRIVING  !=  QUOTES ARE FRESH

A maker quoting against a frozen book is quoting against the past, and the
first thing that arrives when the feed unwedges is somebody else's fill at a
stale price. So four clocks are tracked separately and never collapsed:

    MESSAGE_RECEIPT_TIME        when OUR process received bytes
    VENUE_STATE_TIME            when the VENUE says that state was true
    BOOK_CONTENT_HASH           what the book actually contained
    LAST_MATERIAL_CONTENT_CHANGE  when that content last MEANINGFULLY moved

Heartbeat traffic advances the first and nothing else. That gap is the signal.
"""

import datetime
import hashlib
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"

HEARTBEAT_IS_NOT_FRESHNESS = (
    "never treat heartbeat traffic as evidence that quotes are fresh. A "
    "heartbeat proves the transport works; it says nothing about whether the "
    "publisher behind it is still producing state")

CLOCKS = ("MESSAGE_RECEIPT_TIME", "VENUE_STATE_TIME", "BOOK_CONTENT_HASH",
          "LAST_MATERIAL_CONTENT_CHANGE")

CLOCKS_ARE_NEVER_COLLAPSED = (
    "message receipt, venue state time and content change are three different "
    "facts. Collapsing them into one 'last update' field is how a frozen book "
    "reads as a live one")

# Declared before any feed exists, so the rail cannot be tuned to a symptom.
CONTENT_STALE_AFTER_S = 30.0
VENUE_STATE_LAG_ALARM_S = 15.0
MATERIAL_CHANGE_MIN_TICKS = 1


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def content_hash(book):
    """A hash of what the book CONTAINS -- never of when it arrived.

    Timestamps are deliberately excluded: including one would make every
    message look like new content, which is precisely the bug.
    """
    if book is None:
        return NOT_IDENTIFIED
    material = {k: v for k, v in dict(book).items()
                if k not in ("RECEIPT_UTC", "MESSAGE_RECEIPT_TIME",
                             "SEQ", "seq", "HEARTBEAT", "TIMESTAMP")}
    return hashlib.sha256(json.dumps(material, sort_keys=True,
                                     default=str).encode()).hexdigest()


def is_material(prev_book, book, min_ticks=MATERIAL_CHANGE_MIN_TICKS,
                tick=0.01):
    """Did the content MEANINGFULLY move, or merely jitter?

    A size wobble deep in the book is a change; it is not necessarily a
    material one for a maker at the touch. Touch price movement is.
    """
    if prev_book is None or book is None:
        return True
    if content_hash(prev_book) == content_hash(book):
        return False
    for side in ("BID", "ASK"):
        a, b = prev_book.get(side), book.get(side)
        try:
            if abs(float(a) - float(b)) >= min_ticks * tick - 1e-12:
                return True
        except (TypeError, ValueError):
            return True          # unreadable is material, not ignorable
    return False


class FreshnessTracker:
    """Four clocks, advanced independently. Fails toward STALE."""

    def __init__(self, content_stale_after_s=CONTENT_STALE_AFTER_S,
                 venue_lag_alarm_s=VENUE_STATE_LAG_ALARM_S, tick=0.01):
        self.content_stale_after_s = float(content_stale_after_s)
        self.venue_lag_alarm_s = float(venue_lag_alarm_s)
        self.tick = float(tick)
        self.message_receipt_time = None
        self.venue_state_time = None
        self.book_content_hash = NOT_IDENTIFIED
        self.last_material_content_change = None
        self.messages = 0
        self.heartbeats = 0
        self.material_changes = 0
        self._prev = None

    def on_message(self, receipt_time, venue_state_time=None, book=None,
                   heartbeat=False):
        """One inbound message. A heartbeat advances RECEIPT ONLY."""
        t = _parse(receipt_time)
        self.messages += 1
        if t is not None:
            self.message_receipt_time = t
        if heartbeat or book is None:
            self.heartbeats += 1
            return self.state(now=t)
        v = _parse(venue_state_time)
        if v is not None:
            self.venue_state_time = v
        h = content_hash(book)
        if is_material(self._prev, book, tick=self.tick):
            self.material_changes += 1
            self.last_material_content_change = t or v
        self.book_content_hash = h
        self._prev = dict(book)
        return self.state(now=t)

    def state(self, now=None):
        now = _parse(now) or self.message_receipt_time
        since_content = None
        if now is not None and self.last_material_content_change is not None:
            since_content = (now - self.last_material_content_change
                             ).total_seconds()
        venue_lag = None
        if now is not None and self.venue_state_time is not None:
            venue_lag = (now - self.venue_state_time).total_seconds()

        # Fail toward stale: never having seen content is not freshness.
        if since_content is None:
            stale = True
            why = "NO_MATERIAL_CONTENT_CHANGE_EVER_OBSERVED"
        elif since_content > self.content_stale_after_s:
            stale = True
            why = ("CONTENT_UNCHANGED_FOR_%.1fS_WHILE_MESSAGES_CONTINUED"
                   % since_content)
        else:
            stale = False
            why = None
        return {
            "MESSAGE_RECEIPT_TIME": (self.message_receipt_time.isoformat()
                                     if self.message_receipt_time
                                     else NOT_IDENTIFIED),
            "VENUE_STATE_TIME": (self.venue_state_time.isoformat()
                                 if self.venue_state_time else NOT_IDENTIFIED),
            "BOOK_CONTENT_HASH": self.book_content_hash,
            "LAST_MATERIAL_CONTENT_CHANGE": (
                self.last_material_content_change.isoformat()
                if self.last_material_content_change else NOT_IDENTIFIED),
            "SECONDS_SINCE_MATERIAL_CHANGE": (
                round(since_content, 3) if since_content is not None
                else NOT_IDENTIFIED),
            "VENUE_STATE_LAG_S": (round(venue_lag, 3)
                                  if venue_lag is not None else NOT_IDENTIFIED),
            "VENUE_STATE_LAG_ALARM": (venue_lag is not None
                                      and venue_lag > self.venue_lag_alarm_s),
            "MESSAGES": self.messages,
            "HEARTBEATS": self.heartbeats,
            "MATERIAL_CHANGES": self.material_changes,
            "FEED_CONTENT_STALE": stale,
            "WHY_STALE": why,
            "QUOTING_PERMITTED": not stale,
            "HEARTBEAT_IS_NOT_FRESHNESS": HEARTBEAT_IS_NOT_FRESHNESS,
        }


def describe():
    return {
        "CLOCKS": CLOCKS,
        "CLOCKS_ARE_NEVER_COLLAPSED": CLOCKS_ARE_NEVER_COLLAPSED,
        "HEARTBEAT_IS_NOT_FRESHNESS": HEARTBEAT_IS_NOT_FRESHNESS,
        "CONTENT_STALE_AFTER_S": CONTENT_STALE_AFTER_S,
        "VENUE_STATE_LAG_ALARM_S": VENUE_STATE_LAG_ALARM_S,
        "FAILS_TOWARD_STALE": True,
    }
