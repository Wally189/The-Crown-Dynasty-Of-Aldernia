(() => {
  'use strict';
  const script = document.currentScript;
  const source = script?.dataset.calendar;
  const target = document.getElementById('today-events');
  if (!source || !target) return;
  const dateFmt = new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/London', weekday:'short', day:'numeric', month:'short'});
  const londonDate = () => {
    const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/London',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const p = Object.fromEntries(parts.map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  };
  const safeDate = value => new Date(value + 'T12:00:00Z');
  fetch(source,{cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject(new Error('calendar unavailable'))).then(data=>{
    const today=londonDate();
    const items=(data.events||[]).filter(e=>e.date>=today).slice(0,6);
    if(!items.length){target.innerHTML='<p>No scheduled public-world item is due. The country is allowed a quiet day.</p>';return;}
    target.innerHTML='';
    for(const e of items){
      const article=document.createElement('article'); article.className='event';
      const when=document.createElement('p'); when.className='event-date'; when.textContent=(e.date===today?'Today · ':'')+dateFmt.format(safeDate(e.date));
      const h=document.createElement('h3'); h.textContent=e.title;
      const p=document.createElement('p'); p.textContent=e.summary;
      const meta=document.createElement('p'); meta.className='small'; meta.textContent=`${e.region} · ${e.status.toLowerCase()} · fictional calendar`;
      article.append(when,h,p,meta); target.append(article);
    }
  }).catch(()=>{target.innerHTML='<p>The public calendar could not be loaded. No event has been inferred from the failure.</p>';});
})();