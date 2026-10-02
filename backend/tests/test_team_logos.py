"""Team logos by venue team id + league, with provenance; never a name guess."""
import hashlib
import json
import uuid

import pytest

from sportsassets import team_logos as TL
from tests import paper_harness as H


def _manifest():
    return json.loads(TL.MANIFEST.read_text())


def test_every_manifest_entry_is_verified_and_carries_provenance():
    t = TL.table()
    assert t["withdrawn"] == []
    for e in _manifest()["teams"]:
        for k in ("venue_team_id", "league", "venue_name", "file", "sha256",
                  "source", "source_kind", "retrieved_at"):
            assert e.get(k) not in (None, ""), (k, e)
        body = (TL.ASSET_DIR / e["file"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == e["sha256"]
        assert (e["league"], e["venue_team_id"]) in t["logos"]
    cov = TL.coverage()["verified_by_league"]
    assert cov == {"brb": 14, "mlb": 9, "unl": 13}


def test_botafogo_sp_is_not_botafogo_fr_and_names_are_rechecked():
    got = TL.logo(16888, "brb", "botafogo fc")
    assert got and got["url"].endswith("/venue-team-16888.png")
    assert "brasileirao-b/botafogo-fc" in got["source"]
    # the same venue id under another name, or another league, gets nothing
    assert TL.logo(16888, "brb", "botafogo fr") is None
    assert TL.logo(16888, "bra", "botafogo fc") is None
    assert not [e for e in _manifest()["teams"]
                if TL.fold(e["venue_name"]) in ("botafogo fr",
                                                "botafogo de futebol e regatas")]


def test_national_team_images_are_identified_as_flags():
    for tid, name in ((34911, "croatia"), (34909, "england"),
                      (34898, "wales")):
        got = TL.logo(tid, "unl", name)
        assert got and got["kind"] == "flag", (tid, got)
    assert TL.logo(16909, "brb", "cr brasil")["kind"] == "logo"


def test_mlb_reuses_the_official_files_through_an_exact_name_bridge():
    got = TL.logo(3019, "mlb", "new york yankees")
    assert got["url"].endswith("/mlb-logo-147.svg")
    e = TL.table()["logos"][("mlb", 3019)]
    assert e["bridge"] == {"statsapi_id": 147, "by": "exact full-name equality",
                           "statsapi_name": "New York Yankees"}


def test_no_lookup_without_id_and_league():
    assert TL.logo(None, "brb", "botafogo fc") is None
    assert TL.logo(16888, None, "botafogo fc") is None
    assert TL.logo("bot", "brb", "botafogo fc") is None      # an abbreviation is not an id


def test_a_tampered_file_is_withdrawn(tmp_path, monkeypatch):
    m = _manifest()
    e = dict(m["teams"][0])
    e["sha256"] = "0" * 64
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"teams": [e]}))
    monkeypatch.setattr(TL, "MANIFEST", p)
    TL.table.cache_clear()
    try:
        t = TL.table()
        assert t["logos"] == {} and t["withdrawn"][0]["reason"] == "SHA256_MISMATCH"
        assert TL.logo(e["venue_team_id"], e["league"], e["venue_name"]) is None
    finally:
        TL.table.cache_clear()


@pytest.mark.asyncio
async def test_the_static_route_serves_only_verified_files():
    from fastapi import HTTPException
    from sportsassets.api import agent_pages as AP
    r = await AP.agents_static("venue-team-16888.png")
    assert r.headers["content-type"] == "image/png"
    assert r.body[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    r = await AP.agents_static("mlb-logo-147.svg")
    assert r.headers["content-type"].startswith("image/svg+xml")
    for bad in ("venue-team-99999.png", "manifest.json", "../team_logos.py"):
        with pytest.raises(HTTPException):
            await AP.agents_static(bad)


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_matchups_show_both_teams_and_fall_back_to_initials():
    conn = await H.connect()
    tag = uuid.uuid4().hex[:6]
    ev = "unl-cro-eng-2099-01-0%s" % tag[:1]
    unk = "brb-zzz-yyy-2099-01-01-" + tag
    rows = [
        # a real matchup: both national teams, venue ids from the catalogue
        ("atc-%s-cro" % ev, ev, 34911, "croatia", "croatia", "cro", "unl"),
        ("atc-%s-eng" % ev, ev, 34909, "england", "england", "eng", "unl"),
        # an unmapped team id: names and initials only, no guessed logo
        ("atc-%s-zzz" % unk, unk, 99901, "zzz united", None, "zzz", "brb"),
        ("atc-%s-yyy" % unk, unk, 16888, "botafogo fr", None, "yyy", "brb"),
    ]
    try:
        for slug, e, tid, name, safe, abbr, lg in rows:
            await conn.execute(
                "INSERT INTO us_premap (identifier, market_slug, event_slug, "
                " team_id, team_name, team_safe_name, team_abbr, team_league) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                "t-" + slug, slug, e, tid, name, safe, abbr, lg)
        got = await TL.matchups(conn, ["atc-%s-eng" % ev, "atc-%s-zzz" % unk,
                                       "not-a-catalogue-slug"])
        m = got["atc-%s-eng" % ev]
        assert [t["name"] for t in m] == ["Croatia", "England"]
        assert all(t["logo"] and t["logo"]["kind"] == "flag" for t in m)
        u = got["atc-%s-zzz" % unk]
        assert {t["initials"] for t in u} == {"ZZZ", "YYY"}
        # 16888 under the name "botafogo fr" is not Botafogo-SP's logo
        assert all(t["logo"] is None for t in u)
        assert "not-a-catalogue-slug" not in got
        recs = [{"us_market_slug": "atc-%s-cro" % ev}, {"us_market_slug": None}]
        await TL.decorate(conn, recs)
        assert len(recs[0]["matchup"]) == 2 and "matchup" not in recs[1]
    finally:
        await conn.execute("DELETE FROM us_premap WHERE identifier LIKE $1",
                           "t-%" + tag + "%")
        await conn.execute("DELETE FROM us_premap WHERE event_slug = $1", ev)
        await conn.close()
