(() => {
  'use strict';

  if (window.location.pathname.includes('/country/')) {
    document.body.classList.add('country-surface');
    const countryNav = document.querySelector('.site-nav');
    if (countryNav && !countryNav.querySelector('a[href="map.html"]')) {
      const atlas = document.createElement('a');
      atlas.href = 'map.html';
      atlas.textContent = 'Atlas';
      const home = countryNav.querySelector('a[href="./"]');
      if (home && home.nextSibling) countryNav.insertBefore(atlas, home.nextSibling);
      else if (home) home.insertAdjacentElement('afterend', atlas);
      else countryNav.prepend(atlas);
    }
  }
  const script = document.currentScript;
  const root = script?.dataset.root || './';
  const londonTime = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  });
  const londonDay = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
  });
  const londonHour = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', hourCycle: 'h23' });

  function daypart(hour) {
    if (hour >= 5 && hour < 12) return 'morning';
    if (hour >= 12 && hour < 17) return 'afternoon';
    if (hour >= 17 && hour < 22) return 'evening';
    return 'night';
  }

  function tick() {
    const now = new Date();
    document.querySelectorAll('[data-human-time]').forEach(el => { el.textContent = londonTime.format(now); });
    document.querySelectorAll('[data-computing-time]').forEach(el => { el.textContent = String(now.getTime()); });
    document.querySelectorAll('[data-aldernia-date]').forEach(el => { el.textContent = londonDay.format(now); });
    document.querySelectorAll('[data-aldernia-moment]').forEach(el => {
      el.textContent = `${londonDay.format(now)} · ${daypart(Number(londonHour.format(now)))} in Aldernia`;
    });
  }

  fetch(`${root}aldernia/build.json`, { cache: 'no-store' })
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(build => {
      document.querySelectorAll('[data-build]').forEach(el => { el.textContent = build.id || 'unversioned'; });
      document.documentElement.dataset.build = build.id || '';
    })
    .catch(() => {
      document.querySelectorAll('[data-build]').forEach(el => { el.textContent = 'build unavailable'; });
    });

  tick();
  window.setInterval(tick, 1000);
})();
