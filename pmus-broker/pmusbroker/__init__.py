"""PMUS signing broker -- a fixed-function process that holds the venue secret.

It exposes ONE operation: mint authentication for `GET /v1/ws/markets`. It has
no order client, no database access, no outbound HTTP capability, and no
research logic. Read capability.py first; it is the entire authority surface.

The secret this process holds is TRADING_CAPABLE_AT_CLIENT_LAYER. Arbitrary code
execution here is still a full compromise of that credential. What the design
buys is that the collector -- the process that actually talks to the venue, parses
untrusted frames all day, and carries the research code -- never holds it.
"""
from __future__ import annotations

__all__ = ["capability", "ledger", "mint", "server"]
