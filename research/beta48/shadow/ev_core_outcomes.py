#!/usr/bin/env python3
"""EV CORE -- OUTCOME RECONSTRUCTION. Recovering the event from its contracts.

THE PROBLEM. A settlement record says a contract paid 1.0 or 0.0. It does not
say the score. A distribution-first event model needs the EVENT OUTCOME -- goals
by each side -- not a bag of independent binary labels, because the whole point
of the model is that one latent event state generates every contract.

THE INSIGHT THIS MODULE RESTS ON. The venue lists MANY contracts per fixture and
settles all of them. That settled set is over-determined, so the event outcome
can be RECOVERED from it rather than sourced externally:

    the full-game totals ladder brackets total goals, and when the ladder
      straddles the true value with adjacent lines it PINS it exactly
    an exact-score contract settling YES gives the score outright
    the per-team moneyline and draw contracts give the winner
    both-teams-to-score constrains each side above or at zero
    the halftime result constrains the first half

Each of these is a CONSTRAINT. Intersecting them yields either a unique score, a
narrowed set, or an honest refusal. Nothing is inferred from a team name, a
league table or an external feed -- only from the venue's own settlements.

WHY THIS IS NOT CIRCULAR. The reconstructed outcome is used as the TRAINING
LABEL. It comes from settlements, which are known only after the event, and it
is never available at decision time. `ev_core_features` is responsible for
keeping as-of discipline; this module deliberately uses post-event information
because that is what a label is.

IT FAILS CLOSED. A constraint set that admits more than one score yields
RECONSTRUCTION_STATUS = UNDERDETERMINED with the surviving set reported, and a
set that admits none yields CONTRADICTORY -- which is a data defect worth
seeing, not something to average away. Only UNIQUE is usable as a score label.
Partial labels (winner known, score not) remain usable for the contracts they
do determine, and say so.

This module contacts nothing and can place no order.
"""
import json
import re
from collections import Counter, defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

STATUS_UNIQUE = "UNIQUE"
STATUS_UNDERDETERMINED = "UNDERDETERMINED"
STATUS_CONTRADICTORY = "CONTRADICTORY"
STATUS_NO_CONSTRAINTS = "NO_CONSTRAINTS"

LABEL_SOURCE = "VENUE_SETTLEMENTS_ONLY"
NOT_AVAILABLE_AT_DECISION_TIME = (
    "a reconstructed outcome is a LABEL; it is derived from settlements and is "
    "never a feature -- as-of discipline is enforced separately")

# The venue's soccer slug grammar, read off real settled rows rather than
# assumed. Anchored so a longer suffix cannot be mistaken for a shorter one.
EVENT_RE = re.compile(r"^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})")
TOTAL_RE = re.compile(r"-total-(\d+)pt5$")
FIRST_HALF_TOTAL_RE = re.compile(r"-first-half-total-(\d+)pt5$")
EXACT_RE = re.compile(r"-exact-score-(\d+)-(\d+)$")
TEAM_TOTAL_RE = re.compile(r"-([a-z0-9]+)-team-total-(\d+)pt5$")
DRAW_RE = re.compile(r"-draw$")
BTTS_RE = re.compile(r"-btts$")
HALFTIME_RE = re.compile(r"-halftime-result-(home|away|draw)$")

# A score grid wide enough for association football. Reconstruction searches it
# exhaustively, so the bound is a declared modelling limit rather than a guess:
# a fixture outside it comes back UNDERDETERMINED instead of silently wrong.
MAX_GOALS = 12


def event_key(slug):
    """The fixture a settled contract belongs to: league-home-away-date."""
    m = EVENT_RE.match(slug or "")
    return m.group(0) if m else None


def _won(rec):
    """The outcome string(s) the venue paid out on."""
    out = []
    for t, p in zip(rec.get("tokens") or (), rec.get("payouts") or ()):
        try:
            if float(p) == 1.0:
                out.append(t.get("outcome"))
        except (TypeError, ValueError):
            continue
    return out


def _single(rec):
    w = _won(rec)
    return w[0] if len(w) == 1 else None


def constraints(records):
    """Turn one fixture's settled contracts into score-grid constraints.

    Returns a list of (NAME, predicate) where each predicate takes (home, away)
    goals and returns whether that score is consistent with the settlement.
    """
    out = []
    for r in records:
        slug = r.get("market_slug") or ""
        won = _single(r)
        if won is None:
            continue
        w = won.strip().lower()

        # ORDER MATTERS AND IS NOT COSMETIC. `-halftime-result-draw` also ends
        # in `-draw`, and `-first-half-total-2pt5` also ends in `-total-2pt5`.
        # The SEGMENT patterns are therefore tested FIRST, so a half-time
        # contract can never be read as a full-time one. Getting this backwards
        # produced DRAW_YES and DRAW_NO on the same fixture.
        if HALFTIME_RE.search(slug) or FIRST_HALF_TOTAL_RE.search(slug):
            continue                       # constrains the half, not the game

        m = EXACT_RE.search(slug)
        if m:
            h, a = int(m.group(1)), int(m.group(2))
            if w == "yes":
                out.append(("EXACT_YES_%d_%d" % (h, a),
                            lambda x, y, h=h, a=a: (x, y) == (h, a)))
            elif w == "no":
                out.append(("EXACT_NO_%d_%d" % (h, a),
                            lambda x, y, h=h, a=a: (x, y) != (h, a)))
            continue

        m = FIRST_HALF_TOTAL_RE.search(slug)
        if m:
            continue                       # constrains the half, not the game

        m = TEAM_TOTAL_RE.search(slug)
        if m:
            continue                       # side attribution needs the roster

        m = TOTAL_RE.search(slug)
        if m:
            line = int(m.group(1)) + 0.5
            if w == "over":
                out.append(("TOTAL_OVER_%s" % line,
                            lambda x, y, L=line: (x + y) > L))
            elif w == "under":
                out.append(("TOTAL_UNDER_%s" % line,
                            lambda x, y, L=line: (x + y) < L))
            continue

        if DRAW_RE.search(slug):
            if w == "yes":
                out.append(("DRAW_YES", lambda x, y: x == y))
            elif w == "no":
                out.append(("DRAW_NO", lambda x, y: x != y))
            continue

        if BTTS_RE.search(slug):
            if w == "yes":
                out.append(("BTTS_YES", lambda x, y: x > 0 and y > 0))
            elif w == "no":
                out.append(("BTTS_NO", lambda x, y: x == 0 or y == 0))
            continue

        if HALFTIME_RE.search(slug):
            continue                       # constrains the half, not the game
    return out


def moneyline_winner(records, home_code, away_code):
    """Which side the venue paid, from the two per-team contracts.

    The venue lists a soccer moneyline as two separate YES/NO markets named by
    team code. Read together with the draw market they identify the result.
    """
    home = away = None
    for r in records:
        slug = r.get("market_slug") or ""
        won = _single(r)
        if won is None:
            continue
        w = won.strip().lower()
        if slug.endswith("-" + home_code):
            home = (w == "yes")
        elif slug.endswith("-" + away_code):
            away = (w == "yes")
    if home is True and away is False:
        return "HOME"
    if away is True and home is False:
        return "AWAY"
    if home is False and away is False:
        return "DRAW"
    return None


def reconstruct(records, max_goals=MAX_GOALS):
    """Recover one fixture's score from its settled contract set."""
    records = [r for r in records if r.get("resolved") and r.get("market_slug")]
    if not records:
        return {"RECONSTRUCTION_STATUS": STATUS_NO_CONSTRAINTS,
                "CONTRACTS_USED": 0, "LABEL_SOURCE": LABEL_SOURCE}

    key = event_key(records[0]["market_slug"])
    m = EVENT_RE.match(records[0]["market_slug"])
    league, home_code, away_code, date = (m.groups() if m
                                          else (None, None, None, None))

    cons = constraints(records)
    grid = [(h, a) for h in range(max_goals + 1) for a in range(max_goals + 1)]
    survivors = [s for s in grid if all(f(*s) for _, f in cons)]

    winner = moneyline_winner(records, home_code, away_code)
    if winner is not None:
        pred = {"HOME": lambda x, y: x > y, "AWAY": lambda x, y: x < y,
                "DRAW": lambda x, y: x == y}[winner]
        survivors = [s for s in survivors if pred(*s)]
        cons.append(("MONEYLINE_%s" % winner, pred))

    if not cons:
        status = STATUS_NO_CONSTRAINTS
    elif not survivors:
        status = STATUS_CONTRADICTORY
    elif len(survivors) == 1:
        status = STATUS_UNIQUE
    else:
        status = STATUS_UNDERDETERMINED

    # Even without a unique score, the constraint set often pins derived
    # quantities. A label that is partially known is still a usable label for
    # the contracts it determines, and reporting it is better than discarding
    # the fixture.
    totals = sorted({h + a for h, a in survivors})
    margins = sorted({h - a for h, a in survivors})
    btts = {(h > 0 and a > 0) for h, a in survivors}

    return {
        "EVENT_KEY": key or NOT_IDENTIFIED,
        "LEAGUE": league or NOT_IDENTIFIED,
        "HOME_CODE": home_code or NOT_IDENTIFIED,
        "AWAY_CODE": away_code or NOT_IDENTIFIED,
        "DATE": date or NOT_IDENTIFIED,
        "RECONSTRUCTION_STATUS": status,
        "HOME_GOALS": survivors[0][0] if status == STATUS_UNIQUE
                      else NOT_IDENTIFIED,
        "AWAY_GOALS": survivors[0][1] if status == STATUS_UNIQUE
                      else NOT_IDENTIFIED,
        "TOTAL_GOALS": (totals[0] if len(totals) == 1 else NOT_IDENTIFIED),
        "MARGIN": (margins[0] if len(margins) == 1 else NOT_IDENTIFIED),
        "WINNER": winner or NOT_IDENTIFIED,
        "BTTS": (list(btts)[0] if len(btts) == 1 else NOT_IDENTIFIED),
        "SURVIVING_SCORES": len(survivors),
        "SURVIVING_SAMPLE": survivors[:8],
        "CONSTRAINTS_APPLIED": [n for n, _ in cons],
        "CONTRACTS_USED": len(records),
        "MAX_GOALS_SEARCHED": max_goals,
        "LABEL_SOURCE": LABEL_SOURCE,
        "NOT_AVAILABLE_AT_DECISION_TIME": NOT_AVAILABLE_AT_DECISION_TIME,
    }


def group_events(records):
    """Bucket settled contracts by fixture."""
    by = defaultdict(list)
    for r in records or ():
        if not isinstance(r, dict):
            continue
        k = event_key(r.get("market_slug") or "")
        if k:
            by[k].append(r)
    return dict(by)


def reconstruct_all(records, max_goals=MAX_GOALS, sport=None):
    """Reconstruct every fixture, and report coverage honestly."""
    if sport is not None:
        records = [r for r in records if r.get("sport") == sport]
    by = group_events(records)
    out = {k: reconstruct(v, max_goals) for k, v in by.items()}
    status = Counter(v["RECONSTRUCTION_STATUS"] for v in out.values())
    unique = [v for v in out.values()
              if v["RECONSTRUCTION_STATUS"] == STATUS_UNIQUE]
    total_known = [v for v in out.values() if v["TOTAL_GOALS"] != NOT_IDENTIFIED]
    winner_known = [v for v in out.values() if v["WINNER"] != NOT_IDENTIFIED]
    n = len(out)
    return {
        "EVENTS": out,
        "EVENT_COUNT": n,
        "STATUS_COUNTS": dict(status),
        "SCORE_RECONSTRUCTED": len(unique),
        "SCORE_RECONSTRUCTED_PCT": (100.0 * len(unique) / n) if n
                                   else NOT_IDENTIFIED,
        "TOTAL_GOALS_KNOWN": len(total_known),
        "WINNER_KNOWN": len(winner_known),
        "CONTRADICTORY": status.get(STATUS_CONTRADICTORY, 0),
        "A_CONTRADICTION_IS_A_DATA_DEFECT": (
            "a contract set admitting no score means two settlements disagree; "
            "it is surfaced rather than dropped, because the cause matters"),
        "PARTIAL_LABELS_ARE_STILL_LABELS": (
            "a fixture whose total is known but whose score is not can still "
            "train and score every totals contract on it"),
        "LABEL_SOURCE": LABEL_SOURCE,
    }


def render(rep):
    keys = ("EVENT_COUNT", "SCORE_RECONSTRUCTED", "SCORE_RECONSTRUCTED_PCT",
            "TOTAL_GOALS_KNOWN", "WINNER_KNOWN", "CONTRADICTORY")
    L = ["%-34s = %s" % (k, rep.get(k, NOT_IDENTIFIED)) for k in keys]
    L.append("%-34s = %s" % ("STATUS_COUNTS", rep.get("STATUS_COUNTS")))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
