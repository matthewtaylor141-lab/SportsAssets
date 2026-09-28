"""The release verification must fail closed, with named reasons.

Owner requirement: "Inspect the loop's terminal condition. An expired wait
must fail the job and prevent it from claiming verification. Add
counterexamples for persistent 404, unreadable health, wrong build and
timeout. Compare commit identifiers using an explicit, validated format.
Prefer an authoritative full SHA where available; never fabricate a full
identifier from a prefix."

── HOW THE ROTATION "SUCCEEDED" WITH A BROKEN SHA-WAIT STEP ─────────
Four things had to be true at once, and all four were:

  1 · THE URL DID NOT EXIST. S3 polled `$A/api/health`. The service
      publishes `/healthz` and `/api/health/services`, not `/api/health`.
      Every poll took a 404.
  2 · `jq` TURNED THAT INTO A NON-FATAL VALUE. `curl ... | jq -r '.commit
      // "?"'` on a 404 body produced `"?"` rather than an error, so the
      loop had something to compare and compared it 60 times.
  3 · THE COMPARISON COULD NOT SUCCEED EVEN ON THE RIGHT URL.
      `case "$SERVING" in "$OPERATOR_COMMIT"*)` asks whether the SERVED
      value begins with the full 40-hex SHA. `/healthz` reports
      `RENDER_GIT_COMMIT[:7]` -- seven characters. A 7-char string cannot
      begin with a 40-char one. The test was unsatisfiable by construction.
  4 · AND THE LOOP HAD NO FAILURE BRANCH. `for i in $(seq 1 60); do ...
      done` simply ends. Nothing examined `SHA_CONFIRMED` afterwards, so
      falling out of the loop was indistinguishable from breaking out of it
      on success. Ten minutes elapsed, nothing printed, and the step exited
      0 -- and an exit 0 is what the job reads as verified.

So the step that exists to prove serving-SHA identity proved nothing, and
the only reason the release is still established is that TWO INDEPENDENT
checks did fail closed: S3b read the live commit from Render's own deploy
list, and the desk sign-in returned `ok:true` against the new build. That
evidence is preserved and is NOT what these tests replace.

── WHAT THESE TESTS PIN ──────────────────────────────────────────────
That each of the four failures above now returns a DISTINCT refusal, so
they can never again be collapsed into silence -- and that a prefix match
is reported as weaker evidence rather than promoted to an identity.
"""

import pytest

from sportsassets import serving_sha as S


FULL = "c3d0cfc303fa92e182786d40b4b27a4d05322b32"
OTHER = "f9f63d8a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e"


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FOUR COUNTEREXAMPLES, EACH DISTINCT
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("body", ["?", "", None, "null", "none", "<empty>"])
def test_counterexample_persistent_404(body):
    """A 404 polled sixty times must REFUSE, not compare.

    `"?"` is the literal value the old step read from the nonexistent
    endpoint, so it is the first case here. Every empty-ish reading maps to
    the same refusal because they mean the same thing -- we did not read an
    identifier -- and none of them may reach a comparison.
    """
    got = S.verdict(FULL, body)
    assert got["ok"] is False
    assert got["refusal"] == S.R_NOT_READ, got


def test_counterexample_unreadable_health_body():
    """An HTML error page is not a git object name."""
    got = S.verdict(FULL, "<!doctype html><title>Not Found</title>")
    assert got["ok"] is False
    assert got["refusal"] == S.R_NOT_HEX, got


def test_counterexample_wrong_build():
    """A different build serving is its own failure, not a read failure.

    Distinguishing this from `NOT_READ` is the whole point: one means the
    deploy went wrong, the other means the check went wrong, and they need
    different responses from whoever reads the log.
    """
    got = S.verdict(FULL, OTHER)
    assert got["ok"] is False
    assert got["refusal"] == S.R_DIFFERENT_BUILD, got
    # A WRONG PREFIX IS ALSO A WRONG BUILD, not an unreadable value.
    short = S.verdict(FULL, OTHER[:7])
    assert short["refusal"] == S.R_DIFFERENT_BUILD, short


def test_counterexample_timeout_is_a_refusal_not_a_fallthrough():
    """An expired wait must produce a refusal object, never silence.

    THE SHELL DEFECT IN ONE ASSERTION. The old loop's terminal state was
    "the for loop ended", which is not a value and cannot be tested. Here
    the timeout path is a function call with a return value, so a caller
    that ignores it is visibly ignoring something.
    """
    got = S.combined(FULL, platform_full_sha=None, health_served=None)
    assert got["ok"] is False
    assert got["refusal"] == S.R_NOT_READ, got
    assert got["sources_confirmed"] == 0
    assert "UNKNOWN" in got["why"] and "fails" in got["why"]


# ═════════════════════════════════════════════════════════════════════
# 2 · THE FORMAT IS VALIDATED, IN BOTH DIRECTIONS
# ═════════════════════════════════════════════════════════════════════

def test_a_truncated_value_cannot_prefix_match_everything():
    """Below 7 hex characters is refused, which is what `""` needed.

    Without a minimum length, `"".startswith` logic or a 1-character value
    would satisfy a prefix test against an enormous number of commits. This
    bound is the reason the empty reading cannot pass.
    """
    for short in ("c", "c3", "c3d0cf"):
        got = S.verdict(FULL, short)
        assert got["ok"] is False
        assert got["refusal"] == S.R_TOO_SHORT, (short, got)
    # SEVEN IS ACCEPTED -- git's own abbreviation, and what Render reports.
    assert S.verdict(FULL, FULL[:7])["ok"] is True


def test_the_expected_commit_must_itself_be_a_full_sha():
    """Our own input is validated, so a mistyped release cannot pass loosely.

    If `operator_commit` were accepted as a 7-char value, the comparison
    would become prefix-versus-prefix and a wrong build could satisfy it.
    """
    for bad in (FULL[:7], "", None, "not-hex-at-all", FULL + "aa",
                "C3D0CFC303FA92E182786D40B4B27A4D05322B3"):
        got = S.verdict(bad, FULL)
        assert got["ok"] is False
        assert got["refusal"] == S.R_EXPECTED_NOT_A_FULL_SHA, (bad, got)


def test_case_is_normalised_but_content_is_not():
    """An upper-case reading is not rejected for its case alone."""
    assert S.verdict(FULL, FULL[:7].upper())["ok"] is True
    assert S.verdict(FULL.upper(), FULL)["ok"] is True


def test_a_value_longer_than_a_sha_is_refused():
    got = S.verdict(FULL, FULL + "00")
    assert got["refusal"] == S.R_TOO_LONG, got


# ═════════════════════════════════════════════════════════════════════
# 3 · A PREFIX IS EVIDENCE, NOT AN IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_the_comparison_runs_in_the_direction_that_can_succeed():
    """The FULL sha must start with the SERVED prefix, not the reverse.

    This is the exact inversion that made the old step unsatisfiable, so it
    is asserted directly rather than implied by a passing case.
    """
    got = S.verdict(FULL, FULL[:7])
    assert got["ok"] is True
    assert got["matched_how"] == S.PREFIX
    # AND THE REVERSE DIRECTION IS NOT WHAT IS BEING TESTED: a served value
    # that the expected SHA does not begin with must fail even if it shares
    # a prefix with something else.
    assert S.verdict(FULL, "c3d0cfd")["ok"] is False


def test_a_prefix_is_never_promoted_to_a_full_identifier():
    """No padding, no reconstruction, and it says so.

    THE OVERCLAIM THIS PREVENTS. Recording the 7-char value as "the
    serving SHA" would put a fabricated identifier into the release record,
    and a later reader comparing it against a real SHA would find a
    mismatch with no way to know why.
    """
    got = S.verdict(FULL, FULL[:7])
    assert got["served"] == FULL[:7]
    assert len(got["served"]) == 7
    assert got["served"] != FULL
    assert "NOT reconstructed" in got["evidence_strength"]
    assert "inventing a" in got["the_prefix_is_not_the_identifier"]


def test_exact_and_prefix_are_reported_as_different_strengths():
    """Confirmed is not one thing. A full match is stronger."""
    ex = S.verdict(FULL, FULL)
    pf = S.verdict(FULL, FULL[:7])
    assert ex["matched_how"] == S.EXACT
    assert pf["matched_how"] == S.PREFIX
    assert "STRONGEST" in ex["evidence_strength"]
    assert "WEAKER" in pf["evidence_strength"]


# ═════════════════════════════════════════════════════════════════════
# 4 · THE AUTHORITATIVE SOURCE IS PREFERRED, AND DISAGREEMENT FAILS
# ═════════════════════════════════════════════════════════════════════

def test_the_platforms_full_sha_is_the_authoritative_identifier():
    """When Render reports the full SHA, that is what gets recorded.

    And when it does not, `authoritative_full_sha` is None rather than a
    prefix -- absence is reported as absence.
    """
    both = S.combined(FULL, platform_full_sha=FULL, health_served=FULL[:7])
    assert both["ok"] is True
    assert both["authoritative_full_sha"] == FULL
    assert both["sources_confirmed"] == 2
    assert both["single_source"] is False

    only_health = S.combined(FULL, platform_full_sha=None,
                             health_served=FULL[:7])
    assert only_health["ok"] is True
    assert only_health["authoritative_full_sha"] is None
    assert only_health["single_source"] is True
    assert "weaker" in only_health["single_source_note"]


def test_a_stale_container_under_a_live_deploy_fails_closed():
    """The platform confirms, the process does not. That must NOT pass.

    THE REAL CONDITION THIS CATCHES. A deploy can be live in Render's
    record while the container serves code from before it -- which is
    precisely the failure a serving-SHA check exists to find. Preferring
    the agreeable source, or averaging them, would report the release as
    verified on the strength of the platform's intention.
    """
    got = S.combined(FULL, platform_full_sha=FULL, health_served=OTHER[:7])
    assert got["ok"] is False
    assert got["refusal"] == S.R_SOURCES_DISAGREE, got
    assert got["sources_confirmed"] == 0
    assert "stale code" in got["why"]
    # AND THE OTHER DIRECTION TOO: the process right, the platform wrong.
    rev = S.combined(FULL, platform_full_sha=OTHER, health_served=FULL[:7])
    assert rev["ok"] is False
    assert rev["refusal"] == S.R_SOURCES_DISAGREE, rev


def test_both_refusing_reports_the_platforms_reason_and_keeps_both():
    """Two refusals are not averaged either; both are carried."""
    got = S.combined(FULL, platform_full_sha=OTHER, health_served=OTHER[:7])
    assert got["ok"] is False
    assert got["refusal"] == S.R_DIFFERENT_BUILD
    assert got["platform"]["source"] == "platform_deploy_list"
    assert got["health"]["source"] == "health_endpoint"


def test_confirmation_never_happens_without_a_readable_identifier():
    """The exhaustive property: ok is True only on a real match.

    A sweep rather than a list of cases, because the failure being guarded
    against is an input nobody thought to enumerate quietly passing.
    """
    for served in (None, "", "?", "null", "  ", "zzzzzzz", "c", "c3d0cf",
                   OTHER, OTHER[:7], FULL + "0", "0" * 40, "<html>"):
        got = S.verdict(FULL, served)
        assert got["ok"] is False, served
        assert got.get("refusal"), served
    for served in (FULL, FULL[:7], FULL[:12], FULL[:39], FULL.upper()):
        assert S.verdict(FULL, served)["ok"] is True, served
