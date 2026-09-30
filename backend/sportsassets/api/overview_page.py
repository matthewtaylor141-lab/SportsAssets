"""THE MANAGEMENT OVERVIEW PAGE. One same-origin fetch of /api/command/overview;
every section is drawn with its status and, when empty or unavailable, the
named reason. No external asset, no mutation."""

OVERVIEW_PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operations Overview</title>
<style>
:root{--bg:#f6f7f5;--fg:#18201c;--mute:#5d6862;--card:#fff;--line:#d9ded9;
--ok:#1f7a4d;--empty:#8a6a12;--bad:#a8322b;--accent:#2b5c8a}
@media (prefers-color-scheme:dark){:root{--bg:#121714;--fg:#e6ebe7;--mute:#9aa59f;
--card:#1b221e;--line:#2c3630;--ok:#5cc58f;--empty:#d9b54a;--bad:#ef7b72;--accent:#7fb3e0}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 18px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:0 0 14px}
h2{font-size:16px;margin:0 0 8px;display:flex;gap:10px;align-items:center}
.pill{font-size:11px;letter-spacing:.06em;padding:2px 8px;border-radius:99px;border:1px solid currentColor}
.OK{color:var(--ok)}.EMPTY{color:var(--empty)}.UNAVAILABLE{color:var(--bad)}
.why{color:var(--mute);margin:4px 0 8px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.wrap{overflow-x:auto}.kv td:first-child{color:var(--mute);width:34%}
.verdict{font-weight:600;margin:6px 0}
ul{margin:4px 0 8px 18px;padding:0}li{margin:2px 0}
code{font-size:12px}
</style></head><body><main>
<h1>Operations Overview</h1>
<p class="sub" id="sub">Reading production...</p>
<div id="out"></div>
</main><script>
var API="/api/command/overview";
function esc(s){return String(s==null?"—":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function kv(o){var h='<table class="kv">';for(var k in o){var v=o[k];if(v!==null&&typeof v==="object")v=JSON.stringify(v);h+='<tr><td>'+esc(k)+'</td><td>'+esc(v)+'</td></tr>'}return h+'</table>'}
function rows(a){if(!a||!a.length)return'<p class="why">none</p>';var ks=Object.keys(a[0]);var h='<div class="wrap"><table><tr>';ks.forEach(function(k){h+='<th>'+esc(k)+'</th>'});h+='</tr>';a.forEach(function(r){h+='<tr>';ks.forEach(function(k){var v=r[k];if(v!==null&&typeof v==="object")v=JSON.stringify(v);h+='<td>'+esc(v)+'</td>'});h+='</tr>'});return h+'</table></div>'}
function sec(title,s,body){return '<section><h2>'+esc(title)+' <span class="pill '+esc(s.status)+'">'+esc(s.status)+'</span></h2>'+(s.why?'<p class="why">'+esc(s.why)+'</p>':'')+(body||'')+'</section>'}
function launch(s){var d=s.data||{};var h='<p class="verdict">'+esc(s.verdict)+'</p>';var bo=d.by_owner||{};var cats=bo.categories||{};
for(var c in cats){var l=cats[c];if(!l.length)continue;h+='<h3 style="font-size:14px;margin:10px 0 4px">'+esc(c)+' ('+l.length+')</h3><ul>';l.forEach(function(e){h+='<li><b>'+esc(e.id||e.name||e.prerequisite)+'</b> — '+esc(e.state||(e.met===false?"UNMET":e.met))+(e.why?': '+esc(e.why):'')+'</li>'});h+='</ul>'}
if((bo.unclassified||[]).length){h+='<h3 style="font-size:14px">UNCLASSIFIED</h3>'+rows(bo.unclassified)}
return h}
async function go(){try{var r=await fetch(API,{credentials:"same-origin",cache:"no-store"});
if(r.status===401){document.getElementById("sub").textContent="Locked: open the desk and unlock COMMAND, then reload.";return}
if(!r.ok){document.getElementById("sub").textContent="UNAVAILABLE: overview read returned HTTP "+r.status;return}
var o=await r.json();var b=(o.build_and_mode.data||{});
document.getElementById("sub").textContent="Read "+new Date(o.read_at*1000).toISOString()+" · serving "+(b.serving_commit||"unknown build")+" · "+(b.funded_submission||"");
var h="";
h+=sec("Build and operating mode",o.build_and_mode,kv(o.build_and_mode.data||{}));
var ob=o.observations,od=ob.data||{};
h+=sec("Pair observations",ob,kv({total:od.total,fixtures:od.fixtures,last_24h:od.last_24h,meaning:od.meaning})+rows(od.by_admission_and_label)+'<p class="why">Attempts in the last 24 h, by conclusion</p>'+rows(od.attempts_24h_by_conclusion));
var ca=o.calibration;h+=sec("Calibration progress",ca,kv(ca.data||{}));
var xv=o.xavier;h+=sec("Xavier decisions (7 days)",xv,rows((xv.data||{}).decisions_7d)+kv({daily_review:(xv.data||{}).daily_review}));
var ex=o.execution;h+=sec("Execution status",ex,rows((ex.data||{}).funded_intents_by_state)+rows((ex.data||{}).execution_events));
var ac=o.account;h+=sec("Venue account",ac,Array.isArray(ac.data)?rows(ac.data):"");
h+=sec("Funded launch",o.funded_launch,launch(o.funded_launch));
document.getElementById("out").innerHTML=h}catch(e){document.getElementById("sub").textContent="UNAVAILABLE: "+e}}
go();setInterval(go,60000);
</script></body></html>"""
