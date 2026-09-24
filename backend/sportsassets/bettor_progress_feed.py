"""EVENT-PROGRESS OBSERVATIONS: the store, the freshness rule, and the
four ways an observation can be untrustworthy.

WHAT THIS IS NOT. It is not a feed. It does not fetch anything. Adding a
sport to `bettor_rn1x_policy.PROGRESS_FEED_CONNECTED` is what admits it to
the complete-policy experiment, and that registry stays empty until a real
provider writes rows here. This module is the layer between such a
provider and the frozen policy: ingest, supersede, age out, and refuse.

WHY THERE IS NO PROVIDER YET, precisely. One licensed sports-data
integration exists in this repository:

    edge-engine/src/edge/shadow/fsc_scores.py
        -> https://api.the-odds-api.com/v4/sports/{key}/scores
        -> key EDGE_ODDS_API_KEY, quota-guarded, 120 s cadence,
           600 s freshness floor, fail-soft with a stats dict

It reads exactly three things off each event: per-side `score`,
`completed`, and `last_update`. For TENNIS the per-side score IS a period
count (sets), which is why that sleeve can verify a set loss. For the
sports the RN1 policy has halfway rules for -- soccer, basketball,
football -- the per-side score is goals or points, which is not a period
index. A goal total does not say which half you are in.

And the credential is not where the policy runs. `env-keys` on
sportsassets-api, read 2026-09-23T22:10:54Z, lists no odds-feed key at
all: PMUS_*, VAPID_*, REDIS_URL, DATABASE_URL, the operator flags, and
nothing else. EDGE_ODDS_API_KEY is provisioned on the edge service.
Copying it here would be moving a production credential between services,
which is refused, so connecting it is an owner action and not mine.

THE FOUR REFUSALS, each a different fact with a different remedy:

  STALE          an observation older than MAX_AGE_S. A period index from
                 nine minutes ago cannot license an exit now; play moves.
  SUPERSEDED     a later observation exists for the same event. Feeds
                 correct themselves -- a period is re-stated, a
                 disallowed goal is reversed -- so the newest wins and a
                 correction arriving with the SAME observed_at wins on
                 its ingest order.
  NOT_IN_PLAY    halftime, a stoppage, a suspension, an abandonment. The
                 policy already refuses to read a phase here; this module
                 keeps the distinct statuses instead of flattening them,
                 because "halftime" and "abandoned" need different
                 handling even though both stop the clock.
  DERIVED        an observation whose source is a scheduled start plus
                 wall-clock arithmetic. Refused by source, not by value:
                 that is the inference §3 forbids, and it is the one
                 failure mode that would look exactly like real data.

OVERTIME is not a refusal and is not handled here: the policy's own rule
maps `period > total_periods` to SECOND_HALF, which is unambiguous.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ── what may write a progress observation ───────────────────────────
#
# An allow-list, because the dangerous case is not a missing source but a
# plausible one. `game_start` plus elapsed time produces a period index
# that is correctly typed, correctly timestamped and wrong whenever there
# is injury time, a stoppage or a long halftime.
OBSERVED_SOURCES = (
    "venue_event_state",     # the trading venue's own event record
    "licensed_scores_feed",  # a provider's scores/state endpoint
    "official_scoreboard",   # the league's own published state
)
DERIVED_SOURCES = (
    "game_start_plus_elapsed",
    "wall_clock",
    "inferred_from_price",
)

# ── event status, kept distinct rather than collapsed to a boolean ───
IN_PLAY = "IN_PLAY"
BREAK = "HALFTIME_OR_BREAK"
SUSPENDED = "SUSPENDED"
ABANDONED = "ABANDONED"
FINAL = "FINAL"
STATUSES = (IN_PLAY, BREAK, SUSPENDED, ABANDONED, FINAL)
# Only one of them is play. The other four are different reasons for the
# same refusal and the difference is reportable.
IN_PLAY_STATUSES = (IN_PLAY,)

# A period index from this long ago no longer licenses an exit. Deliberately
# tighter than the tennis sleeve's 600 s: that module confirms a set score
# that cannot un-happen, while this one gates a sale.
MAX_AGE_S = 120.0

STALE = "PROGRESS_OBSERVATION_STALE"
SUPERSEDED = "PROGRESS_OBSERVATION_SUPERSEDED"
NOT_IN_PLAY = "EVENT_NOT_IN_PLAY"
DERIVED = "PROGRESS_SOURCE_IS_DERIVED_NOT_OBSERVED"
MALFORMED = "PROGRESS_OBSERVATION_MALFORMED"

REFUSALS = (STALE, SUPERSEDED, NOT_IN_PLAY, DERIVED, MALFORMED)


def _ts(value) -> float | None:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate(obs: dict) -> dict:
    """Shape and provenance only -- never freshness, which depends on now.

    Returns {'ok': bool, 'refusal': str|None, 'why': str, 'normalized': d}.
    """
    if not isinstance(obs, dict):
        return {"ok": False, "refusal": MALFORMED,
                "why": "a progress observation must be a mapping",
                "normalized": None}
    source = str(obs.get("source") or "")
    if source in DERIVED_SOURCES:
        return {"ok": False, "refusal": DERIVED,
                "why": ("source %r derives the period from a scheduled "
                        "start or a price rather than observing it. This "
                        "is refused by SOURCE, not by value: injury time, "
                        "stoppages and halftime all make it wrong while it "
                        "still looks like real data" % (source,)),
                "normalized": None}
    if source not in OBSERVED_SOURCES:
        return {"ok": False, "refusal": MALFORMED,
                "why": ("source %r is not an observed-progress source. "
                        "Allowed: %s" % (source, ", ".join(OBSERVED_SOURCES))),
                "normalized": None}
    observed_at = _ts(obs.get("observed_at"))
    if observed_at is None:
        return {"ok": False, "refusal": MALFORMED,
                "why": "observed_at is absent or unparseable; an "
                       "observation without a time cannot be aged",
                "normalized": None}
    status = str(obs.get("status") or "")
    if status not in STATUSES:
        return {"ok": False, "refusal": MALFORMED,
                "why": ("status %r is not one of %s" % (status, STATUSES)),
                "normalized": None}
    period = obs.get("period")
    if period is not None:
        try:
            period = int(period)
        except (TypeError, ValueError):
            return {"ok": False, "refusal": MALFORMED,
                    "why": "period must be an integer when present",
                    "normalized": None}
    return {"ok": True, "refusal": None, "why": "observed-source progress",
            "normalized": {
                "event_key": str(obs.get("event_key") or ""),
                "sport": str(obs.get("sport") or ""),
                "source": source,
                "observed_at": observed_at,
                "ingested_at": _ts(obs.get("ingested_at")) or observed_at,
                "revision": int(obs.get("revision") or 0),
                "status": status,
                "period": period,
                "period_type": str(obs.get("period_type") or ""),
                # `in_play` is DERIVED from status so the two can never
                # disagree. A feed that says SUSPENDED and in_play=True is
                # reporting one fact twice and getting it wrong once.
                "in_play": status in IN_PLAY_STATUSES}}


def normalize_all(observations, *, event_key=None) -> tuple[list, list]:
    """Every row through `validate`, always. Returns (accepted, refused).

    EVERY READER NORMALIZES. An earlier draft had `usable` order raw rows
    directly, which broke two ways at once: `in_play` was never derived
    from `status`, so a perfectly good in-play row read as not-in-play;
    and a `wall_clock` row handed straight to `usable` skipped the
    provenance refusal entirely. The database CHECK would have caught the
    second on the way in, but a reader that is only safe because of the
    table it happened to read from is not safe.
    """
    accepted, refused = [], []
    for o in observations or []:
        if not isinstance(o, dict):
            refused.append({"refusal": MALFORMED, "row": o})
            continue
        if event_key is not None and str(o.get("event_key") or "") != event_key:
            continue
        v = validate(o)
        if v["ok"]:
            accepted.append(v["normalized"])
        else:
            refused.append({"refusal": v["refusal"], "why": v["why"]})
    return accepted, refused


def latest(observations, *, event_key=None) -> dict | None:
    """The one observation that stands, with corrections applied.

    Newest `observed_at` wins. A correction restating the SAME instant
    wins on (revision, ingested_at) -- a feed that re-sends a corrected
    period keeps the original's observed_at, so ordering by observed_at
    alone would be a coin toss between the wrong value and the right one.
    """
    accepted, _ = normalize_all(observations, event_key=event_key)
    if not accepted:
        return None
    return max(accepted, key=lambda r: (float(r["observed_at"]),
                                        int(r.get("revision") or 0),
                                        float(r.get("ingested_at") or 0.0)))


def usable(observations, *, now, event_key=None,
           max_age_s: float = MAX_AGE_S) -> dict:
    """The progress a decision may read, or the named reason it may not.

    On success `progress` carries exactly the policy's PROGRESS_FIELDS and
    nothing else, so a rule cannot accidentally reach for a field the
    contract does not promise -- an elapsed figure above all.
    """
    accepted, refused = normalize_all(observations, event_key=event_key)
    out = {"event_key": event_key, "now": float(now),
           "max_age_s": float(max_age_s),
           "refused_on_ingest": [r["refusal"] for r in refused]}
    row = (max(accepted, key=lambda r: (float(r["observed_at"]),
                                        int(r.get("revision") or 0),
                                        float(r.get("ingested_at") or 0.0)))
           if accepted else None)
    if row is None:
        # A row that existed but did not survive validation is NOT the same
        # fact as no row at all, and the remedy differs: fix the provider
        # versus wait for one.
        out.update(ok=False,
                   refusal=(refused[0]["refusal"] if refused
                            else "PROGRESS_OBSERVATION_MISSING_NOW"),
                   progress=None,
                   why=(refused[0].get("why") if refused
                        else "no observation exists for this event"))
        return out
    age = float(now) - float(row["observed_at"])
    out.update(observed_at=float(row["observed_at"]), age_s=age,
               status=row.get("status"), source=row.get("source"),
               superseded_count=max(0, len(accepted) - 1))
    if age > float(max_age_s):
        out.update(ok=False, refusal=STALE, progress=None,
                   why=("the newest observation is %.1f s old against a "
                        "%.0f s limit. A period index from that long ago "
                        "cannot license an exit now" % (age, max_age_s)))
        return out
    if age < 0:
        out.update(ok=False, refusal=MALFORMED, progress=None,
                   why=("the observation is stamped %.1f s in the future; "
                        "a clock disagreement is not freshness" % (-age,)))
        return out
    if not row.get("in_play"):
        out.update(ok=False, refusal=NOT_IN_PLAY, progress=None,
                   why=("status %r is not play. The exposure is retained "
                        "visibly rather than exited during a %s"
                        % (row.get("status"), row.get("status"))))
        return out
    out.update(ok=True, refusal=None,
               progress={"observed_at": float(row["observed_at"]),
                         "period": row.get("period"),
                         "period_type": row.get("period_type"),
                         "in_play": True},
               why="fresh, in play, observed source, corrections applied")
    return out


# ── persistence ─────────────────────────────────────────────────────

INSERT = """
    INSERT INTO rn1x_event_progress
        (event_key, sport, source, observed_at, ingested_at, revision,
         status, period, period_type, in_play)
    VALUES ($1,$2,$3,to_timestamp($4),to_timestamp($5),$6,$7,$8,$9,$10)
    ON CONFLICT (event_key, source, observed_at, revision) DO NOTHING
"""

SELECT_FOR_EVENT = """
    SELECT event_key, sport, source,
           extract(epoch FROM observed_at) AS observed_at,
           extract(epoch FROM ingested_at) AS ingested_at,
           revision, status, period, period_type, in_play
      FROM rn1x_event_progress
     WHERE event_key = $1
     ORDER BY observed_at DESC, revision DESC, ingested_at DESC
     LIMIT $2
"""


async def record(conn, obs: dict) -> dict:
    """Validate then store. A refused observation is NEVER stored: a row
    in this table is a claim the policy may read, so an unusable one
    belongs in the caller's log, not here."""
    v = validate(obs)
    if not v["ok"]:
        return v
    n = v["normalized"]
    await conn.execute(INSERT, n["event_key"], n["sport"], n["source"],
                       n["observed_at"], n["ingested_at"], n["revision"],
                       n["status"], n["period"], n["period_type"],
                       n["in_play"])
    return v


async def read_usable(conn, event_key: str, *, now,
                      limit: int = 20, max_age_s: float = MAX_AGE_S) -> dict:
    rows = [dict(r) for r in
            await conn.fetch(SELECT_FOR_EVENT, event_key, int(limit))]
    for r in rows:
        r["observed_at"] = float(r["observed_at"])
        r["ingested_at"] = float(r["ingested_at"] or r["observed_at"])
    return usable(rows, now=now, event_key=event_key, max_age_s=max_age_s)


def describe() -> dict:
    return {
        "what_it_is": ("the store and freshness rule between a progress "
                       "provider and the frozen policy. NOT a provider"),
        "observed_sources": list(OBSERVED_SOURCES),
        "derived_sources_refused": list(DERIVED_SOURCES),
        "statuses": list(STATUSES),
        "in_play_statuses": list(IN_PLAY_STATUSES),
        "max_age_s": MAX_AGE_S,
        "refusals": list(REFUSALS),
        "overtime": ("not handled here; the policy rule maps period > "
                     "total_periods to SECOND_HALF"),
        "connected_providers": [],
        "why_none": ("the one licensed integration (fsc_scores.py -> "
                     "the-odds-api /v4/sports/{key}/scores) reads per-side "
                     "score, completed and last_update. For tennis the "
                     "score IS a set count; for soccer/basketball/football "
                     "it is goals or points, which is not a period index. "
                     "Its key is provisioned on the edge service and "
                     "sportsassets-api has no odds-feed key at all "
                     "(env-keys, 2026-09-23T22:10:54Z). Moving a "
                     "production credential between services is refused"),
    }
