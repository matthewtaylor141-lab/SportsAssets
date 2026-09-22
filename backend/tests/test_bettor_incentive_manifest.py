

def test_freeze_refuses_the_load_wrapper_by_name_not_as_no_programmes():
    """THE DEFECT: a refusal that named the wrong cause.

    `load()` returns {"ok","why","path","manifest"}. Passing that
    wrapper to freeze() used to return
    MANIFEST_HAS_NO_QUALIFYING_PROGRAMS -- fail-closed, and completely
    misleading: it reads as "the venue had no programmes today" when
    the truth is "you handed me the wrong object". That cost real time
    chasing a delivery defect that did not exist, with a
    byte-identical manifest sitting in the image.
    """
    import json as _json
    import os as _os
    from sportsassets import bettor_incentive_manifest as m

    here = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    path = _os.path.join(here, "..", "research", "beta48", "acceptance",
                         "incentive_manifest.json")
    if not _os.path.exists(path):
        import pytest
        pytest.skip("no captured manifest in this checkout")

    wrapper = m.load(path)
    assert wrapper["ok"] is True

    bad = m.freeze(wrapper, et_date=wrapper["manifest"]["et_date"])
    assert bad["ok"] is False
    assert bad["why"] == m.M_MALFORMED
    assert "not a manifest document" in bad["detail"]

    good = m.freeze(wrapper["manifest"],
                    et_date=wrapper["manifest"]["et_date"])
    assert good["ok"] is True, good
    assert good["markets"] >= 10
