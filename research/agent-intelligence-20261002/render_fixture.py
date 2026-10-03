from pathlib import Path
import importlib.util
root=Path(__file__).resolve().parents[2]
p=root/'backend/sportsassets/api/agent_capability_panel.py'
s=importlib.util.spec_from_file_location('panel',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
output=Path(__file__).parent
(output/'panel.generated.js').write_text(m.JS)
(output/'panel.generated.html').write_text('<html><head><style>body{margin:0;padding:16px;background:#101820;font-family:Arial}'+m.CSS+'</style></head><body data-kind="derek"><div class="office-board"></div></body></html>')
