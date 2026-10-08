import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Secret-bearing URLs (the Polygon RPC key in httpx's INFO request line,
# production 2026-10-08) are redacted on the way out; the lines stay.
from .. import log_redaction as _log_redaction  # noqa: E402

_log_redaction.install()
