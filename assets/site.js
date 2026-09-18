(()=>{
const menu=document.querySelector('.menu');
const nav=document.getElementById('primary-nav');
if(menu&&nav){
 const close=()=>{nav.classList.remove('open');menu.setAttribute('aria-expanded','false');};
 menu.addEventListener('click',()=>{const open=nav.classList.toggle('open');menu.setAttribute('aria-expanded',String(open));if(open){const first=nav.querySelector('a');if(first) first.focus();}});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&nav.classList.contains('open')){close();menu.focus();}});
 window.addEventListener('resize',()=>{if(window.innerWidth>850) close();});
}
document.querySelectorAll('[data-demo-vote]').forEach(box=>{
 box.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
  const out=box.querySelector('.result');
  if(out) out.textContent='Demo choice: '+b.dataset.choice+'. No vote has been transmitted or counted. The public civic register is not yet open.';
 }));
});
document.querySelectorAll('[data-region]').forEach(btn=>btn.addEventListener('click',()=>{
 const target=document.getElementById('region-detail');
 if(target){target.innerHTML='<strong>'+btn.dataset.region+'</strong><br>'+btn.dataset.copy;target.focus();}
}));
})();