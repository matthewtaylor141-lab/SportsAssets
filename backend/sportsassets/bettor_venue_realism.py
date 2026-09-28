"""IS THE VENUE'S CONTRACT THE REAL FIXTURE, OR A SIMULATION OF IT?

WHY THIS MODULE EXISTS, AND WHAT IT STOPPED ME DOING. 2026-09-28, the live
cycle on build c3d0cfc reported `soccer_epl: provider_events 20,
with_pinnacle_h2h 20, venue_markets_open_and_fresh 217,
mapped_to_a_venue_contract 0`. I set out to repair the resolver, and the
authorized read (`research/epl_resolution_trace.sql`, run 258) answered a
different question than the one I asked:

    our_leading_token   our_open_markets   venue_rows_same_token
    lal                              468                       0
    epl                              387                       0
    sea                              321                    1812
    mls                              316                     460
    ...

`epl` appears in ZERO `us_premap` rows. The US venue does not list the English
Premier League at all. But searching the venue's own titles for English
top-flight club names returns 420 rows across 70 events:

    atc-ebfpl-ars-che-2026-09-27-dh3-ars    "eBattles: Arsenal vs. Chelsea"
    atc-ebfpl-ars-liv-2026-09-27-dh2-ars    "eBattles: Arsenal vs. Liverpool"
    atc-ebfpl-ars-tot-2026-09-27-dh1-ars    "eBattles: Arsenal vs. Tottenham"

with `sports_type = efootball_team_full_time_winner` -- 538 events whose
kickoffs run every few minutes. These are VIDEO GAME matches between
human-controlled teams bearing real club names. `ebfpl` is eBattles Football
Premier League, not the Premier League.

SO THE OBVIOUS REPAIR WAS THE DANGEROUS ONE. A league alias `epl -> ebfpl`, or
any bridge that matches participants by name, binds a Pinnacle probability for
a real Arsenal-Chelsea fixture to a contract that settles on a simulation of
it. Both sides would look right at every check the lane makes -- the clubs
match, the date matches, the market type matches, the money line exists -- and
the position would be priced against an event that is not the one occurring.
That is not a mapping win. It is a capital loss with a correct-looking audit
trail.

WHERE THE CONCEPT ALREADY EXISTED, AND WHERE IT DID NOT. `api/app.py` has
`_SIMULATED_MARKERS` and `/api/admin/venue-competitions` separates real from
simulated -- for a DIAGNOSTIC route, reading the live desk sweep. The ENTRY
LANE resolves through `us_premap` and had no such check anywhere:
`resolve_venue_identity` took the resolver's slug and went straight on to the
intent. A guard that lives only in the route that reports the problem does not
prevent it.

THE VENUE'S OWN WORDS, NOT A TOKEN GUESS. Two independent statements by the
publisher are read, and both are reported:

  * `sports_type`, the venue's own classification -- `efootball_*`, `esports_*`
  * the title and question prose -- "eBattles", "eSoccer", "Cyber", "Virtual"

A league TOKEN is never used to decide this. `ebfpl`, `ebfsa`, `ebfwca` and
`ebfwcb` all happen to start `ebf`, and inferring "simulated" from that prefix
would be exactly the kind of guess this module exists to refuse. If the venue
relabels a competition, its own words move with it; a token pattern of mine
does not.

FAIL CLOSED ON AN UNREADABLE ROW. `classify` returns
UNKNOWN_CANNOT_ESTABLISH_REALISM when the catalogue row is absent or carries
neither a sports_type nor any prose, and the caller refuses on it. An
unanswered realism question is not a real fixture.
"""

from __future__ import annotations

#: Words the VENUE ITSELF uses to say a competition is simulated. Matched
#: against the venue's own title, question and sports_type -- never against a
#: league token. Kept here as the single definition; `api/app.py` imports it so
#: the diagnostic route and the entry lane cannot disagree about what the venue
#: called something.
SIMULATED_MARKERS = ("ebattles", "e-battles", "esoccer", "e-soccer",
                     "ebasketball", "efootball", "e-football",
                     "simulated", "cyber", "virtual", "e-cricket",
                     "ehockey", "e-hockey", "etennis", "e-tennis")

#: The venue's own `sports_type` prefixes for a simulated or electronic
#: competition. These are the publisher's classification, read verbatim from
#: `us_premap.sports_type`; the measured values behind them on 2026-09-28 are
#: `efootball_team_full_time_winner` (538 events) and `esports_match_winner`
#: (195 events), against `soccer_team_full_time_winner` (294 events) for the
#: real thing.
SIMULATED_SPORTS_TYPE_PREFIXES = ("efootball_", "esports_", "ebasketball_",
                                  "esoccer_", "ecricket_", "ehockey_",
                                  "etennis_")

#: ── THE ALLOWLIST, AND WHY AN ALLOWLIST IS NEEDED AT ALL ─────────────
#:
#: THE DEFECT THIS FIXES, AND IT WAS IN THE FIRST VERSION OF THIS MODULE. The
#: docstring above says "UNKNOWN is not REAL" and the code did not implement it.
#: `classify` checked for simulation markers and, finding none, returned REAL --
#: so all three of these came back REAL_FIXTURE:
#:
#:     {"market_slug": "unknown-contract"}       no classification at all
#:     {"sports_type": "future_unknown_type"}    a classification I do not know
#:     {"event_title": "Arsenal vs Chelsea"}     prose and nothing else
#:
#: That is absence of evidence read as evidence, which is the single error this
#: module exists to prevent, written into the module that prevents it. A venue
#: that introduces a new simulated family tomorrow -- or renames `efootball_` --
#: would have been admitted by every one of those rows.
#:
#: So REAL now requires an AFFIRMATIVE, RECOGNIZED classification, and everything
#: else is UNKNOWN. The cost of that direction is a refusal on a legitimate
#: competition whose prefix is missing here; the cost of the other direction is a
#: position on a simulation. The first is visible in the funnel and costs a
#: trade, the second costs the trade's capital.
#:
#: ASSEMBLED FROM THE VENUE'S OWN VOCABULARY, NOT FROM MEMORY, AND THE FIRST
#: ATTEMPT PROVES WHY (research-sql run 260, `research/venue_sports_types.sql`,
#: every distinct sports_type with no LIMIT -- 218 of them in 14 families).
#:
#: I wrote this list from memory first. It was wrong in both directions: it
#: invented `mma_`, `boxing_`, `golf_`, `motorsport_`, `cycling_`, `rugby_`,
#: `volleyball_`, `handball_`, `snooker_`, `badminton_`, `aussie_rules_` and
#: `lacrosse_`, none of which the venue uses -- and it OMITTED `ufc_` (3 types,
#: 5 events), `darts_` (46 events) and `futures` (6,606 rows). On a fail-closed
#: allowlist an omission is a refusal, so the guessed list would have refused
#: every UFC and darts contract on the board while carrying twelve families that
#: do not exist.
#:
#: The venue's 14 families, with distinct type counts: baseball 29,
#: football 84, soccer 22, tennis 8, table_tennis 6, futures 1, hockey 21,
#: efootball 1, esports 33, basketball 7, NULL 0, darts 1, ufc 3, cricket 1.
REAL_SPORTS_TYPE_PREFIXES = (
    "baseball_", "football_", "soccer_", "tennis_", "table_tennis_",
    "hockey_", "basketball_", "darts_", "ufc_", "cricket_",
)

#: `futures` IS NOT A FIXTURE AND IS NOT A SIMULATION. 6,606 rows over 76
#: events, and the literal string `futures` with no suffix: a season-long
#: outright, not a match between two sides. This guard's question -- "is the
#: venue's contract the real fixture" -- has no answer for it, and neither REAL
#: nor SIMULATED is honest. It is UNKNOWN under its own reason, which also keeps
#: an outright out of a lane that prices head-to-head fixtures.
NOT_A_FIXTURE_SPORTS_TYPES = ("futures",)

REAL = "REAL_FIXTURE"
SIMULATED = "SIMULATED_OR_ELECTRONIC_FIXTURE"
CONFLICTING = "CONFLICTING_EVIDENCE_ABOUT_THIS_FIXTURE"
UNKNOWN = "UNKNOWN_CANNOT_ESTABLISH_REALISM"

#: The named refusals. A refusal is a name the operator can grep, not a
#: sentence -- the sentence goes in `why`.
R_SIMULATED_NOT_THE_REAL_FIXTURE = (
    "THE_VENUE_CONTRACT_IS_A_SIMULATION_OF_THIS_FIXTURE_NOT_THE_FIXTURE")
R_REALISM_NOT_ESTABLISHED = "THE_VENUE_CONTRACT_REALISM_COULD_NOT_BE_READ"
#: A row whose classification says real and whose prose says simulated. Both
#: statements are the publisher's and they disagree, so this is neither an
#: absence nor a simulation: it is a contradiction, and it refuses under its own
#: name so an operator is not sent looking for a missing field.
R_REALISM_EVIDENCE_CONFLICTS = (
    "THE_VENUE_CLASSIFICATION_AND_ITS_OWN_PROSE_DISAGREE")

#: The columns `classify` reads. Named so a caller's SELECT can be checked
#: against it rather than guessed -- two statements have already been lost in
#: this area to a column name recalled from memory.
CATALOGUE_FIELDS = ("market_slug", "event_slug", "event_title", "question",
                    "sports_type")

_FIELDS_CARRYING_PROSE = ("event_title", "question", "market_slug")


def classify(row) -> dict:
    """The venue's own verdict on one catalogue row. Pure; reads no database.

    REAL REQUIRES AFFIRMATIVE, RECOGNIZED EVIDENCE. The first version of this
    function looked for simulation markers and, finding none, returned REAL --
    so a row with no classification, a row with an unrecognized one, and a row
    carrying nothing but a title were all admitted as real fixtures. The
    docstring said "UNKNOWN is not REAL" and the code said the opposite.

    The four verdicts and what produces each:

      REAL         the venue's own `sports_type` is in `REAL_SPORTS_TYPE_PREFIXES`
                   AND no simulation evidence appears anywhere on the row
      SIMULATED    any simulation evidence -- a simulated `sports_type` prefix,
                   or a marker in the venue's own prose
      CONFLICTING  a recognized REAL classification AND simulation evidence.
                   Both statements are the publisher's; they disagree, and that
                   is neither an absence nor a simulation
      UNKNOWN      everything else: no `sports_type`, an unrecognized one, a
                   `futures` outright, or no row at all

    Every verdict but REAL carries a `refusal` so the caller refuses by name
    without re-deriving the reason.
    """
    out: dict = {"verdict": UNKNOWN, "refusal": R_REALISM_NOT_ESTABLISHED,
                 "evidence": None, "markers_checked": len(SIMULATED_MARKERS),
                 "recognized_real_prefixes": len(REAL_SPORTS_TYPE_PREFIXES),
                 "decided_on": ("an affirmative recognized classification from "
                                "the venue, never the mere absence of a "
                                "simulation marker, and never a league token")}
    if not row:
        out["why"] = ("no catalogue row for this contract, so the venue has "
                      "said nothing about what it settles on")
        return out
    got = dict(row)
    sports_type = str(got.get("sports_type") or "").strip().lower()
    prose = {k: str(got.get(k) or "") for k in _FIELDS_CARRYING_PROSE}

    # ── GATHER BOTH KINDS OF EVIDENCE BEFORE DECIDING ────────────────
    #
    # The old code returned on the first simulation match, so it could never
    # report a CONFLICT -- and it returned REAL by falling off the end, which is
    # how absence became evidence. Both are gathered first now, and the verdict
    # is a function of what was found rather than of which loop exited first.
    sim: list = []
    for prefix in SIMULATED_SPORTS_TYPE_PREFIXES:
        if sports_type.startswith(prefix):
            sim.append({"field": "sports_type", "value": sports_type,
                        "matched": prefix})
            break
    for field in _FIELDS_CARRYING_PROSE:
        text = prose[field].lower()
        if not text:
            continue
        for marker in SIMULATED_MARKERS:
            if marker in text:
                sim.append({"field": field, "value": prose[field][:140],
                            "matched": marker})
                break

    real_prefix = None
    for prefix in REAL_SPORTS_TYPE_PREFIXES:
        if sports_type.startswith(prefix):
            # LONGEST MATCH, so `table_tennis_` is not reported as `tennis_`.
            if real_prefix is None or len(prefix) > len(real_prefix):
                real_prefix = prefix

    # ── CONTRADICTION FIRST: two publisher statements that disagree ──
    if sim and real_prefix:
        out.update(verdict=CONFLICTING, refusal=R_REALISM_EVIDENCE_CONFLICTS,
                   evidence={"real": {"field": "sports_type",
                                      "value": sports_type,
                                      "matched": real_prefix},
                             "simulated": sim},
                   why=("the venue classifies this contract as %r, which is a "
                        "real competition, AND its own %s says %r, which names "
                        "a simulated one. Two statements by the same publisher "
                        "disagree, so nothing here establishes what it settles "
                        "on" % (sports_type, sim[0]["field"],
                                sim[0]["matched"])))
        return out

    if sim:
        out.update(verdict=SIMULATED,
                   refusal=R_SIMULATED_NOT_THE_REAL_FIXTURE,
                   evidence=sim[0], all_evidence=sim,
                   why=("the venue's own %s says %r, which names a simulated "
                        "or electronic competition. The clubs may be real and "
                        "the match is not"
                        % (sim[0]["field"], sim[0]["matched"])))
        return out

    # ── THE ONLY PATH TO REAL, AND IT IS AN AFFIRMATIVE ONE ──────────
    #
    # This return was MISSING from the first rewrite: I deleted the fall-through
    # that produced REAL and did not replace it, so every legitimate contract
    # came back UNKNOWN -- soccer, UFC and table tennis all refused. Caught by
    # the same case list that caught the original defect, which is the argument
    # for keeping the real and simulated controls in it rather than only the
    # counterexamples.
    if real_prefix:
        out.update(verdict=REAL, refusal=None,
                   evidence={"field": "sports_type", "value": sports_type,
                             "matched": real_prefix},
                   why=("the venue classifies this contract as %r, a "
                        "recognized real-competition family, and neither its "
                        "classification nor its prose names a simulated one"
                        % (sports_type,)))
        return out

    # ── UNKNOWN, WITH THE REASON IT IS UNKNOWN ───────────────────────
    #
    # Three distinct absences, reported apart. Collapsing them would send an
    # operator looking for a missing column when the column is present and
    # carries a value nobody here recognises -- which is the case that matters,
    # because it is how a NEW simulated family would arrive.
    if sports_type in NOT_A_FIXTURE_SPORTS_TYPES:
        out.update(evidence={"field": "sports_type", "value": sports_type,
                             "matched": None},
                   why=("the venue classifies this as %r, a season-long "
                        "outright rather than a fixture between two sides. "
                        "Neither REAL nor SIMULATED is an honest answer to "
                        "'is this the real fixture'" % (sports_type,)))
        return out
    if not sports_type:
        out["why"] = ("the catalogue row carries no sports_type, so the venue "
                      "has made no classification. Prose alone does not "
                      "establish a real fixture -- the eBattles rows carry "
                      "real club names")
        return out
    out.update(evidence={"field": "sports_type", "value": sports_type,
                         "matched": None},
               why=("the venue classifies this as %r, which is not a "
                    "recognized real-competition family. It may be legitimate "
                    "and it may be a new simulated one; nothing here "
                    "distinguishes them, so it is unknown rather than "
                    "admitted" % (sports_type,)))
    return out


def is_real(row) -> bool:
    """True only on an affirmative REAL. UNKNOWN is not real."""
    return classify(row).get("verdict") == REAL


CATALOGUE_SQL = """
    SELECT market_slug, event_slug, event_title, question, sports_type
      FROM us_premap
     WHERE market_slug = $1
     LIMIT 1
"""


async def classify_contract(conn, market_slug) -> dict:
    """Read one contract's catalogue row and classify it.

    A read error is UNKNOWN, not REAL: `bettor_funded_pair_cycle` and the entry
    lane both treat an unreadable input as a refusal, and realism is no
    different from the open book in that respect.
    """
    out = {"market_slug": str(market_slug or "")}
    try:
        row = await conn.fetchrow(CATALOGUE_SQL, str(market_slug or ""))
    except Exception as exc:                                   # noqa: BLE001
        out.update(classify(None))
        out["read_error"] = type(exc).__name__
        out["why"] = ("the venue catalogue could not be read (%s), so realism "
                      "is unestablished rather than assumed"
                      % type(exc).__name__)
        return out
    out.update(classify(row))
    return out


def describe() -> dict:
    """What this module enforces, for the operator view and the report."""
    return {
        "question": ("is the venue's contract the real fixture, or a "
                     "simulation of it bearing the same club names"),
        "decided_by": ("the venue's own sports_type classification and its own "
                       "title, question and slug prose"),
        "never_decided_by": ("a league token. `ebfpl`, `ebfsa`, `ebfwca` and "
                             "`ebfwcb` share a prefix and inferring from it "
                             "would be the guess this module refuses"),
        "verdicts": [REAL, SIMULATED, UNKNOWN],
        "unknown_is_not_real": True,
        "refusals": [R_SIMULATED_NOT_THE_REAL_FIXTURE,
                     R_REALISM_NOT_ESTABLISHED],
        "measured_basis_2026_09_28": {
            "route": "research-sql run 258, research/epl_resolution_trace.sql",
            "us_premap_rows_naming_english_top_flight_clubs": 420,
            "events": 70,
            "their_sports_type": "efootball_team_full_time_winner",
            "example": ("atc-ebfpl-ars-che-2026-09-27-dh3-ars, "
                        "'eBattles: Arsenal vs. Chelsea'"),
            "real_soccer_events_for_comparison": {
                "soccer_team_full_time_winner": 294,
                "efootball_team_full_time_winner": 538,
            },
            "us_premap_rows_carrying_the_token_epl": 0,
        },
    }
