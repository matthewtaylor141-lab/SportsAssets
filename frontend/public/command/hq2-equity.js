(function(){
  'use strict'; if(window.__BTHQ2Equity)return;window.__BTHQ2Equity=true;
  function start(){
    if(!window.BTEquityWall){setTimeout(start,300);return;}
    var host=document.querySelector('.btew-host');if(!host){setTimeout(start,500);return;}
    var pill=document.createElement('div');pill.className='bt-equity2-pulse';pill.innerHTML='<i></i><span>Capital pulse · waiting for recorded mark state</span>';host.insertBefore(pill,host.firstChild);
    var lastSeq=null;
    window.BTEquityWall.subscribe(function(st){
      var p=st&&st.live&&st.live.paper;var stale=p&&p.status==='STALE';host.classList.toggle('bt-eq-stale',!!stale);pill.classList.toggle('stale',!!stale);
      var text=!p?'Capital pulse · unavailable':(stale?'No new mark · line frozen at last genuine input':'Recorded mark feed · '+(p.status||'OK'));
      pill.querySelector('span').textContent=text;
      if(lastSeq!=null&&st.seq!==lastSeq){pill.classList.remove('changed');void pill.offsetWidth;pill.classList.add('changed');}
      lastSeq=st.seq;
    });
  } start();
})();
