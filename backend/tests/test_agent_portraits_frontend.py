"""EVERY AGENT IS ITS OWN LICENSED 3D PERSON (frontend/public/command).

Static proofs over the shipped files:
  * models/manifest.json has a complete licensed entry for each of the seven
    agents (model, licence file, SPDX, licensor, test_asset false), each model
    file exists, is a glTF 2.0 binary of a sane size with the ARKit blink and
    viseme targets the pipeline drives, and NO two agents share a model file
    or a Rocketbox source avatar;
  * no recolour / clone mapping is left: the legacy floor CAST maps each seat
    to its own model with no tint, and the HQ2 3D floor draws no sphere +
    capsule stand-ins;
  * the agent pages render the agent's own model live in the hero
    (hq2-portrait.js through cc_avatar.js mountAvatar), lazily, disposing it,
    honouring reduced motion, and show a name card labelled
    "3D PORTRAIT PENDING" (never a figure) without a licensed model;
  * the Chief Allocator is shown as Allie while her id and route stay
    CHIEF_ALLOCATOR and /allocator.
"""
from __future__ import annotations

import json
import pathlib
import re
import struct

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
MODELS = CMD / "team-demo" / "assets" / "models"
MANIFEST = json.loads((MODELS / "manifest.json").read_text())
SLUGS = ["derek", "xavier", "audrey", "karen", "allocator", "eddie", "scout"]


def _glb_json(path: pathlib.Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2 and length == len(data), path.name
    clen, ctype = struct.unpack_from("<II", data, 12)
    assert ctype == 0x4E4F534A, path.name
    return json.loads(data[20:20 + clen])


def test_every_agent_has_a_complete_licensed_entry():
    chars = MANIFEST["characters"]
    for slug in SLUGS:
        e = chars[slug]
        for k in ("model", "license_file", "license_spdx", "licensed_from", "credit"):
            assert e.get(k), (slug, k)
        assert e["test_asset"] is False, slug
        assert e["license_spdx"] == "MIT", slug
        assert (MODELS / e["model"]).is_file(), slug
        assert (MODELS / e["license_file"]).is_file(), slug


def test_no_two_agents_share_a_model_or_a_source_avatar():
    chars = MANIFEST["characters"]
    models = [chars[s]["model"] for s in SLUGS]
    assert len(set(models)) == len(models), models
    sources = [re.search(r"Assets/Avatars/Professions/(\w+)", chars[s]["licensed_from"]).group(1) for s in SLUGS]
    assert len(set(sources)) == len(sources), sources
    assert dict(zip(SLUGS, sources)) == {
        "derek": "Business_Male_03", "xavier": "Business_Male_05", "audrey": "Business_Female_04",
        "karen": "Business_Female_02", "allocator": "Business_Female_03",
        "eddie": "Business_Male_04", "scout": "Business_Male_06"}
    shas = [chars[s]["source"]["sha256"] for s in SLUGS]
    assert len(set(shas)) == len(shas)


def test_models_are_sane_glbs_with_the_driven_face_targets():
    for slug in SLUGS:
        path = MODELS / MANIFEST["characters"][slug]["model"]
        size = path.stat().st_size
        assert 1_000_000 <= size <= 2_400_000, (slug, size)
        j = _glb_json(path)
        assert "EXT_meshopt_compression" in j.get("extensionsUsed", []), slug
        assert j.get("skins"), slug
        names = set()
        for m in j["meshes"]:
            names.update((m.get("extras") or {}).get("targetNames", []))
        assert any(n.endswith("EyeBlinkLeft") for n in names), slug
        assert any(n.endswith("JawOpen") for n in names), slug
        assert sum(n.startswith("AA_VI_") for n in names) == 15, slug


def test_no_recolour_or_clone_mapping_is_left():
    floor = (CMD / "floor-scene.js").read_text()
    start = floor.index("export const CAST = {")
    cast = floor[start:floor.index("};", start)]
    models = re.findall(r"(\w+): \{model: '(\w+)', tint: null", cast)
    assert len(models) == 7 and len({m for _, m in models}) == 7, models
    assert "tint: [" not in floor and "reused with a recoloured" not in floor
    hq2 = (CMD / "hq2-floor-scene.js").read_text()
    assert "CapsuleGeometry" not in hq2 and "SphereGeometry(.19" not in hq2
    assert "loadPeople" in hq2 and "used.has(e.model)" in hq2
    assert "recoloured suits" not in (CMD / "floor.html").read_text()
    # the CSS peg figures are gone (each pod / hero shows the agent's own model)
    assert "radial-gradient(circle at 50% 28%" not in (CMD / "hq2-floor.css").read_text()
    assert "radial-gradient(circle at 50% 25%" not in (CMD / "hq3-live.css").read_text()
    for slug in SLUGS:
        assert (MODELS / "portraits" / (slug + ".jpg")).is_file(), slug


def test_agent_pages_render_the_agents_own_model_live():
    html = (CMD / "agent.html").read_text()
    assert '<script type="module" src="hq2-portrait.js"></script>' in html
    js = (CMD / "hq2-portrait.js").read_text()
    assert "mountAvatar" in js and "compact: true" in js
    assert "3D PORTRAIT PENDING" in js and "test_asset !== false" in js
    assert "IntersectionObserver" in js and "dispose()" in js and "prefers-reduced-motion" in js
    hero = (CMD / "hq2-agent.js").read_text()
    assert 'class="bt-agent2-face bt-portrait' in hero and "<canvas" in hero
    assert "cc:mode" in hero
    av = (CMD / "team-demo" / "assets" / "cc_avatar.js").read_text()
    assert "const dispose = () =>" in av and "renderer.forceContextLoss()" in av
    assert "opts.compact" in av


def test_allie_is_the_chief_allocator_display_name():
    core = (CMD / "floor-core.js").read_text()
    assert re.search(r"agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator'", core)
    nav = (CMD / "agent.html").read_text()
    assert ('<a href="/allocator" data-agent="allocator">Allie<span class="sep"> · </span>'
            '<span class="role">Chief Allocator</span></a>') in nav
    assert MANIFEST["characters"]["allocator"]["model"] == "allie.glb"
