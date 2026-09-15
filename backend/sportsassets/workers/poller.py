"""Worker: Path B Data-API poller."""

import asyncio

from ..ingestion.poller import Poller
from ..notifications import telegram


async def main() -> None:
    poller = Poller()
    poller.on_alert = telegram.alert_admins
    await poller.run()


if __name__ == "__main__":
    asyncio.run(main())
