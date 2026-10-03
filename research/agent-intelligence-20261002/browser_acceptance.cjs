const { chromium }=require('playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true});let checks=[];
 for(const width of [1440,390]){
  const page=await browser.newPage({viewport:{width,height:900},acceptDownloads:true});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(require('path').join(__dirname,'panel.generated.html'),'utf8')});
   let data={status:'OK',read_at:100,control:{enabled:true,hourly_limit:24},work:[],work_limit:60,tool_names:['missed_opportunities','cross_venue','forecast_evaluation','connector_health']};
   if(u.pathname.includes('/tools/')){
    const name=u.pathname.split('/').pop();data={status:'OK',read_at:100,tool:name,data:{status:'OK',distinct_candidate_keys:3,latest_refused_before_valuation:2,unidentified_rows:0,sample:{truncated:true,rows:500},limitation:'Recorded sample; lost profit unknown'}};
    if(name==='cross_venue')data.data={status:'UNAVAILABLE',reason:'NO_VERIFIED_CROSS_VENUE_SNAPSHOT'};
    if(name==='connector_health')return route.fulfill({status:503,contentType:'application/json',body:'{"detail":"test provider unavailable"}'});
   }
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://office.test/');await page.addScriptTag({content:fs.readFileSync(require('path').join(__dirname,'panel.generated.js'),'utf8')});
  await page.getByRole('tab',{name:'Intelligence',exact:true}).click();
  await page.getByRole('button',{name:'Read report',exact:true}).click();
  await page.getByText('Distinct candidate keys',{exact:true}).waitFor();
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download report',exact:true}).click();await download;
  await page.locator('#cap-intelligence select').selectOption('cross_venue');await page.getByRole('button',{name:'Read report',exact:true}).click();
  await page.getByText('No report evidence: NO_VERIFIED_CROSS_VENUE_SNAPSHOT',{exact:true}).waitFor();
  await page.locator('#cap-intelligence select').selectOption('connector_health');await page.getByRole('button',{name:'Read report',exact:true}).click();
  await page.getByText('Report unavailable: test provider unavailable',{exact:true}).waitFor();
  if(errors.length)throw Error(errors.join('\n'));
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('horizontal overflow '+width);
  checks.push({width,report:true,download:true,missing_snapshot:true,error_state:true,js_errors:errors.length});await page.close();
 }
 await browser.close();console.log(JSON.stringify(checks));
})().catch(e=>{console.error(e);process.exit(1)});
