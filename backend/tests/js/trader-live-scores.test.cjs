'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const S = require(path.join(__dirname,'../../../frontend/public/command/trader-live-scores.js'));
const now = 1791412800;
function p(overrides={}) { return {venue:'POLYMARKET_US',event_id:'testgame',title:'SYNTHETIC TEST',game:{
  schema:S.SCHEMA,venue:'POLYMARKET_US',event_id:'testgame',identity_verified:true,
  source:'ESPN',source_at:null,received_at:now,expires_at:now+15,status:'CURRENT',game_status:'IN_PROGRESS',
  home:'Boston Red Sox',away:'New York Yankees',home_id:'2',away_id:'10',home_score:4,away_score:3,
  home_abbreviation:'BOS',away_abbreviation:'NYY',home_logo:'https://a.espncdn.com/i/teamlogos/mlb/500/bos.png',
  league:'MLB',period:7,inning:7,inning_half:'TOP',bases:[true,false,false],outs:1,balls:2,strikes:1,
  display_clock:'5:21',last_play:'Synthetic play',...overrides}}; }
test('uses provider supplied logo',()=>assert.match(S.render(p(),false,now),/a\.espncdn\.com\/i\/teamlogos\/mlb\/500\/bos.png/));
test('renders scores and inning',()=>{const h=S.render(p(),false,now);assert.match(h,/Top 7/);assert.match(h,/>4<\/strong>/)});
test('does not interpolate clock',()=>assert.match(S.render(p(),false,now+5),/5:21 <small>reported/));
test('retrieval only clearly disclosed',()=>assert.match(S.render(p(),false,now),/provider update time not supplied/));
test('stale after expiry',()=>assert.match(S.render(p(),false,now+20),/STALE/));
test('invalid identity cannot render scores',()=>assert.match(S.render(p({identity_verified:false}),false,now),/Live score unavailable/));
test('cross venue forbidden',()=>assert.match(S.render(p({venue:'KALSHI'}),false,now),/Live score unavailable/));
test('legacy schema uses existing renderer',()=>assert.equal(S.render(p({schema:'legacy'})),null));
test('unavailable is not zero score',()=>{const h=S.render(p({status:'UNAVAILABLE',why:'NO_MAPPING'}),false,now);assert.match(h,/NO_MAPPING/);assert.doesNotMatch(h,/team-score/)});
test('scores are HTML escaped by numeric validation',()=>{const h=S.render(p({home_score:'<script>bad</script>'}),false,now);assert.doesNotMatch(h,/<script>/);assert.match(h,/—/)});
test('names and play text escaped',()=>{const h=S.render(p({home:'<img onerror=alert(1)>',last_play:'<script>bad</script>'}),false,now);assert.doesNotMatch(h,/<script>/);assert.match(h,/&lt;script&gt;/)});
test('unsafe logo blocked',()=>assert.equal(S.safeLogo('https://evil.example/logo.png'),null));
test('logo username trick blocked',()=>assert.equal(S.safeLogo('https://evil@a.espncdn.com/i/teamlogos/a.png'),null));
test('logo path traversal blocked',()=>assert.equal(S.safeLogo('https://a.espncdn.com/i/teamlogos/../x.png'),null));
test('final state has no settlement implication',()=>assert.match(S.render(p({game_status:'FINAL'}),false,now),/settlement separate/));
test('fallback attribution explicit',()=>assert.match(S.render(p({source:'THE_ODDS_API',source_at:now-10}),false,now),/The Odds API · fallback/));
test('scheduled missing scores not fabricated',()=>{const h=S.render(p({game_status:'SCHEDULED',home_score:null,away_score:null}),false,now);assert.match(h,/Scheduled/);assert.doesNotMatch(h,/>0<\/strong>/)});
test('future receipt not current',()=>assert.equal(S.isCurrent(p({received_at:now+10}).game,now),false));
test('known source timestamp displayed',()=>assert.match(S.sourceText(p({source_at:now-8}).game,now),/provider update 8s ago/));
test('unknown base state no empty-base invention',()=>assert.doesNotMatch(S.render(p({bases:null}),false,now),/aria-label="Bases/));
