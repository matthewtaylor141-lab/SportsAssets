"""Test support: build a GENUINE label artifact for a given row.

A valid artifact attached to a fabricated row is not provenance for that row,
so the scoring gate now checks that each row's value, status, market identity,
decision and realised offset match the artifact it cites. Fixtures that
minted one artifact and stapled its SHA to a dozen unrelated rows encoded
exactly the defect the gate exists to catch.

`artifact_for` builds the capture series that WOULD produce the label the row
carries, seals it the canonical way, and hands back the artifact. The row is
then bound to its artifact because the artifact is genuinely about that row.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import bettor_dataset as BD

BASE_MID = 0.50
BASE_HALF_SPREAD = 0.01


def _stamp(sec):
    return "2026-09-17T%02d:%02d:%02dZ" % (sec // 3600, (sec % 3600) // 60,
                                           sec % 60)


def artifact_for(target, value, market="FIXTURE", decision="FIXTURE",
                 capture_spec_sha="fixture-capture-spec"):
    """A sealed artifact whose LABELS[target] is exactly `value`.

    `target` is a move label (MID_MOVE_30S, EXECUTABLE_MOVE_60S, ...). The
    series carries one row per declared horizon; the row at the target's
    horizon is placed so the label comes out at `value`, and the others sit
    at the base quote.
    """
    base, horizon = target.rsplit("_", 1)
    horizon = int(horizon.rstrip("S"))
    alias = None
    if base not in ("MID_MOVE", "EXECUTABLE_MOVE", "EXECUTABLE_BUY_MOVE",
                    "EXECUTABLE_SELL_MOVE"):
        # A study target that is not one of the canonical label families
        # (TARGET_CHANGE_30S and friends). Build the value on MID_MOVE and
        # carry it under the study's own name, chain record and all.
        alias, base = target, "MID_MOVE"
    rows = []
    for sec in (0,) + tuple(BD.HORIZONS_S):
        mid = BASE_MID
        bid = BASE_MID - BASE_HALF_SPREAD
        ask = BASE_MID + BASE_HALF_SPREAD
        if sec == horizon and value is not None:
            v = float(value)
            if base == "MID_MOVE":
                mid = BASE_MID + v
                bid, ask = mid - BASE_HALF_SPREAD, mid + BASE_HALF_SPREAD
            elif base in ("EXECUTABLE_MOVE", "EXECUTABLE_BUY_MOVE"):
                # value = forward BID - origin ASK
                bid = (BASE_MID + BASE_HALF_SPREAD) + v
                ask = bid + 2 * BASE_HALF_SPREAD
                mid = (bid + ask) / 2.0
            elif base == "EXECUTABLE_SELL_MOVE":
                # value = origin BID - forward ASK
                ask = (BASE_MID - BASE_HALF_SPREAD) - v
                bid = ask - 2 * BASE_HALF_SPREAD
                mid = (bid + ask) / 2.0
            else:
                raise ValueError("unsupported label family: %s" % base)
        rows.append({"MARKET_ID": market,
                     "DECISION_TIMESTAMP_UTC": _stamp(sec),
                     "MID": round(mid, 10),
                     "BEST_BID": round(bid, 10),
                     "BEST_ASK": round(ask, 10)})
    art = BD.label_artifact(dict(rows[0], DECISION_ID=decision), rows,
                            capture_spec_sha=capture_spec_sha)
    if alias is None:
        return art
    src = "MID_MOVE_%dS" % horizon
    art["LABELS"] = dict(art["LABELS"])
    art["LABELS"][alias] = art["LABELS"][src]
    art["TARGET_LABEL_STATUS"] = dict(art["TARGET_LABEL_STATUS"])
    art["TARGET_LABEL_STATUS"][alias] = art["TARGET_LABEL_STATUS"][src]
    art["OBSERVATION_CHAIN"] = dict(art["OBSERVATION_CHAIN"])
    art["OBSERVATION_CHAIN"][alias] = dict(art["OBSERVATION_CHAIN"][src])
    art["LABEL_ARTIFACT_SHA"] = BD.hashlib.sha256(
        BD.json.dumps({k: v for k, v in art.items()
                       if k != "LABEL_ARTIFACT_SHA"},
                      sort_keys=True, default=str).encode()).hexdigest()
    return art


def bind(rows, target, market="FIXTURE"):
    """Give every row its own genuine artifact for `target`.

    Returns the artifacts dict for `label_artifacts=`. Rows are mutated in
    place with LABEL_ARTIFACT_SHA and a per-row DECISION_ID, so twelve rows
    are twelve observations rather than twelve copies of one.
    """
    arts = {}
    for i, r in enumerate(rows or ()):
        if r.get("%s_STATUS" % target) != "PRESENT":
            continue
        art = artifact_for(target, r.get(target), market=market,
                           decision=r.get("DECISION_ID") or "FIXTURE_%d" % i)
        sha = art["LABEL_ARTIFACT_SHA"]
        arts[sha] = art
        r["LABEL_ARTIFACT_SHA"] = sha
        r.setdefault("DECISION_ID", art["DECISION_ID"])
        off_key = "%s_REALISED_OFFSET_S" % target
        if off_key in r:
            # The row's offset is the artifact's, because the row IS this
            # observation. A fixture that keeps its own number is asserting
            # a different observation and the gate is right to refuse it.
            chain = (art.get("OBSERVATION_CHAIN") or {}).get(target) or {}
            r[off_key] = chain.get("REALIZED_OFFSET_S")
    return arts
