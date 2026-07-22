"""Worker: notification dispatcher + ops health monitor."""

import asyncio

from ..notifications.dispatcher import Dispatcher


async def main() -> None:
    await Dispatcher().run()


if __name__ == "__main__":
    asyncio.run(main())
