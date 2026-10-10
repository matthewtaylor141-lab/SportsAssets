"""Atomic Pinnacle h2h source selection from the existing leased WS cache.

No socket, database, venue client, order or policy control lives here. The
collector still proves venue identity, settlement, executable depth and fees.
The other odds provider supplies discovery and genuinely independent books;
its Pinnacle entry is replaced, never counted as a second Pinnacle book.
"""
from __future__ import annotations

from datetime import datetime
import functools
import math
import re
import time
import unicodedata

from . import pinnapi_feed as F
from . import pinnapi_names as N

PROVIDER = "pinnapi.com/raw-websocket"
LEGACY_PROVIDER = "the-odds-api.com/v4"
VERSION = "PINNAPI_PRIMARY_H2H_V1"
START_TOLERANCE_S = 90 * 60
#: PinnAPI's OWN sport ids, from its public documentation ("## Sport IDs --
#: Stable integer mapping": 1 Soccer, 5 Football, 6 Baseball), read from
#: https://pinnapi.com/llms-full.txt at 2026-10-04T18:56:20Z by fetch-docs run
#: 37226335697 (job 111506643056, 56,250 bytes, sha256 162705de...d394d;
#: excerpt in tests/fixtures/pinnapi_docs_2026_10_04.json). R30A adds
#: football: until now the full-game NFL line was never even matched here,
#: so no NFL valuation could carry a PinnAPI probability.
#:
#: WHICH MARKET. The same documentation names period `num_0` "full match"
#: (its example's description is "Game") and `money_line` as {home, away,
#: draw?}. Pinnacle's American Football rules make the Game period include
#: overtime ("Bets on the Game and 2nd Half-periods include points scored in
#: overtime."), which is the venue contract's own "Overtime is included if
#: played." So the NFL line read is the period-0 money line with EXACTLY the
#: two teams. A draw-priced football line is the regulation market and is
#: refused by name (FOOTBALL_DRAW_PRICED), never de-vigged as the game line.
#: (integration) every sport the inc-pinnapi scope subscribes is matched; the
#: football rule above still governs the football line.
R_FOOTBALL_DRAW_PRICED = "PINNAPI_PRIMARY_FOOTBALL_LINE_PRICES_A_DRAW"
#: PinnAPI's OWN sport ids ("## Sport IDs -- Stable integer mapping";
#: tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04.json): every sport
#: the R30A scope subscribes (pinnapi_feed_runtime.SCOPE_SPORTS), so a
#: subscribed sport is never refused PINNAPI_PRIMARY_SPORT_UNSUPPORTED before
#: its fixture is even looked up. Matching a fixture prices nothing by
#: itself: the de-vig's supported set, the venue-native winner types and the
#: settlement comparison still decide, each refusing by its own name.
SPORTS = {"baseball": 6, "soccer": 1, "football": 5, "basketball": 3,
          "hockey": 4, "tennis": 2}
FAMILY_OF_SPORT = {v: k for k, v in SPORTS.items()}
R_NO_EXACT = "PINNAPI_PRIMARY_NO_EXACT_FIXTURE"
#: the feed holds no record naming either team near the start
#: (pinnapi_names.absence): the fixture is not in the feed
R_NOT_IN_FEED = N.R_NOT_IN_FEED
#: the canonical tier's index key, the index's extra entries, and the two
#: name-match bases a selected quote's provenance records (pinnapi_names)
CANONICAL = "CANONICAL"
RAW = ("__raw_records__",)
EVICTED = ("__events_evicted__",)
#: (RC6.3 feed-retention) the cache's tombstone ring and fixture-claim
#: registry (pinnapi_feed.Tombstones / FixtureClaims), carried by reference
#: so the index path and the scan path read the same state at call time
TOMBSTONES = ("__tombstones__",)
CLAIMS = ("__claims__",)
#: (RC6) the index's per-(sport, family) absence pass, built on first miss
ABSENCE = "__absence__"
MATCH_EXACT = "EXACT_FOLDED_NAMES"
MATCH_CANONICAL = "CANONICAL_NAMES"


def epoch(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (float, int)):
            out = float(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
            out = dt.timestamp()
        return out if math.isfinite(out) else None
    except (ValueError, TypeError, OverflowError):
        return None


def name(value):
    # Full structured names only. No substring/abbreviation/geographic guess.
    # ONE ORTHOGRAPHIC EQUIVALENCE (P0 coverage, 2026-10-06): the conjunction
    # is spelled "&", "and" or not at all ("Bosnia & Herzegovina", "Bosnia
    # and Herzegovina", "Bosnia-Herzegovina") by different sources for the
    # same name; the metered provider's "&" form found no feed fixture
    # (PINNAPI_PRIMARY_NO_EXACT_FIXTURE, UEFA Nations League 2026-10-05). The
    # conjunction is dropped from every name; every other token must still be
    # identical, in order.
    #
    # (RC6) ONCE PER NAME, NOT PER CALL. The fold is a pure function of the
    # text `str(value or "")`, and `fixture_index` folds both names of every
    # cached fixture for every registration batch, on the API's event loop:
    # 60 ms for 2,600 events on a quiet local core (LOCAL BENCHMARK ONLY;
    # 6-8x that on the production CPU, see test_rc6_api_responsive_offloop),
    # nearly all of it re-folding names it folded the batch before. The memo
    # is keyed by that text, so it answers exactly what the fold answers;
    # bounded at pinnapi_names.MEMO_NAMES texts.
    return _name_of_text(str(value or ""))


@functools.lru_cache(maxsize=N.MEMO_NAMES)
def _name_of_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(t for t in re.findall(r"[^\W_]+", text.replace("&", " "))
                    if t != "and")


#: (RC6) the (name text, family) pairs `warm_names` has folded, so a batch
#: knows which of its cache's names are still cold; bounded like the memos
_WARM: set = set()


def cold_names(cache) -> list:
    """[(name text, family)] of the cache's participants that `warm_names`
    has not folded yet -- set lookups only, cheap enough for the loop."""
    out, seen = [], set()
    for ev in list(cache.events.values()):
        if not isinstance(ev, dict):
            continue
        fam = FAMILY_OF_SPORT.get(ev.get("sport_id"))
        for p in ev.get("participants") or []:
            if not (isinstance(p, dict) and p.get("name")):
                continue
            key = (str(p.get("name")), fam)
            if key not in _WARM and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def warm_names(pairs) -> int:
    """PURE but for the memos: fold, canonicalise and tokenise each (name,
    family) exactly as `fixture_index` and the absence pass will, so the
    batch that follows finds them memoised. Run off the loop (on the CPU
    lane, pinnapi_reactive.warm_names); the answers are the memos' own."""
    if len(_WARM) + len(pairs) > N.MEMO_NAMES:
        _WARM.clear()
    for text, fam in pairs:
        name(text)
        N.canonical(text, fam, side="feed")
        if fam:
            N._tokens(text, fam)
        N._acronym(text)
        _WARM.add((text, fam))
    return len(pairs)


def fixture_index(cache) -> dict:
    """{(sport id, frozenset of the two folded names): [(fixture id, home,
    away, start)]} over ONE `fixture_view` of the cache, in its order -- and
    the same under (sport id, CANONICAL, frozenset of the two canonical feed
    names) for the canonical tier (`pinnapi_names`), plus the raw records
    and the cache's eviction count for the absence check.

    THE REGISTRATION COST (adversarial verification, finding 1, fix stage
    2026-10-05): `match_event` rebuilt the whole fixture view for every
    reactive registration -- ~7 ms per seed on 2,600 cached events, every
    discovery pass, on the API's event loop. A pass that registers many
    seeds builds this once; `match_event(index=...)` then answers exactly
    what the full scan answers (the same two names, the same sport, the same
    start tolerance), by lookup."""
    view, _skipped = F.fixture_view(cache.events)
    out: dict = {}
    for fx in view:
        h, a = name(fx.get("home")), name(fx.get("away"))
        if not h or not a or h == a:
            continue
        start = epoch(fx.get("startTime"))
        out.setdefault((fx.get("sport_id"), frozenset((h, a))), []).append(
            (fx["id"], h, a, start))
        fam = FAMILY_OF_SPORT.get(fx.get("sport_id"))
        ch = N.canonical(fx.get("home"), fam, side="feed")["name"]
        ca = N.canonical(fx.get("away"), fam, side="feed")["name"]
        if ch and ca and ch != ca:
            out.setdefault((fx.get("sport_id"), CANONICAL,
                            frozenset((ch, ca))), []).append(
                (fx["id"], ch, ca, start))
    out[RAW] = [dict(v) for v in cache.events.values() if isinstance(v, dict)]
    out[EVICTED] = _evicted(cache)
    out[TOMBSTONES] = getattr(cache, "tombstones", None)
    out[CLAIMS] = getattr(cache, "claims", None)
    return out


#: (RC6.1 api-stall2) the one memoised index: (cache, its key, index)
_CURRENT: list = [None]


def current_index(cache):
    """`fixture_index(cache)` for the cache's CURRENT generation, built at
    most once per generation; None when the cache keeps no generation or
    the index cannot be built (the caller then scans, as before).

    WHY (RC6.1 api-stall2). Production 2026-10-09 (render-ops logs run
    37949540216): the API event loop held 1.1 s at 15:00:21Z
    (pinnapi_feed.participants <- fixture_view <- _candidates <- _match_tier)
    and 1.9 s at 15:15:21Z (pinnapi_names.absence <- match_event <- select <-
    ext_pinnacle_loop.primary_pinnacle_h2h): `select` matched each event by
    scanning the whole cache -- one fixture view per name tier and, on a
    miss, the absence pass over every record -- and the reactive register
    scanned again. ~95 ms per refused event on 2,600 cached events on a
    quiet local core, against 0.05 ms by lookup once the index exists
    (~30 ms to build; LOCAL BENCHMARK ONLY).

    THE SAME ANSWER, BY CONSTRUCTION. `match_event(index=...)` answers what
    the scan answers over the cache the index was built from (fixture_index's
    docstring; test_rc6_api_responsive_reactive pins it), and the index is
    reused only while `cache.generation` -- bumped by every write of the
    cache's events (FeedCache) -- is the one it was built at."""
    gen = getattr(cache, "generation", None)
    if type(gen) is not int:
        return None
    try:
        # the generation, and what the index copies that a direct write
        # (a test's, never production's) could change without one
        key = (gen, id(cache.events), len(cache.events), _evicted(cache))
    except Exception:                                           # noqa: BLE001
        return None
    memo = _CURRENT[0]
    if memo is not None and memo[0] is cache and memo[1] == key:
        return memo[2]
    try:
        idx = fixture_index(cache)
    except Exception:                                           # noqa: BLE001
        return None
    _CURRENT[0] = (cache, key, idx)
    return idx


def _evicted(cache) -> int:
    try:
        return int((getattr(cache, "counts", None) or {})
                   .get("events_evicted", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _clock(cache) -> float:
    """The cache's wall clock (pinnapi_feed.FeedCache.clock; tests pin it),
    else time.time()."""
    clock = getattr(cache, "clock", None)
    try:
        return float(clock()) if callable(clock) else time.time()
    except Exception:                                           # noqa: BLE001
        return time.time()


def _claim(cache, hit, event, *, now) -> None:
    """(RC6.3 feed-retention) Record that the fixture `hit` IS this METERED
    event, by an exact one-to-one match (pinnapi_feed.FixtureClaims): the
    absence pass of a later miss then knows the record is another game. A
    PinnAPI-native seed claims nothing (its identity is the fixture itself);
    an event without an id claims nothing. Never raises."""
    try:
        claims = getattr(cache, "claims", None)
        if claims is None or not isinstance(event, dict):
            return
        if event.get("pinnapi_native") is not None or event.get("id") is None:
            return
        fid, labels = hit
        ev = cache.events.get(fid) if isinstance(
            getattr(cache, "events", None), dict) else None
        if not isinstance(ev, dict):
            return
        fixture_names = frozenset(N._fold(p.get("name"))
                                  for p in (ev.get("participants") or [])
                                  if isinstance(p, dict) and p.get("name"))
        claims.record(fid, event_id=event.get("id"),
                      names=frozenset((N._fold(event.get("home_team")),
                                       N._fold(event.get("away_team")))),
                      fixture_names=fixture_names, at=now)
    except Exception:                                           # noqa: BLE001
        return


def _candidates(cache, sid, home, away, *, index, canonical_tier, family):
    """The fixtures of sport `sid` that could carry {home, away}: from the
    index, exactly those two (folded or canonical) names; else every
    fixture of the sport with its names in the tier's form."""
    if index is not None:
        if canonical_tier:
            return index.get((sid, CANONICAL, frozenset((home, away))), ())
        return index.get((sid, frozenset((home, away))), ())
    view, _skipped = F.fixture_view(cache.events)
    out = []
    for fx in view:
        if fx.get("sport_id") != sid:
            continue
        if canonical_tier:
            h = N.canonical(fx.get("home"), family, side="feed")["name"]
            a = N.canonical(fx.get("away"), family, side="feed")["name"]
        else:
            h, a = name(fx.get("home")), name(fx.get("away"))
        out.append((fx["id"], h, a, epoch(fx.get("startTime"))))
    return out


def match_event(cache, event, family, *, index=None, explain=None):
    """((fixture id, {home, away} labels), None) for the ONE fixture whose
    two participants are exactly the event's, starting within the
    tolerance; else (None, named reason). R30A RC3: a fixture is read from
    `pinnapi_feed.fixture_view`, so a prematch matchup whose game is in play
    is still matched (its live-phase child prices it), and a live game
    whose parent left the cache is its own fixture -- a child record is no
    longer skipped for carrying a parentId. `index` (`fixture_index` of the
    same cache) replaces the scan with a lookup; the answer is the same.

    TWO TIERS, ONE IDENTITY (P1 first-loss census, `pinnapi_names`). The
    folded names first, exactly as before; only when they find NO fixture,
    the canonical names (one deterministic rewrite of each rendering, closed
    tables, never a similarity) -- under every same condition: both
    participants one-to-one, the sport, the start tolerance, exactly one
    fixture (two are AMBIGUOUS by name). `explain` (a dict, optional)
    receives the basis: {"name_match": EXACT_FOLDED_NAMES | CANONICAL_NAMES,
    "rules": [...]}, or, on a miss, the absence evidence. When no fixture
    matches either way AND the feed holds no record naming either team
    anywhere near the start (`pinnapi_names.absence`), the reason is
    PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM; any doubt keeps
    PINNAPI_PRIMARY_NO_EXACT_FIXTURE.

    `event["sport_key"]` (the metered provider's competition, carried on
    its events) scopes the competition-only renderings; a validate call
    passes the one the selection recorded.

    (RC6.3 feed-retention) A hit by a METERED event is recorded as that
    fixture's claim (`_claim`, pinnapi_feed.FixtureClaims); a miss asks the
    absence pass about the cache's EVICTED records (its tombstone ring) and
    ignores records a metered event that is provably another game already
    is -- the asking event's own id is handed in, so its own earlier claim
    never covers a record against it -- see pinnapi_names.NEAR_START_STOP.
    The match rules are unchanged."""
    sid = SPORTS.get(family)
    start = epoch(event.get("commence_time"))
    home, away = name(event.get("home_team")), name(event.get("away_team"))
    if sid is None:
        return None, "PINNAPI_PRIMARY_SPORT_UNSUPPORTED"
    if start is None or not home or not away or home == away:
        return None, "PINNAPI_PRIMARY_FIXTURE_UNPROVED"
    ex = explain if isinstance(explain, dict) else {}
    now = _clock(cache)
    hit, why = _match_tier(cache, event, sid, start, home, away,
                           index=index, canonical_tier=False, family=family)
    if why != R_NO_EXACT:
        if why is None:
            ex.update(name_match=MATCH_EXACT, rules=[])
            _claim(cache, hit, event, now=now)
        return hit, why
    sport_key = event.get("sport_key")
    ch = N.canonical(event.get("home_team"), family, sport_key)
    ca = N.canonical(event.get("away_team"), family, sport_key)
    if ch["name"] and ca["name"] and ch["name"] != ca["name"]:
        hit, why = _match_tier(cache, event, sid, start, ch["name"],
                               ca["name"], index=index, canonical_tier=True,
                               family=family)
        if why != R_NO_EXACT:
            if why is None:
                ex.update(name_match=MATCH_CANONICAL,
                          rules=sorted(set(ch["rules"] + ca["rules"])),
                          canonical={"home": ch["name"], "away": ca["name"]},
                          version=N.VERSION, sport_key=sport_key)
                _claim(cache, hit, event, now=now)
            return hit, why
    records = index.get(RAW) if index is not None else \
        [v for v in cache.events.values() if isinstance(v, dict)]
    evicted = index.get(EVICTED, 0) if index is not None else _evicted(cache)
    # (RC6) a batch's misses share ONE inverted pass over the raw records
    # (pinnapi_names.AbsenceIndex): the same answer, without a full scan of
    # the feed per seed
    prepared = None
    if index is not None:
        prepared = index.get((ABSENCE, sid, family))
        if prepared is None:
            prepared = index[(ABSENCE, sid, family)] = N.AbsenceIndex(
                records, sid, family)
    # (RC6.3 feed-retention) the evicted records themselves, the ring's
    # own state, and which records another metered event already is -- the
    # same objects on the index path and the scan path (fixture_index)
    ring = index.get(TOMBSTONES) if index is not None else \
        getattr(cache, "tombstones", None)
    claims = index.get(CLAIMS) if index is not None else \
        getattr(cache, "claims", None)
    tomb, overflow, unaccounted = [], False, None
    if ring is not None:
        try:
            tomb = ring.within(now)
            overflow = bool(ring.overflow_inside(now))
            unaccounted = int(ring.unaccounted(evicted))
        except Exception:                                       # noqa: BLE001
            # an unreadable ring keeps every doubt: the bare counter rules
            tomb, overflow, unaccounted = [], False, None
    ab = N.absence(records, sport_id=sid, start=start,
                   home=event.get("home_team"), away=event.get("away_team"),
                   family=family, evicted=evicted,
                   tolerance_s=START_TOLERANCE_S, prepared=prepared,
                   tombstones=tomb if ring is not None else None,
                   claims=claims, overflow=overflow, unaccounted=unaccounted,
                   now=now, event_id=event.get("id"))
    ex.update(absence=ab)
    return None, (N.R_NOT_IN_FEED if ab["absent"] else R_NO_EXACT)


def _match_tier(cache, event, sid, start, home, away, *, index,
                canonical_tier, family):
    """One tier's answer: the one fixture whose two names (in the tier's
    form) are exactly {home, away}, inside the tolerance; else a named
    reason. The labels are always the EVENT's own names."""
    hits = []
    for eid, h, a, other_start in _candidates(
            cache, sid, home, away, index=index,
            canonical_tier=canonical_tier, family=family):
        if other_start is None or abs(start - other_start) > START_TOLERANCE_S:
            continue
        if h != a and {home, away} == {h, a}:
            hits.append((eid, {"home": event["home_team"] if home == h
                               else event["away_team"],
                               "away": event["away_team"] if away == a
                               else event["home_team"]}))
    if len(hits) > 1:
        # THE SEED'S OWN FIXTURE (P0 coverage, 2026-10-06: Deportivo Riestra
        # vs Central Cordoba, two feed fixtures of the same two names within
        # the tolerance). A PinnAPI-native seed IS one feed fixture: the
        # discovery matched exactly that fixture id one-to-one to its venue
        # event (and seeds nothing when two fixtures claim one venue event).
        # So when that id is among the name matches it is the fixture, by
        # identity, not by preference; a metered event, or a seed whose id is
        # not among them, stays AMBIGUOUS by name.
        own = (event.get("pinnapi_native") or {}).get("fixture_id") \
            if isinstance(event.get("pinnapi_native"), dict) else None
        mine = [h for h in hits if own is not None and str(h[0]) == str(own)]
        if len(mine) == 1:
            return mine[0], None
    if len(hits) != 1:
        return None, ("PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS" if hits
                      else R_NO_EXACT)
    return hits[0], None


def book_depth(event, prices, sharp_books, *, at, max_age_s):
    """Distinct, finite, current independent books for EACH outcome.

    PinnAPI and the-odds-api's Pinnacle are ONE book. Repeated payload rows
    cannot satisfy the two-book minimum. Old/unknown second-book timestamps
    do not corroborate a fresh WS price.
    """
    evidence = {n: {"pinnacle"} for n in prices}
    wanted = {name(n): n for n in prices}
    for b in event.get("bookmakers") or []:
        key = b.get("key")
        if key == "pinnacle" or key not in sharp_books:
            continue
        for m in b.get("markets") or []:
            stamp = epoch(m.get("last_update") or b.get("last_update"))
            if m.get("key") != "h2h" or stamp is None:
                continue
            if not 0 <= at - stamp <= max_age_s:
                continue
            for o in m.get("outcomes") or []:
                n = wanted.get(name(o.get("name")))
                try:
                    p = float(o["price"])
                except (KeyError, ValueError, TypeError):
                    continue
                if n is not None and math.isfinite(p) and p > 1:
                    evidence[n].add(key)
    return ({n: len(bs) for n, bs in evidence.items()},
            {n: sorted(bs) for n, bs in evidence.items()})


def select(cache, event, fallback, *, family, sharp_books, at,
           max_age_s=30.0, runtime_id=None, explain=None):
    """Prefer one complete current WS outcome set; otherwise named fallback.

    A fallback keeps its original clocks. It still faces all the collector's
    freshness rules; selecting it never certifies it as currently usable.

    `explain` (a dict, optional) receives the WS refusal reason whenever the
    WS price is not used -- ALSO when there is no fallback and None is
    returned. Without it that reason was discarded and the collector recorded
    the event as NO_PINNACLE_ON_EVENT, as if Pinnacle had no price (P0
    incident: 2,534 rows/day whose cause was a feed refusal -- no exact
    fixture, sport not subscribed, age unknown -- not an absent price).
    """
    def fail(reason, provenance=None):
        if isinstance(explain, dict):
            explain["reason"] = reason
            explain["provenance"] = provenance
        if fallback is None:
            return None
        out = dict(fallback)
        out["reference_input"] = {"version": VERSION,
                                  "provider": LEGACY_PROVIDER,
                                  "preferred_provider": PROVIDER,
                                  "fallback_reason": reason,
                                  "feed_read": provenance}
        return out

    if epoch(at) is None or epoch(max_age_s) is None or max_age_s < 0:
        return fail("PINNAPI_PRIMARY_CLOCK_INVALID")
    if cache is None or not cache.authority.granted or not cache.authority.synced:
        return fail(F.R_NO_AUTHORITY)
    if not isinstance(runtime_id, str) or not runtime_id:
        return fail("PINNAPI_PRIMARY_RUNTIME_UNIDENTIFIED")
    name_basis: dict = {}
    hit, reason = match_event(cache, event, family, explain=name_basis,
                              index=current_index(cache))
    if reason:
        return fail(reason, {"fixture_match": name_basis} if name_basis
                    else None)
    eid, labels = hit
    # THE RECORD THAT PRICES THE FIXTURE NOW: its live-phase child while in
    # play, else the fixture itself (R30A RC3)
    qid, why = cache.fixture_quote_id(eid)
    if why:
        return fail(why)
    got = cache.read(qid, F.FULL_GAME_MONEYLINE_KEY,
                     evaluated_ms=at * 1000, max_age_s=max_age_s)
    if not got.get("ok"):
        # THE READ'S OWN PROVENANCE RIDES WITH THE REFUSAL (explain
        # ["provenance"], and the fallback's reference_input["feed_read"]):
        # for a change-rule refusal that is the quiet-line evidence too
        # (pinnapi_feed.quiet_line_evidence; read back by
        # `quiet_line_evidence` below for the collector's ledger row)
        prov = got.get("provenance")
        if got.get("reason") == F.R_UNKNOWN_MARKET and \
                callable(getattr(cache, "market_list", None)):
            # WHAT THE FIXTURE'S PRICING RECORD DOES HOLD (red-team
            # closeout): the evidence ext_pinnacle_loop.
            # fixture_lists_no_moneyline names Pinnacle's own absence by
            prov = {"fixture_id": eid, "quote_event_id": qid,
                    "fixture_match": name_basis,
                    "market_list": cache.market_list(qid)}
        return fail(got.get("reason"), prov)
    q = got["quote"]
    if (epoch(q.change_ms) is None or epoch(q.received_ms) is None
            or q.received_ms > at * 1000):
        return fail("PINNAPI_PRIMARY_CLOCK_INVALID")
    if (q.market_type != "moneyline" or q.period != 0 or q.alternate
            or q.line is not None or q.sport_id != SPORTS[family]):
        return fail("PINNAPI_PRIMARY_NOT_FULL_GAME_H2H")
    expected = {"home", "away", "draw"} if family == "soccer" else {"home", "away"}
    odds = q.decimal_prices()
    if family == "football" and "draw" in odds:
        return fail(R_FOOTBALL_DRAW_PRICED)
    if set(odds) != expected or any(v is None or not math.isfinite(v) or v <= 1
                                   for v in odds.values()):
        return fail("PINNAPI_PRIMARY_INCOMPLETE_OUTCOMES")
    live = cache.events[qid].get("isLive")
    if not isinstance(live, bool) or q.stream != ("live" if live else "prematch"):
        return fail("PINNAPI_PRIMARY_PHASE_UNPROVED")
    labels["draw"] = "Draw"
    prices = {labels[d]: p for d, p in odds.items()}
    depth, evidence = book_depth(event, prices, sharp_books,
                                  at=at, max_age_s=max_age_s)
    prov = dict(got["provenance"], version=VERSION, provider=PROVIDER,
                runtime_id=runtime_id, feed_event_id=eid,
                quote_event_id=qid,
                market_key=q.key, stream=q.stream, raw_odds=dict(q.prices),
                outcome_labels=labels, independent_books=evidence,
                discovery_event_id=event.get("id"), family=family,
                discovery_home=event["home_team"], discovery_away=event["away_team"],
                discovery_start=event["commence_time"],
                discovery_sport_key=event.get("sport_key"),
                fixture_match=name_basis)
    return {"prices": prices, "depth": depth,
            # the change instant the 30 s rule measures from: the provider
            # stamp, or (only for a change in an unstamped frame) our
            # labelled observation of it -- provenance names which
            "observed_at": q.change_ms / 1000,
            "received_at": q.received_ms / 1000,
            "home": event["home_team"], "away": event["away_team"],
            "event_id": event.get("id"),
            "commence_time": event["commence_time"], "reference_input": prov}


#: ── QUIET LINE OR FROZEN FEED: THE EVIDENCE A REFUSED READ CARRIES (QL-1) ──
#:
#: `pinnapi_feed.read` puts three measured facts on every R_NO_CHANGE_TIME /
#: R_STALE refusal (would_pass_on_confirmation, no_observed_change_s,
#: age_since_confirmation_s; see QUIET LINE OR FROZEN FEED there). `select`
#: carries that provenance unchanged on `explain["provenance"]` and on the
#: metered fallback's reference_input["feed_read"]; this reads the evidence
#: back off either, by name, for the collector's ledger row. None for any
#: other refusal and for a provenance written before the fields existed (a
#: reader of an older release's record never invents them).
QUIET_LINE_REFUSALS = (F.R_NO_CHANGE_TIME, F.R_STALE)


def quiet_line_evidence(reason, provenance) -> dict | None:
    """{would_pass_on_confirmation, no_observed_change_s,
    no_observed_change_basis, age_since_confirmation_s, confirmation_limit_s}
    of a change-rule refusal's provenance, else None. Pure."""
    if reason not in QUIET_LINE_REFUSALS or not isinstance(provenance, dict):
        return None
    if not isinstance(provenance.get("would_pass_on_confirmation"), bool):
        return None
    out = {k: provenance.get(k) for k in F.QUIET_LINE_FIELDS}
    out["no_observed_change_basis"] = provenance.get("no_observed_change_basis")
    out["confirmation_limit_s"] = provenance.get("confirmation_limit_s")
    return out


#: ── THE RECHECK'S CLOCK GUARD: THE PRICE, NOT ITS LATEST RE-ASSERTION ──
#:
#: PRODUCTION (2026-10-08 01:40Z readback, release 08828d04): 319 refused
#: paper decisions over 151 contract sides (capital-authority blocker census
#: since 2026-10-06T05:53Z, stage DECISION only) and 2 events in the 24 h
#: first-loss census carried PINNAPI_PRIMARY_CLOCK_INVALID from this recheck
#: -- never from `select`, which always runs at time.time(); and after a
#: read that is ok, `received_ms > at` is the only branch here that can
#: fire (an unknown change time is refused by the read itself). The guard
#: compared the cache quote's `received_ms` with the decision instant `at`.
#: But `received_ms` is the arrival of the LATEST frame that carried the
#: market (pinnapi_feed: "provenance only"), and the cache re-puts every
#: market of a record on every frame: a live push that moves ANY market of
#: the event, or a prematch list re-asserting its prices, re-stamps the
#: money line's `received_ms` with its price, change instant and epoch
#: untouched. Every paper policy reads `at` BEFORE its awaited catalogue /
#: fixture / parameter reads and rechecks after them, so a re-assertion
#: landing inside those awaits refused an UNCHANGED price as a clock fault --
#: our own ordering, not the provider's clock. (The line-market recheck,
#: bettor_market_family.validate_pair, has no such guard at all.)
#:
#: CLOCK_INVALID_RULE. The substance is compared first, so a price that DID
#: change is PINNAPI_PRIMARY_INPUT_CHANGED by its real name (and so reaches
#: the supersession check). The clock guard then asks the question it exists
#: for: did WE HOLD this price at the instant it is evaluated at? Its
#: instant is the quote's `first_observed_ms` -- our receipt of the frame
#: that first carried this exact price on this epoch, carried unchanged by
#: every re-assertion -- or, where a quote carries none, `received_ms` as
#: before. An evaluation instant before we held the price is still refused
#: CLOCK_INVALID. The 30 s rule, its change instant and the instant it is
#: measured at are unchanged: `cache.read` still ages the price from its
#: change at `at`, and the recheck after the book read still runs.
def _held_since_ms(q) -> float:
    """Our receipt instant of the frame that first carried this exact price
    (`first_observed_ms`), else of the latest frame (`received_ms`)."""
    held = getattr(q, "first_observed_ms", None)
    return float(held) if epoch(held) is not None else float(q.received_ms)


def validate(cache, quote, *, at, max_age_s=30.0, runtime_id=None):
    """Recheck after awaited venue/rules reads, before any valuation can act.

    Never silently value old prices after an epoch, fixture or price change.
    Caller must remove actionable probability on failure: paper policies may
    intentionally disregard the collector's other admissibility refusals.
    """
    p = quote.get("reference_input") or {}
    if p.get("provider") != PROVIDER:
        return {"ok": True, "provider": LEGACY_PROVIDER}
    if cache is None or not runtime_id or runtime_id != p.get("runtime_id"):
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    if epoch(at) is None or epoch(max_age_s) is None or max_age_s < 0:
        return {"ok": False, "reason": "PINNAPI_PRIMARY_CLOCK_INVALID"}
    hit, why = match_event(cache, {
        "home_team": p["discovery_home"], "away_team": p["discovery_away"],
        "commence_time": p["discovery_start"],
        "sport_key": p.get("discovery_sport_key")}, p["family"],
        index=current_index(cache))
    if (why or hit[0] != p["feed_event_id"] or
            any(hit[1][d] != p["outcome_labels"].get(d) for d in ("home", "away"))):
        return {"ok": False, "reason": why or "PINNAPI_PRIMARY_FIXTURE_CHANGED"}
    qid = p.get("quote_event_id", p["feed_event_id"])
    now_qid, why = cache.fixture_quote_id(p["feed_event_id"])
    if why or now_qid != qid:
        # the game went in play (or its live record changed) since the
        # quote was selected: a different record prices it now
        return {"ok": False, "reason": why or "PINNAPI_PRIMARY_INPUT_CHANGED"}
    got = cache.read(qid, p["market_key"],
                     evaluated_ms=at * 1000, max_age_s=max_age_s)
    if not got.get("ok"):
        return {"ok": False, "reason": got.get("reason"),
                "provenance": got.get("provenance")}
    q = got["quote"]
    if epoch(q.change_ms) is None or epoch(q.received_ms) is None:
        return {"ok": False, "reason": "PINNAPI_PRIMARY_CLOCK_INVALID"}
    live = cache.events[qid].get("isLive")
    if (q.epoch != p["epoch"] or q.source_change_ms != p["source_change_ms"]
            or q.change_ms != p.get("change_ms", q.change_ms)
            or q.prices != p["raw_odds"] or q.stream != p["stream"]
            or not isinstance(live, bool)
            or q.stream != ("live" if live else "prematch")
            or q.market_type != "moneyline" or q.period != 0 or q.alternate
            or q.line is not None or q.sport_id != SPORTS[p["family"]]):
        return {"ok": False, "reason": "PINNAPI_PRIMARY_INPUT_CHANGED"}
    # THE PRICE WAS HELD AT THE EVALUATION INSTANT (CLOCK_INVALID_RULE).
    if _held_since_ms(q) > at * 1000:
        return {"ok": False, "reason": "PINNAPI_PRIMARY_CLOCK_INVALID"}
    return {"ok": True, "provider": PROVIDER,
            "provenance": got["provenance"],
            "receipt_to_evaluation_ms": at * 1000 - p["received_ms"]}


#: ── A VALUATION WHOSE PRICE HAS BEEN REPLACED (P1, 2026-10-06) ──────────
#: PRODUCTION: 98 paper decisions in ~16 min over ~39 in-play markets refused
#: PINNAPI_PRIMARY_INPUT_CHANGED, across the completed-game, exploration and
#: Derek strategies. The refusal is CORRECT -- in play Pinnacle re-prices
#: every few seconds, and `validate` must never value the price a valuation
#: was built on once the feed holds a different one. What is code-controlled
#: is the WASTE around it: one reactive job writes a valuation, decides it
#: with each strategy IN SEQUENCE (each awaiting its own venue book read),
#: then writes and decides the other side of the contract from the SAME
#: quote; a book retry decides the same old valuation seconds later; the
#: paper-pass backstop re-decides what the hook skipped. Meanwhile the feed's
#: newer price has already been queued on the reactive scheduler (coalesced
#: per fixture, served next), which values it afresh. Every strategy after
#: the first price move was recording a SOFTWARE first loss on a valuation
#: that a newer one was about to replace.
#:
#: `supersession` names that case and ONLY that case: the substance check
#: of `validate` failed (PINNAPI_PRIMARY_INPUT_CHANGED from the price/clock
#: comparison, not from the record change) while EVERYTHING ELSE about the
#: input is the same -- the runtime, the fixture, the record that prices it
#: (so not a phase change: a game going in play is a different record and
#: keeps its refusal), the market, the stream and the live flag -- and the
#: feed now holds a STRICTLY NEWER change of that market which itself reads
#: fresh inside the unchanged 30 s rule. Nothing is valued: the caller skips
#: the old valuation (it is never decided on its old price, and never on
#: the new one either) and only when the newer price is itself going to be
#: valued (queued for evaluation, or a newer valuation of the same contract
#: already written). An epoch change, a fixture change, a phase change, a
#: stale or unreadable quote, a lost authority: each keeps its own refusal.
#:
#: EPOCH CHURN IS NOT A CAUSE (checked, not assumed): a new socket epoch
#: CLEARS the cache (`pinnapi_feed.FeedCache.new_connection`, the only
#: caller of `FeedAuthority.grant`, from `pinnapi_owner`), so every quote on
#: a new epoch is first sight with no change time and reads
#: FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE until a frame really changes it
#: -- an epoch bump with identical substance never reaches the substance
#: comparison, and the epoch comparison stays.
R_SUPERSEDED = "PINNAPI_PRIMARY_VALUATION_SUPERSEDED_BY_A_NEWER_QUOTE"


def version_key(q) -> tuple:
    """A quote's evaluation version, as the reactive scheduler keys it
    (`pinnapi_reactive.version_of`): (epoch, change instant, prices)."""
    return (q.epoch, q.change_ms, tuple(sorted(q.prices.items())))


def supersession(cache, quote, *, at, max_age_s=30.0, runtime_id=None):
    """None, or the evidence that this valuation's money-line price has been
    REPLACED by a strictly newer, fresh price of the same market of the same
    record of the same fixture (see R_SUPERSEDED). Never raises; any doubt
    is None (the caller then keeps `validate`'s refusal)."""
    try:
        p = quote.get("reference_input") or {}
        if p.get("provider") != PROVIDER or p.get("version") != VERSION:
            return None
        check = validate(cache, quote, at=at, max_age_s=max_age_s,
                         runtime_id=runtime_id)
        if check.get("ok") or check.get("reason") != \
                "PINNAPI_PRIMARY_INPUT_CHANGED":
            return None
        qid = p.get("quote_event_id", p["feed_event_id"])
        now_qid, why = cache.fixture_quote_id(p["feed_event_id"])
        if why or now_qid != qid:
            return None                  # a different record: phase change
        got = cache.read(qid, p["market_key"], evaluated_ms=at * 1000,
                         max_age_s=max_age_s)
        if not got.get("ok"):
            return None                  # the newer price is not usable
        q = got["quote"]
        live = cache.events[qid].get("isLive")
        if (q.stream != p["stream"] or not isinstance(live, bool)
                or q.stream != ("live" if live else "prematch")
                or q.market_type != "moneyline" or q.period != 0
                or q.alternate or q.line is not None
                or q.sport_id != SPORTS[p["family"]]
                or q.epoch != p["epoch"]):
            return None
        old = p.get("change_ms", p.get("source_change_ms"))
        if epoch(q.change_ms) is None or epoch(old) is None \
                or not q.change_ms > old:
            return None                  # not strictly newer
        return {"reason": R_SUPERSEDED, "fixture_id": p["feed_event_id"],
                "quote_event_id": qid, "market_key": p["market_key"],
                "epoch": q.epoch, "version": version_key(q),
                "previous_change_ms": old, "newer_change_ms": q.change_ms,
                "previous_raw_odds": dict(p.get("raw_odds") or {}),
                "newer_raw_odds": dict(q.prices),
                "newer_quote_age_s": got["provenance"].get("quote_age_s")}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def stamp_record(rec, quote, check):
    """Provider identity and failure must survive the persisted-row boundary."""
    source = dict(quote.get("reference_input") or {})
    if not source:
        return
    source["decision_check"] = check
    rec.setdefault("settlement_comparison", {})["reference_input"] = source
    if source.get("provider") == PROVIDER:
        rec["provider"] = PROVIDER
        rec["valuation"]["provider"] = PROVIDER
    if not check.get("ok"):
        # A mere extra_refusal is insufficient: paper benchmark intentionally
        # reads probabilities even where other collector gates have refused.
        rec["probability"] = rec["probability_of_selection"] = None
        rec["valuation"]["probability"] = None
        rec["admissible"] = False
        rec["decision"] = "NO_TRADE"
        reason = check["reason"]
        rec["refusals"] = list(dict.fromkeys([reason] + list(rec.get("refusals") or [])))
        rec["why"] = reason + "; " + str(rec.get("why") or "")
