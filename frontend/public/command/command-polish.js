/* Additive, credential-free navigation. No portfolio reads or state changes. */
(function(){'use strict';if(document.querySelector('.bt-trader-launch'))return;
const link=document.createElement('a');link.className='bt-trader-launch';
link.href='trader.html';link.setAttribute('aria-label','Open read-only Trader Mode');
link.append(document.createTextNode('↗ Trader Mode '));const note=document.createElement('span');
note.textContent='Watch Xavier work';link.append(note);
const nav=document.getElementById('hq-nav')||document.querySelector('.fl-tools');
if(nav){link.classList.add('bt-trader-launch--nav');nav.append(link);}else document.body.append(link);
})();
