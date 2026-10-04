"""THE FORWARD SCORE of every registered model, and the predeclared verdict
of every challenger against the champion. Pure; no I/O.

ONLY FORWARD DATA. A forecast exists only for an opportunity at or after its
registration and was written before the outcome (migration 218's trigger);
this module additionally ignores any row whose opportunity predates the
registration, so a hand-inserted row could not leak in either.

THE VERDICT IS A FIXED-SAMPLE TEST, DECIDED ONCE. It is computed on the
FIRST `minimum_sample` resolved opportunities (by opportunity_at) that both
the challenger and the champion forecast -- never on "the sample so far",
so re-evaluating every cycle is not optional stopping: once the sample is
complete the verdict cannot change (forecasts and outcomes are append-only).
The z is Bonferroni-adjusted over the family size recorded at registration.

Scoring reuses intel/calibration.score (Brier, log loss, bootstrap / normal
CIs, Wilson frequency interval, reliability) -- not re-derived.
"""
from __future__ import annotations

from .. import bettor_source_calibration as SC
from ..intel import calibration as CAL
from . import common as C
from . import models as M

VERSION = "POSLEARN_SCORING_V1"


def _ll(p, o):
    return SC.log_loss([(p, o)])


def model_report(reg: dict, fcs: list, opps: dict, outs: dict) -> dict:
    """reg: {registration_id, subject_id, document, registered_at, status};
    fcs: this registration's forecast rows; opps / outs keyed by id."""
    doc = reg["document"]
    reg_at = float(reg["registered_at"])
    mine = [f for f in fcs if opps.get(f["opportunity_id"])
            and opps[f["opportunity_id"]]["opportunity_at"] >= reg_at]
    offered = len(mine)
    scored = [f for f in mine if f.get("probability") is not None]
    res = [(f, outs[f["opportunity_id"]]) for f in scored
           if outs.get(f["opportunity_id"], {}).get("outcome_class")
           == "RESOLVED"]
    pairs = [(float(f["probability"]), int(o["o"])) for f, o in res]
    sc = CAL.score(pairs)
    out = C.Out(registration_id=reg["registration_id"],
                subject_id=reg["subject_id"], role=doc["role"],
                status=reg["status"], model_kind=doc.get("model_kind"),
                registered_at=reg_at, offered=offered,
                forecast=len(scored), resolved=len(res),
                abstained=sum(1 for f in mine if f["action"] == "ABSTAIN"),
                sha256=reg.get("sha256"))
    out.put("coverage", C.rnd(len(scored) / offered) if offered else None,
            "NO_OPPORTUNITY_OFFERED_SINCE_REGISTRATION")
    for k in ("brier", "brier_ci_low", "brier_ci_high", "log_loss",
              "log_loss_ci_low", "log_loss_ci_high", "mean_probability",
              "observed_frequency", "frequency_ci_low", "frequency_ci_high"):
        out.put(k, sc.get(k), (sc.get("unmeasured") or {}).get(k))
    out["calibration"] = {"reliability": sc.get("reliability"),
                          "decomposition": sc.get("decomposition"),
                          "ci_method": sc.get("ci_method"),
                          "small_sample": sc.get("small_sample")}
    entries = [f for f in mine if f["action"] == "ENTER"]
    pred = [C.num(f.get("predicted_net_edge")) for f in entries]
    real = []
    for f in entries:
        o = outs.get(f["opportunity_id"])
        op = opps[f["opportunity_id"]]
        if o and o.get("outcome_class") == "RESOLVED" and op.get(
                "price") is not None and op.get("fee") is not None:
            real.append(int(o["o"]) - op["price"] - op["fee"])
    pm, plo, phi, pn = C.mean_ci(pred, 1.959964)
    rm, rlo, rhi, rn = C.mean_ci(real, 1.959964)
    out["entries"] = len(entries)
    out.put("net_predicted_edge_mean", C.rnd(pm), "NO_ENTRY")
    out.put("realized_edge_mean", C.rnd(rm), "NO_RESOLVED_ENTRY")
    out.put("realized_edge_ci", None if rlo is None else [C.rnd(rlo),
                                                         C.rnd(rhi)],
            "FEWER_THAN_TWO_RESOLVED_ENTRIES")
    out["realized_entries"] = rn
    out["price_basis_mix"] = _mix([opps[f["opportunity_id"]].get(
        "price_basis") or "NONE" for f in entries])
    return out


def _mix(xs):
    d: dict = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return d


def paired(chal: dict, champ_by_opp: dict, fcs: list, opps: dict,
           outs: dict) -> dict:
    """The predeclared fixed-sample test of one challenger."""
    doc = chal["document"]
    reg_at = float(chal["registered_at"])
    n_min = int(doc["minimum_sample"])
    fam = int(doc["family_size"])
    z = C.adjusted_z(M.ALPHA, fam)
    rows = []
    offered = 0
    for f in fcs:
        op = opps.get(f["opportunity_id"])
        if not op or op["opportunity_at"] < reg_at:
            continue
        offered += 1
        o = outs.get(f["opportunity_id"])
        ch = champ_by_opp.get(f["opportunity_id"])
        if (f.get("probability") is None or not ch
                or ch.get("probability") is None or not o
                or o.get("outcome_class") != "RESOLVED"):
            continue
        rows.append((op["opportunity_at"], f["opportunity_id"],
                     float(ch["probability"]), float(f["probability"]),
                     int(o["o"])))
    rows.sort()
    # coverage up to the end of the fixed sample (or so far, before it is
    # complete), so a decided verdict cannot move with later coverage
    cut = rows[n_min - 1][0] if len(rows) >= n_min else float("inf")
    window = [f for f in fcs if opps.get(f["opportunity_id"])
              and reg_at <= opps[f["opportunity_id"]]["opportunity_at"]
              <= cut]
    offered = len(window)
    scored = sum(1 for f in window if f.get("probability") is not None)
    out = C.Out(pairs_available=len(rows), min_sample=n_min,
                family_size=fam, z_adjusted=round(z, 6))
    cov = (scored / offered) if offered else None
    out.put("coverage", C.rnd(cov), "NO_OPPORTUNITY_OFFERED")
    if len(rows) < n_min:
        out["verdict"] = "INSUFFICIENT_SAMPLE"
        out.put("log_loss_improvement", None,
                "FIXED_SAMPLE_%d_NOT_REACHED_HAVE_%d" % (n_min, len(rows)))
        out.put("brier_improvement", None,
                "FIXED_SAMPLE_%d_NOT_REACHED_HAVE_%d" % (n_min, len(rows)))
        return out
    sample = rows[:n_min]
    ll = [_ll(pc, o) - _ll(px, o) for _, _, pc, px, o in sample]
    bs = [(pc - o) ** 2 - (px - o) ** 2 for _, _, pc, px, o in sample]
    m, lo, hi, _ = C.mean_ci(ll, z)
    mb = sum(bs) / len(bs)
    out.put("log_loss_improvement", C.rnd(m))
    out["log_loss_improvement_ci_adjusted"] = [C.rnd(lo), C.rnd(hi)]
    out.put("brier_improvement", C.rnd(mb))
    out["sample_ids_sha256"] = C.sha256_text(
        ",".join(r[1] for r in sample))
    out["sample_last_opportunity_at"] = sample[-1][0]
    if (lo is not None and lo > 0 and mb >= 0
            and cov is not None and cov >= M.MIN_COVERAGE):
        out["verdict"] = "CRITERIA_MET"
    elif hi is not None and hi < 0:
        out["verdict"] = "FAILED"
    else:
        out["verdict"] = "NOT_MET"
    return out
