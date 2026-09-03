"""Cell-level copy policy: whale x sport x market-type x entry band.

Derived 2026-08-06 from four fill-level forensic reconstructions under
strict statistical rules — a cell is copyable only if the donor's ROI in
it is >= +1.5% (survives our ~86s latency decay), carries >= $1M donor
volume, and the MECHANISM is directional forecasting (market-making
hedge legs are not signals):

  kch123        basketball spreads (+22.2% / $21.8M), basketball totals
                (+10.7% / $16.0M), football spreads (+31.8% / $5.7M),
                hockey moneyline (+6.6% / $45.3M)
  HomeRunHazard baseball totals (+1.89% / $20.3M), WNBA totals (+6.50%),
                WNBA moneyline (+3.62%); entry band 50-95c ONLY — every
                sub-50c band in his directional book loses (-6.8% to
                -100%), and >95c fails the $1M sample floor
  swisstony     soccer moneyline (+3.3% / $360M), soccer spreads
                (+3.5% / $99.2M); exact-score (+0.7%) fails the bar
  RN1           UNRESTRICTED (owner decision 2026-08-06 evening). The
                forensic verdict on his BOOK stands — >100% of profit is
                matched-pair spread capture (+3.64%), directional
                residual -1.07% — but neither that residual nor the
                all-fills paper cohort (-2.3%) measures the rule we
                actually run: first entry per market, $3 FOK at his
                price +2%. His first leg is the side the market tends
                to move toward while he completes the pair, and our own
                285-settled live record of exactly that rule is
                +$170.95 (163-122). Reinstated on that evidence, under
                watch: review trigger at -$60 from the sleeve's
                high-water mark.

Kalshi carve-out: at 30-70c the taker fee (7%*p*(1-p), peak 1.75%)
consumes a thin donor edge, so thin-edge cells (swisstony soccer, HRH
baseball) only route to Kalshi at >= 70c; Polymarket (zero fee) takes
everything else.

Exclusive assignment is deliberate: it prevents two whales walking us
onto opposite sides of one game through different assets. Everything
fails closed: unknown whale, league, market type, or price copies
nothing.
"""

from __future__ import annotations

_KINDS = {"atc", "aec", "asc", "tsc", "astatc", "cpc"}

SPORT_OF = {"nba": "basketball", "cbb": "basketball",
            "nfl": "football", "cfb": "football",
            "nhl": "hockey", "mlb": "baseball", "wnba": "wnba",
            "atp": "tennis", "wta": "tennis", "itf": "tennis",
            # Tennis's third tours (audit 2026-08-21): the executor's
            # own _TENNIS_LEAGUES names chal/itfwo/itfme, but this map
            # didn't, so challenger and ITF slugs defaulted to soccer —
            # defeating 0x2c33's tennis block and RN1's tennis clip,
            # the same leak class the esports entries below closed.
            "chal": "tennis", "atpchal": "tennis",
            "itfwo": "tennis", "itfme": "tennis",
            # Esports out of the soccer/other bucket (leak found
            # 2026-08-21 wiring the dossier promotions: 'aec-cs2-…'
            # classified as soccer, so any whale with a soccer cell
            # could copy esports). Named 'esports' = in nobody's CELLS,
            # so cell-gated whales never copy it; UNRESTRICTED whales
            # keep copying it exactly as before (their record on it is
            # +$582 settled), now graded under its own label instead of
            # polluting the soccer rows.
            "cs2": "esports", "csgo": "esports", "dota2": "esports",
            "lol": "esports", "valorant": "esports", "val": "esports"}

# Whales copied WITHOUT a cell gate — the live sleeve's own record of
# the first-entry-per-market rule is the governing measurement (owner
# decision 2026-08-06; see the RN1 note above).
# 0x2c33…0563 promoted from vetting 2026-08-10 (owner approval): 1,712
# probes at our REAL latency graded +0.76% per-$1k residual ROI —
# strongest measured candidate on the board. Vetting measured his whole
# book, so he enters unrestricted like RN1 and earns cells (or removal)
# on his own settled record. Keyed by the roster's auto-generated
# username; the row is pinned (migration 019) so the weekly roster
# refresh can neither rename nor deactivate it out from under this key.
UNRESTRICTED = frozenset({
    "rn1",
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
})

# Soccer halt LIFTED (owner order 2026-08-12 ~10am ET, after ~18h of
# verified guard refusals with zero stacked positions). History: halted
# 2026-08-11 ~3:40pm ET on the PMUS same-market stacking incident. The
# resume rides three protections: one position per game, the venue
# never-add, and the SOCCER_PRICE_FLOOR below. Re-halting is putting
# "soccer" back in this set.
HALTED_SPORTS = frozenset()

# SOCCER PRICE FLOOR (owner-approved resume design 2026-08-12): a
# soccer entry only copies when HIS fill price is >= this — the
# improbable rungs of a ladder (O3.5 at 22c) are skipped, and because
# a skip writes no row it does NOT consume the game's one-position
# slot, so the copy taken is always his most probable line that
# clears the floor. Binds EVERY whale including UNRESTRICTED ones;
# an unreadable price refuses (the floor is the protection, and an
# absent price must not disable it).
SOCCER_PRICE_FLOOR = 0.40

# Whales copied NOWHERE right now. HomeRunHazard's 2026-08-11 pause
# cited 10 settled at -$0.80 with prop-heavy flow feeding the losing
# prop cohort; both facts are superseded (owner review 2026-08-21):
# his settled book reached 12W-8L, +$275.50 on $263.51 (+104.6% ROI,
# tennis 7W-3L +$197.56, baseball 5W-4L +$118.89) and the prop bleed
# is now structurally blocked for everyone via BLOCKED_TYPES. Vetting
# base: 14,383 probes at +1.14% per-$1k at our real latency. Un-paused
# on that evidence; his cells and band still gate every fill.
PAUSED: frozenset[str] = frozenset()

# Market types copied for NOBODY — including UNRESTRICTED whales (owner
# directive 2026-08-11): live prop copies graded 3W/16L, -64% ROI.
# Blocked at the type level so the bleed cannot re-enter through any
# whale. btts/exact_score stay governed by the cell table.
BLOCKED_TYPES = frozenset({"prop"})

# whale -> allowed (sport, market_type) cells. Fail-closed.
CELLS: dict[str, frozenset] = {
    "kch123": frozenset({("basketball", "spread"), ("basketball", "total"),
                         ("football", "spread"), ("hockey", "moneyline")}),
    # Owner directive 2026-08-07 ("every whale trade copied as long as
    # edge metrics are met"): every EDGE-POSITIVE cell in his book is
    # on — the +1.5% bar relaxes to >0 donor ROI, with the 50-95c entry
    # band still guarding (his sub-50c entries lose account-wide) and
    # Kalshi fee floors covering the thin cells. Added: MLB moneyline
    # (+0.80%/$23.9M), tennis moneyline (ATP +1.07%/$24M, WTA
    # +1.12%/$19.4M — his scale sport), NBA spread (+2.76%) and total
    # (+1.27%), NHL total (+3.93%). Still OUT (edge not met): MLB
    # spread (+0.01% dead), ATP totals (-3.51%), CFL (unmeasured mix).
    "homerunhazard": frozenset({("baseball", "total"),
                                ("baseball", "moneyline"),
                                ("wnba", "total"), ("wnba", "moneyline"),
                                ("tennis", "moneyline"),
                                ("basketball", "spread"),
                                ("basketball", "total"),
                                ("hockey", "total")}),
    "swisstony": frozenset({("soccer", "moneyline"), ("soccer", "spread")}),
    # Dossier promotions (owner order 2026-08-21, $100 probation clips):
    # cells are the sports the 30-day flow study actually measured for
    # each — tennis/soccer/baseball/basketball. Their in-game bucket
    # (~55% of flow) is deliberately NOT a cell: unmapped by design.
    # Two weeks of settled fills decide raise-or-drop.
    "ferrarichampions2026": frozenset({
        ("tennis", "moneyline"), ("tennis", "spread"), ("tennis", "total"),
        ("soccer", "moneyline"), ("soccer", "spread"), ("soccer", "total"),
        ("baseball", "moneyline"), ("baseball", "spread"),
        ("baseball", "total"),
        ("basketball", "moneyline"), ("basketball", "spread"),
        ("basketball", "total")}),
    "0x076daa87": frozenset({
        ("tennis", "moneyline"), ("tennis", "spread"), ("tennis", "total"),
        ("soccer", "moneyline"), ("soccer", "spread"), ("soccer", "total"),
        ("baseball", "moneyline"), ("baseball", "spread"),
        ("baseball", "total"),
        ("basketball", "moneyline"), ("basketball", "spread"),
        ("basketball", "total")}),
}

# whale -> (entry floor, entry ceiling) on HIS price, dollars.
ENTRY_BAND: dict[str, tuple[float, float]] = {
    "homerunhazard": (0.50, 0.95),
}

# (whale, sport) cells whose donor edge is too thin to pay Kalshi's
# mid-price taker fee: Kalshi entries only at/above this ask.
KALSHI_MIN_ASK: dict[tuple[str, str], float] = {
    ("swisstony", "soccer"): 0.70,
    ("homerunhazard", "baseball"): 0.70,
    # Tennis ~1.1% donor edge cannot pay the mid-price taker fee either.
    ("homerunhazard", "tennis"): 0.70,
}


import hashlib as _hashlib
import re as _re

# Venue split (owner directive 2026-08-07: "trades firing on both venues
# almost evenly, when it makes sense from a pricing standpoint"). The
# fast PMUS leg reacts in ~86s and wins the race for every fillable
# copy; the cross-venue one-copy rule then locks Kalshi out — Kalshi
# was structurally the leftover venue. A deterministic per-asset split
# gives Kalshi FIRST CLAIM on half the flow in the sports it actually
# lists; the engine's price gates (his+2% fee-loaded, fee floors,
# collapse guard) remain the "when it makes sense" bar, and anything
# Kalshi cannot price is reclaimed by the hourly PMUS sweep. Soccer
# stays PMUS-first: Kalshi's soccer coverage is thin and the 70c fee
# floor already routes most of it to the fee-free venue.
# Tennis removed 2026-08-17 late night (owner: "I just do not want
# tennis being traded on kalshi") — tennis copies never defer to the
# Kalshi leg; they execute PMUS-side immediately. The Kalshi sweep
# additionally refuses tennis rows at the venue level.
KALSHI_FIRST_SPORTS = frozenset({"baseball", "wnba", "basketball",
                                 "football", "hockey"})


def kalshi_first(asset: str) -> bool:
    """Kalshi's first-claim share of fresh copy flow, deterministic by
    asset id — same answer on every service, so the two venues never
    race for one position.

    Default 50 (owner directive 2026-08-10 night: "both firing correct
    trades and copied edges immediately"). PMUS fires instantly on its
    half; the fresh-fill wake fires Kalshi in ~10-40s on the other; and
    the reclaim sweep (COPY_SWEEP_EVERY_S, now 2 minutes) cross-covers
    whatever the first venue refused, so neither leg ever waits long on
    the other. One copy per position stays enforced by the claims and
    the venue-side never-add veto. History: 100 on 2026-08-09, 50 on
    2026-08-10 morning, 100 that evening for Kalshi volume, 50 again
    the same night for both-immediate; KALSHI_FIRST_PCT overrides in
    one env change."""
    if not asset:
        return False
    import os as _os
    pct = int(_os.environ.get("KALSHI_FIRST_PCT", "50"))
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    return int(_hashlib.sha1(str(asset).encode()).hexdigest()[-2:],
               16) % 100 < pct


_DATE_RE = _re.compile(r"^\d{4}$")
_TOTAL_RE = _re.compile(r"^[ou]\d+(pt\d)?$")
_LINE_RE = _re.compile(r"^(pos|neg)?\d+(pt\d)?$")
# Post-date tokens that name a SEGMENT of the game (see market_type_of).
# Exact tokens only — 'first' catches 'first-half' and 'first-quarter',
# the abbreviations are the feed's own ('1h', 'fh', 'q1'); nothing here
# is a stem, so a team code can only collide by being one of these
# literal strings, and that collision refuses in the safe direction.
# The word list is the feed's ATTESTED vocabulary, not a guess: the
# coverage lens's 2026-09-02 capture (scratchpad/tee, the RN1 probe)
# spells 'itc-udi-ven-2026-09-02-first-half-total-0pt5',
# 'elc-qpr-car-2026-09-02-halftime-result' and '...-halftime-result-
# home'; 'halftime' was missing from the first cut and its sibling
# shape '...-halftime-total-o0pt5' typed as the GAME total (review of
# the mapping unit, 2026-09-03, major). 'ht' is the same word in the
# pinned 'epl-x-…-ht-over-1pt5' shape; that pin read it as the game
# total and deferred the reading to its own review — this is that
# review, and it reads prop.
#
# The second line is the NATURAL SIBLINGS of the attested words, not
# attested themselves (re-review of the mapping unit, 2026-09-03,
# minor): '1sthalf'/'h1', '1q', 'f5'/'innings', 'p1', 'sets' each typed
# as the GAME market one spelling over from a word already in the set
# ('...-h1-total-110pt5' -> total, '...-p1-moneyline' -> moneyline,
# '...-sets-2-0' -> spread). The re-review deferred them to census
# attestation; they are added now instead, because the two errors are
# not symmetric on a money path: a token the feed never uses costs
# nothing, a token the feed uses and this set lacks is a wrong-market
# copy at full size, and a team code that happens to spell one of these
# refuses (prop) rather than trades — the safe direction, exactly as
# the lone-token rule below. Every entry is a literal token; no stems.
_SEGMENT_TOKENS = frozenset({"first", "half", "halftime", "ht", "1h",
                             "2h", "fh", "sh", "quarter", "q1", "q2",
                             "q3", "q4", "period", "set",
                             "1sthalf", "2ndhalf", "firsthalf",
                             "secondhalf", "h1", "h2", "1q", "2q", "3q",
                             "4q", "f5", "inning", "innings", "p1", "p2",
                             "p3", "sets"})


def _post_date_tokens(parts: list[str]) -> list[str] | None:
    """Tokens after the YYYY-MM-DD triple, or None if no date found."""
    for i in range(len(parts) - 2):
        if (_DATE_RE.match(parts[i]) and parts[i + 1].isdigit()
                and parts[i + 2].isdigit()):
            return parts[i + 3:]
    return None


def market_type_of(slug: str) -> str:
    """Market type from either slug grammar.

    PMUS venue grammar keys on the kind prefix: atc/aec moneyline, asc
    spread, tsc total, astatc derivatives (ftts=btts, es=exact score).

    The WHALE FEED's slugs are kindless and league-led (audit
    2026-08-06: 'mlb-nyy-bos-2026-07-22...') — there the market type
    lives in the post-date suffix: empty or a single team code is a
    moneyline; 'o8pt5'/'u10' totals; a bare line ('3pt5', 'pos-2pt5',
    optionally after a team code) is a spread; 'es-2-0' exact score;
    'ftts'/'btts' both-teams-to-score. Anything unrecognized returns
    'unknown' — NEVER silently a tradeable type."""
    s = (slug or "").lower()
    parts = [p for p in s.split("-") if p]
    if not parts:
        return "unknown"
    kind = parts[0]
    if kind in ("atc", "aec"):
        return "moneyline"
    if kind == "asc":
        return "spread"
    if kind == "tsc":
        return "total"
    if kind == "cpc":
        return "crypto"
    if kind == "astatc":
        if "ftts" in parts:
            return "btts"
        if "es" in parts:
            return "exact_score"
        return "prop"
    if kind in _KINDS:
        return "unknown"
    # Kindless feed grammar: type from the post-date suffix.
    suffix = _post_date_tokens(parts)
    if suffix is None:
        return "unknown"          # undatable slug: fail closed
    if not suffix:
        return "moneyline"        # bare event slug
    if "ftts" in suffix or "btts" in suffix:
        return "btts"
    if suffix[0] == "es" or ("exact" in suffix and "score" in suffix):
        return "exact_score"
    # WORD-FORM derivatives (capture-leak trace 2026-08-17): the feed
    # also spells types out — 'exact-score-2-3', 'team-total-home-1pt5',
    # 'corners-over-9pt5', 'player-points-x-25pt5'. These used to fall
    # through to the bare-line fallback and classify as SPREAD, which
    # let exact-scores through spread cells and props past the prop
    # block. Team totals and corners/cards/player lines are PROP class
    # (a team total is not the game-total bet); a spelled-out 'total'
    # without 'team' is the game total.
    if "player" in suffix or "corners" in suffix or "cards" in suffix:
        return "prop"
    if "team" in suffix and ("total" in suffix or "totals" in suffix):
        return "prop"
    # A SEGMENT MARKET IS NEVER THE GAME MARKET (to-a-tee program Phase
    # 2, owner order 2026-09-02 "I want us to match everything ...
    # mirror the whales to a tee"; the coverage lens's §4(d) finding and
    # its engineering refutation). The feed spells a first-half total as
    # 'itc-udi-ven-2026-09-02-first-half-total-0pt5', and the word
    # 'total' in that suffix typed it as the GAME total: PREFIX_FOR_TYPE
    # then offered the full-game tsc- rows, and a 1H over 0.5 could
    # resolve onto a game total at the same line — a different bet at
    # score 1.0, the wrong-market class, not a refusal. A suffix that
    # names a segment (a half, a quarter, a period, a set) is a
    # derivative of the game market, which is prop class here: prop is
    # blocked for everyone (BLOCKED_TYPES) and PREFIX_FOR_TYPE carries no
    # entry for it, so the pick refuses instead of mis-routing. Placed
    # BEFORE the moneyline/spread/total word tests so a segment
    # moneyline ('period-1-moneyline') or spread ('q1-spread-neg-2pt5')
    # is caught the same way. A LONE segment token is a segment too:
    # 'lmx-ame-san-2026-08-29-fh' typed as the team-code pick side, and
    # premap.resolve then mapped that first-half pick onto the game's
    # aec- moneyline row with source 'premap' (reproduced 2026-09-03
    # against the sweep's own row builder) — the wrong-market copy the
    # exact lanes refuse by their suffix gates and the premap lane did
    # not. 'ht' and 'halftime' joined the set on the mapping unit's
    # review (2026-09-03): the feed attests 'halftime' outright and the
    # old 'ht-over-1pt5 is the game total' pin was the same wrong-market
    # reading one abbreviation over.
    if any(t in _SEGMENT_TOKENS for t in suffix):
        return "prop"
    # SPELLED MONEYLINE. A bare team code types as moneyline, but the
    # feed also spells it out — and 'moneyline' is nine characters, so
    # it hit the >4 unknown-word guard below and returned "unknown",
    # which PREFIX_FOR_TYPE has no entry for, so resolve refused before
    # any matcher ran. Same omission as the word-form 'spread' fixed
    # earlier today, in the other direction.
    if ("moneyline" in suffix or "h2h" in suffix or "1x2" in suffix
            or ("money" in suffix and "line" in suffix)):
        return "moneyline"
    # Spelled both-teams-to-score, beside the existing ftts/btts test.
    if "both" in suffix and "teams" in suffix and "score" in suffix:
        return "btts"
    # WORD-FORM SPREAD (census 2026-08-25). 'total' was spelled out and
    # handled; 'spread' was not, so 'spl-sha-riy-2026-08-25-spread-away
    # -1pt5' fell all the way to the >4-character unknown-word guard and
    # returned "unknown" — and unknown is never tradeable, so every
    # word-form spread in the feed was refused before it reached a
    # matcher. Same omission, same shape, one word apart.
    #
    # A side qualifier does NOT make it a prop the way it does for a
    # total: a game total split by team is a different bet (a team
    # total), but a spread is ALWAYS stated from one side — 'away -1.5'
    # is the game spread, not a derivative.
    if ("spread" in suffix or "spreads" in suffix
            or "handicap" in suffix or "handicaps" in suffix):
        return "spread"
    # PLURALS, and the venue's own total-games token. `tg` currently
    # falls to the bare-line fallback and types a tennis TOTAL as a
    # SPREAD, which is not a refusal — it is a wrong-MARKET route.
    if ("total" in suffix or "totals" in suffix or "tg" in suffix
            or "games" in suffix):
        return "total"
    # WORD-FORM OVER/UNDER (leak-hunt round 3, 2026-08-24): 'over' is
    # four characters, so it slipped past the >4 unknown-word guard
    # below and fell through to the bare-line fallback — classifying
    # 'over-2pt5' as SPREAD. With the market-type gate live that routes
    # a TOTALS bet onto the game's SPREAD market. ('under', at five
    # characters, was caught by the guard and returned unknown, so the
    # same bet was blocked or mis-typed depending only on which word
    # the feed used.) A side-qualified over/under is a TEAM total,
    # which is prop class, not the game total.
    if any(t in ("over", "under", "ou") for t in suffix):
        if any(t in ("home", "away", "h", "a") for t in suffix):
            return "prop"
        return "total"
    if any(_TOTAL_RE.match(t) for t in suffix):
        return "total"
    # A line token is a spread ONLY in the company of nothing but short
    # team codes — any longer unrecognized word beside it means a market
    # type this parser does not know, and unknown is never tradeable.
    if any(t.isalpha() and len(t) > 4 for t in suffix):
        return "unknown"
    if any(_LINE_RE.match(t) for t in suffix):
        return "spread"
    if len(suffix) == 1 and suffix[0].isalpha():
        return "moneyline"        # team-code pick side
    return "unknown"


def league_of(slug: str) -> str:
    parts = [p for p in (slug or "").lower().split("-") if p]
    if not parts:
        return ""
    if parts[0] in _KINDS and len(parts) > 1:
        return parts[1]
    return parts[0]


def sport_of(slug: str) -> str:
    """Sport bucket for a slug's league. Leagues outside the named US
    majors and tennis are the soccer/other bucket (the donor data cannot
    distinguish them further)."""
    lg = league_of(slug)
    if not lg:
        return ""
    return SPORT_OF.get(lg, "soccer")


def copy_verdict(whale: str, slug: str,
                 price: float | None = None) -> str | None:
    """None if this copy is allowed, else WHICH clause refused it.

    WHY THE REASON EXISTS (2026-08-31). The pre-INSERT census landed
    and reported `cell_gate|rn1: 198` — the largest genuine block on
    the roster's only whale, since his other 530 refusals are exits
    being correctly classified. But "the cell gate refused it" names a
    FUNCTION, not a cause: this gate has six independent ways to say
    no, and rn1 is in UNRESTRICTED, so four of them cannot be why.
    Reading the constants said "almost certainly props" — and almost
    certainly is not a measurement, which is the whole standard here.

    Same bodies, same order, same returns as before; only the value
    changes from False to a name. copy_allowed is now defined in terms
    of this, so the two cannot drift.
    """
    w = (whale or "").strip().lower()
    if not w:
        return "no_whale"
    if w in PAUSED:
        return "whale_paused"
    if sport_of(slug) in HALTED_SPORTS:
        return "sport_halted"
    if market_type_of(slug) in BLOCKED_TYPES:
        return "market_type_blocked"
    if sport_of(slug) in ("soccer", "esports"):
        # esports rode the soccer bucket until 2026-08-21; keeping the
        # floor on it preserves the exact pre-split behavior for the
        # UNRESTRICTED whales that copy it.
        try:
            if price is None or float(price) < SOCCER_PRICE_FLOOR:
                return "soccer_price_floor"
        except (TypeError, ValueError):
            return "soccer_price_unreadable"
    if w in UNRESTRICTED:
        return None
    cells = CELLS.get(w)
    if not cells:
        return "no_cells_for_whale"
    sport = sport_of(slug)
    if not sport:
        return "sport_unknown"
    if (sport, market_type_of(slug)) not in cells:
        return "cell_not_allowed"
    band = ENTRY_BAND.get(w)
    if band is not None:
        # A banded whale with no readable price is REFUSED — the band is
        # the protection, and an absent price must not disable it.
        if price is None:
            return "band_needs_price"
        try:
            px = float(price)
        except (TypeError, ValueError):
            return "band_price_unreadable"
        if not (band[0] <= px <= band[1]):
            return "outside_entry_band"
    return None


def copy_allowed(whale: str, slug: str, price: float | None = None) -> bool:
    """May `whale`'s position in `slug` be copied at his `price`?
    Cell-level gate; fails closed on anything unrecognized.

    Defined in terms of copy_verdict so the boolean and the reason can
    never disagree — a reason that drifts from the decision it explains
    is worse than no reason, because it is believed.
    """
    return copy_verdict(whale, slug, price) is None


def kalshi_min_ask(whale: str, slug: str) -> float:
    """Minimum Kalshi ask for this cell (fee-viability carve-out);
    0.0 = no extra constraint."""
    return KALSHI_MIN_ASK.get(((whale or "").strip().lower(),
                               sport_of(slug)), 0.0)


# ── THE VENUE CANDIDATE GRAMMAR (moved here from live_executor
# 2026-09-03; to-a-tee program Phase 2, owner order 2026-09-02 "mirror
# the whales to a tee"; the coverage lens's §4(b) recommendation).
#
# The copy lane, the underdog sleeve, the mirror's coverage report and
# the runner-side MIRRORCOVER job all need ONE feed-slug -> US-slug
# candidate grammar, and live_executor imports asyncpg and settings at
# module level, so a runner could not import the grammar without
# installing the whole backend — which is how the mirror came to run
# only premap plus the ledger and never the exact lane the copy lane
# trades under quarantine. This module is pure (re, hashlib and the
# unicode fold below); live_executor re-exports every name so each
# existing import site and every source pin keeps working. The bodies
# are verbatim — only the lazy `league_of` import became the local
# name, because the definition now lives in the same file.

# Tennis league code translation, feed -> US venue. The US venue splits
# ITF by tour ('itfwo' women / 'itfme' men) where the feed says 'itf';
# unknown codes are enumerated (a wrong guess is a 404, never a trade).
_TENNIS_LEAGUES = {"atp", "wta", "itf", "itfwo", "itfme", "chal"}
_TENNIS_US_CODES = {"itf": ["itfwo", "itfme", "itf"],
                    "chal": ["chal", "atpchal"]}


def _abbrev_player(name: str) -> str | None:
    """US-venue tennis token: first 3 of first name + first 3 of last.
    Proven against live fills — 'Dusan Lajovic' is 'duslaj' in
    aec-atp-duslaj-benbon-2026-08-11, 'Rafael Jodar' is 'rafjod',
    'Sinja Kraus' is 'sinkra'. Unicode folds ('João' -> 'joa');
    single-token names refuse (no grammar evidence for them)."""
    import unicodedata as _ud

    folded = _ud.normalize("NFKD", name or "").encode(
        "ascii", "ignore").decode().lower()
    toks = _re.findall(r"[a-z]+", folded)
    if len(toks) < 2 or len(toks[0]) < 3 or len(toks[-1]) < 3:
        return None
    return toks[0][:3] + toks[-1][:3]


def _tennis_candidates(title: str | None, global_slug: str) -> list[str]:
    """US aec- candidates for a tennis match, built from the PLAYER
    NAMES in the title — the feed's slug uses surnames while the US
    grammar abbreviates 'First Last' to 6 chars, so slug-to-slug
    translation cannot work for tennis (1,730 ITF + 1,249 ATP + 623
    WTA moneylines dead in the funnel, 2026-08-13). Both player orders
    are generated (home/away order is the venue's choice, not the
    title's) and the outcome-similarity floor downstream remains the
    side authority — a colliding abbreviation still has to present the
    right player NAME to be ordered."""
    s = (global_slug or "").lower()
    m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
    if not m:
        return []
    # THE LEAGUE IS NOT THE FIRST SEGMENT (2026-08-26).
    #
    # This read head[0] and compared it against _TENNIS_LEAGUES. The
    # first segment of one of these slugs is the KIND prefix -- aec,
    # atc, tsc, asc, cpc, astatc -- and the league is the segment AFTER
    # it. league_of has always known that; this function carried a
    # second, wrong copy of the same decision.
    #
    # So for every real tennis slug head[0] was 'aec', the gate refused
    # it, and the function returned NO CANDIDATES:
    #
    #   aec-atp-harwen-stetra-2026-08-24  ->  head[0]='aec'  ->  0
    #   atp-harwen-stetra-2026-08-24      ->  head[0]='atp'  ->  2
    #
    # Only the second shape ever worked and the feed does not produce
    # it. resolve_market_exact was therefore NEVER CALLED for tennis:
    # every tennis copy fell straight through to the fuzzy resolver, and
    # fuzzy output is exactly what the quarantine refuses. Tennis is 48%
    # of the recent unmapped funnel -- 4,919 ATP, 2,425 WTA and 2,168
    # ITF rows in seven days -- and all of it died on this line.
    #
    # league_of is now the single definition. A slug with no kind prefix
    # still resolves, so the shape that used to work still does.
    lg = league_of(s)
    if lg not in _TENNIS_LEAGUES:
        return []
    date = m.group(0)
    # LAST colon: 'Tennis: ATP Cincinnati: A vs B' keeps only the
    # matchup (review 2026-08-13 — a first-colon split swallowed the
    # tournament word into the first player's token).
    body = (title or "").rsplit(":", 1)[-1]
    # Doubles refuse outright: 'A / B vs C / D' has no singles grammar,
    # and a fabricated token is a live probe into the 6-char slug space.
    if "/" in body:
        return []
    players = _re.split(r"\s+vs\.?\s+", body, flags=_re.I)
    if len(players) != 2:
        return []
    a, b = (_abbrev_player(p) for p in players)
    if not a or not b or a == b:
        return []
    codes = list(_TENNIS_US_CODES.get(lg, [lg]))
    if lg == "itf":
        # Tour hint from the title ('ITF W15 ...' / 'Women' vs 'M25' /
        # 'Men') puts the likelier code first; both are still tried.
        tl = (title or "").lower()
        if _re.search(r"\bm\d{2}\b|\bmen\b", tl) and "women" not in tl:
            codes = ["itfme", "itfwo", "itf"]
    out: list[str] = []
    for lg in codes:
        out.append(f"aec-{lg}-{a}-{b}-{date}")
        out.append(f"aec-{lg}-{b}-{a}-{date}")
    return out


def _us_slug_candidates(global_slug: str, outcome: str) -> list[str]:
    """US-venue slug candidates for a global market, most exact first.

    The global feed's slugs are kindless and league-led
    ('atp-ruud-fonseca-2026-08-07'); the US venue keys the same game as
    'atc-<league>-<a>-<b>-<date>-<side>' (per-side team contract) and
    'aec-<league>-<a>-<b>-<date>' (the two-outcome event contract). The
    side code is chosen only when exactly ONE of the slug's two codes
    matches the outcome name — ambiguity falls through to the aec form,
    whose own outcome-similarity floor disambiguates."""
    out: list[str] = []
    s = (global_slug or "").lower()
    m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        head = [t for t in s[:m.start()].strip("-").split("-") if t]
        if len(head) == 3:
            lg, a, b = head
            date = m.group(0)
            ol = (outcome or "").lower()
            words = ol.split()

            def _hits(code: str) -> bool:
                return code in ol or any(w.startswith(code)
                                         or code.startswith(w)
                                         for w in words)

            sides = [c for c in (a, b) if _hits(c)]
            if len(sides) == 1:
                out.append(f"atc-{lg}-{a}-{b}-{date}-{sides[0]}")
            out.append(f"aec-{lg}-{a}-{b}-{date}")
    if s:
        out.append(s)
    return out
