"""THE VENUE SDK VERSION, SELECTED — NOT LEFT TO A BUILD-TIME RESOLUTION.

── THE DEFECT THIS CLOSES ──────────────────────────────────────────
`backend/pyproject.toml` asked for `polymarket-us>=0.1.2` and the image
builds with `pip install .`, so the deployed version was WHATEVER PyPI
happened to have latest at the minute the image was built. That is not a
dependency decision; it is the absence of one, and it produced a concrete
divergence:

    this container had 0.1.2 installed  -> `httpx.AsyncClient(timeout=…)`,
                                           NO retry logic at all
    PyPI latest is 1.0.2 (2026-09-24)   -> `max_retries: int = 2`, so one
                                           logical call makes up to THREE
                                           HTTP attempts

An independent reproduction measured exactly that: 1 call to the outer
pacer, 3 HTTP attempts, all three 429. The local tests could never have
seen it, because the local tests were running a different client.

── WHAT WAS ESTABLISHED, AND HOW ───────────────────────────────────
Read out of the 1.0.2 wheel itself, not assumed:

  * `PolymarketUS(*, key_id, secret_key, gateway_base_url, api_base_url,
    timeout=30.0, max_retries=2)` — and `AsyncPolymarketUS` takes the same
    keywords, so the synchronous and asynchronous constructors agree.
  * `self._http = httpx.Client(timeout=timeout)` on the sync client, which
    is the attribute `venue_http_observer` and the request gate both reach
    for. Present under this version.
  * Retries are performed INSIDE `_request`, with `time.sleep(...)` between
    attempts. The outer pacer cannot see them. A wrapped TRANSPORT can, and
    does, which is why the gate lives there.
  * Retryable statuses: {408, 409, 429, 500, 502, 503, 504}.
  * Retryable methods: {GET, HEAD, OPTIONS, DELETE}. **POST is never
    retried** — the SDK's own docstring gives the reason: the API has no
    idempotency key, so retrying a partially-failed order could submit a
    duplicate. That is the property this system requires of order
    submission, and it is now a read fact rather than a hope.
  * `backoff_delay` honours a `Retry-After` header **in integer seconds
    only** and clamps it to 8 s. It has no HTTP-date parse. Ours does
    (`venue_http_error.describe`), and ours is the one that arms the
    cooldown, so a date-form `Retry-After` is not silently dropped.
  * Every resource method this repository calls exists on 1.0.2 with the
    positional-`params` shape our call sites already use:
    markets.{bbo,book,list,retrieve_by_slug,settlement},
    orders.{cancel,close_position,create,list,preview,retrieve},
    portfolio.{activities,positions}, events.list, account.balances,
    search.query.

── THE DECISION ────────────────────────────────────────────────────
PIN 1.0.2, EXACTLY, and turn the SDK's own retries OFF.

Pinning 1.0.2 rather than 0.1.2 because 1.0.2 is what the deployed image
already resolved: pinning the older one would be a silent downgrade of
production dressed up as reproducibility.

`max_retries=0` because an SDK retry sleeps inside one logical call with no
knowledge of the decision deadline it is spending. Retrying is still
correct — it is OUR retry, bounded, counted, honouring `Retry-After`, and
rechecking the not-before instant before each dispatch. Two retry
mechanisms stacked multiply the request count (2 × 3 = 6) for the same
answer; one of them has to be the only one, and it should be the one that
knows when to stop.

── WHAT THIS MODULE REFUSES TO DO ──────────────────────────────────
It never reports retries as disabled on the strength of having asked. The
kwarg is offered, the constructor's acceptance is checked, and
`report()["sdk_retries_disabled"]` is False when the installed build did
not take it. A control that reports instead of controlling is the error
this whole batch exists to correct.

It is also the answer to "which version is deployed": `report()` is
serialisable and rides the heartbeat, so the question is settled by asking
the running process instead of inferring from a build log.
"""

from __future__ import annotations

import inspect

#: The version this repository selects. `pyproject.toml` must pin exactly
#: this, and `test_the_venue_sdk_is_pinned_to_one_version` asserts it.
PINNED = "1.0.2"

#: Retries we ask the SDK NOT to perform, because we perform them.
OUR_MAX_RETRIES_KWARG = {"max_retries": 0}

#: Read out of 1.0.2's `_retry` module. Recorded so a version bump that
#: changes them shows up as a disagreement rather than as behaviour.
EXPECTED_RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
EXPECTED_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})

R_NOT_INSTALLED = "VENUE_SDK_IS_NOT_INSTALLED"
R_VERSION_UNKNOWN = "VENUE_SDK_VERSION_COULD_NOT_BE_READ"
R_VERSION_DIFFERS = "VENUE_SDK_VERSION_IS_NOT_THE_PINNED_ONE"
R_RETRY_NOT_SETTABLE = "VENUE_SDK_DID_NOT_ACCEPT_max_retries"


def installed() -> str | None:
    """The installed distribution version, or None if it cannot be read."""
    try:
        from importlib.metadata import version
        return str(version("polymarket-us"))
    except Exception:                                          # noqa: BLE001
        return None


def _ctor_accepts(name: str) -> bool | None:
    """Whether the sync constructor accepts `name`. None if unreadable.

    NONE IS NOT FALSE. False says the parameter was examined and is absent;
    None says the constructor could not be examined, which is a different
    fact and must not be reported as "retries are on".
    """
    try:
        from polymarket_us import PolymarketUS
        sig = inspect.signature(PolymarketUS.__init__)
    except Exception:                                          # noqa: BLE001
        return None
    params = sig.parameters
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in params.values())


def client_kwargs() -> dict:
    """Extra constructor keywords for OUR client. Empty when unsupported.

    Passing `max_retries=0` to a build that has no such parameter is a
    TypeError on every read, so the parameter is offered only when the
    constructor was seen to accept it. The fact that it was withheld is
    reported by `report()`, never swallowed.
    """
    return dict(OUR_MAX_RETRIES_KWARG) if _ctor_accepts("max_retries") else {}


def retry_facts() -> dict:
    """What the INSTALLED build's retry module actually says.

    Read, not remembered. A version bump that widens the retryable set or
    starts retrying POST becomes a visible disagreement here instead of a
    surprise in the request count.
    """
    out = {"module_readable": False, "retryable_statuses": None,
           "idempotent_methods": None, "default_max_retries": None,
           "post_is_retried": None, "matches_expected": None,
           "retry_after_header_parsed": None}
    try:
        from polymarket_us import _retry as R
    except Exception:                                          # noqa: BLE001
        return out
    out["module_readable"] = True
    try:
        st = frozenset(int(s) for s in R.RETRYABLE_STATUS_CODES)
        me = frozenset(str(m).upper() for m in R.IDEMPOTENT_METHODS)
        out["retryable_statuses"] = sorted(st)
        out["idempotent_methods"] = sorted(me)
        out["default_max_retries"] = int(getattr(R, "DEFAULT_MAX_RETRIES", -1))
        # THE ONE THAT MATTERS FOR ORDER SAFETY.
        out["post_is_retried"] = bool(R.can_retry_method("POST"))
        out["matches_expected"] = bool(st == EXPECTED_RETRYABLE_STATUSES
                                       and me == EXPECTED_IDEMPOTENT_METHODS)
        # Integer seconds only in 1.0.2; a date form returns None there.
        # Ours parses both, and ours arms the cooldown.
        out["retry_after_header_parsed"] = {
            "integer_seconds": R.retry_after_seconds("30"),
            "http_date": R.retry_after_seconds(
                "Wed, 21 Oct 2026 07:28:00 GMT"),
        }
    except Exception as exc:                                   # noqa: BLE001
        out["read_failed"] = type(exc).__name__
    return out


def report() -> dict:
    """The whole dependency position, serialisable, for the heartbeat.

    Deliberately includes the refusals by name. An operator reading
    `pinned_matches_installed: false` has been told which build is running
    and which one the repository chose; a report that only carried the
    version would leave them to compare it themselves.
    """
    got = installed()
    accepts = _ctor_accepts("max_retries")
    kw = client_kwargs()
    facts = retry_facts()
    refusals = []
    if got is None:
        refusals.append(R_NOT_INSTALLED)
    elif got != PINNED:
        refusals.append(R_VERSION_DIFFERS)
    if accepts is None:
        refusals.append(R_VERSION_UNKNOWN)
    elif accepts is False:
        refusals.append(R_RETRY_NOT_SETTABLE)
    return {
        "distribution": "polymarket-us",
        "pinned": PINNED,
        "installed": got,
        "pinned_matches_installed": bool(got is not None and got == PINNED),
        # THE CLAIM IS CONDITIONED ON THE KWARG HAVING BEEN ACCEPTED.
        "sdk_retries_disabled": bool(kw.get("max_retries") == 0
                                     and accepts is True),
        "max_retries_kwarg_accepted": accepts,
        "our_client_kwargs": kw,
        "sdk_retry_facts": facts,
        "refusals": refusals,
        "who_retries": ("this repository, in pmus.book_read: bounded, "
                        "counted per logical read, honouring Retry-After "
                        "(seconds OR HTTP-date) and rechecking the "
                        "not-before instant before every dispatch"),
        "order_submission": ("never retried. The SDK refuses to retry POST "
                             "because the API has no idempotency key, and "
                             "we add no retry of our own"),
        "why_pinned_here_and_not_only_in_pyproject": (
            "pyproject fixes what an image INSTALLS; this fixes what the "
            "running process can be asked. A build log is not available "
            "to an operator reading a heartbeat"),
    }


def verdict() -> dict:
    """The report plus a pass/fail, for a gate step to act on.

    FAILS ON "WE COULD NOT TELL" AS WELL AS ON "IT IS WRONG". An unreadable
    version and a mismatched one are different facts, and neither is
    permission to proceed -- so both appear in `refusals` and both fail.
    The POST check is separate because it is the one retry property whose
    silent change could duplicate an order.
    """
    r = report()
    refusals = list(r["refusals"])
    facts = r["sdk_retry_facts"]
    if facts.get("post_is_retried") is not False:
        refusals.append("SDK_WOULD_RETRY_POST")
    if facts.get("matches_expected") is not True:
        refusals.append("SDK_RETRY_SET_IS_NOT_THE_ONE_WE_DESIGNED_AGAINST")
    return dict(r, refusals=refusals, ok=not refusals)


def _main(argv=None) -> int:
    """`python -m sportsassets.venue_sdk` -- the gate step's whole body.

    A MODULE RATHER THAN INLINE WORKFLOW PYTHON, deliberately. A multi-line
    `python3 -c` body inside a YAML block scalar puts lines at column 0,
    which ends the scalar; GitHub then reports a missing `workflow_dispatch`
    trigger, which names nothing about indentation. This file is also
    testable, which inline script is not.
    """
    import json

    v = verdict()
    keep = ("pinned", "installed", "pinned_matches_installed",
            "sdk_retries_disabled", "max_retries_kwarg_accepted",
            "refusals", "ok")
    print(json.dumps({k: v[k] for k in keep}, indent=2))
    f = v["sdk_retry_facts"]
    print("sdk default_max_retries: %s" % f.get("default_max_retries"))
    print("sdk post_is_retried:     %s" % f.get("post_is_retried"))
    print("sdk retryable_statuses:  %s" % (f.get("retryable_statuses"),))
    if not v["ok"]:
        print("REFUSED: %s" % ", ".join(v["refusals"]))
        return 1
    print("OK: the installed venue SDK is the pinned one and its own "
          "retries are off")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    import sys

    sys.exit(_main(sys.argv[1:]))


__all__ = ["PINNED", "installed", "client_kwargs", "retry_facts", "report",
           "verdict",
           "R_NOT_INSTALLED", "R_VERSION_UNKNOWN", "R_VERSION_DIFFERS",
           "R_RETRY_NOT_SETTABLE", "EXPECTED_RETRYABLE_STATUSES",
           "EXPECTED_IDEMPOTENT_METHODS"]
