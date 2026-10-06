"""THE SAME-BOOK RULE (pure, dependency-free): the window, the evidence a
symbol needs to be SUPPORTED, which rows count as exact-identity samples, and
the verdict from per-verdict counts. p5_runtime re-exports these names; the
workers' probe and the held-mark lane read them from here so neither imports
p5_runtime (which reaches the execution-intent layers)."""
from __future__ import annotations

#: Same-book window and the evidence it needs to be SUPPORTED.
SAME_BOOK_WINDOW_S = 86400.0
SAME_BOOK_MIN_COMPARABLE = 30
SAME_BOOK_MIN_AGREE_RATE = 0.95

#: S1 COUNTS ONLY COMPARABLE SAMPLES OF AN EXACT IDENTITY (see p5_runtime).
EXACT_SAMPLE_SQL = (
    "coalesce(identity_ok IS TRUE AND identity->>'institutional_symbol' = "
    "symbol AND retail_slug = symbol, false)")


def same_book_status(counts: dict) -> tuple:
    """Pure: (status, detail) from {verdict: (n, n_stable)} totals."""
    agree = counts.get("AGREE_TOP_N", (0, 0))[0]
    touch = counts.get("AGREE_TOUCH_ONLY", (0, 0))[0]
    dis, dis_stable = counts.get("DISAGREE", (0, 0))
    nc = counts.get("NOT_COMPARABLE", (0, 0))[0]
    comparable = agree + touch + dis
    rate = None if not comparable else (agree + touch) / comparable
    detail = {"comparable": comparable, "agree_top_n": agree,
              "agree_touch_only": touch, "disagree": dis,
              "disagree_with_stream_stable_in_window": dis_stable,
              "not_comparable": nc, "agree_rate": None if rate is None
              else round(rate, 4),
              "supported_needs": {"min_comparable": SAME_BOOK_MIN_COMPARABLE,
                                  "min_agree_rate": SAME_BOOK_MIN_AGREE_RATE,
                                  "stable_disagreements": 0}}
    if dis_stable > 0:
        return "CONTRADICTED", detail
    if comparable == 0:
        return "UNTESTED", detail
    if comparable >= SAME_BOOK_MIN_COMPARABLE and \
            rate >= SAME_BOOK_MIN_AGREE_RATE:
        return "SUPPORTED", detail
    return "INCONCLUSIVE", detail
