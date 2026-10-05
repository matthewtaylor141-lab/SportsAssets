/** Accessible architectural office map. Every seat is a real button. */
const POS=[[22,43,18,37],[43,43,50,37],[64,43,82,37],[84,43,18,64],[15,74,50,64],[38,74,82,64],[62,74,34,86],[84,74,69,86]];
export function architecture(){return `<svg class="architecture" viewBox="0 0 1000 470" preserveAspectRatio="none" aria-hidden="true">
<defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#c2e2f3"/><stop offset=".7" stop-color="#f4faff"/><stop offset="1" stop-color="#d0e5ed"/></linearGradient><linearGradient id="floor" x2=".7" y2="1"><stop stop-color="#f0ece5"/><stop offset="1" stop-color="#d9e3e9"/></linearGradient><linearGradient id="wall" x2="1" y2="1"><stop stop-color="#fdfcf7"/><stop offset="1" stop-color="#e3eaf0"/></linearGradient><linearGradient id="glass" x2="1" y2="1"><stop stop-color="#e8f5fb" stop-opacity=".2"/><stop offset="1" stop-color="#fff" stop-opacity=".7"/></linearGradient><pattern id="tiles" width="115" height="65" patternUnits="userSpaceOnUse" patternTransform="skewX(-25)"><path d="M115 0H0V65" fill="none" stroke="#b1c0cb" stroke-opacity=".22" stroke-width="1"/></pattern><filter id="soft"><feGaussianBlur stdDeviation="8"/></filter></defs>
<rect width="1000" height="470" fill="url(#sky)"/>
<path d="M0 120 L500 165 L1000 112 L1000 470 H0Z" fill="url(#floor)"/>
<path d="M0 120 L500 165 L1000 112 L1000 470 H0Z" fill="url(#tiles)"/>
<rect x="0" y="0" width="1000" height="19" fill="#f8f7f2"/><path d="M0 19H1000L904 39H96Z" fill="#e5e9e8"/>
<path d="M85 31H915" stroke="#fffdf3" stroke-width="6"/><path d="M140 42H860" stroke="#ffffff" stroke-width="3" opacity=".8"/>
<path d="M0 62H290V117H0" fill="#b0c9d2" opacity=".28"/>
<path d="M0 104H410L370 92H333L309 100H265V62H245V100H212V82H201V104H171V90H162V98H127V76H112V96H92V55H80V100H52V88H35V105Z" fill="#9fb9c6" opacity=".42"/>
<path d="M0 120H430" stroke="#acd6e4" stroke-width="5"/><path d="M0 129H425" stroke="#d8f3f8" stroke-width="9"/>
<rect x="434" y="30" width="222" height="123" fill="url(#wall)"/>
<path d="M656 30H1000V155H656Z" fill="url(#glass)"/>
<path d="M0 29V146 M95 32V148 M210 33V152 M321 34V155 M430 30V161 M657 30V151 M770 30V147 M882 28V143 M997 22V137" stroke="#dae2e4" stroke-width="8"/>
<path d="M15 147L998 134" stroke="#8da7b4" stroke-width="3" opacity=".5"/>
<path d="M73 50L27 125 M180 41L109 133 M802 43L726 135 M929 42L853 133" stroke="#ffffff" stroke-width="24" opacity=".26"/>
<ellipse cx="528" cy="422" rx="178" ry="32" fill="#b6c9d5" opacity=".22"/><ellipse cx="528" cy="413" rx="167" ry="29" fill="#dae5eb"/><ellipse cx="528" cy="410" rx="164" ry="27" fill="none" stroke="#b2c6d3" stroke-dasharray="5 4"/>
<text x="528" y="415" fill="#8a9fac" font-size="13" letter-spacing="6" text-anchor="middle" font-family="system-ui">BETTOR HQ</text>
<g fill="#639687"><ellipse cx="27" cy="182" rx="14" ry="33" transform="rotate(-22 27 182)"/><ellipse cx="50" cy="174" rx="12" ry="35" transform="rotate(23 50 174)"/><ellipse cx="37" cy="168" rx="11" ry="32"/><ellipse cx="969" cy="175" rx="13" ry="36" transform="rotate(26 969 175)"/><ellipse cx="951" cy="179" rx="12" ry="32" transform="rotate(-22 951 179)"/></g><g fill="#c9c6b8"><path d="M24 200H53L49 239H28Z"/><path d="M946 198H976L972 238H950Z"/></g>
<path d="M605 152L773 248L998 192" stroke="#f4f8fa" stroke-width="14" opacity=".28"/>
</svg>`;}
export function officeHTML(vm,{escape,portrait,pill,large=false}={}){
 const a=vm.agents;
 return `<div class="office ${large?'office-large':''}" id="office-map" aria-label="Interactive BettorToken virtual office">${architecture()}
 <span class="room-caption"><i class="signal"></i> Virtual headquarters · recorded states</span><img class="room-wordmark" src="/brand/bettortoken-logo.png" alt="BettorToken">
 <div class="office-controls"><button class="button" data-action="office-fullscreen">Expand</button></div>
 ${a.map((x,i)=>{const [px,py]=POS[i]; const mx=i%2?72:28,my=27+Math.floor(i/2)*18;return `<button class="room-seat ${['WORKING_ON','REVIEWING','CHALLENGING'].includes(x.state)?'working':''} ${!x.registered?'absent':''}" style="--x:${px}%;--y:${py}%;--mx:${mx}%;--my:${my}%" data-action="agent" data-id="${x.slug}" aria-label="Open ${escape(x.name)}: ${escape(x.state)}"><span class="desk-top"></span><span class="monitor"></span>${portrait(x)}<span class="nameplate"><b><i></i>${escape(x.name)}</b><small>${escape(x.registered?x.state.replaceAll('_',' ').toLowerCase():'Not reported')}</small></span></button>`;}).join('')}
 <div class="office-footer"><span>No simulated activity · click an agent’s desk</span><button data-go="events">Watching real events ↗</button></div></div>`;
}
