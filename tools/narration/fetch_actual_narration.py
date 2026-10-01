"""Run only in the existing authorized runner; retrieves speech for stored answers.
Never prints credentials or sends an order, chat request, directive or policy change.
"""
import hashlib, json, os, pathlib, urllib.request, urllib.error, zipfile
root=pathlib.Path(__file__).resolve().parent
manifest=json.loads((root/'narration-manifest.json').read_text())
token=os.environ.get('ADMIN_TOKEN')
if not token: raise SystemExit('ADMIN_TOKEN is not configured in this runner.')
out=root/'actual-narration';out.mkdir(mode=0o700,exist_ok=True)
receipts=[]
for c in manifest['chapters']:
 agent=c['agent']
 if agent not in ('derek','xavier','audrey'):raise SystemExit('Invalid agent.')
 url='https://command.bettortoken.com/api/command/agents/'+agent+'/speak'
 req=urllib.request.Request(url,data=json.dumps({'message_id':c['message_id']}).encode(),headers={'Content-Type':'application/json','X-Admin-Token':token},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=120) as r:
   mime=r.headers.get('Content-Type','')
   if not mime.startswith('audio/'):raise SystemExit('Speech did not return audio for '+agent)
   data=r.read(20_000_001)
   if not data or len(data)>20_000_000:raise SystemExit('Invalid speech payload size.')
   file=out/(str(c['chapter']).zfill(2)+'-'+agent+'.mp3');file.write_bytes(data)
   receipts.append(dict(c,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),voice_id=r.headers.get('X-Speech-Voice-Id'),persona_version=r.headers.get('X-Speech-Persona-Version'),spoken_sha256=r.headers.get('X-Speech-Spoken-Sha256')))
 except urllib.error.HTTPError as e:raise SystemExit('Speech returned HTTP '+str(e.code)+' for '+agent)
 print('Saved chapter',c['chapter']+1,agent,len(data),'bytes')
(out/'manifest.json').write_text(json.dumps({'source':manifest['source'],'chapters':receipts},indent=2))
with zipfile.ZipFile(root/'Actual-Agent-Narration.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in out.iterdir():z.write(p,p.name)
print('Ready: Actual-Agent-Narration.zip. Return this audio artifact to Codex for final rendering.')
