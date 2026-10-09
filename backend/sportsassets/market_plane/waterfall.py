"""THE COVERAGE WATERFALL OVER A DECLARED TARGET UNIVERSE (RC6, lane D2).

MARKET DATA / RESEARCH ONLY. Nothing here admits, prices, sizes or orders
anything, and nothing here changes a contract's coverage state: every count
is read off the state `market_plane.coverage.terminal` already assigned, from
the same evidence (`populate.classify`).

WHY. The scorecard's coverage unit reads PRICEABLE over the ENTIRE active
registry (pm-acceptance 37836393458: 87 / 147,929), and nothing in the
packet said which of those contracts are the ones BETTOR is meant to price,
where along the chain each of them stops, or why. research-sql run
37871119335 (research/rc6_coverage_waterfall.sql) answered it once by hand;
this module answers it on every coverage pass, so the readback carries it.

THE TARGET UNIVERSE, AND WHERE IT COMES FROM. The only written statement of
the market scope in the repository is research/codex_continuous_coverage_
readiness.md, "Required outcome": "Derek should continuously evaluate the
complete available, entitled venue catalogue, pregame and in-play,
including moneyline, totals, spreads, alternate spreads and team totals."
It names families, no sport, league or horizon. So:

  TARGET_A   full-game MONEYLINE / SPREAD (every listed line: main and
             alternate) / TOTAL (the game total) / TEAM_TOTAL, every sport
             and league the venue lists, pregame and in play
  TARGET_B   the same four families on a PERIOD or segment (halves,
             quarters, periods, innings, first five, sets, maps, games of a
             series, regulation time) -- the statement neither names nor
             excludes them; they are reported apart so the owner can decide
  EXCLUDED   by NAMED class only: PLAYER_PROP, OTHER_PROP (exact score,
             first scorer, team statistic totals, corners, both teams to
             score, method of victory, ...), OUTRIGHT (futures), NON_SPORTS
             (a venue code NON_SPORTS_LEAGUES names), and Kalshi series whose
             ticker names no game family
  UNCLASSIFIED  a contract whose venue states no market type: neither in nor
             out, counted on its own line

Nothing is excluded for failing a later stage, and no stage is skipped:
within a tier, catalogue = lost_external + lost_mapping + lost_settlement +
lost_fair_value + lost_fresh_book + priceable, checked on every pass
(`sums_exact`). The tier itself is the owner's to adopt (an evaluator
change); until then the scorecard's whole-registry unit is untouched.

KALSHI. A Kalshi row's family is read here from its SERIES TICKER suffix
(KX...GAME / SPREAD / TOTAL / TEAMTOTAL, a 1H / 2H / 1Q.. marker for a
period) for the tier only -- basis KALSHI_SERIES_TICKER_SUFFIX, named in the
output. The Kalshi mapping itself (sport, family, settlement) is the Kalshi
lane's; this never maps a Kalshi contract.
"""
from __future__ import annotations

import re

VERSION = "MARKET_PLANE_COVERAGE_WATERFALL_V1"

TARGET_A, TARGET_B = "TARGET_A", "TARGET_B"
EXCLUDED, UNCLASSIFIED = "EXCLUDED", "UNCLASSIFIED"
TIERS = (TARGET_A, TARGET_B, EXCLUDED, UNCLASSIFIED)

MONEYLINE, SPREAD, TOTAL, TEAM_TOTAL = ("MONEYLINE", "SPREAD", "TOTAL",
                                        "TEAM_TOTAL")
TARGET_FAMILIES = (MONEYLINE, SPREAD, TOTAL, TEAM_TOTAL)
X_PLAYER_PROP = "PLAYER_PROP"
X_OTHER_PROP = "OTHER_PROP"
X_OUTRIGHT = "OUTRIGHT"
X_NON_SPORTS = "NON_SPORTS"
X_KALSHI_SERIES = "KALSHI_SERIES_NOT_A_GAME_FAMILY"
NO_MARKET_TYPE = "NO_MARKET_TYPE"
FULL, PERIOD = "FULL", "PERIOD"

BASIS_PMUS = "VENUE_SPORTS_MARKET_TYPE"
BASIS_KALSHI = "KALSHI_SERIES_TICKER_SUFFIX"

LOST_EXTERNAL = "LOST_EXTERNAL"
LOST_MAPPING = "LOST_MAPPING"
LOST_SETTLEMENT = "LOST_SETTLEMENT"
LOST_FAIR_VALUE = "LOST_FAIR_VALUE"
LOST_FRESH_BOOK = "LOST_FRESH_BOOK"
PRICEABLE = "PRICEABLE"
STAGES = (LOST_EXTERNAL, LOST_MAPPING, LOST_SETTLEMENT, LOST_FAIR_VALUE,
          LOST_FRESH_BOOK, PRICEABLE)
#: the cumulative stages a contract REACHES, in order (a contract lost at a
#: stage reached every stage before it)
REACHED = ("CATALOGUE", "MAPPED", "SETTLEMENT_SUPPORTED",
           "FAIR_VALUE_SUPPORTED", "FRESH_PRICEABLE")

SOURCE = {
    "file": "research/codex_continuous_coverage_readiness.md",
    "section": "Required outcome",
    "quote": ("Derek should continuously evaluate the complete available, "
              "entitled venue catalogue, pregame and in-play, including "
              "moneyline, totals, spreads, alternate spreads and team "
              "totals."),
    "status": ("PROPOSED: the only written market-scope statement found; "
               "no owner-approved sport / league / horizon scope exists in "
               "the repository, so none is applied"),
}

#: bounds on the keyed breakdowns (the snapshot is read whole)
MAX_SPORT_FAMILY_KEYS = 60
MAX_REASON_KEYS = 40
REASON_CHARS = 110

_ML = re.compile(r"(_winner(_[0-9]+)?$|^moneyline$)")
_SPREAD = re.compile(r"(_spread|_handicap(_[0-9]+)?)$")
_TOTAL = re.compile(r"(_total|_total_games|_total_sets|_total_maps|"
                    r"_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$")
#: a team total's own slug token (`-tt-<team>-16pt5`, `-tt1h-<team>-...`)
_TT_SLUG = re.compile(r"-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$")
_PMUS_PERIOD = re.compile(
    r"(first|second|third|fourth)_(half|quarter|period)|_regulation_|"
    r"_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|"
    r"_game_total_kills|_rounds_handicap_[0-9]")
_K_PERIOD = re.compile(r"(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$")


def classify(contract: dict) -> dict:
    """PURE. {tier, family, segment, basis} for one registry contract."""
    c = dict(contract or {})
    venue = str(c.get("venue") or "")
    if venue == "KALSHI":
        s = str(c.get("competition") or "").upper()
        fam = (TEAM_TOTAL if s.endswith("TEAMTOTAL") else
               SPREAD if s.endswith("SPREAD") else
               TOTAL if s.endswith("TOTAL") else
               MONEYLINE if s.endswith("GAME") else X_KALSHI_SERIES)
        seg = PERIOD if _K_PERIOD.search(s) else FULL
        basis = BASIS_KALSHI
    else:
        from .ontology import NON_SPORTS_LEAGUES
        from .populate import league_of
        mt = str(c.get("market_type") or "").strip().lower()
        lg = league_of(c.get("event_id"), c.get("competition"))
        basis = BASIS_PMUS
        seg = PERIOD if (mt and _PMUS_PERIOD.search(mt)) else FULL
        if lg in NON_SPORTS_LEAGUES:
            fam = X_NON_SPORTS
        elif not mt:
            fam = NO_MARKET_TYPE
        elif mt == "futures":
            fam = X_OUTRIGHT
        elif re.search(r"(^|_)player_", mt):
            fam = X_PLAYER_PROP
        elif _ML.search(mt):
            fam = MONEYLINE
        elif _SPREAD.search(mt):
            fam = SPREAD
        elif _TOTAL.search(mt):
            fam = (TEAM_TOTAL if _TT_SLUG.search(str(c.get("contract_id")
                                                     or "").lower())
                   else TOTAL)
        else:
            fam = X_OTHER_PROP
    if fam in TARGET_FAMILIES:
        tier = TARGET_A if seg == FULL else TARGET_B
    elif fam == NO_MARKET_TYPE:
        tier = UNCLASSIFIED
    else:
        tier = EXCLUDED
    return {"tier": tier, "family": fam, "segment": seg, "basis": basis}


#: coverage.terminal's states, one stage each -- except CODE_CONTROLLED_GAP,
#: which terminal() assigns at TWO points of its order (not mapped; mapped
#: and valued but no fresh canonical book), told apart by the same evidence
_STATE_STAGE = {"PRICEABLE": PRICEABLE,
                "EXTERNAL_DATA_UNAVAILABLE": LOST_EXTERNAL,
                "MAPPED_BUT_SETTLEMENT_NOT_PROVEN": LOST_SETTLEMENT,
                "MAPPED_BUT_NO_FAIR_VALUE_SOURCE": LOST_FAIR_VALUE}
_BOOK_WHYS = ("NO_CURRENT_CANONICAL_BOOK", "NO_FRESH_CANONICAL_BOOK")


def stage_of(state: str | None, why: str | None = None,
             evidence: dict | None = None) -> str:
    """The stage at which the contract stopped: its terminal state, and for
    CODE_CONTROLLED_GAP the same evidence terminal() read (mapped or not),
    else its own why. An unknown state is a mapping loss, never a pass."""
    if state in _STATE_STAGE:
        return _STATE_STAGE[state]
    e = evidence or {}
    if "mapped" in e:
        return LOST_FRESH_BOOK if e.get("mapped") else LOST_MAPPING
    return (LOST_FRESH_BOOK if str(why or "").startswith(_BOOK_WHYS)
            else LOST_MAPPING)


#: THE DECISION HORIZON, READ (collector_coverage.HORIZON_AHEAD_S /
#: _BEHIND_S: -6 h .. +24 h), never set here. A contract outside it cannot
#: carry a decision valuation yet BY DESIGN -- the collector values only
#: competitions with an event inside it -- so it is REPORTED apart
#: (in_decision_horizon), never removed from any denominator. research-sql
#: run 37873014699 M3: every major-league money line starting 24-48 h out
#: and never valued was NOT_A_COLLECTOR_CANDIDATE_IN_24H.
def _horizon() -> tuple:
    from .. import collector_coverage as CC
    return float(CC.HORIZON_BEHIND_S), float(CC.HORIZON_AHEAD_S)


def _blank() -> dict:
    return {"catalogue": 0, **{s: 0 for s in STAGES}, "valued_24h": 0,
            "within_48h": 0, "within_48h_priceable": 0,
            "in_decision_horizon": 0, "in_decision_horizon_priceable": 0}


class Waterfall:
    """Counters only: one contract in, nothing kept but the counts."""

    def __init__(self, *, now: float):
        self.now = float(now)
        self.behind_s, self.ahead_s = _horizon()
        self.tiers = {t: _blank() for t in TIERS}
        self.venue_tier: dict = {}
        self.fam_seg: dict = {}
        self.sport_fam: dict = {}
        self.excluded: dict = {}
        self.reasons: dict = {}

    def add(self, contract: dict, t: dict, *, valued: bool = False) -> dict:
        k = classify(contract)
        stage = stage_of((t or {}).get("state"), (t or {}).get("why"),
                         (t or {}).get("evidence"))
        st = contract.get("event_start")
        try:
            st = float(st.timestamp()) if hasattr(st, "timestamp") else (
                None if st is None else float(st))
        except (TypeError, ValueError):
            st = None
        w48 = st is not None and -6 * 3600.0 <= st - self.now <= 48 * 3600.0
        hz = st is not None and -self.behind_s <= st - self.now <= self.ahead_s
        venue = str(contract.get("venue") or "UNKNOWN")
        books = [(k["tier"], self.tiers),
                 ("%s|%s" % (venue, k["tier"]), self.venue_tier),
                 ("%s|%s|%s" % (k["tier"], k["family"], k["segment"]),
                  self.fam_seg)]
        if k["tier"] in (TARGET_A, TARGET_B):
            books.append(("%s|%s|%s|%s" % (
                k["tier"], venue, contract.get("sport") or "UNKNOWN",
                k["family"]), self.sport_fam))
        for key, book in books:
            row = book.setdefault(key, _blank())
            row["catalogue"] += 1
            row[stage] += 1
            row["valued_24h"] += int(bool(valued))
            row["within_48h"] += int(w48)
            row["within_48h_priceable"] += int(w48 and stage == PRICEABLE)
            row["in_decision_horizon"] += int(hz)
            row["in_decision_horizon_priceable"] += int(
                hz and stage == PRICEABLE)
        if k["tier"] in (TARGET_A, TARGET_B):
            if stage != PRICEABLE:
                rk = "%s|%s|%s" % (k["tier"], stage,
                                   str((t or {}).get("why") or "")
                                   [:REASON_CHARS])
                self.reasons[rk] = self.reasons.get(rk, 0) + 1
        elif k["tier"] == EXCLUDED:
            xk = "%s|%s|%s" % (venue, k["family"], k["segment"])
            self.excluded[xk] = self.excluded.get(xk, 0) + 1
        return dict(k, stage=stage)

    @staticmethod
    def _reached(row: dict) -> dict:
        n = row["catalogue"]
        mapped = n - row[LOST_EXTERNAL] - row[LOST_MAPPING]
        settled = mapped - row[LOST_SETTLEMENT]
        valued = settled - row[LOST_FAIR_VALUE]
        return dict(zip(REACHED, (n, mapped, settled, valued,
                                  valued - row[LOST_FRESH_BOOK])))

    def result(self) -> dict:
        tiers = {}
        exact = True
        for t, row in self.tiers.items():
            ok = row["catalogue"] == sum(row[s] for s in STAGES)
            exact = exact and ok
            tiers[t] = dict(row, reached=self._reached(row),
                            rate=(round(row[PRICEABLE] / row["catalogue"], 6)
                                  if row["catalogue"] else None))
        total = sum(r["catalogue"] for r in self.tiers.values())
        top_sf = sorted(self.sport_fam.items(),
                        key=lambda kv: -kv[1]["catalogue"])
        top_r = sorted(self.reasons.items(), key=lambda kv: -kv[1])
        return {"version": VERSION, "computed_at": self.now,
                "decision_horizon_s": {"behind": self.behind_s,
                                       "ahead": self.ahead_s,
                                       "source": "collector_coverage."
                                                 "HORIZON_BEHIND_S / "
                                                 "HORIZON_AHEAD_S (read)"},
                "universe": {"source": dict(SOURCE),
                             "tiers": {TARGET_A: "full-game moneyline, "
                                       "spread (every line), game total, "
                                       "team total; every sport and league",
                                       TARGET_B: "the same families on a "
                                       "period or segment",
                                       EXCLUDED: "named classes only",
                                       UNCLASSIFIED: "the venue states no "
                                       "market type"},
                             "basis": {"POLYMARKET_US": BASIS_PMUS,
                                       "KALSHI": BASIS_KALSHI}},
                "catalogue": total,
                "stages": list(STAGES), "reached_order": list(REACHED),
                "tiers": tiers,
                "by_venue_tier": dict(sorted(self.venue_tier.items())),
                "by_tier_family_segment": dict(sorted(self.fam_seg.items())),
                "by_target_sport_family": dict(
                    top_sf[:MAX_SPORT_FAMILY_KEYS]),
                "by_target_sport_family_keys_total": len(top_sf),
                "excluded": dict(sorted(self.excluded.items())),
                "reasons": dict(top_r[:MAX_REASON_KEYS]),
                "reasons_keys_total": len(top_r),
                "sums_exact": bool(exact and total == sum(
                    self.tiers[t]["catalogue"] for t in TIERS))}
