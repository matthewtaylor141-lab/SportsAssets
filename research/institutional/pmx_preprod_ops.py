"""THE THREE PREPROD OPERATIONS, AS THE VENUE DOCUMENTS THEM.

  auth       one bounded authentication + identity check, classified
  reconcile  the documented order search, scoped and validated
  stream     the gRPC order subscription (polymarket.v1.OrderEntryAPI)

A MODULE RATHER THAN A HEREDOC, because these have to be tested. The
subscription request has to be built from generated protobuf messages and
the search verdict has to distinguish four outcomes that a shell script
would flatten into "it printed nothing".

PREPRODUCTION ONLY. Every host is asserted against the preprod allow-list
before a socket is opened; there is no input, flag or environment variable
that reaches production from here.

WHAT IS NEVER PRINTED: the private key, the signed client assertion, the
bearer token, and full account payloads. Receipts carry request bodies
(which contain no credential), statuses, request identifiers, timestamps
and the identifiers returned -- nothing else.

THE AUTHENTICATION EXCHANGE HAS TWO AUDIENCES AND THEY ARE NOT THE SAME
VALUE. Conflating them is the defect this module now pins:

  CLIENT_ASSERTION_AUD    the `aud` CLAIM INSIDE the signed JWT. It names
                          the party that consumes the assertion -- Auth0's
                          token endpoint.
                          https://pmx-preprod.us.auth0.com/oauth/token

  TOKEN_REQUEST_AUDIENCE  the `audience` FORM FIELD of the token request.
                          It names the API the issued access token is for.
                          https://api.preprod.polymarketexchange.com

Until 2026-09-19 the assertion was signed with `aud = "https://<domain>/"`
-- the bare issuer. That is what the runner sent on 2026-09-10 and the
venue accepted it (200, a token, eleven scopes), so it is a value Auth0
tolerates; it is NOT the value the current documentation specifies. The
documented value is now what we send. The historical one is kept below as
CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED, as evidence and as a deliberate
second attempt if the first is rejected -- there is NO automatic fallback,
because a silent retry would leave us unable to say which value the venue
took.

THE ENCODING IS NOT CHANGED, and this is the documented exception rather
than an oversight. The documentation's example POSTs JSON. Retained venue
evidence establishes that FORM ENCODING IS ACCEPTED: runs 1-24 on
2026-09-10 (workflow revision a95b54e) posted
`application/x-www-form-urlencoded` and were answered 200 with a usable
bearer token. An accepted encoding is not replaced on the strength of an
example. If the venue ever rejects it, the JSON body is a one-line change
and the rejection will be legible.

One further discrepancy, recorded and not adopted: the example says
"tokens expire in 180 seconds"; the observed response said
`expires_in: 86400`. The lifetime is read from the response either way.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid

# ── the preprod boundary ─────────────────────────────────────────────

AUTH0_DOMAIN = "pmx-preprod.us.auth0.com"
REST_BASE = "https://api.preprod.polymarketexchange.com"

# THE TWO AUDIENCES. Read the module docstring before touching either.
# They are deliberately built from different constants so that a future
# edit cannot collapse them into one value by accident.
CLIENT_ASSERTION_AUD = "https://%s/oauth/token" % AUTH0_DOMAIN
TOKEN_REQUEST_AUDIENCE = REST_BASE

# What the runner actually sent on 2026-09-10 and the venue accepted.
# Kept as EVIDENCE and as a deliberate second attempt. Nothing in this
# codebase falls back to it automatically.
CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED = "https://%s/" % AUTH0_DOMAIN

# Retained venue evidence, not a preference: form encoding was answered
# 200 with a usable token by runs 1-24 (workflow revision a95b54e).
TOKEN_REQUEST_ENCODING = "application/x-www-form-urlencoded"
TOKEN_REQUEST_ENCODING_EVIDENCE = (
    "OBSERVED_PREPROD 2026-09-10: application/x-www-form-urlencoded was "
    "answered 200 with an access token and eleven scopes. The current "
    "documentation's example posts JSON; an accepted encoding is not "
    "replaced on the strength of an example.")

# ── the gRPC target is NOT settled, and the docs disagree with the docs
#
# Four spellings appear across the captured documentation. Three of them
# name preproduction and are candidates. The fourth carries no
# environment at all and is therefore NOT identifiably preprod, so it is
# deliberately absent from both lists below and `assert_preprod` refuses
# it -- an unqualified host could be production.
#
#   grpc-api.preprod...   /grpc-api/overview, /streaming-endpoints/*
#   grpc-preprod...       /trader-guide/environments, /connection-issues
#   grpc.preprod...       /data-guide/market-data, /candlestick-data
#   grpc-api...  (no env) /changelog                    <- NOT a candidate
#
# GRPC_TARGET stays the most-cited spelling. It is NOT rotated silently
# on a failure: `grpc-probe` tests the candidates and reports which of
# the five stages each one reaches, and a human reads that before any
# constant moves.
GRPC_TARGET = "grpc-api.preprod.polymarketexchange.com:443"

GRPC_CANDIDATES = (
    "grpc-api.preprod.polymarketexchange.com:443",
    "grpc-preprod.polymarketexchange.com:443",
    "grpc.preprod.polymarketexchange.com:443",
)

# Production is not probed, not guessed and not derived from a preprod
# result. It is a string that says so, and it is not a host.
PRODUCTION_GRPC_TARGET = "NOT_IDENTIFIED"

# api.preprod... is the host this project has actually transacted with.
# The streaming example names rest.preprod...; that is NOT adopted, for
# the same reason the auth example is not -- ours is the verified one.
_PREPROD_HOSTS = frozenset({
    "api.preprod.polymarketexchange.com",
    "pmx-preprod.us.auth0.com",
} | {c.rsplit(":", 1)[0] for c in GRPC_CANDIDATES})

# PREFIXED, and the prefix IS the attestation: an unprefixed PMX_* slot
# is PRODUCTION and is read by the production lane alone. Renamed
# 2026-09-19 when the PMX_* slots were filled with production values --
# moving the preprod lane is free (it has no keys today) and moving the
# production ones would have meant re-entering four secrets by hand.
CREDENTIALS_EXPECTED = ("PMX_PREPROD_CLIENT_ID", "PMX_PREPROD_PARTICIPANT_ID",
              "PMX_PREPROD_KEY_ID", "PMX_PREPROD_PRIVATE_KEY_B64")


class NotPreprod(RuntimeError):
    pass


def assert_preprod(url_or_target: str) -> str:
    """Refuse anything that is not a preproduction host."""
    s = str(url_or_target)
    host = s.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host not in _PREPROD_HOSTS:
        raise NotPreprod("refused: %r is not a preprod host" % host)
    return s


# ── 1. AUTHENTICATION, WITH THE FOUR OUTCOMES KEPT APART ─────────────
#
# These are four different problems with four different owners, and a
# single "it did not work" hides which one is in front of you.

A_SECRET_MISSING = "SECRET_MISSING"
A_SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE_TO_THIS_WORKFLOW"
A_REJECTED = "AUTHENTICATION_REJECTED"
A_PERMISSION_DENIED = "AUTHENTICATED_BUT_ENDPOINT_PERMISSION_DENIED"
A_OK = "AUTHENTICATED"
A_UNREACHABLE = "VENUE_UNREACHABLE"


def credential_presence(env=None) -> dict:
    """Which of the four secrets arrived, WITHOUT reading their values.

    THE HONEST LIMIT, stated because it matters to whoever has to fix it:
    a repository secret that does not exist and one that exists but is not
    exposed to this workflow BOTH arrive as an empty string. The step
    cannot tell them apart on its own.

    What it CAN tell apart -- and what the two verdicts below mean -- is
    whether the secret store reached this job at all:

      * every one of the four empty  -> nothing from the store reached
        this job. Either none is configured, or none is exposed to it.
        Reported as SECRET_MISSING, and the fix starts in repository
        settings.
      * some present, some empty     -> the store DID reach this job, so
        the empties are individually absent or individually not exposed.
        Reported as SECRET_UNAVAILABLE_TO_THIS_WORKFLOW, naming which.

    Only names and lengths are recorded. No value is returned or logged.
    """
    env = os.environ if env is None else env
    present, missing = [], []
    for name in CREDENTIALS_EXPECTED:
        v = env.get(name)
        (present if (v is not None and str(v).strip()) else missing).append(name)
    if not missing:
        verdict = None
    elif not present:
        verdict = A_SECRET_MISSING
    else:
        verdict = A_SECRET_UNAVAILABLE
    return {"present": present, "missing": missing, "verdict": verdict,
            "why": ("a secret that does not exist and one that is not exposed "
                    "to this workflow both arrive empty; which of the two it "
                    "is has to be read from repository settings by the "
                    "credential holder")}


def classify_auth(token_status: int | None, probe_status: int | None,
                  transport_error: str | None = None) -> str:
    """The verdict, from the two statuses that actually decide it."""
    if transport_error:
        return A_UNREACHABLE
    if token_status != 200:
        return A_REJECTED
    if probe_status == 403:
        # The token is good. The account is not permitted HERE. On
        # 2026-09-10 /v1/accounts/accounts answered 403 with a token that
        # carried read:accounts, so this is a real and separate state.
        return A_PERMISSION_DENIED
    if probe_status == 200:
        return A_OK
    return A_REJECTED if probe_status in (401,) else A_PERMISSION_DENIED


def identity_of(whoami_body) -> dict:
    """The identity confirmation, sanitized.

    Resource NAMES only -- firm, user, account. Never the whole payload,
    never a token, never a key.
    """
    if not isinstance(whoami_body, dict):
        return {"confirmed": False, "why": "whoami did not answer an object"}
    out = {}
    for key in ("user", "name", "firm", "participant", "account",
                "firmType", "firm_type"):
        v = whoami_body.get(key)
        if isinstance(v, str) and v:
            out[key] = v
    return {"confirmed": bool(out), "fields": out,
            "note": "resource names only; the full payload is not retained"}


# ── 2. THE DOCUMENTED SEARCH CONTRACT ────────────────────────────────
#
# https://docs.polymarket.us/api-reference/report/search-orders
#
# SearchOrdersRequest, exactly as the OpenAPI schema declares it:
#   orderId   string   SINGULAR
#   clordId   string   SINGULAR
#   accounts  string[] A LIST
#   symbol    string
#   startTime / endTime   date-time
#   pageSize / pageToken
#   orderStateFilter, side, type, withLastExecution, ...
#
# `clientAccountId` and `clientParticipantId` also exist. They are NOT
# used: their filtering behaviour has not been established here, and a
# scoped search built on a filter that may not filter is a search whose
# empty answer means nothing.

R_OK = "RECONCILED"
R_NOT_FOUND = "NOT_FOUND_ON_THIS_CONTRACT"
R_INCONCLUSIVE = "INCONCLUSIVE"
R_FAILED = "FAILED"

B_SCOPE = "SCOPE_INCOMPLETE"
B_HTTP = "HTTP_ERROR"
B_MALFORMED = "MALFORMED_JSON"
B_ROW_SHAPE = "INVALID_ROW_SHAPE"
B_TOKEN_REPEAT = "PAGE_TOKEN_REPEATED"
B_PAGE_CAP = "WALK_INCOMPLETE_PAGE_CAP"

MAX_PAGES = 50


def scope_blockers(spec: dict) -> list:
    """The intended account, instrument and window are REQUIRED.

    An unscoped search that answers nothing has not established anything
    about our order: it may simply have been asked the wrong question. So
    the scope is a precondition, not an option.
    """
    out = []
    accounts = spec.get("accounts")
    if not isinstance(accounts, list) or not [a for a in accounts
                                              if isinstance(a, str) and a.strip()]:
        out.append("%s: accounts must be a non-empty list of account "
                   "resource names" % B_SCOPE)
    if not str(spec.get("symbol") or "").strip():
        out.append("%s: symbol (the instrument) is required" % B_SCOPE)
    if not str(spec.get("startTime") or "").strip():
        out.append("%s: startTime is required" % B_SCOPE)
    if not str(spec.get("endTime") or "").strip():
        out.append("%s: endTime is required" % B_SCOPE)
    if not (str(spec.get("orderId") or "").strip()
            or str(spec.get("clordId") or "").strip()):
        out.append("%s: one of orderId or clordId is required" % B_SCOPE)
    return out


def search_body(spec: dict, key: str, value: str,
                page_token: str = "") -> dict:
    """One request body on the documented fields, and only those.

    `orderId` and `clordId` are kept separate: the caller says which one
    this query is for, and the other is not smuggled in beside it.
    """
    if key not in ("orderId", "clordId"):
        raise ValueError("search_body: key must be orderId or clordId")
    body = {
        key: value,
        "accounts": list(spec["accounts"]),
        "symbol": spec["symbol"],
        "startTime": spec["startTime"],
        "endTime": spec["endTime"],
        "pageSize": int(spec.get("pageSize") or 100),
    }
    if spec.get("orderStateFilter"):
        body["orderStateFilter"] = spec["orderStateFilter"]
    if spec.get("withLastExecution"):
        body["withLastExecution"] = True
    if page_token:
        body["pageToken"] = page_token
    return body


def validate_row(row, spec: dict) -> dict:
    """One returned order, checked against what was ASKED for.

    A row that came back from a query bound to our account and instrument
    but carries someone else's is not our order, and counting it would
    turn a filter that does not filter into a reconciliation.
    """
    if not isinstance(row, dict):
        return {"ok": False, "why": "%s: row is not an object" % B_ROW_SHAPE}
    oid = row.get("id")
    clord = row.get("clordId")
    if not isinstance(oid, str) or not oid:
        return {"ok": False, "why": "%s: no string id" % B_ROW_SHAPE}
    acct, sym = row.get("account"), row.get("symbol")
    mismatches = []
    if acct not in spec["accounts"]:
        mismatches.append("account %r not among the accounts asked for" % acct)
    if sym != spec["symbol"]:
        mismatches.append("symbol %r is not %r" % (sym, spec["symbol"]))
    return {"ok": not mismatches, "id": oid, "clordId": clord,
            "account": acct, "symbol": sym, "state": row.get("state"),
            "why": "; ".join(mismatches) or None}


def verdict(queries: list, matched: list) -> dict:
    """The classification, and the rule that an empty walk is not a result.

    FAILED and INCONCLUSIVE are terminal for this run. Neither may be
    reported as NOT_FOUND: an HTTP error, unreadable JSON, a row we could
    not parse, a repeated page token or a walk cut by the page cap all
    mean we do not know, and "we do not know" is not "it is not there".

    And NOT_FOUND itself is not permission to send another order.
    """
    failures = [b for q in queries for b in q.get("blockers", [])]
    hard = [b for b in failures if b.split(":")[0] in (B_HTTP, B_MALFORMED)]
    soft = [b for b in failures if b.split(":")[0] in (B_ROW_SHAPE,
                                                       B_TOKEN_REPEAT,
                                                       B_PAGE_CAP)]
    if hard:
        status = R_FAILED
    elif matched:
        status = R_OK
    elif soft:
        status = R_INCONCLUSIVE
    else:
        status = R_NOT_FOUND
    out = {"status": status, "matchedOn": sorted({m["on"] for m in matched}),
           "blockers": sorted(set(failures)), "queries": queries}
    if status == R_OK:
        # AN IDENTIFIER MATCH IS NOT A RECONCILED LIFECYCLE. It says the
        # venue has a record under that id on that account and
        # instrument. It says nothing about fills, cancellation or
        # settlement, and this field exists so nobody reads it in.
        out["meaning"] = ("the venue returned a row whose identifier, "
                          "account and instrument all match what was asked "
                          "for. That is ORDER RECORD FOUND. It is NOT a "
                          "reconciled lifecycle: fills, cancellation and "
                          "settlement are separate facts not established "
                          "by this search")
    if status == R_NOT_FOUND:
        out["meaning"] = ("the correctly scoped search returned no matching "
                          "row. This is NOT proof of non-submission and NOT "
                          "permission to send another order. Finalize the "
                          "venue-support trace package instead")
    if status == R_INCONCLUSIVE:
        out["meaning"] = ("the walk did not complete or a row could not be "
                          "read. This is NOT a NOT_FOUND result")
    if status == R_FAILED:
        out["meaning"] = ("the venue errored or answered unreadably. This is "
                          "NOT a NOT_FOUND result")
    return out


# ── 3. THE gRPC ORDER SUBSCRIPTION ───────────────────────────────────
#
# https://docs.polymarket.us/streaming-endpoints/order-stream
#
#   host     grpc-api.preprod.polymarketexchange.com:443
#   service  polymarket.v1.OrderEntryAPI
#   rpc      CreateOrderSubscription  (server-side streaming)
#   request  CreateOrderSubscriptionRequest(symbols, accounts, snapshot_only)
#   auth     metadata ('authorization', 'Bearer <token>')
#
# The generated client comes from the venue's own downloadable proto
# bundle, generated on the runner. Nothing here hand-writes a message.

#
# ── THE HOSTNAME DIAGNOSTIC ──────────────────────────────────────────
#
# Five stages, reported separately, because "it did not connect" hides
# which of five different things went wrong and who owns it:
#
#   DNS_RESOLUTION       does the name exist at all?        (their DNS)
#   TCP_TLS_CONNECTION   does :443 answer and negotiate?    (their edge)
#   GRPC_CHANNEL_READY   does a gRPC channel reach READY?   (their proxy)
#   AUTHENTICATED_RPC    does the RPC start with our token? (our creds)
#   PERMISSION_RESULT    what does the service say we may do?  (scopes)
#
# A host that fails at DNS_RESOLUTION says NOTHING about the service. A
# host that reaches AUTHENTICATED_RPC and then returns PERMISSION_DENIED
# says nothing about the hostname. Keeping the stages apart is the whole
# point of the probe.

G_DNS = "DNS_RESOLUTION"
G_TCP = "TCP_TLS_CONNECTION"
G_READY = "GRPC_CHANNEL_READY"
G_RPC = "AUTHENTICATED_RPC"
G_PERM = "PERMISSION_RESULT"
GRPC_STAGES = (G_DNS, G_TCP, G_READY, G_RPC, G_PERM)

GRPC_UNAVAILABLE_MEANING = (
    "UNAVAILABLE (code 14) on ONE hostname is evidence about THAT NAME "
    "and nothing else. It is not evidence that the institutional gRPC "
    "service is down, that our credentials are wrong, or that the "
    "capability does not exist. Only a host that reaches "
    "AUTHENTICATED_RPC can produce evidence about the service.")


def grpc_probe_verdict(candidates: list) -> dict:
    """Summarize a probe WITHOUT choosing a winner for anyone.

    `candidates` is a list of per-host dicts each carrying a `stages`
    mapping of stage name -> bool/None. This returns which hosts reached
    which stage. It deliberately does NOT write GRPC_TARGET, return a
    "use this one" field, or collapse to a single answer: rotating a
    host is a human decision, and in production it is not ours at all.
    """
    reached = {stage: [] for stage in GRPC_STAGES}
    for c in candidates or []:
        stages = (c or {}).get("stages") or {}
        for stage in GRPC_STAGES:
            if stages.get(stage) is True:
                reached[stage].append(c.get("target"))
    return {"environment": "PREPROD",
            "candidatesTried": [c.get("target") for c in (candidates or [])],
            "reachedStage": reached,
            "authenticatedRpc": reached[G_RPC],
            "configuredTarget": GRPC_TARGET,
            "targetRotated": False,
            "productionGrpcTarget": PRODUCTION_GRPC_TARGET,
            "meaning": GRPC_UNAVAILABLE_MEANING}


PROTO_BUNDLE_URL = ("https://drive.google.com/uc?export=download"
                    "&id=1oT9gaeBEn0vukHD9GOoj_YvzPnR3otng")

PROTOC_CMD = ("python -m grpc_tools.protoc --python_out=gen "
              "--grpc_python_out=gen --proto_path=protos/api "
              "protos/api/polymarket/v1/*.proto protos/api/google/api/*.proto "
              "protos/api/protoc-gen-openapiv2/options/*.proto")


def build_subscription_request(trading_pb2, symbols=None, accounts=None,
                               snapshot_only=True):
    """The request, from the GENERATED message type.

    Passing the module in is what makes this testable: the real
    `polymarket.v1.trading_pb2` only exists after the bundle is generated
    on the runner, and a hand-rolled stand-in would prove nothing about
    the wire. A test supplies a recording double and asserts the field
    names; the runner supplies the real one.
    """
    return trading_pb2.CreateOrderSubscriptionRequest(
        symbols=list(symbols or []),
        accounts=list(accounts or []),
        snapshot_only=bool(snapshot_only),
    )


def observe_message(response, seen: dict) -> None:
    """Record what a stream message ACTUALLY is, without asserting a shape.

    The event is a oneof of heartbeat / snapshot / update. Which arrived,
    how many orders a snapshot carried, and whether any execution carried
    an aggressor flag are read off the message; nothing is assumed.
    """
    seen["messages"] = seen.get("messages", 0) + 1
    sid = getattr(response, "session_id", "")
    if sid and not seen.get("sessionId"):
        seen["sessionId"] = sid
    for name in ("heartbeat", "snapshot", "update"):
        try:
            if not response.HasField(name):
                continue
        except (ValueError, AttributeError):
            continue
        seen[name] = seen.get(name, 0) + 1
        if name == "snapshot":
            orders = list(getattr(response.snapshot, "orders", []))
            seen["snapshotOrders"] = seen.get("snapshotOrders", 0) + len(orders)
            for o in orders:
                if getattr(o, "clord_id", ""):
                    seen.setdefault("clordIds", []).append(o.clord_id)
                if getattr(o, "id", ""):
                    seen.setdefault("orderIds", []).append(o.id)
                if getattr(o, "account", ""):
                    seen.setdefault("accounts", []).append(o.account)
        if name == "update":
            for ex in getattr(response.update, "executions", []):
                seen["executions"] = seen.get("executions", 0) + 1
                if getattr(ex, "aggressor", False):
                    seen["aggressorTrue"] = seen.get("aggressorTrue", 0) + 1


STREAM_EVIDENCE_LIMIT = (
    "an authenticated snapshot -- including an EMPTY one -- is evidence "
    "that the subscription was established and that this credential is "
    "scoped to these accounts. It is NOT evidence of a fill, a "
    "cancellation, replay completeness, or profitability. The documented "
    "`aggressor` field on an execution concerns OUR OWN executions; "
    "full-market aggressor coverage and exact queue position are separate "
    "claims and remain unverified")


# ── receipts ─────────────────────────────────────────────────────────

def receipt(kind: str, **fields) -> dict:
    """A sanitized receipt. Nothing secret can be put in one by accident:
    the writer refuses a value that looks like a credential."""
    r = {"kind": kind,
         "runId": os.environ.get("GITHUB_RUN_ID"),
         "executedRevision": os.environ.get("GITHUB_SHA"),
         "environment": "PREPROD",
         "restBase": REST_BASE,
         "grpcTarget": GRPC_TARGET,
         "at": time.time()}
    r.update(fields)
    _refuse_secrets(r)
    return r


_FORBIDDEN = ("BEGIN RSA", "BEGIN PRIVATE", "BEGIN EC", "access_token",
              "Bearer ey", "client_assertion")


def _refuse_secrets(obj) -> None:
    blob = json.dumps(obj, default=str)
    for marker in _FORBIDDEN:
        if marker in blob:
            raise RuntimeError(
                "receipt refused: it contains %r, which is a credential or a "
                "signed assertion and must never be written down" % marker)


def write_receipt(path, payload) -> str:
    _refuse_secrets(payload)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, default=str))
    return str(p)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op", choices=("auth", "reconcile", "stream",
                                   "grpc-probe", "presence"))
    ap.add_argument("--spec", default="", help="JSON argument for the op")
    ap.add_argument("--out", default="", help="where to write the receipt")
    ap.add_argument("--seconds", type=float, default=60.0)
    a = ap.parse_args(argv)

    if a.op == "presence":
        # No network at all: just say which secrets reached this job.
        out = receipt("presence", **credential_presence())
        print(json.dumps(out, indent=1))
        if a.out:
            write_receipt(a.out, out)
        return 0 if out["verdict"] is None else 1

    # The network operations live in `pmx_preprod_net`, which imports
    # `requests` / `grpc`. Keeping them out of this module means the
    # contract above can be tested on a runner with neither installed.
    from pmx_preprod_net import run_op          # noqa: E402
    return run_op(a)


if __name__ == "__main__":
    sys.exit(_cli())
