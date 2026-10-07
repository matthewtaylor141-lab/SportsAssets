/* Additive, credential-free navigation. No portfolio reads or state changes. */
(function(){'use strict';if(document.querySelector('.bt-trader-launch'))return;
const link=document.createElement('a');link.className='bt-trader-launch';
link.href='/trader';link.setAttribute('aria-label','Open read-only Trader Mode');
link.append(document.createTextNode('↗ Trader Mode '));const note=document.createElement('span');
note.textContent='Watch Xavier work';link.append(note);

const mobile=!!(window.matchMedia&&matchMedia('(max-width: 760px)').matches);
const tabbar=document.getElementById('hq-tabbar');
// The root mobile Command hides #hq-nav. Never insert the only Trader Mode
// launcher into a container that CSS deliberately removes on phones.
const nav=mobile?null:(document.getElementById('hq-nav')||document.querySelector('.fl-tools'));
// ...nor into one hidden at THIS size: /floor hides .fl-tools on any
// coarse-pointer screen up to 1024 px (iPhone landscape is 844 px wide)
const shown=(e)=>{const cs=getComputedStyle(e),r=e.getBoundingClientRect();
  return cs.display!=='none'&&cs.visibility!=='hidden'&&r.width>0&&r.height>0;};
const host=nav&&shown(nav)?nav:null;
if(host){
  link.classList.add('bt-trader-launch--nav');
  host.append(link);
}else{
  link.classList.add(tabbar?'bt-trader-launch--mobile-docked':'bt-trader-launch--mobile');
  document.body.append(link);
}
// /floor (and any page) may carry its own fixed bottom bar (.bt-hq2-nav)
// instead of #hq-tabbar: measure whichever bar is really at the bottom and
// keep the launcher above it, so it is never underneath a navigation bar.
if(!host){
  const dock=()=>{
    // bottom bars, and the floor's team dock (#roster.fl-roster, absolutely
    // positioned at the bottom of the room): the launcher sits above them all
    const bars=[tabbar,...document.querySelectorAll('.bt-hq2-nav'),...document.querySelectorAll('.fl-roster')].filter(Boolean);
    let lift=0;
    for(const b0 of bars){
      // the bar or its nearest fixed / sticky container (.bt-hq2-nav sits
      // inside the fixed aside.bt-hq2-shell)
      let b=b0;
      while(b&&b!==document.body&&!/^(fixed|sticky|absolute)$/.test(getComputedStyle(b).position))b=b.parentElement;
      if(!b||b===document.body)continue;
      const r=b.getBoundingClientRect(),cs=getComputedStyle(b);
      if(cs.display==='none'||cs.visibility==='hidden'||!r.height)continue;
      if(r.top>innerHeight/2)lift=Math.max(lift,innerHeight-r.top);
    }
    if(lift>0){link.classList.add('bt-trader-launch--mobile-docked');link.style.bottom=Math.round(lift+12)+'px';}
  };
  dock();addEventListener('resize',dock,{passive:true});addEventListener('load',dock);setTimeout(dock,800);
}
})();
