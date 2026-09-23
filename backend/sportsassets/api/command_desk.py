"""THE SHADOW DESK's read surface for COMMAND. Read-only, mode-separated.

THREE MODES, NEVER SUMMED.

    LIVE_SHADOW        the running engine against incoming data
    HISTORICAL_REPLAY  a declared past window through the SAME engine
    CAPITAL_SCENARIOS  the same replay under different declared
                       execution and sizing assumptions

`combined_pnl` does not exist in this module and must never be added.
A live total and a replay total are produced under different execution
assumptions -- one against evidence we received as it happened, the
other against a tape that was never ours -- and adding them would
produce a number that describes nothing.

WHY THE REPLAY IS SERVED FROM A FILE. It is an ARTIFACT of a release:
produced once, committed, and identified by the release SHA that
carries it. Recomputing it per request would let the number management
is reading drift from the number that was reviewed. The file's own
`generated_at` and window are returned with every response so nobody
has to assume it is current.

NOTHING HERE WRITES. There is no venue client in this module's import
graph, no INSERT, no UPDATE, and no order-placing path. It reads a
file and shapes it.
"""
from __future__ import annotations

import json
import os

MODE_LIVE = "LIVE_SHADOW"
MODE_REPLAY = "HISTORICAL_REPLAY"
MODE_SCENARIO = "CAPITAL_SCENARIOS"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                     "..", "..", ".."))
_REPLAY_DIR = os.path.join(_REPO, "research", "beta48", "learning")

_cache = {}


class ReplayUnavailable(Exception):
    """The artifact could not be read. NOT an empty replay.

    A missing file is an unavailable read and the route turns it into
    503. Returning a well-formed page of zeros would be a dashboard
    confidently reporting that the policy did nothing, which is the
    opposite of what a missing artifact means.
    """


def _newest_bundle() -> str:
    try:
        dirs = sorted(d for d in os.listdir(_REPLAY_DIR)
                      if d.startswith("replay_"))
    except OSError as exc:
        raise ReplayUnavailable("REPLAY_DIR_UNREADABLE: %s" % exc) from exc
    for d in reversed(dirs):
        p = os.path.join(_REPLAY_DIR, d, "bundle.json")
        if os.path.exists(p):
            return p
    raise ReplayUnavailable(
        "NO_REPLAY_BUNDLE_IN_THIS_RELEASE: the artifact is produced by "
        "backend/tools/desk_replay.py and committed; this build carries "
        "none")


def bundle() -> dict:
    p = _newest_bundle()
    st = os.stat(p)
    key = (p, st.st_mtime, st.st_size)
    if _cache.get("key") != key:
        try:
            with open(p) as fh:
                _cache["data"] = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ReplayUnavailable("REPLAY_BUNDLE_UNREADABLE: %s"
                                    % exc) from exc
        _cache["key"] = key
        _cache["path"] = p
    return _cache["data"]


def _provenance(b) -> dict:
    r = b["report"]
    return {
        "mode": MODE_REPLAY,
        "artifact": os.path.basename(os.path.dirname(_cache.get("path", ""))),
        "generated_at": r.get("generated_at"),
        "window": r.get("window"),
        "desk_version": r.get("desk_version"),
        "policy_version": r.get("policy", {}).get("policy_version"),
        "policy_maturity": r.get("policy", {}).get("maturity"),
        "learned_artifact_sha": r.get("policy", {}).get(
            "learned_artifact_sha", NOT_IDENTIFIED),
        "execution_assumptions": r.get("execution_assumptions"),
        "NOT_LIVE": ("HISTORICAL_REPLAY. Never combine this with a live "
                     "shadow total; the two are produced under different "
                     "execution assumptions."),
    }


def overview() -> dict:
    """What management sees first: the mode, the money, the caveat."""
    b = bundle()
    r = b["report"]
    return {
        "provenance": _provenance(b),
        "net": r["net"],
        "invariant": r["invariant"],
        "orders": r["orders"],
        "order_outcomes": r["order_outcomes"],
        "decision_census": r["decision_census"],
        "settlement": {k: v for k, v in r["settlement"].items()
                       if k != "unresolved"},
        "consumption": r["consumption"],
        "limits": r["limits"],
        "portfolio": {k: v for k, v in r["portfolio"].items()
                      if k != "legs"},
        "residual_exposure_usd": r["portfolio"]["inventory_cost_usd"],
        "p_fill": NOT_IDENTIFIED,
        "what_this_is_not": (
            "a live result, a funded result, or evidence that the policy "
            "is profitable. It lost money over this window and the loss "
            "is reported with its attribution rather than smoothed."),
    }


def attribution() -> dict:
    """P&L BY CAUSE. A loss that is not attributed cannot be fixed."""
    b = bundle()
    r = b["report"]
    legs = r["portfolio"]["legs"]
    settled = [v for v in legs.values() if v["settled"]]
    settle_pnl = sum(v["realized"] for v in settled)
    fees = r["portfolio"]["fees_usd"]
    realized = r["net"]["realized_pnl_usd"]
    return {
        "provenance": _provenance(b),
        "realized_pnl_usd": realized,
        "by_cause": [
            {"cause": "SETTLEMENT", "usd": round(settle_pnl, 2),
             "detail": "%d legs settled; %d won, %d lost" % (
                 len(settled),
                 sum(1 for v in settled if (v["payout"] or 0) >= 0.5),
                 sum(1 for v in settled if (v["payout"] or 0) < 0.5))},
            {"cause": "EXIT_VS_BASIS",
             "usd": round(realized - settle_pnl + fees, 2),
             "detail": "sells executed against the average cost of the leg"},
            {"cause": "FEES", "usd": round(-fees, 2),
             "detail": "negative total fees are maker REBATE income; on "
                       "this window fees HELPED and are not the cause"},
        ],
        "stranded_capital": {
            "usd": r["portfolio"]["inventory_cost_usd"],
            "legs": r["settlement"]["legs_unresolved"],
            "detail": "positions with no settlement in our records. Not "
                      "assumed to have paid zero, and not dropped.",
        },
        "diagnosis": (
            "the entry band [0.40, 0.65] sits where the fair-value work "
            "measured NO edge -- that band's mean(payout - price) "
            "interval straddles zero -- while the measured edge lives in "
            "the tails this policy does not trade. Entries with no edge, "
            "then spread paid on the way out. The band is NOT being "
            "retuned against this window."),
    }


def lifecycles(limit=20) -> dict:
    """Inspectable position lifecycles, losers included by construction."""
    b = bundle()
    s = b["lifecycle_sample"]
    return {
        "provenance": _provenance(b),
        "selection_rule": s["selection_rule"],
        "n": s["n"],
        "conditions": [{
            "condition_id": c["condition_id"],
            "realized_usd": c["realized_usd"],
            "legs": c["legs"],
            "orders": len(c["orders"]),
            "fills": sum(len(o["fills"]) for o in c["orders"]),
            "decisions": len(c["decisions"]),
        } for c in s["conditions"][:int(limit)]],
    }


def lifecycle(condition_id: str) -> dict:
    """One condition, end to end: every decision, order, fill and leg."""
    b = bundle()
    for c in b["lifecycle_sample"]["conditions"]:
        if c["condition_id"] == condition_id:
            return {"provenance": _provenance(b), **c}
    raise ReplayUnavailable(
        "CONDITION_NOT_IN_LIFECYCLE_SAMPLE: %s. The sample is the six "
        "worst and six best settled conditions plus the four most "
        "active; it is not the whole book." % condition_id[:20])


def scenarios() -> dict:
    """MODE C. Declared, and honest about what cannot be estimated."""
    b = bundle()
    r = b["report"]
    qs = r["execution_assumptions"]["queue_share"]
    return {
        "provenance": _provenance(b),
        "base": {"queue_share": qs,
                 "order_usd": r["policy"]["order_usd"],
                 "starting_cash": r["limits"]["starting_cash"],
                 "realized_pnl_usd": r["net"]["realized_pnl_usd"]},
        "available": [
            {"axis": "queue_share",
             "meaning": "the share of a print our resting order could "
                        "have taken. We were never in the queue.",
             "status": "SWEEPABLE_BY_RERUNNING_THE_REPLAY"},
            {"axis": "order_usd",
             "meaning": "size per order",
             "status": "SWEEPABLE_BY_RERUNNING_THE_REPLAY"},
        ],
        "REFUSED": {
            "institutional_scale_projection": (
                "NOT_IDENTIFIED. An institutional figure cannot be "
                "obtained by multiplying a small-order result: our own "
                "orders would move the book, and market impact at size "
                "is not estimable from a tape in which we never traded. "
                "No multiple is offered."),
            "capacity": (
                "NOT_IDENTIFIED. Capacity needs measured fill rates at "
                "our own size, which requires a funded pilot."),
        },
    }
