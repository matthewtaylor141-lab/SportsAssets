"""THE POLICY-IMPROVEMENT LOOP's read surface for COMMAND.

WHAT MANAGEMENT NEEDS TO SEE, and in this order:

    the ACTIVE policy      frozen, and which version is running live
    the CANDIDATES         separately trained, never live
    EXPERIMENTS COMPLETED  how many variants were tried, not how many
                           worked
    the LOSS ATTRIBUTION   what the cycle was aiming at
    TRAIN / VALIDATION     side by side, both partitions, every scenario
    the GATE               its criteria, and the reason for each verdict
    PROSPECTIVE            the live lane's own accruing evidence

WHY `accepted: []` IS A FIRST-CLASS RESULT. Two cycles have now run and
neither promoted anything. A panel that only rendered winners would
show an empty page and read as "nothing happened". The rejection
REASONS are the product here, so they are what this module returns.

NOTHING HERE WRITES, and nothing here can promote a policy. Promotion
is a commit, reviewed, not an API call -- so there is no route through
which a displayed result can change what the desk is running.
"""
from __future__ import annotations

import json
import os

NOT_IDENTIFIED = "NOT_IDENTIFIED"

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                     "..", "..", ".."))
_DIR = os.path.join(_REPO, "research", "beta48", "learning")

_cache = {}


class LearningUnavailable(Exception):
    """The artifact could not be read. NOT "no experiments have run".

    The same distinction the desk already enforces: a failed read is
    503, never a well-formed page of zeros. "The loop has run no
    experiments" and "I could not find out" are different facts and
    must not render identically.
    """


def _cycles() -> list:
    try:
        names = sorted(d for d in os.listdir(_DIR)
                       if d.startswith("experiments_"))
    except OSError as exc:
        raise LearningUnavailable("EXPERIMENT_DIR_UNREADABLE: %s"
                                  % exc) from exc
    out = []
    for d in names:
        p = os.path.join(_DIR, d, "report.json")
        if os.path.exists(p):
            out.append((d, p))
    if not out:
        raise LearningUnavailable(
            "NO_EXPERIMENT_REPORT_IN_THIS_RELEASE: produced by "
            "backend/tools/desk_experiments.py and committed; this "
            "build carries none")
    return out


def _load(path: str) -> dict:
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if _cache.get(path) != key:
        try:
            with open(path) as fh:
                _cache[path + ":data"] = json.load(fh)
        except (OSError, ValueError) as exc:
            raise LearningUnavailable("EXPERIMENT_REPORT_UNREADABLE: %s"
                                      % exc) from exc
        _cache[path] = key
    return _cache[path + ":data"]


def _verdict_rows(rep: dict) -> list:
    """One row per candidate: both partitions, and WHY."""
    rows = []
    for c in rep["candidates"]:
        cid = c["id"]
        if cid == "BASELINE":
            continue
        tr = rep["verdicts"].get("TRAIN", {}).get(cid, {})
        va = rep["verdicts"].get("VALIDATION", {}).get(cid, {})
        rows.append({
            "id": cid,
            "hypothesis": c["hypothesis"],
            "targets_loss_line": c["targets"],
            "params": c["params"],
            "train_accepted": bool(tr.get("accepted")),
            "validation_accepted": bool(va.get("accepted")),
            "train_reasons": tr.get("reasons", []),
            "validation_reasons": va.get("reasons", []),
            "sign_stability": rep.get("sign_stability", {}).get(cid),
            "promoted": cid in rep.get("accepted", []),
        })
    return rows


def _scenario_table(rep: dict) -> dict:
    """Every policy, both partitions, every declared scenario.

    THE THREE MARKS ARE SHOWN TOGETHER, never one alone. Because
    cash + inventory_cost - realized == starting_cash, the cost-marked
    terminal IS `realized`; the zero- and one-marked terminals are the
    bounds an unresolved binary leg sits between. Showing only the
    lower one turns open inventory into a reported loss.
    """
    qs = rep["scenarios_queue_share"]
    out = {}
    for part in ("TRAIN", "VALIDATION"):
        out[part] = []
        for cid, rows in rep["results"].get(part, {}).items():
            for q, x in zip(qs, rows):
                out[part].append({
                    "policy": cid,
                    "queue_share": q,
                    "mark_cost_usd": x["realized_pnl_usd"],
                    "mark_zero_usd": x["terminal_lower_vs_start"],
                    "mark_one_usd": round(x["terminal_upper_usd"]
                                          - x["terminal_lower_usd"]
                                          + x["terminal_lower_vs_start"], 2),
                    "unresolved_cost_usd": x["unresolved_cost_usd"],
                    "max_drawdown_usd": x["max_drawdown_usd"],
                    "peak_committed_usd": x["peak_committed_usd"],
                    "turnover_usd": x["turnover_usd"],
                    "orders": x["orders"],
                    "fills": x["fills"],
                    "decisions": x["decisions"],
                    "invariant_ok": x["invariant_ok"],
                })
    return out


def overview() -> dict:
    cycles = _cycles()
    name, path = cycles[-1]
    rep = _load(path)
    return {
        "artifact": name,
        "cycle": rep.get("cycle", 1),
        "cycles_run": len(cycles),
        "generated_at": rep.get("generated_at"),
        "code_version": rep.get("code_version"),
        "desk_version": rep.get("desk_version"),

        "active_policy": {
            "status": "FROZEN",
            "version": rep.get("desk_version"),
            "changed_by_this_loop": False,
            "note": ("the live shadow desk runs this policy and nothing "
                     "in the improvement loop can change it. Promotion "
                     "is a reviewed commit, not an API call."),
        },
        "candidates_are_not_live": (
            "every candidate below was trained and evaluated separately. "
            "None has ever placed a shadow order in the live lane."),

        "variants_tried": rep.get("variants_tried"),
        "partitions": rep.get("partitions"),
        "partition_rule": rep.get("partition_rule"),
        "final_evaluation_set": rep.get("FINAL_EVALUATION_SET"),
        "execution_assumptions": rep.get("execution_assumptions"),
        "scenarios_queue_share": rep.get("scenarios_queue_share"),

        "gate": rep.get("gate"),
        "accepted": rep.get("accepted", []),
        "outcome": rep.get("outcome"),
        "promotion_occurred": bool(rep.get("accepted")),

        "candidates": _verdict_rows(rep),
        "sign_stability": rep.get("sign_stability"),

        "why_no_promotion_is_a_result": (
            "trading nothing is not proof of a strategy, and neither is "
            "an empty accept list proof of failure. The rejection "
            "reasons are the product: each names which declared "
            "criterion the candidate missed, and by how much."),
    }


def cycles() -> dict:
    """Every cycle, oldest first, so the loop's history is inspectable."""
    out = []
    for name, path in _cycles():
        rep = _load(path)
        out.append({
            "artifact": name,
            "cycle": rep.get("cycle", 1),
            "gate": rep.get("gate", {}).get("id", "V1"),
            "variants_tried": rep.get("variants_tried"),
            "accepted": rep.get("accepted", []),
            "outcome": rep.get("outcome"),
            "code_version": rep.get("code_version"),
        })
    return {"n": len(out), "cycles": out}


def results() -> dict:
    name, path = _cycles()[-1]
    rep = _load(path)
    return {
        "artifact": name,
        "cycle": rep.get("cycle", 1),
        "marks_explained": {
            "mark_cost_usd": ("unresolved legs marked at what we paid. "
                              "Equals realized P&L by the ledger "
                              "identity. THIS IS THE POINT ESTIMATE."),
            "mark_zero_usd": "every unresolved leg pays 0. LOWER BOUND.",
            "mark_one_usd": "every unresolved leg pays 1. UPPER BOUND.",
            "why_all_three": (
                "a binary leg with no settlement record in our data "
                "pays somewhere in [0,1]. Quoting the lower bound alone "
                "reports open inventory as a realized loss; quoting the "
                "upper alone reports it as a gain. Neither is a result."),
        },
        "scenarios": _scenario_table(rep),
    }
