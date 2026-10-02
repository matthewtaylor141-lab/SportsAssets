import asyncio,json,time,threading
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  k=self.path.strip('/');p=OUT/'preview'/f'{k}.html'
  if self.path.startswith('/api/command/agents/static/mlb-logo-'):p=ROOT/'backend/sportsassets/assets/agents'/self.path.split('/')[-1]
  if p.is_file():
   self.send_response(200);self.send_header('Content-Type','image/svg+xml' if p.suffix=='.svg' else 'text/html');self.end_headers();self.wfile.write(p.read_bytes())
  else:self.send_response(503);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"status":"UNAVAILABLE","why":"Synthetic layout fixture"}')
 def log_message(self,*a):pass
S=ThreadingHTTPServer(('127.0.0.1',8793),Handler);threading.Thread(target=S.serve_forever,daemon=True).start()
def sec(d):return {'status':'OK','data':d}
def fixture(k):
 now=time.time();account={'cash_usd':497000,'reserved_usd':0,'available_usd':497000,'open_position_value_usd':3090,'total_equity_usd':500090,'realized_pnl_usd':0,'unrealized_pnl_usd':90,'ledger_consistent':True,'marks_complete':True}
 teams=[('San Diego Padres','sd',135),('Philadelphia Phillies','phi',143),('Milwaukee Brewers','mil',158)]
 ps=[{'group_id':f'fixture-{i}','strategy':'PINNACLE_EXPLORATION_PAPER','us_market_slug':f'aec-mlb-{abbr}-test','label':{'participant':name,'event':'Synthetic MLB game','sport_family':'baseball'},'remaining':{'open_qty':2000,'cost_basis_usd':1000,'marked_value_usd':1030,'unrealized_pnl_usd':30,'open_management_orders':1},'recommendation':{'recommendation':'HOLD','reviewed_at':now-20,'selection':{'selection_reason':'Recorded test rationale: retain protection pending fresh evidence.'},'action':{'taken':'KEEP_STANDING'}}} for i,(name,abbr,_) in enumerate(teams)]
 orders=[{'group_id':p['group_id'],'order_id':'order-'+p['group_id'],'open':True,'qty':2000,'filled_qty':100,'limit_price':.55,'direction':'SELL','state':'OPEN','role':'PROTECTION'} for p in ps]
 ds=[{'decision_id':'dec-'+str(i),'strategy':'PINNACLE_EXPLORATION_PAPER','policy_version':'V3','label':p['label'],'us_market_slug':p['us_market_slug'],'verdict':'ENTER' if i==0 else 'REFUSE','edge_pp':.8-i*.2,'net_ev_usd':2-i*4,'purchase_price':.5,'fees_usd':12,'p_pinnacle':.51,'decided_at':now-20,'refusal_words':'Fees exceed the measured edge.'} for i,p in enumerate(ps)]
 recs=[{'recommendation_id':'rec-'+str(i),'kind':'QUOTE_FRESHNESS','recommendation':'Investigate source freshness and compare matched outcomes.','owner_agent':'DEREK','status':'OPEN','created_at':now-30,'events':[{'actor':'SYSTEM','kind':'AUTOMATED_ACKNOWLEDGEMENT','body':'Recorded receipt','at':now-30}]} for i in range(3)]
 return {'account_id':'synthetic','account':sec(account),'owned_positions':sec(ps),'standing_orders':sec(orders),'exposure':sec({'cost_basis_at_risk_usd':3000,'realized_pnl_usd':0}),'strategies':[{'version':'V3','title':'Training','recent':sec(ds)}],'standing_entry_orders':sec([]),'operational_audit':sec(recs),'freshness':{'agent_heartbeat':sec({'heartbeat_at':now-20,'stale_after_s':120})}}
async def main():
 checks=[]
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/tmp/office-chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
  for width in [1440,390]:
   page=await b.new_page(viewport={'width':width,'height':1000},device_scale_factor=1)
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   for kind in ['xavier','derek','audrey']:
    await page.goto('http://127.0.0.1:8793/'+kind);await page.wait_for_function('!!window.CCManagement');j=fixture(kind)
    await page.evaluate('(j)=>CCOffice.render({json:j,okAt:Date.now()/1000})',j)
    assert await page.locator('.mg-select').count()==3
    await page.locator('.mg-select').nth(0).click();await page.locator('.mg-select').nth(1).click()
    assert await page.locator('.mg-grid article').count()==2
    await page.locator('[data-mg-ask]').first.click();assert ('fixture-0' if kind=='xavier' else 'dec-0' if kind=='derek' else 'rec-0') in await page.locator('#talk-in').input_value()
    if width==390:await page.locator('.desk-conversation-close').click()
    await page.locator('[data-mg-view=learning]').click();assert await page.locator('.cap-panel').is_visible();assert not await page.locator('#office-content').is_visible()
    await page.locator('[data-mg-view=work]').click();await page.locator('#mg-density').click()
    await page.evaluate('()=>CCOffice.render({fail:{why:"Synthetic read failure",state:"UNAVAILABLE"}})')
    assert 'STALE' in await page.locator('.mg-note').inner_text();assert await page.locator('.mg-grid article').count()==2
    async with page.expect_download() as dl:await page.locator('#mg-export').click()
    d=await dl.value;path=OUT/f'{kind}-{width}-brief.html';await d.save_as(path);report=path.read_text();assert 'STALE' in report and 'Simulated execution' in report
    overflow=await page.evaluate('()=>document.documentElement.scrollWidth>innerWidth+1');assert not overflow,(kind,width)
    await page.screenshot(path=str(OUT/f'{kind}-{width}.png'),full_page=True)
    checks.append({'kind':kind,'width':width,'compare':True,'draft':True,'tabs':True,'stale_retained':True,'brief':True,'no_overflow':True})
   assert not errors,errors
   await page.close()
  await b.close()
 (OUT/'browser-receipt.json').write_text(json.dumps({'passed':True,'checks':checks,'basis':'Synthetic records in real page template. Not live acceptance; avatar module unavailable in local fixture.'},indent=2));print(json.dumps(checks))
try:asyncio.run(main())
finally:S.shutdown()
