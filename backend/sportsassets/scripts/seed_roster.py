"""One-shot roster seed for launch.

Resolves the current top sports wallets from the LIVE leaderboard API — never
hardcoded addresses (spec §3 seed note: candidates like beachboy4/1j59y6nk
must be verified against the live board, not copied from articles). Prints
the selection for operator review before applying.
"""

import asyncio
import logging

from ..roster import refresh_roster, select_roster


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    candidates = await select_roster()
    if not candidates:
        print("Leaderboard returned no qualified candidates — check LEADERBOARD_API_BASE.")
        return
    print("Qualified top sports wallets (live leaderboard):")
    for c in candidates:
        print(f"  #{c['rank']:>3}  {c['address']}  {c['username'] or '?'}  ${c['profit']:,.0f}")
    result = await refresh_roster()
    print(f"Applied: {result}")


if __name__ == "__main__":
    asyncio.run(main())
