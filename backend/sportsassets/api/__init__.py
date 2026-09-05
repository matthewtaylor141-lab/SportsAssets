"""The API's log handler (2026-09-05).

The API had none. uvicorn configures its own loggers only, so every
INFO record from sportsassets.* fell through to Python's last-resort
handler, which prints WARNING and above. The venue board sweeps landed
that day with INFO lines saying what they cost -- pages, markets,
seconds, RSS before and after -- and the API log read from Render
showed only uvicorn's access lines: the instrument was blind in the one
process it was built for. This is the same shape as workers/__init__.py.
httpx and httpcore are held to WARNING here because the API's sweeps
make dozens of venue calls a minute and a line per call would bury the
lines that matter; the workers keep theirs at INFO on purpose (the
per-call lines are how venue outages are read there).
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
