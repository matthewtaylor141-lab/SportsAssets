"""Downscale Rocketbox 2K TGA maps for tools/rocketbox_to_glb.mjs.

    python rocketbox_textures.py <textures dir> <prefix e.g. m002> <out dir>

Colour: head 2048, body 1024 (JPEG q86). Normal: 1024 (JPEG q90).
Specular -> glTF metallicRoughness (G = roughness = 0.85 - 0.5 * specular,
clamped to 0.35..0.85 so skin reads as skin, not plastic; B = metal = 0).
"""
import os
import sys

from PIL import Image


def main(src, prefix, out):
    os.makedirs(out, exist_ok=True)
    for part, size_c in (("head", 2048), ("body", 1024)):
        c = Image.open(os.path.join(src, "%s_%s_color.tga" % (prefix, part))).convert("RGB")
        c.resize((size_c, size_c), Image.LANCZOS).save(os.path.join(out, "%s_color.jpg" % part), quality=86, optimize=True)
        n = Image.open(os.path.join(src, "%s_%s_normal.tga" % (prefix, part))).convert("RGB")
        n.resize((1024, 1024), Image.LANCZOS).save(os.path.join(out, "%s_normal.jpg" % part), quality=90, optimize=True)
        s = Image.open(os.path.join(src, "%s_%s_specular.tga" % (prefix, part))).convert("L").resize((1024, 1024), Image.LANCZOS)
        rough = s.point(lambda v: int(255 * max(0.35, min(0.85, 0.85 - 0.5 * v / 255.0))))
        zero = Image.new("L", rough.size, 0)
        Image.merge("RGB", (zero, rough, zero)).save(os.path.join(out, "%s_mr.jpg" % part), quality=90, optimize=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
