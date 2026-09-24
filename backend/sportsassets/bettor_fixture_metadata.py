"""AUTHORITATIVE FIXTURE METADATA: the phase, the format, the actual state.

WHAT THIS EXISTS TO SUPPLY. Two guards needed evidence that was not in our
data, and both were being reported as unknown indefinitely:

  * `bettor_settlement_terms.admit_scope` needs the COMPETITION PHASE and the
    GAME FORMAT, because the captured bookmaker rules cover regular-season
    nine-inning games only -- a playoff fixture and a seven-inning
    doubleheader are graded by different clauses.
  * `bettor_settlement_terms.book_context_for` needs ACTUAL EVENT STATE. A
    scheduled start classifies nothing: a catalogue snapshot can be stale in
    either direction, so neither "past it" nor "before it" establishes
    whether play had begun.

The league publishes all three PER GAME. `markets` not carrying them is an
integration gap, not an external blocker, and this module closes it.

THE SHAPE IS TAKEN FROM THE SERVED PAYLOAD, not from recollection. The
runner retrieved one date and printed the field names and the fields below
for every game; the parser was written against that output. The retrieval is
recorded in `SOURCE` and every persisted row carries its own retrieval time.

WHAT IS DELIBERATELY REFUSED RATHER THAN GUESSED:
  * a `gameType` or a state string this module has not DECLARED is a named
    refusal, never a default. A new postseason round or a renamed state must
    be read and declared, not absorbed.
  * a fixture that matches more than one game is AMBIGUOUS and refused. That
    is exactly the doubleheader case, where the format is the thing in
    question, so resolving it by picking one would defeat the purpose.
"""

from __future__ import annotations

import re

from . import bettor_settlement_terms as ST

VERSION = "BETTOR_FIXTURE_METADATA_V1"

#: The league's own schedule endpoint. Public, no credential.
SOURCE = "MLB Stats API, schedule"
SOURCE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s"

#: Retrieved once to establish the shape this parser reads.
SHAPE_PROBE = {
    "job": ("https://github.com/matthewtaylor141-lab/SportsAssets/actions/"
            "runs/36058456314/job/107831250336"),
    "retrieved_at": "2026-09-24T20:58:51Z",
    "date": "2026-09-24",
    "http": 200,
    "fields_used": ["gamePk", "gameType", "scheduledInnings", "doubleHeader",
                    "gameNumber", "officialDate", "gameDate",
                    "status.detailedState", "status.abstractGameState",
                    "status.codedGameState",
                    "teams.home.team.name", "teams.away.team.name"],
    "example": ("gamePk=824298 gameType=R scheduledInnings=9 doubleHeader=N "
                "gameNumber=1 officialDate=2026-09-24 "
                "gameDate=2026-09-24T19:10:00Z status=In Progress "
                "away=Arizona Diamondbacks home=Colorado Rockies"),
}

# ── the declared maps ────────────────────────────────────────────────

R_GAME_TYPE_UNDECLARED = "GAME_TYPE_NOT_DECLARED"
R_FORMAT_UNDECLARED = "SCHEDULED_INNINGS_NOT_DECLARED"
R_STATE_UNDECLARED = "GAME_STATE_NOT_DECLARED"
R_NO_MATCH = "NO_FIXTURE_MATCHED"
R_AMBIGUOUS = "MORE_THAN_ONE_FIXTURE_MATCHED"
R_PAYLOAD = "SCHEDULE_PAYLOAD_NOT_READABLE"

#: `gameType` -> the competition phase the captured rules speak about.
#: Only R is inside the capture; the postseason codes map to the phase the
#: capture EXCLUDES, which is a different thing from being unknown.
GAME_TYPE_PHASE = {
    "R": ST.PHASE_REGULAR,
    "F": ST.PHASE_PLAYOFF,      # wild card
    "D": ST.PHASE_PLAYOFF,      # division series
    "L": ST.PHASE_PLAYOFF,      # league championship series
    "W": ST.PHASE_PLAYOFF,      # world series
}

#: Everything else the endpoint can carry is named, so it is refused as a
#: KNOWN-but-uncovered phase rather than as an unreadable one.
GAME_TYPE_OTHER = {
    "S": "SPRING_TRAINING",
    "E": "EXHIBITION",
    "A": "ALL_STAR",
    "I": "INTRASQUAD",
}

#: `scheduledInnings` -> the game format the captured rules key their
#: thresholds to.
INNINGS_FORMAT = {9: ST.FMT_NINE, 7: ST.FMT_SEVEN}

#: `status.detailedState` -> whether play HAS BEGUN. Only the values actually
#: OBSERVED in the probe are declared; anything else is refused by name so a
#: renamed or unseen state cannot be read as "not started".
#:
#: True  -> play has begun (or has been played)
#: False -> play has not begun
STATE_PLAY_BEGUN = {
    "scheduled": False,
    "pre-game": False,
    "warmup": False,
    "in progress": True,
    "game over": True,
    "final": True,
    "completed early": True,
    "suspended": True,
    "delayed": True,
    "delayed start": False,
    "postponed": False,
    "cancelled": False,
    "canceled": False,
}

#: States that say the fixture DID NOT END NORMALLY. Carried through because
#: they are the terminal conditions the settlement comparison turns on.
STATE_TERMINAL_HINT = {
    "completed early": ST.C_CALLED_FINAL,
    "suspended": ST.C_SUSPENDED_BEYOND,
    "postponed": ST.C_NOT_PLAYED,
    "cancelled": ST.C_NOT_PLAYED,
    "canceled": ST.C_NOT_PLAYED,
}


def _norm(name) -> str:
    """A team name reduced to comparable tokens."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ",
                           str(name or "").lower()).split())


def _epoch(iso):
    """An ISO-8601 Z stamp as epoch seconds, or None."""
    import datetime as _dt

    try:
        s = str(iso).replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(s).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def parse_games(payload) -> dict:
    """Every game in a schedule payload, flattened to the fields we read."""
    try:
        dates = list((payload or {}).get("dates") or [])
    except Exception:                                          # noqa: BLE001
        return {"ok": False, "refusal": R_PAYLOAD, "games": []}
    games = []
    for d in dates:
        # A DATE ENTRY THAT IS NOT AN OBJECT IS A MALFORMED PAYLOAD, not an
        # empty day. Skipping it silently would report "no games" for a
        # response we could not read, which is the confusion this module
        # exists to avoid.
        if not isinstance(d, dict):
            return {"ok": False, "refusal": R_PAYLOAD, "games": [],
                    "why": ("a `dates` entry is %s, not an object, so the "
                            "payload was not readable"
                            % (type(d).__name__,))}
        for g in list(d.get("games") or []):
            if not isinstance(g, dict):
                return {"ok": False, "refusal": R_PAYLOAD, "games": [],
                        "why": "a `games` entry is not an object"}
            st = dict((g or {}).get("status") or {})
            teams = dict((g or {}).get("teams") or {})
            games.append({
                "game_pk": (g or {}).get("gamePk"),
                "game_type": (g or {}).get("gameType"),
                "scheduled_innings": (g or {}).get("scheduledInnings"),
                "double_header": (g or {}).get("doubleHeader"),
                "game_number": (g or {}).get("gameNumber"),
                "official_date": (g or {}).get("officialDate"),
                "game_date": (g or {}).get("gameDate"),
                "detailed_state": st.get("detailedState"),
                "abstract_state": st.get("abstractGameState"),
                "coded_state": st.get("codedGameState"),
                "home": ((teams.get("home") or {}).get("team") or {}).get("name"),
                "away": ((teams.get("away") or {}).get("team") or {}).get("name"),
            })
    return {"ok": True, "refusal": None, "games": games,
            "total": len(games)}


def match_fixture(games, *, home=None, away=None, official_date=None) -> dict:
    """The ONE game this fixture is, or a named refusal.

    Matching is on both team names -- each side must match the corresponding
    side, so a mirrored pairing is not accepted -- and on the official date
    where one is supplied. TWO matches is a refusal, not a choice: that is
    the doubleheader, where the format is precisely what is in question.
    """
    h, a = _norm(home), _norm(away)
    hits = []
    for g in list(games or []):
        if official_date and str(g.get("official_date") or "") != \
                str(official_date):
            continue
        gh, ga = _norm(g.get("home")), _norm(g.get("away"))
        if not gh or not ga:
            continue
        if h and a and (h in gh or gh in h) and (a in ga or ga in a):
            hits.append(g)
    if not hits:
        return {"ok": False, "refusal": R_NO_MATCH, "game": None,
                "candidates": len(list(games or [])),
                "why": ("no game on %s pairs away %r with home %r. The names "
                        "are compared as normalised tokens on the matching "
                        "SIDE, so a mirrored fixture is not accepted"
                        % (official_date, away, home))}
    if len(hits) > 1:
        return {"ok": False, "refusal": R_AMBIGUOUS, "game": None,
                "matched": [g.get("game_pk") for g in hits],
                "why": ("%d games match, which is the DOUBLEHEADER case. The "
                        "game format is the thing being established here, so "
                        "picking one would defeat the check. The G1/G2 "
                        "designation has to come from the fixture itself"
                        % (len(hits),))}
    return {"ok": True, "refusal": None, "game": hits[0]}


def evidence_from(game, *, retrieved_at, source_url, condition_id=None) -> dict:
    """One game's metadata as EVIDENCE: values, provenance, and refusals."""
    g = dict(game or {})
    gt = str(g.get("game_type") or "")
    innings = g.get("scheduled_innings")
    state = str(g.get("detailed_state") or "").strip().lower()

    phase = GAME_TYPE_PHASE.get(gt)
    phase_other = GAME_TYPE_OTHER.get(gt)
    fmt = INNINGS_FORMAT.get(int(innings)) if innings is not None else None
    begun = STATE_PLAY_BEGUN.get(state)

    refusals = []
    if phase is None and phase_other is None:
        refusals.append(R_GAME_TYPE_UNDECLARED)
    if fmt is None:
        refusals.append(R_FORMAT_UNDECLARED)
    if begun is None:
        refusals.append(R_STATE_UNDECLARED)

    return {
        "version": VERSION,
        "condition_id": condition_id,
        "source": SOURCE, "source_url": source_url,
        "retrieved_at": retrieved_at,
        # THE FIXTURE BINDING, so a row can be checked against the fixture it
        # claims to describe rather than trusted.
        "game_pk": g.get("game_pk"),
        "official_date": g.get("official_date"),
        "home": g.get("home"), "away": g.get("away"),
        "game_number": g.get("game_number"),
        "double_header": g.get("double_header"),
        # WHAT THE SCOPE GUARD ASKED FOR
        "phase": phase,
        "phase_uncovered": phase_other,
        "game_type_raw": gt,
        "game_format": fmt,
        "scheduled_innings": innings,
        # WHAT THE CONTEXT RULE ASKED FOR: an ACTUAL state, with the time it
        # was observed, not a schedule.
        "play_has_begun": begun,
        "event_state_raw": g.get("detailed_state"),
        "abstract_state": g.get("abstract_state"),
        "actual_start_at": _epoch(g.get("game_date")),
        "start_evidence": (ST.SE_ACTUAL_REPORTED if begun is not None
                           else ST.SE_SCHEDULED_CATALOGUE),
        "terminal_hint": STATE_TERMINAL_HINT.get(state),
        "refusals": refusals,
        "ok": not refusals,
        "why": ("; ".join(refusals) if refusals else
                "the phase, the format and the actual state are all declared"),
    }


def context_for(evidence, *, observed_at) -> dict:
    """The quote context from ACTUAL state, not from a schedule.

    `play_has_begun` is the league's own report of the fixture's state at
    `retrieved_at`. A quote observed at or after that instant is in whatever
    state was reported; a quote observed BEFORE a report of "not begun" is
    also before the first pitch. Anything else stays UNKNOWN.
    """
    ev = dict(evidence or {})
    begun = ev.get("play_has_begun")
    if begun is None:
        return ST.book_context_for(observed_at=observed_at, start_at=None)
    if begun:
        return ST.book_context_for(
            observed_at=observed_at, start_at=ev.get("actual_start_at"),
            start_evidence=ST.SE_ACTUAL_REPORTED)
    # Reported NOT begun. A quote taken before that report is pre-game.
    at = _epoch(ev.get("retrieved_at"))
    if at is not None and observed_at is not None and float(observed_at) <= at:
        return {"context": ST.CTX_PRE_GAME, "refusal": None,
                "observed_at": float(observed_at),
                "start_evidence": ST.SE_ACTUAL_REPORTED,
                "why": ("the league reported the fixture NOT STARTED at %s "
                        "and this quote was observed at or before that "
                        "instant, so play had not begun when it was taken"
                        % (ev.get("retrieved_at"),))}
    return {"context": None, "refusal": ST.R_CONTEXT_UNKNOWN,
            "observed_at": observed_at,
            "why": ("the fixture was reported not started at %s, but this "
                    "quote was observed AFTER that report, so its state is "
                    "not covered by it" % (ev.get("retrieved_at"),))}


def describe() -> dict:
    return {
        "version": VERSION,
        "source": SOURCE,
        "source_url_template": SOURCE_URL,
        "shape_probe": dict(SHAPE_PROBE),
        "supplies": ["competition phase", "game format",
                     "actual event state with its observation time"],
        "declared_game_types": dict(GAME_TYPE_PHASE),
        "declared_uncovered_game_types": dict(GAME_TYPE_OTHER),
        "declared_formats": {str(k): v for k, v in INNINGS_FORMAT.items()},
        "declared_states": dict(STATE_PLAY_BEGUN),
        "refusals": [R_GAME_TYPE_UNDECLARED, R_FORMAT_UNDECLARED,
                     R_STATE_UNDECLARED, R_NO_MATCH, R_AMBIGUOUS, R_PAYLOAD],
        "an_undeclared_value_is_refused": (
            "a gameType or state this module has not declared is named, not "
            "defaulted. A new postseason round must be read and declared"),
    }
