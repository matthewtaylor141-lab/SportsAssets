"""THE SOCKETS FOR THE PRODUCTION READ. No decisions, no order path.

Every verdict comes from `pmx_production_read`; this file only opens
connections and hands back what came off the wire. There is no insert,
cancel, replace, preview, funding or stream call here, and every URL it
can build comes from that module's allow-list.
"""
from __future__ import annotations

import base64
import json
import os
import time

import pmx_production_read as prod

TIMEOUT = (10.0, 30.0)


def _private_key_pem() -> str:
    """A PEM as-is, or base64 of one. Anything else raises KeyNotUsable,
    which is NOT a transport failure -- see prod.KeyNotUsable."""
    raw = os.environ["PMX_PRIVATE_KEY_B64"]
    if "-----BEGIN" in raw[:64]:
        return raw                     # a PEM pasted as-is
    try:
        pem = base64.b64decode("".join(raw.split()), validate=True).decode()
    except Exception as exc:                                   # noqa: BLE001
        raise prod.KeyNotUsable(
            "the key is set but is neither a PEM nor base64 of one "
            "(%s)" % type(exc).__name__) from None
    if "BEGIN" not in pem:
        raise prod.KeyNotUsable(
            "the key decoded to %d bytes with no PEM header" % len(pem))
    return pem


def mint_token(session) -> tuple:
    """(status, token, expires_in, error_word).

    TWO AUDIENCES, from two constants: the `aud` CLAIM is Auth0's token
    endpoint, the `audience` FIELD is the REST base. Form-encoded, which
    is the encoding retained venue evidence shows is accepted.
    """
    import jwt

    cid = os.environ["PMX_CLIENT_ID"]
    kid = os.environ["PMX_KEY_ID"]
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": cid, "sub": cid, "aud": prod.CLIENT_ASSERTION_AUD,
         "iat": now, "exp": now + 60, "jti": prod.request_id()},
        _private_key_pem(), algorithm="RS256", headers={"kid": kid})
    url = prod.assert_production("https://%s/oauth/token" % prod.AUTH0_DOMAIN)
    r = session.post(url, data={
        "client_id": cid,
        "client_assertion_type":
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "audience": prod.TOKEN_REQUEST_AUDIENCE,
        "grant_type": "client_credentials"},
        headers={"Content-Type": prod.TOKEN_REQUEST_ENCODING},
        timeout=TIMEOUT)
    body = {}
    try:
        body = r.json()
    except Exception:                                          # noqa: BLE001
        pass
    tok = body.get("access_token") if isinstance(body, dict) else None
    why = (body.get("error") or "") if isinstance(body, dict) else ""
    return r.status_code, tok, (body or {}).get("expires_in"), str(why)[:80]


def _headers(token: str, rid: str) -> dict:
    return {"Authorization": "Bearer %s" % token,
            "X-Client-Id": os.environ["PMX_CLIENT_ID"],
            "x-participant-id": os.environ["PMX_PARTICIPANT_ID"],
            "X-Request-Id": rid,
            "Content-Type": "application/json"}


def run_verify(args) -> int:
    import requests

    presence = prod.credential_presence()
    if presence["verdict"]:
        out = prod.receipt("verify", verdict=presence["verdict"], **presence)
        print(json.dumps(out, indent=1))
        if args.out:
            prod.write_receipt(args.out, out)
        return 1

    names = [n.strip() for n in (args.reads or "").split(",") if n.strip()]
    symbol = (getattr(args, "symbol", "") or "").strip()
    if not names:
        # the default sweep skips the symbol reads, because a symbol is
        # not guessable and an empty one is not a question worth asking
        names = [n for n in prod.READS if n not in prod.SYMBOL_READS]
    account = (os.environ.get(prod.ACCOUNT_ENV) or "").strip()
    # refuse the whole run before opening anything, if one name is not a
    # read or a symbol read has no symbol
    for name in names:
        prod.request_for(name, account, symbol)
    session = requests.Session()

    t0 = time.time()
    try:
        tok_status, token, expires_in, why = mint_token(session)
    except prod.KeyNotUsable as exc:
        # NOT the venue. No socket was opened. Run 1 reported this as
        # VENUE_UNREACHABLE and that was wrong.
        out = prod.receipt("verify",
                           verdict=prod.A_KEY_NOT_USABLE,
                           why=str(exc),
                           keyShape=prod.key_shape(
                               os.environ.get("PMX_PRIVATE_KEY_B64", "")),
                           venueContacted=False)
        print(json.dumps(out, indent=1))
        if args.out:
            prod.write_receipt(args.out, out)
        return 1
    except Exception as exc:                                   # noqa: BLE001
        out = prod.receipt("verify",
                           verdict=prod.A_UNREACHABLE,
                           transportError=type(exc).__name__,
                           venueContacted=True)
        print(json.dumps(out, indent=1))
        if args.out:
            prod.write_receipt(args.out, out)
        return 1
    auth_ms = round((time.time() - t0) * 1000, 1)

    if tok_status != 200 or not token:
        out = prod.receipt("verify",
                           verdict=prod.classify_auth(tok_status, None),
                           tokenStatus=tok_status, error=why,
                           authMs=auth_ms)
        print(json.dumps(out, indent=1))
        if args.out:
            prod.write_receipt(args.out, out)
        return 1

    scopes = prod.scopes_of(token)
    results, first_probe = [], None
    for name in names:
        method, url, body = prod.request_for(name, account, symbol)
        rid = prod.request_id()
        t1 = time.time()
        try:
            params = None
            if name == "positions" and account:
                params = {"name": account}
            r = session.request(method, url, headers=_headers(token, rid),
                                params=params,
                                json=body if method == "POST" else None,
                                timeout=TIMEOUT)
            try:
                parsed = r.json()
            except Exception:                                  # noqa: BLE001
                parsed = None
            row = prod.summarize(name, r.status_code, parsed)
        except Exception as exc:                               # noqa: BLE001
            row = {"read": name, "status": None,
                   "transportError": type(exc).__name__}
        row["requestId"] = rid
        row["ms"] = round((time.time() - t1) * 1000, 1)
        if first_probe is None:
            first_probe = row.get("status")
        results.append(row)

    denied = [r["read"] for r in results if r.get("status") in (401, 403)]
    out = prod.receipt(
        "verify",
        verdict=prod.classify_auth(tok_status, first_probe),
        authMs=auth_ms,
        tokenLifetimeSeconds=expires_in,
        scopes=scopes,
        scopeCount=len(scopes),
        accountSupplied=bool(account),
        symbol=symbol or None,
        reads=results,
        permissionDenied=denied,
        readsAttempted=len(results),
        readsOk=len([r for r in results if r.get("status") == 200]))
    print(json.dumps(out, indent=1))
    if args.out:
        prod.write_receipt(args.out, out)
    # rc 1 is a NAMED verdict, not a crash
    return 0 if out["verdict"] == prod.A_OK else 1
