// ROCKETBOX FBX -> A CHARACTER .glb FOR THE COMMAND CENTRE PIPELINE.
//
// Step 1 (outside this script): FBX2glTF (npm "fbx2gltf", its bundled Linux
// binary) converts the facial FBX with its skeleton and blendshapes:
//     FBX2glTF -b --pbr-metallic-roughness -i <Name>_facial.fbx -o raw
// and tools/rocketbox_textures.py downscales the 2K TGA maps to JPEG
// (colour 2K head / 1K body, normal 1K, specular -> roughness 1K).
// Step 2 (this script, run from a directory with @gltf-transform/core,
// /extensions, /functions and meshoptimizer installed):
//     node rocketbox_to_glb.mjs <three dir> <fbx> <raw.glb> <tex dir> <out.glb>
// FBX2glTF writes no morph target names, so they are read from the FBX with
// three.js FBXLoader and their ORDER is verified (max |delta| per target,
// invariant to vertex welding, must agree up to the one unit scale); the
// script refuses to write a model whose order does not match. Textures are
// attached by material (head/opacity -> head maps, body -> body maps), then
// the model is quantized and Meshopt-compressed.
const [THREE_DIR, FBX, RAW, TEX, OUT] = process.argv.slice(2);
if (!OUT) throw new Error('usage: node rocketbox_to_glb.mjs <three dir> <fbx> <raw.glb> <tex dir> <out.glb>');
import fs from 'fs';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { quantize, meshopt, prune, dedup } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';
globalThis.self = globalThis; globalThis.window = globalThis;
globalThis.document = {createElementNS: () => ({style: {}, addEventListener() {}, getContext: () => null})};
const THREE = await import(THREE_DIR + '/build/three.module.js');
const {FBXLoader} = await import(THREE_DIR + '/examples/jsm/loaders/FBXLoader.js');
THREE.TextureLoader.prototype.load = function () { return new THREE.Texture(); };

const buf = fs.readFileSync(FBX);
const fbx = new FBXLoader().parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), '');
let fm; fbx.traverse(o => { if (o.isMesh && o.morphTargetDictionary) fm = o; });
const names = Object.keys(fm.morphTargetDictionary).sort((a, b) => fm.morphTargetDictionary[a] - fm.morphTargetDictionary[b]);
await MeshoptEncoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder': MeshoptEncoder});
const doc = await io.read(RAW);
const root = doc.getRoot();
const mesh = root.listMeshes()[0];
const prims = mesh.listPrimitives();
// order check: max |delta| per target is invariant to vertex welding; the
// FBX-to-glTF unit scale is one constant, so every target's ratio must agree
const maxAbs = (a) => { let m = 0; for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i])); return m; };
const F = fm.geometry.morphAttributes.position.map(a => maxAbs(a.array));
const Gs = names.map((_, t) => Math.max(...prims.map(p => { const tg = p.listTargets()[t]; const a = tg && tg.getAttribute('POSITION'); return a ? maxAbs(a.getArray()) : 0; })));
const ratios = F.map((f, t) => f > 1e-4 ? Gs[t] / f : null).filter(x => x !== null).sort((a, b) => a - b);
const med = ratios[Math.floor(ratios.length / 2)];
const bad = F.filter((f, t) => f > 1e-4 && Math.abs(Gs[t] / f / med - 1) > 0.02).length;
const shifted = F.map((f, t) => t + 1 < F.length && f > 1e-4 ? Math.abs(Gs[t + 1] / f / med - 1) : null).filter(x => x !== null);
const report = {targets: names.length, unit_ratio: med, mismatched: bad, shifted_order_mean_deviation: shifted.reduce((a, b) => a + b, 0) / shifted.length};
console.log('ORDER', JSON.stringify(report));
if (bad) throw new Error('morph target order does not match the FBX');
// gltf-transform writes extras.targetNames from each target's own name
for (const p of prims) p.listTargets().forEach((tg, t) => tg.setName(names[t].split('.').pop()));
// ONLY THE TARGETS THE PIPELINE DRIVES: the 15 visemes (AA_VI_*) and the
// ARKit 52 (AK_*). The FACS (AU_*), HB_* and SR_* sets duplicate them and
// cost every vertex a texture fetch per target per frame (175 -> 67), which
// matters on phones and in software GL. Pruned AFTER the order check.
const KEEP = (n) => /^(AA_VI_|AK_)/.test(n.split('.').pop());
const kept = names.filter(KEEP);
for (const p of prims) for (const tg of p.listTargets()) if (!KEEP(tg.getName())) { p.removeTarget(tg); tg.dispose(); }
const w = mesh.getWeights();
mesh.setWeights(names.map((n, i) => [n, w[i] || 0]).filter(([n]) => KEEP(n)).map(([, v]) => v));
names.length = 0; names.push(...kept);
mesh.setExtras(Object.assign({}, mesh.getExtras(), {targetNames: names.map(n => n.split('.').pop())}));
const tex = (f, mime) => doc.createTexture(f).setImage(fs.readFileSync(TEX + '/' + f)).setMimeType(mime);
// THE OPACITY MATERIAL (hair cards, lashes) takes the vendor's RGBA opacity
// map when it was supplied (tools/rocketbox_textures.py writes opacity.png):
// its own colour and coverage, alpha-tested and double sided, rough and
// non-metallic like hair. Without that map it falls back to the head maps
// (and the manifest hides it, as for the first Derek candidate).
const hasOpacity = fs.existsSync(TEX + '/opacity.png');
for (const m of root.listMaterials()) {
  const isOp = /opacity/.test(m.getName());
  if (isOp && hasOpacity) {
    m.setBaseColorTexture(tex('opacity.png', 'image/png')).setBaseColorFactor([1, 1, 1, 1]);
    m.setNormalTexture(null).setMetallicRoughnessTexture(null).setMetallicFactor(0).setRoughnessFactor(0.72);
    m.setAlphaMode('MASK').setAlphaCutoff(0.32).setDoubleSided(true);
    continue;
  }
  const part = /head|opacity/.test(m.getName()) ? 'head' : 'body';
  m.setBaseColorTexture(tex(part + '_color.jpg', 'image/jpeg')).setBaseColorFactor([1, 1, 1, 1]);
  m.setNormalTexture(tex(part + '_normal.jpg', 'image/jpeg'));
  m.setMetallicRoughnessTexture(tex(part + '_mr.jpg', 'image/jpeg')).setMetallicFactor(0).setRoughnessFactor(1);
  if (isOp) m.setAlphaMode('MASK').setAlphaCutoff(0.5).setDoubleSided(true);
}
for (const t of root.listTextures()) if (!t.getImage() || t.getImage().byteLength < 200) t.dispose();
await doc.transform(prune(), dedup(), quantize({quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 12, quantizeWeight: 8}), meshopt({encoder: MeshoptEncoder, level: 'medium'}));
await io.write(OUT, doc);
console.log('wrote', fs.statSync(OUT).size, 'bytes');
fs.writeFileSync(OUT.replace(/\.glb$/, '.targets.json'), JSON.stringify({names: names.map(n => n.split('.').pop()), order_check: report}, null, 1));
