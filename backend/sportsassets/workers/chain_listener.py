"""Worker: Path A on-chain OrderFilled listener."""

import asyncio

from ..ingestion.chain import ChainListener


async def main() -> None:
    await ChainListener().run()


if __name__ == "__main__":
    asyncio.run(main())
