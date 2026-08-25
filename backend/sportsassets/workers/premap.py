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
FAST_MAX_PAGES = int(os.environ.get("PREMAP_FAST_PAGES", "8"))
PAGE_LIMIT = 100
MAX_EVENT_PAGES = 40            # bounds a sweep at ~4k events
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
        keys.add(_norm(t))
    sm = pmus._surname_matchup(title)
    if sm:
        a, b = [p.strip() for p in re.split(r"\s+vs\s+", sm, flags=re.I)]
        keys.add(f"{a} vs {b}".lower())
        keys.add(f"{b} vs {a}".lower())
    if t and " vs" in t.lower():
        sides = re.split(r"\s+vs\.?\s+", t, flags=re.I)
        if len(sides) == 2:
            na, nb = _norm(sides[0]), _norm(sides[1])
            if na and nb:
                keys.add(f"{na} vs {nb}")
                keys.add(f"{nb} vs {na}")
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
    return out


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
        his_signed = signed_line(outcome) or signed_line(his_title)

        def _lined_ok(r: dict) -> bool:
            # SIGN AGREEMENT FIRST (leak-hunt round 3): _norm erases
            # +/-, so 'Chiefs -3' and 'Chiefs +3' are the same string.
            # This check used to live inside the line branch and was
            # skipped whenever no line parsed — which is exactly the
            # whole-number handicap case, so those inverted silently.
            rs = (r.get("signed") or "").strip()
            if his_signed or rs:
                if not rs or rs != his_signed:
                    return False
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
        for _page in range(max_pages):
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
            if len(got) < PAGE_LIMIT:
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
    summary = {"mode": mode, "events": events, "rows": seen_rows,
               "err": err, "events_err": events_err,
               "lane": "fast" if windowed_only else "full",
               "window_h": [back_h, fwd_h], "max_pages": max_pages,
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
    d = date_of(global_slug)
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
            "event_title, intent, signed FROM us_premap "
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
    d = date_of(global_slug)
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
            "event_title, intent, signed FROM us_premap "
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
