from __future__ import annotations
from decimal import Decimal

def karen_incremental_value(saved_loss_usd, false_block_opportunity_cost_usd):
    """Karen alpha = prevented loss - opportunity cost of false blocks."""
    saved=Decimal(str(saved_loss_usd))
    false_cost=abs(Decimal(str(false_block_opportunity_cost_usd)))
    return saved-false_cost
