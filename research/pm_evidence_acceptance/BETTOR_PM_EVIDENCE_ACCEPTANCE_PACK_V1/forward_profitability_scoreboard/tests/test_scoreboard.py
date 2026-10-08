from decimal import Decimal
from scoreboard.scoreboard import EventRow,RoutingRow,ArbRow,mechanism_summary,routing_summary,arb_summary,reconcile_total,classify_mechanism
import json
from pathlib import Path

TH=json.loads((Path(__file__).parents[1]/"thresholds.json").read_text())

def test_directional_rows_cluster_by_event():
    rows=[
      EventRow("E1","DIRECTIONAL",Decimal("1"),Decimal("2")),
      EventRow("E1","DIRECTIONAL",Decimal("1"),Decimal("-1")),
      EventRow("E2","DIRECTIONAL",Decimal("1"),Decimal("3")),
    ]
    s=mechanism_summary(rows)["DIRECTIONAL"]
    assert s["independent_events"]==2
    assert s["realized_pnl"]==Decimal("4")

def test_routing_savings_measure_next_best():
    rows=[RoutingRow("d1","E1","KALSHI_TB_NO",Decimal(".45"),Decimal(".47"),Decimal("10"),"NYY")]
    s=routing_summary(rows)
    assert s["total_savings_vs_next_best"]==Decimal(".20")

def test_stale_routing_row_not_counted_as_comparable_savings():
    rows=[RoutingRow("d1","E1","STALE",Decimal(".40"),Decimal(".47"),Decimal("10"),"NYY",False)]
    assert routing_summary(rows)["comparable_decisions"]==0

def test_same_venue_arb_separate_from_cross_venue():
    rows=[
      ArbRow("a","E1","SAME_VENUE",Decimal("1"),10,10,Decimal("1"),True,True,True,True,True),
      ArbRow("b","E2","CROSS_VENUE",Decimal("2"),10,10,Decimal("2"),True,True,True,True,True)
    ]
    s=arb_summary(rows)
    assert s["SAME_VENUE"]["opportunities"]==1
    assert s["CROSS_VENUE"]["opportunities"]==1

def test_partial_arb_not_execution_locked_by_fill_rate():
    rows=[ArbRow("a","E1","SAME_VENUE",Decimal("1"),8,10,Decimal("0"),True,True,True,True,False)]
    s=arb_summary(rows)["SAME_VENUE"]
    assert s["full_match_rate"]==0 and s["execution_locked_count"]==0

def test_pnl_reconciliation_exact_identity():
    r=reconcile_total(mechanism_realized={"directional":Decimal("10"),"arb":Decimal("5")},
                      settlement_adjustment=Decimal("-1"),outcome_variance=Decimal("2"),
                      reported_total=Decimal("16"))
    assert r["green"] and r["residual"]==0

def test_double_counted_pnl_does_not_reconcile():
    r=reconcile_total(mechanism_realized={"derek":Decimal("100"),"archer":Decimal("100")},
                      reported_total=Decimal("100"))
    assert not r["green"]

def test_directional_under_100_independent_events_stays_shadow():
    rows=[EventRow(f"E{i}","DIRECTIONAL",Decimal("0"),Decimal("1")) for i in range(50)]
    s=mechanism_summary(rows)["DIRECTIONAL"]
    assert classify_mechanism("DIRECTIONAL",s,TH)["status"]=="SHADOW_ONLY"

def test_directional_negative_lcb_stays_shadow():
    rows=[EventRow(f"E{i}","DIRECTIONAL",Decimal("0"),Decimal("-1" if i%2 else "1")) for i in range(120)]
    s=mechanism_summary(rows)["DIRECTIONAL"]
    assert classify_mechanism("DIRECTIONAL",s,TH)["status"]=="SHADOW_ONLY"

def test_incomplete_settlement_blocks_mechanism():
    rows=[EventRow(f"E{i}","DIRECTIONAL",Decimal("0"),Decimal("1"),settlement_proven=False) for i in range(120)]
    s=mechanism_summary(rows)["DIRECTIONAL"]
    assert "SETTLEMENT_INCOMPLETE" in classify_mechanism("DIRECTIONAL",s,TH)["blockers"]

def test_missing_fee_evidence_blocks_mechanism():
    rows=[EventRow(f"E{i}","DIRECTIONAL",Decimal("0"),Decimal("1"),fees_complete=False) for i in range(120)]
    s=mechanism_summary(rows)["DIRECTIONAL"]
    assert "FEES_INCOMPLETE" in classify_mechanism("DIRECTIONAL",s,TH)["blockers"]
