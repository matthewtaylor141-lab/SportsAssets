"""Idempotently add the two v6 assets to the existing, current homepage shell."""
from pathlib import Path
p=Path('frontend/public/command/index.html');s=p.read_text()
for marker,addition in [('  <link rel="stylesheet" href="office.css">','\n  <link rel="stylesheet" href="management-v6.css">'),('  <script src="office.js"></script>','\n  <script src="management-v6.js"></script>')]:
 if addition.strip() in s:continue
 if s.count(marker)!=1:raise SystemExit('Homepage anchor changed; merge manually: '+marker)
 s=s.replace(marker,marker+addition,1)
p.write_text(s)
print('Homepage assets linked; existing page/data/chat logic preserved.')
