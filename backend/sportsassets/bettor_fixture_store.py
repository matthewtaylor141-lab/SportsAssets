"""THE ONE PLACE THAT READS AND WRITES `fixture_metadata`.

WHY IT EXISTS, AND IT IS THE DEFECT IT CLOSES. The scope evidence the
settlement comparison needs -- competition phase, game format, and the
league's own report of the event state -- was written by ONE admin route,
invoked by hand for ONE acceptance position. The entry lane only ever READ
the table, so for every ordinary candidate the row was absent, the quote
context stayed unproven, `book_terms()` withheld the bookmaker side, and
every settlement verdict came back UNKNOWN. Measured in production on
2026-09-25: 15 of 15 candidate rows carried `ctx - phase - fmt -` with
`rules_read true` -- the venue side read, our own side missing.

So the write is no longer owned by a route. The entry lane acquires the
same evidence for the candidates it is evaluating, through the same
parser, into the same row, and the manager reads that row too. One
acquisition, one table, one reading.

WHAT THIS MODULE WILL NOT DO.

  * It does not infer a context from a scheduled start. `context_for`
    refuses unless the league REPORTED a state, and that refusal is
    preserved here rather than softened.
  * It does not fabricate a fixture. `match_fixture` requires the two team
    names and the official date to select exactly one game; no match and
    more-than-one match are both refusals with their own names.
  * It writes nothing but this one row. No order, no position, no decision.
"""

from __future__ import annotations

import json

from . import bettor_fixture_metadata as FM

VERSION = "BETTOR_FIXTURE_STORE_V1"

#: How old a persisted row may be before the lane re-acquires it. The event
#: state MOVES -- scheduled, pre-game, in progress, final -- and a row from
#: an hour ago can only fail to establish a context for a current quote. It
#: can never wrongly establish one, because `context_for` compares the
#: quote's own observation time against the instant the state was reported.
MAX_ROW_AGE_S = 300.0

R_NO_ROW = "NO_FIXTURE_METADATA_ROW_FOR_THIS_CONDITION"
R_READ_FAILED = "FIXTURE_METADATA_READ_FAILED"

READ_SQL = """
    SELECT phase, phase_uncovered, game_format, scheduled_innings,
           play_has_begun, event_state_raw, abstract_state, start_evidence,
           terminal_hint, game_pk, home_team, away_team, source, source_url,
           reader_version,
           official_date::text AS official_date,
           extract(epoch FROM actual_start_at)::float8 AS actual_start_at,
           extract(epoch FROM retrieved_at)::float8 AS retrieved_at_epoch,
           to_char(retrieved_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
               AS retrieved_at
      FROM fixture_metadata WHERE condition_id = $1
"""

#: The upsert, verbatim from the admin route that used to own it, including
#: the cast note that cost a run to find.
UPSERT_SQL = """
    INSERT INTO fixture_metadata(condition_id, phase,
      phase_uncovered, game_format, scheduled_innings,
      play_has_begun, event_state_raw, abstract_state,
      actual_start_at, start_evidence, terminal_hint, game_pk,
      official_date, home_team, away_team, game_number,
      double_header, source, source_url, retrieved_at,
      reader_version, refusals, raw)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,
             CASE WHEN $9::float8 IS NULL THEN NULL
                  ELSE to_timestamp($9::float8) END,
             -- ::text::date, NOT ::date. asyncpg infers the PARAMETER's
             -- type from the cast and then encodes client-side, so
             -- `$13::date` demanded a datetime.date and raised DataError
             -- on the string the caller sends. Casting from text keeps the
             -- parameter a string and lets Postgres parse it.
             $10,$11,$12,$13::text::date,$14,$15,$16,$17,$18,$19,
             $20::text::timestamptz,$21,$22::jsonb,$23::jsonb)
      ON CONFLICT (condition_id) DO UPDATE SET
        phase = EXCLUDED.phase,
        phase_uncovered = EXCLUDED.phase_uncovered,
        game_format = EXCLUDED.game_format,
        scheduled_innings = EXCLUDED.scheduled_innings,
        play_has_begun = EXCLUDED.play_has_begun,
        event_state_raw = EXCLUDED.event_state_raw,
        abstract_state = EXCLUDED.abstract_state,
        actual_start_at = EXCLUDED.actual_start_at,
        start_evidence = EXCLUDED.start_evidence,
        terminal_hint = EXCLUDED.terminal_hint,
        game_pk = EXCLUDED.game_pk,
        official_date = EXCLUDED.official_date,
        home_team = EXCLUDED.home_team,
        away_team = EXCLUDED.away_team,
        game_number = EXCLUDED.game_number,
        double_header = EXCLUDED.double_header,
        source = EXCLUDED.source,
        source_url = EXCLUDED.source_url,
        retrieved_at = EXCLUDED.retrieved_at,
        reader_version = EXCLUDED.reader_version,
        refusals = EXCLUDED.refusals,
        raw = EXCLUDED.raw,
        written_at = now()
"""


async def read(conn, condition_id) -> dict:
    """The persisted row, or WHY there is none. Never raises.

    A FAILED READ IS NOT AN ABSENT ROW. An absent row is an acquisition
    task; a failed read is a database fault. Reporting the second as the
    first would let an outage look like missing evidence.
    """
    try:
        row = await conn.fetchrow(READ_SQL, str(condition_id))
    except Exception as exc:                                   # noqa: BLE001
        return {"read": False, "error": type(exc).__name__,
                "refusal": R_READ_FAILED,
                "why": ("the fixture metadata read failed, which is not "
                        "evidence that no row exists")}
    if row is None:
        return {"read": False, "error": None, "refusal": R_NO_ROW,
                "why": ("no authoritative fixture metadata is persisted "
                        "for this condition, so the competition phase, "
                        "the game format and the actual event state are "
                        "unestablished")}
    return dict(row, read=True)


def row_age_s(row, *, now) -> float | None:
    """Seconds since the row's evidence was retrieved, or None."""
    at = (row or {}).get("retrieved_at_epoch")
    if at is None:
        at = FM._epoch((row or {}).get("retrieved_at"))
    if at is None:
        return None
    return float(now) - float(at)


def needs_acquisition(row, *, now, max_age_s=MAX_ROW_AGE_S) -> dict:
    """Should the lane acquire this fixture's evidence now? And why.

    Pure, so the policy is checkable without a database or a network.
    """
    if not (row or {}).get("read"):
        return {"acquire": True, "why": "no row is persisted",
                "refusal": (row or {}).get("refusal") or R_NO_ROW}
    age = row_age_s(row, now=now)
    if age is None:
        return {"acquire": True,
                "why": "the row carries no readable retrieval time"}
    if age > float(max_age_s):
        return {"acquire": True, "age_s": round(age, 3),
                "why": ("the persisted state was reported %.0fs ago, past "
                        "the %.0fs bound, and an event state moves"
                        % (age, float(max_age_s)))}
    if (row or {}).get("play_has_begun") is None:
        return {"acquire": True, "age_s": round(age, 3),
                "why": ("the row is recent but carries no reported event "
                        "state, so no context can be established from it")}
    return {"acquire": False, "age_s": round(age, 3),
            "why": "a recent row with a reported event state is held"}


async def upsert(conn, evidence, *, raw_game=None) -> dict:
    """Write ONE row. Returns what was written, or the failure by name."""
    ev = dict(evidence or {})
    try:
        await conn.execute(
            UPSERT_SQL,
            str(ev.get("condition_id") or ""), ev.get("phase"),
            ev.get("phase_uncovered"), ev.get("game_format"),
            ev.get("scheduled_innings"), ev.get("play_has_begun"),
            ev.get("event_state_raw"), ev.get("abstract_state"),
            ev.get("actual_start_at"), ev.get("start_evidence"),
            ev.get("terminal_hint"), ev.get("game_pk"),
            ev.get("official_date"), ev.get("home"), ev.get("away"),
            ev.get("game_number"), ev.get("double_header"),
            ev.get("source"), ev.get("source_url"), ev.get("retrieved_at"),
            FM.VERSION, json.dumps(list(ev.get("refusals") or [])),
            json.dumps(raw_game if raw_game is not None else {}))
    except Exception as exc:                                   # noqa: BLE001
        return {"persisted": False, "error": type(exc).__name__,
                "detail": str(exc)[:300],
                "why": ("the fixture evidence was read but could not be "
                        "persisted, so nothing downstream may use it")}
    return {"persisted": True, "condition_id": ev.get("condition_id"),
            "game_pk": ev.get("game_pk"),
            "writes": "ONE fixture_metadata row. No order, position or "
                      "decision."}


def describe() -> dict:
    return {
        "version": VERSION,
        "table": "fixture_metadata",
        "reader": FM.VERSION,
        "max_row_age_s": MAX_ROW_AGE_S,
        "consumers": ["workers.ext_pinnacle_loop (entry)",
                      "workers.rn1x_shadow (management)",
                      "api /api/admin/rn1x-acquire-fixture-scope"],
        "writes": "ONE fixture_metadata row per condition. Nothing else.",
        "will_not": [
            "infer a quote context from a scheduled start",
            "select a fixture without both team names and the official date",
            "treat a failed read as an absent row",
        ],
    }