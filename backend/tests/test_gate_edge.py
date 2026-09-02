"""What each refusal gate costs or saves (analytics/gate_edge.py)."""
import asyncio

from sportsassets.analytics import gate_edge as ge


def _t(gate, payout, key, size=100, price=0.5, error=None):
    return {"gate": gate, "error": error, "size": size, "price": price,
            "payout": payout, "event_key": key}


def test_error_text_maps_to_a_gate_first_prefix_wins():
    assert ge.gate_of("unmapped: exact[...] no market") == "unmapped"
    assert ge.gate_of("no verified Polymarket US market for this outcome") == "unmapped"
    assert ge.gate_of("one position per game") == "one_per_game"
    assert ge.gate_of("never-add: this market was already copied") == "never_add"
    assert ge.gate_of("short-branch-refused: every BUY_SHORT fill ...") == "short_branch"
    assert ge.gate_of("stale-signal: reaction 950s > 900s cap") == "stale_signal"
    assert ge.gate_of("hold: pending paper certification") == "hold_pending_certification"
    assert ge.gate_of("something new") == "other"
    assert ge.gate_of(None) == "other"


def test_each_gate_is_scored_against_the_taken_trades_on_the_same_basis():
    refused = [_t("one_per_game", 1.0 if i % 5 else 0.0, f"g{i}") for i in range(40)]   # earns
    refused += [_t("never_add", 0.0 if i % 5 else 1.0, f"h{i}") for i in range(40)]     # loses
    refused += [_t("unmapped", 1.0 if i % 2 else 0.0, f"u{i}") for i in range(40)]      # flat
    refused += [_t("short_branch", 1.0, f"s{i}") for i in range(5)]                     # too few
    taken = [_t(None, 1.0 if i % 2 else 0.0, f"t{i}") for i in range(40)]
    out = ge.score(refused, taken)
    g = out["gates"]
    assert list(g)[:3] == ["one_per_game", "never_add", "unmapped"]     # by refusal count
    assert g["one_per_game"]["lever"].startswith("REFUSED TRADES EARN")
    assert g["never_add"]["lever"].startswith("REFUSED TRADES LOSE")
    assert g["unmapped"]["lever"].startswith("FLAT")
    assert g["short_branch"]["lever"].startswith("NOT DEMONSTRATED")
    assert g["one_per_game"]["refused"] == 40 and g["one_per_game"]["share_of_refusals"] == round(40 / 125, 4)
    assert out["taken_at_his_price"]["taken"] == 40
    assert "levers (refused trades earn at 95%): one_per_game" in out["reading"]
    assert "keep (refused trades lose at 95%): never_add" in out["reading"]
    assert out["n_refused_scored"] == 125


def test_the_gate_label_falls_back_to_the_error_text():
    out = ge.score([_t(None, 1.0, "g1", error="one position per game")], [])
    assert "one_per_game" in out["gates"]


def test_the_cohort_reads_refused_and_taken_buys_and_splits_them():
    class _Pool:
        def __init__(self):
            self.calls = []

        async def fetch(self, sql, *a):
            self.calls.append((sql, a))
            return [{"status": "rejected", "error": "one position per game", "whale": "rn1",
                     "lane": "chain", "size": 10.0, "price": 0.5, "outcome_index": 0,
                     "resolved_prices": "[1, 0]", "event_key": "g1"},
                    {"status": "settled", "error": None, "whale": "rn1", "lane": "chain",
                     "size": 10.0, "price": 0.5, "outcome_index": 1,
                     "resolved_prices": "[1, 0]", "event_key": "g2"},
                    {"status": "rejected", "error": "unmapped: x", "whale": "rn1",
                     "lane": "chain", "size": 10.0, "price": 0.5, "outcome_index": 7,
                     "resolved_prices": "[1, 0]", "event_key": "g3"}]

    pool = _Pool()
    out = asyncio.run(ge.cohort_gate_edge(pool, 30, "RN1"))
    sql, args = pool.calls[0]
    assert "lo.status IN ('rejected', 'filled', 'settled', 'cashed_out', 'exiting')" in sql
    assert "COALESCE(m.resolved, false) = true" in sql and args == (30, "rn1")
    assert out["n_refused_scored"] == 1 and out["taken_at_his_price"]["taken"] == 1
    assert out["unresolvable_payout"] == 1 and out["whale"] == "rn1"
