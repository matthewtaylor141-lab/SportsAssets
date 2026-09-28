"""IS THE SERVICE SERVING THE BUILD WE RELEASED? An auditable verdict.

── WHY THIS IS A MODULE AND NOT FOUR LINES OF SHELL ─────────────────
Because the four lines of shell were wrong for an unknown number of
releases and nobody could tell. `command-verify`'s S3 step polled
`/api/health` -- an endpoint this service does not publish -- and compared
a SEVEN-character served commit against a FORTY-hex expected SHA with
`case "$SERVING" in "$OPERATOR_COMMIT"*)`, which asks whether the short
value begins with the long one. It cannot. So the step ran 60 iterations,
matched nothing, and fell through with no failure branch: ten minutes
spent, nothing proven, and a green tick.

Two properties that shell could not give us and this does:

  1 · IT FAILS CLOSED. `verdict()` returns CONFIRMED only when a
      well-formed identifier actually matched. Everything else -- a 404, a
      body with no commit field, a truncated value, a different build, an
      expired wait -- is a refusal with a NAMED reason.

  2 · THE COUNTEREXAMPLES ARE TESTABLE. A persistent 404, an unreadable
      health body, the wrong build and a timeout are four different
      failures that must not be confused, and each has a test. You cannot
      unit-test a `case` statement embedded in a 400-kilobyte workflow.

── THE COMPARISON RULE, STATED ──────────────────────────────────────
Git identifies a commit by a 40-hex SHA-1. A service may report a PREFIX
of it (`RENDER_GIT_COMMIT[:7]`). Those are not the same evidence and this
module never pretends they are:

    served is 40 hex   -> EXACT equality required. Strongest evidence.
    served is 7..39    -> the expected SHA must START WITH it, and the
                          verdict records PREFIX_MATCH. Weaker: a 7-char
                          prefix is ~268M possibilities, so a collision is
                          implausible but it is not proof of identity, and
                          calling it proof would be the same overclaim in
                          the other direction.
    served is < 7       -> REFUSED. Too short to identify anything, and
                          this is what stops `""` and `"?"` from
                          prefix-matching everything.

NEVER FABRICATE A FULL IDENTIFIER FROM A PREFIX. A previous instinct here
was to pad or to store the 7-char value as "the serving SHA". Both are
inventions: the prefix is evidence ABOUT the full id, not the id. The
verdict carries `served_raw` exactly as read and `matched_how` so a reader
always knows which of the two they have.

PREFER AN AUTHORITATIVE FULL SHA. Render's deploy list reports the full
commit of the live deploy. When that is available it is the primary
evidence and the health endpoint is a cross-check -- because the deploy
list says what the platform believes it launched, while the health
endpoint says what the process actually imported. Disagreement between
them is its own named failure and must never be averaged away.
"""

import re

#: A full git object name: exactly 40 lowercase hex characters.
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Anything that could be a prefix of one. Case is normalised before this
#: is applied, so an upper-case value from a platform is not rejected for
#: its case -- only for its content.
HEX_RE = re.compile(r"^[0-9a-f]+$")

#: THE SHORTEST PREFIX WE WILL ACCEPT AS EVIDENCE. Seven is git's own
#: conventional abbreviation and what `RENDER_GIT_COMMIT[:7]` produces.
#: Below it, the value identifies too little to mean anything -- and this
#: bound is precisely what stops an empty string or a jq `"?"` from
#: satisfying a prefix test against every SHA in existence.
MIN_PREFIX = 7

# ── THE NAMED OUTCOMES ──────────────────────────────────────────────
CONFIRMED = "CONFIRMED"
R_EXPECTED_NOT_A_FULL_SHA = "EXPECTED_COMMIT_IS_NOT_A_40_HEX_SHA"
R_NOT_READ = "SERVING_IDENTIFIER_NOT_READ"
R_NOT_HEX = "SERVING_IDENTIFIER_IS_NOT_HEX"
R_TOO_SHORT = "SERVING_IDENTIFIER_TOO_SHORT_TO_IDENTIFY_A_COMMIT"
R_TOO_LONG = "SERVING_IDENTIFIER_LONGER_THAN_A_FULL_SHA"
R_DIFFERENT_BUILD = "A_DIFFERENT_BUILD_IS_SERVING"
R_SOURCES_DISAGREE = "THE_PLATFORM_AND_THE_PROCESS_DISAGREE"

EXACT = "EXACT_FULL_SHA"
PREFIX = "PREFIX_MATCH"


def _norm(v):
    """Strip and lower-case, mapping every empty-ish reading to None.

    `jq -r '.commit // ""'` yields `""` on a missing field and the literal
    `"null"` on a JSON null; a 404 body yields whatever the error page
    contained. All of them mean THE SAME THING -- we did not read an
    identifier -- and every one of them must fail rather than be compared.
    `"?"` is in this list because that is exactly what sixty polls of the
    nonexistent `/api/health` produced.
    """
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "?", "null", "none", "unknown", "<empty>"):
        return None
    return s


def verdict(expected, served, *, source="health_endpoint"):
    """Does `served` identify `expected`? A verdict with a named reason.

    `expected` must be a full 40-hex SHA -- it is OUR input, the commit we
    asked the platform to deploy, and if it is malformed the release
    request itself was malformed. Refusing here rather than coercing is
    what stops a truncated or mistyped operator input from being compared
    loosely and passing.
    """
    exp = _norm(expected)
    if exp is None or not FULL_SHA_RE.match(exp):
        return {"ok": False, "refusal": R_EXPECTED_NOT_A_FULL_SHA,
                "expected_raw": expected, "source": source,
                "why": ("the commit we intended to deploy must be a full "
                        "40-hex SHA. A shorter or non-hex value cannot be "
                        "compared strictly, and comparing it loosely is how "
                        "a wrong build passes")}
    got = _norm(served)
    if got is None:
        return {"ok": False, "refusal": R_NOT_READ,
                "expected": exp, "served_raw": served, "source": source,
                "why": ("no identifier was read. A 404 body, a missing "
                        "field, a JSON null and an empty string are all "
                        "this, and none of them may be compared")}
    if not HEX_RE.match(got):
        return {"ok": False, "refusal": R_NOT_HEX,
                "expected": exp, "served_raw": served, "source": source,
                "why": ("the value read is not hexadecimal, so it is not a "
                        "git object name. This is what an HTML error page "
                        "or an unexpected payload looks like")}
    if len(got) < MIN_PREFIX:
        return {"ok": False, "refusal": R_TOO_SHORT,
                "expected": exp, "served_raw": served, "source": source,
                "min_prefix": MIN_PREFIX,
                "why": ("fewer than %d hex characters identifies too little "
                        "to be evidence, and accepting it would let a "
                        "1-character value prefix-match a vast number of "
                        "commits" % MIN_PREFIX)}
    if len(got) > 40:
        return {"ok": False, "refusal": R_TOO_LONG,
                "expected": exp, "served_raw": served, "source": source,
                "why": "longer than a git object name, so it is not one"}

    if len(got) == 40:
        if got == exp:
            return {"ok": True, "verdict": CONFIRMED, "matched_how": EXACT,
                    "expected": exp, "served_raw": served, "served": got,
                    "source": source,
                    "evidence_strength": ("STRONGEST: a full object name "
                                          "compared for equality")}
        return {"ok": False, "refusal": R_DIFFERENT_BUILD,
                "expected": exp, "served_raw": served, "served": got,
                "source": source,
                "why": "a different full SHA is serving"}

    # A PREFIX. The expected full SHA must begin with it -- this direction,
    # because a short id identifies a long one and not the reverse. The old
    # shell asked the reverse and could therefore never match.
    if exp.startswith(got):
        return {"ok": True, "verdict": CONFIRMED, "matched_how": PREFIX,
                "expected": exp, "served_raw": served, "served": got,
                "prefix_length": len(got), "source": source,
                "evidence_strength": (
                    "WEAKER THAN EXACT: %d hex characters, so this is "
                    "evidence about the full id rather than the id itself. "
                    "The full SHA is NOT reconstructed from it" % len(got)),
                "the_prefix_is_not_the_identifier": (
                    "`served` is reported exactly as read. Padding it, or "
                    "recording it as the serving SHA, would be inventing a "
                    "full identifier from partial evidence")}
    return {"ok": False, "refusal": R_DIFFERENT_BUILD,
            "expected": exp, "served_raw": served, "served": got,
            "prefix_length": len(got), "source": source,
            "why": ("the expected SHA does not begin with the served "
                    "prefix, so a different build is serving")}


def combined(expected, *, platform_full_sha=None, health_served=None):
    """One verdict from both sources, preferring the authoritative one.

    ── WHY BOTH, AND WHY DISAGREEMENT IS ITS OWN FAILURE ────────────
    They answer different questions. The platform's deploy list says what
    it believes it LAUNCHED; the health endpoint says what the process
    actually IMPORTED. A deploy can be live while the container serves
    stale code, and a process can be correct while the platform's record
    is not. Confirming from one alone leaves the other's failure mode
    unexamined.

    So when both are readable they must AGREE, and disagreement returns
    `THE_PLATFORM_AND_THE_PROCESS_DISAGREE` rather than picking a winner.
    Averaging or preferring one silently is how a container serving old
    code passes a release check.

    When only one is readable the verdict is that one's, and
    `sources_confirmed` says how many stood behind it -- so a
    single-source confirmation is never presented as a double-checked one.
    """
    out = {"expected": _norm(expected)}
    plat = (verdict(expected, platform_full_sha, source="platform_deploy_list")
            if _norm(platform_full_sha) is not None else None)
    heal = (verdict(expected, health_served, source="health_endpoint")
            if _norm(health_served) is not None else None)
    out["platform"] = plat
    out["health"] = heal

    if plat is None and heal is None:
        return dict(out, ok=False, refusal=R_NOT_READ, sources_confirmed=0,
                    why=("neither the platform's deploy list nor the "
                         "service's health endpoint yielded an identifier, "
                         "so the serving build is UNKNOWN. Unknown fails"))
    # BOTH READABLE AND BOTH WELL-FORMED: they must agree.
    if plat and heal and plat.get("ok") != heal.get("ok"):
        return dict(out, ok=False, refusal=R_SOURCES_DISAGREE,
                    sources_confirmed=0,
                    why=("one source confirms the reviewed build and the "
                         "other does not. The platform says what it "
                         "launched; the process says what it imported. A "
                         "disagreement is a real condition -- a container "
                         "serving stale code -- and is never resolved by "
                         "preferring the agreeable source"))
    oks = [v for v in (plat, heal) if v and v.get("ok")]
    if not oks:
        # Both refused. The PLATFORM's reason leads, because it is the
        # authoritative source, but both are carried.
        lead = plat or heal
        return dict(out, ok=False, refusal=lead["refusal"],
                    sources_confirmed=0, why=lead.get("why"))
    return dict(out, ok=True, verdict=CONFIRMED,
                sources_confirmed=len(oks),
                # THE AUTHORITATIVE FULL SHA WHEN WE HAVE ONE, and None
                # rather than a prefix when we do not.
                authoritative_full_sha=(
                    plat["served"] if plat and plat.get("ok")
                    and plat.get("matched_how") == EXACT else None),
                matched_how=[v["matched_how"] for v in oks],
                single_source=(len(oks) == 1),
                single_source_note=(
                    "only one source confirmed; the other was unreadable. "
                    "This is weaker than agreement between the platform and "
                    "the process and is reported as such"
                    if len(oks) == 1 else None))


def _main(argv):
    """CLI so the release workflow uses THIS comparison, not its own.

    WHY A CLI AND NOT A SHELL REIMPLEMENTATION OF THE RULE. A second copy
    of the comparison in YAML is a second chance to invert it, and the
    inverted one is what shipped last time. The workflow calls this; the
    tests call the same functions; there is one rule.

    Exit 0 only on a confirmed verdict. Every refusal exits 1 and prints
    the named reason, so the step's exit status is the verdict rather than
    a side effect of whether a loop happened to end.
    """
    import json
    if len(argv) < 2:
        print("usage: serving_sha <expected-full-sha> [served] "
              "[platform-full-sha]")
        return 2
    expected = argv[1]
    served = argv[2] if len(argv) > 2 and argv[2] else None
    platform = argv[3] if len(argv) > 3 and argv[3] else None
    out = combined(expected, platform_full_sha=platform,
                   health_served=served)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":                                  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))
