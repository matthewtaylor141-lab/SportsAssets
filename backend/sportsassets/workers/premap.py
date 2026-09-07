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


def _canon_line(n: str) -> str:
    """One spelling per line value. The venue writes '1.50' where the
    whale's slug says 1pt5 -> '1.5'; every comparison in this module is
    string equality, so numerically identical lines failed line_ok and
    the row was refused as no_side_match (live census 2026-08-29: the
    printed candidates carried line '1.50' against his_lines ['1.5']).
    Trailing zeros after the point (and a then-bare point) are dropped;
    whole numbers pass through untouched."""
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n


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
    return {_canon_line(n) for n in out if n}


def _clock_artifact(rl: str, r: dict) -> bool:
    """True when a row's stamped line is nothing but the question's
    CLOCK minutes — '7:00 PM' matched the ':' line context and
    stamped line='00' on BOTH sides of a moneyline market, and the
    unlined pick then failed the line-presence comparison against a
    phantom (live census 2026-08-29, the WNBA example: 'Washington
    Mystics' refused against sides mystics/sparks). Authentication
    mirrors the named-tennis bridge's clock quarantine: the line must
    be the question's SOLE parsed line AND verbatim-equal to a strict
    clock parse's minutes. A global time-strip is NOT an option — the
    bridge lane depends on the clock stamping (its quarantine
    authenticates the same artifact instead of erasing it).

    Fleet round 37 (major): the question-only check had a
    coincidence hole — a REAL line stamped from the SIDE DESCRIPTION
    ('Alabama -30') collided with a ':30' kickoff clock in the
    question, authenticated as an artifact, and the erased real line
    both refused the correct lined pick AND let an unlined moneyline
    pick wrongly match the spread side (a money-direction wrong-
    market copy). A line the SIDE ITSELF states is real whatever the
    clock says: the row's signed magnitude and its side_norm tokens
    both veto the artifact."""
    q = r.get("question")
    if not rl or not q:
        return False
    m = re.search(r"\b\d{1,2}:(\d{2})\b", q)
    if not (m and m.group(1) == rl and _lines_of(q) == {rl}):
        return False
    mag = (r.get("signed") or "").lstrip("+-")
    if mag and _canon_line(mag) == rl:
        return False               # the side states this line, signed
    sn = r.get("side_norm") or ""
    if rl in _lines_of(sn) or re.search(
            r"(?:^|\s)" + re.escape(rl) + r"(?:\s|$)", sn):
        return False               # the side states this line, plain
    return True


def signed_line(text: str | None) -> str:
    """The SIGNED handicap a description carries ('-3.5', '+3.5'), or ''.

    _norm strips punctuation, so 'Chiefs -3.5' and 'Chiefs +3.5' both
    normalize to 'chiefs  3 5' — a whale taking +3.5 (getting points)
    matched the venue's -3.5 side (giving points), the exact opposite
    bet, on every spread. The sign must be read BEFORE normalization
    and compared on its own (leak-hunt round 3, 2026-08-24). The
    magnitude is canonicalized like every line ('-1.50' -> '-1.5') so
    a trailing zero can never fail the sign-agreement equality."""
    m = re.search(r"([+-])\s*(\d+(?:\.\d+)?)", str(text or ""))
    if not m:
        return ""
    return f"{m.group(1)}{_canon_line(m.group(2))}"


def date_of(slug: str | None) -> str:
    """The YYYY-MM-DD a slug names, or '' — the game identity that a
    title alone cannot carry."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", (slug or "").lower())
    return m.group(0) if m else ""


# The venue's slug grammar leads with a market-kind token; the whale's
# feed does not. Kept next to event_keys_for because that is the only
# place the asymmetry has to be reconciled.
_KIND_PREFIXES = frozenset({"aec", "atc", "asc", "tsc", "astatc"})
# the feed's attested draw wording (C3): the matchup inside it is the
# event, the rest is the question
_DRAW_TITLE_MATCHUP_RE = re.compile(
    r"^\s*will\s+(.+?\s+vs\.?\s+.+?)\s+end\s+in\s+a\s+draw\s*\??\s*$", re.I)
# E1 (2026-09-07, gap_esports_chi §1.3/§3.3): the feed's attested esports
# event title, 'Counter-Strike: <A> vs <B> (BO<n>) - <tournament>' on
# every cs2 row of esports_chi_rows_1352.txt, keyed 'fokus vs nemiga
# stake ranked episode 4@…' where the venue's 'FOKUS vs. Nemiga' keys
# 'fokus vs nemiga@…' -- the two could never meet (no_key_intersection
# on every series whose team codes his feed digit-suffixes). Exactly
# this template, read on the raw title (the punctuation it names is
# what _key_norm erases), down to its matchup; a lookup, not a
# decision -- what the rows then answer to is match_side, unchanged.
_ESPORTS_TITLE_MATCHUP_RE = re.compile(
    r"^\s*counter-strike:\s+(?P<a>.+?)\s+vs\s+(?P<b>.+?)\s+\(bo\d\)(?:\s+-\s+.*)?\s*$",
    re.I | re.S)


def _esports_matchup(title: str | None) -> str | None:
    """'<A> vs <B>' of the feed's esports event title, or None when the
    title is not that wording (it then keys as it always did)."""
    m = _ESPORTS_TITLE_MATCHUP_RE.match(title or "")
    return f"{m.group('a')} vs {m.group('b')}" if m else None


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
    # C2 (2026-09-06): the title is read through the explicit Latin
    # fold on BOTH sides (sweep time and copy time), so his
    # 'Kristiansund BK vs. Tromsø IL' keys 'tromso il', the venue's
    # ASCII, instead of 'troms il' -- the key sets could never meet on
    # an ø/ł/đ club. Keys are a lookup, not a decision: what the rows
    # then answer to is unchanged.
    title = pmus.fold_latin(title) if title else title
    # C3 (2026-09-06): the feed's draw question is 'Will A vs. B end in
    # a draw?' and _clean_title keeps it whole, so its side split read
    # 'will a' vs 'b end in a draw' and its surname key 'draw vs …' --
    # keys no venue row carries (fl1-olm-pfc-…-draw: no_key_intersection
    # while the venue listed lg1-olm-pfc). The attested wording is read
    # down to its matchup before keying; a lookup, not a decision, and
    # only that one wording -- never a wider key.
    dm = _DRAW_TITLE_MATCHUP_RE.match(title or "")
    if dm:
        title = dm.group(1)
    # E1 (2026-09-07): the esports event title, the same way -- its own
    # template (_esports_matchup above), its matchup, nothing wider
    em = _esports_matchup(title)
    if em:
        title = em
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
        kindless = slug_key
        if len(head) == 2 and head[0] in _KIND_PREFIXES:
            kindless = head[1]
            keys.add(kindless)
        # THE LEAGUE-STRIPPED PAIR KEY (R1, 2026-09-07; gap_league_codes
        # §c R1, gap_esports_chi §0.2). His feed and the venue code the
        # same game under DIFFERENT league codes -- itsb/srb, sui/swsl,
        # nor/els, den/sld, chi1/pdc, col1/lco -- with the team codes
        # and the date byte-identical, so the slug keys never met:
        #
        #   whale  nor-kbk-tro-2026-09-06-kbk   -> key nor-kbk-tro-2026-09-06
        #   venue  atc-els-kbk-tro-2026-09-06-kbk -> key els-kbk-tro-2026-09-06
        #
        # and the only bridge was the matchup key from markets.event_title,
        # absent on a moneyline judged before the title is stored and
        # unequal whenever the venue spells a club otherwise ($58k of
        # chi1/col1, $44k of listed itsb/sui/nor/den in 24 h). Both sides
        # now also emit <a>-<b>-<date>: it ENDS in the date, so
        # _dated_admissible admits it like the slug key, and it is a
        # LOOKUP, never a decision -- the rows it fetches answer to the
        # same arms as before (identity, the alias arm's single-code and
        # opponent witness, the draw witness, C3's _c3_code, C5). Exactly
        # three tokens must stand in front of the date (the grammar
        # <lg>-<a>-<b>-<date>): a two-token slug has no league to strip
        # and a bare date matches every game that day, so neither ever
        # produces a key (the league_alias_probe's own lesson).
        pk = _pair_key(kindless, d)
        if pk:
            keys.add(pk)
    return sorted(k for k in keys if k)


def _pair_key(kindless_key: str, d: str) -> str | None:
    """`<a>-<b>-<date>` of a kindless `<lg>-<a>-<b>-<date>` key, or
    None for any other token count -- never anything shorter."""
    if not d or not kindless_key.endswith(d):
        return None
    toks = [t for t in kindless_key[:-len(d)].rstrip("-").split("-") if t]
    if len(toks) != 3:
        return None
    return f"{toks[1]}-{toks[2]}-{d}"


# THE PER-CLUB DATED NAME KEY (C6-N, 2026-09-07; gap_league_codes §c R2,
# gap_esports_chi §3.2). When BOTH club codes differ on top of the league
# code (his cdh-csc vs the venue's hua-col, cle-gdr vs leq-agu) the pair
# key does not fetch the rows either, and the only witness the venue
# offers is its own per-team question naming his club on his date. So the
# venue side keys each FULL-GAME per-team row by the P4 subject its
# question names (and the draw row by the two clubs its draw template
# names), and his side keys his title's anchor, his event title's two
# sides and his draw title's two sides -- `<club>@<date>`, folded and
# normalized exactly as the arms read names. A lookup only: what the
# fetched rows then answer to is _yn_names_pick's witness, never the key.
def venue_name_keys(question: str | None, identifier: str | None) -> list[str]:
    """The name keys one venue row carries: `<P4 subject>@<date>` for a
    full-game per-team atc row, `<a>@<date>` and `<b>@<date>` for its
    draw row; [] for every other row or wording. The question's own
    date must be the identifier's (a row whose two dates disagree is
    keyed by nothing here)."""
    ident = str(identifier or "").lower()
    m = _C3_IDENT_RE.match(ident) if ident.startswith("atc-") else None
    if m is None or not question or _folds_away(question):
        return []
    d = m.group("date")
    rest = m.group("rest") or ""
    n = " ".join(_norm(pmus.fold_latin(str(question))).split())
    names: list[str] = []
    if rest in (m.group("a"), m.group("b")) and m.group("a") != m.group("b"):
        for pat in pmus._YN_Q_PATTERNS:
            gm = pat.fullmatch(n)
            if gm and pmus._yn_date_ok(gm.groupdict(), d):
                names.append(" ".join(gm.group("subj").split()))
                break
    elif rest == "draw":
        gm = pmus._YN_DRAW_Q_RE.fullmatch(n)
        if gm and pmus._yn_date_ok(gm.groupdict(), d):
            names += [" ".join(gm.group("a").split()), " ".join(gm.group("b").split())]
    return sorted({f"{x}@{d}" for x in names if x})


def keys_for_row(event_keys: list[str], r: dict) -> list[str]:
    """The keys the sweep writes on ONE row: the event's keys plus the
    row's own name keys (C6-N)."""
    extra = venue_name_keys(r.get("question"), r.get("identifier"))
    if not extra:
        return list(event_keys)
    return sorted(set(event_keys) | set(extra))


def his_name_keys(market_title: str | None, event_title: str | None,
                  slug: str | None) -> list[str]:
    """His side of the name key: his win-question's anchor, his event
    title's two sides, his draw title's two sides -- each `<club>@<date>`
    on his slug's date; [] when the slug carries no date."""
    d = date_of(slug)
    if not d:
        return []
    names: set[str] = set()
    anchor, _why = _bridge_title_subject(market_title, slug)
    if anchor:
        names.add(anchor)
    for reader in (lambda: _yn_event_sides(event_title),
                   lambda: _yn_draw_title_sides(market_title)):
        sides = reader()
        if sides:
            names.update(sides)
    return sorted(f"{x}@{d}" for x in names if x)


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

    R1 (2026-09-07): the league-stripped pair key `<a>-<b>-<date>` ends
    in the date too and is admitted by the same rule; a pair key with
    no date (`kbk-tro`) is not a dated key and never passes here.
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


def _want_prefixes(global_slug: str | None) -> set[str] | None:
    """The venue prefixes his slug's family may match: a C3 family
    (spread / total / btts, on any segment) names its own kind; every
    other slug answers PREFIX_FOR_TYPE on market_type_of, unchanged."""
    from ..copy_sports import market_type_of

    # the C3 reading is behind the owner's identity switch (review
    # major 1): off, a first-half total is 'prop' with no prefix, as
    # before C3
    his = c3_his(global_slug) if yn_identity_on() else None
    if his is not None:
        return {C3_PREFIX[his["family"]]}
    if yn_identity_on() and map_his(global_slug) is not None:
        # E2: the map winner's kind, inside the same switch; off, the
        # slug reads market_type_of's 'unknown' as before
        return {MAP_PREFIX}
    return PREFIX_FOR_TYPE.get(market_type_of(global_slug or ""))


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
    # one spelling per value on BOTH sides of every comparison
    return {_canon_line(n) for n in out}


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

    # C2 (2026-09-06): the Latin letters NFKD does not decompose (ø, ł,
    # đ, ß ...) are MAPPED to their base first (pmus.fold_latin), so
    # 'Tromsø IL' is read as 'tromso il' and no longer counts as
    # erased content; any letter outside that explicit table is still
    # erased content, and still refuses here.
    for ch in _ud.normalize("NFKD", pmus.fold_latin(raw)):
        if ord(ch) > 127 and _ud.category(ch) not in ("Mn", "Cf"):
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
    # C2: his anchor is read through the same explicit Latin fold the
    # venue subject is (pmus.fold_latin) -- 'Tromsø' -> 'tromso', the
    # venue's own ASCII -- never through a fold that drops the letter
    n = " ".join(_norm(pmus.fold_latin(his_title)).split())
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
    if len(question) >= 290:
        # Round 2.2 set this at 108 on the premise of a ~110-char
        # venue truncation; the 2026-08-27 census FALSIFIED it —
        # 119/121-char USL/Copa do Brasil questions arrive intact
        # (the earlier "110-cut" was our own probe's [:110] display
        # cap). The guard survives at the one real cap left in the
        # pipeline (the probe's own [:300] capture), so a string long
        # enough to have been cut by US still refuses; full venue
        # questions recover.
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


# ── THE NAMED-TENNIS BRIDGE (Phase 0, 2026-08-27) ───────────────────
#
# The census measured the named class at moneyline 91 / spread 83 /
# total 62 per 48h and captured the venue's plain named-moneyline
# wording — tennis, named sides. The grounding then proved the tennis
# class dies at ONE clause: _market_rows stamps the question's
# '1:30 AM UTC' clock time as line='30' (the ':' _LINE_CTX context) on
# both aec- sides, and match_side's _lined_ok vetoes the already-
# successful exact name-equality hit. The same misparse family as the
# date-as-line bug that killed the yes/no class.
#
# THE STAMP IS NOT FIXED. Un-poisoning the line arms match_side's
# surname CONTAINMENT tier on the live order path — the tournament
# executed a wrong-person fill through exactly that ('Sena Saito'
# taking Airi Saito's row). This lane instead QUARANTINES the clock
# artifact behind its own witness (the row's line must equal the
# strict parse's own minutes group verbatim) and selects by set
# equality only. Two designs died in attack (9 verified kills); this
# is the judge's synthesis, validated by a 41-assertion executed
# harness before transplant.
#
# PHASE 0: consumed ONLY by resolve_explain's probe. resolve() takes
# no named argument; match_side, _market_rows, _lines_of and
# bridge_explain are byte-untouched.

_NAMED_TOUR_OF = {"itf": "itf", "itfme": "itf", "itfwo": "itf"}
# ONLY census-attested venue families. itfme: 2026-08-27 census.
# itfwo: mapper-evidence run 10 (2026-08-27) recorded the verbatim
# pair itf->itfwo 112 times in one 48h sample — the women's ITF
# family entered by observation, exactly the path the lg_pairs
# telemetry exists for (the whale spelling 'itf' was confirmed by the
# same run: 62 itfme recoveries). atp/wta DO NOT ship: they refuse
# tour_unknown/tour_pair_mismatch with the verbatim pair recorded,
# and enter only via a future round citing production-attested venue
# questions per family.
_NAMED_NAME_FLOOR, _NAMED_NAME_CAP = 2, 4
# Cap 4, not 5: the longest attested tennis name is 2 tokens; 4
# admits 'juan martin del potro'; 5 exactly fits the executed doubles
# kill ('First Last and First Last').
_NAMED_BAD_TOKENS = frozenset({
    "and", "y", "e", "set", "sets", "tiebreak", "tiebreaker", "game",
    "games", "match", "doubles", "winner", "meeting", "first",
    "second", "third", "retirement", "retired", "retire", "retires",
    "walkover", "withdrew", "withdrawal", "withdraws",
    "ace", "aces", "total", "totals", "handicap", "spread",
    "over", "under",
    # Named 1.1: abbreviation family + the separator itself — the
    # implementation fleet rode 'TB' through the title witness and
    # 'vs' through regex backtracking into a name slot.
    "vs", "tb", "sf", "qf", "ret", "wo"})
# This lane OWNS its vocabulary — person identity is not club
# identity, and it must not ride on _BRIDGE_SCOPE_TOKENS surviving a
# refactor. Single-letter tokens refuse (O'Connell → honest miss,
# counted); 'y'/'e' are doubles conjunctions HERE and refuse even
# though the club helper exempts them.
_NAMED_DERIV_TOKENS = frozenset({"set", "sets", "tiebreak",
                                 "tiebreaker", "game", "games",
                                 "doubles", "ace", "aces"})
_NAMED_ORDINAL_RE = re.compile(r"\d+(st|nd|rd|th)")
# POSITIVE prefix grammar (round-6 fleet): refusing market nouns one by
# one is a vocabulary, and a vocabulary can never be proven closed — the
# round-5 additions killed 'Most Aces' while 'Most Double Faults' (whose
# winner is routinely the match LOSER) walked past. A dropped prefix must
# now ATTEST its tour: the only attested family is itf (census
# 2026-08-27, 289 rows; the pinned 'Qualification ITF' shape). Extend by
# observation, never by assumption — exactly the _NAMED_TOUR_OF law.
_NAMED_TOUR_MARKERS = frozenset({"itf"})
# The header grammar a marker-bearing segment must fullmatch, and the
# tier codes (M15/W35/J300) that attest a tournament segment. One 'itf'
# token flattened into the whole prefix laundered any prop noun phrase
# riding the same banner (round-7 critical): attestation is now
# PER-SEGMENT — every colon segment must be a pure tour header or carry
# a tier code. Extended by observation only.
_NAMED_HEADER_TOKENS = frozenset({"itf", "wta", "atp", "men", "women",
                                  "ladies", "junior", "juniors",
                                  "singles"})
_NAMED_TIER_RE = re.compile(r"[mwj]\d{2,3}")
_NAMED_SURFACES = frozenset({"clay", "hard", "grass", "carpet"})


def _named_prefix_attested(prefix: str) -> bool:
    for seg in prefix.split(":"):
        toks = _norm(seg).split()
        if not toks:
            return False
        header = (all(t in _NAMED_HEADER_TOKENS for t in toks)
                  and any(t in _NAMED_TOUR_MARKERS for t in toks))
        # a bare tier code laundered arbitrary prop nouns riding its
        # segment ('M15 Most Double Faults', round-8 critical). The ONE
        # census-attested tournament shape ends with its surface —
        # 'M15 Cap d'Agde (France), clay' — so a tier segment attests
        # only as tier code + anything + terminal surface. No real
        # prop market ends ', clay'.
        # Flashscore (the feed's tournament-name source) writes indoor
        # events 'hard (indoor)' — the terminal token is then the
        # modifier, with the surface immediately before it. Accepted
        # only in that adjacent shape, ahead of the ~October indoor
        # swing that would otherwise silently refuse the census class.
        tail_ok = (toks[-1] in _NAMED_SURFACES
                   or (len(toks) >= 2 and toks[-1] in ("indoor", "outdoor")
                       and toks[-2] in _NAMED_SURFACES))
        tiered = (any(_NAMED_TIER_RE.fullmatch(t) for t in toks)
                  and tail_ok)
        if not (header or tiered):
            return False
    return True


def _tok_subseq(small: list[str], big: list[str]) -> bool:
    i = 0
    for tok in big:
        if i < len(small) and small[i] == tok:
            i += 1
    return i == len(small)


def _named_title_danger(toks: list[str]) -> bool:
    """True when a dropped title segment carries ANY marker from the
    lane's danger vocabulary — single tokens (deriv + bad + ordinals)
    or adjacent-token joins ('tie break' -> 'tiebreak'). Blocking may
    over-refuse; selection may not (round-4 fleet, three kills)."""
    danger = _NAMED_DERIV_TOKENS | _NAMED_BAD_TOKENS
    if any(t in danger or _NAMED_ORDINAL_RE.fullmatch(t) for t in toks):
        return True
    # joins hit the FULL danger set: 'Walk Over', 'With Drawal' and
    # dotted initials ('T.B.' -> 't b' -> 'tb') are still their marker
    return any(len(j) >= 2 and j in danger
               for n in (2, 3) for i in range(len(toks) - n + 1)
               for j in ("".join(toks[i:i + n]),))
_NAMED_MONTHS = dict(_BRIDGE_MONTHS)
# Copied at import — a month added for one lane must never widen the
# other.
_NAMED_Q_PREFIX = "who will win in the upcoming tennis event "
_NAMED_Q_STRICT_RE = re.compile(
    r"^who will win in the upcoming tennis event (?P<a>[a-z ]+?) vs "
    r"(?P<b>[a-z ]+?) scheduled for (?P<mon>[a-z]+) (?P<day>\d{1,2}) "
    r"(?P<yr>\d{4}) at (?P<hh>\d{1,2}) (?P<mm>\d{2}) (?P<ap>am|pm) utc$")


def _name_toks(s) -> list[str]:
    """ORDERED token list — NEVER a set. The tournament proved a
    frozenset here turns code-building into a hash-seed lottery
    ('hirkoy' builds from [hiromasa, koyama], not the reverse)."""
    return _norm(s).split()


def _named_name_ok(toks: list[str]) -> bool:
    if not (_NAMED_NAME_FLOOR <= len(toks) <= _NAMED_NAME_CAP):
        return False
    for t in toks:
        if len(t) < 2 or not t.isalpha() or t in _NAMED_BAD_TOKENS:
            return False
    return True


def _name_code_builds(code: str, toks: list[str]) -> bool:
    """Does the venue/slug code build from an ORDERED name?

    A code is a concatenation of prefixes of a strictly-increasing
    subsequence of the tokens, each piece >= 3 chars or a whole
    token: 'hirkoy' = hir+koy, 'lucacas' = luca+cas, 'koyama' whole,
    'maiito' = mai+ito. Deterministic; order-sensitive by design."""
    code = (code or "").strip()
    toks = list(toks)

    def rec(pos: int, ti: int) -> bool:
        if pos == len(code):
            return True
        for j in range(ti, len(toks)):
            t = toks[j]
            for ln in range(1, min(len(t), len(code) - pos) + 1):
                if code[pos:pos + ln] == t[:ln] and (ln == len(t)
                                                    or ln >= 3):
                    if rec(pos + ln, j + 1):
                        return True
        return False

    return bool(code) and rec(0, 0)


def _name_seq_eq(a: list[str], b: list[str]) -> bool:
    """Person-name equality, ORDER-AWARE (named 1.1).

    Set equality erased order and admitted 'Jose Maria Perez' for a
    'Maria Jose Perez' pick — distinct compound-name people. Exact
    sequence, or the FULL reversal (the surname-first/given-first
    feed variance set-equality deliberately recovered — 'Koyama
    Hiromasa' == 'Hiromasa Koyama'), and nothing else: permutations
    of 3+-token names are different people until proven otherwise."""
    return a == b or a == b[::-1]


def _builds_one(code: str, sides: list[list[str]]) -> int | None:
    """Index of the ONE side the code builds from; None on zero or
    two — every use site refuses ambiguity."""
    hits = [i for i, s in enumerate(sides) if _name_code_builds(code, s)]
    return hits[0] if len(hits) == 1 else None


def named_ml_bridge_explain(rows_kept: list[dict], rows_all: list[dict],
                            outcome: str | None, his_title: str | None,
                            his_slug: str | None,
                            his_event_title: str | None = None,
                            trace: dict | None = None
                            ) -> tuple[dict | None, str]:
    """The tennis named-moneyline lane: (hit, 'ok') or (None, why).

    Consulted only AFTER match_side returns None; Phase 0 — only the
    probe calls it. Every gate is conjunctive and every refusal is
    named; the clock quarantine is the lane's replacement for
    _lined_ok, and the opponent-equality twin gate is its replacement
    for trust."""
    from ..copy_sports import _post_date_tokens, market_type_of

    tr = trace if isinstance(trace, dict) else {}
    on_t = _name_toks(outcome)
    on = " ".join(on_t)
    if not on or on in ("yes", "no", "draw", "tie") or \
            (on_t and on_t[0] in ("over", "under")):
        return None, "not_named"
    if any(ch in str(outcome or "") for ch in "/&"):
        return None, "doubles_shape"
    if _folds_away(outcome):
        return None, "nonlatin_content"
    if len(on_t) < _NAMED_NAME_FLOOR:
        return None, "outcome_thin"
    if not _named_name_ok(on_t):
        return None, "outcome_name_bad"
    if market_type_of(his_slug or "") != "moneyline":
        return None, "wrong_type"
    d = date_of(his_slug)
    if not d:
        return None, "no_date"
    parts = [p for p in (his_slug or "").lower().split("-") if p]
    pre = parts[:parts.index(d[:4])] if d[:4] in parts else None
    if not pre or len(pre) != 3:
        return None, "slug_shape"
    wlg, wa, wb = pre
    if wa == wb:
        return None, "degenerate_codes"
    suffix = _post_date_tokens(parts)
    if suffix and (len(suffix) != 1 or not suffix[0].isalpha()):
        return None, "slug_suffix_shape"
    if suffix and (suffix[0] in _NAMED_BAD_TOKENS
                   or suffix[0] in _NAMED_DERIV_TOKENS
                   or suffix[0] not in (wa, wb)):
        # NAMED 1.1 (implementation-fleet kill): a '-set' derivative
        # suffix LAUNDERED as a pick marker through a name-prefix
        # collision — 'set' is a 3-char DP prefix of 'Setkic', so the
        # builds test authenticated a set-winner marker as his pick.
        # The suffix must BE one of his slug's own codes, and never a
        # derivative/bad token, before any building is consulted.
        return None, "slug_suffix_not_code"
    if wlg not in _NAMED_TOUR_OF:
        tr["lg_pair_seen"] = {"whale_lg": wlg}
        return None, "tour_unknown"
    rawt = str(his_title or "")
    if _folds_away(rawt):
        return None, "nonlatin_content"
    # The real daytime ITF title carries TWO colons — 'ITF MEN -
    # SINGLES: M15 Cap d'Agde (France), clay: A vs B' (census 2026-08-27:
    # 289 of 309 named refusals were this shape; the overnight corpus
    # that sized the old single-colon gate never showed it). rpartition
    # takes the LAST colon, so the matchup clause is unchanged, and the
    # ENTIRE multi-segment prefix is scanned for derivative markers
    # below — 'DOUBLES:' and 'First Set Winner:' refuse wherever the
    # marker sits. A colon inside the matchup (no attested case) still
    # refuses at the two-halves check.
    prefix, _, matchup = rawt.rpartition(":")
    if prefix:
        ptoks = _norm(prefix).split()
        if _named_title_danger(ptoks):
            # 'First Set Winner: X vs Y', 'Match Tie-Break: ...',
            # '2nd Meeting: ...' — the dropped prefix is scanned against
            # the lane's FULL danger vocabulary (deriv + bad + ordinals)
            # with adjacent-token joins, so the venue's two-word
            # spellings ('Tie Break') cannot walk past the single-token
            # set.
            return None, "title_prefix_derivative"
        if not _named_prefix_attested(prefix):
            # the POSITIVE gate, per segment: 'Most Double Faults',
            # 'ITF Most Double Faults', '...clay: Fastest Serve' —
            # every prop-market segment refuses structurally, because
            # no amount of tour banner in SIBLING segments attests it.
            # Blocking may over-refuse; selection may not.
            return None, "title_prefix_unattested"
    halves = [" ".join(h.split()) for h in
              re.split(r"\s+vs\.?\s+", _norm(matchup)) if h.strip()]
    if len(halves) != 2:
        return None, "title_shape"
    ht = [h.split() for h in halves]
    for h in ht:
        if not (1 <= len(h) <= _NAMED_NAME_CAP):
            return None, "title_half_bad"
        if any((not t.isalpha()) or t in _NAMED_BAD_TOKENS for t in h):
            return None, "title_half_bad"
        if any(len(j) >= 2 and j in (_NAMED_DERIV_TOKENS | _NAMED_BAD_TOKENS)
               for n in (2, 3) for i in range(len(h) - n + 1)
               for j in ("".join(h[i:i + n]),)):
            # 'Tie Break' joins to 'tiebreak' — a marker split across
            # tokens is still a marker, in the halves as in the prefix
            return None, "title_half_bad"
    ia, ib = _builds_one(wa, ht), _builds_one(wb, ht)
    if ia is None or ib is None or ia == ib:
        # THE ITO LAW, enforced by the witness itself: on a
        # same-surname board the codes carry more than the surname
        # ('maiito'/'aoiito'), so a surname-only title cannot
        # corroborate them and the pick safely refuses; a full-name
        # title recovers.
        return None, "title_slug_mismatch"
    if _folds_away(his_event_title):
        return None, "nonlatin_content"
    if any(ch in str(his_event_title or "") for ch in "/&"):
        return None, "doubles_shape"
    # The EVENT title wears the same Flashscore banner as the whale
    # title (census 2026-08-27 run 9: his_event_side_bad 247 +
    # unsplittable 55 — the banner tokens flooded the side split).
    # Same treatment, same gates, distinct census reasons.
    rawe = str(his_event_title or "")
    eprefix, _, ematchup = rawe.rpartition(":")
    if eprefix:
        eptoks = _norm(eprefix).split()
        if _named_title_danger(eptoks):
            return None, "event_prefix_derivative"
        if not _named_prefix_attested(eprefix):
            return None, "event_prefix_unattested"
    esides = [" ".join(s.split()) for s in
              re.split(r"\s+vs\.?\s+", _norm(ematchup))
              if s.strip()]
    if len(esides) != 2:
        return None, "his_event_unsplittable"
    et = [s.split() for s in esides]
    for s in et:
        if not (1 <= len(s) <= _NAMED_NAME_CAP):
            return None, "his_event_side_bad"
        if any((not t.isalpha()) or len(t) < 2
               or t in _NAMED_BAD_TOKENS for t in s):
            return None, "his_event_side_bad"
    ea, eb = _builds_one(wa, et), _builds_one(wb, et)
    if ea is None or eb is None or ea == eb:
        return None, "his_event_mismatch"
    for h in ht:
        # EVERY title-half token must live, in order, inside one of the
        # corroborated event-title sides (or its reversal). A colonless
        # prop title ('Most Double Faults Koyama vs Castelnuovo') hides
        # its market nouns inside a half where the prefix gates cannot
        # see them; the event witness can (round-7 fleet).
        if not any(_tok_subseq(h, s) or _tok_subseq(h, s[::-1])
                   for s in et):
            return None, "title_half_alien"
    oc = [c for c in (wa, wb) if _name_code_builds(c, on_t)]
    if len(oc) != 1:
        return None, "outcome_code_ambiguous"
    his_code = oc[0]
    my_i = ea if his_code == wa else eb
    his_side_t, opp_t = et[my_i], et[1 - my_i]
    if not (_name_seq_eq(his_side_t, on_t)
            or (len(his_side_t) == 1 and his_side_t[0] == on_t[-1])):
        return None, "his_event_side_mismatch"
    if not _named_name_ok(opp_t):
        # THE TWIN GATE'S PRECONDITION: the opponent witness is
        # full-name, no exceptions. A feed that does not state the
        # opponent's full name cannot rule out a name twin — an
        # honest miss the census counts.
        return None, "opponent_witness_thin"
    if suffix and not (_name_code_builds(suffix[0], on_t)
                       and not _name_code_builds(suffix[0], opp_t)):
        return None, "slug_pick_mismatch"
    if (_lines_of(rawt) | _lines_of(outcome)
            | slug_lines(his_slug)):
        # rawt, not matchup: 'Handicap -3.5: A vs B' states its line in
        # the DROPPED prefix — the invisible-evidence family (round-4
        # fleet). The attested census title carries no _lines_of hits.
        return None, "his_signal_lined"
    if signed_line(outcome) or signed_line(rawt):
        return None, "his_signal_signed"
    if any(not (r.get("event_slug") or "").strip() for r in rows_all):
        return None, "event_slug_missing"
    if len({r.get("event_slug") or "" for r in rows_all}) > 1:
        return None, "multi_event_pool"
    passing = []
    for r in rows_kept:
        gate = None
        ident = str(r.get("identifier") or "").lower()
        rq = str(r.get("question") or "")
        nq = " ".join(_norm(rq).split())
        m = None
        if _prefix_of(ident) != "aec":
            gate = "prefix_not_aec"
        elif r.get("kind") != "side":
            gate = "kind_not_side"
        elif str(r.get("signed") or "").strip():
            gate = "row_signed"
        elif not r.get("intent"):
            gate = "no_intent"
        elif _folds_away(rq) or _folds_away(r.get("event_title")) or \
                _folds_away(r.get("side_norm")):
            gate = "nonlatin_content"
        elif any(ch in rq for ch in "/&"):
            gate = "doubles_shape"
        else:
            ip = [p for p in ident.split("-") if p]
            if len(ip) != 7:
                # EXACTLY ['aec', lg, ca, cb, YYYY, MM, DD]: every
                # set1/-2/-s2 derivative identifier is structurally
                # non-candidate.
                gate = "ident_shape"
            elif "-".join(ip[4:]) != d:
                gate = "ident_date_mismatch"
            elif ident != str(r.get("market_slug") or "").lower() or \
                    str(r.get("event_slug") or "").lower() != ident[4:]:
                gate = "slug_family_mismatch"
            elif ip[1] not in _NAMED_TOUR_OF or \
                    _NAMED_TOUR_OF[ip[1]] != _NAMED_TOUR_OF.get(wlg):
                gate = "tour_pair_mismatch"
                tr.setdefault("lg_pairs", []).append((wlg, ip[1]))
            elif len(rq) >= 290:
                gate = "question_maybe_truncated"
            else:
                m = _NAMED_Q_STRICT_RE.fullmatch(nq)
                if not m:
                    gate = "q_not_strict_shape"
                elif m.group("mon") not in _NAMED_MONTHS:
                    gate = "q_month_unknown"
                elif (int(m.group("yr")),
                      _NAMED_MONTHS[m.group("mon")],
                      int(m.group("day"))) != tuple(
                          int(x) for x in d.split("-")):
                    gate = "q_date_mismatch"
                elif not (1 <= int(m.group("hh")) <= 12
                          and 0 <= int(m.group("mm")) <= 59):
                    gate = "q_clock_invalid"
        if gate is None and m is not None:
            at, bt = m.group("a").split(), m.group("b").split()
            if not (_named_name_ok(at) and _named_name_ok(bt)):
                gate = "q_name_bad"
            elif set(at) == set(bt):
                gate = "question_degenerate"
            else:
                vt = [s.split() for s in
                      (" ".join(x.split()) for x in
                       re.split(r"\s+vs\.?\s+",
                                _norm(r.get("event_title") or "")))
                      if s]
                if len(vt) != 2 or {frozenset(x) for x in vt} != \
                        {frozenset(at), frozenset(bt)}:
                    gate = "event_title_feed_mismatch"
                else:
                    ca, cb = ident.split("-")[2], ident.split("-")[3]
                    ja = _builds_one(ca, [at, bt])
                    jb = _builds_one(cb, [at, bt])
                    if ja is None or jb is None or ja == jb:
                        gate = "venue_code_mismatch"
                    else:
                        rl = str(r.get("line") or "").strip()
                        if rl and not (_lines_of(rq) == {rl}
                                       and rl == m.group("mm")):
                            # THE CLOCK QUARANTINE — the lane's
                            # replacement for _lined_ok. A line is
                            # admissible ONLY as the authenticated
                            # clock artifact: the question's sole
                            # parsed line AND verbatim-equal to the
                            # strict parse's own minutes group. A
                            # smuggled real bet line fails both ways.
                            gate = "line_not_clock_artifact"
                        else:
                            ka = _builds_one(wa, [at, bt])
                            kb = _builds_one(wb, [at, bt])
                            if ka is None or kb is None or ka == kb:
                                gate = "cross_code_mismatch"
                            else:
                                st = _name_toks(r.get("side_norm"))
                                if not (_name_seq_eq(st, at)
                                        or _name_seq_eq(st, bt)):
                                    gate = "side_not_in_question"
                                elif not ((_name_seq_eq(on_t, at)
                                           and _name_seq_eq(opp_t,
                                                            bt))
                                          or (_name_seq_eq(on_t, bt)
                                              and _name_seq_eq(
                                                  opp_t, at))):
                                    # THE TWIN GATE: whale pair ==
                                    # venue pair, bijectively, full
                                    # names, set equality, never
                                    # containment.
                                    gate = "opponent_mismatch"
        if gate is None:
            passing.append(r)
        else:
            g = tr.setdefault("row_gates", [])
            if len(g) < 12:
                g.append({"ident_tail": ident[-40:], "gate": gate})
    cands = [r for r in passing
             if _name_seq_eq(_name_toks(r.get("side_norm")), on_t)]
    if len(cands) != 1:
        return None, ("no_candidate_row" if not cands
                      else "multiple_candidates")
    sel = cands[0]
    sibs = [r for r in passing
            if str(r.get("identifier") or "").lower()
            == str(sel.get("identifier") or "").lower()]
    sib_ok = (len(sibs) == 2 and any(
        _name_seq_eq(_name_toks(a.get("side_norm")), on_t)
        and _name_seq_eq(_name_toks(b.get("side_norm")), opp_t)
        for a, b in (sibs, sibs[::-1])))
    if not sib_ok:
        # SIBLING CORROBORATION: on the aec- family only intent names
        # a side; a candidate whose sibling is absent is unverifiable.
        return None, "sibling_side_missing"
    if {r.get("intent") for r in sibs} != \
            {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"}:
        return None, "sibling_intent_broken"
    blockers = set()
    for r in rows_all:
        if _folds_away(r.get("question")):
            return None, "nonlatin_in_pool"
        nq = " ".join(_norm(str(r.get("question") or "")).split())
        if _NAMED_Q_PREFIX.rstrip() in nq:
            # NAMED 1.1: startswith let a LEADING marker escape
            # ('2nd Meeting: Who will win...') — the implementation
            # fleet admitted meeting 1 for a meeting-2 pick. The
            # venue wording ANYWHERE in a question blocks; blocking
            # may over-refuse, selection may not.
            blockers.add((str(r.get("identifier") or "").lower(), nq))
    want = (str(sel.get("identifier") or "").lower(),
            " ".join(_norm(str(sel.get("question") or "")).split()))
    if blockers != {want}:
        # ANY tennis-named question in the pool must BE the selected
        # market — prefix startswith, no token analysis, so marker
        # placement cannot hide. Blocking may over-refuse (lawful);
        # selection may not.
        tr["blockers"] = sorted(b[0][-40:] for b in blockers)[:12]
        return None, "event_scan_ambiguous"
    return sel, "ok"


# ── THE YES/NO IDENTITY BRANCH (to-a-tee program Phase 2, owner order
# 2026-09-02 "I want us to match everything ... mirror the whales to a
# tee"; the coverage lens's §2.f / §4(c) finding, its market refutation
# F2 and its engineering refutation) ──────────────────────────────────
#
# The venue lists RN1's soccer per-team markets — probe NAMEDML-Q shows
# the yes and the no row of atc-spl-neo-kha-2026-09-03-neo — and premap
# refused every one of them twice over: the title-date phantom line
# (fixed in match_side below) and _questions_agree, which needs the two
# feeds to WORD the proposition alike ('Will NEOM SC win on
# 2026-09-03?' against 'Will NEOM SC win against Al Khaleej Saudi Club
# in the Saudi Pro League match scheduled for Sep 3, 2026?'). The copy
# lane's own answer, pmus.resolve_team_yesno_exact, maps them — under
# mapping_src 'yesno_exact', a class live_executor.QUARANTINE_RESUME_SRC
# does not admit, so P1's admission would refuse every one of them
# (refutation F2): zero dollars of coverage until that class certifies.
#
# This branch admits the row on IDENTITY, not wording. His slug names
# the contract outright — <lg>-<a>-<b>-<date>-<t> is the venue's own
# atc- identifier minus the kind prefix — so the row whose identifier
# is byte-for-byte "atc-" + his slug IS his market, on his date, on his
# side token. Three conjuncts, each reused verbatim from a reviewed
# gate elsewhere in this repository and none re-implemented:
#   1. identifier == "atc-" + his_slug, byte-for-byte. No normalising:
#      a slug that needs normalising is not the identifier.
#   2. the row's question fullmatches pmus._YN_Q_PATTERNS — the one
#      measured per-team template — with a date clause equal to his
#      slug's date (pmus._yn_date_ok) and every free slot scope-screened
#      (pmus._yn_slot_bad), so a reserves/half/aggregate qualifier in
#      the venue's wording refuses.
#   3. _bridge_title_subject(his_title, his_slug) names a subject whose
#      dated win-title agrees with his slug's date, and that subject
#      names the question's subject (pmus._yn_name_match) — a title
#      naming the OTHER team is metadata shear and refuses.
# Yes takes the yes row and it must carry the venue's own BUY_LONG; No
# takes the no row and it must carry BUY_SHORT (his No token is draw
# plus opponent, the venue's SHORT on the same identifier — equal
# payoff, refutation F2); any other intent is a shape the census has
# never seen and refuses. No opponent witness is needed: the identifier
# already carries both team codes and his side, which is what
# resolve_team_yesno_exact's witness gates reconstruct from names when
# no identifier is at hand. The evidence floor on raw tokens
# (pmus._yn_thin) is likewise not applied — it guards name-derived game
# identity, and here the game identity is the identifier. The line
# guard still applies in match_side, so a lined row never takes an
# unlined pick; exactly one row may pass, two is ambiguity and refuses.
#
# The hit keeps source 'premap' — the class the quarantine admits and
# the class P1 admission reads — and resolve labels it matched_by
# 'premap_identity' so fills stay attributable and the branch can be
# killed on its own. DARK BY DEFAULT: PREMAP_YN_IDENTITY=on is the
# owner's flip, exactly like PREMAP_NAMED_LANE, so enabling is a config
# change and rollback needs no deploy; with the switch off every
# caller's answer is byte-identical to before this branch existed.
PREMAP_YN_IDENTITY_ENV = "PREMAP_YN_IDENTITY"
_YN_IDENTITY_INTENT = {"yes": "ORDER_INTENT_BUY_LONG",
                       "no": "ORDER_INTENT_BUY_SHORT"}


def yn_identity_on() -> bool:
    """The owner's flip for the identity branch: off unless 'on'."""
    return os.getenv(PREMAP_YN_IDENTITY_ENV, "").strip().lower() == "on"


def _yn_his_identifiers(his_slug: str) -> frozenset:
    """The venue identifiers that ARE his slug, byte-for-byte.

    His feed slug is kindless — <lg>-<a>-<b>-<date>-<t> — and the
    venue's per-team contract is that slug under the atc- kind prefix.
    When his slug already carries the prefix it IS the venue's own
    identifier (the copy lane's candidate grammar hands the slug itself
    through as its last candidate, copy_sports._us_slug_candidates) and
    'atc-atc-…' names nothing. ONE answer for both arms: the wording
    arm's veto and the identity branch read this set and nothing else
    (re-review of the mapping unit, 2026-09-03, minor: the identity
    branch demanded 'atc-' + his slug only, so the venue slug the
    wording veto accepted was refused one arm over — fail-closed, but
    two readings of 'his identifier' where the veto's comment promised
    one). No normalising: a slug that needs normalising is not the
    identifier."""
    if his_slug.startswith("atc-"):
        return frozenset({his_slug})
    return frozenset({"atc-" + his_slug})


def _yn_title_is_his_game(his_title: str | None,
                          his_slug: str | None) -> bool:
    """Is his title HIS OWN GAME — the dated 'Will X win on <date>?' on
    his slug's date, or the bare 'Will X win?' — and nothing else?

    (Re-review of the mapping unit, 2026-09-03, major.) The date strip
    in match_side made the wording arm reach dated per-team titles for
    the first time, and that arm's question test is containment: 'will
    neom sc win' sits inside 'will neom sc win the 1st half on 2026 09
    03', so a title asking about the first half, the reserves, the
    aggregate or a two-goal margin took the venue's FULL-GAME atc-
    contract with source 'premap' and no switch — a wrong-market trade
    the identity branch refused by design (title_not_win_shape) and the
    wording arm never looked at. At HEAD the phantom '-09' refused the
    ISO forms by accident; only their non-ISO forms were live. So the
    wording arm answers to the bridge's own title gate, the one the
    identity branch already answers to: the title must be his dated
    win-question on his slug's date (_bridge_title_subject names a
    subject) or the dateless bare form (title_undated — HEAD admitted
    it, and test_a_bare_win_question_was_always_the_wording_branchs
    holds it there). Everything else refuses, the safe direction: a
    qualifier, a margin, an aggregate, a title dated another day than
    his slug, a subject carrying a digit, and a title whose fold erases
    content (_folds_away — a non-Latin qualifier the whole gate stack
    is blind to). Three deliberate departures from HEAD, all refusals,
    each measured against HEAD's own tree (scratchpad rr3_probe):
    'Will X win on 9/3/2026?' resolved at HEAD and refuses now (the
    grammar reads ISO and month-name dates only, and a date it cannot
    read is a date it cannot hold against his slug); a DATELESS title
    carrying a qualifier ('Will X win? (Aggregate)', '... the 1st
    half?', '... (Παράταση)?') resolved at HEAD against a terse venue
    row and refuses now — the same wrong-market trade with the date
    left off; and a month-name title dated ANOTHER DAY than his slug
    ('Will X win on Sep 4, 2026?' on a 2026-09-03 slug) resolved at
    HEAD and refuses now — the wrong game outright."""
    if _folds_away(his_title):
        return False
    anchor, why = _bridge_title_subject(his_title, his_slug)
    return anchor is not None or why == "title_undated"


def yn_identity_rows(rows: list[dict], outcome: str | None,
                     his_title: str | None,
                     his_slug: str | None) -> list[dict]:
    """Every row that IS his own per-team contract on his literal
    Yes/No side — normally one; the caller demands exactly one. Pure:
    no network, no table, nothing but the rows it is handed.

    Since C2 (2026-09-06) the body lives in _yn_pick, which reads the
    same two facts this branch always read — the anchor from
    _bridge_title_subject(his_title, his_slug) and the identifier set
    from _yn_his_identifiers(his_slug) — and then, when his own
    identifier is not on the board at all, the league-alias and draw
    readings documented there. This four-argument form is the
    identity arm alone: no event title, so no alias and no draw."""
    return _yn_pick(rows, outcome, his_title, his_slug, None, None,
                    identity_only=True)


# ---------------------------------------------------------------- C2
# THE LEAGUE-CODE ALIAS AND THE DRAW (2026-09-06, owner: "I need you
# to map more of his trades ... la liga should be very easy to map";
# "we need more volume"). In the two hours to 19:04Z the shadow read
# 260 of his markets and mapped 3; the bulk was soccer per-team yes/no
# the identity branch above refused for four reasons the premap-rows
# dump made exact (docs/mirror-coverage.md §8):
#   * 'serie a' failed the CLUB scope screen on the LEAGUE slot
#     (single-letter 'a') — every Serie A market;
#   * a name with a digit ('Bologna FC 1909') never matched the
#     '[a-z ]' slots — every such opponent;
#   * 'Tromsø' folded to 'troms' and never matched the venue's ASCII
#     'Tromso' — every ø/ł/đ club;
#   * his league code is not the venue's ('nor' vs 'els', 'arg' vs
#     'lpa', 'pol' vs 'ekst') while the team codes and the date are
#     byte-identical — so 'atc-' + his slug names nothing.
# The first three are fixed at their gates (pmus._yn_league_slot_bad,
# the '[a-z0-9 ]' slots, pmus.fold_latin). The fourth is admitted by
# _yn_alias_pick under IDENTITY, never a table: his slug
# <lg>-<a>-<b>-<date>-<t> is read and a venue row is his only when
#   (i)   NO row carries his own identifier ('atc-' + his slug): an
#         identifier that IS his and refused stays refused;
#   (ii)  exactly ONE venue league code carries his <a>-<b>-<date>-<t>
#         suffix among the rows the keys fetched — two codes is
#         yn:league-ambiguous and refuses (a same-day women's/youth/
#         esoccer twin with the same team codes lands here);
#   (iii) the row's question fullmatches the measured per-team
#         template on his slug's date, its subject names his title's
#         anchor and its opponent names the OTHER side of HIS event
#         title (game agreement by NAMES on both teams — the witness
#         the identity branch never needed because the identifier
#         carried the league); the league slot passes the league rule;
#   (iv)  side_norm / intent are the venue's own for his Yes/No.
# The alias observed (his code -> venue code) rides the hit as
# `league_alias` and resolve labels it matched_by 'premap_alias',
# source 'premap'. The draw (_yn_draw_pick) is the same identity on the
# '-draw' suffix: the venue's dated draw template naming BOTH his
# event's teams in either order, his title a draw question naming
# nothing but those teams, on his date. Every refusal is named
# (trace['refusal']) so the census counts it.
_YN_SLUG_RE = re.compile(
    r"^(?P<lg>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<t>[a-z0-9]+)$")

# tokens the draw title may carry beside the two team names and 'draw'
_YN_DRAW_TITLE_FURNITURE = frozenset({
    "will", "the", "a", "an", "in", "end", "ends", "be", "there",
    "match", "game", "draw", "on", "between", "and", "vs", "of", "v"})
_YN_DRAW_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r" (\d{1,2})(?:,? (\d{4}))?\b", re.I)


def _yn_slug_parts(his_slug: str | None) -> dict | None:
    """<lg>, <a>, <b>, <date>, <t> of his kindless slug, t one of a, b
    or 'draw'; None for any other shape (a kind-prefixed slug is the
    venue's own identifier and has no alias)."""
    m = _YN_SLUG_RE.fullmatch(his_slug or "")
    if not m:
        return None
    p = m.groupdict()
    if p["a"] == p["b"] or p["t"] not in (p["a"], p["b"], "draw"):
        return None
    return p


def _yn_event_sides(his_event_title: str | None) -> list[str] | None:
    """The two team names of his event title ('A vs. B'), folded and
    normalized, or None when it does not name exactly two."""
    if not his_event_title or _folds_away(his_event_title):
        return None
    n = " ".join(_norm(pmus.fold_latin(his_event_title)).split())
    sides = [" ".join(x.split()) for x in re.split(r"\s+vs\s+", n)
             if x.strip()]
    if len(sides) != 2 or any(pmus._yn_slot_bad(s) for s in sides):
        return None
    return sides


def _yn_rows_by_code(rows: list[dict], suffix: str) -> dict[str, list]:
    """The atc- rows whose identifier is 'atc-<code>-<suffix>', by
    venue league code."""
    by_code: dict[str, list] = {}
    tail = "-" + suffix
    for r in rows:
        ident = r.get("identifier")
        if not isinstance(ident, str) or not ident.startswith("atc-"):
            continue
        rest = ident[4:]
        if not rest.endswith(tail):
            continue
        code = rest[:-len(tail)]
        if not code or "-" in code:
            continue
        by_code.setdefault(code, []).append(r)
    return by_code


def _yn_team_question(q: str | None, d: str) -> tuple[dict | None, str | None]:
    """The venue's per-team question read through the measured template
    on his date: ({'subj', 'opp', 'lg'}, None), or (None, the refusal
    name). The one reading _yn_team_row and the C5 witness share (C5,
    2026-09-07): fold, P4 fullmatch, his date, the club scope screen on
    both names, the league rule on the league slot."""
    if not q or _folds_away(q):
        return None, "yn:folds"
    n = " ".join(_norm(pmus.fold_latin(str(q))).split())
    gm = None
    for pat in pmus._YN_Q_PATTERNS:
        gm = pat.fullmatch(n)
        if gm:
            break
    if gm is None:
        return None, "yn:shape"
    gd = gm.groupdict()
    if not pmus._yn_date_ok(gd, d):
        return None, "yn:qdate"
    subj = " ".join((gd.get("subj") or "").split())
    opp = " ".join((gd.get("opp") or "").split())
    lgq = " ".join((gd.get("lg") or "").split())
    if pmus._yn_slot_bad(subj) or pmus._yn_slot_bad(opp):
        return None, "yn:scope"
    if pmus._yn_league_slot_bad(lgq):
        return None, "yn:league-slot"
    return {"subj": subj, "opp": opp, "lg": lgq}, None


def _yn_team_row(r: dict, *, d: str, anchor: str,
                 opp_witness: str | None) -> str | None:
    """One per-team row against his date, his anchor and (alias only)
    his opponent witness: the refusal name, or None when it passes.
    The side and the intent are the caller's filter."""
    gd, why = _yn_team_question(r.get("question"), d)
    if gd is None:
        return why
    if not pmus._yn_name_match(gd["subj"], anchor):
        return "yn:subj"
    if opp_witness is not None and not pmus._yn_name_match(gd["opp"], opp_witness):
        return "yn:opp-witness"
    return None


def _yn_gate(cands: list[dict], on: str, want_intent: str,
             judge) -> tuple[list[dict], str]:
    """Rows on his literal side with the venue's own intent for it,
    each through `judge`; returns (passing rows, the first refusal
    name — 'yn:side' when no row is on his side at all)."""
    out: list[dict] = []
    refusal = "yn:side"
    for r in cands:
        if _norm(r.get("side_norm")) != on:
            continue
        if r.get("intent") != want_intent:
            if refusal == "yn:side":
                refusal = "yn:intent"
            continue
        why = judge(r)
        if why is None:
            out.append(r)
        elif refusal in ("yn:side", "yn:intent"):
            refusal = why
    return out, refusal


def _yn_title_is_his_draw(his_title: str | None, d: str,
                          sides: list[str]) -> tuple[bool, str]:
    """Is his title a DRAW question on his game and nothing else? A
    closed VETO, not a grammar (the feed's draw wording is unattested):
    the fold must not go blind; any date it carries must be his slug's
    (ISO or month-name; a form the gate cannot read refuses); it must
    say 'draw' and never 'win'; and every token that is not furniture
    must belong to one of his event's two team names — a qualifier
    (half, aggregate, margin, 'no bet') refuses."""
    if not his_title or _folds_away(his_title):
        return False, "folds"
    raw = str(his_title)
    iso = date_of(raw)
    if iso and iso != d:
        return False, "date_mismatch"
    for mo, day, yr in _YN_DRAW_MONTH_RE.findall(raw):
        m = _BRIDGE_MONTHS.get(mo.lower())
        want = (int(d[0:4]), int(d[5:7]), int(d[8:10]))
        if m is None or (int(yr) if yr else want[0], m, int(day)) != want:
            return False, "date_mismatch"
    if re.search(r"\b\d{1,2}/\d{1,2}", raw):
        return False, "date_unreadable"
    stripped = _YN_DRAW_MONTH_RE.sub(" ", re.sub(r"\d{4}-\d{2}-\d{2}", " ", raw))
    toks = _norm(pmus.fold_latin(stripped)).split()
    if "draw" not in toks:
        return False, "not_draw_shape"
    if any(t.startswith("win") for t in toks):
        return False, "not_draw_shape"
    team_toks = {t for s in sides for t in s.split()}
    extra = [t for t in toks if t not in _YN_DRAW_TITLE_FURNITURE
             and t not in team_toks]
    if extra:
        return False, "qualifier"
    return True, "ok"


def _yn_draw_row(r: dict, *, d: str, sides: list[str]) -> str | None:
    """One draw row against his date and BOTH his teams, either order."""
    q = r.get("question")
    if not q or _folds_away(q):
        return "yn:folds"
    n = " ".join(_norm(pmus.fold_latin(str(q))).split())
    gm = pmus._YN_DRAW_Q_RE.fullmatch(n)
    if gm is None:
        return "yn:shape"
    gd = gm.groupdict()
    if not pmus._yn_date_ok(gd, d):
        return "yn:qdate"
    qa = " ".join((gd.get("a") or "").split())
    qb = " ".join((gd.get("b") or "").split())
    lgq = " ".join((gd.get("lg") or "").split())
    if pmus._yn_slot_bad(qa) or pmus._yn_slot_bad(qb):
        return "yn:scope"
    if pmus._yn_league_slot_bad(lgq):
        return "yn:league-slot"
    nm = pmus._yn_name_match
    if not ((nm(qa, sides[0]) and nm(qb, sides[1]))
            or (nm(qa, sides[1]) and nm(qb, sides[0]))):
        return "yn:draw-names"
    return None


def _yn_pick(rows: list[dict], outcome: str | None, his_title: str | None,
             his_slug: str | None, his_event_title: str | None,
             trace: dict | None, *, identity_only: bool = False,
             names_witness: str | None = None) -> list[dict]:
    """The yes/no branch: identity, then the league alias, then the
    names-witnessed row (C6-N), then the draw — see the block comments
    above and at _yn_names_pick. Pure. Returns the passing rows (alias,
    names and draw hits are COPIES carrying `yn_branch` and, for an
    alias, `league_alias`); `trace` receives the refusal name or the
    hit's labels. `names_witness` is the OTHER club's name as his stored
    moneyline title states it (names_resolve reads it when his event
    title is not stored); None otherwise."""
    def _t(**kw):
        if trace is not None:
            trace.update(kw)

    on = _norm(outcome)
    want_intent = _YN_IDENTITY_INTENT.get(on)
    if not want_intent or not his_slug:
        _t(refusal="yn:outcome")
        return []
    d = date_of(his_slug)
    if not d:
        _t(refusal="yn:slug-date")
        return []
    # a title whose fold erases content is blind and refuses first
    # (pmus yn:title-folds)
    if _folds_away(his_title):
        _t(refusal="yn:title-folds")
        return []
    parts = _yn_slug_parts(his_slug)
    if parts is not None and parts["t"] == "draw":
        # C3: the identity arm reads his own market title as the draw's
        # witness (the feed's attested wording names both sides); with
        # no event title and no such title it stays yn:draw-unwitnessed
        return _yn_draw_pick(rows, parts, on, want_intent, d, his_title,
                             None if identity_only else his_event_title,
                             trace, identity_only=identity_only)
    # the bridge's own title gate consumes his date clause and refuses
    # any title that is not his dated win-question
    anchor, why = _bridge_title_subject(his_title, his_slug)
    if anchor is None:
        _t(refusal=("yn:name-digits" if why == "subject_has_digit"
                    else f"yn:title-{why}"))
        return []
    want_ids = _yn_his_identifiers(his_slug)
    mine = [r for r in rows if isinstance(r.get("identifier"), str)
            and r["identifier"] in want_ids]
    out, refusal = _yn_gate(mine, on, want_intent,
                            lambda r: _yn_team_row(r, d=d, anchor=anchor,
                                                   opp_witness=None))
    if out:
        _t(matched_by="premap_identity", admitted=want_ids)
        return out
    if mine:
        # his OWN identifier is on the board and refused: no other
        # league's row may stand in for it
        _t(refusal=refusal)
        return []
    if identity_only:
        _t(refusal="yn:no-row")
        return []
    alias_trace: dict = {}
    out = _yn_alias_pick(rows, parts, on, want_intent, d, anchor,
                         his_event_title, alias_trace)
    if out or alias_trace.get("refusal") != "yn:no-row":
        _t(**alias_trace)
        return out
    # C6-N: no row under ANY league code carries his suffix -- both club
    # codes differ -- so the venue's own question is the only witness
    return _yn_names_pick(rows, parts, on, want_intent, d, anchor,
                          his_event_title, trace, other=names_witness)


def _yn_alias_pick(rows: list[dict], parts: dict | None, on: str,
                   want_intent: str, d: str, anchor: str,
                   his_event_title: str | None,
                   trace: dict | None) -> list[dict]:
    def _t(**kw):
        if trace is not None:
            trace.update(kw)

    if parts is None:
        _t(refusal="yn:no-row")
        return []
    suffix = f"{parts['a']}-{parts['b']}-{parts['date']}-{parts['t']}"
    by_code = _yn_rows_by_code(rows, suffix)
    if not by_code:
        _t(refusal="yn:no-row")
        return []
    if len(by_code) > 1:
        _t(refusal="yn:league-ambiguous", league_codes=sorted(by_code))
        return []
    (code, cands), = by_code.items()
    if code == parts["lg"]:
        # cannot happen past the identity arm (that IS his identifier);
        # named so a future edit that reorders the arms is visible
        _t(refusal="yn:identity-refused")
        return []
    sides = _yn_event_sides(his_event_title)
    if sides is None:
        _t(refusal="yn:alias-unwitnessed")
        return []
    hit = [s for s in sides if pmus._yn_name_match(s, anchor)]
    if len(hit) != 1:
        _t(refusal="yn:event-shear")
        return []
    other = sides[1] if hit[0] is sides[0] else sides[0]
    out, refusal = _yn_gate(cands, on, want_intent,
                            lambda r: _yn_team_row(r, d=d, anchor=anchor,
                                                   opp_witness=other))
    if not out:
        _t(refusal=refusal)
        return []
    alias = f"{parts['lg']}->{code}"
    _t(matched_by="premap_alias", league_alias=alias,
       admitted=frozenset(r["identifier"] for r in out))
    return [dict(r, yn_branch="alias", league_alias=alias) for r in out]


# ---------------------------------------------------------------- C6-N
# THE NAMES-WITNESSED PER-TEAM ROW WHEN NO CODE IS SHARED (2026-09-07;
# gap_esports_chi §1.1-1.2, §3.2; gap_league_codes §b, §c R3). His feed
# and the venue code the same game with a different league code AND two
# different club codes:
#   his    chi1-cdh-csc-2026-09-06-csc  "Will CSD Colo-Colo win on 2026-09-06?"
#          event "CD Huachipato vs. CSD Colo-Colo"   (No $24,372; his +30,419 win)
#   venue  atc-pdc-hua-col-2026-09-06-col  yes/LONG no/SHORT, line empty,
#          "Will CSD Colo-Colo win against CD Huachipato in the Primera
#          Chile match scheduled for Sep 6, 2026?"
#   his    col1-dep-mif-…-mif / col1-cle-gdr-…-gdr   venue  atc-lco-per-mil-… / atc-lco-leq-agu-…
# The alias arm refuses yn:no-row (no row under any code carries his
# <a>-<b>-<date>-<t>) and C5 has no candidate (it needs exactly one shared
# code). Yet the venue's own per-team question names his club as his title
# states it and his opponent as his event title states it, on his date --
# C2's alias witness (premap.py _yn_alias_pick step iii) minus the suffix
# equality. That, and nothing else, admits the row:
#   (i)   his title is his dated win question (_bridge_title_subject: the
#         anchor X; the C2 gate, already passed by _yn_pick);
#   (ii)  his event title names exactly two sides, X matches exactly ONE
#         (yn:event-shear) and that side's POSITION is t's position in his
#         slug (t == a -> 0), else yn:names-shear; with no event title, his
#         stored moneyline title on the OTHER code (names_resolve: the C5
#         read _C5_TITLES_SQL, bounded, the trades/markets store) names the
#         opponent, else yn:names-unwitnessed;
#   (iii) among the fetched rows the FULL-GAME per-team atc rows on his
#         date whose suffix is the code at the SAME position (never fh/sh/
#         draw/exact) and whose P4 question passes _yn_team_row(anchor=X,
#         opp_witness=other) -- subject X, opponent the other side, his
#         date, the club scope screens, the league-slot rule -- form
#         EXACTLY ONE identifier; two (a same-day twin under another code
#         with the same league phrase) is yn:names-ambiguous; a row naming
#         X against another club, or his opponent against another club, is
#         the venue's pair naming another club: yn:names-mismatch ('Junior
#         FC' is not 'CDP Junior FC' under token-set equality -- by design);
#         none at all: yn:no-row stands;
#   (iv)  _yn_gate: the venue's own side and intent for his Yes/No; then
#         match_side's _yn_line_ok and identity veto as for every arm.
# The draw: the -draw row of that one event (the per-team rows naming his
# two sides at their positions witness the event; a reversed title is
# yn:names-shear) through _yn_draw_row on his event / draw-title sides.
# The hit is matched_by 'premap_names', source 'premap', and carries
# code_pair {"cdh": "hua", "csc": "col"} and witness (the row that named
# him) -- read in this one call, never stored, never reused for another
# date or opponent, never a table. Consulted only after the alias arm's
# yn:no-row and only where C5 has no candidate (one shared code is C5's
# shape: today's reason stands and C5 runs after, as before), inside
# PREMAP_YN_IDENTITY like all of C2/C3/C5.
_C6_UNWITNESSED = "yn:names-unwitnessed"
_C6_SHEAR = "yn:names-shear"
_C6_AMBIGUOUS = "yn:names-ambiguous"
_C6_MISMATCH = "yn:names-mismatch"


def _c5_owns(rows: list[dict], parts: dict, d: str) -> bool:
    """C5's trigger, read purely: his own event is on the board under
    some league code, or an event shares exactly one of his codes in
    position -- either way the names arm stands aside."""
    a, b = parts["a"], parts["b"]
    return any((va, vb) == (a, b) or ((va == a) != (vb == b))
               for (_lg, va, vb) in _c5_venue_events(rows, d))


def _yn_names_rows(rows: list[dict], d: str, pos: int) -> list[tuple[dict, tuple]]:
    """The FULL-GAME per-team atc rows on date d whose suffix is the
    code at position `pos` of their own identifier, with the event head
    (lg, va, vb) each belongs to."""
    out: list[tuple[dict, tuple]] = []
    for r in rows:
        ident = r.get("identifier")
        if not isinstance(ident, str) or not ident.startswith("atc-"):
            continue
        m = _C3_IDENT_RE.match(ident)
        if m is None or m.group("date") != d:
            continue
        va, vb, t = m.group("a"), m.group("b"), m.group("rest") or ""
        if va == vb or t != (va, vb)[pos]:
            continue
        out.append((r, (m.group("lg"), va, vb)))
    return out


def _yn_names_pick(rows: list[dict], parts: dict | None, on: str,
                   want_intent: str, d: str, anchor: str,
                   his_event_title: str | None, trace: dict | None, *,
                   other: str | None = None) -> list[dict]:
    """Step (ii)-(iv) of the block comment above for a per-team slug.
    `other` is the opponent as his stored moneyline title names him
    (names_resolve), read only when his event title names no sides."""
    def _t(**kw):
        if trace is not None:
            trace.update(kw)

    if parts is None or parts["t"] == "draw" or _c5_owns(rows, parts, d):
        _t(refusal="yn:no-row")
        return []
    pos = 0 if parts["t"] == parts["a"] else 1
    sides = _yn_event_sides(his_event_title)
    if sides is not None:
        hit = [s for s in sides if pmus._yn_name_match(s, anchor)]
        if len(hit) != 1:
            _t(refusal="yn:event-shear")
            return []
        if sides.index(hit[0]) != pos:
            _t(refusal=_C6_SHEAR)
            return []
        other_name, src = sides[1 - pos], "event"
    elif other:
        other_name, src = other, "trades"
    else:
        _t(refusal=_C6_UNWITNESSED)
        return []
    cands = _yn_names_rows(rows, d, pos)
    passing: dict[str, tuple] = {}
    named: list[str] = []
    for r, head in cands:
        why = _yn_team_row(r, d=d, anchor=anchor, opp_witness=other_name)
        if why is None:
            passing[r["identifier"]] = head
        elif why == "yn:opp-witness":
            named.append(r["identifier"])          # his club, another opponent
        elif why == "yn:subj":
            gd, _w = _yn_team_question(r.get("question"), d)
            if gd is not None and pmus._yn_name_match(gd["opp"], other_name):
                named.append(r["identifier"])      # his opponent, another club
    # the OTHER position's row is the same pair read in mirror: his
    # opponent as its subject against a club that is not his ('Academico
    # de Viseu FC win against Gil Vicente Barcelos' beside his 'Gil Vicente
    # FC') -- his opponent's name key fetches that row alone when the
    # venue calls his club otherwise, so the mismatch is named from it;
    # it admits nothing
    for r, _head in _yn_names_rows(rows, d, 1 - pos):
        if _yn_team_row(r, d=d, anchor=other_name, opp_witness=anchor) == "yn:opp-witness":
            named.append(r["identifier"])
    if not passing:
        if named:
            _t(refusal=_C6_MISMATCH, venue_rows=sorted(set(named)))
        else:
            _t(refusal="yn:no-row")
        return []
    if len(passing) > 1:
        _t(refusal=_C6_AMBIGUOUS, candidates=sorted(passing))
        return []
    (ident, (lg, va, vb)), = passing.items()
    out, refusal = _yn_gate([r for r, _h in cands if r["identifier"] == ident],
                            on, want_intent,
                            lambda r: _yn_team_row(r, d=d, anchor=anchor,
                                                   opp_witness=other_name))
    if not out:
        _t(refusal=refusal)
        return []
    code_pair = {parts["a"]: va, parts["b"]: vb}
    alias = {"league_alias": f"{parts['lg']}->{lg}"} if lg != parts["lg"] else {}
    _t(matched_by="premap_names", code_pair=code_pair, witness=ident, witness_src=src,
       admitted=frozenset(r["identifier"] for r in out), **alias)
    return [dict(r, yn_branch="names", matched_by="premap_names", code_pair=code_pair,
                 witness=ident, **alias) for r in out]


def _yn_names_draw_pick(rows: list[dict], parts: dict, on: str, want_intent: str,
                        d: str, sides: list[str], witness: str,
                        trace: dict | None) -> list[dict]:
    """The draw of the one event whose per-team rows name his two sides
    at their positions (the block comment above); `sides` are his
    event's (or his draw title's) two clubs in his order."""
    def _t(**kw):
        if trace is not None:
            trace.update(kw)

    if _c5_owns(rows, parts, d):
        _t(refusal="yn:no-row")
        return []
    events: dict[tuple, str] = {}
    swapped = False
    named: list[str] = []
    for pos in (0, 1):
        for r, head in _yn_names_rows(rows, d, pos):
            why = _yn_team_row(r, d=d, anchor=sides[pos], opp_witness=sides[1 - pos])
            if why is None:
                events.setdefault(head, r["identifier"])
            elif _yn_team_row(r, d=d, anchor=sides[1 - pos], opp_witness=sides[pos]) is None:
                swapped = True
            elif why == "yn:opp-witness":
                named.append(r["identifier"])      # his club, another opponent
            elif why == "yn:subj":
                gd, _w = _yn_team_question(r.get("question"), d)
                if gd is not None and pmus._yn_name_match(gd["opp"], sides[1 - pos]):
                    named.append(r["identifier"])  # his opponent, another club
    if not events:
        if swapped:
            _t(refusal=_C6_SHEAR)
        elif named:
            _t(refusal=_C6_MISMATCH, venue_rows=sorted(set(named)))
        else:
            _t(refusal="yn:no-row")
        return []
    if len(events) > 1:
        _t(refusal=_C6_AMBIGUOUS, candidates=sorted(f"{lg}-{va}-{vb}-{d}" for lg, va, vb in events))
        return []
    ((lg, va, vb), witness_id), = events.items()
    draw_id = f"atc-{lg}-{va}-{vb}-{d}-draw"
    cands = [r for r in rows if r.get("identifier") == draw_id]
    if not cands:
        _t(refusal="yn:no-row", event=f"{lg}-{va}-{vb}-{d}")
        return []
    out, refusal = _yn_gate(cands, on, want_intent,
                            lambda r: _yn_draw_row(r, d=d, sides=sides))
    if not out:
        _t(refusal=refusal)
        return []
    code_pair = {parts["a"]: va, parts["b"]: vb}
    alias = {"league_alias": f"{parts['lg']}->{lg}"} if lg != parts["lg"] else {}
    _t(matched_by="premap_names", code_pair=code_pair, witness=witness_id,
       draw_witness=witness, admitted=frozenset(r["identifier"] for r in out), **alias)
    return [dict(r, yn_branch="draw", matched_by="premap_names", code_pair=code_pair,
                 witness=witness_id, draw_witness=witness, **alias) for r in out]


# THE DRAW'S TITLE WITNESS (C3, 2026-09-06). The feed's draw market
# title is 'Will <A> vs. <B> end in a draw?' (c3_rows_2039.log table 2:
# every draw row, 24 of them, that wording and no other), and the
# markets table carried no event title for lal-esp-sev-…-draw ($5.5k,
# refused yn:draw-unwitnessed) or bra-cru-cap-…-draw. That title NAMES
# BOTH SIDES, which is all the witness ever certified: so when no event
# title names two sides, his market title is read -- through exactly
# this template, the attested wording and nothing else (a dated or
# qualified variant is not it and stays unwitnessed, the C2 pin) -- and
# the venue's draw row must then name both of those sides (yn:draw-
# names) as before. matched_by 'premap_draw_title' says which witness
# admitted it.
_YN_DRAW_TITLE_SIDES_RE = re.compile(
    r"^will (?P<a>[a-z0-9 ]+?) vs (?P<b>[a-z0-9 ]+?) end in a draw$")


def _yn_draw_title_sides(his_title: str | None) -> list[str] | None:
    """The two team names of his draw title in the attested wording,
    folded and normalized, each past the club scope screen; else
    None."""
    if not his_title or _folds_away(his_title):
        return None
    n = " ".join(_norm(pmus.fold_latin(his_title)).split())
    m = _YN_DRAW_TITLE_SIDES_RE.fullmatch(n)
    if m is None:
        return None
    sides = [" ".join(m.group("a").split()), " ".join(m.group("b").split())]
    if any(pmus._yn_slot_bad(s) for s in sides):
        return None
    return sides


def _yn_draw_pick(rows: list[dict], parts: dict, on: str, want_intent: str,
                  d: str, his_title: str | None,
                  his_event_title: str | None,
                  trace: dict | None, *,
                  identity_only: bool = False) -> list[dict]:
    def _t(**kw):
        if trace is not None:
            trace.update(kw)

    witness = "event"
    sides = _yn_event_sides(his_event_title)
    if sides is None:
        sides = _yn_draw_title_sides(his_title)
        witness = "title"
    if sides is None:
        _t(refusal="yn:draw-unwitnessed")
        return []
    ok, why = _yn_title_is_his_draw(his_title, d, sides)
    if not ok:
        _t(refusal=f"yn:draw-title-{why}")
        return []
    suffix = f"{parts['a']}-{parts['b']}-{parts['date']}-draw"
    by_code = _yn_rows_by_code(rows, suffix)
    if identity_only:
        # the four-argument identity arm: his own league code only
        by_code = {c: v for c, v in by_code.items() if c == parts["lg"]}
    if not by_code:
        if identity_only:
            _t(refusal="yn:no-row")
            return []
        # C6-N: no -draw row under any code carries his codes; the
        # event's per-team rows naming his two sides are the witness
        return _yn_names_draw_pick(rows, parts, on, want_intent, d, sides,
                                   witness, trace)
    if len(by_code) > 1:
        _t(refusal="yn:league-ambiguous", league_codes=sorted(by_code))
        return []
    (code, cands), = by_code.items()
    out, refusal = _yn_gate(cands, on, want_intent,
                            lambda r: _yn_draw_row(r, d=d, sides=sides))
    if not out:
        _t(refusal=refusal)
        return []
    admitted = frozenset(r["identifier"] for r in out)
    label = "premap_draw_title" if witness == "title" else None
    if code == parts["lg"]:
        _t(matched_by=label or "premap_identity", admitted=admitted,
           draw_witness=witness)
        return [dict(r, yn_branch="draw", draw_witness=witness,
                     **({"matched_by": label} if label else {})) for r in out]
    alias = f"{parts['lg']}->{code}"
    _t(matched_by=label or "premap_alias", league_alias=alias, admitted=admitted,
       draw_witness=witness)
    return [dict(r, yn_branch="draw", league_alias=alias, draw_witness=witness,
                 **({"matched_by": label} if label else {})) for r in out]


# ---------------------------------------------------------------- C5
# A DIFFERING TEAM CODE, TRANSLATED BY THE VENUE'S OWN QUESTION (2026-09-07;
# owner: "map more of his trades", "mirror a larger percentage of RN1's
# positions"; identity-only, fail closed, never on a guess). The venue's
# rows for the Premier League games of 2026-09-06 (render-ops epl-rows,
# 2026-09-07 00:05Z; scratchpad/hard2/epl_rows_0004.log):
#   his    epl-eve-mun-2026-09-06-mun   "Will Manchester United FC win on 2026-09-06?"
#   venue  atc-epl-eve-mnu-2026-09-06-mnu  "Will Manchester United FC win against
#          Everton FC in the Premier League match scheduled for Sep 6, 2026?"
#          atc-epl-eve-mnu-2026-09-06-eve  "Will Everton FC win against Manchester
#          United FC in the Premier League match scheduled for Sep 6, 2026?"
#   his    epl-ars-che-…-che  /  venue  atc-epl-ars-cfc-…-cfc  ("Chelsea FC")
# League code equal, date equal, ONE team code equal in the same position
# (eve, ars), the OTHER differs per CLUB (mun/mnu, che/cfc): the identity
# arm reads yn:no-row (the identifier never matches) and every family of
# the event refuses with it ($91k + $118k in 6 h). The venue's own
# question names his club in his own words; that -- and nothing else --
# certifies the pair (his code -> venue code) FOR THAT EVENT:
#   1. TRIGGER (c5_certify): his slug parses as <lg>-<a>-<b>-<date>-<rest>;
#      among the venue's FULL-GAME per-team moneyline rows on his date
#      (atc-<LG>-<va>-<vb>-<date>-<t>, t in {va, vb}) NO event carries
#      both his codes (his own event on the board belongs to the
#      identity/alias arms, refused or not) and EXACTLY ONE event shares
#      exactly one of his codes IN THE SAME POSITION (home stays home)
#      under his league code -- or, with none under his code, under
#      exactly one other code (the C2 alias, then witnessed on BOTH
#      clubs). Two candidate events, or two league codes:
#      yn:code-translate:ambiguous. Zero: today's reason stands.
#   2. WITNESS: the venue's row for the UNSHARED code fullmatches the P4
#      template on his date (pmus._YN_Q_PATTERNS, the club and league
#      screens as C2), the row for the SHARED code likewise, and the two
#      rows NAME EACH OTHER (subject / opponent, pmus._yn_name_match --
#      C2's normalisation: fold, case, the pinned generic club tokens).
#      Then HIS OWN WORDS must name the unshared club: his title when
#      this market is that club's moneyline (_bridge_title_subject, the
#      C2 title witness), else his moneyline title on that club as
#      trades / markets store it (one bounded read), else the other side
#      of his event title (markets.event_title; trades carries none).
#      Nothing readable: yn:code-translate:unwitnessed. A name that is
#      not the venue's: yn:code-translate:name-mismatch. Both venue rows
#      must be sided yes/no with the venue's intents and carry no line.
#   3. Then his slug is REWRITTEN to the venue's codes and the identity
#      lane runs exactly as today for the family (c5_resolve: the yes/no
#      identity arm, the draw's witness, C3 totals / btts / spreads by
#      identifier segment): no family gets a side rule here. The hit
#      carries code_translated {"mun": "mnu"} and witness (the venue row
#      that named him); the translation lives in this one call and is
#      never stored, so another date or opponent derives its own or
#      none.
# NEVER a table, never a prefix / similarity on codes (mun/mnu, che/cfc
# look alike; that is not evidence). Consulted only after every other
# arm found nothing, inside PREMAP_YN_IDENTITY like all of C2 / C3.
_C5_SLUG_RE = re.compile(
    r"^(?P<lg>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<rest>[a-z0-9-]+)$")
_C5_AMBIGUOUS = "yn:code-translate:ambiguous"
_C5_UNWITNESSED = "yn:code-translate:unwitnessed"
_C5_MISMATCH = "yn:code-translate:name-mismatch"
_C5_TRANSLATED = "yn:code-translated"
_C5_OVERFLOW = "yn:code-translate:titles-overflow"
# his stored moneyline title on one club: the feed's market_title on
# trades, else the markets table's title for the same slug (neither
# table stores an event title on trades; markets.event_title is what
# the mirror hands in as event_title). BOTH LEGS READ THROUGH AN INDEX
# THAT EXISTS (review v2, LOW-2): trades by trades_ts_idx -- his fills
# on a dated game are days old at most, so the read is bounded to the
# last 3 days rather than a sequential scan of the feed's biggest
# table; markets by markets_event_idx on his event slug (<lg>-<a>-<b>-
# <date>, the kindless form the feed stores) with the market slug as
# the filter. No new index: migrate.py runs every file inside a
# transaction (no CONCURRENTLY), and a plain CREATE INDEX on trades
# would hold a SHARE lock against every ingestion INSERT for the whole
# build, on the API's boot path -- see migrations/053's header. THE
# BOUND IS FAIL-CLOSED: LIMIT 9 with no order, and nine distinct rows
# back is yn:code-translate:titles-overflow (a ninth row could be the
# one that disagrees), never eight of them read as agreement.
_C5_TITLES_MAX = 8
_C5_TITLES_SQL = (
    "SELECT market_title AS t FROM trades "
    " WHERE market_slug = $1 AND market_title IS NOT NULL "
    "   AND ts >= now() - interval '3 days' "
    "UNION SELECT title AS t FROM markets "
    " WHERE event_slug = $2 AND slug = $1 AND title IS NOT NULL "
    "LIMIT 9")


def _c5_slug_parts(his_slug: str | None) -> dict | None:
    """<lg>, <a>, <b>, <date>, <rest> of his kindless slug for ANY
    family (a moneyline's rest is a team code; a draw's 'draw'; a C3
    family's 'total-2pt5', 'btts', 'spread-away-1pt5' ...)."""
    m = _C5_SLUG_RE.fullmatch(his_slug or "")
    if m is None or m.group("a") == m.group("b"):
        return None
    return m.groupdict()


def _c5_venue_events(rows: list[dict], d: str) -> dict[tuple, dict[str, list[dict]]]:
    """The venue's FULL-GAME per-team moneyline rows on date d, by event
    (LG, va, vb) and then by the team code the row is for. A half row
    (-fh-mnu), a draw, an exact score never count."""
    out: dict[tuple, dict[str, list[dict]]] = {}
    for r in rows:
        ident = r.get("identifier")
        if not isinstance(ident, str) or not ident.startswith("atc-"):
            continue
        m = _C3_IDENT_RE.match(ident)
        if m is None or m.group("date") != d:
            continue
        va, vb, t = m.group("a"), m.group("b"), m.group("rest") or ""
        if va == vb or t not in (va, vb):
            continue
        out.setdefault((m.group("lg"), va, vb), {}).setdefault(t, []).append(r)
    return out


def _c5_sided(rs: list[dict]) -> str | None:
    """A per-team moneyline market as the venue lists it: a yes row and a
    no row carrying the venue's own intents, one question, no line and
    no sign. The reason it is not, else None."""
    sides = {_norm(r.get("side_norm")) for r in rs}
    if sides != {"yes", "no"}:
        return "venue-sides"
    for r in rs:
        if r.get("intent") != _YN_IDENTITY_INTENT[_norm(r.get("side_norm"))]:
            return "venue-intent"
        if (r.get("line") or "").strip() or (r.get("signed") or "").strip():
            return "venue-lined"
    if len({" ".join(str(r.get("question") or "").split()) for r in rs}) != 1:
        return "venue-question"
    return None


async def _c5_his_club(pool, p: dict, code: str, market_title: str | None,
                       event_title: str | None, his_slug: str | None,
                       other: str) -> tuple[str | None, str]:
    """HIS name for the club his feed codes `code`, and where it was
    read: 'title' (this market is that club's moneyline and his title
    names it), 'trades' (his moneyline title on that club, stored),
    'event' (the side of his event title that is not `other`, the
    venue's name for the other club). (None, why) when nothing he
    wrote names it -- a total's or a draw's matchup title is not read
    here (the brief's rule: a title that names no ONE club is not the
    witness for a code)."""
    if p["rest"] == code:
        anchor, _why = _bridge_title_subject(market_title, his_slug)
        if anchor is not None:
            return anchor, "title"
    ev_slug = f"{p['lg']}-{p['a']}-{p['b']}-{p['date']}"
    ml_slug = f"{ev_slug}-{code}"
    try:
        stored = list(await pool.fetch(_C5_TITLES_SQL, ml_slug, ev_slug))
    except Exception:  # noqa: BLE001 — unreadable is unwitnessed, never a guess
        stored = []
    if len(stored) > _C5_TITLES_MAX:
        # the bound was reached: a title past it could be the one that
        # disagrees, so the store witnesses nothing (review v2, LOW-2)
        return None, "titles-overflow"
    anchors: set[str] = set()
    for r in stored:
        anchor, _why = _bridge_title_subject(r["t"], ml_slug)
        if anchor is not None:
            anchors.add(anchor)
    if len(anchors) == 1:
        return next(iter(anchors)), "trades"
    if anchors:
        return None, "titles-disagree"
    sides = _yn_event_sides(event_title)
    if sides is None:
        return None, "event-title-shape" if event_title else "no-event-title"
    hit = [s for s in sides if pmus._yn_name_match(s, other)]
    if len(hit) != 1:
        return None, "event-title-other-side"
    return (sides[1] if hit[0] is sides[0] else sides[0]), "event"


async def c5_certify(pool, rows: list[dict], market_title: str | None,
                     event_title: str | None, his_slug: str | None,
                     trace: dict) -> dict | None:
    """The translation certified for this event, or None. Steps 1-2 of
    the block comment above; `trace` names the refusal (only once a
    single candidate event was found -- before that today's reason
    stands) or the certification."""
    def _t(**kw):
        trace.update(kw)

    p = _c5_slug_parts(his_slug)
    if p is None:
        return None
    d = p["date"]
    events = _c5_venue_events(rows, d)
    if any((va, vb) == (p["a"], p["b"]) for (_lg, va, vb) in events):
        return None
    cands = {k: v for k, v in events.items() if (k[1] == p["a"]) != (k[2] == p["b"])}
    if not cands:
        return None
    own = {k: v for k, v in cands.items() if k[0] == p["lg"]}
    alias = None
    if own:
        cands = own
    else:
        codes = sorted({k[0] for k in cands})
        if len(codes) > 1:
            _t(refusal=_C5_AMBIGUOUS, league_codes=codes)
            return None
        alias = f"{p['lg']}->{codes[0]}"
    if len(cands) > 1:
        _t(refusal=_C5_AMBIGUOUS, candidates=sorted(f"{lg}-{va}-{vb}-{d}" for lg, va, vb in cands))
        return None
    ((lg, va, vb), by_code), = cands.items()
    pos = 0 if va == p["a"] else 1
    s_code, u_code = (va, vb)[pos], (va, vb)[1 - pos]
    h_code = (p["a"], p["b"])[1 - pos]
    S, U = by_code.get(s_code), by_code.get(u_code)
    if not S or not U:
        # one per-team row alone is no witness (the other never named it):
        # today's reason stands, nothing to trace
        return None
    _t(event=f"{lg}-{va}-{vb}-{d}", shared=s_code, pair=[h_code, u_code])
    for rs in (S, U):
        why = _c5_sided(rs)
        if why:
            _t(refusal=_C5_UNWITNESSED, why=why)
            return None
    qs, why = _yn_team_question(S[0].get("question"), d)
    if qs is None:
        _t(refusal=_C5_UNWITNESSED, why=f"shared:{why}")
        return None
    qu, why = _yn_team_question(U[0].get("question"), d)
    if qu is None:
        _t(refusal=_C5_UNWITNESSED, why=f"unshared:{why}")
        return None
    nm = pmus._yn_name_match
    if not (nm(qu["opp"], qs["subj"]) and nm(qs["opp"], qu["subj"])):
        _t(refusal=_C5_MISMATCH, why="rows-disagree", venue_names=[qs["subj"], qu["subj"]])
        return None
    club, src = await _c5_his_club(pool, p, h_code, market_title, event_title, his_slug,
                                   qs["subj"])
    if club is None:
        _t(refusal=_C5_OVERFLOW if src == "titles-overflow" else _C5_UNWITNESSED, why=src)
        return None
    if not nm(qu["subj"], club):
        # his words name the SHARED row's subject for the unshared code:
        # the venue's two questions are swapped against their identifiers
        # (review v2, observation B) -- named, so the record says which
        _t(refusal=_C5_MISMATCH, his_club=club, venue_club=qu["subj"], witness_src=src,
           **({"why": "rows-swapped"} if nm(qs["subj"], club) else {}))
        return None
    if alias:
        # under another league code the shared club is his only when his
        # words name it too (C2's opponent witness, both clubs)
        club2, src2 = await _c5_his_club(pool, p, s_code, market_title, event_title,
                                         his_slug, qu["subj"])
        if club2 is None:
            _t(refusal=_C5_OVERFLOW if src2 == "titles-overflow" else _C5_UNWITNESSED,
               why=f"alias:{src2}")
            return None
        if not nm(qs["subj"], club2):
            _t(refusal=_C5_MISMATCH, his_club=club2, venue_club=qs["subj"],
               witness_src=src2,
               **({"why": "rows-swapped"} if nm(qu["subj"], club2) else {}))
            return None
    rest = u_code if p["rest"] == h_code else p["rest"]
    cert = {"slug": f"{lg}-{va}-{vb}-{d}-{rest}", "code_translated": {h_code: u_code},
            "witness": str(U[0]["identifier"]), "league_alias": alias}
    # the record: his club as his words state it, the venue's name on the
    # row that named him (venue_club) AND on the shared row (shared_club),
    # so a pair whose two questions are swapped reads as such even where
    # only his event title witnessed it (nothing of his binds a code to a
    # name there; the family lanes read names on their own rows)
    _t(certified=True, code_translated=cert["code_translated"], witness=cert["witness"],
       witness_src=src, his_club=club, venue_club=qu["subj"], shared_club=qs["subj"],
       slug=cert["slug"], **({"league_alias": alias} if alias else {}))
    return cert


async def c5_resolve(pool, rows: list[dict], kept: list[dict], outcome: str | None,
                     market_title: str | None, event_title: str | None,
                     his_slug: str | None, trace: dict, *,
                     his_c3: dict | None, wording: bool) -> dict | None:
    """Step 3: the identity lane exactly as resolve runs it, on the
    rewritten slug -- the same match_side / c3_pick calls on the same
    prefix- and segment-kept rows. The hit (a COPY) carries the lane's
    own matched_by, code_translated and witness; a lane refusal on the
    rewritten slug is named in `trace` as the lane names it."""
    cert = await c5_certify(pool, rows, market_title, event_title, his_slug, trace)
    if cert is None:
        return None
    slug = cert["slug"]
    hit = None
    label = None
    if wording:
        hit = match_side(kept, outcome, market_title, slug,
                         yn_identity=True, his_event_title=event_title)
        if hit is not None:
            label = hit.get("matched_by") or (
                "premap_alias" if hit.get("league_alias") else "premap_identity")
        elif _norm(outcome) in ("yes", "no"):
            lane: dict = {}
            _yn_pick(kept, outcome, market_title, slug, event_title, lane)
            trace["lane"] = lane
            trace["refusal"] = lane.get("refusal") or (
                "yn:picked-vetoed" if lane.get("matched_by") else "yn:no-row")
    if hit is None and his_c3 is not None:
        lane = {}
        # the same C3 call resolve makes (C4: a spread also sees the
        # event's aec row and the grammar class's certification state)
        gcert = await grammar_cert(pool) if his_c3["family"] == "spread" else None
        hit = c3_pick(kept, outcome, market_title, slug, lane, board=rows, cert=gcert)
        trace["lane"] = lane
        if hit is not None:
            label = hit["matched_by"]
            trace.pop("refusal", None)
        elif lane.get("refusal"):
            trace["refusal"] = lane["refusal"]
    if hit is None:
        return None
    out = dict(hit, matched_by=label, code_translated=cert["code_translated"],
               witness=cert["witness"])
    if cert["league_alias"] and not out.get("league_alias"):
        out["league_alias"] = cert["league_alias"]
    trace.update(label=_C5_TRANSLATED, matched_by=label, admitted=out.get("identifier"))
    return out


async def names_resolve(pool, rows: list[dict], kept: list[dict], outcome: str | None,
                        market_title: str | None, event_title: str | None,
                        his_slug: str | None, trace: dict, *, wording: bool) -> dict | None:
    """C6-N's stored-title witness (step ii of the block comment at
    _yn_names_pick): when no event title of his names two sides, his
    moneyline title on the OTHER code -- as trades / markets store it,
    the bounded C5 read -- names his opponent, and the identity lane's
    yes/no call runs again with that one name as the witness. Read only
    once the pure arm said yn:names-unwitnessed; an unreadable, empty,
    disagreeing or overflowing store witnesses nothing."""
    if not wording or _norm(outcome) not in ("yes", "no"):
        return None
    parts = _yn_slug_parts(his_slug)
    if parts is None or parts["t"] == "draw" or _yn_event_sides(event_title) is not None:
        return None
    pure: dict = {}
    _yn_pick(kept, outcome, market_title, his_slug, event_title, pure)
    if pure.get("refusal") != _C6_UNWITNESSED:
        return None
    other_code = parts["b"] if parts["t"] == parts["a"] else parts["a"]
    ev_slug = f"{parts['lg']}-{parts['a']}-{parts['b']}-{parts['date']}"
    ml_slug = f"{ev_slug}-{other_code}"
    try:
        stored = list(await pool.fetch(_C5_TITLES_SQL, ml_slug, ev_slug))
    except Exception:  # noqa: BLE001 — unreadable is unwitnessed, never a guess
        stored = []
    if len(stored) > _C5_TITLES_MAX:
        trace.update(refusal=_C6_UNWITNESSED, why="titles-overflow")
        return None
    anchors: set[str] = set()
    for r in stored:
        anchor, _why = _bridge_title_subject(r["t"], ml_slug)
        if anchor is not None:
            anchors.add(anchor)
    if len(anchors) != 1:
        trace.update(refusal=_C6_UNWITNESSED,
                     why="titles-disagree" if anchors else "no-stored-title")
        return None
    other = next(iter(anchors))
    lane: dict = {}
    _yn_pick(kept, outcome, market_title, his_slug, event_title, lane, names_witness=other)
    hit = match_side(kept, outcome, market_title, his_slug, yn_identity=True,
                     his_event_title=event_title, names_witness=other)
    trace.update(lane=lane, witness_src="trades", other=other)
    if hit is None:
        trace["refusal"] = lane.get("refusal") or (
            "yn:picked-vetoed" if lane.get("matched_by") else "yn:no-row")
        return None
    trace.update(matched_by=hit.get("matched_by"), code_pair=hit.get("code_pair"),
                 witness=hit.get("witness"), admitted=hit.get("identifier"))
    return hit


# ---------------------------------------------------------------- C3
# THE DERIVATIVE FAMILIES BY IDENTITY (2026-09-06; owner 19:33Z: "I need
# more trades firing in the mirror sleeve"). His last 3 h refused $20k
# of spreads (no_side_match: the asc rows carried no side), $23k of
# first-half totals (typed prop, no prefix), $10k of btts (his title is
# not a win question) -- docs/mirror-coverage.md §9. Each family below
# is admitted by IDENTITY, nothing else: the venue identifier is BUILT
# from his slug's league (or the C2 alias: exactly one venue league code
# carrying his <a>-<b>-<date> suffix), team codes, date, SEGMENT and
# line, byte for byte after the pt5 fold; the venue's question is then
# parsed by its attested grammar and must name his teams (his title's
# two sides, or -- for a total, whose question names only the codes --
# the identifier's own codes); his title must be the family's attested
# wording on the same segment and line; the row's side_norm and intent
# must be the venue's own for the side the table below names. No
# similarity score, no nearest line, no home/away position: a team is a
# or b only through its NAME in the venue's question. Every refusal is
# named for the census ('spread:line-absent' …).
#
#   spreads  his <lg>-<a>-<b>-<date>-spread-(home|away)-<L>, title
#            'Spread: <Team> (-L)', outcome <his team>; venue
#            asc-<LG>-<a>-<b>-<date>[-<seg>]-(neg|pos)-<L> with yes/no
#            sides, question 'Will the <A> cover -L vs the <B> in <A> vs.
#            <B>?' -- a (the first slug team) is ALWAYS the subject.
#            His signed line is the title's when the title names his
#            outcome, else its negation. Then:
#              his team is a, -L  -> neg-L  yes  BUY_LONG
#              his team is a, +L  -> pos-L  yes  BUY_LONG
#              his team is b, -L  -> pos-L  no   BUY_SHORT  (a covers +L
#                                    is the complement of b covering -L)
#              his team is b, +L  -> neg-L  no   BUY_SHORT
#            Whole-number lines refuse (push lines are not listed).
#   totals   his …-total-<L> / …-first-half-total-<L>, title '<A> vs.
#            <B>: O/U L' / '…: 1st Half O/U L', outcome Over/Under;
#            venue tsc-<LG>-<a>-<b>-<date>[-fh|-sh]-<L>, over/under
#            sides, question 'Will the total in <A> vs <B> be more than
#            L?' (identical across segments: the segment lives ONLY in
#            the identifier, so his segment maps to that segment and
#            nothing else).
#   btts     his …-btts (…-first-half-btts), title '<A> vs. <B>: Both
#            Teams to Score', outcome Yes/No; venue astatc-<LG>-<a>-<b>-
#            <date>-btts / -fh-btts, question 'Will both teams score in
#            the match|first half|second half between <A> and <B> on
#            <date> …' -- the segment word must agree with the
#            identifier's, the date with his.
# Not on the venue tonight (c3_rows_2041.log lists none for these
# events): exact score, halftime result, team totals -- refused by
# family name, never mapped to a neighbour.
C3_FAMILIES = frozenset({"spread", "total", "btts"})
C3_PREFIX = {"spread": "asc", "total": "tsc", "btts": "astatc"}
C3_ABSENT = {"exact_score": "exact:family-absent",
             "halftime_result": "halftime:family-absent",
             "team_total": "team_total:family-absent"}
_C3_SEGMENTS = frozenset({"fh", "sh", "1h", "2h", "1q", "2q", "3q", "4q", "ht"})
_C3_SEG_TITLE = {"fh": ("1st half", "first half"),
                 "sh": ("2nd half", "second half"),
                 "ht": ("halftime",)}
_C3_IDENT_RE = re.compile(
    r"^(?P<kind>[a-z]+)-(?P<lg>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<rest>[a-z0-9-]+))?$")
_C3_SPREAD_TITLE_RE = re.compile(
    r"^\s*(?:(?P<seg>1st half|first half|2nd half|second half)\s+)?spread\s*:\s*"
    r"(?P<team>.+?)\s*\(\s*(?P<sign>[+-])\s*(?P<line>\d+(?:\.\d+)?)\s*\)\s*$", re.I)
_C3_ASC_Q_RE = re.compile(
    r"^\s*will the (?P<a>.+?) cover (?P<sign>[+-]?)(?P<line>\d+(?:\.\d+)?) vs the "
    r"(?P<b>.+?) in (?P<a2>.+?) vs\.? (?P<b2>.+?)\s*\?\s*$", re.I)
_C3_TSC_Q_RE = re.compile(
    r"^\s*will the total in (?P<a>.+?) vs\.? (?P<b>.+?) be more than "
    r"(?P<line>\d+(?:\.\d+)?)\s*\?\s*$", re.I)
_C3_BTTS_Q_RE = re.compile(
    r"^\s*will both teams score in the (?P<seg>match|first half|second half) between "
    r"(?P<a>.+?) and (?P<b>.+?) on (?P<date>\d{4}-\d{2}-\d{2})\b", re.I)
_C3_BTTS_SEG = {"match": "", "first half": "fh", "second half": "sh"}


def _c3_line_tok(line: str) -> str:
    """'19.5' -> '19pt5', '2' -> '2': the slug spelling of a line."""
    return line.replace(".", "pt")


def c3_his(his_slug: str | None) -> dict | None:
    """His slug read as a C3 family: {family, seg, lg, a, b, date, line,
    side} or None when it is not one (the caller keeps the old path).
    `line` is canonical ('19.5'); spreads carry `side` home/away as the
    feed wrote it (never read as a position -- his team is his outcome)."""
    from ..copy_sports import family_of, segment_of

    s = (his_slug or "").lower()
    fam = family_of(s)
    if fam not in C3_FAMILIES:
        return None
    m = _C3_IDENT_RE.match("x-" + s)
    if m is None or m.group("a") == m.group("b"):
        return None
    seg = segment_of(s)
    rest = [t for t in (m.group("rest") or "").split("-") if t]
    if seg == "maps":
        # E2 (2026-09-07): the feed's total of maps, 'tot-<L>' -- the
        # total family on the segment 'maps', which no venue identifier
        # carries as a segment token, so c3_same_segment empties the pool
        # and the pick refuses total:segment-absent by name; the venue's
        # tsc-…-tot-<L> rows (esports_chi_rows_1403.log) are a lane
        # nobody has built (docs §17), never the wording arm's
        if len(rest) != 2 or rest[0] != "tot":
            return None
        rest = ["total", rest[1]]
    elif seg:
        rest = rest[2:] if rest[:1] in (["first"], ["second"], ["1st"], ["2nd"]) else rest[1:]
    out = {"family": fam, "seg": seg, "lg": m.group("lg"), "a": m.group("a"),
           "b": m.group("b"), "date": m.group("date"), "line": "", "side": ""}
    if fam == "btts":
        if rest != ["btts"]:
            return None
        return out
    lm = _SLUG_WHOLE_RE.match(rest[-1] if rest else "")
    if lm is None or lm.group(1):
        return None
    out["line"] = _canon_line(f"{int(lm.group(2))}.{lm.group(3)}" if lm.group(3)
                              else str(int(lm.group(2))))
    if fam == "spread":
        if len(rest) != 3 or rest[0] != "spread" or rest[1] not in ("home", "away"):
            return None
        out["side"] = rest[1]
    elif len(rest) != 2 or rest[0] != "total":
        return None
    return out


def _venue_segment(identifier: str | None) -> str | None:
    """The segment token a venue identifier carries right after its
    date ('fh', 'sh', '1h', '1q' …), '' for the full game, None when the
    identifier is not the <kind>-<lg>-<a>-<b>-<date>… shape."""
    m = _C3_IDENT_RE.match(str(identifier or "").lower())
    if m is None:
        return None
    rest = [t for t in (m.group("rest") or "").split("-") if t]
    return rest[0] if rest and rest[0] in _C3_SEGMENTS else ""


def c3_same_segment(rows: list[dict], seg: str) -> list[dict]:
    """Only the rows on HIS segment (a row whose identifier does not
    parse is kept: the legacy shapes the older tests build). A
    segmentless question never matches across segments (the venue's
    total questions are identical on fh/sh/full)."""
    return [r for r in rows if _venue_segment(r.get("identifier")) in (seg, None)]


def _c3_norm(s: str | None) -> str:
    return " ".join(_norm(pmus.fold_latin(str(s or ""))).split())


def _c3_title_parts(his_title: str | None) -> tuple[list[str] | None, str]:
    """(the two sides of his '<A> vs. <B>: <qualifier>' title, the
    normalized qualifier) -- (None, '') when the title is not that
    shape or a side fails the club scope screen."""
    raw = str(his_title or "")
    if not raw or _folds_away(raw):
        return None, ""
    parts = raw.split(":")
    vs = [p for p in parts if re.search(r"\s+vs\.?\s+", p, re.I)]
    if len(vs) != 1:
        return None, ""
    matchup = vs[0]
    qual = " ".join(_c3_norm(p) for p in parts if p is not matchup).strip()
    sides = [_c3_norm(x) for x in re.split(r"\s+vs\.?\s+", matchup.strip(), flags=re.I)]
    if len(sides) != 2 or any(pmus._yn_slot_bad(x) for x in sides):
        return None, ""
    return sides, qual


def _c3_names_agree(venue_a: str, venue_b: str, his: list[str]) -> bool:
    nm = pmus._yn_name_match
    return ((nm(venue_a, his[0]) and nm(venue_b, his[1]))
            or (nm(venue_a, his[1]) and nm(venue_b, his[0])))


def _c3_rows_by_code(rows: list[dict], kind: str, suffix: str) -> dict[str, list]:
    """Rows whose identifier is '<kind>-<code>-<suffix>', by code."""
    by_code: dict[str, list] = {}
    head, tail = kind + "-", "-" + suffix
    for r in rows:
        ident = r.get("identifier")
        if not isinstance(ident, str) or not ident.startswith(head) or not ident.endswith(tail):
            continue
        code = ident[len(head):-len(tail)]
        if code and "-" not in code:
            by_code.setdefault(code, []).append(r)
    return by_code


def _c3_code(rows: list[dict], his: dict, suffixes: list[str], fam: str,
             trace: dict) -> tuple[str | None, list[dict]]:
    """The venue league code for his family rows: his own when any row
    carries it, else the ONE other code carrying his suffix (the C2
    alias rule); two is ambiguous, none is absent."""
    by_code: dict[str, list] = {}
    for sfx in suffixes:
        for code, rs in _c3_rows_by_code(rows, C3_PREFIX[fam], sfx).items():
            by_code.setdefault(code, []).extend(rs)
    if not by_code:
        return None, []
    if his["lg"] in by_code:
        return his["lg"], by_code[his["lg"]]
    if len(by_code) > 1:
        trace["refusal"] = f"{fam}:league-ambiguous"
        trace["league_codes"] = sorted(by_code)
        return None, []
    (code, rs), = by_code.items()
    trace["league_alias"] = f"{his['lg']}->{code}"
    return code, rs


def _c3_seg_id(his: dict, code: str) -> str:
    seg = f"-{his['seg']}" if his["seg"] else ""
    return f"{C3_PREFIX[his['family']]}-{code}-{his['a']}-{his['b']}-{his['date']}{seg}"


# ---------------------------------------------------------------- C4
# THE MASCOT SPREAD (2026-09-06 22:2xZ; owner 19:33Z "more trades firing").
# The venue names the SAME college game by mascots on some asc rows
# ('Will the Cougars cover -24.5 vs the Huskies in Cougars vs. Huskies?')
# and by schools on others (the 1h/1q rows: 'Will the Washington State
# cover 3.5 vs the Washington …'); his feed names schools ('Washington
# State', 'Spread: Washington (-23.5)'). C3's subject step -- his team
# is a or b ONLY by name in the question -- reads nothing on a mascot
# row ($7.5k + $3.8k + $1.3k + $0.3k + $0.1k refused
# spread:names-unreadable in the two hours to 22:21Z). The identity
# chain that replaces the name on those rows, and nothing else:
#   1. the codes: his slug and the venue's identifier share the team
#      codes byte for byte (cfb-washst-wash on both); a = washst, b =
#      wash; the C3 key (league, a, b, date, segment, line) is unchanged;
#   2. his outcome -> a or b by the CODE (map_lane.code_reads: the C1
#      whole-word / first-word rule, the C3 stem rule -- 'Washington
#      State' names washst by wash + st, 'Washington' names wash -- and
#      the C4 multi-word split, 'South Carolina State' -> scarst); his
#      title's team the same way. Neither, or both: spread:code-unmatched
#      (the title: spread:title-unreadable). THE PAIR WITNESS (review
#      fold, 2026-09-06): the two names must resolve to the two DIFFERENT
#      positions -- exactly C1's pair rule, where an unnamed sibling
#      corroborates nothing. Two different names on ONE code ('Ole Miss'
#      and 'Mississippi State' both name miss by the first-word rule,
#      msst is named by nothing) is spread:code-collision; a title naming
#      his outcome's own team ('Spread: Washington State (-23.5)' /
#      'Washington State') carries no second name and is
#      spread:pair-unwitnessed -- no single name can exclude the
#      collision ('Mississippi State' alone reads miss, and would buy the
#      Rebels' side), so no map;
#   3. the SUBJECT: C3's grammar has a in the subject slot; on a mascot
#      row that is a position, and a position is certified only by the
#      venue's own rows on the same event -- the moneyline row
#      aec-<lg>-<a>-<b>-<date> must list exactly the question's two
#      names as its sides, and the C1 grammar class must hold a VENUE-
#      TRUTH record for that aec market (mirror_grammar_echo.certified:
#      the venue's per-side contract for the code named the mascot the
#      code rule chose, docs §7.7 step 1) placing the subject's mascot at
#      the code's position. No record, no aec row, other names, the
#      class unreadable or tripped: spread:subject-uncertified; a record
#      placing the subject at the OTHER position: spread:subject-conflict.
#   4. C3's table unchanged (a with -L -> neg-L yes; b with -L -> pos-L
#      no …), line byte-equal, segment never crosses, whole numbers
#      refuse. matched_by premap_spread_code; source premap; behind
#      PREMAP_YN_IDENTITY with the rest of C3.
# Never a nickname table, never a similarity, never home/away.
GRAMMAR_CERT_KEY = "mirror_grammar_echo"


async def grammar_cert(pool) -> dict | None:
    """The C1 grammar class's own state key, read for the C4 subject
    step: the dict (possibly empty: the key absent) or None when the
    read failed or the value is not a dict -- every reader refuses."""
    import json as _json

    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1 /* premap-c4-cert */",
            GRAMMAR_CERT_KEY)
    except Exception:  # noqa: BLE001 — unreadable is a refusal, never a guess
        return None
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _c4_subject_certified(his: dict, code: str, names: tuple[str, str],
                          board: list[dict], cert: dict | None, trace: dict) -> str | None:
    """Step 3 of the chain: the refusal name, or None when the venue's
    own rows on this event certify that the question's subject (names[0])
    is slug team a. Pure: `board` is the event's rows, `cert` the class
    state as read by the caller."""
    from .. import map_lane

    aec_id = f"aec-{code}-{his['a']}-{his['b']}-{his['date']}"
    aec = [r for r in board if str(r.get("identifier") or "").lower() == aec_id]
    sides = sorted({_c3_norm(r.get("side_norm")) for r in aec})
    trace["aec"] = {"identifier": aec_id, "sides": sides}
    if not aec:
        trace["subject"] = "aec-absent"
        return "spread:subject-uncertified"
    if len(sides) != 2 or sides != sorted(names):
        trace["subject"] = "aec-names"
        return "spread:subject-uncertified"
    if cert is None:
        trace["subject"] = "grammar-unreadable"
        return "spread:subject-uncertified"
    if cert.get("tripped") or int(cert.get("mismatch") or 0) > 0:
        trace["subject"] = "grammar-tripped"
        return "spread:subject-uncertified"
    rec = (cert.get("certified") or {}).get(aec_id) if isinstance(cert.get("certified"), dict) else None
    if not isinstance(rec, dict):
        trace["subject"] = "grammar-uncertified"
        return "spread:subject-uncertified"
    desc = _c3_norm(rec.get("outcome_desc"))
    i = rec.get("side_index")
    head = map_lane.slug_head(rec.get("his_slug"))
    if (i not in (0, 1) or desc not in names or head is None
            or (head[1], head[2], head[3]) != (his["a"], his["b"], his["date"])):
        trace["subject"] = "record-shape"
        return "spread:subject-uncertified"
    j = names.index(desc)            # 0: the question's subject, 1: its opponent
    if j != i:
        # the venue certified this mascot as the OTHER position's team:
        # the question's subject is b, which the grammar never has
        trace["subject"] = {"certified": desc, "code": (his["a"], his["b"])[i], "slot": j}
        return "spread:subject-conflict"
    trace["subject"] = {"certified": desc, "code": (his["a"], his["b"])[i]}
    return None


def _c4_subject_by_code(his: dict, code: str, names: tuple[str, str], on: str,
                        title_team: str, board: list[dict], cert: dict | None,
                        trace: dict) -> tuple[int, int] | None:
    """Steps 2 and 3: (his team's position, his title's team's position)
    or None with the refusal named. Runs only when neither venue name is
    his outcome by name; a venue name that names a code by the code
    rules was the authority, so C3's own refusal stands there. The two
    positions are always DIFFERENT (the pair witness): a map here is
    never on one name's reading of a code."""
    from .. import map_lane

    a, b = his["a"], his["b"]
    trace["venue_names"] = list(names)
    if any(map_lane.code_reads(c, n, s) for n in names for c, s in ((a, b), (b, a))):
        trace["refusal"] = "spread:names-unreadable"
        return None
    pos = [i for i, c in enumerate((a, b)) if map_lane.code_reads(c, on, (b, a)[i])]
    trace["code_hits"] = [(a, b)[i] for i in pos]
    if len(pos) != 1:
        trace["refusal"] = "spread:code-unmatched"
        return None
    tpos = [i for i, c in enumerate((a, b)) if map_lane.code_reads(c, title_team, (b, a)[i])]
    trace["title_hits"] = [(a, b)[i] for i in tpos]
    if len(tpos) != 1:
        trace["refusal"] = "spread:title-unreadable"
        return None
    if pos[0] == tpos[0]:
        # THE PAIR WITNESS (review fold, 2026-09-06, HIGH-1): the code
        # rules read each name alone, and a first-word prefix names one
        # code for two schools ('Ole Miss' and 'Mississippi State' both
        # read miss; msst is named by nothing) -- the sign was not
        # flipped and the wrong side bought. Two different names on one
        # position is the collision itself; one name (his title names
        # his outcome's team) is no witness at all: C1 maps a moneyline
        # only when the sibling names the OTHER code, and this chain
        # holds the same bar. Refused before any certification is read.
        trace["refusal"] = ("spread:code-collision" if on != title_team
                            else "spread:pair-unwitnessed")
        return None
    why = _c4_subject_certified(his, code, names, board, cert, trace)
    if why:
        trace["refusal"] = why
        return None
    return pos[0], tpos[0]


def _c3_pick_spread(rows: list[dict], his: dict, outcome: str | None,
                    his_title: str | None, trace: dict, board: list[dict] | tuple = (),
                    cert: dict | None = None) -> dict | None:
    on = _c3_norm(outcome)
    if not on or on in ("yes", "no"):
        trace["refusal"] = "spread:outcome"
        return None
    if "." not in his["line"]:
        trace["refusal"] = "spread:whole-number"
        return None
    tm = _C3_SPREAD_TITLE_RE.match(str(his_title or ""))
    if tm is None or _folds_away(his_title):
        trace["refusal"] = "spread:title-shape"
        return None
    if _canon_line(tm.group("line")) != his["line"]:
        trace["refusal"] = "spread:title-shear"
        return None
    tseg = {"1st half": "fh", "first half": "fh", "2nd half": "sh",
            "second half": "sh"}.get((tm.group("seg") or "").lower(), "")
    if tseg != his["seg"]:
        trace["refusal"] = "spread:title-segment"
        return None
    title_team = _c3_norm(tm.group("team"))
    ltok = _c3_line_tok(his["line"])
    seg = f"-{his['seg']}" if his["seg"] else ""
    suffixes = [f"{his['a']}-{his['b']}-{his['date']}{seg}-{sg}-{ltok}" for sg in ("neg", "pos")]
    code, cands = _c3_code(rows, his, suffixes, "spread", trace)
    if code is None:
        trace.setdefault("refusal", "spread:line-absent")
        return None
    # the names, from the venue's question on his line
    names = None
    for r in cands:
        qm = _C3_ASC_Q_RE.match(pmus.fold_latin(str(r.get("question") or "")))
        if qm is None:
            continue
        qa, qb = _c3_norm(qm.group("a")), _c3_norm(qm.group("b"))
        # the trailing matchup restates the subjects: by name ('… in
        # Bulldogs vs. Rattlers?') or by the identifier's own codes
        # ('… in JUV vs MIL?'); anything else is not this grammar
        tail = (_c3_norm(qm.group("a2")), _c3_norm(qm.group("b2")))
        if tail not in ((qa, qb), (his["a"], his["b"])):
            continue
        if _canon_line(qm.group("line")) != his["line"]:
            continue
        names = (qa, qb)
        break
    if names is None:
        trace["refusal"] = "spread:question-shape"
        return None
    nm = pmus._yn_name_match
    mine = [i for i, n in enumerate(names) if nm(n, on)]
    label = "premap_spread"
    if len(mine) > 1:
        trace["refusal"] = "spread:names-ambiguous"
        trace["venue_names"] = list(names)
        return None
    if mine:
        titled = [i for i, n in enumerate(names) if nm(n, title_team)]
        if len(titled) != 1:
            trace["refusal"] = "spread:title-unreadable"
            trace["venue_names"] = list(names)
            return None
    else:
        # C4: the question names neither team his outcome names (the
        # mascot rows) -- the code chain, certified by the venue's own
        # rows on this event and the grammar class's venue-truth record
        by_code = _c4_subject_by_code(his, code, names, on, title_team, list(board), cert, trace)
        if by_code is None:
            return None
        mine, titled, label = [by_code[0]], [by_code[1]], "premap_spread_code"
    trace["subject_via"] = "code" if label == "premap_spread_code" else "name"
    gives = tm.group("sign") == "-"
    if titled[0] != mine[0]:
        gives = not gives            # the title's sign is the other team's
    # the table: a is the question's subject
    if mine[0] == 0:
        sign_tok, side = ("neg" if gives else "pos"), "yes"
    else:
        sign_tok, side = ("pos" if gives else "neg"), "no"
    want_id = f"{_c3_seg_id(his, code)}-{sign_tok}-{ltok}"
    want_intent = _YN_IDENTITY_INTENT[side]
    hits = [r for r in cands if r.get("identifier") == want_id
            and _norm(r.get("side_norm")) == side]
    if not hits:
        trace["refusal"] = "spread:line-absent"
        trace["wanted"] = want_id
        return None
    if len(hits) > 1:
        trace["refusal"] = "spread:ambiguous"
        return None
    r = hits[0]
    if r.get("intent") != want_intent:
        trace["refusal"] = "spread:intent"
        return None
    if _canon_line(str(r.get("line") or "")) != his["line"]:
        trace["refusal"] = "spread:line-shear"
        return None
    qm = _C3_ASC_Q_RE.match(pmus.fold_latin(str(r.get("question") or "")))
    if qm is None or (qm.group("sign") == "-") != (sign_tok == "neg") \
            or _canon_line(qm.group("line")) != his["line"]:
        trace["refusal"] = "spread:sign-shear"
        return None
    trace.update(matched_by=label, admitted=want_id, side=side,
                 his_team=("a", "b")[mine[0]], his_signed=("-" if gives else "+") + his["line"])
    return dict(r, matched_by=label,
                **({"league_alias": trace["league_alias"]} if trace.get("league_alias") else {}))


def _c3_pick_total(rows: list[dict], his: dict, outcome: str | None,
                   his_title: str | None, trace: dict) -> dict | None:
    on = _c3_norm(outcome)
    if on not in ("over", "under"):
        trace["refusal"] = "total:outcome"
        return None
    sides, qual = _c3_title_parts(his_title)
    if sides is None:
        trace["refusal"] = "total:title-shape"
        return None
    line_n = _c3_norm(his["line"])
    want_q = {f"{w} o u {line_n}" for w in _C3_SEG_TITLE.get(his["seg"], ())} \
        if his["seg"] else {f"o u {line_n}"}
    if qual not in want_q:
        trace["refusal"] = "total:title-shear"
        return None
    ltok = _c3_line_tok(his["line"])
    seg = f"-{his['seg']}" if his["seg"] else ""
    code, cands = _c3_code(rows, his, [f"{his['a']}-{his['b']}-{his['date']}{seg}-{ltok}"],
                           "total", trace)
    if code is None:
        trace.setdefault("refusal", "total:line-absent")
        return None
    want_id = f"{_c3_seg_id(his, code)}-{ltok}"
    hits = [r for r in cands if r.get("identifier") == want_id
            and (r.get("side_norm") or "").split()[:1] == [on]]
    if not hits:
        trace["refusal"] = "total:side"
        return None
    if len(hits) > 1:
        trace["refusal"] = "total:ambiguous"
        return None
    r = hits[0]
    if _canon_line(str(r.get("line") or "")) != his["line"]:
        trace["refusal"] = "total:line-shear"
        return None
    qm = _C3_TSC_Q_RE.match(pmus.fold_latin(str(r.get("question") or "")))
    if qm is None or _canon_line(qm.group("line")) != his["line"]:
        trace["refusal"] = "total:question-shape"
        return None
    qa, qb = _c3_norm(qm.group("a")), _c3_norm(qm.group("b"))
    if not (_c3_names_agree(qa, qb, sides) or (qa, qb) == (his["a"], his["b"])):
        trace["refusal"] = "total:names"
        trace["venue_names"] = [qa, qb]
        return None
    want_intent = "ORDER_INTENT_BUY_LONG" if on == "over" else "ORDER_INTENT_BUY_SHORT"
    if r.get("intent") != want_intent:
        trace["refusal"] = "total:intent"
        return None
    label = f"premap_{his['seg']}_total" if his["seg"] else "premap_total"
    trace.update(matched_by=label, admitted=want_id, side=on)
    return dict(r, matched_by=label,
                **({"league_alias": trace["league_alias"]} if trace.get("league_alias") else {}))


def _c3_pick_btts(rows: list[dict], his: dict, outcome: str | None,
                  his_title: str | None, trace: dict) -> dict | None:
    on = _norm(outcome)
    want_intent = _YN_IDENTITY_INTENT.get(on)
    if not want_intent:
        trace["refusal"] = "btts:outcome"
        return None
    sides, qual = _c3_title_parts(his_title)
    if sides is None:
        trace["refusal"] = "btts:title-shape"
        return None
    want_q = {f"{w} both teams to score" for w in _C3_SEG_TITLE.get(his["seg"], ())} \
        if his["seg"] else {"both teams to score"}
    if qual not in want_q:
        trace["refusal"] = "btts:title-shear"
        return None
    seg = f"-{his['seg']}" if his["seg"] else ""
    code, cands = _c3_code(rows, his, [f"{his['a']}-{his['b']}-{his['date']}{seg}-btts"],
                           "btts", trace)
    if code is None:
        trace.setdefault("refusal", "btts:no-row")
        return None
    want_id = f"{_c3_seg_id(his, code)}-btts"
    hits = [r for r in cands if r.get("identifier") == want_id
            and _norm(r.get("side_norm")) == on]
    if not hits:
        trace["refusal"] = "btts:side"
        return None
    if len(hits) > 1:
        trace["refusal"] = "btts:ambiguous"
        return None
    r = hits[0]
    if r.get("intent") != want_intent:
        trace["refusal"] = "btts:intent"
        return None
    qm = _C3_BTTS_Q_RE.match(pmus.fold_latin(str(r.get("question") or "")))
    if qm is None:
        trace["refusal"] = "btts:question-shape"
        return None
    if _C3_BTTS_SEG.get(qm.group("seg").lower()) != his["seg"]:
        trace["refusal"] = "btts:segment-shear"
        return None
    if qm.group("date") != his["date"]:
        trace["refusal"] = "btts:qdate"
        return None
    qa, qb = _c3_norm(qm.group("a")), _c3_norm(qm.group("b"))
    if not _c3_names_agree(qa, qb, sides):
        trace["refusal"] = "btts:names"
        trace["venue_names"] = [qa, qb]
        return None
    label = f"premap_{his['seg']}_btts" if his["seg"] else "premap_btts"
    trace.update(matched_by=label, admitted=want_id, side=on)
    return dict(r, matched_by=label,
                **({"league_alias": trace["league_alias"]} if trace.get("league_alias") else {}))


def c3_pick(rows: list[dict], outcome: str | None, his_title: str | None,
            his_slug: str | None, trace: dict | None = None, *,
            board: list[dict] | tuple = (), cert: dict | None = None) -> dict | None:
    """The C3 families' identity pick over the prefix-kept rows: the
    matched row (a COPY carrying matched_by and, under an alias,
    league_alias) or None with the refusal named in `trace`. Pure.
    C4: `board` is the event's unfiltered rows (the aec moneyline row a
    mascot spread's subject step reads) and `cert` the grammar class's
    state as the caller read it; a spread whose question names mascots
    refuses spread:subject-uncertified without both."""
    t: dict = {} if trace is None else trace
    his = c3_his(his_slug)
    if his is None:
        t["refusal"] = "c3:not-a-family"
        return None
    t["family"], t["segment"] = his["family"], his["seg"]
    rows = c3_same_segment(rows, his["seg"])
    if his["seg"] and not rows:
        t["refusal"] = f"{his['family']}:segment-absent"
        return None
    if his["family"] == "spread":
        return _c3_pick_spread(rows, his, outcome, his_title, t, board, cert)
    if his["family"] == "total":
        return _c3_pick_total(rows, his, outcome, his_title, t)
    return _c3_pick_btts(rows, his, outcome, his_title, t)


# ---------------------------------------------------------------- E2
# THE MAP WINNER (2026-09-07; gap_esports_chi §1.4/§3.4, $24.5k + $5.2k
# refused in 30 h as no_key_intersection / unknown_market_type:unparsed).
# His '<lg>-<a>-<b>-<date>-game<N>' with title 'Counter-Strike: <A> vs
# <B> - Map <N> Winner' and a TEAM as his outcome; the venue lists ONE
# per-team yes/no row per map, 'astatc-cs2-<a>-<b>-<date>-map<N>' with
# the question 'Will <X> win Map <N> vs <Y>?' (nf-venue_1350.log,
# esports_chi_rows_1403.log: fnc-nip map1/map2 name NIP, hero-1win map2
# Heroic, 1win-astr map2 1WIN, fokus-nemi map1/map2 FOKUS, k27-sin map2
# K27). The rows are reached through E1's keys (his event title's
# matchup), so his codes need not equal the venue's ('1win-ast' vs
# '1win-astr'); the identity is then the venue's own words, in the C3
# discipline: his title's map number must be his slug's
# (map:title-shear); a row on this event whose identifier suffix is
# map<N> (map:segment-absent); its question in exactly that template
# with the same N (map:question-shape); <X> naming HIS OUTCOME and <Y>
# the other side of his title's matchup by _yn_name_match (map:names);
# then his BUY of X is the row's `yes` side with the venue's own intent.
# His fill on Y -- no listed row has Y as its subject -- refuses
# map:subject-absent: 'no on NIP wins Map 1' is 'fnatic wins Map 1' only
# if a listed map is never voided, which no row states, so the
# complement is NOT assumed. Two identifiers passing is map:ambiguous.
# Never a position, never a code similarity, never a nickname.
MAP_PREFIX = "astatc"
_MAP_TITLE_RE = re.compile(
    r"^\s*counter-strike:\s+(?P<a>.+?)\s+vs\s+(?P<b>.+?)\s+-\s+map\s+(?P<n>\d+)\s+winner\s*$",
    re.I)
_MAP_Q_RE = re.compile(
    r"^\s*will\s+(?P<x>.+?)\s+win\s+map\s+(?P<n>\d+)\s+vs\s+(?P<y>.+?)\s*\??\s*$", re.I)
_MAP_REST_RE = re.compile(r"^map(\d+)$")


def map_his(his_slug: str | None) -> dict | None:
    """His slug read as a map winner: {lg, a, b, date, n} or None when
    it is not one (family_of reads 'game<N>' and nothing else)."""
    from ..copy_sports import family_of

    s = (his_slug or "").lower()
    if family_of(s) != "map_winner":
        return None
    m = _C3_IDENT_RE.match("x-" + s)
    if m is None or m.group("a") == m.group("b"):
        return None
    rest = [t for t in (m.group("rest") or "").split("-") if t]
    if len(rest) != 1 or not rest[0].startswith("game"):
        return None
    return {"lg": m.group("lg"), "a": m.group("a"), "b": m.group("b"),
            "date": m.group("date"), "n": int(rest[0][4:])}


def _c3_pick_map(rows: list[dict], his: dict, outcome: str | None,
                 his_title: str | None, trace: dict) -> dict | None:
    """The map winner's identity pick over the astatc-kept rows: the
    `yes` row (a COPY carrying matched_by premap_map_winner) or None
    with the refusal named in `trace`. Pure."""
    nm = pmus._yn_name_match
    trace["family"], trace["segment"] = "map_winner", f"map{his['n']}"
    on = _c3_norm(outcome)
    tm = _MAP_TITLE_RE.match(pmus.fold_latin(str(his_title or "")))
    if tm is None or _folds_away(his_title) or int(tm.group("n")) != his["n"]:
        trace["refusal"] = "map:title-shear"
        return None
    sides = [_c3_norm(tm.group("a")), _c3_norm(tm.group("b"))]
    want_rest = f"map{his['n']}"
    # THE EVENT WITNESS (M4 review, HIGH-1 / MEDIUM-1): the matchup keys
    # reach every astatc row titled '<A> vs. <B>' on his date, whatever
    # game code or team codes its identifier carries -- fnatic and NIP
    # field rosters in several games, and a bracket's same-day rematch
    # lists the pair under reversed codes. The identifier's league code
    # must be his (plain equality: the venue's esports codes read cs2 on
    # every row), and when ANY row on his date carries his own stem
    # 'astatc-<lg>-<a>-<b>-<date>-' (his codes byte for byte, the C3
    # identity) an identifier outside it is another event. The
    # digit-suffixed case ('1win-ast' -> '1win-astr') carries no stem on
    # the board and is unchanged; fails closed.
    cands = []
    own_stem = f"{MAP_PREFIX}-{his['lg']}-{his['a']}-{his['b']}-{his['date']}-"
    own = any(str(r.get("identifier") or "").lower().startswith(own_stem) for r in rows)
    for r in rows:
        ident = str(r.get("identifier") or "").lower()
        m = _C3_IDENT_RE.match(ident)
        if (m is None or m.group("kind") != MAP_PREFIX or m.group("date") != his["date"]
                or m.group("lg") != his["lg"]):
            continue
        if own and not ident.startswith(own_stem):
            continue
        if (m.group("rest") or "") != want_rest:
            continue
        cands.append(r)
    if not cands:
        trace["refusal"] = "map:segment-absent"
        return None
    parsed = []
    for r in cands:
        qm = _MAP_Q_RE.match(pmus.fold_latin(str(r.get("question") or "")))
        if qm is None or int(qm.group("n")) != his["n"]:
            continue
        parsed.append((r, _c3_norm(qm.group("x")), _c3_norm(qm.group("y"))))
    if not parsed:
        trace["refusal"] = "map:question-shape"
        return None
    mine = [i for i, s in enumerate(sides) if on and nm(s, on)]
    if len(mine) != 1:
        # his outcome is not one side of his own title's matchup (a
        # yes/no, another name, or both sides at once)
        trace["refusal"] = "map:names"
        trace["title_sides"] = sides
        return None
    other = sides[1 - mine[0]]
    hits = [r for r, x, y in parsed if nm(x, on) and nm(y, other)]
    if not hits:
        trace["venue_names"] = [[x, y] for _, x, y in parsed]
        if any(nm(x, other) and nm(y, on) for _, x, y in parsed):
            # the venue's subject on this map is the OTHER team; his
            # team has no row of its own and the complement is not read
            trace["refusal"] = "map:subject-absent"
        else:
            trace["refusal"] = "map:names"
        return None
    ids = sorted({str(r.get("identifier")) for r in hits})
    if len(ids) > 1:
        trace["refusal"] = "map:ambiguous"
        trace["identifiers"] = ids
        return None
    yes = [r for r in hits if _norm(r.get("side_norm")) == "yes"]
    if not yes:
        trace["refusal"] = "map:side"
        return None
    if len(yes) > 1:
        trace["refusal"] = "map:ambiguous"
        return None
    r = yes[0]
    if r.get("intent") != _YN_IDENTITY_INTENT["yes"]:
        trace["refusal"] = "map:intent"
        return None
    trace.update(matched_by="premap_map_winner", admitted=ids[0], side="yes",
                 his_team=("a", "b")[mine[0]], venue_subject=on)
    return dict(r, matched_by="premap_map_winner")


def match_side(rows: list[dict], outcome: str | None,
               his_title: str | None,
               his_slug: str | None = None, *,
               yn_identity: bool = False,
               his_event_title: str | None = None,
               names_witness: str | None = None) -> dict | None:
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
    - A date in his title is never a line and never a sign (to-a-tee
      Phase 2, 2026-09-03; see his_title_nodate below).
    - yn_identity=False by default: the yes/no identity branch
      (yn_identity_rows above) is consulted only when the caller asks,
      and then only after wording found nothing.
    - his_event_title (C2) feeds ONLY that armed branch: the league
      alias and the draw need his event's two team names as the
      witness (_yn_pick); the wording arm never reads it. names_witness
      (C6-N) likewise: the opponent his stored moneyline title names,
      handed in by names_resolve when no event title is stored.
    """
    on = _norm(outcome)
    if not on:
        return None
    # HIS TITLE'S DATE IS NEVER A LINE AND NEVER A SIGN (to-a-tee
    # program Phase 2, owner order 2026-09-02 "I want us to match
    # everything ... mirror the whales to a tee"; the coverage lens's
    # §2.f reproduction, confirmed by both refutations). 'Will NEOM SC
    # win on 2026-09-03?' read as the lines {'03','09'} through
    # _LINE_CTX's '-' context and as the sign '-09' through signed_line,
    # so _yn_line_ok refused every unlined per-team row against a
    # phantom (probe UNMAPEG: his_lines=['03','09']) — every dated
    # yes/no title in the feed, twice over. The venue side has stripped
    # dates before reading lines since round 28 (_question_line); the
    # whale side never did. One regex, both sides — applied HERE, in the
    # matcher, because _lines_of and signed_line themselves are pinned
    # raw by the Phase-0 yes/no tests (those grammars consume the date
    # and never call them) — and the title's sign is read through the
    # same strip in both arms below. A real handicap in a dated title
    # ('cover -1.5 on 2026-09-03') still reads as its line and its sign.
    his_title_nodate = _QDATE_RE.sub(" ", his_title or "")
    # the whale's line may live in his title, his outcome ("Over 3.5")
    # OR — for most of the feed — only in his slug (`total-4pt5`), which
    # nothing read until 2026-08-25. See slug_lines above.
    his_lines = (_lines_of(his_title_nodate) | _lines_of(outcome)
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
        if not signed_line(outcome):
            # HIS TITLE'S DATE IS NOT A SIGN EITHER: the title's sign is
            # re-read through the same date strip his lines go through
            # (his_title_nodate above) — '-09' out of '2026-09-03' was
            # the second phantom that refused every dated yes/no title.
            # The line above stays verbatim on purpose: the yes/no sign
            # source is the market's, never a side's, and a pin holds
            # it to that (test_spread_sign_attribution); the strip is a
            # second statement, not a different source.
            his_signed_yn = signed_line(his_title_nodate)

        def _yn_line_ok(r: dict) -> bool:
            rs = (r.get("signed") or "").strip()
            if his_signed_yn or rs:
                if not rs or rs != his_signed_yn:
                    return False
            rl = (r.get("line") or "").strip()
            if rl and _clock_artifact(rl, r):
                rl = ""            # a clock is not a line (census
                                   # 2026-08-29); see _clock_artifact
            if bool(rl) != bool(his_lines):
                return False
            return not rl or rl in his_lines

        # A PER-TEAM CONTRACT THAT IS NOT HIS OWN IDENTIFIER IS SHEAR
        # (review of the mapping unit, 2026-09-03, blocking). The date
        # strip above made the wording arm reach dated per-team titles
        # for the first time — at HEAD the phantom '-09' refused every
        # one of them by accident — and the wording arm filtered on
        # side, question containment and the line guard, never on the
        # identifier. So a whale whose slug says '-kha' with a title
        # asking about NEOM, against a venue row worded tersely 'Will
        # NEOM SC win?', resolved atc-spl-neo-kha-2026-09-03-NEO with
        # source 'premap' and no switch: his slug bet Al Khaleej, the
        # mapping traded NEOM. The identity branch below refuses exactly
        # this shear (test_a_title_naming_the_other_team_is_shear_and_
        # refuses: never let one outvote the other); the wording arm
        # must too. His slug names the venue's own atc- identifier minus
        # the kind prefix, so a yes/no hit on an atc- row is his only
        # when that identifier IS 'atc-' + his slug byte-for-byte (or
        # his slug is already the venue's own identifier). A bare event
        # slug or a draw token therefore refuses every atc- row — the
        # safe direction; aec-/astatc- yes/no rows carry no side token
        # to contradict and are untouched. A dateless title on the same
        # sheared slug resolved -neo at HEAD as well (probe_shear, case
        # 4): that was the same wrong-market trade and now refuses too.
        #
        # AND HIS OWN IDENTIFIER IS HIS ONLY WHEN HIS TITLE IS HIS GAME
        # (re-review of the mapping unit, 2026-09-03, major). The
        # identifier veto alone was scope-blind: 'Will NEOM SC win the
        # 1st half on 2026-09-03?' on his own -neo slug passed it,
        # passed containment and passed the line guard ('1st' is not a
        # line), and traded the full game. The row that IS his
        # identifier is admitted only when _yn_title_is_his_game reads
        # his title as his dated win-question or the bare one — the
        # gate the identity branch already answers to, so both arms
        # answer to one title reading and one identifier reading
        # (_yn_his_identifiers).
        title_is_his_game = (_yn_title_is_his_game(his_title, his_slug)
                             if his_slug else True)

        # C2: the armed arm sets `c2_ids` to the identifiers _yn_pick
        # admitted -- his own, or the alias / draw identifier it derived
        # from his slug (codes, date, side) and its own title gate --
        # and the veto reads that set in place of his literal one; the
        # wording arm never sets it, so it reads exactly as before
        c2_ids: frozenset | None = None

        def _yn_identity_ok(r: dict) -> bool:
            if not his_slug:
                return True
            ident = str(r.get("identifier") or "")
            if not ident.startswith("atc-"):
                return True
            if c2_ids is not None:
                return ident in c2_ids
            if ident not in _yn_his_identifiers(his_slug):
                return False
            return title_is_his_game

        cands = [r for r in rows
                 if _norm(r.get("side_norm")) == on
                 and _questions_agree(want_q, _norm(r.get("question")))
                 and _yn_line_ok(r)
                 and _yn_identity_ok(r)]
        if not cands and yn_identity:
            # THE IDENTITY BRANCH (yn_identity_rows above), consulted
            # ONLY when wording found nothing: an existing wording hit
            # keeps its row, two agreeing rows stay ambiguous, and the
            # line guard applies to the identity row exactly as it does
            # to a wording row. Its rows are his identifier by
            # construction; the veto is applied all the same so both
            # arms answer to one gate. C2: _yn_pick is that branch plus
            # the league alias and the draw (his event title is their
            # witness); yn_identity_rows is its four-argument form.
            c2_trace: dict = {}
            picked = _yn_pick(rows, outcome, his_title, his_slug,
                              his_event_title, c2_trace,
                              names_witness=names_witness)
            c2_ids = c2_trace.get("admitted")
            cands = [r for r in picked
                     if _yn_line_ok(r) and _yn_identity_ok(r)]
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
            his_signed = signed_line(his_title_nodate)

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
            if rl and _clock_artifact(rl, r):
                rl = ""            # a clock is not a line (census
                                   # 2026-08-29); see _clock_artifact
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
    # NO COMPLEMENT DEDUCTION (adversarial review 2026-08-29). A
    # tempting widening — "exactly two sides, the sibling is marked, so
    # this side is the other intent" — was written and then KILLED by
    # its own review before it ever ran: (1) it feeds the live premap
    # lane (the only class trading under quarantine) with intents the
    # venue never stated on the side being bought; (2) the side echo
    # re-derives intent through THIS function, so a systematically
    # wrong deduction certifies itself ok and the 691/0 streak that
    # justified the premap resume says nothing about it; (3) the
    # manual desk lanes pass a deduced BUY_SHORT to submit_fok with no
    # LIVE_ALLOW_SHORT gate. Explicit markers on the side itself, or
    # a unique identifier, or refuse — nothing else.
    return None


_QDATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* "
    r"\d{1,2}\b",
    re.I)


def _question_line(q: str) -> str:
    """The ONE line a question states, or ''.

    Round 28 (owner's unmapped report): the stamp was `_lines_of(q)`
    demanding exactly one candidate — a DATE in the question ('Aug
    28', '8/28', '2026-08-28') added its digits to the set, voided
    the stamp, and every over/under pick on that market refused with
    no_side_match (60% of the unmapped census). Dates are never
    lines: strip them first. If several candidates still remain and
    exactly ONE is a decimal, that decimal is the line (half-point
    lines dominate this domain and a date fragment is never .5); any
    remaining ambiguity still stamps '' and the pick refuses — the
    wrong-line class stays impossible.
    """
    ql = _lines_of(_QDATE_RE.sub(" ", q or ""))
    if len(ql) == 1:
        return next(iter(ql))
    decs = {x for x in ql if "." in x}
    if len(decs) == 1:
        return next(iter(decs))
    return ""


def _explicit_marker(side: dict) -> str | None:
    """The long/short marker the venue wrote ON this side, or None --
    the first two readings of side_intent, without its unique-
    identifier default."""
    lg = side.get("long")
    if isinstance(lg, bool):
        return "ORDER_INTENT_BUY_LONG" if lg else "ORDER_INTENT_BUY_SHORT"
    t = str(side.get("marketSideType") or "").upper()
    if t.endswith("LONG"):
        return "ORDER_INTENT_BUY_LONG"
    if t.endswith("SHORT"):
        return "ORDER_INTENT_BUY_SHORT"
    return None


def _asc_yes_no(m: dict, sides: list[dict]) -> dict | None:
    """{id(side): (side_norm, intent)} for an asc- market whose two
    sides share the market slug and describe themselves by the line
    alone (no letters once normalised), each carrying an explicit
    marker, exactly one long and one short. None for every other
    shape (the rows are built as before)."""
    slug = str(m.get("slug") or "").lower()
    if not slug.startswith("asc-") or len(sides) != 2:
        return None
    if any(str(s.get("identifier") or "").lower() != slug for s in sides):
        return None
    if any(re.search(r"[a-z]", _norm(s.get("description"))) for s in sides):
        return None
    markers = [_explicit_marker(s) for s in sides]
    if sorted(x or "" for x in markers) != ["ORDER_INTENT_BUY_LONG",
                                            "ORDER_INTENT_BUY_SHORT"]:
        return None
    return {id(s): ("yes" if mk == "ORDER_INTENT_BUY_LONG" else "no", mk)
            for s, mk in zip(sides, markers)}


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
    line = _question_line(q)
    ev_slug = ev.get("slug") or ev.get("eventSlug") or ""
    ev_title = ev.get("title") or ""
    out: list[dict] = []
    all_sides = [s for s in (m.get("marketSides") or [])
                 if isinstance(s, dict)]
    sides = [s for s in all_sides
             if s.get("identifier") and s.get("description")]
    if sides:
        yn_by_marker = _asc_yes_no(m, sides)
        for s in sides:
            if yn_by_marker is not None:
                # THE asc- SIDE IS ITS MARKER (C3, 2026-09-06). The
                # venue's spread market is ONE proposition ('Will the
                # Bulldogs cover -10.5 vs the Rattlers …?') whose two
                # sides share the identifier and describe themselves by
                # the LINE ('10.50'), so every asc row normalised to the
                # digits ('10 50'), both sides collided on the
                # (identifier, side_norm) index and the short side's
                # write won: 100+ rows in c3_rows_2038.log, all
                # ORDER_INTENT_BUY_SHORT, none orderable. The side that
                # says the proposition holds is the venue's own LONG
                # marker, exactly as it reads on the astatc yes/no rows;
                # so the long side is 'yes' and the short side 'no',
                # read from the explicit marker on the side itself --
                # never deduced from a sibling, never from ordering
                # (see side_intent). A market whose markers do not
                # name exactly one long and one short keeps the old
                # rows, which nothing maps.
                yn, intent = yn_by_marker[id(s)]
                out.append({
                    "identifier": str(s["identifier"]).lower(),
                    "event_slug": ev_slug, "event_title": ev_title,
                    "market_slug": (m.get("slug") or "").lower(),
                    "question": q[:300], "kind": "side",
                    "line": (next(iter(_lines_of(s["description"])))
                             if len(_lines_of(s["description"])) == 1
                             else line),
                    "side_norm": yn,
                    "signed": signed_line(s["description"]) or signed_line(q),
                    "intent": intent,
                })
                continue
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
                        # R1 / C6-N: the event's keys (the pair key among
                        # them) plus the row's own name keys; the UPSERT
                        # rewrites event_keys on conflict, so every row the
                        # sweep still sees gains them on its next pass
                        await _upsert(pool, r, keys_for_row(keys, r))
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
                        await _upsert(pool, r, keys_for_row(keys, r))
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
    keys.update(his_name_keys(market_title, event_title, global_slug))
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
        # This only REPORTS. It measured the class as zero for two weeks
        # because the venue side never emitted the stripped key
        # (gap_league_codes §0.1): "would have hit" read false on every
        # row. R1 (2026-09-07) now emits the league-stripped pair key on
        # BOTH sides, so that key is in the fetch above and the old
        # question has no answer left to give. RE-POINTED (R3): the probe
        # asks what the venue's identifiers say beside C6-N's own lookup:
        # his club NAMES (his_name_keys) were in the fetch above and met
        # nothing; does ANY atc row on his date carry either of his club
        # codes in its position? Nothing: the venue does not list his
        # league that day (gre1, bel1, ere, bra3 on 2026-09-06) --
        # `venue:league-unlisted` rides the split, and the row leaves the
        # mapper's bucket. Something: listed, and the gap is the lookup or
        # an arm -- would_have_hit, counted by the census exactly as
        # before. Reads only; the identifier scan is bounded (LIMIT 25)
        # and lives on this diagnostic path alone, never in resolve; a
        # slug outside the <lg>-<a>-<b>-<date> grammar asks nothing.
        try:
            parts = _yn_slug_parts(global_slug) or _c5_slug_parts(global_slug)
            if parts is not None:
                pat = (f"^atc-[a-z0-9]+-({parts['a']}-[a-z0-9]+|[a-z0-9]+-{parts['b']})"
                       f"-{d}-")
                alt = await pool.fetch(
                    "SELECT identifier FROM us_premap WHERE identifier ~ $1::text LIMIT 25",
                    pat)
                out["league_alias_probe"] = {
                    "name_keys": his_name_keys(market_title, event_title, global_slug)[:4],
                    "code_pattern": pat,
                    "rows_it_would_find": len(alt),
                    "would_have_hit": bool(alt),
                    "sample": [str(r["identifier"]) for r in alt[:3]],
                }
                if not alt:
                    out["split"] = "venue:league-unlisted"
        except Exception as exc:  # noqa: BLE001 — diagnostics never
            out["league_alias_probe"] = {"error": type(exc).__name__}
        return out
    from ..copy_sports import family_of

    mtype = market_type_of(global_slug or "")
    want = _want_prefixes(global_slug)
    his_c3 = c3_his(global_slug) if yn_identity_on() else None
    # E2: the map winner, read inside the same switch (resolve's own)
    his_map = map_his(global_slug) if yn_identity_on() and his_c3 is None else None
    out["family"] = family_of(global_slug or "")
    out["c3_on"] = yn_identity_on()
    if not want:
        # THE SPLIT (C1 build step 5, measurement only): the step name
        # stays -- the copy lane's census buckets on it -- and `split`
        # says which of two different gaps this is: the slug grammar
        # named NO family ('unparsed', a parser gap) or it named one the
        # venue table carries no prefix for ('family_not_listed':
        # prop / exact_score / crypto, a venue-family gap). C3 names
        # the family the venue does not list (`refusal`): exact score,
        # halftime result, team totals -- c3_rows_2041.log lists none.
        out["step"] = "unknown_market_type"
        out["split"] = "unparsed" if mtype == "unknown" else "family_not_listed"
        out["detail"] = (f"market_type_of({global_slug!r}) is unrecognised" if mtype == "unknown"
                         else f"market_type_of({global_slug!r}) = {mtype!r}: no venue prefix "
                              f"for that family")
        if out["family"] in C3_ABSENT:
            out["refusal"] = C3_ABSENT[out["family"]]
        return out
    kept = [r for r in rows if _prefix_of(r.get("identifier")) in want]
    if not kept:
        out["step"] = "type_prefix_filter_emptied"
        out["detail"] = (f"{len(rows)} rows on this event, none with a "
                         f"{sorted(want)} prefix")
        return out
    if his_c3 is not None:
        kept = c3_same_segment(kept, his_c3["seg"])
        if not kept:
            # the venue lists the family on this event but not on his
            # segment: a family gap, named (total:segment-absent)
            out["step"] = "unknown_market_type"
            out["split"] = f"{his_c3['family']}:segment-absent"
            out["refusal"] = out["split"]
            out["detail"] = (f"no {C3_PREFIX[his_c3['family']]}- row on segment "
                             f"{his_c3['seg']!r} for this event")
            return out
    # E2: a map winner is read by its own pick alone, as a segmented C3
    # family is -- the wording arm never sees its rows
    wording = (his_c3 is None or not his_c3["seg"]) and his_map is None
    hit = match_side(kept, outcome, market_title, global_slug) if wording else None
    c3_trace: dict = {}
    map_trace: dict = {}
    if hit is None and yn_identity_on():
        # the same second call resolve makes, so the census attributes
        # exactly what production does once the owner's flip is on
        if wording:
            hit = match_side(kept, outcome, market_title, global_slug,
                             yn_identity=True, his_event_title=event_title)
        if hit is not None and hit.get("league_alias"):
            out["league_alias"] = hit["league_alias"]
        if hit is not None and hit.get("matched_by"):
            out["matched_by"] = hit["matched_by"]
        if hit is not None and hit.get("code_pair"):
            # C6-N: the pair the venue's question witnessed, the row that named him
            out["code_pair"] = dict(hit["code_pair"])
            out["witness"] = hit["witness"]
        if hit is None and his_c3 is not None:
            # the same C3 call resolve makes, inside the same switch (C4:
            # a spread also sees the event's aec row and the grammar
            # class's certification state)
            cert = await grammar_cert(pool) if his_c3["family"] == "spread" else None
            hit = c3_pick(kept, outcome, market_title, global_slug, c3_trace,
                          board=rows, cert=cert)
            out["c3"] = c3_trace
            if hit is not None:
                out["matched_by"] = hit["matched_by"]
                if hit.get("league_alias"):
                    out["league_alias"] = hit["league_alias"]
        if hit is None and his_map is not None:
            # E2: the same map-winner call resolve makes
            hit = _c3_pick_map(kept, his_map, outcome, market_title, map_trace)
            out["map"] = map_trace
            if hit is not None:
                out["matched_by"] = hit["matched_by"]
    c5_trace: dict = {}
    if hit is None and yn_identity_on():
        # C5: the same code translation resolve makes, after every other
        # arm found nothing; the trace is the census's reading
        hit = await c5_resolve(pool, rows, kept, outcome, market_title, event_title,
                               global_slug, c5_trace, his_c3=his_c3, wording=wording)
        if c5_trace:
            out["yn_c5"] = c5_trace
        if hit is not None:
            out["matched_by"] = hit["matched_by"]
            out["code_translated"] = hit["code_translated"]
            out["witness"] = hit["witness"]
            if hit.get("league_alias"):
                out["league_alias"] = hit["league_alias"]
    c6_trace: dict = {}
    if hit is None and yn_identity_on():
        # C6-N: the same stored-title witness resolve reads, after C5;
        # the trace is the census's reading (yn_c6)
        hit = await names_resolve(pool, rows, kept, outcome, market_title, event_title,
                                  global_slug, c6_trace, wording=wording)
        if c6_trace:
            out["yn_c6"] = c6_trace
        if hit is not None:
            out["matched_by"] = hit["matched_by"]
            out["code_pair"] = dict(hit["code_pair"])
            out["witness"] = hit["witness"]
            if hit.get("league_alias"):
                out["league_alias"] = hit["league_alias"]
    if hit is None:
        out["step"] = "no_side_match"
        # printed through the same date strip the matcher applies, so
        # the census shows the lines the matcher saw (it printed the
        # phantom ['03','09'] before, probe UNMAPEG)
        _hl = sorted(_lines_of(_QDATE_RE.sub(" ", market_title or ""))
                     | _lines_of(outcome) | slug_lines(global_slug))
        out["detail"] = (f"outcome {outcome!r} matched none of "
                         f"{[(r.get('side_norm'), r.get('line') or '-') for r in kept][:6]}"
                         f" his_lines={_hl[:4]}")
        # NUMBER-LABELED SIDES (census 2026-08-29): candidates printed
        # as ('1 50','1.50') carry no team name at all, so a named
        # pick can never match by side_norm. Before designing a
        # resolution (sign-vs-question inversion risk), the census
        # prints the full anatomy of ONE such row set: question,
        # signed, intent, identifier — ground truth first.
        if kept and all((r.get("side_norm") or "").replace(" ", "")
                        .replace(".", "").isdigit() for r in kept[:2]):
            def _one_line(s: str | None) -> str:
                # the venue's question text can carry raw newlines —
                # jq -r prints them and the probe line shatters
                return " ".join(str(s or "").split())
            anatomy = "; ".join(
                f"id=…{_one_line(r.get('identifier'))[-40:]}"
                f" q={_one_line(r.get('question'))[:70]!r}"
                f" sg={r.get('signed')!r} it={r.get('intent')}"
                for r in kept[:3])
            out["detail"] += f" ANATOMY[{anatomy}]"
        else:
            # every refusal names its candidates — the id tail alone
            # (market slug + side) answers "was the right market even
            # in the kept set?"
            ids = ",".join("…" + " ".join(
                str(r.get("identifier") or "").split())[-28:]
                for r in kept[:6])
            out["detail"] += f" IDS[{ids}]"
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
        # NAMED-TENNIS PROBE (Phase 0, 2026-08-27): the named lane's
        # own read-only measurement, mirroring the yes/no probe's
        # discipline. The step string stays 'no_side_match'.
        if _norm(outcome) not in ("yes", "no"):
            try:
                nt: dict = {}
                nhit, nreason = named_ml_bridge_explain(
                    kept, rows, outcome, market_title, global_slug,
                    event_title, trace=nt)
                out["named_ml"] = {
                    "would_resolve": bool(nhit), "reason": nreason,
                    "identifier": (nhit or {}).get("identifier"),
                    "side_norm": (nhit or {}).get("side_norm"),
                    "attested_family": bool(nhit) and str(
                        (nhit or {}).get("identifier") or ""
                    ).split("-")[1:2] in (["itfme"], ["itfwo"]),
                    "sub_gate": (
                        "line_poison_recovered" if nhit and any(
                            str(r.get("line") or "").strip()
                            for r in kept)
                        else "no_named_candidate" if kept and all(
                            _norm(r.get("side_norm")) in ("yes", "no")
                            for r in kept)
                        else nreason),
                    "has_tennis_raw_row": any(
                        " ".join(_norm(str(r.get("question") or ""))
                                 .split())
                        .startswith(_NAMED_Q_PREFIX.rstrip())
                        for r in kept),
                }
                for k in ("row_gates", "blockers", "lg_pair_seen",
                          "lg_pairs"):
                    if k in nt:
                        out["named_ml"][k] = nt[k]
                if nhit:
                    sib = [r for r in kept
                           if str(r.get("identifier") or "").lower()
                           == str(nhit.get("identifier")
                                  or "").lower()
                           and r is not nhit]
                    _nq = " ".join(_norm(str(
                        nhit.get("question") or "")).split())
                    _nm = _NAMED_Q_STRICT_RE.fullmatch(_nq)
                    _nip = [x for x in str(nhit.get("identifier")
                                           or "").split("-") if x]
                    out["named_ml"]["audit"] = {
                        "clock": ({"hh": _nm.group("hh"),
                                   "mm": _nm.group("mm"),
                                   "ap": _nm.group("ap")}
                                  if _nm else None),
                        "venue_codes": _nip[2:4],
                        "his_opponent_vs_other_side": [
                            str(event_title)[:120],
                            (sib[0].get("side_norm")
                             if sib else None)],
                        "identifier": nhit.get("identifier"),
                        "market_slug": nhit.get("market_slug"),
                        "event_slug": nhit.get("event_slug"),
                        "kind": nhit.get("kind"),
                        "side_norm": nhit.get("side_norm"),
                        "intent": nhit.get("intent"),
                        "sibling_side": (sib[0].get("side_norm")
                                         if sib else None),
                        "sibling_intent": (sib[0].get("intent")
                                           if sib else None),
                        "question": str(nhit.get("question"))[:300],
                        "qlen": len(str(nhit.get("question") or "")),
                        "line_before": nhit.get("line"),
                        "event_title":
                            str(nhit.get("event_title"))[:120],
                        "his_slug": global_slug,
                        "his_lg": (global_slug or "").split("-")[0],
                        "his_title": str(market_title)[:120],
                        "his_event_title": str(event_title)[:120],
                        "outcome": str(outcome)[:40],
                    }
            except Exception as exc:  # noqa: BLE001 — a probe never breaks
                out["named_ml"] = {"error": type(exc).__name__}
        # THE IDENTITY BRANCH, MEASURED DARK (to-a-tee Phase 2): what
        # PREMAP_YN_IDENTITY=on would do with this row set, printed
        # whether or not the owner has flipped it, so the census counts
        # the recoverable per-team class before a dollar rides it — the
        # way named_ml is measured above. Reads only.
        # C2: `yn_c2` names the branch's refusal (yn:league-slot,
        # yn:league-ambiguous, yn:name-digits, yn:no-row ...) or its
        # label (premap_identity / premap_alias + the alias seen), and
        # the step's `split` carries that name so the mirror's census
        # (explain_unmapped: 'no_side_match:yn:...') counts it; a dark
        # would-resolve reads 'yn:would-resolve'. C5: a row _yn_pick
        # picked and match_side's line / identity guard then vetoed
        # reads 'yn:picked-vetoed' -- the one path that left the label
        # bare (epl investigation 2026-09-06 §1.3), so a bare
        # no_side_match on a yes/no row can only mean 'judged by older
        # code'.
        try:
            _ih = match_side(kept, outcome, market_title, global_slug,
                             yn_identity=True, his_event_title=event_title)
            out["yn_identity"] = {
                "on": yn_identity_on(),
                "would_resolve": _ih is not None,
                "identifier": _ih.get("identifier") if _ih else None,
                "side_norm": _ih.get("side_norm") if _ih else None,
                "intent": _ih.get("intent") if _ih else None,
            }
            if _norm(outcome) in ("yes", "no"):
                _tr: dict = {}
                _yn_pick(kept, outcome, market_title, global_slug,
                         event_title, _tr)
                out["yn_c2"] = _tr
                if _ih is None and _tr.get("refusal"):
                    out["split"] = _tr["refusal"]
                elif _ih is not None:
                    out["split"] = "yn:would-resolve"
                elif _tr.get("matched_by"):
                    out["split"] = "yn:picked-vetoed"
        except Exception as exc:  # noqa: BLE001 — a probe never breaks
            out["yn_identity"] = {"error": type(exc).__name__}
        if c3_trace.get("refusal"):
            # C3: the family's own refusal name is the split the census
            # counts (no_side_match:spread:line-absent …)
            out["split"] = c3_trace["refusal"]
        if c5_trace.get("refusal"):
            # C5: a single candidate event was found and the translation
            # refused (yn:code-translate:ambiguous / unwitnessed /
            # name-mismatch), or it was certified and the lane refused
            # the rewritten slug by its own name
            out["split"] = c5_trace["refusal"]
        if c6_trace.get("refusal"):
            # C6-N: the stored-title witness was read and the names arm
            # refused by its own name (yn:names-… with `why`)
            out["split"] = c6_trace["refusal"]
        if map_trace.get("refusal"):
            # E2: the map winner's own refusal is the split -- last, so
            # a C5 trace on a digit-suffixed code ('1win-ast') never
            # relabels a family C5 has no lane for
            out["split"] = map_trace["refusal"]
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
    # C6-N: his side of the per-club dated name key (a lookup only)
    keys.update(his_name_keys(market_title, event_title, global_slug))
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
    want = _want_prefixes(global_slug)
    if not want:
        return None
    # `kept` beside `rows`, not instead of it: the named lane's
    # blockers (multi_event_pool, event_slug_missing, nonlatin_in_pool,
    # event_scan_ambiguous) must scan EVERYTHING the keys fetched,
    # exactly as the Phase-0 probe has measured it since 2026-08-27 —
    # judging pool-wide ambiguity against a filtered pool makes a
    # dropped sibling look unique, which is the wrong-side incident's
    # shape all over again.
    kept = [r for r in rows if _prefix_of(r.get("identifier")) in want]
    if not kept:
        return None
    # C3: a spread / total / btts pick sees only the rows on ITS
    # segment (the venue's fh/sh totals share the full game's question
    # word for word), and a segmented pick is read by the family's own
    # identity pick alone -- its title veto and matched_by label. ALL
    # of C3 sits behind PREMAP_YN_IDENTITY, the switch C2's identity
    # arm answers to (review major 1): off, nothing here runs
    his_c3 = c3_his(global_slug) if yn_identity_on() else None
    if his_c3 is not None:
        kept = c3_same_segment(kept, his_c3["seg"])
        if not kept:
            return None
    # E2 (2026-09-07): the map winner (the block comment at map_his),
    # inside the same switch and read by its own pick alone, as a
    # segmented C3 family is -- the wording arm never sees its rows
    his_map = map_his(global_slug) if yn_identity_on() and his_c3 is None else None
    wording = (his_c3 is None or not his_c3["seg"]) and his_map is None
    hit = match_side(kept, outcome, market_title, global_slug) if wording else None
    matched_by = "premap"
    if hit is None and yn_identity_on():
        # THE YES/NO IDENTITY BRANCH (yn_identity_rows above; to-a-tee
        # program Phase 2, owner order 2026-09-02). Consulted ONLY
        # after wording found nothing, so no existing mapping changes
        # source or side, and DARK by default: PREMAP_YN_IDENTITY=on is
        # the owner's flip — a config change, no deploy to roll back —
        # exactly like the named lane below. A hit is source 'premap',
        # the class the quarantine admits, labelled matched_by
        # 'premap_identity' so its fills stay attributable and the
        # branch can be killed on its own. This is the literal call
        # above with the branch armed; the branch runs only when the
        # wording filter found nothing, so one call's answer is the
        # other's.
        if wording:
            hit = match_side(kept, outcome, market_title, global_slug,
                             yn_identity=True, his_event_title=event_title)
        if hit is not None:
            # C2: a row admitted through the league alias (or the draw
            # under an alias) is labelled 'premap_alias' and carries the
            # alias it observed; source stays 'premap' either way. C3:
            # the draw witnessed by his own title says so on the row.
            matched_by = hit.get("matched_by") or (
                "premap_alias" if hit.get("league_alias") else "premap_identity")
        if hit is None and his_c3 is not None:
            # C3: the family's identity pick (spread / total / btts) --
            # source 'premap', matched_by premap_spread / premap_total /
            # premap_fh_total / premap_btts …; inside the same switch
            cert = await grammar_cert(pool) if his_c3["family"] == "spread" else None
            hit = c3_pick(kept, outcome, market_title, global_slug, board=rows, cert=cert)
            if hit is not None:
                matched_by = hit["matched_by"]
        if hit is None and his_map is not None:
            # E2: the map winner's identity pick -- source 'premap',
            # matched_by premap_map_winner; the `yes` row of the venue's
            # per-team map market naming his outcome, never its `no`
            hit = _c3_pick_map(kept, his_map, outcome, market_title, {})
            if hit is not None:
                matched_by = hit["matched_by"]
    if hit is None and os.getenv("PREMAP_NAMED_LANE",
                                 "").strip().lower() == "on":
        # NAMED-TENNIS LANE, Phase 1 wiring (mapper-fail diagnosis
        # 2026-08-30; the lane itself is the Phase-0 bridge that has
        # been measured read-only since 2026-08-27). Consulted ONLY
        # after match_side returns None — no existing mapping may
        # change source or side — and DARK by default: the env switch
        # is the owner's flip, so enabling is a config change and
        # rollback needs no deploy. The gate to flipping it is the
        # accumulated named_ml audit (app.py records 40/run) reading
        # zero-mismatch over both attested families. A bridge failure
        # of any kind falls through to the legacy resolvers exactly as
        # a premap miss does today, and a bridge hit still passes the
        # same no-intent refusal below — matched_by is distinct so
        # fills are attributable and the lane can be killed
        # independently of classic premap.
        try:
            nhit, _nwhy = named_ml_bridge_explain(
                kept, rows, outcome, market_title, global_slug,
                event_title)
        except Exception:  # noqa: BLE001 — fail closed to legacy
            nhit = None
        if nhit is not None:
            hit = nhit
            matched_by = "premap_named"
    if hit is None and yn_identity_on():
        # C5 (2026-09-07): a differing TEAM code translated by the venue's
        # own question, per event (the block comment at c5_certify), then
        # the identity lane as above on the rewritten slug. Consulted only
        # after every other arm found nothing, inside the same switch;
        # the hit carries code_translated and witness and keeps the
        # lane's own matched_by and source 'premap'.
        hit = await c5_resolve(pool, rows, kept, outcome, market_title, event_title,
                               global_slug, {}, his_c3=his_c3, wording=wording)
        if hit is not None:
            matched_by = hit["matched_by"]
    if hit is None and yn_identity_on():
        # C6-N (2026-09-07): the names-witnessed row's stored-title
        # witness (the block comment at _yn_names_pick); the arm itself
        # already ran inside match_side above with his event title
        hit = await names_resolve(pool, rows, kept, outcome, market_title, event_title,
                                  global_slug, {}, wording=wording)
        if hit is not None:
            matched_by = hit["matched_by"]
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
    out = {"market_slug": hit["identifier"],
           "title": hit.get("question") or hit.get("event_title"),
           "outcome": hit.get("side_norm"),
           "intent": intent,
           "matched_by": matched_by, "score": 1.0}
    if hit.get("league_alias"):
        out["league_alias"] = hit["league_alias"]
    if hit.get("code_translated"):
        # C5: the translation this event certified and the venue row that
        # named his club -- the plan's record of why this identifier
        out["code_translated"] = dict(hit["code_translated"])
        out["witness"] = hit["witness"]
    if hit.get("code_pair"):
        # C6-N: the code pair the venue's question witnessed for this
        # event and the row that named him -- the record, never a table
        out["code_pair"] = dict(hit["code_pair"])
        out["witness"] = hit["witness"]
    return out


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
