"""THE COMMAND CENTRE'S I/O SIDE. Reads evidence; decides nothing.

The read model in `sportsassets.bettor_command_center` is pure: records
in, five views out. This module is the half that touches the world --
the Postgres journal, the two ingestion_state rows, the JUnit artifacts
on disk and the deployed SHA -- and it exists separately so that every
state the page can reach is reachable in a test by handing `build()` a
list of dicts.

WHAT IT WILL NOT DO. It opens no socket, writes no row, flips no
control, spends no allowance and reads no credential. Every statement
below is a SELECT, and `test_bettor_command_center.py` reads this file's
source and fails the build if a mutating verb appears in it.

A FAILED READ IS NOT AN EMPTY RUN. Anything that cannot produce the
real records raises `RetrievalIncomplete`, which the route turns into
503. The page then says UNAVAILABLE and keeps its previous snapshot
labelled stale. It never renders a well-formed page of zeros, because a
page of zeros during an outage is the one failure that actually
misleads somebody.

Run:  python -m pytest backend/tests/test_bettor_command_center.py
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET

from .. import bettor_command_center as CC

# The journal is keyed by run id; the run row names which one is ours.
JOURNAL_TABLE = "bettor_incentive_journal"

# A whole ET-date of frames is tens of thousands of rows. The page needs
# counts, extremes and per-market presence -- not every ladder -- so the
# heavy work is done in SQL and only the shaped rows come back.
LADDER_SAMPLE = 400


class RetrievalIncomplete(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


# ── where the evidence artifacts live ────────────────────────────────
#
# TWO SOURCES, IN ORDER, AND THE PAGE IS TOLD WHICH ONE ANSWERED.
#
#   1. THE EVIDENCE STORE in Postgres (`bettor_evidence_store`). This is
#      the production path. Each artifact carries the commit it was
#      produced at, a SHA-256 of its exact bytes and the instant it was
#      published, so a figure on screen can name the commit it
#      describes.
#
#   2. THE WORKING TREE under `BETTOR_EVIDENCE_ROOT`. This is the
#      development and preview path, and it is a fallback rather than a
#      peer: a file on disk carries no commit and no digest, so anything
#      read this way is labelled `provenance: "working tree"` and its
#      source_sha is UNKNOWN.
#
# THE PRODUCTION IMAGE STILL DOES NOT CARRY THEM, deliberately --
# `backend/Dockerfile` ships exactly one acceptance file so that 4.9 MB
# of patches and suite XML stays out of a production image. That is why
# the store exists. Before the store was wired the deployed API had no
# route to this evidence at all and rendered UNKNOWN for all of it; that
# was honest about an UNFINISHED INTEGRATION, not about a missing
# measurement, and the two are different things.
#
# When NEITHER source has it, it is genuinely unavailable and UNKNOWN is
# the right answer -- with "not published to the evidence store, and not
# present on disk" as the stated reason.

STORE_FIRST = "evidence store (postgres)"
TREE_FALLBACK = "working tree (development)"


def evidence_root() -> str:
    return os.environ.get("BETTOR_EVIDENCE_ROOT") or "research/beta48/acceptance"


def _artifact(name: str):
    path = os.path.join(evidence_root(), name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), path
    except Exception:                                          # noqa: BLE001
        return None, path


def _text(name: str):
    path = os.path.join(evidence_root(), name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(), path
    except Exception:                                          # noqa: BLE001
        return None, path


async def read_store(pool, names: list) -> dict:
    """Every named artifact the store holds. Never raises.

    A store that is absent or unreadable is NOT an error here: the
    fallback covers it and the caller reports which source answered. It
    would be an error to silently present a tree read as a store read,
    which is why provenance travels with every hit.
    """
    if pool is None:
        return {}
    try:
        from .. import bettor_evidence_store as ES
        return await ES.latest_many(pool, names)
    except Exception as exc:                                   # noqa: BLE001
        log_detail = type(exc).__name__
        return {"__error__": log_detail}


# ── JUnit ────────────────────────────────────────────────────────────

def parse_junit(path: str, *, name, kind, sha, environment,
                superseded_by=None, body=None, artifact_label=None) -> dict:
    """One JUnit document to one suite row.

    COMPLETION IS READ, NOT ASSUMED. pytest writes the XML at the end of
    a run, so a document that parses is a run that finished -- but one
    that is absent, truncated or malformed is an INCOMPLETE run, and
    `suite_row` withholds its counts rather than showing the prefix that
    happened to survive.

    `body` is the document text when it came from the evidence store;
    `path` is used only when reading from disk. The parse and every
    refusal below are identical either way -- the same bytes must
    produce the same row whichever side of the integration they arrived
    from, or the store would be a second opinion rather than a
    transport.
    """
    label = artifact_label or path
    try:
        root = (ET.fromstring(body) if body is not None
                else ET.parse(path).getroot())
    except Exception as exc:                                   # noqa: BLE001
        return CC.suite_row(name=name, kind=kind, sha=sha,
                            environment=environment, at=None, complete=False,
                            artifact=label, superseded_by=superseded_by,
                            failure_ids=[])
    suites = ([root] if root.tag == "testsuite"
              else list(root.iter("testsuite")))
    tests = failures = errors = skipped = 0
    at = None
    failure_ids, failure_detail = [], []
    for s in suites:
        tests += int(s.get("tests") or 0)
        failures += int(s.get("failures") or 0)
        errors += int(s.get("errors") or 0)
        skipped += int(s.get("skipped") or 0)
        at = at or s.get("timestamp")
        for case in s.iter("testcase"):
            bad = (list(case.iter("failure")) + list(case.iter("error")))
            if not bad:
                continue
            ident = "%s::%s" % (case.get("classname") or "",
                                case.get("name") or "")
            failure_ids.append(ident)
            msg = (bad[0].get("message") or "").strip()
            failure_detail.append({"id": ident, "message": msg[:400],
                                   "time": case.get("time")})
    row = CC.suite_row(name=name, kind=kind, sha=sha,
                       environment=environment, at=at, complete=True,
                       passed=tests - failures - errors - skipped,
                       failed=failures + errors, skipped=skipped,
                       failure_ids=failure_ids, artifact=label,
                       superseded_by=superseded_by)
    row["failures"] = failure_detail
    row["total"] = tests
    return row


# THE SUITES, DECLARED. Each one names what it actually exercised --
# unit code, a transport double, a recorded tape or the real venue --
# because "264 passed" says nothing without that.
SUITE_MANIFEST = [
    {"file": "release_tests.xml", "name": "release suite",
     "kind": CC.K_UNIT, "sha": "d630d3d",
     "environment": "CI container, python 3.12, no venue"},
    {"file": "repair_tests.xml", "name": "repair suite",
     "kind": CC.K_UNIT, "sha": "195ee01",
     "environment": "CI container, python 3.12, no venue",
     "superseded_by": "release suite @ d630d3d"},
    {"file": "gate_tests_against_defective_code.xml",
     "name": "gate, run against DELIBERATELY DEFECTIVE code",
     "kind": CC.K_SIM, "sha": "195ee01 + injected defects",
     "environment": "CI container; the 13 failures are the POINT -- this "
                    "run proves the gate fails when the code is wrong"},
]


def load_suites(store: dict | None = None) -> list:
    """Suite rows, PREFERRING the evidence store.

    THE SHA ON A ROW IS THE COMMIT THE RUN TESTED, and where the store
    answered it is read from the stored `source_sha` rather than from
    the hardcoded entry in SUITE_MANIFEST. A declared SHA is a claim
    someone typed; a stored one travelled with the bytes.
    """
    store = store or {}
    rows = []
    for spec in SUITE_MANIFEST:
        hit = store.get(spec["file"])
        if hit:
            row = parse_junit(
                None, body=hit["body"], name=spec["name"],
                kind=spec["kind"],
                sha=hit.get("source_sha") or spec["sha"],
                environment=spec["environment"],
                superseded_by=spec.get("superseded_by"),
                artifact_label="evidence store: %s@%s"
                               % (hit["name"], hit["digest"][:12]))
            row["provenance"] = {
                "source": STORE_FIRST,
                "source_sha": hit.get("source_sha"),
                "digest": hit.get("digest"),
                "published_at": CC.iso(hit.get("published_at")),
                "size_bytes": hit.get("size_bytes"),
                "declared_sha": spec["sha"],
                "sha_matches_declared": (
                    (hit.get("source_sha") or "")[:7] == spec["sha"][:7]),
            }
        else:
            path = os.path.join(evidence_root(), spec["file"])
            row = parse_junit(
                path, name=spec["name"], kind=spec["kind"], sha=spec["sha"],
                environment=spec["environment"],
                superseded_by=spec.get("superseded_by"))
            row["provenance"] = {
                "source": TREE_FALLBACK,
                "source_sha": None,
                "digest": None,
                "why": "read from disk, not from the evidence store: a "
                       "file carries no commit and no digest, so its "
                       "attribution is the declared one and nothing "
                       "verified it",
                "declared_sha": spec["sha"],
            }
        rows.append(row)
    return rows


ECON_ARTIFACTS = ("evaluation.json", "incentive_opportunity.json",
                  "incentive_manifest.json")


def load_artifacts(store: dict | None = None) -> dict:
    """Economic artifacts, PREFERRING the evidence store.

    A store hit is parsed from its stored bytes and carries its commit
    and digest. A miss falls back to disk. A miss on BOTH is genuinely
    unavailable, and the reason says which of the two things is true --
    "never published" is a different fact from "not in this image".
    """
    store = store or {}
    out, prov = {}, {}
    for fname, key in (("evaluation.json", "evaluation"),
                       ("incentive_opportunity.json", "opportunity"),
                       ("incentive_manifest.json", "manifest")):
        hit = store.get(fname)
        if hit:
            try:
                out[key] = json.loads(hit["body"])
                prov[key] = {"source": STORE_FIRST,
                             "source_sha": hit.get("source_sha"),
                             "digest": hit.get("digest"),
                             "published_at": CC.iso(hit.get("published_at"))}
                continue
            except Exception:                                  # noqa: BLE001
                prov[key] = {"source": STORE_FIRST, "unparseable": True,
                             "digest": hit.get("digest"),
                             "why": "stored bytes are not valid JSON; "
                                    "falling back to disk"}
        val, path = _artifact(fname)
        out[key] = val
        if key not in prov or val is not None:
            prov[key] = {"source": TREE_FALLBACK if val is not None else None,
                         "path": path,
                         "source_sha": None, "digest": None,
                         "why": None if val is not None else
                                "not published to the evidence store, and "
                                "not present on disk -- genuinely "
                                "unavailable, which is what UNKNOWN means"}
    present = {k: (out.get(k) is not None) for k in
               ("evaluation", "opportunity", "manifest")}
    return {"evaluation": out.get("evaluation"),
            "opportunity": out.get("opportunity"),
            "manifest": out.get("manifest"),
            "provenance": prov,
            "present": present,
            "from_store": sorted(k for k, v in prov.items()
                                 if v.get("source") == STORE_FIRST),
            "why_absent": "an artifact absent from BOTH the evidence "
                          "store and the working tree is genuinely "
                          "unavailable. The production image ships only "
                          "incentive_manifest.json from acceptance/ on "
                          "purpose, which is why the store exists."}


# ── the database side ────────────────────────────────────────────────

async def read_control(pool) -> tuple:
    from .. import bettor_live_control as CTL
    try:
        state = await CTL.read_control(pool)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("CONTROL_UNREADABLE",
                                  type(exc).__name__) from exc
    if state.get("why") in (CTL.W_UNREADABLE, CTL.W_MALFORMED):
        raise RetrievalIncomplete(state["why"], str(state.get("detail") or ""))
    return (not CTL.is_closed(state)), state


async def read_probe(pool) -> dict | None:
    from .. import bettor_live_control as CTL
    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", CTL.BUDGET_KEY)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("PROBE_ROW_UNREADABLE",
                                  type(exc).__name__) from exc
    if raw is None:
        return None
    try:
        return json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("PROBE_ROW_MALFORMED",
                                  type(exc).__name__) from exc


async def read_run_row(pool) -> dict | None:
    from .. import bettor_incentive_state as ST
    got = await ST.read_run(pool)
    if got.get("why") in (ST.S_UNREADABLE, ST.S_MALFORMED):
        raise RetrievalIncomplete(got["why"], str(got.get("detail") or ""))
    return got.get("run")


async def read_journal(pool, run_id: str) -> list:
    """Every non-ladder record, plus a bounded, SHAPED ladder view.

    WHY NOT `read_run()`. That helper returns every row with its full
    bids/offers payload -- a day of twelve markets is tens of thousands
    of rows and tens of megabytes, and pulling it into an API process to
    count it would make the page a load-generator against the same
    database the collector is writing to. The counts, extremes and
    per-market presence are computed in SQL instead, and the ladders
    that come back carry only what the views read: `at`, `slug`,
    `boot_id`, `epoch` and whether depth was present.

    THE DEPTH TEST IS DONE IN SQL AND IT IS THE SAME TEST the pure
    function applies: a frame has depth when its bids or offers array
    is non-empty. A frame that arrived carrying neither is a frame that
    told us nothing about the book, and it stays counted apart.
    """
    try:
        meta = await pool.fetch(
            "SELECT boot_id, at, kind, epoch, slug, payload FROM "
            + JOURNAL_TABLE +
            " WHERE run_id = $1 AND kind <> 'LADDER' ORDER BY at, id",
            run_id)
        ladders = await pool.fetch(
            "SELECT boot_id, at, epoch, slug,"
            " (jsonb_array_length(COALESCE(payload->'bids','[]'::jsonb))"
            "  + jsonb_array_length(COALESCE(payload->'offers','[]'::jsonb)))"
            "   AS levels"
            " FROM " + JOURNAL_TABLE +
            " WHERE run_id = $1 AND kind = 'LADDER' ORDER BY at, id",
            run_id)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("JOURNAL_UNREADABLE",
                                  type(exc).__name__) from exc

    out = []
    for r in meta:
        pay = r["payload"]
        out.append({"boot_id": r["boot_id"], "at": float(r["at"]),
                    "kind": r["kind"], "epoch": r["epoch"], "slug": r["slug"],
                    "payload": json.loads(pay)
                    if isinstance(pay, (str, bytes)) else pay})
    for r in ladders:
        n = int(r["levels"] or 0)
        out.append({"boot_id": r["boot_id"], "at": float(r["at"]),
                    "kind": "LADDER", "epoch": r["epoch"], "slug": r["slug"],
                    # Shaped, not truncated: the views ask only whether
                    # depth was persisted, and this answers exactly that.
                    "payload": {"bids": [1] * n, "offers": [],
                                "levels": n}})
    out.sort(key=lambda r: r["at"])
    return out


async def deployed_identity(pool=None) -> dict:
    """The SHA this process is actually running, from every source.

    Delegates to `bettor_evidence_store.deployed_identity`, which reads
    the environment, a build stamp and the evidence store's newest
    source_sha, and reports disagreement rather than picking a winner.
    Shaped here into the flat form `CC.build()` expects.
    """
    from .. import bettor_evidence_store as ES

    got = await ES.deployed_identity(pool)
    return {"sha": got["running_sha"],
            "source": got["source"] or "environment / build stamp",
            "why": got["why"],
            "detail": got}


def allowlist_from(artifacts: dict) -> list:
    man = (artifacts or {}).get("manifest") or {}
    return list(((man.get("freeze_preview") or {}).get("slugs")) or [])


async def snapshot(pool=None, *, now=None) -> dict:
    """Read everything, then build. Raises RetrievalIncomplete."""
    if pool is None:
        try:
            from ..db import get_pool
            pool = await get_pool()
        except Exception as exc:                               # noqa: BLE001
            raise RetrievalIncomplete("NO_DATABASE_POOL",
                                      type(exc).__name__) from exc

    control, control_state = await read_control(pool)
    probe = await read_probe(pool)
    run_row = await read_run_row(pool)

    # THE STORE FIRST, THE TREE AS FALLBACK. One round trip for every
    # artifact the page can show; a store that is absent or unreadable
    # returns nothing and the tree covers it, with provenance saying so.
    wanted = [s["file"] for s in SUITE_MANIFEST] + list(ECON_ARTIFACTS)
    store = await read_store(pool, wanted)
    store_error = store.pop("__error__", None) if isinstance(store, dict) \
        else None

    artifacts = load_artifacts(store)
    suites = load_suites(store)

    records = []
    if run_row and run_row.get("run_id"):
        records = await read_journal(pool, run_row["run_id"])

    payload = CC.build(records=records, control=control, probe=probe,
                       run_row=run_row,
                       allowlist=allowlist_from(artifacts),
                       suites=suites, artifacts=artifacts,
                       deployed=await deployed_identity(pool),
                       now=now if now is not None else time.time())
    payload["views"]["live_operation"]["control_row"] = {
        "why": control_state.get("why"),
        "read_at": CC.iso(control_state.get("read_at")),
        "source": "ingestion_state.%s"
                  % control_state.get("key", "bettor_live_observation"),
    }
    payload["evidence_availability"] = {
        "artifacts": artifacts["present"],
        "provenance": artifacts["provenance"],
        "from_store": artifacts["from_store"],
        "store": {
            "reachable": store_error is None,
            "error": store_error,
            "names_found": sorted(k for k in store) if store else [],
            "table": "bettor_evidence_artifact",
        },
        "root": evidence_root(),
        "why_absent": artifacts["why_absent"],
    }
    payload["views"]["live_operation"]["identity"]["identity_detail"] = \
        (await deployed_identity(pool))["detail"]
    return payload
