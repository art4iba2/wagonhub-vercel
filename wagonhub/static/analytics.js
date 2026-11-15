// No analytics script is requested until the visitor opts in.
(() => {
  const id = window.WAGONHUB_METRIKA_ID;
  const banner = document.querySelector('#analytics-consent');
  const settings = document.querySelector('#analytics-settings');
  if (!Number.isSafeInteger(id) || id <= 0) return;
  settings.hidden = false;
  const key = 'wagonhub_analytics_consent';
  let active = false;
  function start() {
    if (active) return;
    active = true;
    window.ym = window.ym || function () { (window.ym.a = window.ym.a || []).push(arguments); };
    window.ym.l = Date.now();
    window.ym(id, 'init', {clickmap:true, trackLinks:true, trackHash:true, webvisor:false});
    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://mc.yandex.ru/metrika/tag.js';
    document.head.appendChild(script);
  }
  function consent(value) {
    localStorage.setItem(key, value);
    banner.hidden = true;
    if (value === 'yes') start();
    else if (active) {
      // The script has already loaded; reload to stop tracking this session.
      active = false;
      window.location.reload();
    }
  }
  if (localStorage.getItem(key) === 'yes') start();
  else if (localStorage.getItem(key) !== 'no') banner.hidden = false;
  document.querySelector('#analytics-accept').addEventListener('click', () => consent('yes'));
  document.querySelector('#analytics-decline').addEventListener('click', () => consent('no'));
  settings.addEventListener('click', () => { banner.hidden = false; });
  window.wagonhubAnalytics = {
    goal(name) {
      if (active && /^[a-z_]+$/.test(name)) window.ym(id, 'reachGoal', name);
    }
  };
  document.addEventListener('click', event => {
    const element = event.target.closest('[data-goal]');
    if (element) window.wagonhubAnalytics.goal(element.dataset.goal);
  });
})();
