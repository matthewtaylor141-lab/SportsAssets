"""Which whales get our money, decided by the data and not by anyone.

Owner order 2026-09-01 (evening): "Stop asking me to make decisions that
impact profitability. Use the real data from the real transactions on
the blockchain to make decisions on whales that are in our roster."

THE TWO NUMBERS THAT DECIDE. Both already exist and both come from the
chain.

  funded    edge_gate.snapshot()[w]["funded"] -- the analytics worker's
            merge-inclusive, whole-book interval on HIS fills, proven
            above zero at 95%. His edge, on his book, from his on-chain
            trades. It says he is worth measuring. It cannot say we can
            capture him: rn1 is funded at [+4.4%, +6.9%] and our copies
            of him return ~0%.

  realized  proof.cohort_assess()["by_whale"][w] -- the cluster-robust
            interval on OUR settled copies of him. What we actually
            capture. This is the only number that can promote a whale to
            full size or take him off the roster.

THE STATES. A whale is ABSENT (not ours), MEASURING (funded on his book,
trading at a small clip so `realized` can exist), PROMOTED (realized
lower bound above zero: full clip), or DEMOTED (realized upper bound
below zero: no new entries; exits still mirror through
exitable_whales()).

THE ARITHMETIC THAT SETS THE THRESHOLDS. Proof needs roughly
n = (1.645*sigma/mu)^2 settled copies. A lower bound above zero on
three copies is possible with a lucky low-variance streak and means
nothing, so promotion needs MIN_N_PROMOTE settled and MIN_CLUSTERS
independent games. Demotion is asymmetric on purpose -- a whale losing
at 95% on twenty copies is already costing money -- but still needs
MIN_N_DEMOTE so one bad afternoon cannot fire it.

DEMOTION STICKS. A demoted whale stops accumulating settled copies, so
his realized interval cannot move; re-entry is an owner decision made
with that knowledge, never an automatic one. The rules never re-promote
a whale they demoted.

Pure functions. No I/O. Every decision carries the numbers that made it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MEASURE_CLIP_USD = 50.0
PROMOTED_CLIP_USD = 250.0
MIN_N_PROMOTE = 30
MIN_CLUSTERS_PROMOTE = 20
MIN_N_DEMOTE = 20
# A bound on how many whales can be in measurement at once: the budget
# is MAX_MEASURING x clip x fills/day, and measurement is meant to be
# cheap. Funded-but-unrostered whales beyond the cap wait, best edge
# lower bound first.
MAX_MEASURING = 8

# 'cut' is the owner's demotion (set-roster removed him): sticky like
# 'demoted', so the rules never re-admit a whale the owner took off.
STATES = ("absent", "measuring", "promoted", "demoted", "cut")
STICKY = ("demoted", "cut")


@dataclass
class Decision:
    whale: str
    from_state: str
    to_state: str
    clip_usd: float | None
    reason: str
    n: int = 0
    roi: float | None = None
    ci_lo: float | None = None
    ci_hi: float | None = None
    clusters: int | None = None
    funded: bool = False
    changed: bool = field(init=False)

    def __post_init__(self) -> None:
        self.changed = self.from_state != self.to_state

    def as_row(self) -> dict[str, Any]:
        return {"whale": self.whale, "from_state": self.from_state,
                "to_state": self.to_state, "clip_usd": self.clip_usd,
                "reason": self.reason, "n": self.n, "roi": self.roi,
                "ci_lo": self.ci_lo, "ci_hi": self.ci_hi,
                "clusters": self.clusters, "funded": self.funded,
                "changed": self.changed}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2%}"


def decide(whale: str, funded: bool, realized: dict | None,
           current_state: str) -> Decision:
    """One whale, one decision, from the two numbers and his current state."""
    r = realized or {}
    n = int(r.get("n") or 0)
    roi = r.get("roi")
    ci = r.get("ci95")
    lo, hi = (float(ci[0]), float(ci[1])) if isinstance(ci, (list, tuple)) and len(ci) == 2 else (None, None)
    cl = r.get("clusters")
    base = dict(whale=whale, n=n, roi=roi, ci_lo=lo, ci_hi=hi,
                clusters=cl, funded=funded)

    # DEMOTION STICKS, AND SO DOES THE OWNER'S CUT. Nothing below can
    # undo either; re-entry is the owner's set-roster, which resets the
    # state to measuring (workers/roster_auto.owner_set).
    if current_state in STICKY:
        return Decision(from_state=current_state, to_state=current_state,
                        clip_usd=0.0,
                        reason=("demoted; re-entry is an owner decision"
                                if current_state == "demoted" else
                                "cut by the owner; re-entry is an owner decision"),
                        **base)

    # LOSING AT 95% on enough copies: out. Exits keep mirroring.
    if hi is not None and hi < 0 and n >= MIN_N_DEMOTE:
        return Decision(from_state=current_state, to_state="demoted", clip_usd=0.0,
                        reason=(f"realized upper bound {_pct(hi)} < 0 on n={n}"
                                f" ({cl} games): losing at 95%"), **base)

    # EARNING AT 95% on enough independent games: full clip.
    if (lo is not None and lo > 0 and n >= MIN_N_PROMOTE
            and (cl or 0) >= MIN_CLUSTERS_PROMOTE):
        return Decision(from_state=current_state, to_state="promoted",
                        clip_usd=PROMOTED_CLIP_USD,
                        reason=(f"realized lower bound {_pct(lo)} > 0 on n={n}"
                                f" ({cl} games): earning at 95%"), **base)

    # Already promoted and no longer proven: hold the clip, do not churn.
    # A promoted whale whose interval widens back over zero is not a
    # loser; he is a whale whose sample got noisier. Demotion above is
    # the only way down.
    if current_state == "promoted":
        return Decision(from_state="promoted", to_state="promoted",
                        clip_usd=PROMOTED_CLIP_USD,
                        reason=(f"holding: realized {_pct(roi)} "
                                f"[{_pct(lo)}, {_pct(hi)}] on n={n}"), **base)

    # Funded on his own book: measure, or keep measuring.
    if funded:
        why = ("his book is funded at 95%; our capture is "
               + ("unmeasured" if n == 0 else
                  f"undemonstrated: {_pct(roi)} [{_pct(lo)}, {_pct(hi)}] on n={n}"))
        return Decision(from_state=current_state, to_state="measuring",
                        clip_usd=MEASURE_CLIP_USD, reason=why, **base)

    # Not funded. If we were measuring him, hold rather than flap on a
    # gate that flickers; if we never had him, leave him absent.
    if current_state == "measuring":
        return Decision(from_state="measuring", to_state="measuring",
                        clip_usd=MEASURE_CLIP_USD,
                        reason="gate no longer funds his book; holding "
                               "measurement pending a realized verdict", **base)
    return Decision(from_state="absent", to_state="absent", clip_usd=None,
                    reason="not funded on his own book", **base)


def plan(funded_map: dict[str, dict], realized_map: dict[str, dict],
         current: dict[str, str], edge_lower: dict[str, float] | None = None
         ) -> list[Decision]:
    """Decide every whale, then enforce the measurement cap.

    `funded_map` is edge_gate.snapshot()["whales"]; `realized_map` is
    proof by_whale; `current` maps whale -> state for whales we hold;
    `edge_lower` orders funded newcomers when the cap binds.
    """
    names = sorted(set(funded_map) | set(realized_map) | set(current))
    out: list[Decision] = []
    for w in names:
        d = decide(w, bool((funded_map.get(w) or {}).get("funded")),
                   realized_map.get(w), current.get(w, "absent"))
        out.append(d)
    # THE CAP. Whales already measuring keep their seat; newcomers fill
    # the rest, best own-book lower bound first. The rest wait as
    # 'absent' with a reason that says so.
    measuring_now = [d for d in out if d.to_state == "measuring"]
    seats = [d for d in measuring_now if d.from_state == "measuring"]
    newcomers = [d for d in measuring_now if d.from_state != "measuring"]
    room = max(0, MAX_MEASURING - len(seats))
    el = edge_lower or {}
    newcomers.sort(key=lambda d: -(el.get(d.whale) or float("-inf")))
    for d in newcomers[room:]:
        d.to_state = "absent"
        d.clip_usd = None
        d.reason = (f"funded, waiting: measurement cap {MAX_MEASURING} "
                    f"is full")
        d.changed = d.from_state != d.to_state
    return out
