"""THE PAPER EXPERIMENT AS THE DEFAULT OPERATIONAL VIEW. Read-only.

What `/api/command/paper/operations?agent=derek|xavier|audrey` and
`/api/command/paper/overview` return: the management site's headline view of
the ACTIVE PAPER SESSION (one account, one ledger: paper_ledger), with the
funded system kept to its own, separately labelled, secondary section on the
pages. EVERY STATEMENT HERE IS A SELECT; nothing is written, locked or
reserved, and no funded table is read.

WHY A SEPARATE READ. The per-agent paper routes (bettor_paper_readmodel)
return the session's raw sections. Management asked for the OPERATIONAL
picture instead, and three things in it need the server:

  * DEREK'S STRATEGIES APART. The original two-model research policy
    (DEREK_ENTRY_POLICY_V2) and the two EXPERIMENTAL benchmark policies
    (PINNACLE_ONLY_PAPER_BENCHMARK, PINNACLE_COMPLETED_GAME_PAPER) are
    counted, listed and funnelled SEPARATELY, each carrying its policy
    version, its disclosure, and -- for the completed-game policy -- its
    CONDITIONAL / EXPERIMENTAL economics label. A benchmark decision is never
    counted as one of Derek's research decisions, and vice versa.
  * THE ELIGIBILITY FUNNEL, from each decision's OWN recorded conditions
    (policy_decision.conditions, in order): how many decisions passed every
    condition up to and including each stage. Nothing is recomputed.
  * THREE FRESHNESS STAMPS, never conflated:
      server_read     when this read was served (the page keeps its own "last
                      successful read" beside it);
      agent_heartbeat the paper runtime's OWN heartbeat: paper_session_health
                      .heartbeat_at (written by every paper pass that ran),
                      with the last scheduling attempt from ingestion_state
                      ['paper_session_last_pass'];
      ledger          the last committed ledger transaction: max(committed_at)
                      and max(seq) in paper_ledger for the account.
    An unchanged cash balance is not stale (the ledger stamp says when cash
    last moved, nothing more); a recent page read is not agent health.

EVERY SECTION IS INDEPENDENTLY {"status": OK | EMPTY | UNAVAILABLE, "why",
"data"}. A failed read is UNAVAILABLE with the exception named -- NEVER a
zero, never an empty list. EMPTY carries the named reason.
"""
from __future__ import annotations

import time
from typing import Any

from . import bettor_paper_ledger as L
from . import bettor_paper_readmodel as RM
from . import bettor_paper_session as S

VERSION = "PAPER_OPS_READ_V1"

#: THE PAPER RUNTIME'S CADENCE: the servicing task schedules a pass every
#: 60 s. A heartbeat older than five missed passes is STALE; the page says so
#: and never animates the agent as working.
HEARTBEAT_STALE_AFTER_S = 300.0
#: the runtime's own attempt record (agents.paper_runtime.HEARTBEAT_KEY)
LAST_ATTEMPT_KEY = "paper_session_last_pass"
RECENT_DECISIONS = 25
RECENT_ROWS = 50

TWO_MODEL = RM.TWO_MODEL                              # DEREK_ENTRY_POLICY_V2
STRICT = "PINNACLE_ONLY_PAPER_BENCHMARK"
COMPLETED_GAME = "PINNACLE_COMPLETED_GAME_PAPER"
MAKER = "PINNACLE_COMPLETED_GAME_MAKER_PAPER"
EXPLORATION = "PINNACLE_EXPLORATION_PAPER"
STRATEGY_ORDER = (TWO_MODEL, STRICT, COMPLETED_GAME, MAKER, EXPLORATION)

#: THE STRATEGIES, in the order the pages show them. `kind` is what keeps
#: them apart on the page: Derek's ORIGINAL RESEARCH decisions first, then the
#: EXPERIMENTAL BENCHMARKS in their own labelled block. Versions and
#: disclosures come from the policy module itself (`_policy_meta`).
STRATEGY_META = {
    TWO_MODEL: {
        "kind": "ORIGINAL_RESEARCH",
        "title": "Derek's original two-model research decisions",
        "label": "RESEARCH",
        "summary": ("Derek's own entry policy: the internal research model "
                    "and Pinnacle, blended. It keeps recording a decision "
                    "per evaluated market; whether it may open paper "
                    "entries is its own switch (PAPER_ENTRIES:"
                    "DEREK_ENTRY_POLICY_V2)."),
        "version": TWO_MODEL, "disclosure": None, "economics_label": None},
    STRICT: {
        "kind": "EXPERIMENTAL_BENCHMARK",
        "title": "Pinnacle-only paper benchmark (strict settlement match)",
        "label": "EXPERIMENTAL",
        "summary": ("Decides on the de-vigged Pinnacle probability alone; "
                    "every settlement condition must be compatible."),
        "version": "PINNACLE_ONLY_PAPER_BENCHMARK_V1", "disclosure": None,
        "economics_label": None},
    COMPLETED_GAME: {
        "kind": "EXPERIMENTAL_BENCHMARK",
        "title": "Completed-game paper policy",
        "label": "CONDITIONAL · EXPERIMENTAL",
        "summary": ("Pinnacle-only, matched on the ordinarily completed "
                    "game; its economics are CONDITIONAL on ordinary "
                    "completion and not risk-adjusted. Postponement, "
                    "abandonment and suspension terms are disclosed "
                    "research risks with unmeasured frequency."),
        "version": "PINNACLE_COMPLETED_GAME_PAPER_V2", "disclosure": None,
        "economics_label": "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"},
    MAKER: {
        "kind": "EXPERIMENTAL_BENCHMARK",
        "title": "Completed-game maker-entry policy (resting bids)",
        "label": "CONDITIONAL · RESTING ORDERS",
        "summary": ("The completed-game policy's match, 0.5 pp threshold and "
                    "positive after-fee rule, met by RESTING a bid below the "
                    "ask. An order is not a fill: fills are simulated only "
                    "when observed liquidity strictly crosses the bid after "
                    "the queue ahead. The taker fee is charged; the venue's "
                    "maker rebate is not assumed."),
        "version": "PINNACLE_COMPLETED_GAME_MAKER_PAPER_V1",
        "disclosure": None,
        "economics_label": "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"},
    EXPLORATION: {
        "kind": "TRAINING",
        "title": "Exploration (training / simulated execution)",
        "label": "TRAINING",
        "summary": ("A bounded training strategy that may take positions "
                    "failing the 0.5 pp edge or after-fee requirement to "
                    "generate forward experience: $1,000 average sizing target incl. "
                    "fees; available cash only, no fixture allocation cap. "
                    "No aggregate exposure cap or realized-loss stop. "
                    "Negative expected value is a research "
                    "cost, not investment performance."),
        "version": "PINNACLE_EXPLORATION_PAPER_V3", "disclosure": None,
        "economics_label":
            "EXPLORATION_RESEARCH_COST_NOT_INVESTMENT_PERFORMANCE"},
}


def _policy_meta() -> dict:
    """STRATEGY_META with the versions and disclosures read from the policy
    module (agents.paper_benchmark), so the page names exactly what the
    decision writer records. Falls back to the constants above, by name,
    in a build without it."""
    out = {k: dict(v, strategy=k) for k, v in STRATEGY_META.items()}
    try:
        from .agents import paper_benchmark as PB
    except ImportError:                                         # pragma: no cover
        return out
    out[STRICT].update(version=PB.VERSION, disclosure=PB.DISCLOSURE)
    out[COMPLETED_GAME].update(version=PB.CG_VERSION,
                               disclosure=PB.CG_DISCLOSURE,
                               economics_label=PB.ECONOMICS_LABEL)
    out[MAKER].update(version=PB.MAKER_VERSION,
                      disclosure=PB.MAKER_DISCLOSURE)
    out[EXPLORATION].update(version=PB.EXPLORE_VERSION,
                            disclosure=PB.EXPLORE_DISCLOSURE)
    return out


def _now(now) -> float:
    return float(now if now is not None else time.time())


def _base(now: float) -> dict:
    out = RM._base(now)
    out.update(version=VERSION, account_id=None,
               funded=("NOT READ HERE: the funded system is inactive and is "
                       "shown only in its own labelled section on the "
                       "pages"))
    return out


async def _sec(coro, *, empty_why: str, is_empty=None) -> dict:
    return await RM._section(coro, empty_why=empty_why, is_empty=is_empty)


async def _last_updated(conn, acct: str):
    """The ledger's last commit, or None when it cannot be read (the
    freshness section names that failure; nothing is invented here)."""
    try:
        return await RM._last_updated(conn, acct)
    except Exception:                                           # noqa: BLE001
        return None


def _unavailable(why: str) -> dict:
    return {"status": "UNAVAILABLE", "why": why, "data": None}


# ═════════════════════════════════════════════════════════════════════
# REFUSALS IN PLAIN WORDS, AND READABLE MARKET NAMES
# ═════════════════════════════════════════════════════════════════════

#: What each recorded refusal code means, in management's words. The code
#: itself stays on the record (Technical details); an unknown code is read
#: out as words, never hidden.
REFUSAL_WORDS = {
    "EXPLORATION_REALIZED_LOSS_STOP_REACHED": "exploration has reached its $1,000 realized-loss stop; no new training entries",
    "EXPLORATION_AGGREGATE_EXPOSURE_LIMIT": "exploration is at its $5,000 aggregate exposure limit",
    "EXPLORATION_ALREADY_HOLDS_THIS_FIXTURE": "exploration already holds a position in this fixture (one per fixture)",
    "FIXTURE_NOT_SELECTED_BY_THE_EXPLORATION_SAMPLE": "this fixture was not drawn by the exploration sample (recorded probability and draw)",
    "ONE_CONTRACT_EXCEEDS_THE_EXPLORATION_ENTRY_BUDGET": "a single contract would exceed the $100 exploration entry budget",
    "NO_DEPTH_AT_THE_BEST_LEVEL": "no displayed quantity at the best level",
    "EXPLORATION_LIMITS_UNREADABLE": "the exploration limits could not be read, so no training entry was made",
    "NO_RESTING_PRICE_BELOW_THE_ASK_CLEARS_THE_THRESHOLD_AND_FEES": "no resting price below the ask clears the 0.5-point threshold and the fee",
    "NO_ASK_TO_REST_BELOW": "the book showed no ask to rest a bid below",
    "A_MAKER_ENTRY_ORDER_ALREADY_RESTS_ON_THIS_FIXTURE": "a resting maker bid already stands on this fixture (one at a time)",
    "BELOW_MIN_GROSS_EDGE": "the edge was below the policy's minimum (the threshold recorded on the decision)",
    "EDGE_BELOW_5PP": "the edge was below the 5-point minimum",
    "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT": "the edge cleared the threshold but the fee per contract consumed it",
    "BELOW_MIN_NET_EV": "the expected profit after fees was below the minimum",
    "NET_EV_NOT_POSITIVE_AFTER_FEES": "no expected profit after fees",
    "FEES_NOT_ESTABLISHED": "the fees could not be established",
    "SETTLEMENT_NOT_SUPPORTED": ("the venue's and Pinnacle's settlement rules "
                                 "are not established as the same"),
    "FIXTURE_IDENTITY_NOT_ESTABLISHED": ("the game could not be matched "
                                         "exactly between Pinnacle and the "
                                         "venue"),
    "PAYOUT_OUTCOME_MATCH_NOT_ESTABLISHED": ("the outcome that pays could not "
                                             "be matched to Pinnacle's"),
    "MARKET_OR_LINE_NOT_A_MONEYLINE_MATCH": "not the same market or line",
    "GRADING_PERIOD_NOT_FULL_GAME": "not graded on the full game",
    "ORDINARY_GRADING_PERIOD_MISMATCH": ("the venue and Pinnacle grade a "
                                         "completed game differently"),
    "ORDINARY_GRADING_PERIOD_NOT_ESTABLISHED": ("how a completed game is "
                                                "graded is not established"),
    "VENUE_RULES_TEXT_NOT_RECORDED_ON_THE_VALUATION_ROW": (
        "the venue's rules text was not recorded"),
    "NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT": ("no completed-game terms are "
                                               "held for this sport"),
    "NO_QUALIFIED_PINNACLE_PROBABILITY": "no usable Pinnacle price",
    "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE": "the Pinnacle price did not qualify",
    "PINNACLE_NOT_FRESH": "the Pinnacle price was too old",
    "PROBABILITY_EVIDENCE_STALE": "the Pinnacle price was too old",
    "PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN": "the Pinnacle price's age is unknown",
    "THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT": "the venue order book reading was too old",
    "THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY": "the venue order book was empty or unreadable",
    "NO_ESTABLISHED_EXECUTABLE_DEPTH": "not enough contracts available at the price",
    "NO_SIZED_QUANTITY": "no quantity could be sized",
    "LIMIT_PRICE_NOT_SUPPORTED": "the limit price is outside what the venue supports",
    "NO_CAPACITY_UNDER_THE_LANE_RAILS": "the paper risk limits had no room",
    "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE": "another strategy already holds this game",
    "PAPER_RISK_REFUSED_THE_ORDER": "the paper risk check refused the order",
    "STRATEGY_ENTRIES_DISABLED": ("this strategy's paper entries are switched "
                                  "off (it records its decision only)"),
    "NO_APPROVED_INTERNAL_MODEL": "no approved internal model",
    "INTERNAL_MODEL_NOT_QUALIFIED": "the internal model did not qualify",
    "INTERNAL_MODEL_CANNOT_SCORE_THIS_CANDIDATE": "the internal model cannot score this market",
    "RESEARCH_MODEL_CANNOT_SCORE_THIS_CANDIDATE": "the research model cannot score this market",
    "RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED": "the research model's provenance is not verified",
    "NO_RESEARCH_MODEL_CANDIDATE_EXISTS": "no research model exists yet",
    "ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE": "the internal model disagreed with Pinnacle",
    "REAL_EVENT_NOT_ESTABLISHED": "the event is not established as real",
    "NOT_A_SUPPORTED_POLYMARKET_US_CONTRACT": "not a supported venue contract",
    "ENTRY_LANE_REFUSED": "the entry lane refused it",
}


def refusal_words(code) -> str | None:
    """A refusal code in plain words (None when there is no refusal)."""
    if not code:
        return None
    c = str(code)
    return REFUSAL_WORDS.get(c) or c.replace("_", " ").lower()


def market_name(label, *, valuation: dict | None = None,
                catalogue: dict | None = None) -> dict:
    """A READABLE NAME FOR ONE MARKET, from the decision's recorded
    instrument label (participants, event title, market type, line, period,
    date -- derek_policy.instrument_label, read from the valuation and the
    venue catalogue at the decision), then the shared catalogue resolver
    (market_labels), then the valuation row's own selection. Never a slug as
    the name; never an invented team. Logos: none -- no licensed team logo
    files exist in this repository (market_labels.LOGO_NOTE)."""
    lb = label if isinstance(label, dict) else {}
    va = valuation if isinstance(valuation, dict) else {}
    ca = catalogue if isinstance(catalogue, dict) else {}
    home, away = lb.get("home_team"), lb.get("away_team")
    title = (lb.get("event_title")
             or ("%s at %s" % (away, home) if home and away else None)
             or (ca.get("secondary") or "").split(" · ")[0] or None)
    sel = lb.get("participant") or va.get("contract_selection")
    mt = lb.get("market_type")
    if mt and str(mt).upper() in ("MONEYLINE", "H2H"):
        mt = "to win"
    selection = None
    if sel:
        selection = "%s %s" % (sel, mt or "") if mt else str(sel)
        if lb.get("line") not in (None, ""):
            selection += " %s" % lb.get("line")
    elif ca.get("primary"):
        selection = ca.get("primary")
    bits = [lb.get("competition"), lb.get("period"), lb.get("event_date")]
    source = ("decision label" if (lb.get("event_title") or home
                                   or lb.get("participant"))
              else "venue catalogue" if ca.get("primary")
              else "valuation row" if sel else None)
    warnings = list(ca.get("warnings") or []) if not source == "decision label" else []
    if not title and not selection:
        warnings.append("METADATA_INCOMPLETE: no participant, event title or "
                        "catalogue row recorded for this market")
    return {"title": title, "selection": (selection or "").strip() or None,
            "detail": " · ".join(str(b) for b in bits if b) or None,
            "source": source, "warnings": warnings, "logo": None,
            "logo_note": ("no licensed team logo files exist in this "
                          "repository; none is shown")}


async def _names_for(conn, rows: list, *, slug_key="us_market_slug",
                     intent_key="intent") -> None:
    """Attach `market` (market_name) to each row in place: the record's
    label first, the venue catalogue for rows whose label names nothing."""
    from . import market_labels as ML
    need = [r for r in rows if not ((r.get("label") or {}).get("event_title")
                                    or (r.get("label") or {}).get("participant")
                                    or (r.get("label") or {}).get("home_team"))]
    cat = {}
    if need:
        try:
            got = await ML.resolve_many(conn, [
                {"market_slug": r.get(slug_key), "intent": r.get(intent_key)}
                for r in need])
            cat = {id(r): g for r, g in zip(need, got)
                   if g and g.get("source") != "NO_CATALOGUE_ROW"}
        except Exception:                                       # noqa: BLE001
            cat = {}
    for r in rows:
        r["market"] = market_name(r.get("label"), valuation=r.get("valuation"),
                                  catalogue=cat.get(id(r)))


async def account_section(conn, *, account_id: str, now: float) -> dict:
    """THE ONE ACCOUNT READ every page shows (homepage, Derek, Xavier,
    Audrey): bettor_paper_ledger.balances over the one paper ledger, the
    same function and the same keys everywhere, so the four pages can never
    disagree about cash, reserved, available, position value, equity or
    P&L."""
    async def acct():
        b = await L.balances(conn, account_id, now=now)
        if not b.get("ok"):
            raise RuntimeError(b.get("refusal") or "BALANCES_REFUSED")
        return dict(account_view(b), source=(
            "bettor_paper_ledger.balances(%s) at %s" % (account_id, now)))
    return await _sec(acct(), empty_why="NO_PAPER_ACCOUNT")


# ═════════════════════════════════════════════════════════════════════
# THE THREE FRESHNESS STAMPS
# ═════════════════════════════════════════════════════════════════════

async def freshness(conn, *, account_id: str, now: float) -> dict:
    """{server_read, agent_heartbeat, ledger}: three independent sections.
    None of them stands in for another."""

    async def heartbeat():
        sess = await S.active_session(conn, account_id)
        h = None if sess is None else await S.health(conn, sess["session_id"])
        attempt = None
        try:
            v = await conn.fetchval(
                "SELECT value FROM ingestion_state WHERE key = $1",
                LAST_ATTEMPT_KEY)
            v = L._j(v) if isinstance(v, str) else v
            if isinstance(v, dict):
                attempt = {"written_at": v.get("written_at"),
                           "ran": v.get("ran"),
                           "refusal": v.get("refusal"), "why": v.get("why"),
                           "at": v.get("at"), "errors": v.get("errors")}
        except Exception:                                       # noqa: BLE001
            attempt = None
        if sess is None and attempt is None:
            return None
        hb = (h or {}).get("heartbeat_at")
        lp = (h or {}).get("last_pass") or {}
        return {
            "session_id": None if sess is None else sess["session_id"],
            "heartbeat_at": hb,
            "age_s": None if hb is None else round(now - float(hb), 3),
            "stale_after_s": HEARTBEAT_STALE_AFTER_S,
            "stale": None if hb is None else (
                now - float(hb) > HEARTBEAT_STALE_AFTER_S),
            "passes": (h or {}).get("passes"),
            "errors": (h or {}).get("errors"),
            "last_error": (h or {}).get("last_error"),
            "last_pass_at": lp.get("at"),
            "last_pass_errors": lp.get("errors"),
            "last_attempt": attempt,
            "source": ("paper_session_health.heartbeat_at, written by every "
                       "paper pass that ran; the last scheduling attempt "
                       "(ran or refused) from ingestion_state['%s']"
                       % LAST_ATTEMPT_KEY)}

    async def ledger():
        r = await conn.fetchrow(
            "SELECT max(committed_at) AS at, max(seq) AS seq, count(*) AS n "
            "  FROM paper_ledger WHERE account_id = $1", account_id)
        if r is None or r["seq"] is None:
            return None
        last = await conn.fetchrow(
            "SELECT kind, committed_at FROM paper_ledger WHERE account_id = $1"
            " ORDER BY seq DESC LIMIT 1", account_id)
        at = L._epoch(r["at"])
        return {"committed_at": at, "sequence": int(r["seq"]),
                "entries": int(r["n"]),
                "kind": None if last is None else last["kind"],
                "age_s": None if at is None else round(now - at, 3),
                "source": ("max(committed_at) and max(seq) of the account's "
                           "rows in the one paper ledger"),
                "note": ("when cash or reservations last moved; an unchanged "
                         "balance is not a stale one")}

    return {
        "server_read": {"status": "OK", "why": None, "data": {
            "at": now, "source": ("the API's clock when it served this read; "
                                  "the page keeps the time of its own last "
                                  "successful read beside it"),
            "note": "a recent read is not agent health"}},
        "agent_heartbeat": await _sec(
            heartbeat(), empty_why=("NO_PAPER_HEARTBEAT_YET: no active paper "
                                    "session and no recorded pass attempt")),
        "ledger": await _sec(
            ledger(), empty_why="NO_LEDGER_ENTRY_FOR_THE_PAPER_ACCOUNT"),
    }


async def _session_brief(conn, *, account_id: str) -> dict:
    sess = await S.active_session(conn, account_id)
    en = await S.enablement(conn)
    return {"active": bool(sess is not None and en.get("enabled")),
            "session_id": None if sess is None else sess["session_id"],
            "started_at": None if sess is None else sess["started_at"],
            "status": None if sess is None else sess.get("status"),
            "enablement": en,
            "reason": (None if sess is not None and en.get("enabled") else
                       en.get("refusal") or "NO_ACTIVE_PAPER_SESSION_YET"),
            "real_money_submission": "DISABLED"}


# ═════════════════════════════════════════════════════════════════════
# DEREK: THE STRATEGIES APART, THE FUNNEL, THE RECENT DECISIONS
# ═════════════════════════════════════════════════════════════════════

def decision_view(r) -> dict:
    """One decision, as the operational list shows it: verdict, refusal,
    edge (pp, from the record's own policy decision), modelled net EV and
    the one-line explanation (bettor_paper_readmodel.explanation)."""
    d = RM._row(r)
    pd = d.pop("policy_decision", None)
    if isinstance(pd, str):
        pd = L._j(pd)
    pd = pd if isinstance(pd, dict) else {}
    d["strategy"] = d.get("strategy") or TWO_MODEL
    d["edge_pp"] = pd.get("gross_edge_pp")
    d["net_ev_usd"] = pd.get("net_expected_profit_usd")
    d["fees_usd"] = pd.get("fees_usd")
    d["economics_label"] = pd.get("economics_label")
    d["explanation"] = RM.explanation(d, pd)
    d["refusal_words"] = refusal_words(d.get("refusal"))
    eco = d.pop("economics", None)
    eco = L._j(eco) if isinstance(eco, str) else eco
    acq = (eco or {}).get("acquisition") if isinstance(eco, dict) else None
    acq = acq if isinstance(acq, dict) else {}
    # THE PURCHASE PRICE: the volume-weighted price the walk would pay, else
    # the limit; the fees from the record (policy decision, else acquisition)
    d["purchase_price"] = acq.get("vwap") if acq.get("vwap") is not None \
        else d.get("limit_price")
    if d.get("fees_usd") is None:
        d["fees_usd"] = acq.get("fees_usd")
    # THE SAME RECORD'S OWN FIGURES WHEN THE TOP-LEVEL KEY IS EMPTY: a
    # refusal stores its gross edge in the shortfall (or the economics' best
    # level), an exploration decision in its estimate. Display only -- each
    # value is labelled with where it came from; nothing is recomputed.
    sh = pd.get("shortfall") if isinstance(pd.get("shortfall"), dict) else {}
    est = pd.get("estimate") if isinstance(pd.get("estimate"), dict) else {}
    src = {}
    if d.get("edge_pp") is None:
        for key, val in (("estimate.gross_edge_pp_at_best",
                          est.get("gross_edge_pp_at_best")),
                         ("shortfall.edge_pp", sh.get("edge_pp")),
                         ("economics.best_level_edge_pp",
                          (eco or {}).get("best_level_edge_pp")
                          if isinstance(eco, dict) else None)):
            if val is not None:
                d["edge_pp"], src["edge_pp"] = val, key
                break
    if d.get("fees_usd") is None and est.get("fees_usd") is not None:
        d["fees_usd"], src["fees_usd"] = est["fees_usd"], "estimate.fees_usd"
    if d.get("net_ev_usd") is None:
        for key, val in (("estimate.expected_net_profit_usd",
                          est.get("expected_net_profit_usd")),
                         ("shortfall.ev_after_fees_usd",
                          sh.get("ev_after_fees_usd"))):
            if val is not None:
                d["net_ev_usd"], src["net_ev_usd"] = val, key
                break
    if d.get("purchase_price") is None and isinstance(eco, dict):
        lv = eco.get("levels") or []
        if lv and isinstance(lv[0], dict) and lv[0].get("price") is not None:
            d["purchase_price"] = lv[0]["price"]
            src["purchase_price"] = "economics.levels[0].price (best level)"
    d["figure_sources"] = src
    va = {k: d.pop(k) for k in ("v_contract_selection", "v_event_key",
                                "v_sport_family", "v_market", "v_period")
          if k in d}
    d["valuation"] = {k[2:]: v for k, v in va.items()}
    return d


DECISION_COLS = (
    "d.decision_id, d.decided_at, d.strategy, d.policy_version, d.verdict, "
    "d.refusal, d.refusals, d.us_market_slug, d.holding_side, d.intent, "
    "d.fixture, d.label, d.p_internal, d.p_pinnacle, d.p_blended, "
    "d.limit_price, d.proposed_qty, d.policy_decision, d.economics, "
    "v.contract_selection AS v_contract_selection, v.event_key AS "
    "v_event_key, v.sport_family AS v_sport_family, v.market AS v_market, "
    "v.period AS v_period")
#: the valuation row a decision was made from, when the table exists
DECISION_FROM = ("paper_decisions d LEFT JOIN external_valuations v "
                 "ON v.id = d.valuation_id")

FUNNEL_SQL = """
    WITH d AS (
        SELECT decision_id,
               CASE WHEN jsonb_typeof(policy_decision->'conditions') = 'array'
                    THEN policy_decision->'conditions' ELSE '[]'::jsonb END
                 AS c
          FROM paper_decisions WHERE account_id = $1 AND strategy = $2),
    x AS (
        SELECT d.decision_id, e.n, e.y->>'condition' AS cond,
               e.y->>'passed' AS passed
          FROM d, jsonb_array_elements(d.c) WITH ORDINALITY AS e(y, n)),
    f AS (
        SELECT decision_id,
               min(n) FILTER (WHERE passed IS DISTINCT FROM 'true') AS first_fail
          FROM x GROUP BY decision_id)
    SELECT x.n, min(x.cond) AS condition,
           count(*) FILTER (WHERE f.first_fail IS NULL OR f.first_fail > x.n)
             AS passed,
           count(*) FILTER (WHERE f.first_fail = x.n AND x.passed = 'false')
             AS failed_here,
           count(*) FILTER (WHERE f.first_fail = x.n AND x.passed IS NULL)
             AS not_evaluated_here
      FROM x JOIN f USING (decision_id)
     GROUP BY x.n ORDER BY x.n
"""


async def strategy_block(conn, *, account_id: str, strategy: str,
                         meta: dict, now: float,
                         limit: int = RECENT_DECISIONS) -> dict:
    """One strategy, every part its own section."""

    async def counts():
        r = await conn.fetchrow(
            "SELECT count(*) AS n, "
            "       count(*) FILTER (WHERE verdict = 'ENTER') AS enter, "
            "       count(*) FILTER (WHERE verdict = 'REFUSE') AS refuse, "
            "       count(*) FILTER (WHERE decided_at > $3) AS last_24h, "
            "       count(*) FILTER (WHERE decided_at > $3 "
            "                          AND verdict = 'ENTER') AS enter_24h, "
            "       count(DISTINCT us_market_slug) AS markets, "
            "       min(decided_at) AS first_at, max(decided_at) AS latest_at,"
            "       array_agg(DISTINCT policy_version) AS versions "
            "  FROM paper_decisions WHERE account_id = $1 AND strategy = $2",
            account_id, strategy, L._ts(now - 86400.0))
        if r is None or not r["n"]:
            return None
        return {"decisions": int(r["n"]), "enter": int(r["enter"]),
                "refuse": int(r["refuse"]), "last_24h": int(r["last_24h"]),
                "enter_24h": int(r["enter_24h"]),
                "markets": int(r["markets"]),
                "first_at": L._epoch(r["first_at"]),
                "latest_at": L._epoch(r["latest_at"]),
                "policy_versions": sorted(v for v in (r["versions"] or [])
                                          if v)}

    async def refusals():
        rows = await conn.fetch(
            "SELECT refusal, count(*) AS n, max(decided_at) AS latest_at "
            "  FROM paper_decisions WHERE account_id = $1 AND strategy = $2 "
            "   AND verdict = 'REFUSE' GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
            account_id, strategy)
        return [{"refusal": r["refusal"], "n": int(r["n"]),
                 "refusal_words": refusal_words(r["refusal"]),
                 "latest_at": L._epoch(r["latest_at"])} for r in rows]

    async def recent():
        rows = await conn.fetch(
            "SELECT %s FROM %s WHERE d.account_id = $1 "
            "   AND d.strategy = $2 ORDER BY d.decided_at DESC LIMIT $3"
            % (DECISION_COLS, DECISION_FROM), account_id, strategy,
            int(limit))
        out = [decision_view(r) for r in rows]
        await _names_for(conn, out)
        return out

    async def orders():
        """EVERY ENTRY ORDER of this strategy (newest first): the decision's
        Pinnacle probability and edge, the purchase price actually filled
        (volume-weighted over its simulated fills), the fees paid, the state
        and -- for an order that ended unfilled -- its reason in words."""
        rows = await conn.fetch(
            "SELECT o.order_id, o.created_at, o.state, o.qty, o.filled_qty, "
            "       o.limit_price, o.terminal_reason, o.us_market_slug, "
            "       o.intent, o.fixture, o.label, o.group_id, o.decision_id, "
            "       d.p_pinnacle, d.policy_decision->>'gross_edge_pp' AS edge, "
            "       d.verdict, d.policy_version, "
            "       (SELECT sum(f.gross_usd) / nullif(sum(f.qty), 0) "
            "          FROM paper_fills f WHERE f.order_id = o.order_id) "
            "         AS avg_fill_price, "
            "       (SELECT sum(f.fee_usd) FROM paper_fills f "
            "         WHERE f.order_id = o.order_id) AS fees_usd, "
            "       (SELECT sum(f.gross_usd) FROM paper_fills f "
            "         WHERE f.order_id = o.order_id) AS cost_usd "
            "  FROM paper_orders o LEFT JOIN paper_decisions d "
            "    ON d.decision_id = o.decision_id "
            " WHERE o.account_id = $1 AND o.strategy = $2 "
            "   AND o.role = 'ENTRY' ORDER BY o.created_at DESC LIMIT $3",
            account_id, strategy, int(limit))
        out = []
        for r in rows:
            x = RM._row(r)
            x["edge_pp"] = None if x.pop("edge", None) is None \
                else float(r["edge"])
            x["unfilled_reason_words"] = refusal_words(
                x.get("terminal_reason")) if not x.get("filled_qty") else None
            out.append(x)
        await _names_for(conn, out)
        return out

    async def funnel():
        total = await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE account_id = $1 "
            "   AND strategy = $2", account_id, strategy)
        if not total:
            return None
        rows = await conn.fetch(FUNNEL_SQL, account_id, strategy)
        no_cond = await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE account_id = $1 "
            "   AND strategy = $2 AND (policy_decision IS NULL OR "
            "   jsonb_typeof(policy_decision->'conditions') IS DISTINCT FROM "
            "   'array')", account_id, strategy)
        dn = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE TRUE) AS orders, "
            "       count(*) FILTER (WHERE filled_qty > 0) AS filled "
            "  FROM paper_orders WHERE account_id = $1 AND strategy = $2 "
            "   AND role = 'ENTRY'", account_id, strategy)
        ho = await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE account_id = $1 "
            "   AND strategy = $2", account_id, strategy)
        enter = await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE account_id = $1 "
            "   AND strategy = $2 AND verdict = 'ENTER'", account_id, strategy)
        stages = [{"stage": "decisions recorded", "n": int(total),
                   "kind": "RECORDED"}]
        stages += [{"stage": r["condition"], "n": int(r["passed"]),
                    "failed_here": int(r["failed_here"]),
                    "not_evaluated_here": int(r["not_evaluated_here"]),
                    "kind": "CONDITION", "order": int(r["n"])} for r in rows]
        stages += [{"stage": "verdict ENTER", "n": int(enter),
                    "kind": "VERDICT"},
                   {"stage": "paper entry orders", "n": int(dn["orders"]),
                    "kind": "ORDER"},
                   {"stage": "orders with a simulated fill",
                    "n": int(dn["filled"]), "kind": "FILL"},
                   {"stage": "handed to Xavier", "n": int(ho),
                    "kind": "HANDOFF"}]
        return {"stages": stages, "without_conditions": int(no_cond or 0),
                "basis": ("each decision's own recorded policy_decision."
                          "conditions, in order: a stage counts the decisions "
                          "that passed it AND every stage before it; nothing "
                          "is recomputed")}

    e24 = "NO_DECISION_RECORDED_FOR_%s" % strategy
    return dict(
        meta,
        counts=await _sec(counts(), empty_why=e24),
        refusals=await _sec(refusals(), empty_why=(
            "NO_REFUSAL_RECORDED_FOR_%s" % strategy)),
        recent=await _sec(recent(), empty_why=e24),
        funnel=await _sec(funnel(), empty_why=e24),
        orders=await _sec(orders(), empty_why=(
            "NO_PAPER_ENTRY_ORDER_FOR_%s: no decision of this strategy "
            "has opened a paper entry" % strategy)))


async def derek_operations(conn, *, account_id: str | None = None,
                           now: float | None = None,
                           limit: int = RECENT_DECISIONS) -> dict:
    acct = account_id or L.ACCOUNT_ID
    at = _now(now)
    out = _base(at)
    out["account_id"] = acct
    meta = _policy_meta()

    async def switches():
        rows = await conn.fetch(
            "SELECT control_key, enabled, why, updated_by, updated_at "
            "  FROM paper_control ORDER BY control_key")
        return [RM._row(r) for r in rows]

    out["controls"] = await _sec(switches(), empty_why="NO_PAPER_CONTROL_ROW")
    out["strategies"] = [
        await strategy_block(conn, account_id=acct, strategy=s, meta=meta[s],
                             now=at, limit=limit)
        for s in STRATEGY_ORDER]
    out["account"] = await account_section(conn, account_id=acct, now=at)

    async def standing():
        # DEREK'S STANDING ENTRY ORDERS (resting maker bids and marketable
        # entries awaiting their book): price, size, rationale, expiry and
        # cancellation conditions, as persisted. An order is not a fill.
        from . import bettor_paper_experiment as EXP
        return (await EXP.standing_orders(conn, at, acct))["entry_orders"]
    out["standing_entry_orders"] = await _sec(standing(), empty_why=(
        "NO_OPEN_ENTRY_ORDER: Derek has no resting or pending entry order "
        "right now"))
    out["logos"] = ("no licensed team logo files exist in this repository; "
                    "markets are named from the records, without logos")
    out["separation"] = ("ORIGINAL_RESEARCH (DEREK_ENTRY_POLICY_V2) and each "
                         "EXPERIMENTAL_BENCHMARK are counted apart: a "
                         "benchmark decision is never one of Derek's "
                         "research decisions")
    out["freshness"] = await freshness(conn, account_id=acct, now=at)
    out["last_updated_at"] = await _last_updated(conn, acct)
    return out


# ═════════════════════════════════════════════════════════════════════
# XAVIER: EVERY HANDOFF, HIS REVIEWS, PROTECTION, SETTLEMENTS, PENDING
# ═════════════════════════════════════════════════════════════════════

async def xavier_operations(conn, *, account_id: str | None = None,
                            now: float | None = None,
                            limit: int = RECENT_ROWS) -> dict:
    acct = account_id or L.ACCOUNT_ID
    at = _now(now)
    out = _base(at)
    out["account_id"] = acct
    bal_box: dict[str, Any] = {}

    async def bal():
        if "b" not in bal_box:
            bal_box["b"] = await L.balances(conn, acct, now=at)
        b = bal_box["b"]
        if not b.get("ok"):
            raise RuntimeError(b.get("refusal") or "BALANCES_REFUSED")
        return b

    async def handoffs():
        if "h" in bal_box:
            return bal_box["h"]
        rows = await conn.fetch(
            "SELECT h.handoff_id, h.group_id, h.decision_id, h.entry_order_id,"
            "       h.first_fill_id, h.first_fill_at, h.owner, h.confirmed_qty,"
            "       h.outstanding_qty, h.created_at, h.updated_at, h.strategy,"
            "       o.us_market_slug, o.holding_side, o.intent, o.fixture, "
            "       o.label, o.limit_price, o.qty AS ordered_qty "
            "  FROM paper_handoffs h LEFT JOIN paper_orders o "
            "    ON o.order_id = h.entry_order_id "
            " WHERE h.account_id = $1 ORDER BY h.created_at DESC LIMIT $2",
            acct, int(limit))
        out = [RM._row(r) for r in rows]
        await _names_for(conn, out)
        bal_box["h"] = out
        return out

    async def handoff_counts():
        rows = await conn.fetch(
            "SELECT strategy, count(*) AS n, sum(confirmed_qty) AS qty, "
            "       max(created_at) AS latest_at FROM paper_handoffs "
            " WHERE account_id = $1 GROUP BY 1 ORDER BY 1", acct)
        return [RM._row(r) for r in rows]

    async def positions():
        return (await bal()).get("open_positions") or []

    async def reviews():
        rows = await conn.fetch(
            "SELECT review_id, group_id, reviewed_at, trigger, recommendation,"
            "       refusal, strategy, action, standing, measure, exceptional "
            "  FROM paper_xavier_reviews WHERE account_id = $1 "
            " ORDER BY reviewed_at DESC LIMIT $2", acct, int(limit))
        return [RM._row(r) for r in rows]

    async def review_counts():
        rows = await conn.fetch(
            "SELECT strategy, coalesce(recommendation, 'REFUSED: ' || "
            "       coalesce(refusal, '?')) AS recommendation, count(*) AS n, "
            "       max(reviewed_at) AS latest_at FROM paper_xavier_reviews "
            " WHERE account_id = $1 GROUP BY 1, 2 ORDER BY 1, 3 DESC", acct)
        return [RM._row(r) for r in rows]

    async def standing():
        rows = await conn.fetch(
            "SELECT * FROM paper_orders WHERE account_id = $1 "
            "   AND role <> 'ENTRY' ORDER BY created_at DESC LIMIT $2",
            acct, int(limit))
        return [dict(L.order_view(r), strategy=r["strategy"],
                     open=r["state"] in L.OPEN_STATES) for r in rows]

    async def standing_summary():
        rows = await conn.fetch(
            "SELECT strategy, role, state, count(*) AS n FROM paper_orders "
            " WHERE account_id = $1 AND role <> 'ENTRY' "
            " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3", acct)
        return [dict(RM._row(r), open=r["state"] in L.OPEN_STATES)
                for r in rows]

    async def settlements():
        rows = await conn.fetch(
            "SELECT s.settlement_id, s.position_key, s.version, s.supersedes,"
            "       s.group_id, s.us_market_slug, s.holding_side, s.qty, "
            "       s.outcome, s.payout_per_contract, s.payout_usd, "
            "       s.evidence_source, s.settled_at, "
            "       (SELECT o.strategy FROM paper_orders o "
            "         WHERE o.account_id = s.account_id "
            "           AND o.group_id = s.group_id AND o.role = 'ENTRY' "
            "         LIMIT 1) AS strategy "
            "  FROM paper_settlements s WHERE s.account_id = $1 "
            " ORDER BY s.settled_at DESC LIMIT $2", acct, int(limit))
        return [RM._row(r) for r in rows]

    async def settlement_counts():
        rows = await conn.fetch(
            "SELECT coalesce((SELECT o.strategy FROM paper_orders o "
            "         WHERE o.account_id = s.account_id "
            "           AND o.group_id = s.group_id AND o.role = 'ENTRY' "
            "         LIMIT 1), 'UNKNOWN') AS strategy, s.outcome, "
            "       count(*) AS n, sum(s.payout_usd) AS payout_usd "
            "  FROM paper_settlements s WHERE s.account_id = $1 "
            " GROUP BY 1, 2 ORDER BY 1, 2", acct)
        return [RM._row(r) for r in rows]

    async def pending():
        b = await bal()
        openp = [p for p in (b.get("open_positions") or [])
                 if p.get("settlement") is None]
        last = None
        sess = await S.active_session(conn, acct)
        if sess is not None:
            h = await S.health(conn, sess["session_id"]) or {}
            st = ((h.get("last_pass") or {}).get("steps") or {}).get(
                "settle")
            if isinstance(st, dict):
                last = dict(st, at=(h.get("last_pass") or {}).get("at"))
        return {"positions": [dict({k: p.get(k) for k in (
                    "position_key", "group_id", "us_market_slug",
                    "holding_side", "fixture", "label", "strategy",
                    "open_qty", "cost_basis_usd", "first_fill_at")},
                    market=market_name(p.get("label")))
                    for p in openp],
                "count": len(openp),
                "last_settle_step": last,
                "rule": ("an open position stays pending until the venue's "
                         "authoritative settlement is recorded; the completed-"
                         "game policy's exceptional case settles at the "
                         "venue's own published price "
                         "(SETTLED_AT_VENUE_PRICE), never an assumed refund")}

    async def owned():
        """EVERY POSITION XAVIER OWNS (one per handoff), with its current
        exit recommendation (his latest review: recommendation, selection),
        its exposure, its scenario payoffs and -- kept apart -- the profit
        already REALIZED versus the risk that REMAINS."""
        b = await bal()
        hs = await handoffs()
        allp = {p["group_id"]: p for p in await L.positions(
            conn, acct, include_closed=True)}
        marks = {p["group_id"]: p for p in (b.get("open_positions") or [])}
        revs = {r["group_id"]: RM._row(r) for r in await conn.fetch(
            "SELECT DISTINCT ON (group_id) group_id, review_id, reviewed_at, "
            "       trigger, recommendation, refusal, selection, action, "
            "       exposure, measure FROM paper_xavier_reviews "
            " WHERE account_id = $1 ORDER BY group_id, reviewed_at DESC",
            acct)}
        stand = {}
        for r in await conn.fetch(
                "SELECT group_id, count(*) AS n FROM paper_orders "
                " WHERE account_id = $1 AND role <> 'ENTRY' "
                "   AND state = ANY($2::text[]) GROUP BY 1", acct,
                list(L.OPEN_STATES)):
            stand[r["group_id"]] = int(r["n"])
        eco = {}
        ids = [h["decision_id"] for h in hs if h.get("decision_id")]
        if ids:
            for r in await conn.fetch(
                    "SELECT decision_id, economics, policy_decision, "
                    "       p_pinnacle FROM paper_decisions "
                    " WHERE decision_id = ANY($1::text[])", ids):
                e = L._j(r["economics"]) if isinstance(r["economics"], str) \
                    else r["economics"]
                pd = L._j(r["policy_decision"]) if isinstance(
                    r["policy_decision"], str) else r["policy_decision"]
                eco[r["decision_id"]] = (e or {}, pd or {},
                                         r["p_pinnacle"])
        out = []
        for h in hs:
            g = h["group_id"]
            p = allp.get(g) or {}
            m = marks.get(g) or {}
            e, pd, p_pin = eco.get(h.get("decision_id"), ({}, {}, None))
            acq = (e.get("acquisition") or {}) if isinstance(e, dict) else {}
            oq = float(p.get("open_qty") or 0.0)
            basis = float(p.get("cost_basis_usd") or 0.0)
            avg = float(p.get("avg_cost_per_contract_incl_fees") or 0.0)
            exc = (acq.get("exceptional_settlement") or {}) if isinstance(
                acq, dict) else {}
            scen = []
            for cond, sc in sorted((exc.get("scenarios") or {}).items()):
                rng = sc.get("payoff_per_contract_range")
                scen.append({
                    "condition": cond, "venue_payout": sc.get("venue_payout"),
                    "payoff_basis": sc.get("payoff_basis"),
                    "probability": "UNMEASURED",
                    "payoff_range_usd": (None if rng is None or oq <= 0 else
                                         [round(oq * (0.0 - avg) if rng[0] < 0
                                                else 0.0, 6),
                                          round(oq * (1.0 - avg) if rng[1] > 0
                                                else 0.0, 6)]),
                    "note": sc.get("probability_note")})
            rv = revs.get(g)
            out.append({
                "group_id": g, "strategy": h.get("strategy"),
                "market": h.get("market"), "label": h.get("label"),
                "us_market_slug": h.get("us_market_slug"),
                "intent": h.get("intent"), "decision_id": h.get("decision_id"),
                "first_fill_at": h.get("first_fill_at"),
                "confirmed_qty": h.get("confirmed_qty"),
                "status": ("SETTLED: %s" % (p.get("settlement") or {}).get(
                    "outcome") if p.get("settlement") else
                    "OPEN" if oq > 1e-9 else "CLOSED"),
                "realized": {
                    "realized_pnl_usd": p.get("realized_pnl_usd"),
                    "settlement": p.get("settlement"),
                    "sale_proceeds_net_usd": p.get("sale_proceeds_net_usd"),
                    "basis": ("booked on the ledger from sales and "
                              "settlements; never a floor or a scenario")},
                "remaining": None if oq <= 1e-9 else {
                    "open_qty": oq, "cost_basis_usd": basis,
                    "avg_cost_incl_fees": avg,
                    "marked_value_usd": m.get("marked_value_usd"),
                    "unrealized_pnl_usd": m.get("unrealized_pnl_usd"),
                    "mark": m.get("mark"),
                    "ordinary_completion": {
                        "win_usd": round(oq * 1.0 - basis, 6),
                        "lose_usd": round(-basis, 6),
                        "p_pinnacle_at_decision": p_pin,
                        "basis": ("the open contracts pay $1 each if the "
                                  "selection wins an ordinarily completed "
                                  "game, $0 if it loses; less the cost "
                                  "basis incl. fees")},
                    "conditional_ev_at_decision_usd": acq.get(
                        "expected_net_profit_usd"),
                    "economics_label": acq.get("label") or pd.get(
                        "economics_label"),
                    "exceptional_settlement": scen,
                    "exceptional_note": (
                        "exceptional-settlement probabilities are UNMEASURED: "
                        "never zero, never invented; not in the conditional "
                        "EV" if scen else
                        "no exceptional-settlement scenarios recorded on the "
                        "entry decision (the strict policy requires every "
                        "settlement condition to be compatible)"),
                    "open_management_orders": stand.get(g, 0)},
                "recommendation": None if rv is None else {
                    "recommendation": rv.get("recommendation"),
                    "refusal": rv.get("refusal"),
                    "refusal_words": refusal_words(rv.get("refusal")),
                    "selection": rv.get("selection"),
                    "trigger": rv.get("trigger"),
                    "reviewed_at": rv.get("reviewed_at"),
                    "action": rv.get("action"), "review_id": rv.get("review_id")}})
        return out

    async def exposure():
        b = await bal()
        op = b.get("open_positions") or []
        basis = sum(float(p.get("cost_basis_usd") or 0) for p in op)
        return {"open_positions": len(op),
                "cost_basis_at_risk_usd": round(basis, 6),
                "max_loss_usd": round(-basis, 6),
                "max_gain_usd": round(sum(float(p.get("open_qty") or 0)
                                          for p in op) - basis, 6),
                "marked_value_usd": b.get("open_position_value_usd"),
                "marked_value_marked_only_usd": b.get(
                    "open_position_value_marked_only_usd"),
                "unrealized_pnl_usd": b.get("unrealized_pnl_usd"),
                "reserved_for_open_orders_usd": b.get("reserved_usd"),
                "realized_pnl_usd": b.get("realized_pnl_usd"),
                "basis": ("max loss/gain if every open contract loses/wins "
                          "an ordinarily completed game; realized P&L is "
                          "booked and kept apart")}

    out["handoffs"] = await _sec(handoffs(), empty_why=(
        "NO_PAPER_HANDOFF: Xavier takes a group from its first simulated "
        "fill"))
    out["handoff_counts"] = await _sec(handoff_counts(),
                                       empty_why="NO_PAPER_HANDOFF")
    out["positions"] = await _sec(positions(),
                                  empty_why="NO_OPEN_PAPER_POSITION")
    out["reviews"] = await _sec(reviews(),
                                empty_why="NO_XAVIER_PAPER_REVIEW_YET")
    out["review_counts"] = await _sec(review_counts(),
                                      empty_why="NO_XAVIER_PAPER_REVIEW_YET")
    out["standing_orders"] = await _sec(standing(), empty_why=(
        "NO_PAPER_MANAGEMENT_ORDER: no standing protection, hedge, exit or "
        "reduce order has been simulated"))
    out["standing_summary"] = await _sec(standing_summary(),
                                         empty_why="NO_PAPER_MANAGEMENT_ORDER")
    out["settlements"] = await _sec(settlements(),
                                    empty_why="NO_PAPER_SETTLEMENT_YET")
    out["settlement_counts"] = await _sec(settlement_counts(),
                                          empty_why="NO_PAPER_SETTLEMENT_YET")
    out["pending_settlements"] = await _sec(
        pending(), empty_why="NO_OPEN_POSITION_AWAITING_SETTLEMENT",
        is_empty=lambda d: not (d or {}).get("count"))
    out["owned_positions"] = await _sec(owned(), empty_why=(
        "NO_PAPER_POSITION_OWNED: Xavier owns a group from its first "
        "simulated fill"))
    out["exposure"] = await _sec(exposure(), empty_why="NO_PAPER_ACCOUNT")
    out["account"] = await account_section(conn, account_id=acct, now=at)
    out["freshness"] = await freshness(conn, account_id=acct, now=at)
    out["last_updated_at"] = await _last_updated(conn, acct)
    return out


# ═════════════════════════════════════════════════════════════════════
# AUDREY: THE ACCOUNT, P&L BY STRATEGY, AUDITS, THE DAILY REPORT
# ═════════════════════════════════════════════════════════════════════

ACCOUNT_KEYS = (
    "cash_usd", "reserved_usd", "available_usd", "open_position_value_usd",
    "open_position_value_marked_only_usd", "total_equity_usd",
    "equity_excluding_unmarked_usd", "equity_basis", "realized_pnl_usd",
    "unrealized_pnl_usd", "unrealized_pnl_marked_only_usd", "fees_paid_usd",
    "marks_complete", "unmarked_positions", "stale_marks", "starting_cash_usd",
    "last_sequence", "last_updated_at", "ledger_entries", "ledger_consistent",
    "reserved_is", "mark_method", "real_money_submission")


def account_view(b: dict) -> dict:
    out = {k: b.get(k) for k in ACCOUNT_KEYS}
    out["open_positions"] = len(b.get("open_positions") or [])
    return out


def pnl_by_strategy(all_positions: list, open_views: list,
                    fees: dict) -> list:
    """REALIZED from every position (open and closed, sales and
    settlements); UNREALIZED from the marked open positions -- NOT STATED
    (None) for a strategy holding any unmarked position, with the marked-only
    figure beside it. Grouped by the strategy each group keeps for life."""
    by: dict[str, dict] = {}

    def row(s):
        return by.setdefault(s, {
            "strategy": s, "realized_pnl_usd": 0.0,
            "unrealized_pnl_usd": 0.0, "unrealized_marked_only_usd": 0.0,
            "unmarked_positions": 0, "open_positions": 0,
            "closed_positions": 0, "open_cost_basis_usd": 0.0,
            "fees_usd": fees.get(s, 0.0)})
    for p in all_positions:
        r = row(p.get("strategy") or TWO_MODEL)
        r["realized_pnl_usd"] += float(p.get("realized_pnl_usd") or 0.0)
        if float(p.get("open_qty") or 0.0) <= 1e-9:
            r["closed_positions"] += 1
    for p in open_views:
        r = row(p.get("strategy") or TWO_MODEL)
        r["open_positions"] += 1
        r["open_cost_basis_usd"] += float(p.get("cost_basis_usd") or 0.0)
        u = p.get("unrealized_pnl_usd")
        if u is None:
            r["unmarked_positions"] += 1
        else:
            r["unrealized_marked_only_usd"] += float(u)
    for s, f in fees.items():
        row(s)
    out = []
    for s in sorted(by, key=lambda k: (STRATEGY_ORDER.index(k)
                                       if k in STRATEGY_ORDER else 99, k)):
        r = by[s]
        r["unrealized_pnl_usd"] = (None if r["unmarked_positions"] else
                                   round(r["unrealized_marked_only_usd"], 6))
        for k in ("realized_pnl_usd", "unrealized_marked_only_usd",
                  "open_cost_basis_usd", "fees_usd"):
            r[k] = round(float(r[k]), 6)
        r["kind"] = STRATEGY_META.get(s, {}).get("kind", "OTHER")
        out.append(r)
    return out


REPORT_SUMMARY_KEYS = ("equity", "pnl", "open_exposure", "breadth",
                       "decisions", "data_gaps", "xavier_reviews")


async def audrey_operations(conn, *, account_id: str | None = None,
                            now: float | None = None,
                            limit: int = RECENT_ROWS) -> dict:
    acct = account_id or L.ACCOUNT_ID
    at = _now(now)
    out = _base(at)
    out["account_id"] = acct
    box: dict[str, Any] = {}

    async def bal():
        if "b" not in box:
            box["b"] = await L.balances(conn, acct, now=at)
        b = box["b"]
        if not b.get("ok"):
            raise RuntimeError(b.get("refusal") or "BALANCES_REFUSED")
        return b

    async def pnl():
        b = await bal()
        allp = await L.positions(conn, acct, include_closed=True)
        fees = {r["strategy"]: float(r["fees"]) for r in await conn.fetch(
            "SELECT strategy, coalesce(sum(fee_usd), 0) AS fees "
            "  FROM paper_fills WHERE account_id = $1 GROUP BY 1", acct)}
        return pnl_by_strategy(allp, b.get("open_positions") or [], fees)

    async def report():
        r = await conn.fetchrow(
            "SELECT report_id, report_day, version, generated_at, final, "
            "       reconciles, reporting_tz, digest, report "
            "  FROM paper_audrey_reports WHERE account_id = $1 "
            " ORDER BY report_day DESC, version DESC LIMIT 1", acct)
        if r is None:
            return None
        d = RM._row(r)
        rep = d.pop("report", None)
        rep = L._j(rep) if isinstance(rep, str) else rep
        rep = rep if isinstance(rep, dict) else {}
        d["summary"] = {k: rep.get(k) for k in REPORT_SUMMARY_KEYS
                        if k in rep}
        rc = rep.get("reconciliation") or {}
        d["reconciliation_checks"] = rc.get("checks")
        d["benchmark_sections"] = sorted(
            k for k in rep if k in ("pinnacle_only_paper_benchmark",
                                    "pinnacle_completed_game_paper"))
        return d

    async def reports():
        rows = await conn.fetch(
            "SELECT DISTINCT ON (report_day) report_id, report_day, version, "
            "       generated_at, final, reconciles, reporting_tz, digest "
            "  FROM paper_audrey_reports WHERE account_id = $1 "
            " ORDER BY report_day DESC, version DESC LIMIT $2", acct, 14)
        return [RM._row(r) for r in rows]

    async def findings():
        rows = await conn.fetch(
            "SELECT finding_id, found_at, kind, severity, subject, detail, "
            "       improvement_task_id FROM paper_audrey_findings "
            " WHERE account_id = $1 ORDER BY found_at DESC LIMIT $2",
            acct, int(limit))
        return [RM._row(r) for r in rows]

    async def finding_counts():
        rows = await conn.fetch(
            "SELECT severity, kind, count(*) AS n, max(found_at) AS latest_at"
            "  FROM paper_audrey_findings WHERE account_id = $1 "
            " GROUP BY 1, 2 ORDER BY 3 DESC", acct)
        return [RM._row(r) for r in rows]

    async def reconciliation():
        """THE LEDGER RECONCILED (agents.paper_brief.reconcile): every check
        names the two figures it compares; reconciled only when all pass."""
        from .agents import paper_brief as PB
        r = await PB.reconcile(conn, now=at, account_id=acct, entries=0)
        if not r.get("present"):
            raise RuntimeError(r.get("why") or "LEDGER_NOT_PRESENT")
        if r.get("checks") is None:
            raise RuntimeError(r.get("why") or "BALANCES_REFUSED")
        return {k: r.get(k) for k in ("reconciled", "failed_checks", "checks",
                                      "entries_count", "first_seq",
                                      "last_seq", "by_kind")}

    async def performance():
        """EACH STRATEGY'S RECORD, kept apart: decisions and entries, fills,
        positions open and settled by outcome, realized P&L (booked) and
        what is still open."""
        rows = {}

        def row(s_):
            return rows.setdefault(s_, {
                "strategy": s_, "kind": STRATEGY_META.get(s_, {}).get(
                    "kind", "OTHER"), "decisions": 0, "enter": 0,
                "entry_orders": 0, "filled_orders": 0, "settled": {},
                "won": 0, "lost": 0, "open_positions": 0,
                "closed_positions": 0, "realized_pnl_usd": 0.0})
        for r in await conn.fetch(
                "SELECT strategy, count(*) AS n, count(*) FILTER (WHERE "
                "       verdict = 'ENTER') AS e FROM paper_decisions "
                " WHERE account_id = $1 GROUP BY 1", acct):
            x = row(r["strategy"])
            x.update(decisions=int(r["n"]), enter=int(r["e"]))
        for r in await conn.fetch(
                "SELECT strategy, count(*) AS n, count(*) FILTER (WHERE "
                "       filled_qty > 0) AS f FROM paper_orders "
                " WHERE account_id = $1 AND role = 'ENTRY' GROUP BY 1", acct):
            x = row(r["strategy"])
            x.update(entry_orders=int(r["n"]), filled_orders=int(r["f"]))
        for p_ in await L.positions(conn, acct, include_closed=True):
            x = row(p_.get("strategy") or TWO_MODEL)
            x["realized_pnl_usd"] = round(x["realized_pnl_usd"] + float(
                p_.get("realized_pnl_usd") or 0.0), 6)
            if float(p_.get("open_qty") or 0) > 1e-9:
                x["open_positions"] += 1
            else:
                x["closed_positions"] += 1
            st = (p_.get("settlement") or {}).get("outcome")
            if st:
                x["settled"][st] = x["settled"].get(st, 0) + 1
                x["won"] += st == "WON"
                x["lost"] += st == "LOST"
        return [rows[k] for k in sorted(rows, key=lambda k: (
            STRATEGY_ORDER.index(k) if k in STRATEGY_ORDER else 99, k))]

    async def learning():
        """THE LEARNING RECORD (agents.paper_learning, the same read as GET
        /api/command/paper/learning): per agent what was learned, the
        proposed change, its evaluation (INSUFFICIENT_FORWARD_DATA stated as
        such) and whether it is active; Audrey's event-audit counts; the
        activation control."""
        from .agents import paper_learning as PLRN
        return await PLRN.learning_summary(conn, account_id=acct, now=at)

    async def events():
        from .agents import paper_learning as PLRN
        if not await PLRN.has_schema(conn):
            raise RuntimeError("MIGRATION_185_IS_NOT_APPLIED")
        return await PLRN.event_audits(conn, account_id=acct, limit=30)

    out["account"] = await account_section(conn, account_id=acct, now=at)
    out["reconciliation"] = await _sec(reconciliation(),
                                       empty_why="NO_LEDGER")
    out["performance_by_strategy"] = await _sec(
        performance(), empty_why="NO_PAPER_DECISION_OR_POSITION_YET")
    try:
        lr = await learning()
        out["learning"] = {"status": "OK", "why": None, "data": lr}
    except Exception as exc:                                    # noqa: BLE001
        out["learning"] = _unavailable("%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]))
    out["event_audits"] = await _sec(events(), empty_why=(
        "NO_EVENT_AUDITED_YET: Audrey audits each first fill, handoff, "
        "management fill and settlement once, on the paper pass after it"))
    out["pnl_by_strategy"] = await _sec(pnl(), empty_why=(
        "NO_PAPER_POSITION_YET: P&L by strategy starts with the first "
        "simulated fill"))
    out["daily_report"] = await _sec(report(),
                                     empty_why="NO_PAPER_DAILY_REPORT_YET")
    out["daily_reports"] = await _sec(reports(),
                                      empty_why="NO_PAPER_DAILY_REPORT_YET")
    out["findings"] = await _sec(findings(),
                                 empty_why="NO_PAPER_AUDIT_FINDING")
    out["finding_counts"] = await _sec(finding_counts(),
                                       empty_why="NO_PAPER_AUDIT_FINDING")

    async def ops_audit():
        # AUDREY'S OPERATIONAL AUDIT (migration 189): her recommendations to
        # Derek and Xavier, the automated (template) acknowledgement, any
        # genuine agent response and every measurement
        from . import bettor_paper_experiment as EXP
        ag = await EXP.agents(conn, at)
        recs = ag["audrey"].get("recommendations")
        if recs is None:
            raise RuntimeError("MIGRATION_189_NOT_APPLIED")
        return recs
    out["operational_audit"] = await _sec(ops_audit(), empty_why=(
        "NO_OPERATIONAL_RECOMMENDATION_YET: Audrey's operational audit runs "
        "every 10 minutes on the paper pass"))
    out["freshness"] = await freshness(conn, account_id=acct, now=at)
    out["last_updated_at"] = await _last_updated(conn, acct)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE HOMEPAGE: THE PAPER ACCOUNT AND EACH AGENT'S PAPER STATUS
# ═════════════════════════════════════════════════════════════════════

async def overview(conn, *, account_id: str | None = None,
                   now: float | None = None) -> dict:
    acct = account_id or L.ACCOUNT_ID
    at = _now(now)
    out = _base(at)
    out["account_id"] = acct
    since = L._ts(at - 86400.0)

    async def derek():
        rows = await conn.fetch(
            "SELECT strategy, count(*) AS n, "
            "       count(*) FILTER (WHERE verdict = 'ENTER') AS enter, "
            "       count(*) FILTER (WHERE decided_at > $2) AS last_24h, "
            "       count(*) FILTER (WHERE decided_at > $2 "
            "                          AND verdict = 'ENTER') AS enter_24h, "
            "       max(decided_at) AS latest_at FROM paper_decisions "
            " WHERE account_id = $1 GROUP BY 1", acct, since)
        if not rows:
            return None
        by = {r["strategy"]: {"decisions": int(r["n"]),
                              "enter": int(r["enter"]),
                              "last_24h": int(r["last_24h"]),
                              "enter_24h": int(r["enter_24h"]),
                              "latest_at": L._epoch(r["latest_at"]),
                              "kind": STRATEGY_META.get(
                                  r["strategy"], {}).get("kind", "OTHER")}
              for r in rows}
        return {"by_strategy": by,
                "last_activity_at": max((v["latest_at"] for v in by.values()
                                         if v["latest_at"]), default=None)}

    async def xavier():
        r = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM paper_handoffs WHERE account_id=$1)"
            "         AS handoffs, "
            "       (SELECT count(*) FROM paper_xavier_reviews "
            "         WHERE account_id=$1) AS reviews, "
            "       (SELECT count(*) FROM paper_xavier_reviews "
            "         WHERE account_id=$1 AND reviewed_at > $2) AS reviews_24h,"
            "       (SELECT max(reviewed_at) FROM paper_xavier_reviews "
            "         WHERE account_id=$1) AS last_review_at, "
            "       (SELECT count(*) FROM paper_orders WHERE account_id=$1 "
            "         AND role <> 'ENTRY' AND state = ANY($3::text[])) "
            "         AS open_management_orders, "
            "       (SELECT count(*) FROM paper_settlements "
            "         WHERE account_id=$1) AS settlements",
            acct, since, list(L.OPEN_STATES))
        pos = await L.positions(conn, acct)
        if not (r["handoffs"] or r["reviews"] or pos):
            return None
        return {"handoffs": int(r["handoffs"]), "reviews": int(r["reviews"]),
                "reviews_24h": int(r["reviews_24h"]),
                "last_activity_at": L._epoch(r["last_review_at"]),
                "open_positions": len(pos),
                "pending_settlement": sum(1 for p in pos
                                          if p.get("settlement") is None),
                "open_management_orders": int(r["open_management_orders"]),
                "settlements": int(r["settlements"])}

    async def audrey():
        r = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM paper_audrey_findings "
            "         WHERE account_id=$1) AS findings, "
            "       (SELECT count(*) FROM paper_audrey_findings "
            "         WHERE account_id=$1 AND found_at > $2) AS findings_24h, "
            "       (SELECT count(*) FROM paper_audrey_findings "
            "         WHERE account_id=$1 AND severity <> 'INFO') AS warnings,"
            "       (SELECT max(found_at) FROM paper_audrey_findings "
            "         WHERE account_id=$1) AS last_finding_at, "
            "       (SELECT max(generated_at) FROM paper_audrey_reports "
            "         WHERE account_id=$1) AS last_report_at, "
            "       (SELECT max(report_day) FROM paper_audrey_reports "
            "         WHERE account_id=$1) AS last_report_day", acct, since)
        if not (r["findings"] or r["last_report_at"]):
            return None
        la = [L._epoch(r["last_finding_at"]), L._epoch(r["last_report_at"])]
        return {"findings": int(r["findings"]),
                "findings_24h": int(r["findings_24h"]),
                "warnings_or_critical": int(r["warnings"]),
                "last_report_day": (None if r["last_report_day"] is None
                                    else r["last_report_day"].isoformat()),
                "last_activity_at": max((x for x in la if x), default=None)}

    try:
        out["session"] = {"status": "OK", "why": None,
                          "data": await _session_brief(conn,
                                                       account_id=acct)}
    except Exception as exc:                                    # noqa: BLE001
        out["session"] = _unavailable("%s: %s" % (type(exc).__name__,
                                                  str(exc)[:160]))
    out["account"] = await account_section(conn, account_id=acct, now=at)
    out["agents"] = {
        "derek": await _sec(derek(), empty_why="NO_PAPER_DECISION_YET"),
        "xavier": await _sec(xavier(), empty_why=(
            "NO_PAPER_POSITION_HANDED_TO_XAVIER_YET")),
        "audrey": await _sec(audrey(), empty_why=(
            "NO_PAPER_AUDIT_OR_REPORT_YET"))}
    out["freshness"] = await freshness(conn, account_id=acct, now=at)
    out["last_updated_at"] = await _last_updated(conn, acct)
    return out


OPERATIONS = {"derek": derek_operations, "xavier": xavier_operations,
              "audrey": audrey_operations}


def describe() -> dict:
    return {"version": VERSION, "strategies": list(STRATEGY_ORDER),
            "heartbeat_stale_after_s": HEARTBEAT_STALE_AFTER_S,
            "routes": ["/api/command/paper/operations?agent=derek|xavier|"
                       "audrey", "/api/command/paper/overview"]}
