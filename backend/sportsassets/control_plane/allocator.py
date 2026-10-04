"""THE CAPITAL ALLOCATION ENGINE (control plane, stream C). Pure.

OWNER (control plane section 3): "A positive-EV trade does NOT automatically
deserve capital. Allocate capital using measurable expected utility ...
Introduce a capital-efficiency metric such as EXPECTED_NET_PROFIT /
EXPECTED_CAPITAL_HOURS but do NOT blindly maximize that one metric.
Kelly-style sizing may be evaluated, but: never raw full Kelly; probability
uncertainty must reduce sizing; execution uncertainty must reduce sizing;
correlated positions must reduce sizing; portfolio exposure limits override
all sizing recommendations. The allocator must explain every size:
REQUESTED_SIZE, APPROVED_SIZE, SIZE_LIMITING_FACTOR, PORTFOLIO_EXPOSURE_AFTER."

IT EXTENDS ALLIE, IT DOES NOT DUPLICATE HER. ALLIE_CAPITAL_EFFICIENCY_V1
(`allie_capital.allocate`, still SHADOW_PENDING_OWNER_APPROVAL -- this module
never calls her authoritative) remains the capital-efficiency evaluation of
one candidate: its 13 fields are recomputed here from the SAME decision-time
inputs but with three red-team corrections applied AT THE CALL SITE, and
carried verbatim on the allocation as her evidence:

  item 9   exposure (fixture, book) from POSITION / LEDGER TRUTH -- open
           cost basis after partial exits, reductions and protection fills,
           plus open BUY reservations -- never entry filled_qty x limit
           (`portfolio_from_ledger`, fed by readers_capital.positions_at)
  item 11  Allie's capacity ceiling is the SELECTED execution style's
           capacity, never the taker ladder's when the style is maker
  item 7   her hurdle sample is the PRE-ALLOCATION QUALIFIED-OPPORTUNITY
           TAPE: every ranked candidate that cleared every gate (admitted or
           not, funded or not -- cp_opportunity_rankings), not the selected
           canonical intents
Her proposal (which still contains her research fixture / book caps) is one
RESEARCH cap on the size; it never raises a size.

WHAT THIS MODULE ADDS (each step recorded with its inputs and reason):

  1 GATES. Not admitted by the ranker; approved rails unreadable (fail
    closed); no requested size / unit cost / probability; expected net <= 0;
    capital efficiency (NET_EV / capital-hours, the ranker's component) not
    above the OPPORTUNITY-COST HURDLE -> 0. The hurdle is the
    `hurdle_quantile` of the qualified-opportunity tape (prior batches as of
    the clock + this batch's eligible candidates) WHETHER OR NOT idle cash
    covers the trade (red-team item 6: idle cash has option value -- a
    better opportunity may exist now or arrive before this capital
    releases); fewer than `min_hurdle_sample` tape entries -> the hurdle is
    UNMEASURED and nothing is funded (never a manufactured zero). Capital
    efficiency is therefore a GATE and a reported figure; it is never the
    quantity maximized -- the size comes from expected log utility below.
  2 FRACTIONAL KELLY ON THE UNCERTAINTY-ADJUSTED EDGE (expected log
    utility). For a binary contract bought at unit cost c (price + fee per
    $1 of payout) with probability p:
        p_cons = p - u_p           probability uncertainty REDUCES size
        c_cons = c + u_e           execution uncertainty REDUCES size
        f_full = max(0, (p_cons - c_cons) / (1 - c_cons))
        f_used = kelly_fraction x drawdown factor x volatility factor
                 (kelly_fraction <= 0.5 by construction: NEVER raw full
                  Kelly; the database CHECKs kelly_fraction_used < 1)
        kelly_usd = capital base x f_full x f_used
    then CORRELATED POSITIONS REDUCE IT: kelly_usd minus the exposure
    already held in the candidate's event / market / team / league / sport /
    strategy / agent / market-family / venue groups, weighted by the
    research correlation assumption (ranking.CORRELATION_ASSUMPTION) -- the
    fractional-Kelly budget of a group is shared by everything already in
    it. The expected log-utility gain of the approved size is recorded.
  3 CAPS, the smallest binds: REQUEST (never more than asked), KELLY,
    EVIDENCE (the selected style's executable capacity; a MEASURED zero is
    zero -- item 12; unmeasured capacity is 0, liquidity is never assumed),
    RESEARCH (Allie's proposal), and the APPROVED RAILS -- read from the
    existing approved sources, never invented here (readers_capital.
    approved_rails): the paper account's effective caps (the frozen session
    config through bettor_paper_limits, the owner's capital policy), the
    funding boundary (available cash), the hedge reserve, the concurrent-
    group limit, the same-strategy-same-contract rule. A rail the approved
    policy does not set is recorded NOT_SET, never filled with a number.
    RAILS OVERRIDE EVERYTHING: they are always in the min, and on a tie the
    rail is named. The SMALL LIVE rail (execmirror_control max_order_usd at
    its scale) is reported as the live lane's view of the same size; SMALL
    LIVE stays SHADOW.
  4 THE SIZE, EXPLAINED: REQUESTED_SIZE, APPROVED_SIZE (whole contracts at
    the unit cost, never above any cap), SIZE_LIMITING_FACTOR (+ its kind:
    RAIL / EVIDENCE / RESEARCH / KELLY / REQUEST / GATE),
    PORTFOLIO_EXPOSURE_AFTER (every group the candidate touches, the totals,
    available cash and open groups after it), KELLY_FRACTION_USED and the
    UNCERTAINTY_HAIRCUTS.

THE BATCH. Candidates are processed in the ranker's ADMISSION ORDER (best
first); every approval is added to the portfolio before the next candidate
is sized, so correlated candidates of the same batch shrink each other and
every cumulative rail (market, fixture, aggregate, cash, groups) holds over
the batch.

RESEARCH PARAMETERS (addendum item 8) live in the frozen `AllocatorConfig`
(label RESEARCH, sha on every record); the values reuse the codebase's
existing research defaults (intel/sizing: quarter Kelly and its unmeasured
uncertainty defaults; allie_capital: hurdle quantile and minimum sample) so
no new magnitude is invented. Approved rails are a separate input with their
own sha. Nothing here is an approved limit.

AUTHORITY: SHADOW. No order, size, admission or gate on the production path
changes; nothing here imports an order, venue, execution, funded or live
module.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from .. import allie_capital as AC
from . import ranking as RK

VERSION = "CP_CAPITAL_ALLOCATOR_V1"
CONFIG_VERSION = "CP_ALLOCATOR_CONFIG_V1"
AUTHORITY = "SHADOW"
LABEL = "RESEARCH"

RAIL, EVIDENCE, RESEARCH, KELLY, REQUEST, GATE = (
    "RAIL", "EVIDENCE", "RESEARCH", "KELLY", "REQUEST", "GATE")
KINDS = (RAIL, EVIDENCE, RESEARCH, KELLY, REQUEST, GATE)
#: tie-break when two caps bind at the same amount: the rail is named first
KIND_ORDER = {RAIL: 0, EVIDENCE: 1, RESEARCH: 2, KELLY: 3, REQUEST: 4}

# gate factors
F_NOT_ADMITTED = "NOT_ADMITTED_BY_THE_RANKER"
F_RAILS = "APPROVED_RAILS_UNREADABLE"
F_NO_REQUEST = "REQUESTED_SIZE_UNAVAILABLE"
F_NO_UNIT = "UNIT_COST_UNAVAILABLE"
F_NO_P = "PROBABILITY_UNAVAILABLE"
F_NON_POSITIVE = "NON_POSITIVE_EXPECTED_NET"
F_HURDLE_UNMEASURED = "OPPORTUNITY_COST_HURDLE_UNMEASURED"
F_BELOW_HURDLE = "CAPITAL_EFFICIENCY_NOT_ABOVE_OPPORTUNITY_COST"
F_NO_EDGE = "NO_EDGE_AFTER_PROBABILITY_AND_EXECUTION_UNCERTAINTY"
F_BELOW_ONE = "BELOW_ONE_CONTRACT_AT_THE_BINDING_CAP"

R_NOT_SET = "NOT_SET_BY_THE_APPROVED_POLICY"


@dataclass(frozen=True)
class AllocatorConfig:
    """THE FROZEN, VERSIONED ALLOCATOR RESEARCH PARAMETERS (addendum item 8).
    Values are the codebase's existing research defaults; none is an
    approved rail."""
    version: str = CONFIG_VERSION
    label: str = LABEL
    #: intel/sizing.KELLY_FRACTION (quarter Kelly)
    kelly_fraction: float = 0.25
    #: intel/sizing.UNMEASURED_CAL_UNCERTAINTY (probability half-width)
    unmeasured_probability_uncertainty: float = 0.10
    #: intel/sizing.UNMEASURED_EXEC_UNCERTAINTY (per $1 contract)
    unmeasured_execution_uncertainty: float = 0.02
    #: intel/sizing.DRAWDOWN_HALT_PCT / 100
    drawdown_halt_fraction: float = 0.20
    #: intel/sizing.UNMEASURED_DRAWDOWN_FACTOR
    unmeasured_drawdown_factor: float = 0.75
    #: the same conservative default for an unmeasured realized volatility
    unmeasured_volatility_factor: float = 0.75
    #: a weighted exposure dimension whose key is unknown: the correlation
    #: cannot be checked, so the Kelly budget is halved (the ranker's
    #: conservative penalty, RankerConfig.unavailable_factor)
    unknown_group_factor: float = 0.5
    #: allie_capital.HURDLE_QUANTILE / MIN_HURDLE_SAMPLE
    hurdle_quantile: float = 0.75
    min_hurdle_sample: int = 10
    correlation: tuple = RK.CORRELATION_ASSUMPTION
    precision: int = 6

    def __post_init__(self):
        # NEVER RAW FULL KELLY: the fraction is capped at half Kelly here
        # and kelly_fraction_used < 1 is CHECKed by the database
        if not 0.0 < self.kelly_fraction <= 0.5:
            raise ValueError("kelly_fraction must be in (0, 0.5]: never "
                             "raw full Kelly")
        for k in ("unmeasured_drawdown_factor", "unmeasured_volatility_factor",
                  "unknown_group_factor"):
            if not 0.0 < getattr(self, k) <= 1.0:
                raise ValueError("%s must be in (0, 1]" % k)
        if self.unmeasured_probability_uncertainty < 0 or \
                self.unmeasured_execution_uncertainty < 0:
            raise ValueError("uncertainty defaults are non-negative")
        if not 0.0 < self.hurdle_quantile < 1.0 or self.min_hurdle_sample < 1:
            raise ValueError("the hurdle needs a quantile and a sample")

    def to_dict(self) -> dict:
        return {"config_kind": "CP_ALLOCATOR_CONFIG", "version": self.version,
                "label": self.label, "kelly_fraction": self.kelly_fraction,
                "unmeasured_probability_uncertainty":
                    self.unmeasured_probability_uncertainty,
                "unmeasured_execution_uncertainty":
                    self.unmeasured_execution_uncertainty,
                "drawdown_halt_fraction": self.drawdown_halt_fraction,
                "unmeasured_drawdown_factor": self.unmeasured_drawdown_factor,
                "unmeasured_volatility_factor":
                    self.unmeasured_volatility_factor,
                "unknown_group_factor": self.unknown_group_factor,
                "hurdle_quantile": self.hurdle_quantile,
                "min_hurdle_sample": self.min_hurdle_sample,
                "correlation": {d: w for d, w in self.correlation},
                "precision": self.precision,
                "allocator_version": VERSION,
                "provenance": {
                    "kelly_fraction": "intel/sizing.KELLY_FRACTION",
                    "unmeasured_probability_uncertainty":
                        "intel/sizing.UNMEASURED_CAL_UNCERTAINTY",
                    "unmeasured_execution_uncertainty":
                        "intel/sizing.UNMEASURED_EXEC_UNCERTAINTY",
                    "drawdown_halt_fraction":
                        "intel/sizing.DRAWDOWN_HALT_PCT / 100",
                    "unmeasured_drawdown_factor":
                        "intel/sizing.UNMEASURED_DRAWDOWN_FACTOR",
                    "hurdle_quantile": "allie_capital.HURDLE_QUANTILE",
                    "min_hurdle_sample": "allie_capital.MIN_HURDLE_SAMPLE",
                    "correlation": "ranking.CORRELATION_ASSUMPTION"}}

    @property
    def sha(self) -> str:
        return RK.sha_of(self.to_dict())

    def weight(self, dim: str) -> float:
        return dict(self.correlation)[dim]


DEFAULT_CONFIG = AllocatorConfig()

num = RK.num


def _r(v, n=6):
    return None if v is None else round(float(v), n)


# ─────────────────────────── the portfolio (ledger truth) ───────────────

def position_keys(p: dict) -> dict:
    """The exposure groups of one ledger position or open reservation, from
    its recorded label / fixture / strategy only (nothing guessed). AGENT is
    absent: positions carry no originating-agent attribution yet."""
    lab = p.get("label") or {}
    teams = [t for t in (lab.get("home_team"), lab.get("away_team")) if t]
    keys = {
        "EVENT": p.get("fixture") or lab.get("event_key"),
        "MARKET": p.get("us_market_slug"),
        "TEAM": teams or None,
        "SPORT": p.get("sport") or lab.get("sport"),
        "LEAGUE": lab.get("competition"),
        "STRATEGY": p.get("strategy"),
        "MARKET_FAMILY": lab.get("market_type"),
        "VENUE": p.get("venue") or "POLYMARKET",
    }
    return RK.keys_of({"exposure_keys": keys})


def portfolio_from_ledger(*, positions: list, reservations: list,
                          cash: dict, clock: float, account_id: str,
                          drawdown_fraction=None, drawdown_basis=None,
                          realized_volatility: dict | None = None) -> dict:
    """THE PORTFOLIO AS OF THE CLOCK FROM POSITION / LEDGER TRUTH (red-team
    item 9). Pure.

      positions     open positions as the ledger derives them (open_qty =
                    bought - sold - settled; cost_basis_usd = average cost
                    incl. fees x open_qty) -- partial exits, reductions and
                    protection fills already netted
      reservations  open BUY reservations (the ledger's reserved_delta sum
                    per order at the clock)
      cash          {cash_usd, reserved_usd, available_usd} summed from the
                    ledger at the clock
    """
    exp: dict = {d: {} for d in RK.DIMENSIONS}
    event_groups: dict = {}
    groups, held = set(), set()
    total = 0.0

    def add(rec, usd, *, group):
        nonlocal total
        if usd <= 0:
            return
        total += usd
        for d, ks in position_keys(rec).items():
            for k in ks:
                exp[d][k] = exp[d].get(k, 0.0) + usd
        if group:
            groups.add(group)
            ev = (position_keys(rec).get("EVENT") or [None])[0]
            if ev is not None:
                event_groups.setdefault(ev, set()).add(group)
        held.add("%s|%s|%s" % (rec.get("strategy"), rec.get("us_market_slug"),
                               rec.get("holding_side")))

    for p in positions or []:
        if (num(p.get("open_qty")) or 0.0) <= 1e-9:
            continue
        add(p, num(p.get("cost_basis_usd")) or 0.0, group=p.get("group_id"))
    for r in reservations or []:
        add(r, num(r.get("reserved_usd")) or 0.0, group=r.get("group_id"))
    c = cash or {}
    cash_usd = num(c.get("cash_usd"))
    reserved = num(c.get("reserved_usd"))
    available = num(c.get("available_usd"))
    open_basis = sum(num(p.get("cost_basis_usd")) or 0.0
                     for p in positions or []
                     if (num(p.get("open_qty")) or 0.0) > 1e-9)
    base = None if cash_usd is None else cash_usd + open_basis
    return {
        "as_of": float(clock), "account_id": account_id,
        "cash_usd": cash_usd, "reserved_usd": reserved,
        "available_usd": available,
        "capital_base_usd": base,
        "capital_base_basis": "ledger cash (reservations included) + open "
                              "cost basis; no mark needed",
        "exposure": {d: {k: round(v, 6) for k, v in m.items()}
                     for d, m in exp.items()},
        "total_exposure_usd": round(total, 6),
        "open_groups": len(groups),
        "event_groups": {k: len(v) for k, v in event_groups.items()},
        "held_contracts": sorted(held),
        "drawdown_fraction": num(drawdown_fraction),
        "drawdown_basis": drawdown_basis,
        "realized_volatility": dict(realized_volatility or {}),
        "unavailable": {"AGENT": "POSITIONS_CARRY_NO_ORIGINATING_AGENT"},
        "basis": "POSITION_LEDGER_TRUTH: paper_fills / paper_settlements / "
                 "paper_ledger as of the clock (red-team item 9)"}


def _copy_portfolio(pf: dict) -> dict:
    out = dict(pf)
    out["exposure"] = {d: dict(m) for d, m in (pf.get("exposure") or {})
                       .items()}
    out["event_groups"] = dict(pf.get("event_groups") or {})
    out["held_contracts"] = list(pf.get("held_contracts") or [])
    return out


# ─────────────────────────── the hurdle (items 6 and 7) ─────────────────

def hurdle(prior_tape: list, batch_rows: list, cfg: AllocatorConfig) -> dict:
    """THE OPPORTUNITY-COST HURDLE of one batch: the `hurdle_quantile` of
    the PRE-ALLOCATION QUALIFIED-OPPORTUNITY TAPE -- capital efficiency of
    every ranked candidate that cleared every gate before the clock (the
    caller reads it point-in-time) plus this batch's eligible candidates.
    Selected and rejected alike (item 7); applied whether or not idle cash
    covers the trade (item 6). Computed once per batch (one sort)."""
    xs = [num(x) for x in prior_tape or []]
    xs += [r.get("capital_efficiency_component") for r in batch_rows
           if r.get("gates_passed")]
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < cfg.min_hurdle_sample:
        return {"value": None, "status": "UNMEASURED", "sample": n,
                "why": "QUALIFIED_OPPORTUNITY_TAPE_%d_BELOW_%d"
                       % (n, cfg.min_hurdle_sample),
                "basis": "pre-allocation qualified-opportunity tape"}
    return {"value": AC.quantile(xs, cfg.hurdle_quantile),
            "status": "MEASURED", "sample": n, "why": None,
            "basis": "the %d%% quantile of %d qualified opportunities' "
                     "capital efficiency (prior tape %d + this batch %d), "
                     "selected and rejected alike; idle cash is NOT free"
                     % (int(cfg.hurdle_quantile * 100), n,
                        len([x for x in prior_tape or []
                             if num(x) is not None]),
                        n - len([x for x in prior_tape or []
                                 if num(x) is not None]))}


# ─────────────────────────── the rails ─────────────────────────────────

def rail_caps(cand: dict, pf: dict, rails: dict) -> dict:
    """{name: cap} of the APPROVED RAILS for this candidate against the
    portfolio as it stands (earlier approvals of the batch included). Every
    value comes from `rails` (read from the approved sources); a rail the
    approved policy does not set is NOT_SET with no number."""
    pr = (rails or {}).get("paper") or {}
    src = (rails or {}).get("sources") or {}
    keys = RK.keys_of(cand)
    exp = pf.get("exposure") or {}
    out = {}

    def put(name, usd, basis, why=None):
        out[name] = {"usd": None if usd is None else max(0.0, usd),
                     "kind": RAIL, "source": src.get(name) or src.get(
                         "paper"), "basis": basis,
                     "why": why if usd is None else None}

    avail = num(pf.get("available_usd"))
    put("AVAILABLE_CASH", avail,
        "funding boundary %s: available = cash - reserved"
        % pr.get("funding_boundary"),
        "AVAILABLE_CASH_UNREADABLE" if avail is None else None)
    frac = num(pr.get("hedge_reserve_fraction"))
    cash = num(pf.get("cash_usd"))
    if frac is None or frac <= 0:
        put("HEDGE_RESERVE", None, "an ENTRY may not spend the hedge reserve",
            R_NOT_SET)
    else:
        put("HEDGE_RESERVE", None if avail is None or cash is None
            else avail - frac * cash,
            "available - %.4f x cash (an ENTRY keeps the hedge reserve)"
            % frac, "CASH_UNREADABLE")
    poc = num(pr.get("per_order_cap_usd"))
    put("PER_ORDER_CAP", poc, "limit x qty + max fees of one order",
        R_NOT_SET)
    pmc = num(pr.get("per_market_cap_usd"))
    slug = (keys.get("MARKET") or [None])[0]
    put("PER_MARKET_CAP", None if pmc is None else
        pmc - (num((exp.get("MARKET") or {}).get(slug)) or 0.0),
        "cap - (open cost basis + open reservations) on the market",
        R_NOT_SET)
    pfc = num(pr.get("per_fixture_cap_usd"))
    fx = (keys.get("EVENT") or [None])[0]
    if pfc is None:
        put("PER_FIXTURE_CAP", None, "per-fixture concentration", R_NOT_SET)
    elif fx is None:
        # the ledger applies the fixture cap only to an order with a fixture
        put("PER_FIXTURE_CAP", None, "per-fixture concentration",
            "NO_FIXTURE_ON_THE_CANDIDATE_THE_LEDGER_SKIPS_THIS_CAP")
    else:
        put("PER_FIXTURE_CAP",
            pfc - (num((exp.get("EVENT") or {}).get(fx)) or 0.0),
            "cap - (open cost basis + open reservations) on the fixture")
    agg = num(pr.get("aggregate_exposure_cap_usd"))
    put("AGGREGATE_EXPOSURE_CAP", None if agg is None else
        agg - (num(pf.get("total_exposure_usd")) or 0.0),
        "cap - total open exposure", R_NOT_SET)
    mg = num(pr.get("max_concurrent_groups"))
    if mg is None:
        put("MAX_CONCURRENT_GROUPS", None, "concurrent groups", R_NOT_SET)
    else:
        n = int(pf.get("open_groups") or 0)
        out["MAX_CONCURRENT_GROUPS"] = {
            "usd": 0.0 if n >= int(mg) else None, "kind": RAIL,
            "source": src.get("MAX_CONCURRENT_GROUPS") or src.get("paper"),
            "basis": "%d open of %d allowed; a new entry is a new group"
                     % (n, int(mg)),
            "why": None if n >= int(mg) else "HEADROOM_IN_GROUPS"}
    if pr.get("same_strategy_same_contract"):
        k = "%s|%s|%s" % (cand.get("strategy"), slug,
                          cand.get("holding_side"))
        held = k in set(pf.get("held_contracts") or [])
        out["SAME_STRATEGY_SAME_CONTRACT"] = {
            "usd": 0.0 if held else None, "kind": RAIL,
            "source": src.get("SAME_STRATEGY_SAME_CONTRACT")
            or src.get("paper"),
            "basis": str(pr.get("same_strategy_same_contract")),
            "why": None if held else "NOT_HELD_BY_THIS_STRATEGY"}
    return out


def live_view(approved_usd: float, rails: dict) -> dict:
    """The SMALL LIVE lane's view of the same size: the canonical size at
    capital scale, capped by the live per-order rail. SMALL LIVE is SHADOW;
    the adapter applies this itself (EXPECTED_SCALE_DIFFERENCE)."""
    lv = (rails or {}).get("live") or {}
    scale, cap = num(lv.get("scale")), num(lv.get("max_order_usd"))
    if not scale or cap is None:
        return {"status": "UNAVAILABLE", "why": "LIVE_RAIL_UNREADABLE",
                "mode": lv.get("mode") or "SHADOW"}
    scaled = approved_usd / scale
    return {"status": "MEASURED", "mode": lv.get("mode") or "SHADOW",
            "scale": scale, "max_order_usd": cap,
            "scaled_usd": round(scaled, 6),
            "live_usd": round(min(scaled, cap), 6),
            "binding": ("LIVE_MAX_ORDER_USD" if cap < scaled
                        else "CANONICAL_SIZE_AT_SCALE"),
            "source": lv.get("source"),
            "applied_by": "the SMALL LIVE adapter (SHADOW)"}


# ─────────────────────────── one candidate ─────────────────────────────

def _style_capacity(cand: dict):
    """(capacity USD, qty, basis) of the SELECTED execution style (items 11,
    12): a measured zero stays zero; unmeasured -> None."""
    usd, basis = RK._capacity_usd(cand)
    lp = num(cand.get("limit_price"))
    qty = None if usd is None or not lp else usd / lp
    return usd, qty, basis


def _allie(cand: dict, pf: dict, rails: dict, tape: list, req, cap_qty):
    """ALLIE_CAPITAL_EFFICIENCY_V1 recomputed from the decision-time inputs
    with ledger-truth exposure, the selected style's capacity and the
    qualified-opportunity tape; or her recorded allocation when the inputs
    were not carried; or UNAVAILABLE."""
    ai = cand.get("allie_inputs")
    if isinstance(ai, dict):
        keys = RK.keys_of(cand)
        fx = (keys.get("EVENT") or [None])[0]
        exp = pf.get("exposure") or {}
        lv = (rails or {}).get("live") or {}
        got = AC.allocate(
            eddie_ev_usd=ai.get("eddie_ev_usd"),
            modelled_net_usd=ai.get("modelled_net_usd"),
            capital_required_usd=req,
            event_start_at=ai.get("event_start_at"),
            decided_at=ai.get("decided_at"),
            median_lag_s=ai.get("median_lag_s"), lag_n=ai.get("lag_n"),
            eddie_max_qty=cap_qty, limit_price=cand.get("limit_price"),
            displayed_depth_qty=ai.get("displayed_depth_qty"),
            fixture_open_groups=(pf.get("event_groups") or {}).get(fx, 0)
            if fx else 0,
            fixture_open_usd=(num((exp.get("EVENT") or {}).get(fx)) or 0.0)
            if fx else 0.0,
            book_open_usd=num(pf.get("total_exposure_usd")) or 0.0,
            idle_capital_usd=pf.get("available_usd"),
            recent_adjusted_ppch=list(tape),
            paper_rail_usd=((rails or {}).get("paper") or {}).get(
                "per_order_cap_usd"),
            live_rail_usd=lv.get("max_order_usd"),
            live_scale=lv.get("scale"), order_cost_usd=req)
        return got, ("RECOMPUTED: ledger-truth exposure (item 9), selected-"
                     "style capacity (item 11), qualified-opportunity tape "
                     "hurdle sample (item 7)")
    rec = cand.get("allie_recorded")
    if isinstance(rec, dict) and rec:
        return rec, ("RECORDED_AT_THE_DECISION (her inputs were not carried; "
                     "entry-nominal exposure -- items 7/9/11 not applied)")
    return ({"status": "UNAVAILABLE", "why": "NO_ALLIE_INPUTS_OR_RECORD",
             "version": AC.VERSION},
            "UNAVAILABLE")


def allocation_id(run_id: str, opportunity_id: str, snapshot_id: str) -> str:
    return "cpa_" + hashlib.sha256(("%s|%s|%s" % (
        run_id, opportunity_id, snapshot_id)).encode()).hexdigest()[:24]


def _exposure_after(cand: dict, pf: dict, approved: float) -> dict:
    keys = RK.keys_of(cand)
    exp = pf.get("exposure") or {}
    by = {}
    for d, ks in keys.items():
        by[d] = {k: round((num((exp.get(d) or {}).get(k)) or 0.0)
                          + approved, 6) for k in ks}
    avail = num(pf.get("available_usd"))
    res = num(pf.get("reserved_usd"))
    return {"by_dimension": by,
            "total_exposure_usd": round(
                (num(pf.get("total_exposure_usd")) or 0.0) + approved, 6),
            "available_usd": None if avail is None
            else round(avail - approved, 6),
            "reserved_usd": None if res is None else round(res + approved, 6),
            "open_groups": int(pf.get("open_groups") or 0)
            + (1 if approved > 0 else 0),
            "capital_base_usd": pf.get("capital_base_usd"),
            "dimensions_without_key": [d for d in RK.DIMENSIONS
                                       if d not in keys],
            "basis": pf.get("basis")}


def _apply(pf: dict, cand: dict, approved: float, group_key: str) -> None:
    """Add one approval to the batch's running portfolio (in place)."""
    if approved <= 0:
        return
    keys = RK.keys_of(cand)
    exp = pf.setdefault("exposure", {})
    for d, ks in keys.items():
        m = exp.setdefault(d, {})
        for k in ks:
            m[k] = round((num(m.get(k)) or 0.0) + approved, 6)
    pf["total_exposure_usd"] = round(
        (num(pf.get("total_exposure_usd")) or 0.0) + approved, 6)
    if num(pf.get("available_usd")) is not None:
        pf["available_usd"] = round(pf["available_usd"] - approved, 6)
    if num(pf.get("reserved_usd")) is not None:
        pf["reserved_usd"] = round(pf["reserved_usd"] + approved, 6)
    pf["open_groups"] = int(pf.get("open_groups") or 0) + 1
    fx = (keys.get("EVENT") or [None])[0]
    if fx is not None:
        eg = pf.setdefault("event_groups", {})
        eg[fx] = int(eg.get(fx) or 0) + 1
    pf.setdefault("held_contracts", []).append(group_key)


def allocate_one(cand: dict, row: dict, *, pf: dict, rails: dict,
                 hurdle_: dict, tape: list,
                 cfg: AllocatorConfig = DEFAULT_CONFIG) -> dict:
    """SIZE ONE RANKED CANDIDATE against the portfolio as it stands. Pure;
    returns the explained allocation (approved 0 with the gate when it is
    not funded). Does not modify `pf` (the batch applies the approval)."""
    p_ = cfg.precision
    caps: dict = {}
    haircuts: dict = {}
    req = num(cand.get("requested_usd"))
    unit = num(cand.get("unit_cost"))
    prob = num(cand.get("probability"))
    style_usd, style_qty, style_basis = _style_capacity(cand)
    allie, allie_basis = _allie(cand, pf, rails, tape, req, style_qty)
    net = row.get("net_ev")
    ce = row.get("capital_efficiency_component")
    ce_detail = {
        "metric": "EXPECTED_NET_PROFIT / EXPECTED_CAPITAL_HOURS",
        "expected_net_profit_usd_at_request": net,
        "capital_efficiency": ce,
        "hurdle": hurdle_,
        "beats_hurdle": (None if ce is None or hurdle_.get("value") is None
                         else ce > hurdle_["value"]),
        "role": "A GATE AND A REPORTED FIGURE; THE SIZE IS NOT CHOSEN TO "
                "MAXIMIZE IT"}

    def gated(factor, why=None):
        return _record(cand, row, req=req, approved=0.0, qty=0.0,
                       factor=factor, kind=GATE, caps=caps,
                       haircuts=haircuts, kelly_used=None, allie=allie,
                       allie_basis=allie_basis, ce_detail=ce_detail, pf=pf,
                       rails=rails, cfg=cfg, why=why, utility=None)

    # ── 1 · the gates ───────────────────────────────────────────────────
    if not row.get("admitted"):
        return gated("%s:%s" % (F_NOT_ADMITTED, row.get("admission_basis")),
                     ",".join(row.get("gate_failures") or []) or None)
    if (rails or {}).get("status") != "READ":
        return gated(F_RAILS, (rails or {}).get("why"))
    if req is None or req <= 0:
        return gated(F_NO_REQUEST)
    if unit is None or not 0.0 < unit < 1.0:
        return gated(F_NO_UNIT)
    if prob is None or not 0.0 < prob < 1.0:
        return gated(F_NO_P)
    if net is None or net <= 0:
        return gated(F_NON_POSITIVE)
    if hurdle_.get("value") is None:
        return gated(F_HURDLE_UNMEASURED, hurdle_.get("why"))
    if ce is None or ce <= hurdle_["value"]:
        return gated(F_BELOW_HURDLE, "capital efficiency %s <= hurdle %s"
                     % (ce, hurdle_["value"]))
    # ── 2 · fractional Kelly on the uncertainty-adjusted edge ───────────
    u_p = num(cand.get("probability_half_width"))
    up_basis = "MEASURED_PROBABILITY_HALF_WIDTH"
    if u_p is None:
        u_p = cfg.unmeasured_probability_uncertainty
        up_basis = ("UNMEASURED_RESEARCH_DEFAULT_%.2f"
                    % cfg.unmeasured_probability_uncertainty)
    u_e = num(cand.get("execution_cost_sd_per_contract"))
    ue_basis = "MEASURED_EXECUTION_COST_SD_PER_CONTRACT"
    if u_e is None:
        u_e = cfg.unmeasured_execution_uncertainty
        ue_basis = ("UNMEASURED_RESEARCH_DEFAULT_%.2f"
                    % cfg.unmeasured_execution_uncertainty)
    p_cons = prob - max(0.0, u_p)
    c_cons = min(0.999, unit + max(0.0, u_e))
    f_full = max(0.0, (p_cons - c_cons) / (1.0 - c_cons))
    haircuts["probability"] = {"p": prob, "half_width": _r(u_p, p_),
                               "p_conservative": _r(p_cons, p_),
                               "basis": up_basis}
    haircuts["execution"] = {"unit_cost": unit, "sd": _r(u_e, p_),
                             "cost_conservative": _r(c_cons, p_),
                             "basis": ue_basis}
    haircuts["fill"] = {
        "fill_factor": (row.get("components") or {}).get("NET_EV", {})
        .get("inputs", {}).get("fill_factor"),
        "basis": (row.get("components") or {}).get("NET_EV", {})
        .get("inputs", {}).get("fill_factor_basis"),
        "role": "in NET_EV (expected filled quantity, item 13)"}
    if f_full <= 0:
        return gated(F_NO_EDGE, "p_conservative %.4f <= cost_conservative "
                     "%.4f" % (p_cons, c_cons))
    dd = num(pf.get("drawdown_fraction"))
    if dd is None:
        m_dd, dd_basis = cfg.unmeasured_drawdown_factor, (
            "UNMEASURED_RESEARCH_DEFAULT_%.2f" % cfg.unmeasured_drawdown_factor)
    else:
        m_dd = RK.clamp01(1.0 - max(0.0, dd) / cfg.drawdown_halt_fraction)
        dd_basis = "1 - drawdown %.4f / halt %.2f (%s)" % (
            dd, cfg.drawdown_halt_fraction, pf.get("drawdown_basis"))
    rv = num((pf.get("realized_volatility") or {}).get(
        str(cand.get("strategy"))))
    model_sd = math.sqrt(prob * (1.0 - prob)) / unit
    if rv is None or rv <= 0:
        m_vol, vol_basis = cfg.unmeasured_volatility_factor, (
            "UNMEASURED_RESEARCH_DEFAULT_%.2f"
            % cfg.unmeasured_volatility_factor)
    else:
        m_vol = min(1.0, model_sd / rv)
        vol_basis = ("min(1, model per-$ sd %.4f / realized per-$ sd %.4f)"
                     % (model_sd, rv))
    kelly_used = cfg.kelly_fraction * m_dd * m_vol
    haircuts["drawdown"] = {"factor": _r(m_dd, p_), "basis": dd_basis}
    haircuts["volatility"] = {"factor": _r(m_vol, p_), "basis": vol_basis}
    base = num(pf.get("capital_base_usd"))
    if base is None or base <= 0:
        return gated(F_RAILS, "NO_CAPITAL_BASE_FROM_THE_LEDGER")
    kelly_usd = base * f_full * kelly_used
    corr_usd, corr_detail, missing = RK.correlated_exposure(
        RK.keys_of(cand), pf, cfg)
    unknown = [d for d in missing if cfg.weight(d) > 0]
    after = max(0.0, kelly_usd - corr_usd)
    if unknown:
        after *= cfg.unknown_group_factor
    haircuts["correlation"] = {
        "correlated_exposure_usd": _r(corr_usd, p_),
        "by_dimension": corr_detail,
        "dimensions_without_key": unknown,
        "unknown_group_factor": cfg.unknown_group_factor if unknown else 1.0,
        "kelly_before_correlation_usd": _r(kelly_usd, p_),
        "kelly_after_correlation_usd": _r(after, p_),
        "basis": "fractional-Kelly budget minus the weighted exposure "
                 "already held in the candidate's groups"}
    kelly_detail = {"f_full": _r(f_full, 9), "kelly_fraction": cfg.kelly_fraction,
                    "kelly_fraction_used": _r(kelly_used, 9),
                    "capital_base_usd": base,
                    "kelly_usd": _r(kelly_usd, p_)}
    # ── 3 · the caps ────────────────────────────────────────────────────
    caps["REQUEST"] = {"usd": req, "kind": REQUEST,
                       "basis": "the strategy's requested capital at its "
                                "proposed quantity"}
    caps["KELLY"] = {"usd": after, "kind": KELLY,
                     "basis": "capital base x f_full(p - u_p, c + u_e) x "
                              "kelly_fraction_used - correlated exposure",
                     "detail": kelly_detail}
    caps["CAPACITY"] = {"usd": 0.0 if style_usd is None else style_usd,
                        "kind": EVIDENCE, "basis": style_basis,
                        "why": ("CAPACITY_UNMEASURED: liquidity is never "
                                "assumed") if style_usd is None else None}
    ap = num(allie.get("allie_proposed_allocation_usd"))
    caps["ALLIE_V1_PROPOSAL"] = {
        "usd": ap, "kind": RESEARCH,
        "basis": "%s (%s); binding %s" % (AC.VERSION, allie_basis,
                                         allie.get("binding_constraint")),
        "why": None if ap is not None else (allie.get("why")
                                            or "ALLIE_UNAVAILABLE")}
    caps.update(rail_caps(cand, pf, rails))
    bound = [(c["usd"], KIND_ORDER[c["kind"]], name)
             for name, c in caps.items() if c.get("usd") is not None]
    usd_cap, _, factor = min(bound)
    qty = math.floor((usd_cap + 1e-9) / unit)
    approved = qty * unit
    while qty > 0 and approved > usd_cap + 1e-9:
        qty -= 1
        approved = qty * unit
    kind = caps[factor]["kind"]
    why = None
    if qty <= 0 and usd_cap > 0:
        why = "%s: %s binds at $%.6f, below one contract at $%.6f" % (
            F_BELOW_ONE, factor, usd_cap, unit)
    f_frac = approved / base
    utility = None
    if approved > 0 and f_frac < 1.0:
        utility = (p_cons * math.log(1.0 + f_frac * (1.0 - c_cons) / c_cons)
                   + (1.0 - p_cons) * math.log(1.0 - f_frac))
    ce_detail.update({
        "expected_net_profit_usd_at_approved": _r(
            net / req * approved if req else None, p_),
        "expected_capital_hours_at_approved": _r(
            (row.get("components") or {}).get("CAPITAL_EFFICIENCY", {})
            .get("inputs", {}).get("expected_hours_to_release") * approved
            if (row.get("components") or {}).get("CAPITAL_EFFICIENCY", {})
            .get("inputs", {}).get("expected_hours_to_release") is not None
            else None, p_)})
    return _record(cand, row, req=req, approved=approved, qty=float(qty),
                   factor=factor, kind=kind, caps=caps, haircuts=haircuts,
                   kelly_used=kelly_used, allie=allie,
                   allie_basis=allie_basis, ce_detail=ce_detail, pf=pf,
                   rails=rails, cfg=cfg, why=why, utility=utility)


def _record(cand, row, *, req, approved, qty, factor, kind, caps, haircuts,
            kelly_used, allie, allie_basis, ce_detail, pf, rails, cfg, why,
            utility) -> dict:
    p_ = cfg.precision
    return {
        "allocation_id": allocation_id(row["rank_run_id"],
                                       row["opportunity_id"],
                                       row["snapshot_id"]),
        "rank_run_id": row["rank_run_id"], "ranking_id": row["ranking_id"],
        "rank": row["rank"], "opportunity_id": row["opportunity_id"],
        "snapshot_id": row["snapshot_id"],
        "meta_decision_id": cand.get("meta_decision_id")
        or row.get("meta_decision_id"),
        "strategy": cand.get("strategy"), "as_of": row["as_of"],
        "requested_size": _r(req, p_),
        "approved_size": _r(approved, p_), "approved_qty": _r(qty, p_),
        "size_limiting_factor": factor, "size_limiting_kind": kind,
        "size_limiting_why": why,
        "caps": {k: dict(v, usd=_r(v.get("usd"), p_))
                 for k, v in caps.items()},
        "portfolio_exposure_after": _exposure_after(cand, pf, approved),
        "kelly_fraction_used": _r(kelly_used, 9),
        "uncertainty_haircuts": haircuts,
        "capital_efficiency": ce_detail,
        "expected_log_utility_gain": _r(utility, 12),
        "allie": allie, "allie_basis": allie_basis,
        "live_lane": live_view(approved, rails),
        "lane": "PAPER_CANONICAL",
        "allocator_version": VERSION, "config_sha": cfg.sha,
        "rails_sha": (rails or {}).get("rails_sha"),
        "code_sha": row["code_sha"], "source": row["source"],
        "model_versions": dict(row.get("model_versions") or {},
                               ranker=row.get("ranker_version"),
                               allie=allie.get("version") or AC.VERSION,
                               allocator=VERSION),
        "authority": AUTHORITY,
    }


# ─────────────────────────── the batch ─────────────────────────────────

def allocate_batch(run: dict, candidates: list, *, portfolio: dict,
                   rails: dict, prior_tape: list | None = None,
                   cfg: AllocatorConfig = DEFAULT_CONFIG) -> dict:
    """ALLOCATE ONE RANKED BATCH (pure, deterministic). Admitted candidates
    are sized in ADMISSION ORDER against a running copy of the portfolio
    (each approval applied before the next); every other candidate gets its
    explained zero. Returns the allocations in RANK order, the hurdle and
    the portfolio after the batch."""
    by_key = {(str(c["opportunity_id"]), str(c["snapshot_id"])): c
              for c in candidates}
    pf = _copy_portfolio(portfolio or {})
    tape = [x for x in (num(t) for t in prior_tape or []) if x is not None]
    h = hurdle(tape, run["rows"], cfg)
    tape_for_allie = tape + [r["capital_efficiency_component"]
                             for r in run["rows"] if r["gates_passed"]
                             and r["capital_efficiency_component"]
                             is not None]
    out = []
    for row in run["rows"]:                       # RANK order
        cand = by_key[(row["opportunity_id"], row["snapshot_id"])]
        alloc = allocate_one(cand, row, pf=pf, rails=rails, hurdle_=h,
                             tape=tape_for_allie, cfg=cfg)
        if alloc["approved_size"] and alloc["approved_size"] > 0:
            slug = (RK.keys_of(cand).get("MARKET") or [None])[0]
            _apply(pf, cand, alloc["approved_size"],
                   "%s|%s|%s" % (cand.get("strategy"), slug,
                                 cand.get("holding_side")))
        out.append(alloc)
    return {"allocations": out, "hurdle": h, "portfolio_after": pf,
            "allocator_version": VERSION, "config": cfg.to_dict(),
            "config_sha": cfg.sha, "rails_sha": (rails or {}).get(
                "rails_sha"), "authority": AUTHORITY}
