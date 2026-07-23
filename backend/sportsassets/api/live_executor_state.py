"""Kill-switch state for the LIVE beta (shared via ingestion_state)."""

import json

from ..db import get_pool
from ..live_executor import PAUSE_KEY


async def set_paused(paused: bool) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        PAUSE_KEY, json.dumps(paused),
    )
