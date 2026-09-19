"""THE NETWORK HALF. Imports `requests`; imports `grpc` only for `stream`.

Split from `pmx_preprod_ops` on purpose: the contract -- the search body,
the row validation, the verdict rules, the auth classification -- is pure
and must be testable on a machine with neither library installed. This
file is the part that opens sockets, and it makes no decisions of its own:
every verdict comes from the module beside it.

NOTHING HERE SENDS AN ORDER. There is no insert, cancel, replace or
funding call in this file, and the subscription is read-only by
construction.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid

import pmx_preprod_ops as ops

TIMEOUT = (10.0, 30.0)


# ── the verified authentication exchange, unchanged ──────────────────

def _private_key_pem() -> str:
    raw = os.environ["PMX_PREPROD_PRIVATE_KEY_B64"]
    if "-----BEGIN" in raw[:64]:
        return raw                     # a PEM pasted as-is
    pem = base64.b64decode("".join(raw.split())).decode()
    if "BEGIN" not in pem:
        raise RuntimeError(
            "PMX_PREPROD_PRIVATE_KEY_B64 is neither a PEM nor base64 of one")
    return pem


def mint_token(session) -> tuple:
    """(status, token, expires_in, error_word).

    THE TOKEN GOES STRAIGHT INTO A HEADER and is never written into a
    receipt: `ops.write_receipt` refuses a payload containing one.

    TWO AUDIENCES, NOT ONE. The `aud` CLAIM inside the signed assertion
    names Auth0's token endpoint (ops.CLIENT_ASSERTION_AUD); the
    `audience` FORM FIELD of the request names the API the token is for
    (ops.TOKEN_REQUEST_AUDIENCE). They are different values and are read
    from two different constants so they cannot silently converge.

    Until 2026-09-19 the assertion was signed with the bare issuer,
    `https://<domain>/`. The venue accepted that on 2026-09-10, so it is
    a tolerated value -- but the current documentation specifies the
    token endpoint, and that is now what we send. There is no automatic
    fallback to the old value: a silent retry would leave us unable to
    say which audience the venue actually took.

    THE ENCODING IS UNCHANGED and that is deliberate. The documentation's
    example posts JSON; retained venue evidence (runs 1-24, revision
    a95b54e) establishes that form encoding is accepted. See
    ops.TOKEN_REQUEST_ENCODING_EVIDENCE.
    """
    import jwt

    cid = os.environ["PMX_PREPROD_CLIENT_ID"]
    kid = os.environ["PMX_PREPROD_KEY_ID"]
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": cid, "sub": cid, "aud": ops.CLIENT_ASSERTION_AUD,
         "iat": now, "exp": now + 60, "jti": str(uuid.uuid4())},
        _private_key_pem(), algorithm="RS256", headers={"kid": kid})
    url = ops.assert_preprod("https://%s/oauth/token" % ops.AUTH0_DOMAIN)
    r = session.post(url, data={
        "client_id": cid,
        "client_assertion_type":
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "audience": ops.TOKEN_REQUEST_AUDIENCE,
        "grant_type": "client_credentials"},
        headers={"Content-Type": ops.TOKEN_REQUEST_ENCODING},
        timeout=TIMEOUT)
    body = {}
    try:
        body = r.json()
    except Exception:                                          # noqa: BLE001
        pass
    tok = body.get("access_token") if isinstance(body, dict) else None
    # the error WORD only, never the body
    why = (body.get("error") or "") if isinstance(body, dict) else ""
    return r.status_code, tok, (body or {}).get("expires_in"), str(why)[:80]


def _headers(token: str) -> dict:
    return {"Authorization": "Bearer %s" % token,
            "X-Client-Id": os.environ["PMX_PREPROD_CLIENT_ID"],
            "x-participant-id": os.environ["PMX_PREPROD_PARTICIPANT_ID"],
            "Content-Type": "application/json"}


def _request_id() -> str:
    """A DISTINCT identifier per request.

    The previous version minted one per RUN and put it on every call, so
    a receipt could not be matched to the request it describes and venue
    support could not be pointed at one call. One per request, recorded.
    """
    return str(uuid.uuid4())


# ── op: auth ─────────────────────────────────────────────────────────

def op_auth(args) -> int:
    import requests

    presence = ops.credential_presence()
    if presence["verdict"]:
        out = ops.receipt("auth", verdict=presence["verdict"], **presence)
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    s = requests.Session()
    t0 = time.time()
    try:
        tok_status, token, expires_in, why = mint_token(s)
    except ops.NotPreprod:
        raise
    except Exception as exc:                                   # noqa: BLE001
        out = ops.receipt("auth", verdict=ops.A_UNREACHABLE,
                          transportError=type(exc).__name__,
                          elapsedS=round(time.time() - t0, 3))
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    probe_status, ident, rid = None, None, None
    if tok_status == 200 and token:
        rid = _request_id()
        url = ops.assert_preprod(ops.REST_BASE + "/v1/whoami")
        r = s.get(url, headers={**_headers(token), "X-Request-Id": rid},
                  timeout=TIMEOUT)
        probe_status = r.status_code
        if probe_status == 200:
            try:
                ident = ops.identity_of(r.json())
            except Exception:                                  # noqa: BLE001
                ident = {"confirmed": False, "why": "whoami was not JSON"}

    v = ops.classify_auth(tok_status, probe_status)
    out = ops.receipt(
        "auth", verdict=v,
        tokenStatus=tok_status,
        # the venue's own stated lifetime, which is a fact about the
        # venue and not a secret
        tokenExpiresIn=expires_in,
        tokenError=(why or None) if tok_status != 200 else None,
        probeEndpoint="/v1/whoami", probeStatus=probe_status,
        probeRequestId=rid, identity=ident,
        elapsedS=round(time.time() - t0, 3),
        secretsPresent=presence["present"])
    print(json.dumps(out, indent=1))
    if args.out:
        ops.write_receipt(args.out, out)
    return 0 if v == ops.A_OK else 1


# ── op: reconcile ────────────────────────────────────────────────────

def op_reconcile(args) -> int:
    import requests

    spec = json.loads(args.spec) if args.spec.strip() else {}
    blockers = ops.scope_blockers(spec)
    if blockers:
        out = ops.receipt("reconcile", status=ops.R_FAILED, blockers=blockers,
                          why="the scoped search was not attempted")
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    s = requests.Session()
    tok_status, token, _, why = mint_token(s)
    if tok_status != 200 or not token:
        out = ops.receipt("reconcile", status=ops.R_FAILED,
                          blockers=["%s: token %s %s" % (ops.B_HTTP,
                                                         tok_status, why)])
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    url = ops.assert_preprod(ops.REST_BASE + "/v1/report/orders/search")
    queries, matched, receipts = [], [], []

    for key in ("orderId", "clordId"):
        value = str(spec.get(key) or "").strip()
        if not value:
            continue
        q = {"query": key, "pages": 0, "rows": 0, "blockers": []}
        token_seen, page_token = set(), ""
        while True:
            body = ops.search_body(spec, key, value, page_token)
            rid = _request_id()
            t0 = time.time()
            r = s.post(url, headers={**_headers(token), "X-Request-Id": rid},
                       json=body, timeout=TIMEOUT)
            q["pages"] += 1
            rows, parsed = [], None
            try:
                parsed = r.json()
            except Exception as exc:                           # noqa: BLE001
                q["blockers"].append("%s: page %d did not parse (%s)"
                                     % (ops.B_MALFORMED, q["pages"],
                                        type(exc).__name__))
            if r.status_code != 200:
                q["blockers"].append("%s: page %d answered %d"
                                     % (ops.B_HTTP, q["pages"], r.status_code))
            elif isinstance(parsed, dict):
                raw = parsed.get("order")
                if raw is None:
                    raw = parsed.get("orders")
                if raw is None:
                    raw = []
                if not isinstance(raw, list):
                    q["blockers"].append("%s: page %d `order` is not a list"
                                         % (ops.B_ROW_SHAPE, q["pages"]))
                    raw = []
                rows = raw
            elif parsed is not None:
                q["blockers"].append("%s: page %d was not an object"
                                     % (ops.B_ROW_SHAPE, q["pages"]))

            checked = []
            for row in rows:
                v = ops.validate_row(row, spec)
                checked.append(v)
                if not v["ok"]:
                    # Either the row was unreadable or it carried an
                    # account/instrument we did not ask for. Both are
                    # INVALID_ROW_SHAPE for the verdict's purposes: a
                    # search whose filter did not filter has not told us
                    # what we asked.
                    q["blockers"].append("%s: page %d %s"
                                         % (ops.B_ROW_SHAPE, q["pages"],
                                            v["why"]))
                    continue
                if (key == "orderId" and v["id"] == value) or \
                   (key == "clordId" and v["clordId"] == value):
                    matched.append({"on": key, **v})
            q["rows"] += len(rows)

            receipts.append({
                "query": key, "page": q["pages"], "requestId": rid,
                "request": body, "status": r.status_code,
                "requestedAt": t0, "elapsedS": round(time.time() - t0, 3),
                "rows": [{k: c.get(k) for k in
                          ("id", "clordId", "account", "symbol", "state")}
                         for c in checked],
            })

            nxt = (parsed or {}).get("nextPageToken") or "" \
                if isinstance(parsed, dict) else ""
            if not nxt or q["blockers"]:
                break
            if nxt in token_seen:
                # A TOKEN THAT REPEATS IS A WALK THAT DOES NOT TERMINATE.
                # Following it forever, or stopping and calling the result
                # empty, are both wrong; it is INCONCLUSIVE.
                q["blockers"].append("%s: page %d returned a token already "
                                     "seen" % (ops.B_TOKEN_REPEAT, q["pages"]))
                break
            token_seen.add(nxt)
            page_token = nxt
            if q["pages"] >= ops.MAX_PAGES:
                q["blockers"].append("%s: stopped at %d pages with a token "
                                     "still outstanding"
                                     % (ops.B_PAGE_CAP, q["pages"]))
                break
        queries.append(q)

    out = ops.receipt("reconcile", **ops.verdict(queries, matched))
    out["receipts"] = receipts
    print(json.dumps({k: v for k, v in out.items() if k != "receipts"},
                     indent=1))
    if args.out:
        ops.write_receipt(args.out, out)
    return 0 if out["status"] == ops.R_OK else 1


# ── op: stream ───────────────────────────────────────────────────────

def op_stream(args) -> int:
    import grpc
    import requests

    spec = json.loads(args.spec) if args.spec.strip() else {}
    s = requests.Session()
    tok_status, token, _, why = mint_token(s)
    if tok_status != 200 or not token:
        out = ops.receipt("stream", established=False,
                          blockers=["token %s %s" % (tok_status, why)])
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    # THE GENERATED CLIENT, from the venue's own bundle. If it is not
    # importable the run says so; it does not fall back to a hand-written
    # message, which would prove nothing about the wire.
    try:
        from polymarket.v1 import trading_pb2, trading_pb2_grpc
    except ImportError as exc:
        out = ops.receipt("stream", established=False,
                          blockers=["GENERATED_CLIENT_MISSING: %s" % exc],
                          why="the proto bundle was not generated; see "
                              "PROTO_BUNDLE_URL and PROTOC_CMD")
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    # An operator may name one of the documented candidates explicitly;
    # it is still asserted preprod. NOTHING ROTATES ON ITS OWN -- if the
    # configured target fails, the run says so and `grpc-probe` is the
    # tool that establishes which name works.
    target = ops.assert_preprod(spec.get("target") or ops.GRPC_TARGET)
    meta = [("authorization", "Bearer %s" % token)]

    def once(label, snapshot_only):
        seen = {"label": label, "snapshotOnly": snapshot_only}
        t0 = time.time()
        channel = grpc.secure_channel(target, grpc.ssl_channel_credentials())
        try:
            stub = trading_pb2_grpc.OrderEntryAPIStub(channel)
            req = ops.build_subscription_request(
                trading_pb2, symbols=spec.get("symbols"),
                accounts=spec.get("accounts"), snapshot_only=snapshot_only)
            for response in stub.CreateOrderSubscription(req, metadata=meta):
                ops.observe_message(response, seen)
                if snapshot_only and seen.get("snapshot"):
                    break
                if time.time() - t0 > args.seconds:
                    break
        except grpc.RpcError as exc:
            seen["rpcError"] = {"code": str(exc.code()),
                                "details": str(exc.details())[:200]}
        finally:
            channel.close()
        seen["seconds"] = round(time.time() - t0, 2)
        seen["established"] = "rpcError" not in seen
        return seen

    first = once("SUBSCRIBE", snapshot_only=True)
    second = once("RECONNECT", snapshot_only=True)
    out = ops.receipt("stream", subscribe=first, reconnect=second,
                      established=bool(first.get("established")),
                      service="polymarket.v1.OrderEntryAPI",
                      rpc="CreateOrderSubscription",
                      evidenceLimit=ops.STREAM_EVIDENCE_LIMIT)
    print(json.dumps(out, indent=1))
    if args.out:
        ops.write_receipt(args.out, out)
    return 0 if out["established"] else 1


# ── op: grpc-probe ───────────────────────────────────────────────────
#
# PREPRODUCTION ONLY, and read-only: the one RPC it opens is the order
# SUBSCRIPTION, which places nothing. Its job is to say which of the
# documented hostnames exists, answers, and serves us -- five stages
# kept apart, so a DNS miss on one name is never reported as the venue
# being down. See ops.GRPC_UNAVAILABLE_MEANING.

def _dns_and_tls(host: str, port: int, out: dict) -> None:
    import socket
    import ssl

    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        out["stages"][ops.G_DNS] = True
        # the count only -- an address is infrastructure detail we have
        # no need to write down
        out["dnsAnswers"] = len(infos)
    except Exception as exc:                                   # noqa: BLE001
        out["stages"][ops.G_DNS] = False
        out["dnsError"] = type(exc).__name__
        return
    finally:
        out["dnsMs"] = round((time.time() - t0) * 1000, 1)

    t1 = time.time()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10.0) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                out["stages"][ops.G_TCP] = True
                out["tlsVersion"] = tls.version()
    except Exception as exc:                                   # noqa: BLE001
        out["stages"][ops.G_TCP] = False
        out["tlsError"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    finally:
        out["tlsMs"] = round((time.time() - t1) * 1000, 1)


def _channel_and_rpc(grpc, target: str, token: str, seconds: float,
                     out: dict) -> None:
    t0 = time.time()
    channel = grpc.secure_channel(target, grpc.ssl_channel_credentials())
    try:
        try:
            grpc.channel_ready_future(channel).result(timeout=10.0)
            out["stages"][ops.G_READY] = True
        except Exception as exc:                               # noqa: BLE001
            out["stages"][ops.G_READY] = False
            out["channelError"] = type(exc).__name__
            return
        finally:
            out["channelMs"] = round((time.time() - t0) * 1000, 1)

        try:
            from polymarket.v1 import trading_pb2, trading_pb2_grpc
        except ImportError as exc:
            out["rpcSkipped"] = "GENERATED_CLIENT_MISSING: %s" % exc
            return

        t1 = time.time()
        stub = trading_pb2_grpc.OrderEntryAPIStub(channel)
        req = ops.build_subscription_request(trading_pb2, snapshot_only=True)
        meta = [("authorization", "Bearer %s" % token)]
        try:
            for response in stub.CreateOrderSubscription(req, metadata=meta):
                # one message is all the evidence this stage needs
                out["stages"][ops.G_RPC] = True
                out["stages"][ops.G_PERM] = True
                out["permissionResult"] = "SERVED"
                seen: dict = {}
                ops.observe_message(response, seen)
                # the SHAPE that arrived, never its contents
                out["firstMessage"] = sorted(
                    k for k in seen
                    if k in ("heartbeat", "snapshot", "update"))
                break
            else:
                out["stages"][ops.G_RPC] = True
                out["stages"][ops.G_PERM] = True
                out["permissionResult"] = "SERVED_EMPTY"
        except grpc.RpcError as exc:
            code = str(exc.code())
            out["rpcError"] = {"code": code,
                               "details": str(exc.details())[:200]}
            if "PERMISSION_DENIED" in code:
                # the service answered ABOUT US: reached and authenticated
                out["stages"][ops.G_RPC] = True
                out["stages"][ops.G_PERM] = True
                out["permissionResult"] = "PERMISSION_DENIED"
            elif "UNAUTHENTICATED" in code:
                out["stages"][ops.G_RPC] = False
                out["stages"][ops.G_PERM] = True
                out["permissionResult"] = "UNAUTHENTICATED"
            else:
                # UNAVAILABLE / UNIMPLEMENTED / DEADLINE_EXCEEDED: the
                # service never judged us. NOT a permission result, and
                # NOT evidence about the service -- only about this name.
                out["stages"][ops.G_RPC] = False
                out["permissionResult"] = "NOT_REACHED"
        finally:
            out["rpcMs"] = round((time.time() - t1) * 1000, 1)
    finally:
        channel.close()


def op_grpc_probe(args) -> int:
    import grpc
    import requests

    spec = json.loads(args.spec) if args.spec.strip() else {}
    asked = spec.get("targets") or list(ops.GRPC_CANDIDATES)
    targets = [ops.assert_preprod(t) for t in asked]

    # The token is minted ONCE and shared, so a difference between hosts
    # is a difference between hosts and not a difference between tokens.
    s = requests.Session()
    tok_status, token, _, why = mint_token(s)
    if tok_status != 200 or not token:
        out = ops.receipt("grpc-probe", authenticated=False,
                          blockers=["token %s %s" % (tok_status, why)],
                          meaning="no token: stages 4 and 5 cannot be "
                                  "attempted for ANY host, and stages 1-3 "
                                  "would say nothing about entitlement")
        print(json.dumps(out, indent=1))
        if args.out:
            ops.write_receipt(args.out, out)
        return 1

    results = []
    for target in targets:
        host, _, port = target.rpartition(":")
        one = {"target": target, "requestId": _request_id(),
               "at": time.time(),
               "stages": {stage: None for stage in ops.GRPC_STAGES}}
        _dns_and_tls(host, int(port or 443), one)
        if one["stages"][ops.G_TCP]:
            _channel_and_rpc(grpc, target, token,
                             min(args.seconds, 20.0), one)
        results.append(one)

    out = ops.receipt("grpc-probe", authenticated=True,
                      candidates=results, **ops.grpc_probe_verdict(results))
    print(json.dumps(out, indent=1))
    if args.out:
        ops.write_receipt(args.out, out)
    # rc 1 is a NAMED verdict, not a crash: no candidate served us.
    return 0 if out["authenticatedRpc"] else 1


def run_op(args) -> int:
    return {"auth": op_auth, "reconcile": op_reconcile,
            "stream": op_stream, "grpc-probe": op_grpc_probe}[args.op](args)
