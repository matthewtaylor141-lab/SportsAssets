#!/usr/bin/env python3
"""Wire every Command page to the one BettorToken brand source.

Idempotent: a second run changes nothing. For each command/*.html it makes
sure the favicon, the app icon and brand/brand.css are linked (brand.css
LAST in <head>, so its marks win over older page styles). Run it again
whenever a page is added.
"""
from pathlib import Path

CMD = Path(__file__).resolve().parents[1]
BLOCK_START = "<!-- bt-brand: the one BettorToken brand source (brand/install_brand.py) -->"
BLOCK = (BLOCK_START + "\n"
         '<link rel="icon" href="brand/favicon.ico" sizes="any" data-brand="icon">\n'
         '<link rel="icon" type="image/png" sizes="32x32" href="brand/bettortoken-app-icon-32.png" data-brand="icon32">\n'
         '<link rel="apple-touch-icon" href="brand/bettortoken-app-icon-180.png" data-brand="touch">\n'
         '<link rel="stylesheet" href="brand/brand.css" data-brand="css">\n')

changed = []
for page in sorted(CMD.glob("*.html")):
    s = page.read_text()
    if BLOCK_START in s:
        # keep exactly one block, and keep it last in <head>
        a = s.index(BLOCK_START)
        b = s.index('data-brand="css">\n', a) + len('data-brand="css">\n')
        cur = s[a:b]
        rest = s[:a] + s[b:]
        head_end = rest.lower().index("</head>")
        new = rest[:head_end] + BLOCK + rest[head_end:]
        if new != s:
            page.write_text(new); changed.append(page.name + " (moved last)")
        continue
    i = s.lower().find("</head>")
    if i < 0:
        continue
    page.write_text(s[:i] + BLOCK + s[i:])
    changed.append(page.name)

print("BettorToken brand wired")
for c in changed:
    print(" +", c)
