(() => {
  'use strict';
  const script = document.currentScript;
  const source = script?.dataset.calendar;
  const target = document.getElementById('today-events');
  const homeTarget = document.getElementById('home-events');
  if (!source || (!target && !homeTarget)) return;

  const dateFmt = new Intl.DateTimeFormat('en-GB', {
    timeZone:'Europe/London', weekday:'short', day:'numeric', month:'short'
  });
  const londonDate = () => {
    const parts = new Intl.DateTimeFormat('en-CA',{
      timeZone:'Europe/London',year:'numeric',month:'2-digit',day:'2-digit'
    }).formatToParts(new Date());
    const p = Object.fromEntries(parts.map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  };
  const safeDate = value => new Date(value + 'T12:00:00Z');
  const isoWeekday = date => {
    const day = date.getUTCDay();
    return day === 0 ? 7 : day;
  };
  const dateString = date => date.toISOString().slice(0,10);

  const recurringItems = (calendar, today) => {
    const recurring = calendar.recurring_weekly;
    if (!recurring || !Array.isArray(recurring.beats) || !recurring.effective_from) return [];
    const start = today > recurring.effective_from ? today : recurring.effective_from;
    const cursor = safeDate(start);
    const beats = new Map(recurring.beats.map(beat => [beat.weekday, beat]));
    const items = [];
    for (let offset = 0; offset < 14 && items.length < 7; offset += 1) {
      const date = new Date(cursor.getTime() + offset * 86400000);
      const beat = beats.get(isoWeekday(date));
      if (!beat) continue;
      items.push({
        ...beat,
        id: `ALD-RHYTHM-${dateString(date)}`,
        date: dateString(date),
        status: 'RHYTHM'
      });
    }
    return items;
  };

  fetch(source,{cache:'no-store'})
    .then(r=>r.ok?r.json():Promise.reject(new Error('calendar unavailable')))
    .then(data=>{
      const today=londonDate();
      const dated=(data.events||[]).filter(e=>e.date>=today);
      const rhythm=recurringItems(data,today);
      const seen=new Set();
      const items=[...dated,...rhythm]
        .sort((a,b)=>a.date.localeCompare(b.date) || String(a.id).localeCompare(String(b.id)))
        .filter(item=>{
          const key=`${item.date}|${item.title}`;
          if(seen.has(key)) return false;
          seen.add(key);
          return true;
        })
        .slice(0,7);

      if(target){
        if(!items.length){
          target.textContent='';
          const empty=document.createElement('p');
          empty.textContent='No scheduled public-world item is due. The country is allowed a quiet day.';
          target.append(empty);
        } else {
          target.textContent='';
          for(const e of items){
            const article=document.createElement('article');
            article.className='event';
            const when=document.createElement('p');
            when.className='event-date';
            when.textContent=(e.date===today?'Today · ':'')+dateFmt.format(safeDate(e.date));
            const h=document.createElement('h3');
            h.textContent=e.title;
            const p=document.createElement('p');
            p.textContent=e.summary;
            const meta=document.createElement('p');
            meta.className='small';
            meta.textContent=`${e.region} · ${String(e.status).toLowerCase()} · fictional calendar`;
            article.append(when,h,p,meta);
            target.append(article);
          }
        }
      }

      if(homeTarget){
        homeTarget.textContent='';
        const preview=items.slice(0,3);
        if(!preview.length){
          const empty=document.createElement('p');
          empty.textContent='Nothing is scheduled. Aldernia is allowed a quiet day.';
          homeTarget.append(empty);
        }
        for(const e of preview){
          const article=document.createElement('article');
          article.className='home-event';
          const when=document.createElement('p');
          when.className='story-tag';
          when.textContent=(e.date===today?'Today · ':'')+dateFmt.format(safeDate(e.date));
          const h=document.createElement('h3');
          h.textContent=e.title;
          const p=document.createElement('p');
          p.textContent=e.summary;
          const meta=document.createElement('p');
          meta.className='small';
          meta.textContent=`${e.region} · fictional calendar`;
          article.append(when,h,p,meta);
          homeTarget.append(article);
        }
      }
    })
    .catch(()=>{
      for(const el of [target,homeTarget]){
        if(!el) continue;
        el.textContent='';
        const p=document.createElement('p');
        p.textContent='The public calendar could not be loaded. No event has been inferred from the failure.';
        el.append(p);
      }
    });
})();