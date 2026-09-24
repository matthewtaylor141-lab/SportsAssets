"""PROGRESS PROVIDERS — the integration, prepared, and the access it needs.

`bettor_progress_feed` validates, ages and orders progress observations.
Nothing produces any. This module is the missing half: the adapter layer
that turns a provider's payload into observations that module accepts, plus
a statement precise enough to act on of what access is required.

WHAT WAS SEARCHED, AND FOUND WANTING (measured, 2026-09-24):

- **The odds provider's scores endpoint** (TheOddsAPI v4 `/scores`) answered
  HTTP 200 twice, at 02:08:18Z and 02:33:10Z, with `progress_fields []` and
  `carries_observed_period false`. It returns scores and completion, and no
  period, quarter, inning or clock. Scores alone cannot locate halftime:
  0-0 is the same string in minute 5 and minute 80.
- **The venue's own market data** (`pmus.book_read` -> `marketData`) carries
  quotes, ladders and `stats.sharesTraded`. There is no event-state object
  and no period field anywhere in it, so `venue_event_state` -- an allowed
  source -- has nothing behind it here.
- **`game_start_time`**, which we do hold, is a SCHEDULED start. Deriving a
  period from it is refused by source name in `bettor_progress_feed`, and
  that refusal is the point: injury time, stoppages and a long halftime all
  make the derivation wrong while it still looks like real data.

So there is no accessible source today, and this module does not pretend
otherwise. It refuses with `NO_PROGRESS_PROVIDER_CONFIGURED` until one is
configured, and the refusal names the capability rather than the vendor.

THE EXACT CAPABILITY REQUIRED (the smallest thing that unblocks the
second-half loss exit for soccer):

    for each in-play fixture, a record carrying
      1. the fixture identified by the two team names or a stable id we can
         map, WITH kickoff date -- enough for `bettor_venue_mapping`;
      2. an OBSERVED period or half indicator (1 or 2 for soccer), taken
         from the match, not computed from kickoff;
      3. the provider's own timestamp for WHEN that was true, to the
         second. Our receipt time is not a substitute: it makes every
         observation look fresh by construction;
      4. a status distinguishing in-play from halftime, suspended,
         abandoned and final -- four different reasons not to sell;
      5. refreshed at least every 60 s while in play, because
         `bettor_progress_feed.MAX_AGE_S` is 120 s and an observation older
         than that does not license an exit.

    Not required: a clock, possession, lineups, odds, or xG.

Any provider meeting 1-5 connects by setting `PROGRESS_PROVIDER` to a
registered adapter name and its credential env var. The adapters below are
written against the two response shapes that actually occur, so connecting
one is configuration rather than development.
"""
from __future__ import annotations

import os
import time

from . import bettor_progress_feed as feed

#: The env var naming which adapter to use. Absent means refuse -- never a
#: default provider, because a wrong default is a silent wrong exit.
ENV_PROVIDER = "PROGRESS_PROVIDER"

R_NOT_CONFIGURED = "NO_PROGRESS_PROVIDER_CONFIGURED"
R_UNKNOWN_ADAPTER = "PROGRESS_ADAPTER_NOT_REGISTERED"
R_NO_CREDENTIAL = "PROGRESS_PROVIDER_CREDENTIAL_ABSENT"
R_NO_PERIOD_IN_PAYLOAD = "PROVIDER_PAYLOAD_CARRIES_NO_OBSERVED_PERIOD"
R_NO_PROVIDER_TIMESTAMP = "PROVIDER_PAYLOAD_CARRIES_NO_OBSERVED_AT"

#: What every adapter must produce, restated so an adapter author cannot
#: miss it: the five requirements above, as data.
CAPABILITY = (
    "fixture identity mappable to a venue contract",
    "an OBSERVED period or half indicator, not derived from kickoff",
    "the provider's own timestamp for when that was true",
    "a status separating in-play from break, suspended, abandoned, final",
    "a refresh at least every %ds while in play" % int(feed.MAX_AGE_S / 2),
)

#: Soccer is the sport this unblocks. The halfway rule is written for
#: basketball and football too, so an adapter that carries their period
#: indices connects them as well, with no change here.
SPORTS_THIS_WOULD_UNBLOCK = ("soccer", "basketball", "football")

# Status vocabularies seen in the wild, mapped to ours. A value NOT in the
# map is left unmapped and refused as MALFORMED by the feed rather than
# guessed at -- an unknown status must never read as IN_PLAY.
_STATUS_WORDS = {
    "inplay": feed.IN_PLAY, "in_play": feed.IN_PLAY, "live": feed.IN_PLAY,
    "1h": feed.IN_PLAY, "2h": feed.IN_PLAY, "playing": feed.IN_PLAY,
    "ht": feed.BREAK, "halftime": feed.BREAK, "half_time": feed.BREAK,
    "break": feed.BREAK, "interval": feed.BREAK,
    "suspended": feed.SUSPENDED, "interrupted": feed.SUSPENDED,
    "postponed": feed.SUSPENDED, "delayed": feed.SUSPENDED,
    "abandoned": feed.ABANDONED, "cancelled": feed.ABANDONED,
    "canceled": feed.ABANDONED, "awarded": feed.ABANDONED,
    "ft": feed.FINAL, "final": feed.FINAL, "finished": feed.FINAL,
    "ended": feed.FINAL, "aet": feed.FINAL, "pen": feed.FINAL,
}


def map_status(raw) -> str | None:
    """Their word for ours, or None. None is refused, never assumed."""
    return _STATUS_WORDS.get(str(raw or "").strip().lower().replace(" ", ""))


def _first(d: dict, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return None


def adapt_period_and_status(row: dict) -> dict:
    """The shape-independent core: pull period, status and the stamp.

    Refuses by name when the payload lacks an observed period or the
    provider's own timestamp, because those are the two things that make a
    progress observation usable and the two things every payload measured
    so far has been missing.
    """
    status = map_status(_first(row, "status", "state", "phase",
                               "match_status", "short_status"))
    period = _first(row, "period", "half", "quarter", "inning",
                    "period_number", "current_period")
    observed_at = _first(row, "observed_at", "as_of", "updated_at",
                         "timestamp", "last_updated")
    if period is None:
        return {"ok": False, "refusal": R_NO_PERIOD_IN_PAYLOAD,
                "why": ("the payload carries no period/half/quarter/inning. "
                        "Scores and elapsed minutes are not substitutes: "
                        "the first cannot locate halftime and the second "
                        "is refused by source")}
    if observed_at is None:
        return {"ok": False, "refusal": R_NO_PROVIDER_TIMESTAMP,
                "why": ("the payload carries no provider timestamp. Using "
                        "our receipt time would make every observation "
                        "fresh by construction")}
    return {"ok": True, "refusal": None,
            "period": period, "status": status, "observed_at": observed_at}


def to_observation(row: dict, *, sport: str, event_key: str,
                   source: str = "licensed_scores_feed",
                   ingested_at=None) -> dict:
    """One provider row -> one observation, or a named refusal.

    The observation is handed to `bettor_progress_feed.validate`, which is
    the only thing that decides whether it is admissible. This function
    never widens that: an unmapped status stays unmapped so the feed
    refuses it, rather than being rounded to IN_PLAY here.
    """
    core = adapt_period_and_status(row)
    if not core["ok"]:
        return {"ok": False, "refusal": core["refusal"], "why": core["why"],
                "observation": None}
    obs = {"event_key": event_key, "sport": sport, "source": source,
           "observed_at": core["observed_at"],
           "ingested_at": ingested_at if ingested_at is not None
           else time.time(),
           "status": core["status"],
           "period": core["period"],
           "period_type": "HALF" if sport == "soccer" else "PERIOD",
           "revision": row.get("revision") or 0}
    checked = feed.validate(obs)
    if not checked["ok"]:
        return {"ok": False, "refusal": checked["refusal"],
                "why": checked["why"], "observation": None}
    return {"ok": True, "refusal": None, "why": checked["why"],
            "observation": checked["normalized"]}


#: name -> {credential env var, docstring}. Registering an adapter does NOT
#: connect it: `PROGRESS_PROVIDER` must also name it, and the credential
#: must be present. Two registrations, because the two shapes below are the
#: ones real scores APIs use; both go through `to_observation`.
ADAPTERS = {
    "generic_scores_v1": {
        "credential_env": "PROGRESS_PROVIDER_KEY",
        "shape": ("a flat row per fixture with status/period/as_of, e.g. "
                  "{'status': 'inplay', 'period': 2, 'as_of': <iso>}"),
        "source": "licensed_scores_feed",
    },
    "official_scoreboard_v1": {
        "credential_env": "SCOREBOARD_PROVIDER_KEY",
        "shape": ("a league scoreboard row, e.g. {'state': '2H', "
                  "'current_period': 2, 'updated_at': <iso>}"),
        "source": "official_scoreboard",
    },
}


def configured() -> dict:
    """Which provider is connected, or the named reason none is.

    Absence is never permission: with no provider configured this refuses,
    and `bettor_rn1x_policy.PROGRESS_FEED_CONNECTED` stays empty, so the
    second-half exit stays UNAVAILABLE rather than quietly falling back to
    something derived.
    """
    name = (os.getenv(ENV_PROVIDER) or "").strip()
    out = {"provider": name or None, "connected": False,
           "adapters_available": sorted(ADAPTERS),
           "capability_required": list(CAPABILITY),
           "would_unblock": list(SPORTS_THIS_WOULD_UNBLOCK)}
    if not name:
        out.update(refusal=R_NOT_CONFIGURED,
                   why=("no progress provider is configured. The exit stays "
                        "UNAVAILABLE by design: the alternative is deriving "
                        "a period from a scheduled start, which is refused "
                        "by source name"))
        return out
    spec = ADAPTERS.get(name)
    if spec is None:
        out.update(refusal=R_UNKNOWN_ADAPTER,
                   why=("%r is not a registered adapter. Registered: %s"
                        % (name, ", ".join(sorted(ADAPTERS)))))
        return out
    out["credential_env"] = spec["credential_env"]
    out["source"] = spec["source"]
    if not (os.getenv(spec["credential_env"]) or "").strip():
        out.update(refusal=R_NO_CREDENTIAL,
                   why=("adapter %s is selected and %s is not set on this "
                        "service" % (name, spec["credential_env"])))
        return out
    out.update(connected=True, refusal=None,
               why=("adapter %s is selected and its credential is present. "
                    "Observations still pass bettor_progress_feed.validate "
                    "and the 120 s staleness bound before any exit" % name))
    return out


def describe() -> dict:
    return {
        "module": "bettor_progress_providers",
        "purpose": ("the adapter layer between a scores/state provider and "
                    "bettor_progress_feed. Prepared, not connected"),
        "configured": configured(),
        "searched_and_unavailable": {
            "odds_provider_scores_endpoint": (
                "HTTP 200, progress_fields [], carries_observed_period "
                "false, measured 02:08:18Z and 02:33:10Z"),
            "venue_market_data": (
                "quotes, ladders and sharesTraded only; no event-state "
                "object and no period field"),
            "game_start_time": (
                "a SCHEDULED start. Refused by source name, deliberately"),
        },
        "refusals": (R_NOT_CONFIGURED, R_UNKNOWN_ADAPTER, R_NO_CREDENTIAL,
                     R_NO_PERIOD_IN_PAYLOAD, R_NO_PROVIDER_TIMESTAMP),
    }
