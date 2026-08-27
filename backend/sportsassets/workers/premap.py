"""Worker: pre-map the venue universe so copy-time mapping is a lookup.

Owner order 2026-08-24 ("move quicker with precision"): the wrong-side
incident's root causes all lived in resolve-at-trade-time — searches,
fuzzy scoring, a 20-second budget, sides missing from direct lookups.
This worker walks the venue's own event boards on a cycle and writes
every ACTIVE market side into `us_premap` with deterministic lookup
keys. At trade time the executor asks the table, not the network:
zero milliseconds, and the side identifier comes from the venue's own
side expansion — wrong-side-by-construction is impossible, ambiguity
refuses.

Rows are validated live at zero risk: while the quarantine holds, every
premap-resolved mapping is refused-but-recorded, so the MAPA audit
stream proves side fidelity on real signals before any dollar rides.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from ..db import get_pool
from .. import pmus

log = logging.getLogger(__name__)

REFRESH_SECONDS = 1800          # full sweep cadence
# FAST LANE (2026-08-25). The full sweep walks up to 40 pages across a
# now-12h..now+96h window and runs every 30 minutes, so a market listed
# just before tip-off can be invisible for up to half an hour — and
# that is exactly when the whales trade it.
#
# The unmapped census put two causes at 36.6% of sampled misses which
# are both this same gap wearing different names:
#   resolves 101/400 (25.3%)  — the row maps NOW but did not at the
#                               moment of his fill
#   type_prefix_filter_emptied 45/400 (11.3%) — the event was captured
#                               but that market type was not yet
#
# The fast lane re-sweeps only the IMMINENT window on a short cadence.
# It writes through the same _market_rows / _upsert path as the full
# sweep, so rows carry the venue's own side expansion and the
# wrong-side-by-construction property is identical — this is the same
# sweep, aimed narrowly and run often, not a second way of building a
# row.
FAST_REFRESH_SECONDS = float(
    os.environ.get("PREMAP_FAST_REFRESH_S", "180"))
FAST_WINDOW_BACK_H = float(os.environ.get("PREMAP_FAST_BACK_H", "3"))
FAST_WINDOW_FWD_H = float(os.environ.get("PREMAP_FAST_FWD_H", "14"))
# 8 -> 25 for the same reason; ~6s inside a 180s cadence.
FAST_MAX_PAGES = int(os.environ.get("PREMAP_FAST_PAGES", "25"))
PAGE_LIMIT = 100
# RAISED 40 -> 120 once truncation became visible (2026-08-25). The
# window is now-12h..now+96h — 4.5 days of a board carrying worldwide
# soccer plus ATP/WTA/ITF/challenger tennis plus the US majors — and
# 4,000 events did not cover it. At LIST_PACING_S=0.35 the extra 80
# pages cost ~28s inside a 1800s cadence, so the pacing that fixed the
# 2026-08-23 429s is untouched.
MAX_EVENT_PAGES = 120           # bounds a sweep at ~12k events
LIST_PACING_S = 0.35            # stay under venue rate limits (429 fix, 2026-08-23)
PRUNE_HOURS = 26                # rows unseen for a day age out
LIST_CALL_TIMEOUT_S = 30        # a hung SDK call must not wedge the sweep


def _items(resp, key: str) -> list:
    """The SDK returns either {"events": [...]} / {"markets": [...]} or a
    BARE LIST depending on version — `.get` on a list raises and the old
    sweep died silently on exactly that. Accept both shapes."""
    if isinstance(resp, dict):
        return list(resp.get(key) or [])
    if isinstance(resp, list):
        return list(resp)
    return []


def _norm(s: str | None) -> str:
    return pmus._norm(s)


_LINE_CTX = re.compile(
    r"(?:[+-]|\bover\b|\bunder\b|\bo\b|\bu\b|o/u|:)\s*(\d+(?:\.\d+)?)",
    re.I)


def _lines_of(text: str | None) -> set[str]:
    """Every line a text states — half-point lines anywhere, plus WHOLE
    numbers when they sit in a handicap or total context.

    Only \d+\.5 was matched before, so a whole-number line ('-3', 'O/U
    47') produced NO line at all on both the venue row and the whale's
    pick — and an empty line skipped the comparison entirely (leak-hunt
    round 3, 2026-08-24)."""
    t = text or ""
    out = {n for n in re.findall(r"\d+\.\d+", t)}
    out |= {m.group(1) for m in _LINE_CTX.finditer(t)}
    return {n for n in out if n}


def signed_line(text: str | None) -> str:
    """The SIGNED handicap a description carries ('-3.5', '+3.5'), or ''.

    _norm strips punctuation, so 'Chiefs -3.5' and 'Chiefs +3.5' both
    normalize to 'chiefs  3 5' — a whale taking +3.5 (getting points)
    matched the venue's -3.5 side (giving points), the exact opposite
    bet, on every spread. The sign must be read BEFORE normalization
    and compared on its own (leak-hunt round 3, 2026-08-24)."""
    m = re.search(r"([+-])\s*(\d+(?:\.\d+)?)", str(text or ""))
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}"


def date_of(slug: str | None) -> str:
    """The YYYY-MM-DD a slug names, or '' — the game identity that a
    title alone cannot carry."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", (slug or "").lower())
    return m.group(0) if m else ""


# The venue's slug grammar leads with a market-kind token; the whale's
# feed does not. Kept next to event_keys_for because that is the only
# place the asymmetry has to be reconciled.
_KIND_PREFIXES = frozenset({"aec", "atc", "asc", "tsc", "astatc"})


def _key_norm(text: str | None) -> str:
    """A title normalized for use as an EVENT KEY.

    pmus._norm replaces each punctuation RUN with a space and never
    collapses the result, so the two sides of one game disagree on
    spacing alone:

        "Arsenal vs. Chelsea"  -> "arsenal vs  chelsea"   (two spaces)
        "Arsenal vs Chelsea"   -> "arsenal vs chelsea"

    Two distinct keys, one game, and the deterministic lane cannot
    intersect them. Abbreviations are worse: "Inter Miami C.F." becomes
    "inter miami c f" while "Inter Miami CF" becomes "inter miami cf" —
    completely disjoint key sets for the same club.

    DELIBERATELY LOCAL. pmus._norm also produces `side_norm`, which is
    half of the us_premap unique index and half of match_side's
    equality test; changing it would silently rewrite what counts as
    the same SIDE, which is the wrong-side incident's own machinery.
    Keys are a lookup, sides are a decision, and only the lookup is
    widened here.
    """
    return re.sub(r"\s+", " ", _norm(text)).strip()


def _key_variants(text: str | None) -> set[str]:
    """Key spellings for one title: spacing-collapsed, and with dots
    and apostrophes DELETED rather than spaced, so "C.F." and "CF"
    reach the same string."""
    out: set[str] = set()
    base = _key_norm(text)
    if base:
        out.add(base)
    t = (text or "").replace(".", "").replace("'", "").replace("\u2019", "")
    tight = _key_norm(t)
    if tight:
        out.add(tight)
    return out


def event_keys_for(title: str | None, slug: str | None = None) -> list[str]:
    """Deterministic lookup keys for one event, built the same way at
    write time (venue side) and read time (whale side) so a match is an
    exact string hit, never a similarity score.

    DATE-STAMPED (leak-hunt round 2, 2026-08-24): title-derived keys
    alone are date-free, so 'yankees vs red sox' matched ANY Yankees-Red
    Sox game — a reviewer reproduced a 2026-08-23 whale trade resolving
    to the 2026-08-27 game's live orderable side. Every title key is now
    also emitted stamped with the event's date; resolve() uses the
    stamped keys whenever the whale's signal carries a date, so the game
    must agree. The bare keys remain for dateless signals."""
    keys: set[str] = set()
    t = pmus._clean_title(title)
    if t:
        # Every emission goes through _key_variants: raw _norm left
        # "arsenal vs  chelsea" facing "arsenal vs chelsea" and the two
        # could never meet.
        keys |= _key_variants(t)
    sm = pmus._surname_matchup(title)
    if sm:
        a, b = [p.strip() for p in re.split(r"\s+vs\s+", sm, flags=re.I)]
        # These were emitted with .lower() alone — not normalized at
        # all — so a surname carrying punctuation ("O'Connell") built a
        # key no normalized row could match.
        keys |= _key_variants(f"{a} vs {b}")
        keys |= _key_variants(f"{b} vs {a}")
    if t and " vs" in t.lower():
        sides = re.split(r"\s+vs\.?\s+", t, flags=re.I)
        if len(sides) == 2:
            na, nb = _key_norm(sides[0]), _key_norm(sides[1])
            if na and nb:
                keys |= _key_variants(f"{na} vs {nb}")
                keys |= _key_variants(f"{nb} vs {na}")
    d = date_of(slug)
    if d:
        keys |= {f"{k}@{d}" for k in list(keys)}
        s = (slug or "").lower()
        slug_key = s[: s.find(d) + len(d)].strip("-")
        keys.add(slug_key)
        # THE KIND PREFIX MADE THE TWO SIDES UNABLE TO MEET (2026-08-25).
        #
        # This function runs on BOTH sides — venue slugs at sweep time,
        # whale slugs at copy time — and the two grammars differ by one
        # token. The venue names the market TYPE in a leading prefix
        # (aec/atc/asc/tsc/astatc); the whale's feed does not.
        #
        #   whale  efl-don-mid-2026-08-25-spread-away-1pt5
        #            -> key  efl-don-mid-2026-08-25
        #   venue  asc-efl-don-mid-2026-08-25-away-1pt5
        #            -> key  asc-efl-don-mid-2026-08-25
        #
        # Same game, same date, same teams — and the keys can never
        # intersect. The deterministic lane was structurally dead for
        # every market whose venue slug carries a kind prefix, which is
        # all of them.
        #
        # This is the largest single cause in the funnel. The unmapped
        # census, over 400 sampled rows: no_key_intersection 207
        # (51.8%), and its first example was exactly this pair.
        #
        # Emitting the kind-stripped form as well lets the two grammars
        # meet on the game. It does NOT loosen market-type agreement:
        # resolve() still applies the PREFIX_FOR_TYPE filter to the rows
        # this key returns, so an asc- spread row and a tsc- total row
        # on one game both match the game key and are then separated by
        # type exactly as before. Game agreement comes from the date in
        # the key, which is untouched.
        head = slug_key.split("-", 1)
        if len(head) == 2 and head[0] in _KIND_PREFIXES:
            keys.add(head[1])
    return sorted(k for k in keys if k)


def _dated_admissible(keys: set[str], d: str) -> set[str]:
    """Which keys a DATED whale signal may match on.

    The rule is game agreement: a dated signal must never match another
    day's game. Two forms satisfy that — a title key stamped with the
    date ("yankees vs red sox@2026-08-25"), and a slug key that ENDS in
    the date ("efl-don-mid-2026-08-25").

    The old filter admitted the first plus anything starting with a
    venue kind prefix. That silently excluded the second: the whale's
    own slug key carries no kind prefix and no "@", so the one
    deterministic key his signal produces was thrown away on every
    dated trade — which is every trade. The deterministic lane could
    then only ever match on TITLE STRINGS.

    Ending in the date is exactly as strong a guarantee as carrying an
    "@" stamp, because it IS the date, in the position the venue and
    the whale both put it. Admitting it costs nothing and recovers the
    largest cause in the funnel (no_key_intersection, 51.8% of sampled
    unmapped rows).
    """
    ok = set(dated_keys(keys))
    ok |= {k for k in keys if k.startswith(
        tuple(f"{p}-" for p in _KIND_PREFIXES))}
    ok |= {k for k in keys if d and k.endswith(d)}
    return {k for k in ok if k}


def dated_keys(keys: list[str] | set[str]) -> list[str]:
    """Only the date-stamped keys — what a dated whale signal may match
    on, so a different day's game can never be a candidate."""
    return sorted(k for k in keys if "@" in k)


# The venue's slug grammar names the market TYPE in its prefix (the desk
# groups on it — see pmus.list_desk_events). Copy-time resolution must
# not let a moneyline pick land on a spread, a segment, or a prop:
# every market on an event shares one key set, so without this the
# candidate pool is the whole board (leak-hunt round 2, 2026-08-24).
PREFIX_FOR_TYPE = {"moneyline": {"aec", "atc"},
                   "spread": {"asc"},
                   "total": {"tsc"},
                   "btts": {"astatc"}}


def _prefix_of(identifier: str | None) -> str:
    return ((identifier or "").split("-", 1)[0] or "").lower()


class _SkipFallback(Exception):
    """Internal: a partially-successful events sweep must not fall
    through to the degraded markets path."""


def _questions_agree(a: str, b: str) -> bool:
    """Two normalized questions describe the same proposition."""
    if not a or not b:
        return False
    return a == b or a in b or b in a


# THE WHALE'S LINE IS IN HIS SLUG, AND NOTHING READ IT (census
# 2026-08-25).
#
#   UNMAPEG no_side_match: his=ucl-sf-hbs-2026-08-25-total-4pt5
#     outcome=Over keys=5 premap_rows=20
#     | outcome 'Over' matched none of ['over','under','over','under',...]
#
# The premap had the market, both sides, and the right words. The whale
# picked "Over". The rows say "over". They did not match because an
# over/under pick is meaningless without its line, so match_side
# requires BOTH sides to state one — and his line is not in his outcome
# text ("Over") nor in the market title. It is in his slug: total-4pt5.
#
# _lines_of never saw the slug, so his_lines came back empty and
# line_ok refused every row. Not one totals copy in the feed could
# match, for a reason that has nothing to do with totals being hard.
# no_side_match is 93 of 400 sampled misses (23.3%).
#
# THIS TIGHTENS THE GUARD RATHER THAN RELAXING IT. The requirement that
# the lines agree is untouched; we are supplying the line the whale
# actually stated instead of treating its absence as unknowable. A pick
# whose line disagrees with a row still refuses, and now for the right
# reason.
#
# Gated on the market TYPE so a moneyline can never acquire a line by
# accident: a stray digit pattern in a team code or an event slug would
# otherwise turn a currently-matching unlined pick into a refusal.
_SLUG_HALF_RE = re.compile(r"(?<![a-z0-9])(\d+)pt(\d)(?![a-z0-9])")
_SLUG_OU_RE = re.compile(r"(?<![a-z0-9])[ou](\d+)(?:pt(\d))?(?![a-z0-9])")
_LINED_TYPES = ("total", "spread", "prop")
# The same shapes copy_sports._LINE_RE / _TOTAL_RE accept as THE line
# when they decide the market type.
_SLUG_WHOLE_RE = re.compile(r"^(pos|neg|o|u)?(\d+)(?:pt(\d))?$")


def slug_lines(global_slug: str | None) -> set[str]:
    """Lines stated by the WHALE'S OWN SLUG, in `_lines_of` format.

    Two encodings appear in the feed: the spelled form (`total-4pt5`,
    `spread-away-1pt5`) and the compact one (`o8pt5`, `u10`). Both
    decode to the same "4.5" / "10" strings the venue rows carry.
    """
    s = (global_slug or "").lower()
    if not s:
        return set()
    from ..copy_sports import market_type_of

    if market_type_of(s) not in _LINED_TYPES:
        return set()
    out: set[str] = set()
    for m in _SLUG_HALF_RE.finditer(s):
        out.add(f"{int(m.group(1))}.{m.group(2)}")
    for m in _SLUG_OU_RE.finditer(s):
        out.add(f"{int(m.group(1))}.{m.group(2)}" if m.group(2)
                else str(int(m.group(1))))
    # WHOLE NUMBERS TOO (2026-08-25). The two patterns above require a
    # `pt` decimal marker or a leading o/u, so a whole-number line
    # stated in the feed's own grammar decodes to nothing:
    #
    #   nba-bos-mia-2026-08-24-bos-neg-10   -> spread, no line
    #   spl-ett-nsr-2026-08-25-total-3      -> total,  no line
    #
    # and an empty his_lines makes line_ok refuse every row. _lines_of
    # learned this exact lesson on 2026-08-24 ("only \d+\.5 was matched
    # before, so a whole-number line produced NO line at all") and the
    # decision was not carried across when slug_lines was written today
    # — failure mode (d), by me, twelve hours later.
    #
    # These are precisely the tokens copy_sports already accepts as THE
    # line when it decides the market type, so accepting them here
    # cannot type a market differently than the gate that let it in.
    #
    # POST-DATE TOKENS ONLY, never the raw slug: the date's own digits
    # would otherwise become lines.
    from ..copy_sports import _post_date_tokens

    suffix = _post_date_tokens([t for t in s.split("-") if t]) or []
    for tok in suffix:
        m = _SLUG_WHOLE_RE.match(tok)
        if m:
            out.add(f"{int(m.group(2))}.{m.group(3)}" if m.group(3)
                    else str(int(m.group(2))))
    return out


def _title_sign_is_his(his_title: str | None, outcome: str | None) -> bool:
    """Does the title's handicap describe HIS outcome, or the other team?

    A spread title names ONE team's handicap: "Spread: Doncaster
    (-1.5)". match_side took that sign as the whale's whenever his
    outcome text carried none — so a bare "Middlesbrough" pick was
    compared against Doncaster's -1.5, and Middlesbrough's own row
    (+1.5) mismatched and refused. Half of every spread pick, refused
    for stating the wrong team's sign.

    Only True when the title names ONE subject and that subject is his
    outcome. A title containing "vs" names two, and there is no way to
    tell which the sign belongs to, so it returns False and the caller
    treats the sign as UNSTATED rather than guessing.
    """
    # Both sides through _key_variants, for the same reason the event
    # keys are: "Inter Miami C.F." normalizes to "inter miami c f" and
    # "Inter Miami CF" to "inter miami cf", so a plain containment test
    # says the title is about a different club than his pick — and the
    # sign is then treated as unstated on a market where he DID state
    # one. Caught by its own test.
    titles = _key_variants(his_title)
    outs = _key_variants(outcome)
    if not titles or not outs:
        return False
    if any(" vs " in t for t in titles):
        return False
    return any(o in t for t in titles for o in outs if o)


# ── THE YES/NO BRIDGE (Phase 0, 2026-08-26) ─────────────────────────
#
# no_side_match is 239 of 400 sampled unmapped rejections (59.8%), and
# the production example that named the class:
#
#   his_slug  col-aus-scb-2026-08-27-scb   outcome "Yes"
#   his_title "Will SC Braga win on 2026-08-27?"
#   detail    outcome 'Yes' matched none of ['yes','no','yes','yes',...]
#
# The yes/no branch of match_side demands _questions_agree between his
# title and the venue's question WORDING, and the two feeds word the
# same proposition differently — a structural refusal on the whole
# "will TEAM win" family. The identity is recoverable without wording
# luck: his slug's final token names the market subject in team-code
# vocabulary, his title names the team, the venue question names both
# the subject AND the opponent, and the row's own event_title names the
# two teams. The bridge triangulates all four and refuses on ANY
# disagreement.
#
# DESIGNED UNDER ATTACK, NOT UNDER HOPE. Three candidate designs were
# adversarially attacked with the shipped-incident corpus (2026-08-23
# ambiguity-to-ordering, 2026-08-24 yes/no-to-named-team inversion, the
# Ito containment kill, the bare-Yes/No cross-proposition match, the
# date-as-line misparse) plus constructed wrong-market inputs:
# same-city teams, colliding codes, doubleheaders, win-or-draw /
# first-half / to-nil / extra-time qualifiers, derbies, partial
# capture. Two designs DIED in attack with executed wrong-market
# admissions. This one survived 56 executed checks with 0 failures, and
# every clause below exists because an attack demanded it.
#
# STRUCTURAL PROPERTIES (each is a shipped incident's rule):
#   * double uniqueness — exactly one candidate row (step 2) AND
#     exactly one event-wide subject match (step 3); no ordered
#     selection exists anywhere;
#   * literal side_norm yes/no polarity only — never a named side;
#   * distinctive-token SET EQUALITY — never containment;
#   * fully anchored, closed-tail grammars on BOTH feeds;
#   * unlined, unsigned rows only — the O/U branch is untouched;
#   * his title's date is consumed by the grammar and never fed to
#     _lines_of/signed_line, so "on 2026-08-27" cannot misparse as a
#     line ({'08','27'}) and a sign ('-08') the way it does today.
#
# PHASE 0: this function is consumed ONLY by resolve_explain's
# read-only probe and the census. Nothing on the order path calls it;
# resolve() takes no bridge argument yet. The census must first measure
# how much of the 239 the bridge would recover and which venue question
# tails exist, and any grammar extension is a reviewed change that
# re-runs the attack harness.

GENERIC_CLUB_TOKENS = frozenset({"fc", "cf", "sc", "ac", "afc", "ca",
                                 "cd", "club", "the", "de"})
# EXACTLY these ten. The list may only strip legal-form furniture,
# never identity markers: adding 'b', 'ii', 'w', 'u21', 'women' or
# 'reserves' would merge 'SC Braga B' into 'SC Braga' — the
# reserve-team collapse the attack corpus measured. An addition is a
# LOOSENING and needs the same review as a grammar change. Pinned.
# The round-2 tournament re-affirmed the pin the hard way: a design
# that added 'sk' died with an executed kill (SK Rapid Wien vs FC
# Rapid Bucharest — in Austrian/Balkan naming the legal-form prefix IS
# the disambiguator between same-name clubs).

_BRIDGE_LEAGUES = frozenset({"uecl", "uefa champions league"})
# 'primera nacional' REMOVED 2026-08-27 (round-2.1 verification): the
# seed leagues were never put through the same-day-sibling + in-league
# homonym review the constant demands of ADDITIONS, and the execution
# fleet killed through it four ways — Primera Nacional is the richest
# homonym league in world football (two Estudiantes, two San Martin,
# two Gimnasia y Esgrima, Defensores x2, Almirante Brown/Almagro), the
# venue writes clubs city-less, and a round-robin matchday satisfies
# every date gate on the wrong game. Re-entry requires the written
# review plus a homonym-disambiguation gate proven against those exact
# pairs. The probe's lg_seen channel keeps counting its occurrences.
# EXACTLY these three, each attested by >=1 complete untruncated
# win-question in the 2026-08-26 production census (uecl: 5 samples
# across 3 events; primera nacional: 1 win-question plus a
# corroborating draw-question; uefa champions league: the complete
# 107-char Viking FK sample). The league slot is COMPETITION IDENTITY:
# the round-2 tournament killed a design that left it open-vocabulary
# (a same-day basketball derby, a UEFA Youth League fixture and a
# Primera Division homonym all walked through an open [a-z ]+ slot).
# An addition is a LOOSENING with GENERIC_CLUB_TOKENS-grade review and
# must cite (i) a verbatim untruncated production win-question from
# >=2 distinct events, (ii) a written same-day-sibling analysis for
# that league's sport (doubleheaders share teams AND date, so the tail
# date cannot separate them; youth/women/reserve sections share club
# names; homonym clubs exist inside one league), and (iii) a full
# attack-harness re-run.

_BRIDGE_NAME_TOKEN_CAP = 5
# Max tokens in a strict-parsed subject or opponent. The longest
# observed real name is 4 ('fk borac banja luka'); the cap refuses the
# generic-token padding class ('... FK Austria Wien de the club in
# the ...', 6 tokens) which distinctive-set equality alone admits.

_BRIDGE_SCOPE_TOKENS = frozenset({
    "aggregate", "agg", "half", "halves", "fh", "sh", "extra", "et",
    "time", "penalties", "penalty", "pens", "shootout", "overtime",
    "ot", "leg", "legs", "qualification", "qualifying", "qualifier",
    "playoff", "playoffs", "series", "doubleheader", "game", "games",
    "reserve", "reserves", "res", "youth", "yth", "women", "womens",
    "u19", "u21", "u23", "b", "ii", "first", "second", "third", "1st",
    "2nd", "3rd", "quarter", "period", "inning", "innings", "session",
    "map", "set", "sets", "tiebreak", "draw", "tie", "advance",
    "advances",
    # Round 2.3 additions, each an EXECUTED round-3 fleet kill or its
    # sibling: aggregate synonyms, period synonyms, and the
    # translation space the English-only list lost to.
    "overall", "combined", "cumulative", "global", "decider",
    "qualify", "aet", "ht", "ft", "friendly", "friendlies",
    "exhibition", "testimonial", "amateur", "iii", "iv",
    "femenino", "feminino", "femenina", "femenil", "damen", "frauen",
    "feminine", "reservas", "reservi", "juvenil", "juveniles",
    "sub20", "sub21", "sub23", "shoot", "play",
    # Round 2.4: the fourth fleet resurrected the aggregate smuggle
    # through Portuguese ('Agregado'). The list stays a BELT — the
    # name-slot vocabulary is asymptotic by nature (documented below)
    # — but every executed word joins it.
    "agregado", "agregada", "aggregato", "gesamt", "samlet",
    "totale", "prolongacion", "prorroga", "verlangerung",
    "penales", "penaltis", "rigori"})

_BRIDGE_SCOPE_STEMS = (
    "prorrog", "prolong", "agregad", "aggregat", "verlanger",
    "penalt", "penales", "qualif", "reserv", "juvenil", "femin",
    "femen", "shootout", "playoff", "overtime", "aggregate")
# Round 2.5: exact-token membership loses to MORPHOLOGY — the fifth
# fleet resurrected the container class through Portuguese
# 'Prorrogação' (folds to 'prorrogacao'; the list held 'prorroga').
# A reviewed STEM family closes each inflection space with one entry.
# Stems are >= 5 chars and checked by startswith; reviewed against
# real club vocabulary ('penarol' diverges from 'penalt' at char 5).
# Same review bar as the token list.
# SCOPE IS NOT IDENTITY (round 2.2). The round-2.1 fleet executed an
# admission where the whale feed hung his match pick off a tie-level
# container ('SC Braga vs Austin FC (Aggregate)') and the fifth
# witness CORROBORATED the venue's aggregate market instead of vetoing
# it — every token of a side was read as club identity. Any of these
# tokens appearing in a name slot (either feed's event-title sides,
# the strict question's subject/opponent, the identifier's league
# slot, the event_slug's league slot) refuses the row. The list is
# CLOSED and reviewed: collisions with real club names ('First
# Vienna', a 'B' reserve side) refuse in the safe direction — an
# honest miss, never an admission. Additions/removals carry
# GENERIC_CLUB_TOKENS-grade review.

_BRIDGE_MARKET_TOKEN_RE = re.compile(r"^m[a-z]$")
# The identifier's post-date market token, ATTESTED SHAPE ONLY. The
# round-2.1 fleet rode '-agg'/'-fh'/'-et'/'-yth' post-date tokens
# (alphabetic, so the round-2 gate admitted them) onto sub-markets
# whose questions had lost their qualifier. Production identifiers
# carry 'ma'-family tokens; anything else refuses until a census
# attests otherwise.

_BRIDGE_TITLE_RE = re.compile(
    r"^will (?:the )?(?P<subj>.+?) win"
    r"(?: on (?P<iso>\d{4} \d{2} \d{2})"
    r"| on (?P<mon>[a-z]+) (?P<day>\d{1,2})(?: (?P<yr>\d{4}))?)?$")

_BRIDGE_Q_RE = re.compile(
    r"^will (?:the )?(?P<subj>.+?) win"
    r"(?: (?:against|vs) (?P<opp>[a-z0-9 ]+?))?"
    r"(?: (?:in|on) (?:their )?(?:match|game))?"
    r"(?: on (?P<qmon>[a-z]+) (?P<qday>\d{1,2})(?: (?P<qyr>\d{4}))?)?$")

_BRIDGE_Q_STRICT_RE = re.compile(
    r"^will (?:the )?(?P<subj>[a-z ]+?) win"
    r" against (?P<opp>[a-z ]+?)"
    r" in the (?P<lg>[a-z ]+?) match"
    r" scheduled for (?P<qmon>[a-z]+) (?P<qday>\d{1,2}) (?P<qyr>\d{4})$")
# THE ONE MEASURED TEMPLATE (round 2, 2026-08-26 census): "Will X win
# against Y in the <league> match scheduled for <Mon> <D>, <YYYY>?".
# 'against' only — 'vs' appears in zero observed win-questions, so a
# 'vs'-tailed row is unselectable but still raw-parses and BLOCKS.
# Year MANDATORY. The [a-z ] charsets make any digit in a name
# structurally unmatchable (subsuming round 1's digit checks). The
# end anchor makes the venue's own 110-char truncation ('... Aug 26,
# 202') a miss that still blocks via raw. Round 1's imagined strict
# branches (bare 'win against Y?', 'vs', 'in their match/game', 'on
# <mon> <day>') are gone from SELECTION: the census measured
# would_resolve=0 through them, and the round-2 tournament executed
# wrong-market admissions through the dateless bare form against
# shipped code (an aggregate-market pool, and a 2026-08-21 identifier
# pool under a 2026-08-27 slug). They remain in _BRIDGE_Q_RE, so such
# rows still block. STRICT ⊂ RAW is a pinned property: every string
# this pattern accepts also fullmatches _BRIDGE_Q_RE with the
# identical subj group, so a strict candidate is always self-visible
# to the Step-3 blocking scan.

_BRIDGE_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_BRIDGE_MONTHS.update({m[:3]: v for m, v in list(_BRIDGE_MONTHS.items())})


def _distinctive(subject_norm: str) -> frozenset:
    """Whole identity tokens of a normalized subject.

    NO length-based stripping: '<3 chars' would erase the reserve and
    women's markers, and 'SC Braga B' must yield {'braga','b'} so it
    can never set-equal 'SC Braga'."""
    return frozenset(t for t in subject_norm.split()
                     if t not in GENERIC_CLUB_TOKENS)


def _collapsed(s: str) -> str:
    return _norm(s).replace(" ", "")


def _collapsed_distinctive(s: str) -> str:
    """_collapsed with the reviewed-ten furniture removed, ORDER KEPT.

    The two-form corroboration's second form: 'FC Midtjylland' whose
    code is 'mid' misses under _collapsed ('fcmidtjylland') and hits
    here ('midtjylland'). Built from GENERIC_CLUB_TOKENS only — no new
    tokens, no reordering, no containment — so the Ito kill stays
    dead, and FK/HNK/KF/GNK/SK furniture (deliberately NOT in the ten;
    see the SK Rapid kill above) still misses BY DESIGN."""
    return "".join(t for t in _norm(s).split()
                   if t not in GENERIC_CLUB_TOKENS)


def _code_prefix_hit(name: str, code: str) -> bool:
    """Does a slug team-code prefix either form of the name?"""
    return (_collapsed(name).startswith(code)
            or _collapsed_distinctive(name).startswith(code))


def _folds_away(raw: str | None) -> bool:
    """Does ascii-folding DELETE letters from this string?

    Round 2.5, the fifth fleet's structural find: _norm is
    NFKD -> encode('ascii','ignore'), which does not TRANSLITERATE
    non-Latin scripts — it ERASES them. A Greek/Cyrillic/CJK scope
    qualifier ('(Затяжний)', '(Παράταση)', '(加時)') vanished before
    any gate could read it, leaving the clean template. Accented
    Latin survives folding (é decomposes to e + combining mark);
    any LETTER that would be deleted outright means the string
    carries content our entire gate stack is blind to — and blind
    refuses."""
    import unicodedata as _ud

    for ch in _ud.normalize("NFKD", str(raw or "")):
        if ord(ch) > 127 and _ud.category(ch)[0] not in ("M", "C"):
            # Round 2.6: letters-only was one category too narrow —
            # the sixth fleet erased a '(٢)' game-2 marker (category
            # Nd) through the letter check, mapping a full-match pick
            # onto the second-instance market. ANY erased character
            # outside combining marks (Mn — the accent case that must
            # keep recovering Fenerbahçe/Potosí) and format controls
            # (Cf — cosmetic, verified) is content the gate stack
            # goes blind to: digits, number symbols, dingbats,
            # non-decomposing punctuation. Blind refuses.
            return True
    return False


def _has_scope_token(norm_name: str) -> bool:
    """Any reviewed scope token in a normalized name string?

    Adjacent-pair COLLAPSE included (round 2.3): _norm splits
    punctuation, so '(Shoot-Out)' arrives as 'shoot out' and a
    single-token membership test on a list containing 'shootout'
    walks straight past its own entry — an executed round-3 kill.
    Any adjacent pair whose concatenation is a listed token refuses
    too."""
    toks = norm_name.split()
    if any(t in _BRIDGE_SCOPE_TOKENS for t in toks):
        return True
    if any(t.startswith(_BRIDGE_SCOPE_STEMS) for t in toks):
        return True
    # Round 2.4: pairs were not enough — '(P l ay Off)'-style
    # 3+-fragment splits evaded the bigram, and '(S.O.)' arrived as
    # 's o', two letters that can never re-join to 'shootout'. All
    # adjacent runs up to 4 fragments re-join; any SINGLE-LETTER
    # token in a name slot refuses outright (no attested club name
    # carries one — 'SC Braga B' is already scope-refused via 'b',
    # and an abbreviation dot-split is exactly this shape).
    if any(len(t) == 1 and t not in ("y", "e") for t in toks):
        # 'y'/'e' are the Romance conjunctions real club names carry
        # ('Defensa y Justicia', 'Gimnasia y Esgrima') — exempted,
        # reviewed. Every other single letter refuses: '(S.O.)'
        # arrives as 's o', and no attested club name carries one.
        return True
    for n in (2, 3, 4):
        for i in range(len(toks) - n + 1):
            if "".join(toks[i:i + n]) in _BRIDGE_SCOPE_TOKENS:
                return True
    return False


def _bridge_title_subject(his_title: str | None,
                          his_slug: str | None) -> tuple[str | None, str]:
    """The team his 'Will X win …?' title asks about, or (None, why).

    The date clause is consumed HERE and compared against his slug's
    own date — never handed to _lines_of/signed_line, which today read
    'on 2026-08-27' as a line and a sign and refuse every dated yes/no
    title at _yn_line_ok."""
    n = " ".join(_norm(his_title).split())
    m = _BRIDGE_TITLE_RE.fullmatch(n)
    if not m:
        return None, "title_not_win_shape"
    if not m.group("iso") and not m.group("mon"):
        # DATED TITLES ONLY (round-2 tightening). A bare 'Will X win?'
        # can be an aggregate/advance market riding a dated
        # moneyline-shaped slug onto a single-match venue row — the
        # tournament constructed that admission. No pinned recovery
        # uses a dateless title; if a round-3 census shows real
        # moneyline picks with dateless titles, re-admitting is a
        # reviewed change.
        return None, "title_undated"
    d = date_of(his_slug)
    if m.group("iso"):
        if not d or m.group("iso").replace(" ", "-") != d:
            return None, "title_date_mismatch"
    elif m.group("mon"):
        mo = _BRIDGE_MONTHS.get(m.group("mon"))
        if mo is None:
            return None, "title_month_unknown"
        if not d:
            return None, "title_date_mismatch"
        yr = m.group("yr")
        want = (int(d[0:4]), int(d[5:7]), int(d[8:10]))
        got = (int(yr) if yr else want[0], mo, int(m.group("day")))
        if want != got:
            return None, "title_date_mismatch"
    subj = " ".join(m.group("subj").split())
    if any(ch.isdigit() for ch in subj):
        # 'FC Schalke 04' refuses — the safe direction. A digit in a
        # subject is more often a line, a game number or a year that
        # escaped the grammar than a team identity.
        return None, "subject_has_digit"
    return subj, "ok"


def _q_parse_raw(question: str | None):
    """Step-3 BLOCKING form: anchored parse only, no validation.

    Deliberately permissive relative to strict: a question that parses
    here but fails strict validation is unselectable but still BLOCKS
    uniqueness — refusing whether or not the true sibling was captured.
    (Input coerced to str, round 2.6 — a poisoned non-string row must
    refuse at the gates, not detonate the scan; the REGEX itself stays
    byte-frozen.)
    """
    n = " ".join(_norm(str(question or "")).split())
    return _BRIDGE_Q_RE.fullmatch(n)


def _q_parse_strict(question: str | None, his_dist: frozenset,
                    other_code: str, event_title: str | None,
                    slug_date: str, his_opp_dist: frozenset,
                    his_sides: frozenset) -> tuple[frozenset | None,
                                                   str]:
    """Step-2 SELECTION form: the ONE measured template, validated.

    All clauses conjunctive; any miss refuses the row. The opponent
    clause is MANDATORY and validated against the row's own event_title
    by distinctive-set EQUALITY; the league is a CLOSED whitelist; the
    tail date must equal the whale slug's date exactly. slug_date is
    guaranteed non-empty by bridge_explain's gate 5."""
    question = str(question or "")
    if len(question) >= 108:
        # Round 2.2: a scope qualifier rendered AFTER the date is
        # amputated by any ~110-char truncation in the pipeline,
        # leaving the exact clean template. A question long enough to
        # have been cut cannot prove it was not; unprovable refuses.
        # Long fixtures become honest misses; the probe's qlen channel
        # measures the cost and whether truncation exists at all.
        return None, "question_maybe_truncated"
    n = " ".join(_norm(question).split())
    m = _BRIDGE_Q_STRICT_RE.fullmatch(n)
    if m is None:
        return None, "q_not_strict_shape"
    subj = " ".join(m.group("subj").split())
    if len(subj.split()) > _BRIDGE_NAME_TOKEN_CAP:
        return None, "q_subject_too_long"
    opp = " ".join(m.group("opp").split())
    if len(opp.split()) > _BRIDGE_NAME_TOKEN_CAP:
        return None, "q_opp_too_long"
    lg = " ".join(m.group("lg").split())
    if lg not in _BRIDGE_LEAGUES:
        return None, "league_not_whitelisted"
    mo = _BRIDGE_MONTHS.get(m.group("qmon"))
    if mo is None:
        # 'sept' is unknown TODAY — a conscious September cliff. The
        # probe's strict_trace records the month token verbatim, so
        # the cliff resolves itself within days of the venue first
        # emitting 'Sept'/'Sep' — by observation, not guess.
        return None, "q_month_unknown"
    want = (int(slug_date[0:4]), int(slug_date[5:7]), int(slug_date[8:10]))
    if (int(m.group("qyr")), mo, int(m.group("qday"))) != want:
        return None, "q_date_mismatch"
    if _has_scope_token(subj) or _has_scope_token(opp):
        return None, "scope_token_in_name"
    sides = re.split(r"\s+vs\.?\s+", _norm(event_title or ""))
    sides = [" ".join(s.split()) for s in sides if s.strip()]
    if len(sides) != 2:
        return None, "event_title_unsplittable"
    if any(_has_scope_token(s) for s in sides):
        return None, "scope_token_in_event"
    if frozenset(sides) != his_sides:
        # FEED AGREEMENT (round 2.2; wording corrected 2.6: both
        # sides are compared post-_norm — accent-folded, never
        # stripped — and _folds_away now guarantees the folded form
        # lost nothing content-bearing). Distinctive-set
        # equality let two independently TERSE renderings corroborate
        # a name-twin's fixture ('Rapid vs Union' taking FC Rapid
        # Bucuresti vs FC Union Berlin — different game). The two
        # feeds must render the fixture identically, unstripped;
        # furniture drift between feeds is an honest miss the census
        # counts, never a match.
        return None, "event_title_feed_mismatch"
    od = _distinctive(opp)
    other_sides = [s for s in sides if _distinctive(s) != his_dist]
    if len(other_sides) != 1:
        # Zero: a derby rendering. Two: the row's event does not even
        # CONTAIN his team — executed in round-2.1 verification: a
        # 'FC Porto vs Austin FC' row took a Braga pick because
        # his side was only ever used to EXCLUDE, never REQUIRED.
        return None, "row_event_missing_his_team"
    if not any(_distinctive(s) == od for s in other_sides):
        return None, "opp_not_event_team"
    if od != his_opp_dist:
        # THE FIFTH WITNESS (round 2.1). Every executed kill in the
        # verification fleet rode a channel with no EXTERNAL anchor:
        # the question's opp was validated against the row's OWN
        # event_title — a sub-event row always agrees with itself —
        # so '(Aggregate)' / '(First Half)' / 'in extra time' rode in
        # the opp slot, and name-twin fixtures (FC Rapid Bucuresti
        # for SK Rapid Wien) self-corroborated. The whale's own feed
        # names his ACTUAL opponent in his event title; the question's
        # opponent must set-equal it, or the row is about a different
        # proposition or a different game. Fail-closed: a whale event
        # title that cannot name the opponent refuses upstream, and
        # the census prices what that costs.
        return None, "opp_not_his_opponent"
    if not (_code_prefix_hit(opp, other_code)
            or any(_code_prefix_hit(s, other_code)
                   for s in other_sides)):
        return None, "opp_not_corroborated"
    return _distinctive(subj), "ok"


def _bridge_ident_ok(identifier: str | None, slug_date: str,
                     c1: str = "", c2: str = "",
                     whale_lg: str = "") -> bool:
    """A candidate row's identifier must carry at most ONE post-date
    token, purely alphabetic — a '-2' or '-game-2' doubleheader
    disambiguator refuses the row — its embedded YYYY-MM-DD triple
    must equal the whale slug's date, AND (round 2.1) its pre-date
    body must be exactly [prefix, league, a, b] with {a, b} equal to
    the whale slug's two team codes.

    The date clause is round 2's wrong-game fix (a 2026-08-21 pool
    admitted under a 2026-08-27 slug). The shape clause is round
    2.1's: the verification fleet rode sub-event identifiers
    ('...-scb-aus-AGG-2026-08-27-ma') and wrong-game identifiers
    whose own codes named the other fixture ('...-smsj-ebac-...' for
    a san/est pick) through a gate that never read the identifier's
    body. The venue's own codes are a witness; now they testify. The
    venue LEAGUE token stays unread — league aliasing (whale 'bol1',
    venue 'lpb', same game) is measured production behavior."""
    from ..copy_sports import _post_date_tokens

    parts = [p for p in str(identifier or "").lower().split("-") if p]
    toks = _post_date_tokens(parts)
    if toks is None or len(toks) > 1:
        return False
    if toks and not _BRIDGE_MARKET_TOKEN_RE.fullmatch(toks[0]):
        # Round 2.2: alphabetic was not enough — '-agg'/'-fh'/'-et'
        # post-date tokens rode sub-markets whose questions had lost
        # the qualifier. Only the attested market-token family passes.
        return False
    for i in range(len(parts) - 2):
        if (re.fullmatch(r"\d{4}", parts[i]) and parts[i + 1].isdigit()
                and parts[i + 2].isdigit()):
            if (f"{parts[i]}-{parts[i + 1]}-{parts[i + 2]}"
                    != slug_date):
                return False
            body = parts[1:i]
            if len(body) != 3 or {body[1], body[2]} != {c1, c2}:
                return False
            # LEAGUE-TOKEN EQUALITY (round 2.3). Round 2.2 left this
            # slot unread-except-denylist for aliasing, and the
            # round-3 fleet walked the translation space straight
            # through it: 'femenino', 'reservas', 'juvenil', 'sub20'
            # all admitted where 'women'/'reserves'/'youth' refused.
            # A vocabulary can never be proven closed; an equality
            # can. The venue's league token must EQUAL the whale
            # slug's own league token. Aliased leagues (whale 'bol1',
            # venue 'lpb') become honest misses the trace counts —
            # and the youth-mirror residual (whale 'uyl' onto venue
            # 'ucl') dies with them. The scope check stays as belt.
            return (body[0] == whale_lg
                    and body[0] not in _BRIDGE_SCOPE_TOKENS)
    return False


def _bridge_event_slug_ok(event_slug: str | None, slug_date: str,
                          c1: str, c2: str, whale_lg: str = "",
                          ident_prefix: str = "") -> bool:
    """The row's event_slug must carry the same fixture shape as the
    identifier: [prefix, league, a, b, YYYY, MM, DD] with {a, b} equal
    to the whale slug's codes, no post-date tokens, a scope-free
    league slot, and the date equal to the whale's. Round 2.2: the
    fleet rode sub-market container slugs ('ucl-sha-mid-AGG-...') and
    variant suffixes that no gate read. A self-description is a
    witness; now it testifies."""
    parts = [p for p in str(event_slug or "").lower().split("-") if p]
    for i in range(len(parts) - 2):
        if (re.fullmatch(r"\d{4}", parts[i]) and parts[i + 1].isdigit()
                and parts[i + 2].isdigit()):
            if (f"{parts[i]}-{parts[i + 1]}-{parts[i + 2]}"
                    != slug_date):
                return False
            if parts[i + 3:]:
                return False
            body = parts[:i]
            if len(body) != 4:
                # Round 2.4: the attested event_slug shape carries a
                # venue prefix; a 3-body slug skipped the prefix
                # check entirely (fourth-fleet finding). Exactly four,
                # fail closed.
                return False
            if not ident_prefix or body[0] != ident_prefix:
                return False
            body = body[1:]
            if len(body) != 3 or {body[1], body[2]} != {c1, c2}:
                return False
            return (body[0] == whale_lg
                    and body[0] not in _BRIDGE_SCOPE_TOKENS)
    return False


def _bridge_trace_row(trace: dict, r: dict, gate: str,
                      qd: frozenset | None, his_dist: frozenset,
                      other: str) -> None:
    """Record ONE kept row's first failing Step-2 gate (probe only).

    Bounded to 12 rows. The extras exist so a round-3 review reads
    evidence instead of guessing: the verbatim league value names
    whitelist candidates with counts; the verbatim month token
    resolves the September cliff by observation; the set differences
    and the two-form shadow-eval size the furniture-token miss class
    before anyone proposes stripping a token."""
    rows = trace.setdefault("row_gates", [])
    if len(rows) >= 12:
        return
    ident = (r.get("identifier") or "")
    entry: dict = {"ident_tail": ident[-40:], "gate": gate}
    n = " ".join(_norm(r.get("question")).split())
    m = _BRIDGE_Q_STRICT_RE.fullmatch(n)
    if gate == "league_not_whitelisted" and m:
        entry["lg_seen"] = " ".join(m.group("lg").split())[:60]
    elif gate == "q_month_unknown" and m:
        entry["month_seen"] = m.group("qmon")[:20]
    elif gate == "subject_set_mismatch" and qd is not None:
        entry["dist_diff"] = sorted(qd ^ his_dist)[:8]
    elif gate == "opp_not_event_team" and m:
        opp = " ".join(m.group("opp").split())
        entry["opp_dist"] = sorted(_distinctive(opp))[:8]
        sides = re.split(r"\s+vs\.?\s+",
                         _norm(r.get("event_title") or ""))
        entry["side_dists"] = [sorted(_distinctive(
            " ".join(s.split())))[:8] for s in sides if s.strip()][:2]
    elif gate == "opp_not_corroborated" and m:
        opp = " ".join(m.group("opp").split())
        entry["shadow"] = {
            "other": other,
            "collapsed": _collapsed(opp)[:40],
            "collapsed_distinctive": _collapsed_distinctive(opp)[:40],
        }
    rows.append(entry)


def bridge_explain(rows_kept: list[dict], rows_all: list[dict],
                   outcome: str | None, his_title: str | None,
                   his_slug: str | None,
                   his_event_title: str | None = None,
                   trace: dict | None = None) -> tuple[dict | None, str]:
    """The full bridge: (hit, 'ok') or (None, named_refusal).

    Consulted only AFTER match_side returns None — a primary hit always
    wins. rows_kept is the prefix-filtered pool selection runs over;
    rows_all is the PRE-filter pool the event-wide blocking scan runs
    over, any prefix family, lined or not.

    trace, when a dict, is filled with the probe's evidence: per-row
    first-failing gates (with the league value on
    league_not_whitelisted, the month token on q_month_unknown, and
    set differences on subject/opp mismatches), the two-form
    corroboration shadow-eval on every corroboration refusal, and the
    blocker set on event_scan_ambiguous. Tracing changes NO decision —
    it only records why each was made."""
    from ..copy_sports import _post_date_tokens, market_type_of

    on = _norm(outcome)
    if on not in ("yes", "no"):
        return None, "not_yes_no"
    if market_type_of(his_slug or "") != "moneyline":
        # totals/spreads/props never enter, and the doubleheader whale
        # slug ('…-2026-07-22-2-nyy') types spread today — measured —
        # so it refuses here.
        return None, "wrong_type"
    parts = [p for p in (his_slug or "").lower().split("-") if p]
    suffix = _post_date_tokens(parts)
    if not suffix or len(suffix) != 1 or not suffix[0].isalpha():
        return None, "no_subject_token"
    code = suffix[0]
    if len(code) < 3:
        return None, "code_too_short"
    d = date_of(his_slug)
    pre = parts[:parts.index(d[:4])] if d and d[:4] in parts else None
    if not pre or len(pre) != 3:
        return None, "slug_shape"
    _lg, c1, c2 = pre
    if c1 == c2:
        return None, "degenerate_codes"
    if code not in (c1, c2):
        # kills 'draw', 'dnb', surname tails — the final token must BE
        # one of the two team codes
        return None, "code_not_team"
    other = c2 if code == c1 else c1
    subj, why = _bridge_title_subject(his_title, his_slug)
    if subj is None:
        return None, why
    his_dist = _distinctive(subj)
    if not his_dist:
        return None, "empty_distinctive"
    mine = _code_prefix_hit(subj, code)
    theirs = _code_prefix_hit(subj, other)
    if not mine or theirs:
        # whale-internal corroboration, veto-only, two-form and
        # SYMMETRIC: the title's team must prefix-match HIS slug code
        # under either form ('fcmidtjylland' OR 'midtjylland') and
        # must not also match the opponent's under either form. Both
        # or neither is a collision, and a collision refuses. (When
        # the slug's codes collide the other way — 'man' prefixing
        # 'manchestercity' — the opp-corroboration clause in
        # _q_parse_strict lands the refusal instead.)
        if trace is not None:
            trace["gate10"] = {
                "code": code, "other": other,
                "collapsed": _collapsed(subj)[:40],
                "collapsed_distinctive":
                    _collapsed_distinctive(subj)[:40],
                "mine": mine, "theirs": theirs,
            }
        return None, "slug_corroboration_failed"
    whale_lg = parts[0] if parts else ""
    if _folds_away(his_event_title) or _folds_away(his_title):
        return None, "nonlatin_content"
    sides_h = re.split(r"\s+vs\.?\s+", _norm(his_event_title or ""))
    sides_h = [" ".join(s.split()) for s in sides_h if s.strip()]
    if len(sides_h) != 2:
        # FIFTH WITNESS, whale side (round 2.1): his own event title
        # names both teams of the game he actually bet. A title that
        # cannot be split refuses — fail closed; the probe's
        # bridge['his'] channel measures what this costs.
        return None, "his_event_unsplittable"
    if all(len(_distinctive(s)) < 2 for s in sides_h):
        # THE TWIN CLASS, CLOSED INTERIM (round 2.4). The fourth
        # fleet executed wrong-game admissions with REAL club pairs:
        # 'FC Rapid' (Wien) taking FC Rapid Bucuresti's fixture,
        # 'FC Dinamo' (Zagreb) taking Kyiv's — furniture clears a
        # raw-token floor while carrying zero identity, and UECL is
        # saturated with Rapid/Dinamo/Union/Sparta/Slavia twins. When
        # BOTH sides reduce to one distinctive token, no readable
        # witness separates same-named fixtures; the one that could —
        # market_slug, which carries city/country — is captured by
        # the probe but its production shape is UNATTESTED, and
        # grammars built on imagined shapes recover zero. So the
        # class refuses until the census attests market_slug and a
        # round-2.4b gate reopens it on the real witness. Phase 0
        # makes this free: nothing trades on the bridge, so the cost
        # is a census count, not a dollar.
        return None, "sides_single_distinctive"
    if any(len(s.split()) < 2 for s in sides_h):
        # EVIDENCE FLOOR (round 2.3). The round-3 fleet executed an
        # identical-terse wrong-game admission: both feeds rendering
        # 'Rapid vs Union' — string identity is not game identity
        # when each side carries one bare token; the terse prefixes
        # corroborate the wrong fixture's codes by construction. A
        # single-token side is insufficient identity; single-name
        # clubs (Floriana) become honest misses the census counts,
        # until the market_slug witness (captured, unobserved,
        # ungated) is attested for a round-2.4 gate.
        return None, "his_event_side_thin"
    if any(_has_scope_token(s) for s in sides_h):
        # Round 2.2: his own feed can hang a match pick off a
        # tie-level container ('SC Braga vs Austin FC (Aggregate)') —
        # the fleet executed the fifth witness CORROBORATING the
        # venue's aggregate market through exactly this. A scope token
        # in his event title is a scope disagreement inside his own
        # feed; unresolvable refuses.
        return None, "his_event_has_scope"
    hd = [_distinctive(s) for s in sides_h]
    mine_side = [i for i, d in enumerate(hd) if d == his_dist]
    if len(mine_side) != 1:
        # Zero: his title's team is not in his own event title (feeds
        # disagree about his own game). Two: a derby rendering. Both
        # refuse.
        return None, "his_event_side_mismatch"
    his_opp_dist = hd[1 - mine_side[0]]
    if not his_opp_dist:
        return None, "his_event_opp_empty"
    his_sides = frozenset(sides_h)
    if any(not (r.get("event_slug") or "").strip() for r in rows_all):
        # Round 2.1: the markets-mode ingest fallback can stamp ''
        # on every row, collapsing the multi-event set to {''} and
        # silently disarming this gate — executed in verification
        # with a two-event pool. An unlabeled pool is unverifiable;
        # unverifiable refuses.
        return None, "event_slug_missing"
    if len({r.get("event_slug") or "" for r in rows_all}) > 1:
        return None, "multi_event_pool"
    cands = []
    for r in rows_kept:
        gate = None
        qd = None
        if _norm(r.get("side_norm")) != on:
            gate = "side_polarity"
        elif r.get("kind") != "side":
            # Round 2.6: 'kind' was the next SELECTed-but-unread
            # field. The sweep writes exactly two values — 'side'
            # (marketSides rows, the only shape the bridge grammar
            # was built for) and 'contract' (whole-market fallback
            # rows with a hardcoded intent). Only the attested shape
            # passes.
            gate = "kind_not_side"
        elif str(r.get("line") or "").strip() or \
                str(r.get("signed") or "").strip():
            gate = "lined_or_signed"
        elif not r.get("intent"):
            gate = "no_intent"
        elif _folds_away(r.get("question")) or \
                _folds_away(r.get("event_title")):
            gate = "nonlatin_content"
        elif not _bridge_ident_ok(r.get("identifier"), d, c1, c2,
                                  whale_lg):
            gate = "ident_suffix_or_date"
        elif not _bridge_event_slug_ok(
                r.get("event_slug"), d, c1, c2, whale_lg,
                next(iter([p for p in str(r.get("identifier") or "")
                           .lower().split("-") if p]), "")):
            gate = "event_slug_shape"
        else:
            qd, qwhy = _q_parse_strict(r.get("question"), his_dist,
                                       other, r.get("event_title"), d,
                                       his_opp_dist, his_sides)
            if qd is None:
                gate = qwhy
            elif qd != his_dist:
                gate = "subject_set_mismatch"
        if gate is None:
            cands.append(r)
        elif trace is not None:
            _bridge_trace_row(trace, r, gate, qd, his_dist, other)
    if len(cands) != 1:
        return None, ("no_candidate_row" if not cands
                      else "multiple_candidates")
    blockers = set()
    for r in rows_all:
        if _folds_away(r.get("question")):
            # A question whose letters fold away raw-parses as a
            # DIFFERENT string than the venue wrote. Unreadable rows
            # in the pool make the blocking scan itself unreliable;
            # unreliable refuses (round 2.5).
            return None, "nonlatin_in_pool"
        m = _q_parse_raw(r.get("question"))
        if m is None:
            continue
        sd = _distinctive(" ".join(m.group("subj").split()))
        if sd == his_dist:
            blockers.add((str(r.get("identifier") or "").lower(),
                          _norm(r.get("question"))))
    want = (str(cands[0].get("identifier") or "").lower(),
            _norm(cands[0].get("question")))
    if blockers != {want}:
        if trace is not None:
            trace["blockers"] = {
                "n": len(blockers),
                "identifiers": sorted(b[0][:40] for b in blockers)[:12],
            }
        return None, "event_scan_ambiguous"
    return cands[0], "ok"


def match_side_bridge(rows_kept: list[dict], rows_all: list[dict],
                      outcome: str | None, his_title: str | None,
                      his_slug: str | None,
                      his_event_title: str | None = None) -> dict | None:
    """bridge_explain's hit, reasonless — the match_side-shaped form."""
    hit, _why = bridge_explain(rows_kept, rows_all, outcome, his_title,
                               his_slug, his_event_title)
    return hit


def match_side(rows: list[dict], outcome: str | None,
               his_title: str | None,
               his_slug: str | None = None) -> dict | None:
    """Pick the unique premap row that IS the whale's outcome.

    Precision rules (each one is a shipped incident):
    - Yes/No picks match only literal yes/no sides — never a named team
      (inversion incident 2026-08-24).
    - Over/Under picks require an over/under side AND line equality with
      the whale's title (wrong-line class).
    - Named picks match by exact normalized equality, else by a unique
      distinctive surname token (>3 chars). Two candidates passing is
      ambiguity, and ambiguity refuses — a tie must never fall to
      venue ordering (incident 2026-08-23).
    - A lined row never matches an unlined pick and vice versa.
    """
    on = _norm(outcome)
    if not on:
        return None
    # the whale's line may live in his title, his outcome ("Over 3.5")
    # OR — for most of the feed — only in his slug (`total-4pt5`), which
    # nothing read until 2026-08-25. See slug_lines above.
    his_lines = (_lines_of(his_title) | _lines_of(outcome)
                 | slug_lines(his_slug))

    def line_ok(r: dict) -> bool:
        """Line agreement for the OVER/UNDER branch.

        The old form ended in a bare `return True`, so a row whose line
        failed to stamp satisfied ANY lined pick: a whale's Over 2.5
        matched an Over 9.5 row — a different bet entirely (leak-hunt
        round 3, 2026-08-24). An over/under pick is MEANINGLESS without
        its line, so both sides must state one and they must agree.
        """
        rl = (r.get("line") or "").strip()
        if not his_lines or not rl:
            return False
        return rl in his_lines

    cands: list[dict] = []
    if on in ("yes", "no"):
        # A BARE Yes/No NAMES NOTHING (leak-hunt round 3): every
        # derivative on an event shares one key set, so a whale's 'Yes'
        # on "Will both teams score?" matched the clean-sheet market's
        # Yes — a different proposition entirely. The QUESTION must
        # correspond, and with no question to compare we refuse.
        want_q = _norm(his_title)
        if not want_q:
            return None
        # LINE AND SIGN, EXPLICITLY (certification audit 2026-08-24):
        # the question check above happens to catch a differing line
        # because the line usually appears IN the question text — but
        # that is emergent, not guaranteed. A yes/no pick on a lined
        # market states a line and a sign; both must agree outright.
        his_signed_yn = signed_line(outcome) or signed_line(his_title)

        def _yn_line_ok(r: dict) -> bool:
            rs = (r.get("signed") or "").strip()
            if his_signed_yn or rs:
                if not rs or rs != his_signed_yn:
                    return False
            rl = (r.get("line") or "").strip()
            if bool(rl) != bool(his_lines):
                return False
            return not rl or rl in his_lines

        cands = [r for r in rows
                 if _norm(r.get("side_norm")) == on
                 and _questions_agree(want_q, _norm(r.get("question")))
                 and _yn_line_ok(r)]
    elif on.split()[:1] and on.split()[0] in ("over", "under"):
        want = on.split()[0]
        # side descriptions carry their line ("Over 2.5" → "over 2 5");
        # match on the leading token, corroborate the line separately
        cands = [r for r in rows
                 if (r.get("side_norm") or "").split()[:1] == [want]
                 and line_ok(r)]
    else:
        # LINE AGREEMENT, not line ABSENCE (leak-hunt round 2,
        # 2026-08-24): requiring an empty line excluded every spread row
        # — _market_rows stamps the line on them — so spreads wrote rows
        # that could never match and the lane was silently dead for the
        # whole type. A lined pick matches a row whose line it names; an
        # unlined pick still matches only unlined rows.
        # WHOSE SIGN IS IT? (2026-08-25.)
        #
        # This was `signed_line(outcome) or signed_line(his_title)`, and
        # a spread title names ONE team's handicap. So a bare
        # "Middlesbrough" pick on "Spread: Doncaster (-1.5)" borrowed
        # Doncaster's sign, mismatched Middlesbrough's own +1.5 row, and
        # refused. Reproduced against the live matcher: "Doncaster"
        # resolves, "Middlesbrough" returns None — half of every spread
        # pick, refused for stating the other team's sign.
        #
        # His outcome always speaks for him. The title speaks for him
        # only when it names ONE subject and that subject is his pick.
        his_signed = signed_line(outcome)
        if not his_signed and _title_sign_is_his(his_title, outcome):
            his_signed = signed_line(his_title)

        def _lined_ok(r: dict) -> bool:
            # SIGN AGREEMENT WHEN HE STATED ONE (leak-hunt round 3):
            # _norm erases +/-, so 'Chiefs -3' and 'Chiefs +3' are the
            # same string and only the sign separates giving points from
            # getting them. THIS PATH IS UNCHANGED — a stated sign must
            # still match exactly, which is the 2026-08-24 inversion
            # guard.
            rs = (r.get("signed") or "").strip()
            if his_signed:
                if not rs or rs != his_signed:
                    return False
            # HE STATED NONE. There is nothing to compare a sign
            # against, and demanding equality with an empty string
            # refused every row — a guard blocking everything, which is
            # an outage wearing a guard's uniform. The MAGNITUDE must
            # still agree, and the caller's uniqueness rule
            # (len(...) == 1 on every branch) still refuses ambiguity,
            # so a team listed at two handicaps returns nothing rather
            # than a coin flip.
            rl = (r.get("line") or "").strip()
            if not rl:
                return not his_lines
            return rl in his_lines

        exact = [r for r in rows if r.get("side_norm") == on
                 and _lined_ok(r)]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
        # APOSTROPHES SPLIT ONE NAME INTO TWO SPELLINGS (2026-08-25).
        #
        # _norm turns punctuation into spaces, so the venue's
        # "Christopher O'Connell" becomes "christopher o connell" while
        # the whale feed's "Christopher OConnell" becomes "christopher
        # oconnell". Exact equality fails on a difference that is not a
        # difference. Measured on the live census: no_side_match is 47
        # of 400 sampled unmapped rows (11.8%), and the very first
        # example it printed was that exact pair.
        #
        # Collapsing whitespace makes both "christopheroconnell". This
        # runs ONLY after exact equality has found nothing, and it still
        # demands a UNIQUE hit — ambiguity refuses, as everywhere else
        # in this function. Two different players on one market would
        # have to collapse to the same string to cause a wrong match,
        # and if they did, the len() != 1 guard refuses rather than
        # picking.
        tight = on.replace(" ", "")
        if tight:
            collapsed = [r for r in rows
                         if (r.get("side_norm") or "").replace(" ", "")
                         == tight and _lined_ok(r)]
            if len(collapsed) == 1:
                return collapsed[0]
            if len(collapsed) > 1:
                return None
        # a lined pick normalizes with its number attached
        # ("kansas city chiefs  3 5"), so also try the name alone
        base = re.sub(r"\s*\d+\s+5$", "", on).strip()
        if his_lines and base and base != on:
            lined = [r for r in rows
                     if _norm(r.get("side_norm") or "")
                     .startswith(base) and _lined_ok(r)]
            if len(lined) == 1:
                return lined[0]
            if len(lined) > 1:
                return None
        out_last = (on.split() or [""])[-1]
        if len(out_last) > 3:
            tok = [r for r in rows
                   if out_last in (r.get("side_norm") or "").split()
                   and _lined_ok(r)]
            if len(tok) == 1:
                return tok[0]
        return None
    if len(cands) == 1:
        return cands[0]
    return None


async def _ensure_table(pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS us_premap (
            identifier text PRIMARY KEY,
            event_slug text,
            event_title text,
            market_slug text,
            question text,
            kind text,
            line text,
            side_norm text,
            event_keys text[],
            intent text,
            signed text,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """)
    # Sides that share an identifier (the aec- family) are distinct
    # ONLY by side_norm — keying on identifier alone silently
    # overwrote one side of every such market (2026-08-24).
    await pool.execute(
        "ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS intent text")
    # SIGNED WAS PRODUCED AND NEVER STORED (2026-08-25).
    #
    # _market_rows stamps `signed` on every row, and match_side's
    # _lined_ok requires the venue side's sign to equal the whale's:
    #
    #     rs = (r.get("signed") or "").strip()
    #     if his_signed or rs:
    #         if not rs or rs != his_signed:
    #             return False
    #
    # There was no `signed` column, so every row loaded from this table
    # had rs = "" — and any whale pick stating a sign hit `not rs` and
    # refused. Every signed spread was structurally unresolvable, and
    # premap is the ONLY lane allowed to trade under the quarantine, so
    # the class was dead end to end.
    #
    # This does not loosen the guard, it makes it FUNCTIONAL: it goes
    # from refusing every signed pick to refusing MISMATCHED ones,
    # which is the inversion protection it was written for. A guard
    # that blocks everything is an outage wearing a guard's uniform.
    await pool.execute(
        "ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS signed text")
    await pool.execute(
        "ALTER TABLE us_premap DROP CONSTRAINT IF EXISTS us_premap_pkey")
    await pool.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS us_premap_ident_side "
        "ON us_premap (identifier, side_norm)")
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS us_premap_keys ON us_premap "
        "USING gin (event_keys)")


def side_intent(side: dict, sides: list[dict]) -> str | None:
    """Which ORDER INTENT buys THIS side, or None when the venue does
    not say unambiguously.

    THE INCIDENT'S ROOT CAUSE (venue ground truth, 2026-08-24): on the
    aec- family BOTH sides carry the SAME identifier — equal to the
    market slug — and CreateOrderParams has no side field. The slug
    therefore does not name a side; only `intent` does (BUY_LONG vs
    BUY_SHORT). Every copy this engine ever placed sent BUY_LONG, so on
    that whole family the side was decided by the venue's ordering
    rather than by the whale's pick.

    None means REFUSE: an ambiguous side must never be ordered, because
    the order carries no other field that could correct it."""
    lg = side.get("long")
    if isinstance(lg, bool):
        return "ORDER_INTENT_BUY_LONG" if lg else "ORDER_INTENT_BUY_SHORT"
    t = str(side.get("marketSideType") or "").upper()
    if t.endswith("LONG"):
        return "ORDER_INTENT_BUY_LONG"
    if t.endswith("SHORT"):
        return "ORDER_INTENT_BUY_SHORT"
    # No explicit long/short marker. Safe ONLY when this side's
    # identifier is unique among the market's sides — then the slug
    # itself names the side and the default BUY_LONG is correct.
    idents = [str(s.get("identifier") or "").lower() for s in sides]
    mine = str(side.get("identifier") or "").lower()
    if mine and idents.count(mine) == 1:
        return "ORDER_INTENT_BUY_LONG"
    return None


def _market_rows(ev: dict, m: dict) -> list[dict]:
    """Rows for one venue market: each side its own orderable row.

    A side row carries the INTENT that buys it. Sides that share an
    identifier (the aec- family) are distinguished ONLY by intent, so
    the row key must be (identifier, side_norm) — keying on identifier
    alone silently overwrote one side of every such market with the
    other, which is why the table looked healthy while a whole family
    was unorderable."""
    q = m.get("question") or m.get("title") or ""
    # WHOLE NUMBERS COUNT (leak-hunt round 3): only \d+\.5 was stamped,
    # so a whole-number line ('-3', 'O/U 47') left the row unlined and
    # the line comparison was skipped on both sides.
    _ql = _lines_of(q)
    line = next(iter(_ql)) if len(_ql) == 1 else ""
    ev_slug = ev.get("slug") or ev.get("eventSlug") or ""
    ev_title = ev.get("title") or ""
    out: list[dict] = []
    all_sides = [s for s in (m.get("marketSides") or [])
                 if isinstance(s, dict)]
    sides = [s for s in all_sides
             if s.get("identifier") and s.get("description")]
    if sides:
        for s in sides:
            out.append({
                "identifier": str(s["identifier"]).lower(),
                "event_slug": ev_slug, "event_title": ev_title,
                "market_slug": (m.get("slug") or "").lower(),
                "question": q[:300], "kind": "side",
                "line": (next(iter(_lines_of(s["description"])))
                         if len(_lines_of(s["description"])) == 1
                         else line),
                "side_norm": _norm(s["description"]),
                "signed": signed_line(s["description"]) or signed_line(q),
                # the UNFILTERED list: judging uniqueness against a
                # filtered one made a dropped sibling look unique, so a
                # shared-identifier SHORT side would default to BUY_LONG
                # — the exact inversion this whole build exists to stop
                "intent": side_intent(s, all_sides),
            })
        return out
    # per-side contract: the market IS one side; its subject names it
    subject = m.get("outcome") or ""
    if not subject:
        mq = re.search(r"^will (?:the )?(.+?) (?:cover|win)", _norm(q))
        if mq:
            subject = mq.group(1)
    if not subject:
        title = m.get("title") or ""
        tl = f" {title.lower()} "
        if title and " vs" not in tl and " - " not in tl and " @ " not in tl:
            subject = title
    if all_sides:
        # a market that HAS sides never falls through to the contract
        # branch: that branch writes the PARENT slug with a hardcoded
        # BUY_LONG, which is a sideless order by another name
        return out
    if m.get("slug") and subject:
        out.append({
            "identifier": (m.get("slug") or "").lower(),
            "event_slug": ev_slug, "event_title": ev_title,
            "market_slug": (m.get("slug") or "").lower(),
            "question": q[:300], "kind": "contract",
            "line": line,
            "side_norm": _norm(subject),
            "intent": "ORDER_INTENT_BUY_LONG",
        })
    return out


async def _upsert(pool, r: dict, keys: list[str]) -> None:
    await pool.execute(
        """
        INSERT INTO us_premap (identifier, event_slug,
            event_title, market_slug, question, kind, line,
            side_norm, event_keys, intent, signed, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
        ON CONFLICT (identifier, side_norm) DO UPDATE SET
            event_slug=$2, event_title=$3, market_slug=$4,
            question=$5, kind=$6, line=$7,
            event_keys=$9, intent=$10, signed=$11, updated_at=now()
        """,
        r["identifier"], r["event_slug"], r["event_title"],
        r["market_slug"], r["question"], r["kind"],
        r["line"], r["side_norm"], keys, r.get("intent"),
        r.get("signed"))


async def _record_last(pool, summary: dict,
                       key: str = "premap_last") -> None:
    """Record a sweep's progress under its OWN state key.

    The fast lane writes `premap_last_fast`, never `premap_last`. A
    narrow 14-hour sweep and a 108-hour one produce wildly different
    row counts, and every probe, dashboard and alert that reads
    `premap_last` was written against the full sweep's numbers. Sharing
    the key would have the fast lane's small count read as a collapsed
    full sweep every three minutes — an instrument reporting on a
    population it never measured, which is the exact failure mode that
    has cost this codebase the most today.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    summary = {**summary,
               "at": _dt.now(_tz.utc).isoformat(timespec="seconds")}
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            key, _json.dumps(summary))
    except Exception:  # noqa: BLE001 — diagnostics never kill the sweep
        log.exception("premap_last write failed")


async def refresh(*, back_h: float = 12.0, fwd_h: float = 96.0,
                  max_pages: int = MAX_EVENT_PAGES,
                  prune: bool = True,
                  windowed_only: bool = False,
                  state_key: str = "premap_last") -> dict:
    """Sweep the venue universe into us_premap. Primary path walks
    events.list; if the installed SDK lacks it (2026-08-24: rows=0 with
    no visible error — the worker's SDK predates .events), fall back to
    paginating markets.list directly and keying each market from its own
    title/question. Every completion or failure writes premap_last so
    a silent sweep is impossible."""
    pool = await get_pool()
    await _ensure_table(pool)
    client = pmus._get_client()
    seen_rows = 0
    events = 0
    err = None
    mode = "events"
    # A sweep that never records is indistinguishable from one that
    # never STARTED (2026-08-24: rows=0 last=none read three probes in a
    # row) — record the start, then progress every page, so a hang shows
    # exactly where it hangs.
    await _record_last(pool, {"mode": "starting", "events": 0, "rows": 0},
                       state_key)
    try:
        # PREMAP-GT ground truth (probe #1030, 2026-08-24): the venue
        # IGNORES the eventSlug filter on markets.list (every queried
        # event got back the same generic page), and a bare
        # {"active": True} events.list leads with a stale historical
        # catalog (2025 games on page 1). The desk solved both on
        # 2026-08-21: probe the PARAM-VARIANT LADDER most-specific
        # first (a start-time window), and read each event's markets
        # INLINE off the event row — no per-event calls at all.
        from datetime import datetime as _dt, timedelta as _td
        from datetime import timezone as _tz

        def _iso(d):
            return d.strftime("%Y-%m-%dT%H:%M:%SZ")

        _now = _dt.now(_tz.utc)
        _window = {"active": True, "closed": False,
                   "startTimeMin": _iso(_now - _td(hours=back_h)),
                   "startTimeMax": _iso(_now + _td(hours=fwd_h))}
        # THE FAST LANE TAKES THE WINDOWED RUNG OR NOTHING.
        #
        # Rungs 2 and 3 carry no start-time bound at all, and PREMAP-GT
        # established that a bare {"active": True} board leads with a
        # stale historical catalog (2025 games on page 1). The full
        # sweep can absorb that: it has 40 pages of budget and prunes
        # what it replaces. The fast lane has 8 pages and runs every
        # three minutes — falling through would spend its whole budget
        # on last year's games, every three minutes, writing
        # title-keyed rows over the good ones.
        variants = (_window,) if windowed_only else (
            _window,
            {"active": True, "closed": False},
            {"active": True},
        )
        variant, first = None, None
        for v in variants:
            try:
                probe = await asyncio.wait_for(asyncio.to_thread(
                    client.events.list, {"limit": PAGE_LIMIT, **v}),
                    timeout=LIST_CALL_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — next variant
                continue
            evs = _items(probe, "events")
            # the winning variant is the one whose events carry live
            # inline markets — a page of bare historical rows is the
            # junk catalog, not a win
            if any(m for e in evs for m in (e.get("markets") or [])
                   if isinstance(m, dict) and not m.get("closed")):
                variant, first = v, evs
                break
        if variant is None:
            raise RuntimeError(
                "no events.list variant returned live inline markets")
        offset = 0
        pages_walked = 0
        last_page_full = False
        for _page in range(max_pages):
            pages_walked = _page + 1
            if _page == 0:
                got = first
            else:
                resp = await asyncio.wait_for(asyncio.to_thread(
                    client.events.list,
                    {"limit": PAGE_LIMIT, "offset": offset, **variant}),
                    timeout=LIST_CALL_TIMEOUT_S)
                got = _items(resp, "events")
            if not got:
                break
            offset += len(got)
            for ev in got:
                ev_slug = ev.get("slug") or ev.get("eventSlug")
                if not ev_slug:
                    continue
                markets = [m for m in (ev.get("markets") or [])
                           if isinstance(m, dict) and not m.get("closed")]
                if not markets:
                    continue
                events += 1
                keys = event_keys_for(ev.get("title"), ev_slug)
                if not keys:
                    continue
                for m in markets:
                    for r in _market_rows(ev, m):
                        await _upsert(pool, r, keys)
                        seen_rows += 1
            await _record_last(pool, {"mode": "events/page%d" % _page,
                                      "events": events, "rows": seen_rows},
                              state_key)
            await asyncio.sleep(LIST_PACING_S)
            last_page_full = len(got) >= PAGE_LIMIT
            if not last_page_full:
                break
    except Exception as exc:  # noqa: BLE001 — maybe try the fallback
        err = f"{type(exc).__name__}: {str(exc)[:160]}"
        # FALLBACK ONLY ON A DEAD PATH (leak-hunt round 2, 2026-08-24):
        # the except wraps the whole page loop, so a page-7 timeout or
        # 429 used to route a HEALTHY sweep into the markets fallback —
        # whose rows are keyed off each market's own question (junk
        # surnames, no dated slug key) and overwrite good rows
        # table-wide via the identifier upsert. Partial success keeps
        # what it wrote and waits for the next cycle instead.
        if seen_rows > 0:
            log.warning("premap events path failed mid-sweep after %d "
                        "rows (%s); keeping them, no fallback",
                        seen_rows, err)
            mode, events_err = "events/partial", err
        elif windowed_only:
            # The fast lane never runs the markets fallback either. Its
            # rows are keyed off each market's own question — a
            # degraded key set that the upsert would spread table-wide
            # — and the full sweep is already the recovery path for a
            # dead events board. A narrow lane failing is a reason to
            # wait 180 seconds, not to write worse rows.
            log.warning("premap fast lane: events path failed (%s); "
                        "no fallback, full sweep owns recovery", err)
            mode, events_err = "fast/failed", err
        else:
            log.warning("premap events path failed (%s); markets fallback",
                        err)
            mode = "markets"
        try:
            if mode != "markets":
                raise _SkipFallback()
            offset = 0
            for _page in range(max_pages):
                mresp = await asyncio.wait_for(asyncio.to_thread(
                    client.markets.list,
                    {"limit": PAGE_LIMIT, "offset": offset, "active": True}),
                    timeout=LIST_CALL_TIMEOUT_S)
                raw = _items(mresp, "markets")
                got = [m for m in raw if not m.get("closed")]
                if not raw:
                    break
                offset += len(raw)
                await asyncio.sleep(LIST_PACING_S)
                for m in got:
                    ev_slug = (m.get("eventSlug") or m.get("event_slug")
                               or "")
                    ev = {"slug": ev_slug,
                          "title": m.get("title") or m.get("question")}
                    keys = event_keys_for(
                        m.get("question") or m.get("title"), ev_slug)
                    if not keys:
                        continue
                    events += 1
                    for r in _market_rows(ev, m):
                        await _upsert(pool, r, keys)
                        seen_rows += 1
                await _record_last(pool, {"mode": "markets/page%d" % _page,
                                          "events": events,
                                          "rows": seen_rows}, state_key)
                if len(raw) < PAGE_LIMIT:
                    break
            # The events-path failure stays on the record even when the
            # fallback succeeds (leak-hunt 2026-08-24): markets-mode
            # keys come from market titles, a DEGRADED key set vs the
            # event boards — a silent mode downgrade must be visible.
            events_err, err = err, None
        except _SkipFallback:
            pass          # partial sweep: keep the rows, err stays set
        except Exception as exc2:  # noqa: BLE001 — recorded, next cycle
            events_err = err
            err = f"{type(exc2).__name__}: {str(exc2)[:160]}"
    else:
        events_err = None
    # NEVER prune on an empty sweep (leak-hunt 2026-08-24): a sweep
    # that wrote zero rows proves nothing about staleness — repeated
    # empty sweeps would otherwise age the whole table out and take
    # the premap lane down with it.
    #
    # AND ONLY THE FULL SWEEP MAY PRUNE. Staleness is a table-wide
    # judgement, and the fast lane only ever looks at a 14-hour slice
    # of the calendar; it has no standing to decide what the other 94
    # hours mean. (In practice the full sweep keeps those rows'
    # updated_at fresh, so the DELETE would be a no-op — but a no-op
    # that runs on the wrong authority is one config change away from
    # deleting the table.)
    if seen_rows > 0 and prune:
        pruned = await pool.execute(
            "DELETE FROM us_premap WHERE updated_at < now() - "
            "interval '%s hours'" % int(PRUNE_HOURS))
    else:
        pruned = None
    # THE SWEEP COULD NOT SEE ITS OWN TRUNCATION.
    #
    # The page loop exits on a short page (board exhausted) or by
    # running out of budget (board TRUNCATED), and the summary could
    # not tell those apart — it published `events` and `rows`, which
    # read as a large healthy sweep either way. A truncated board is
    # markets that can NEVER be premapped, and premap is the only lane
    # allowed to trade under the quarantine, so it is a silent hard
    # ceiling on coverage that no instrument reported.
    _truncated = bool(locals().get("last_page_full")) and \
        locals().get("pages_walked") == max_pages
    summary = {"mode": mode, "events": events, "rows": seen_rows,
               "err": err, "events_err": events_err,
               "lane": "fast" if windowed_only else "full",
               "window_h": [back_h, fwd_h], "max_pages": max_pages,
               "pages_walked": locals().get("pages_walked", 0),
               "truncated": _truncated,
               "truncated_note": (
                   "the page budget ran out while the venue was still "
                   "returning full pages — part of the board was never "
                   "read, and those markets cannot be resolved at all"
                   if _truncated else None),
               "pruned": int(pruned.split()[-1]) if pruned else 0}
    await _record_last(pool, summary, state_key)
    log.info("premap refresh: %s", summary)
    return summary


def live_rows_for_market(parent_slug: str) -> list[dict]:
    """RAW venue rows for one market via DIRECT slug lookup — the
    side-echo re-derivation input, in the exact shape the sweep writes
    and match_side consumes.
    (Leak-hunt find 2026-08-24: the echo originally consumed
    pmus.event_board, whose DESK-shaped rows carry none of the keys
    _market_rows reads — the tripwire was structurally inert. And
    PREMAP-GT proved markets.list IGNORES the eventSlug filter, so a
    list-based refetch would compare against a GENERIC page — direct
    retrieve_by_slug is the venue call the exact resolver already uses
    in production.)
    Closed markets are INCLUDED: the just-filled market may close within
    seconds, and its absence must read as unverified, never as a
    different side. Exceptions propagate — the caller owns retries."""
    client = pmus._get_client()
    m = (client.markets.retrieve_by_slug(parent_slug) or {}).get(
        "market") or {}
    if not m.get("slug"):
        raise RuntimeError(f"market not found: {parent_slug}")
    return _market_rows({"slug": "", "title": ""}, m)


async def resolve_explain(pool, market_title: str | None,
                          event_title: str | None, outcome: str | None,
                          global_slug: str | None) -> dict:
    """WHY resolve() said no. Same steps, same order, no side effects.

    26,569 rejected rows sit in listed_mapper_fail — markets that ARE
    listed on our venue and that we still could not name. It is the only
    bucket big enough to move a 0.55% fill rate, and until now nothing
    could attribute a single one of them to a cause.

    The endpoint built to diagnose it (api_mapgap) filters
    `us_market_slug IS NOT NULL`, and that column is written only AFTER
    a mapping succeeds — so it measures the population that WORKED while
    reporting on the one that failed. Every published number about this
    bucket describes the wrong rows.

    resolve() has six distinct ways to return None and they need
    completely different fixes: no keys built, no key intersection, an
    unknown market type, a type-prefix filter that emptied the pool, no
    side match, and a side with no intent. "Coverage is fine" was an
    assumption resting on all six being invisible.

    Deliberately a SEPARATE function rather than instrumenting resolve.
    resolve is on the copy hot path; it must not grow a diagnostic
    branch, and a reader must be able to see at a glance that this one
    cannot affect an order.
    """
    from ..copy_sports import market_type_of

    out: dict = {"step": None, "detail": None, "keys": 0, "rows": 0}
    # A DATELESS SIGNAL CANNOT ESTABLISH GAME AGREEMENT (2026-08-26).
    #
    # When the whale's slug carries no date, event_keys_for is called
    # with slug=None and _dated_admissible is skipped, so the key set is
    # BARE TITLES with no date stamp. A bare "tigre vs cacique" matches
    # the venue's row for that pairing on ANY date — the 2026-08-24 game
    # and the 2026-09-14 game alike. That is the whale's pick reaching
    # another game's row, which is the incident this entire lane exists
    # to prevent.
    #
    # It was latent before and I ARMED IT. Until the event_title fix
    # earlier tonight, a dateless signal produced only {market_title}
    # ("tigre"), which intersected nothing because venue rows are keyed
    # off event titles. Supplying event_title turned "matches nothing"
    # into "may match the wrong date's game". Found by the coverage
    # fleet's own verifiers, rejecting the proposal I had already
    # shipped.
    #
    # Refusing costs nothing that was ever safe: with no date, no slug
    # key is built either, so bare titles were the ONLY keys such a
    # signal had. Game agreement is the basis of the deterministic lane,
    # and a signal that cannot say which game it is on has no business
    # selecting a market.
    d = date_of(global_slug)
    if not d:
        out["step"] = "no_date_on_his_signal"
        out["detail"] = ("his slug carries no date, so no key can "
                         "establish which game he bet — bare title keys "
                         "match that pairing on every date")
        return out
    keys: set[str] = set()
    for t in (market_title, event_title):
        keys.update(event_keys_for(t, global_slug if d else None))
    if global_slug:
        keys.update(event_keys_for(None, global_slug))
    keys = {k for k in keys if k}
    if d:
        keys = _dated_admissible(keys, d)
    out["keys"] = len(keys)
    if not keys:
        out["step"] = "no_keys_built"
        out["detail"] = "no event key could be derived from his titles/slug"
        return out
    try:
        rows = [dict(r) for r in await pool.fetch(
            "SELECT identifier, side_norm, kind, line, question, "
            "event_title, "
            "intent, signed, event_slug, market_slug FROM us_premap "
            "WHERE event_keys && $1::text[]", sorted(keys))]
    except Exception as exc:  # noqa: BLE001
        out["step"] = "premap_query_failed"
        out["detail"] = f"{type(exc).__name__}"
        return out
    out["rows"] = len(rows)
    if not rows:
        out["step"] = "no_key_intersection"
        out["detail"] = ("his keys match no us_premap row — either the "
                         "sweep never captured this market, or the two "
                         "key sets are built differently")
        # WHICH OF THE TWO, MEASURED (2026-08-25). "Either A or B" is
        # where this cause has sat since the census was built, and the
        # two need opposite fixes: A is a sweep-coverage problem, B is a
        # key-grammar problem. The probe printed a pair that names a
        # THIRD possibility neither of them covers:
        #
        #   whale  bol1-gvs-ori-2026-08-25-gvs
        #   venue  atc-lpb-gvs-ori-2026-08-25-gvs
        #
        # Same game, same date, same teams, same market. The whale feed
        # calls the league bol1 and the venue calls it lpb, so the keys
        # cannot intersect no matter how well the sweep ran. That is
        # league-code ALIASING, and after the kind-prefix bridge it is
        # the obvious next suspect for a cause still sitting at 37%.
        #
        # This only REPORTS. Dropping the league token from the key
        # would widen what a signal can match, and widening a key is
        # how a whale's pick reaches another game's row — the incident
        # this whole lane exists to prevent. So the size of the class
        # gets measured first, on real rejected rows, and the matcher
        # is not touched until the number says it is worth the risk and
        # the ambiguity it would introduce is understood.
        try:
            # AT LEAST TWO TOKENS MUST SURVIVE IN FRONT OF THE DATE.
            #
            # Counting hyphens is not enough and my first attempt got
            # this wrong: `gvs-2026-08-25` carries three hyphens, two of
            # them inside the date, so a naive rule strips it to
            # `2026-08-25` — A BARE DATE, which matches every game
            # played that day. That is not a widened key, it is no key
            # at all, and it would have made this diagnostic report a
            # huge recoverable class that does not exist.
            #
            # The real grammar is <league>-<home>-<away>-<date>, so
            # after dropping the league at least the two team tokens
            # must remain.
            stripped = set()
            for k in keys:
                if not d or not k.endswith(d):
                    continue
                toks = [t for t in k[:-len(d)].rstrip("-").split("-") if t]
                if len(toks) >= 3:
                    stripped.add("-".join(toks[1:]) + "-" + d)
            if stripped:
                alt = await pool.fetch(
                    "SELECT identifier FROM us_premap "
                    "WHERE event_keys && $1::text[] LIMIT 25",
                    sorted(stripped))
                out["league_alias_probe"] = {
                    "stripped_keys": sorted(stripped)[:4],
                    "rows_it_would_find": len(alt),
                    "would_have_hit": bool(alt),
                    "sample": [str(r["identifier"]) for r in alt[:3]],
                }
        except Exception as exc:  # noqa: BLE001 — diagnostics never
            out["league_alias_probe"] = {"error": type(exc).__name__}
        return out
    want = PREFIX_FOR_TYPE.get(market_type_of(global_slug or ""))
    if not want:
        out["step"] = "unknown_market_type"
        out["detail"] = f"market_type_of({global_slug!r}) is unrecognised"
        return out
    kept = [r for r in rows if _prefix_of(r.get("identifier")) in want]
    if not kept:
        out["step"] = "type_prefix_filter_emptied"
        out["detail"] = (f"{len(rows)} rows on this event, none with a "
                         f"{sorted(want)} prefix")
        return out
    hit = match_side(kept, outcome, market_title, global_slug)
    if hit is None:
        out["step"] = "no_side_match"
        out["detail"] = (f"outcome {outcome!r} matched none of "
                         f"{[r.get('side_norm') for r in kept][:6]}")
        # PHASE-0 BRIDGE PROBE (2026-08-26): read-only. Mirrors exactly
        # the composition resolve would run (match_side first, bridge
        # only on refusal) so the census can MEASURE what the bridge
        # would recover from the 59.8% class, and which venue question
        # tails exist, before any executor code consumes it. The step
        # string above is unchanged so census bucketing stays stable.
        try:
            btrace: dict = {}
            bhit, breason = bridge_explain(kept, rows, outcome,
                                           market_title, global_slug,
                                           event_title,
                                           trace=btrace)
            out["bridge"] = {
                "would_resolve": bool(bhit),
                "identifier": (bhit or {}).get("identifier"),
                "matched_question": (bhit or {}).get("question"),
                "reason": breason,
            }
            # ROUND-3 PROBE (2026-08-27). Round 2 measured
            # would_resolve=0 and captured the venue's real wordings;
            # the tournament that consumed them demanded EVIDENCE
            # channels, not just a verdict bit:
            #   * a would_resolve AUDIT RECORD — Phase 1 is gated on a
            #     zero-mismatch hand audit of every one of these rows,
            #     resolution-rules reading included;
            #   * his market_type/title/outcome on every refusal — the
            #     202-strong named class ships NO code this round, so
            #     its composition must be measured before its round-3
            #     tournament (E1/E3 evidence bar);
            #   * strict_trace per kept row — the league values seen,
            #     the month tokens seen, the set differences — so
            #     whitelist and furniture decisions cite counts;
            #   * the corroboration shadow-eval and blocker telemetry
            #     recorded by the trace above.
            if bhit:
                tq = " ".join(_norm(bhit.get("question")).split())
                tm = _BRIDGE_Q_STRICT_RE.fullmatch(tq)
                _wparts = [x for x in (global_slug or "").lower()
                           .split("-") if x]
                out["bridge"]["audit"] = {
                    # market_slug: the venue's fullest club-naming
                    # witness (round-3 fleet found it stored on every
                    # row and read by NOTHING). Captured here so its
                    # production shape is OBSERVED before any round-2.4
                    # gate consumes it — grammars built on imagined
                    # shapes recover zero; that lesson is paid for.
                    "market_slug": str(bhit.get("market_slug"))[:120],
                    "his_slug": global_slug,
                    "his_lg": (_wparts[0] if _wparts else ""),
                    "his_event_title": str(event_title)[:120],
                    "his_title": str(market_title)[:120],
                    "event_slug": str(bhit.get("event_slug"))[:60],
                    "event_title": str(bhit.get("event_title"))[:120],
                    "matched_question":
                        str(bhit.get("question"))[:300],
                    "lg": (" ".join(tm.group("lg").split())
                           if tm else None),
                    "tail_date": (f"{tm.group('qyr')}-"
                                  f"{tm.group('qmon')}-"
                                  f"{tm.group('qday')}" if tm else None),
                }
            from ..copy_sports import market_type_of as _mt
            out["bridge"]["his"] = {
                "market_type": _mt(global_slug or ""),
                "title": str(market_title)[:120],
                "event_title": str(event_title)[:120],
                "outcome": str(outcome)[:40],
            }
            # THE VENUE'S OWN WORDINGS, CAPTURED ON REFUSAL —
            # STRATIFIED (round 3). Round 2's 3-pair cap let the MLB
            # pool's inning-winner rows crowd out the plain moneyline
            # wording, which is exactly the string the named-variant
            # tournament is waiting to observe. Win-shaped yes/no rows
            # are captured FIRST; question[:300] plus the raw length
            # separates venue-side truncation from probe-side.
            def _q_entry(r):
                q = str(r.get("question"))
                return {"side": str(r.get("side_norm"))[:30],
                        "q": q[:300], "qlen": len(q),
                        "market_slug":
                            str(r.get("market_slug"))[:100],
                        "event_title": str(r.get("event_title"))[:120],
                        "identifier": str(r.get("identifier"))[:80],
                        "line": str(r.get("line") or ""),
                        "signed": str(r.get("signed") or ""),
                        "event_slug": str(r.get("event_slug"))[:60]}

            def _is_win_yesno(r):
                return (" win " in f" {_norm(r.get('question'))} "
                        and _norm(r.get("side_norm")) in ("yes", "no"))

            seenq: list[dict] = []
            seen_keys: set = set()
            for r in (sorted(kept, key=lambda x: not _is_win_yesno(x))):
                e = _q_entry(r)
                k = (e["side"], e["q"])
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                seenq.append(e)
                if len(seenq) >= 12:
                    break
            out["bridge"]["venue_q_sample"] = seenq
            out["bridge"]["outcome_shape"] = (
                "yes_no" if _norm(outcome) in ("yes", "no") else "named")
            for k in ("row_gates", "gate10", "blockers"):
                if k in btrace:
                    out["bridge"][k] = btrace[k]
        except Exception as exc:  # noqa: BLE001 — a probe never breaks
            out["bridge"] = {"error": type(exc).__name__}
        # Split the two failure modes this counter conflates: wording
        # disagreement vs the date-as-line misparse at _yn_line_ok.
        if _norm(outcome) in ("yes", "no"):
            try:
                wq = _norm(market_title)
                out["yn_detail"] = {
                    "question_agreed": sum(
                        1 for r in kept
                        if _questions_agree(wq, _norm(r.get("question")))),
                    "his_lines_parsed": sorted(
                        _lines_of(market_title) | _lines_of(outcome)
                        | slug_lines(global_slug)),
                    "his_signed_parsed": (signed_line(outcome)
                                          or signed_line(market_title)
                                          or ""),
                }
            except Exception as exc:  # noqa: BLE001
                out["yn_detail"] = {"error": type(exc).__name__}
        return out
    if not hit.get("intent"):
        out["step"] = "side_has_no_intent"
        out["detail"] = (f"{hit.get('identifier')} matched but the sweep "
                         f"could not name its long/short side")
        return out
    out["step"] = "resolves"
    out["detail"] = hit.get("identifier")
    return out


async def resolve(pool, market_title: str | None, event_title: str | None,
                  outcome: str | None,
                  global_slug: str | None) -> dict | None:
    """Copy-time resolution from the table: exact keys, unique side, no
    network. None means 'not pre-mapped' — the caller falls through to
    the legacy resolvers (which the quarantine still gates)."""
    # GAME AGREEMENT (leak-hunt round 2): when the whale's signal names
    # a date, ONLY date-stamped keys may match, so another day's game
    # can never be a candidate. Dateless signals keep the bare keys.
    # A DATELESS SIGNAL CANNOT ESTABLISH GAME AGREEMENT (2026-08-26).
    #
    # When the whale's slug carries no date, event_keys_for is called
    # with slug=None and _dated_admissible is skipped, so the key set is
    # BARE TITLES with no date stamp. A bare "tigre vs cacique" matches
    # the venue's row for that pairing on ANY date — the 2026-08-24 game
    # and the 2026-09-14 game alike. That is the whale's pick reaching
    # another game's row, which is the incident this entire lane exists
    # to prevent.
    #
    # It was latent before and I ARMED IT. Until the event_title fix
    # earlier tonight, a dateless signal produced only {market_title}
    # ("tigre"), which intersected nothing because venue rows are keyed
    # off event titles. Supplying event_title turned "matches nothing"
    # into "may match the wrong date's game". Found by the coverage
    # fleet's own verifiers, rejecting the proposal I had already
    # shipped.
    #
    # Refusing costs nothing that was ever safe: with no date, no slug
    # key is built either, so bare titles were the ONLY keys such a
    # signal had. Game agreement is the basis of the deterministic lane,
    # and a signal that cannot say which game it is on has no business
    # selecting a market.
    d = date_of(global_slug)
    if not d:
        return None
    keys: set[str] = set()
    for t in (market_title, event_title):
        keys.update(event_keys_for(t, global_slug if d else None))
    if global_slug:
        keys.update(event_keys_for(None, global_slug))
    keys = {k for k in keys if k}
    if d:
        keys = _dated_admissible(keys, d)
    if not keys:
        return None
    try:
        rows = [dict(r) for r in await pool.fetch(
            "SELECT identifier, side_norm, kind, line, question, "
            "event_title, "
            "intent, signed, event_slug, market_slug FROM us_premap "
            "WHERE event_keys && $1::text[]",
            sorted(keys))]
    except Exception:  # noqa: BLE001 — table absent/degraded: fall through
        return None
    if not rows:
        return None
    # MARKET-TYPE AGREEMENT (leak-hunt round 2): every market on an
    # event shares one key set, so without this the candidate pool is
    # the WHOLE board — a moneyline pick could land on a segment or a
    # prop whose side carries the same name. The venue names the type
    # in its slug prefix; an unrecognized type refuses (fail closed).
    from ..copy_sports import market_type_of

    want = PREFIX_FOR_TYPE.get(market_type_of(global_slug or ""))
    if not want:
        return None
    rows = [r for r in rows if _prefix_of(r.get("identifier")) in want]
    if not rows:
        return None
    hit = match_side(rows, outcome, market_title, global_slug)
    if hit is None:
        return None
    # AMBIGUOUS SIDE = REFUSE (venue ground truth 2026-08-24): on the
    # aec- family both sides share the market slug, so the order's only
    # side selector is `intent`. A row whose intent the sweep could not
    # determine is UNORDERABLE — returning it would hand side selection
    # back to the venue, which is the incident itself.
    intent = hit.get("intent")
    if not intent:
        log.warning("premap refuses %s: no side intent (sides share an "
                    "identifier and the venue named no long/short)",
                    hit.get("identifier"))
        return None
    return {"market_slug": hit["identifier"],
            "title": hit.get("question") or hit.get("event_title"),
            "outcome": hit.get("side_norm"),
            "intent": intent,
            "matched_by": "premap", "score": 1.0}


async def fast_refresh() -> dict:
    """The imminent window, swept often.

    Same function, same `_market_rows` / `_upsert` path, same venue
    side expansion — only the aim is different. This is deliberately
    NOT a second way of building a premap row: the wrong-side incident
    was caused by a resolution path that could invent a side, and the
    property that makes premap safe is that every row comes from the
    venue's own expansion of the market it belongs to. A parallel
    row-builder would put that back at risk for a coverage gain, which
    is a trade this desk does not make.
    """
    return await refresh(back_h=FAST_WINDOW_BACK_H,
                         fwd_h=FAST_WINDOW_FWD_H,
                         max_pages=FAST_MAX_PAGES,
                         prune=False,
                         windowed_only=True,
                         state_key="premap_last_fast")


# Held by the full sweep for its whole run. The fast lane checks it and
# SKIPS rather than waiting: while a full sweep is in flight the
# imminent window is already being covered by it, so a skipped fast
# cycle forfeits nothing — and two lanes walking the venue's event
# board at once is how the 429s came back on 2026-08-23.
_SWEEP_LOCK = asyncio.Lock()


async def _full_loop() -> None:
    while True:
        started = time.monotonic()
        try:
            async with _SWEEP_LOCK:
                await refresh()
        except Exception:  # noqa: BLE001 — supervised loop, next cycle
            log.exception("premap refresh failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(60.0, REFRESH_SECONDS - elapsed))


async def _fast_loop() -> None:
    while True:
        started = time.monotonic()
        try:
            if _SWEEP_LOCK.locked():
                log.info("premap fast lane: full sweep in flight, "
                         "skipping this cycle (it covers the window)")
            else:
                # AND IT MUST HOLD THE LOCK WHILE IT RUNS (2026-08-25,
                # adversarial review). Checking `locked()` and never
                # acquiring gave one-way exclusion: the fast lane
                # yielded to a full sweep already running, but a full
                # sweep STARTING mid-fast-sweep walked the venue's
                # event board concurrently — precisely the two-lanes-at-
                # once condition that brought the 429s back on
                # 2026-08-23, and the thing the lock was added for.
                #
                # There is no await between the check and the acquire,
                # and asyncio is single-threaded, so this cannot block:
                # nothing else can take the lock in between.
                async with _SWEEP_LOCK:
                    await fast_refresh()
        except Exception:  # noqa: BLE001 — supervised loop, next cycle
            log.exception("premap fast refresh failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(30.0, FAST_REFRESH_SECONDS - elapsed))


async def main() -> None:
    if FAST_REFRESH_SECONDS > 0:
        # gather, not create_task: a lane that dies must take main down
        # so the worker supervisor restarts BOTH. A detached task that
        # dies silently is a lane that stops sweeping while the
        # heartbeat keeps saying premap is up.
        await asyncio.gather(_full_loop(), _fast_loop())
    else:
        await _full_loop()
