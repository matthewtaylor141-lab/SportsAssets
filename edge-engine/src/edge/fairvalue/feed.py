"""Odds feed client. Licensed feed only (never scraped) — The Odds API tier
by default; the FeedClient ABC keeps OpticOdds/Sportradar swappable.

Normalized output per event:
  FeedEvent(sport_key, league_code, home, away, commence_ts,
            h2h={outcome_name: decimal_odds}, totals=..., spreads=...)
Only sharp books (config) contribute; fair value comes from de-vig downstream.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# Low-margin / exchange books only. Adding SOFT books would bias the median
# toward the soft side and manufacture edges that are really just our own
# fair value being wrong, so this list stays sharp-only on purpose.
SHARP_BOOKS = {"pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
               "smarkets", "matchbook", "betanysports", "lowvig"}

# Not all sharp books are equally sharp, and a plain median pretends they are.
#
# Pinnacle is the line the rest of the market prices against, and a betting
# EXCHANGE is not an opinion at all — it is a real market clearing real money
# at a few percent commission, with no vig to remove. Those belong in a
# different class from a small low-margin sportsbook that is itself mostly
# copying Pinnacle with a lag.
#
# This matters most exactly where it hurts most. When only two or three books
# quote a market, an unweighted median can BE the softest book's opinion —
# and thin-consensus markets are where the largest apparent edges appear.
# That is a mechanism for manufacturing fake edge precisely where we are
# least equipped to check it, which is the winner's curse in one sentence.
ANCHOR_BOOKS = {"pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au"}
BOOK_WEIGHTS = {
    "pinnacle": 3.0,
    "betfair_ex_eu": 3.0, "betfair_ex_uk": 3.0, "betfair_ex_au": 2.0,
    "smarkets": 2.0, "matchbook": 2.0,
    "betanysports": 1.0, "lowvig": 1.0,
}


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Median that counts sharper books more, and stays a MEDIAN.

    A weighted mean would let one wild quote drag the estimate; the whole
    reason to use a median on book prices is robustness to exactly that.
    So weights move the halfway point rather than the arithmetic.
    """
    if not pairs:
        raise ValueError("no quotes")
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    half, run = total / 2.0, 0.0
    for price, w in ordered:
        run += w
        if run >= half:
            return price
    return ordered[-1][0]

# The Odds API sport keys -> our league slug codes (subset; extend as mapped).
SPORT_KEY_LEAGUE = {
    "soccer_epl": "epl", "soccer_spain_la_liga": "lal", "soccer_france_ligue_one": "fl1",
    "soccer_uefa_europa_league": "uel", "soccer_brazil_campeonato": "bra",
    "soccer_fifa_world_cup": "fifwc", "soccer_germany_bundesliga2": "bl2",
    "soccer_spl": "spl", "soccer_italy_serie_a": "sea", "soccer_argentina_primera_division": "arg",
    "soccer_mexico_ligamx": "mex", "soccer_japan_j_league": "j1100",
    "basketball_nba": "nba", "basketball_wnba": "wnba", "basketball_ncaab": "cbb",
    "tennis_wta": "wta",
    # US majors for the derivative (spread/total) edge — kch123 evidence.
    "icehockey_nhl": "nhl", "americanfootball_nfl": "nfl",
    # MEASURED-NEGATIVE leagues, mapped so the blocklist can actually reach
    # them. Coverage is now "every active sport the feed carries", which means
    # these keys WOULD arrive unmapped and be treated as unknown-but-allowed.
    # Naming them here is what keeps them blocked.
    "soccer_uefa_champs_league": "ucl", "soccer_germany_bundesliga": "bun",
    "soccer_efl_champ": "elc", "soccer_turkey_super_league": "tur",
    "soccer_portugal_primeira_liga": "por", "soccer_netherlands_eredivisie": "ere",
    "baseball_mlb": "mlb",
}

# Sports we never ask for at all. Empty: leagues are excluded by POLICY
# (blocklist / category_blocks), which is measurement-driven and visible in
# config, rather than by a hardcoded prefix nobody thinks to revisit.
SPORT_KEY_SKIP_PREFIXES: tuple[str, ...] = ()


@dataclass
class FeedEvent:
    sport_key: str
    league_code: str
    home: str
    away: str
    commence_ts: float
    h2h: dict[str, float] = field(default_factory=dict)      # name -> decimal odds
    totals: dict[str, float] = field(default_factory=dict)   # "Over 2.5" -> odds
    spreads: dict[str, float] = field(default_factory=dict)  # "Home -1.5" -> odds
    # Partial-game quotes, keyed by segment ('f5', 'h1', ...). A venue market
    # for a segment we have no quotes for must be REFUSED, never priced off
    # the full-game line — those are different bets.
    segments: dict = field(default_factory=dict)   # seg -> {h2h,totals,spreads}
    # Distinct sharp books behind the consensus. A 1c edge agreed by six
    # books is a signal; the same 1c from one book is a rounding error, so
    # the exploration tier requires depth before it trusts a small number.
    books: int = 0
    # How many REFERENCE-class books (Pinnacle, Betfair/Smarkets-style
    # exchanges) are among them. Zero means our fair value is a consensus of
    # followers with nothing anchoring it.
    anchors: int = 0
    # The provider's own event id. Segment and player-prop markets are only
    # served per-event (the bulk endpoint answers 422 for them), so without
    # this we cannot ask for them at all.
    # Player props: (market_key, normalised player, point) -> {"Over": odds,
    # "Under": odds}. Keyed on the EXACT point because a prop paired against
    # the wrong line is a different bet that still prices cleanly.
    props: dict = field(default_factory=dict)
    event_id: str = ""
    fetched_at: float = 0.0   # staleness stamp — set at fetch, checked pre-order

    def is_fresh(self, max_age_s: float = 30.0, now: float | None = None) -> bool:
        """Hard rule: no order without a quote fresher than max_age_s."""
        return (now or time.time()) - self.fetched_at <= max_age_s

    def event_key(self) -> str:
        """Venue-agnostic identity of the real-world event (one-per-event cap)."""
        import hashlib

        from edge.venues.mapper import norm_team

        day = int(self.commence_ts // 86400) if self.commence_ts else 0
        raw = f"{self.league_code}|{norm_team(self.home)}|{norm_team(self.away)}|{day}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


class OddsFeed(ABC):
    """Abstract odds feed (build step 3). Licensed API access only — no
    scraping. Implementations: TheOddsAPIClient (live), OpticOddsFeed (stub)."""

    @abstractmethod
    def fetch_events(self, sport_key: str) -> list[FeedEvent]: ...

    def quota(self) -> dict:
        """Remaining request budget, if the provider reports one."""
        return {}


FeedClient = OddsFeed  # back-compat alias for existing imports


class TheOddsAPIClient(OddsFeed):
    def resolve_sport_keys(self, policy=None) -> list[str]:
        """EVERY active sport the feed carries, minus the ones policy blocks.

        This used to return only the 18 keys in the static map, which capped
        the top of the funnel at whatever that hand-written list happened to
        contain — markets we never asked about could never be traded, however
        good the price. The edge comes from de-vigging a sharp two-sided
        quote, and that arithmetic does not care which league it is; the
        league filter exists to exclude leagues MEASURED negative, and those
        are named in the blocklist. So: ask for everything, exclude what is
        blocked, let the strategy filter judge the rest on price."""
        try:
            resp = self._sess.get(f"{self.BASE}/sports", params={"apiKey": self._key}, timeout=15)
            resp.raise_for_status()
            keys, blocked = [], []
            for s in resp.json():
                k = s.get("key", "")
                if not s.get("active") or not k:
                    continue
                if k.startswith(SPORT_KEY_SKIP_PREFIXES):
                    blocked.append(k)
                    continue
                if policy is not None and policy.league_allowed(self.league_of(k)) == "block":
                    blocked.append(k)
                    continue
                keys.append(k)
            if keys:
                log.info("sport coverage: %s active sports (%s blocked)",
                         len(keys), len(blocked))
                return keys
        except (requests.RequestException, ValueError) as exc:
            log.warning("sport-key discovery failed (%s); using static map", exc)
        return list(SPORT_KEY_LEAGUE)

    @staticmethod
    def league_of(sport_key: str) -> str:
        """Resolve a feed sport key to our league code.

        Exact match first, then LONGEST-PREFIX match against the same table.

        The prefix pass is what keeps the blocklist reachable. The feed
        carries a competition's qualifying and playoff rounds under keys that
        extend the parent key — `soccer_uefa_champs_league_qualification`
        alongside `soccer_uefa_champs_league`. Only the parent was mapped, so
        qualifiers resolved to their raw sport key, missed the `ucl` entry on
        the blocklist entirely, and were admitted by
        `unknown_league_policy: allow`. Found live 2026-08-02 holding
        `atc-ucl-din-kau-2026-08-04-din` and `atc-ucl-mja-sba-2026-08-04-draw`
        — two open positions in a league measured net-negative and shut.

        Longest prefix rather than any prefix, so a genuinely distinct
        competition that happens to share an ancestor's stem still resolves
        to its own entry when one exists.

        This is exactly the failure CLAUDE.md warns about: "Blocked sport keys
        must appear in SPORT_KEY_LEAGUE or the blocklist cannot reach them."
        Naming every variant by hand is the same trap one level down — there
        is always another round nobody thought to add — so the matching rule
        absorbs the variants instead.
        """
        if sport_key in SPORT_KEY_LEAGUE:
            return SPORT_KEY_LEAGUE[sport_key]
        best = ""
        for key in SPORT_KEY_LEAGUE:
            if sport_key.startswith(key + "_") and len(key) > len(best):
                best = key
        if best:
            return SPORT_KEY_LEAGUE[best]
        if sport_key.startswith("tennis_wta"):
            return "wta"
        if sport_key.startswith("tennis_atp"):
            return "atp"
        return sport_key

    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str | None = None, cache_ttl_s: float = 25.0,
                 quota_reserve: int = 50) -> None:
        self._key = api_key or os.environ.get("EDGE_ODDS_API_KEY", "")
        if not self._key:
            log.warning("EDGE_ODDS_API_KEY not set — feed calls will fail")
        self._sess = requests.Session()
        # Rate budgeting: short TTL cache absorbs intra-cycle re-fetches, and
        # a quota reserve floor stops us from burning the last N credits —
        # stale-but-served beats exhausted-and-blind.
        self._cache: dict[str, tuple[float, list[FeedEvent]]] = {}
        self._cache_ttl = cache_ttl_s
        self._quota_reserve = quota_reserve
        self._quota: dict[str, float] = {}
        self._server_skew_s: float | None = None  # local - server clock, from Date header
        # sport_key -> has live/imminent games (drives per-sport TTL)
        self._sport_active: dict[str, bool] = {}
        # sport_key -> events inside the trading window (drives the governor)
        self._sport_events: dict[str, int] = {}
        # Sports parked by the credit governor: polled rarely, so effectively
        # not traded. Named in telemetry — a silently disabled sport is the
        # exact failure this engine has already had once.
        self._parked: set[str] = set()
        # Per-event payloads (segments/props), cached hard — see EVENT_TTL_S.
        self._event_cache: dict[str, tuple[float, dict]] = {}
        self._no_props: set[str] = set()
        self._quota_t0: float | None = None
        self._quota_used0: float | None = None
        # sports whose alternate-line request the provider rejects (422)
        self._no_alt_lines: set[str] = set()
        # sports whose partial-game markets the provider rejects (422)
        self._no_segments: set[str] = set()

    def quota(self) -> dict:
        q = dict(self._quota)
        if self._parked:
            q["parked_sports"] = len(self._parked)
        q["burn_per_day"] = round(self._burn_per_day() or 0.0)
        return q

    # ── credit governor ─────────────────────────────────────────────────
    #
    # Covering every active sport multiplies credit burn by the number of
    # sports. Running dry mid-month is worse than covering fewer sports, and
    # slowing EVERY sport down is worst of all — the 30s freshness rule means
    # a slow quote is an untradeable one, so a uniform slowdown silently
    # stops all trading. The governor therefore spends the budget where the
    # games are: sports are ranked by how many events they actually have in
    # the trading window, and the tail is parked until credits recover.

    TRADING_WINDOW_H = 72
    QUOTA_TARGET_DAYS = 30      # the budget should outlast the billing month

    def _burn_per_day(self) -> float | None:
        used, t0 = self._quota.get("used"), self._quota_t0
        if used is None or t0 is None or self._quota_used0 is None:
            return None
        elapsed = time.time() - t0
        spent = used - self._quota_used0
        if elapsed < 120 or spent <= 0:
            return None
        return spent / elapsed * 86_400

    def rebalance_budget(self, sport_keys: list[str]) -> set[str]:
        """Park the least-active sports if the current burn rate would exhaust
        the credit budget before QUOTA_TARGET_DAYS. Returns the parked set."""
        remaining = self._quota.get("remaining")
        burn = self._burn_per_day()
        target = float(os.environ.get("EDGE_QUOTA_TARGET_DAYS",
                                      self.QUOTA_TARGET_DAYS))
        if remaining is None or burn is None or burn <= 0 or target <= 0:
            return self._parked
        runway = remaining / burn
        if runway >= target:
            if self._parked:
                log.info("credit runway %.0fd — unparking %s sports",
                         runway, len(self._parked))
            self._parked = set()
            return self._parked
        # Keep the share of sports the budget can actually afford, richest
        # first. Sports with no events in the window cost nothing to drop.
        keep_n = max(1, int(len(sport_keys) * runway / target))
        ranked = sorted(sport_keys, key=lambda k: -self._sport_events.get(k, 0))
        self._parked = set(ranked[keep_n:])
        log.warning("credit runway %.0fd < %.0fd target — parking %s of %s "
                    "sports (fewest events first)", runway, target,
                    len(self._parked), len(sport_keys))
        return self._parked

    def parked_sports(self) -> list[str]:
        return sorted(self._parked)

    def server_clock_skew_s(self) -> float | None:
        """|local - feed server| seconds, from the last response's Date header
        (watchdog + check-live input)."""
        return self._server_skew_s

    def _track_response(self, resp) -> None:
        for header, key in (("x-requests-remaining", "remaining"),
                            ("x-requests-used", "used")):
            val = resp.headers.get(header)
            if val is not None:
                try:
                    self._quota[key] = float(val)
                except ValueError:
                    pass
        date_hdr = resp.headers.get("date")
        if date_hdr:
            try:
                from email.utils import parsedate_to_datetime

                self._server_skew_s = time.time() - parsedate_to_datetime(date_hdr).timestamp()
            except (TypeError, ValueError):
                pass

    # Partial-game markets the provider exposes, by sport family. These are
    # a large share of what the venue lists (MLB first-5-innings run lines
    # and totals especially), and without the matching sharp quote they can
    # only ever be refused.
    SEGMENT_MARKETS = {
        "baseball": ["h2h_1st_5_innings", "spreads_1st_5_innings",
                     "totals_1st_5_innings"],
        "soccer": ["h2h_h1", "totals_h1"],
        "basketball": ["h2h_h1", "spreads_h1", "totals_h1"],
        "americanfootball": ["h2h_h1", "spreads_h1", "totals_h1"],
        "icehockey": ["h2h_p1", "totals_p1"],
    }
    # provider market key -> (segment, bucket)
    SEGMENT_KEYS = {
        "h2h_1st_5_innings": ("f5", "h2h"),
        "spreads_1st_5_innings": ("f5", "spreads"),
        "totals_1st_5_innings": ("f5", "totals"),
        "h2h_h1": ("h1", "h2h"), "spreads_h1": ("h1", "spreads"),
        "totals_h1": ("h1", "totals"),
        "h2h_p1": ("p1", "h2h"), "totals_p1": ("p1", "totals"),
        "h2h_q1": ("q1", "h2h"), "spreads_q1": ("q1", "spreads"),
        "totals_q1": ("q1", "totals"),
    }

    def _segment_markets(self, sport_key: str) -> list[str]:
        if os.environ.get("EDGE_SEGMENT_MARKETS", "1") == "0":
            return []
        family = sport_key.split("_", 1)[0]
        return self.SEGMENT_MARKETS.get(family, [])

    # Alternate spread/total lines are requested for EVERY sport: venues list
    # handicaps (soccer -2.5, etc.) that a book's standard line never quotes,
    # and without the alternate ladder those markets can never be priced at
    # all. Sports that reject the request are remembered and fall back.
    # EDGE_ALT_LINES=0 disables entirely.
    ALT_LINE_SPORTS: set[str] | None = None  # None = all sports

    # Below this many remaining credits, start conserving; above it, keep
    # every sport fresh (a slow TTL makes quotes fail the 30s freshness
    # rule, which silently disables trading on that sport).
    QUOTA_CONSERVE_BELOW = 50_000

    IDLE_TTL_S = 900.0   # nothing to trade: just re-check now and then

    def _ttl_for(self, sport_key: str) -> float:
        """Fast TTL for any sport with something to trade; long TTL only for
        sports that have NO events in the trading window (nothing to be stale
        about) or that the governor has parked.

        Never slow down a sport that has tradeable games: the 30s freshness
        rule turns a slow quote into a permanent reject, which reads as 'the
        engine found nothing' rather than 'the engine stopped looking'."""
        if self._sport_events.get(sport_key, 1) == 0:
            return max(self._cache_ttl, self.IDLE_TTL_S)
        if sport_key in self._parked:
            return max(self._cache_ttl, self.IDLE_TTL_S)
        # Low credits used to quietly stretch the TTL of non-imminent sports.
        # That is the same silent-disable in a different costume, and the
        # governor now handles scarcity by parking named sports instead.
        return self._cache_ttl

    # Segment and player-prop markets are served ONLY per event; the bulk
    # endpoint answers 422 and names every one of them. Measured 2026-07-31.
    EVENT_TTL_S = 1800          # partial-game lines move far slower than h2h
    EVENT_MAX_PER_SPORT = 40    # bound the fan-out; nearest games first

    # Player props, per sport family. Measured 2026-07-31: one MLB game
    # returns 440 prop outcomes with Pinnacle among the books — roughly the
    # entire tradeable universe we had before, from a single fixture.
    PROP_MARKETS = {
        "baseball": ["pitcher_strikeouts", "pitcher_outs", "pitcher_earned_runs",
                     "pitcher_hits_allowed", "batter_home_runs", "batter_hits",
                     "batter_total_bases", "batter_rbis", "batter_runs_scored"],
        "basketball": ["player_points", "player_rebounds", "player_assists"],
        "icehockey": ["player_points", "player_shots_on_goal"],
        "soccer": ["player_shots_on_target"],
    }

    def _prop_markets(self, sport_key: str) -> list[str]:
        if os.environ.get("EDGE_PROP_MARKETS", "1") == "0":
            return []
        if sport_key in self._no_props:
            return []
        family = sport_key.split("_")[0]
        return self.PROP_MARKETS.get(family, [])

    def _absorb(self, raw: dict, bucket_of: dict, samples: dict,
                seg_samples: dict, contributing: set) -> None:
        """Fold one provider payload's sharp-book quotes into the sample
        pools. Shared by the bulk and per-event paths so a market parsed one
        way is parsed the same way the other."""
        for book in raw.get("bookmakers", []):
            if book.get("key") not in SHARP_BOOKS:
                continue
            contributing.add(book["key"])
            for mkt in book.get("markets", []):
                seg, bucket = None, bucket_of.get(mkt["key"])
                if bucket is None:
                    seg_bucket = self.SEGMENT_KEYS.get(mkt["key"])
                    if seg_bucket is None:
                        continue
                    seg, bucket = seg_bucket
                for oc in mkt.get("outcomes", []):
                    name, price = oc.get("name", ""), float(oc.get("price", 0) or 0)
                    if price <= 1.0:
                        continue
                    key = name if bucket == "h2h" else f"{name} {oc.get('point')}"
                    wq = (price, BOOK_WEIGHTS.get(book["key"], 1.0))
                    if seg is None:
                        samples[bucket].setdefault(key, []).append(wq)
                    else:
                        seg_samples.setdefault(seg, {}).setdefault(
                            bucket, {}).setdefault(key, []).append(wq)

    def _absorb_props(self, raw: dict, prop_markets: list, contributing: set) -> dict:
        """Provider prop outcomes -> {(market, player, point): {side: [(odds,
        weight)]}}.

        Props name the player in `description`, not `name` — `name` carries
        Over/Under. Reading `name` as the player (the obvious mistake, since
        every other market puts the subject there) would collapse every
        player in a game onto one key and price them all off whichever
        outcome happened to land last.
        """
        from edge.fairvalue.props import norm_player

        wanted = set(prop_markets)
        out: dict = {}
        for book in raw.get("bookmakers", []):
            key = book.get("key")
            if key not in SHARP_BOOKS:
                continue
            for mkt in book.get("markets", []):
                if mkt.get("key") not in wanted:
                    continue
                for oc in mkt.get("outcomes", []):
                    side = (oc.get("name") or "").strip().title()
                    player = norm_player(oc.get("description") or "")
                    point, price = oc.get("point"), float(oc.get("price", 0) or 0)
                    if side not in ("Over", "Under") or not player:
                        continue
                    if point is None or price <= 1.0:
                        continue
                    contributing.add(key)
                    out.setdefault((mkt["key"], player, float(point)), {}) \
                       .setdefault(side, []).append(
                           (price, BOOK_WEIGHTS.get(key, 1.0)))
        return out

    def _enrich_segments(self, sport_key: str, events: list, now: float) -> int:
        """Fetch partial-game markets per event and merge them in.

        Cached hard (EVENT_TTL_S) because these lines move slowly and each
        call is billed per market per region — a per-cycle refresh would cost
        more than the whole rest of the feed and tell us nothing new.
        """
        if os.environ.get("EDGE_EVENT_MARKETS", "1") == "0":
            return 0
        seg_markets = self._segment_markets(sport_key)
        if sport_key in self._no_segments:
            seg_markets = []
        prop_markets = self._prop_markets(sport_key)
        wanted = seg_markets + prop_markets
        if not wanted:
            return 0
        if self._quota.get("remaining", float("inf")) <= self._quota_reserve:
            return 0
        bucket_of: dict = {}
        enriched = 0
        # Nearest games first: those are the ones a venue actually lists.
        for ev in sorted(events, key=lambda e: e.commence_ts)[:self.EVENT_MAX_PER_SPORT]:
            if not ev.event_id:
                continue
            hit = self._event_cache.get(ev.event_id)
            if hit and now - hit[0] < self.EVENT_TTL_S:
                raw = hit[1]
            else:
                try:
                    r = self._sess.get(
                        f"{self.BASE}/sports/{sport_key}/events/{ev.event_id}/odds",
                        params={"apiKey": self._key,
                                "regions": os.environ.get("EDGE_ODDS_REGIONS", "eu,uk,us"),
                                "markets": ",".join(wanted),
                                "oddsFormat": "decimal"},
                        timeout=20)
                    self._track_response(r)
                    if r.status_code == 422:
                        # The provider does not carry some of these for this
                        # sport, and names which. Drop only what it named:
                        # losing props because a segment key is unsupported
                        # (or the reverse) would throw away the larger half.
                        named = (r.json() or {}).get("message", "")
                        dropped = False
                        if any(m in named for m in seg_markets):
                            self._no_segments.add(sport_key)
                            dropped = True
                        if any(m in named for m in prop_markets):
                            self._no_props.add(sport_key)
                            dropped = True
                        if not dropped:
                            self._no_segments.add(sport_key)
                            self._no_props.add(sport_key)
                        return enriched
                    if r.status_code != 200:
                        continue
                    raw = r.json()
                except Exception as exc:  # noqa: BLE001
                    log.debug("segment fetch failed for %s: %s", ev.event_id, exc)
                    continue
                self._event_cache[ev.event_id] = (now, raw)

            seg_samples: dict = {}
            contributing: set[str] = set()
            self._absorb(raw, bucket_of, {"h2h": {}, "totals": {}, "spreads": {}},
                         seg_samples, contributing)
            prop_samples = self._absorb_props(raw, prop_markets, contributing)
            if not seg_samples and not prop_samples:
                continue
            if seg_samples:
                ev.segments = {
                    seg: {b: {k: _weighted_median(v) for k, v in keyed.items()}
                          for b, keyed in buckets.items()}
                    for seg, buckets in seg_samples.items()}
            if prop_samples:
                ev.props = {k: {side: _weighted_median(v)
                                for side, v in sides.items()}
                            for k, sides in prop_samples.items()}
            # A segment or prop quote from books that never quoted the full game
            # counts toward the anchor: it is the segment we are pricing.
            ev.anchors = max(ev.anchors, len(contributing & ANCHOR_BOOKS))
            enriched += 1
        return enriched

    def fetch_events(self, sport_key: str) -> list[FeedEvent]:
        now = time.time()
        cached = self._cache.get(sport_key)
        if cached and now - cached[0] < self._ttl_for(sport_key):
            return cached[1]
        if self._quota.get("remaining", float("inf")) <= self._quota_reserve:
            log.warning("odds quota at reserve floor (%s left) — serving stale cache",
                        self._quota.get("remaining"))
            return cached[1] if cached else []
        markets = "h2h,totals,spreads"
        if (os.environ.get("EDGE_ALT_LINES", "1") != "0"
                and (self.ALT_LINE_SPORTS is None
                     or sport_key in self.ALT_LINE_SPORTS)
                and sport_key not in self._no_alt_lines):
            markets += ",alternate_spreads,alternate_totals"
        # Segments are NOT requested here. The bulk endpoint answers 422 and
        # names every one of them ("Markets not supported by this endpoint");
        # they are served per event and fetched below in _enrich_segments.
        seg_markets: list[str] = []

        def _get(mkts: str):
            r = self._sess.get(
                f"{self.BASE}/sports/{sport_key}/odds",
                params={"apiKey": self._key,
                        "regions": os.environ.get("EDGE_ODDS_REGIONS", "eu,uk,us"),
                        "markets": mkts, "oddsFormat": "decimal"},
                timeout=20,
            )
            self._track_response(r)
            return r

        resp = _get(markets)
        if resp.status_code == 422 and seg_markets:
            # Provider rejects partial-game markets for this sport. Remember
            # and retry without them rather than losing the sport entirely.
            log.info("%s: segment markets unavailable (422) — full game only",
                     sport_key)
            self._no_segments.add(sport_key)
            markets = markets.replace("," + ",".join(seg_markets), "")
            resp = _get(markets)
        if resp.status_code == 422 and "alternate" in markets:
            # Provider rejects alternate markets for this sport (commonly
            # out-of-season or plan-scoped). Remember and fall back to the
            # base markets rather than losing the sport entirely.
            log.info("%s: alternate lines unavailable (422) — base markets only",
                     sport_key)
            self._no_alt_lines.add(sport_key)
            resp = _get("h2h,totals,spreads")
        resp.raise_for_status()
        out: list[FeedEvent] = []
        for raw in resp.json():
            ev = FeedEvent(
                sport_key=sport_key,
                league_code=self.league_of(sport_key),
                home=raw.get("home_team", ""),
                away=raw.get("away_team", ""),
                commence_ts=_iso_ts(raw.get("commence_time")),
                event_id=raw.get("id", "") or "",
                fetched_at=now,
            )
            # Consensus across ALL sharp books: median decimal odds per outcome.
            samples: dict[str, dict[str, list[float]]] = {"h2h": {}, "totals": {}, "spreads": {}}
            # Alternate lines fold into the same buckets — downstream pairing
            # is point-exact, so extra lines just mean more pairable points.
            bucket_of = {"h2h": "h2h", "totals": "totals", "spreads": "spreads",
                         "alternate_totals": "totals", "alternate_spreads": "spreads"}
            seg_samples: dict = {}   # segment -> bucket -> key -> [odds]
            contributing: set[str] = set()
            self._absorb(raw, bucket_of, samples, seg_samples, contributing)
            ev.h2h = {k: _weighted_median(v) for k, v in samples["h2h"].items()}
            ev.totals = {k: _weighted_median(v) for k, v in samples["totals"].items()}
            ev.spreads = {k: _weighted_median(v) for k, v in samples["spreads"].items()}
            ev.segments = {
                seg: {b: {k: _weighted_median(v) for k, v in keyed.items()}
                      for b, keyed in buckets.items()}
                for seg, buckets in seg_samples.items()}
            ev.books = len(contributing)
            # Whether a reference-class book quoted this event at all. An
            # estimate with no anchor is a consensus of followers, and the
            # prediction worth testing is that those revert hardest.
            ev.anchors = len(contributing & ANCHOR_BOOKS)
            if ev.h2h:
                out.append(ev)
        # Partial-game markets, per event, off the endpoint that serves them.
        try:
            n = self._enrich_segments(sport_key, out, now)
            if n:
                log.info("%s: segments enriched on %s events", sport_key, n)
        except Exception as exc:  # noqa: BLE001
            # Enrichment is additive. A failure here must cost us the extra
            # markets, never the full-game slate we already have in hand.
            log.warning("%s: segment enrichment failed (non-fatal): %s",
                        sport_key, exc)
        self._cache[sport_key] = (now, out)
        # Live/imminent window: any game from 6h ago (in-play) to 4h ahead.
        self._sport_active[sport_key] = any(
            now - 6 * 3600 < e.commence_ts < now + 4 * 3600 for e in out)
        # Trading window (matches venue discovery): what the governor ranks on.
        horizon = now + self.TRADING_WINDOW_H * 3600
        self._sport_events[sport_key] = sum(
            1 for e in out if now - 6 * 3600 < e.commence_ts < horizon)
        if self._quota_t0 is None and "used" in self._quota:
            self._quota_t0, self._quota_used0 = time.time(), self._quota["used"]
        return out


class OpticOddsFeed(OddsFeed):
    """Second-provider stub (build step 3). Kept as a compile-time interface
    check and a clear seam for a feed migration — not implemented in v1."""

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("EDGE_OPTICODDS_API_KEY", "")

    def fetch_events(self, sport_key: str) -> list[FeedEvent]:
        raise NotImplementedError(
            "OpticOdds adapter is a v1 stub — TheOddsAPIClient is the live feed. "
            "Implement fetch_events against the OpticOdds REST API to activate."
        )


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _iso_ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
