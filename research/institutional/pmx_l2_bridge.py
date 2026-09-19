"""THE L2 EVIDENCE BRIDGE. Market data crosses; the credential never does.

Owner directive 2026-09-19 23:0xZ: "I no longer possess the production
PMX private key. It exists only as a GitHub repository secret ... Use
the existing authenticated pmx-production GitHub lane as the temporary
institutional L2 credential holder. Build the narrowest possible
read-only evidence bridge."

WHAT CROSSES AND WHAT DOES NOT. This job authenticates inside the
GitHub runner, reads instruments / BBO / L2 orderbook, and emits SQL
that INSERTS BOOKS into the experimental evidence tables. The token,
the assertion and the key never leave the runner, are never printed,
and are never written into the SQL it emits -- `_refuse_secrets` scans
the generated statements and aborts rather than let one through.

WHAT THIS CANNOT BECOME, by construction rather than by promise:

  * It reuses `pmx_production_read.request_for`, whose allow-list has
    no insert, cancel, replace, preview or funding path. A name absent
    from that mapping raises before a socket opens.
  * The only tables it writes are bettor_l2_evidence (append-only, and
    CHECKed to OBSERVED_PRODUCTION) and the STATUS column of
    bettor_l2_requests. `emit()` refuses to produce a statement
    touching anything else, and the workflow greps the output before
    psql sees it.
  * There is no order, position or funding table on the bridge at all.
    A trading capability would need a new table, a new module and a
    new review. That is the point of the shape.

LATENCY IS MEASURED, NEVER MANUFACTURED. Two figures travel on every
row: `venueRequestMs`, the L2 call itself, and `bridgeLatencyMs`, the
whole round trip from BETTOR's request to the evidence landing. A CI
runner reached minutes after a request is a DIFFERENT EXECUTION
ENVIRONMENT from a persistent worker reached in milliseconds, so every
row is stamped GITHUB_BRIDGE and the schema will not let the two
regimes be pooled.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import pmx_production_read as prod
import pmx_production_net as net

LATENCY_REGIME = "GITHUB_BRIDGE"
EVIDENCE_CLASS = "OBSERVED_PRODUCTION"

# The only tables this bridge may name. Checked against every emitted
# statement: a generated INSERT that reached another table would be a
# capability nobody reviewed.
ALLOWED_TABLES = ("bettor_l2_evidence", "bettor_l2_requests")

# The reads it may perform, a strict subset of the production lane's
# own allow-list.
BRIDGE_READS = ("instruments", "bbo", "book")


class BridgeRefusal(RuntimeError):
    pass


def _now():
    return datetime.now(tz=timezone.utc)


def _q(value) -> str:
    """One SQL literal. NULL, a number, or a single-quoted string with
    quotes doubled. No interpolation of anything unescaped."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"), sort_keys=True,
                           default=str)
    text = str(value).replace("'", "''")
    return "'" + text + "'"


def book_sha(bids, offers) -> str:
    import hashlib
    raw = json.dumps({"bids": bids or [], "offers": offers or []},
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def evidence_id(instrument, received_at, sha) -> str:
    import hashlib
    raw = "|".join([str(instrument), received_at.isoformat(), str(sha)])
    return "l2ev_" + hashlib.sha256(raw.encode()).hexdigest()[:36]


def _scales(instrument_record) -> tuple:
    """(priceScale, fractionalQtyScale) as INTEGERS, or (None, None).

    NEVER a default. The whole reason the instrument is fetched beside
    the book is that the scale is per instrument and assuming one
    misprices every row that does not use it.
    """
    rec = instrument_record if isinstance(instrument_record, dict) else {}
    try:
        ps = int(str(rec.get("priceScale")).strip())
        qs = int(str(rec.get("fractionalQtyScale")).strip())
    except (TypeError, ValueError, AttributeError):
        return None, None
    return (ps, qs) if ps > 0 and qs > 0 else (None, None)


def emit(statements) -> str:
    """Join the statements, having refused anything out of scope."""
    text = "\n".join(statements)
    lowered = text.lower()
    for verb in ("drop ", "alter ", "truncate ", "grant ", "revoke ",
                 "create ", "delete ", "copy "):
        if verb in lowered:
            raise BridgeRefusal(
                "refused: the bridge emitted %r, which is outside its "
                "write scope" % verb.strip())
    # every statement must name one of the two allowed tables
    for line in statements:
        stripped = line.strip().lower()
        if not stripped or stripped.startswith("--"):
            continue
        if not any(t in stripped for t in ALLOWED_TABLES):
            raise BridgeRefusal(
                "refused: a statement naming no allowed table: %.80s" % line)
    prod._refuse_secrets(text)
    return text


def fetch(symbol: str, session, token, *, requested_at=None) -> dict:
    """instruments + bbo + book for one symbol, with the real timings."""
    out = {"symbol": symbol}
    reads = {}
    for name in BRIDGE_READS:
        method, url, body = prod.request_for(name, "", symbol)
        rid = prod.request_id()
        t1 = time.time()
        try:
            r = session.request(method, url, headers=net._headers(token, rid),
                                json=body if method == "POST" else None,
                                timeout=net.TIMEOUT)
            try:
                parsed = r.json()
            except Exception:                                  # noqa: BLE001
                parsed = None
            reads[name] = {"status": r.status_code, "body": parsed,
                           "requestId": rid,
                           "ms": round((time.time() - t1) * 1000, 1)}
        except Exception as exc:                               # noqa: BLE001
            reads[name] = {"status": None,
                           "transportError": type(exc).__name__,
                           "requestId": rid,
                           "ms": round((time.time() - t1) * 1000, 1)}

    received = _now()
    inst_body = (reads.get("instruments") or {}).get("body") or {}
    rows = inst_body.get("instruments") or []
    record = rows[0] if rows and isinstance(rows[0], dict) else None
    ps, qs = _scales(record)

    book_body = (reads.get("book") or {}).get("body") or {}
    bids = book_body.get("bids") or []
    offers = book_body.get("offers") or []

    out.update({
        "instrumentRecord": record,
        "priceScale": ps,
        "quantityScale": qs,
        "bids": bids,
        "offers": offers,
        "bbo": (reads.get("bbo") or {}).get("body"),
        "venueState": book_body.get("state"),
        "sourceTimestamp": book_body.get("transactTime"),
        "receivedTimestamp": received,
        "venueRequestId": (reads.get("book") or {}).get("requestId"),
        "venueRequestMs": (reads.get("book") or {}).get("ms"),
        "bookSha": book_sha(bids, offers),
        "statuses": {k: v.get("status") for k, v in reads.items()},
        "bridgeLatencyMs": (
            None if requested_at is None
            else round((received - requested_at).total_seconds() * 1000, 1)),
    })
    return out


def statements_for(fetched: dict, *, request_row=None, run_id="",
                   revision="") -> list:
    """The INSERT for one observation, plus the request's status."""
    received = fetched["receivedTimestamp"]
    eid = evidence_id(fetched["symbol"], received, fetched["bookSha"])
    rid = (request_row or {}).get("l2_request_id")
    cols = ("l2_evidence_id", "l2_request_id", "request_id", "instrument_id",
            "identity_binding_sha", "source_timestamp", "received_timestamp",
            "l2_book_sha", "bids", "offers", "price_scale", "quantity_scale",
            "evidence_class", "venue_request_ms", "bridge_latency_ms",
            "latency_regime", "venue_state", "bbo", "instrument_record",
            "bridge_run_id", "bridge_revision")
    values = (eid, rid, fetched.get("venueRequestId"), fetched["symbol"],
              (request_row or {}).get("identity_binding_sha"),
              fetched.get("sourceTimestamp"), received.isoformat(),
              fetched["bookSha"], fetched["bids"], fetched["offers"],
              fetched.get("priceScale"), fetched.get("quantityScale"),
              EVIDENCE_CLASS, fetched.get("venueRequestMs"),
              fetched.get("bridgeLatencyMs"), LATENCY_REGIME,
              fetched.get("venueState"), fetched.get("bbo"),
              fetched.get("instrumentRecord"), run_id, revision)

    stmts = ["INSERT INTO bettor_l2_evidence (%s)\nVALUES (%s)\n"
             "ON CONFLICT (l2_evidence_id) DO NOTHING;"
             % (", ".join(cols), ", ".join(_q(v) for v in values))]
    if rid:
        served = "SERVED" if fetched["statuses"].get("book") == 200 \
            else "FAILED"
        stmts.append(
            "UPDATE bettor_l2_requests SET status = %s, claimed_at = now() "
            "WHERE l2_request_id = %s AND status = 'PENDING';"
            % (_q(served), _q(rid)))
    return stmts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--requests", default="",
                    help="JSON list of pending request rows")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    ap.add_argument("--revision", default=os.environ.get("GITHUB_SHA", ""))
    args = ap.parse_args(argv)

    import requests as rq

    presence = prod.credential_presence()
    if presence["verdict"]:
        print(json.dumps({"verdict": presence["verdict"]}, indent=1))
        return 1

    pending = []
    if args.requests:
        try:
            pending = json.loads(args.requests) or []
        except Exception:                                      # noqa: BLE001
            pending = []
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        pending.append({"symbol": sym, "l2_request_id": None,
                        "requested_at": None})
    if not pending:
        print(json.dumps({"verdict": "NOTHING_REQUESTED"}, indent=1))
        return 0

    session = rq.Session()
    try:
        tok_status, token, _exp, why = net.mint_token(session)
    except Exception as exc:                                   # noqa: BLE001
        print(json.dumps({"verdict": "VENUE_UNREACHABLE",
                          "transportError": type(exc).__name__}, indent=1))
        return 1
    if tok_status != 200 or not token:
        print(json.dumps({"verdict": prod.classify_auth(tok_status, None),
                          "tokenStatus": tok_status}, indent=1))
        return 1

    statements, summary = [], []
    for row in pending[:20]:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        requested_at = None
        raw = row.get("requested_at")
        if raw:
            try:
                requested_at = datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00"))
            except ValueError:
                requested_at = None
        fetched = fetch(symbol, session, token, requested_at=requested_at)
        statements.extend(statements_for(fetched, request_row=row,
                                         run_id=args.run_id,
                                         revision=args.revision))
        summary.append({
            "symbol": symbol,
            "statuses": fetched["statuses"],
            "bidLevels": len(fetched["bids"]),
            "offerLevels": len(fetched["offers"]),
            "priceScale": fetched.get("priceScale"),
            "quantityScale": fetched.get("quantityScale"),
            "bookSha": fetched["bookSha"],
            "venueRequestMs": fetched.get("venueRequestMs"),
            "bridgeLatencyMs": fetched.get("bridgeLatencyMs"),
            "sourceTimestamp": fetched.get("sourceTimestamp"),
        })

    sql = emit(statements)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(sql + "\n")
    print(json.dumps({"verdict": "AUTHENTICATED",
                      "evidenceClass": EVIDENCE_CLASS,
                      "latencyRegime": LATENCY_REGIME,
                      "observations": summary,
                      "statements": len(statements)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
