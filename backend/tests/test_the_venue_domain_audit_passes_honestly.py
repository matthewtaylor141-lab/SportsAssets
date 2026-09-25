"""THE VENUE CONCURRENCY DOMAIN, AND WHAT ITS `PASS` DOES NOT COVER.

WHY THIS FILE EXISTS. `calibration-evidence` is the reservation workflow
the funded calibration path gathers evidence under, and
`startup_census.post_acquisition` will not let it read the venue unless it
can PROVE it holds the global slot. That proof includes
`venue_domain.audit()["DOMAIN_AUDIT"] == "PASS"`, and the audit was
FAILING, so two tests in test_calibration_domain.py had been red:

    INCOMPATIBLE_GROUP_MEMBERSHIP: ['incentive-manifest', 'tape-probe']

The two are NOT the same kind of finding, and collapsing them would have
hidden the one that mattered.

  * `incentive-manifest` was a TRUE POSITIVE and a real gap. It had NO
    concurrency block at all, and it genuinely reads the venue gateway:
    it runs capture_incentive_manifest.py, which imports
    sportsassets.bettor_incentive_manifest, whose INCENTIVES_URL is
    https://gateway.polymarket.us/v1/incentives. It could have run
    alongside the very gather that is meant to own the slot.

  * `tape-probe` was a FALSE POSITIVE. It fetches a different host, and
    the classifier reached it through INVOKES_VENUE_CAPABLE_MODULE:
    test_tape_capture -- a module whose only mention of the gateway is
    the assertion that the capturer CANNOT reach it. The classifier
    matched a PROHIBITION and read it as a capability.

Both were resolved by joining the domain, which tightens. The alternative
for `tape-probe` -- teaching the classifier to discount a host named
inside a negative assertion -- would have made a venue-access gate depend
on a regex telling intent from prohibition, and the first time that
reasoning was wrong a real collector would leave the domain unnoticed.

AND THE PART THAT IS STILL OPEN, ASSERTED HERE SO THE GREEN CANNOT READ AS
COMPLETE. `venue_domain._py_files` walks `research/**` ONLY. The package
that actually holds the venue clients -- backend/sportsassets/ -- is never
scanned, so `INCENTIVES_URL` itself is invisible to the module classifier;
`incentive-manifest` was caught only because its own YAML comments happen
to name the host. The transitive import walk itself is sound -- it would
follow the chain if the file were read -- so the scan scope is the entire
gap, and widening it is a sufficient remedy. A PASS therefore means "no
workflow
that names the gateway in its YAML, or invokes a research/ module that
does, sits outside the domain" -- which is narrower than "every
venue-touching workflow is in the domain". Widening the scan is a decision
with a real cost the module itself names (serialising production ops
behind a research run), so it is recorded here rather than made quietly.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from sportsassets import calibration_domain as cd

ROOT = cd._repo_root()
WF = ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def audit():
    _rc, vd = cd.machinery()
    return vd.audit(str(ROOT))


def _group(name):
    text = (WF / ("%s.yml" % name)).read_text(errors="ignore")
    m = re.search(r"^concurrency:\s*\n(?:\s+.*\n)*?\s+group:\s*(.+)$",
                  text, re.M)
    return m.group(1).strip() if m else None


# ── the audit passes, and ownership becomes provable ─────────────────

def test_the_domain_audit_passes(audit):
    assert audit["DOMAIN_AUDIT"] == "PASS", \
        audit["WORKFLOWS_OUTSIDE_THE_DOMAIN"]
    assert audit["WORKFLOWS_OUTSIDE_THE_DOMAIN"] == {}


def test_every_workflow_the_scan_names_is_inside_the_domain(audit):
    _rc, vd = cd.machinery()
    for name in audit["KNOWN_VENUE_TOUCHING_WORKFLOWS"]:
        assert _group(name) == vd.GLOBAL_DOMAIN, name


def test_the_two_that_were_outside_are_now_in_by_name():
    """Named explicitly, so a later edit that drops either one fails HERE
    with the reason rather than in an ownership test three files away."""
    for name in ("incentive-manifest", "tape-probe"):
        assert _group(name) == "pmus-public-read-global", name
        text = (WF / ("%s.yml" % name)).read_text(errors="ignore")
        assert "cancel-in-progress: false" in text, name


def test_incentive_manifest_really_does_read_the_gateway():
    """The TRUE POSITIVE, traced rather than taken from the audit's word:
    workflow -> capture script -> module -> URL."""
    wf = (WF / "incentive-manifest.yml").read_text(errors="ignore")
    assert "capture_incentive_manifest.py" in wf
    cap = (ROOT / "research" / "beta48"
           / "capture_incentive_manifest.py").read_text()
    assert "bettor_incentive_manifest" in cap
    mod = (ROOT / "backend" / "sportsassets"
           / "bettor_incentive_manifest.py").read_text()
    assert "https://gateway.polymarket.us/v1/incentives" in mod


def test_tape_probes_only_gateway_mention_is_an_unreachability_assertion():
    """The FALSE POSITIVE, shown to be one -- which is why it was resolved
    by joining the domain and NOT by relaxing the classifier."""
    src = (ROOT / "research" / "beta48" / "tape"
           / "test_tape_capture.py").read_text()
    lines = [ln for ln in src.splitlines() if "gateway.polymarket.us" in ln]
    assert lines, "the classifier matched something"
    # Every occurrence is inside a refusal: a banned-host list, or a host
    # the guard is asserted to reject.
    for ln in lines:
        assert ("banned" in ln or "for bad in" in ln
                or ln.strip().startswith(('"', "'", "(", '"""'))), ln
    assert "cannot_reach_the_collectors_hosts" in src


def test_ownership_is_provable_now(audit):
    """THE POINT OF THE REPAIR. With the audit passing, a run of the
    reservation workflow that is present and executing in the census owns
    the slot -- which is what post_acquisition refused before."""
    from tests.test_calibration_domain import REPO, SELF, me, pages
    got = cd.coordination(fetch=pages({"calibration-evidence": [me()]}),
                          repo=REPO, env=SELF)
    assert got["ownership"]["OWNED"] is True, got["ownership"]
    assert got["blockers"] == []
    assert got["MAY_READ_THE_VENUE"] is True


# ── what the PASS does NOT cover, recorded not waived ────────────────

def test_the_scan_covers_research_only_and_this_is_recorded():
    """`backend/sportsassets/` is where the venue clients live and it is
    NOT scanned. Asserted so the audit's PASS is never read as proof that
    every venue-touching workflow is domained."""
    import inspect

    _rc, vd = cd.machinery()
    src = inspect.getsource(vd._py_files)
    assert '"research"' in src
    assert "backend" not in src, (
        "if the scan is widened, this test and the docstring above must be "
        "rewritten -- and the serialisation cost weighed, not assumed")


def test_the_url_that_matters_is_invisible_to_the_module_classifier():
    """The concrete consequence: the module holding the gateway URL is not
    in the capable set, so `incentive-manifest` was caught by its own
    comments and not by the mechanism designed to catch it."""
    _rc, vd = cd.machinery()
    capable, direct = vd.venue_capable_modules(ROOT)
    assert "bettor_incentive_manifest" not in direct
    assert "bettor_incentive_manifest" not in capable
    # And the workflow is still named -- by the weaker signal.
    why = vd.audit(str(ROOT))["WHY_EACH_IS_VENUE_TOUCHING"]
    assert why["incentive-manifest"] == ["NAMES_THE_VENUE_HOST"]


def test_the_import_walk_would_follow_the_chain_if_the_file_were_scanned():
    """THE SCAN SCOPE IS THE WHOLE CAUSE -- the import walk is fine.

    A CORRECTION OF MINE. I first recorded a second defect here: that
    `\\b(import|from)\\s+x\\b` cannot resolve `from sportsassets import x`.
    That is wrong. The alternation matches the `import x` inside the
    package-qualified form, so the walk WOULD follow
    capture_incentive_manifest -> bettor_incentive_manifest. The only
    reason it does not is that `_py_files` never reads the target file, so
    the dependency is not in `src` to be matched against.

    This matters for the remedy: widening the scan is sufficient on its
    own, and no regex change is needed.
    """
    dep = "bettor_incentive_manifest"
    walk = re.compile(r"\b(import|from)\s+%s\b" % re.escape(dep))
    real = (ROOT / "research" / "beta48"
            / "capture_incentive_manifest.py").read_text()
    assert "from sportsassets import %s" % dep in real
    assert walk.search(real), "the walk does match the real import line"
    # It is absent from the scan's inputs, and that is the whole gap.
    _rc, vd = cd.machinery()
    scanned = {p.stem for p in vd._py_files(ROOT)}
    assert "capture_incentive_manifest" in scanned
    assert dep not in scanned


# ── the venue-terms capture, recorded as facts ───────────────────────
#
# These live here rather than in their own file because they answer the
# same kind of question the audit above does: what did an external party
# actually give us, and is the gap ours or theirs.

def test_the_venue_terms_attempts_are_recorded_not_remembered():
    """A 404 answers "does the venue publish this" as definitely as a 200,
    and a retrieval nobody wrote down has to be repeated."""
    from sportsassets import bettor_venue_settlement as VS

    atts = VS.VENUE_TERMS_CAPTURE_ATTEMPTS
    assert len(atts) == 4, atts
    for a in atts:
        assert a["asked_at"].endswith("Z")
        assert a["result"]
        assert a["detail"]
    results = {a["result"] for a in atts}
    assert results == {"HTTP_404", "HTTP_200_NO_SETTLEMENT_PROSE",
                       "HTTP_200_CLIENT_RENDERED",
                       "HTTP_200_INDEX_PUBLISHED_AND_READABLE"}


def test_the_sitemap_attempt_retracts_the_not_published_claim():
    """THE ATTEMPT THAT CORRECTED ME. Three failed guesses were used to
    support "the venue does not publish this". The fourth attempt reached
    the published index, so the record has to carry the retraction beside
    the attempts that produced the wrong conclusion -- and must not turn
    618 URLs into a settlement answer."""
    from sportsassets import bettor_venue_settlement as VS

    a = [x for x in VS.VENUE_TERMS_CAPTURE_ATTEMPTS
         if x["result"] == "HTTP_200_INDEX_PUBLISHED_AND_READABLE"][0]
    assert "618" in a["detail"]
    assert "retracts" in a["detail"]
    # AND IT CLAIMS NOTHING ABOUT CONTENT. An enumerated URL is a page that
    # exists; inferring compatibility from a sitemap is exactly the error
    # this entry is here to prevent.
    assert "not one word of its content" in a["detail"].lower()
    assert "no settlement" in a["detail"].lower()


def test_a_404_body_size_is_not_mistaken_for_a_capture():
    """The 404s served 117 KB of documentation shell. A byte count alone
    would have read as a hit, so the record says so."""
    from sportsassets import bettor_venue_settlement as VS

    d = [a for a in VS.VENUE_TERMS_CAPTURE_ATTEMPTS
         if a["result"] == "HTTP_404"][0]
    assert len(d["targets_404"]) == 5
    assert "byte count alone" in d["detail"]


def test_the_gap_is_named_as_external_and_not_as_ours():
    from sportsassets import bettor_venue_settlement as VS

    note = VS.VENUE_TERMS_NOT_YET_LOCATED
    # THE SPLIT THIS ASSERTS, AND THE OLD NAME GOT IT WRONG. Locating the
    # page is ours; what the page says is the venue's. A single
    # "EXTERNAL DEPENDENCY" label over both was how "we guessed eight URLs
    # badly" got reported as "the venue publishes nothing".
    assert "Locating it is OURS to finish" in note
    assert "the venue's to answer" in note
    assert "GUESSED" in note
    assert "618" in note
    # AND IT DOES NOT PROMISE A WORKAROUND. The truthful state is that
    # every candidate refuses, and that is what it says.
    assert "every supported-market entry candidate refuses" in note
    assert "truthful state" in note
    assert "silence is still not agreement" in note
    # THE RETRACTED CLAIM MUST NOT SURVIVE UNDER THE OLD NAME.
    assert not hasattr(VS, "VENUE_TERMS_NOT_PUBLISHED")


def test_nothing_was_written_into_the_settlement_tables():
    """The capture is evidence. Which sentence states which condition is a
    READING, and BOOK_TERMS still carries only the Pinnacle capture."""
    from sportsassets import bettor_settlement_terms as ST

    assert set(k[0] for k in ST.BOOK_TERMS) == {"baseball"}
    assert ST.CAPTURED_SCOPE.keys() == {("baseball", "h2h")}
