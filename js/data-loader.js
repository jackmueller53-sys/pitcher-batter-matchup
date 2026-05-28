/* Loads the three JSON files written by scripts/fetch-data.js.
   Exposes window.MatchupData = { pitchers, batters, league, meta, ready }. */
(function () {
  'use strict';

  const out = { pitchers: [], batters: [], league: null, meta: null, ready: null };

  function loadJSON(url) {
    return fetch(url, { cache: 'default' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url); return r.json(); })
      .catch(e => { console.error('[data] load fail', url, e.message); return null; });
  }

  out.ready = Promise.all([
    loadJSON('data/pitchers.json'),
    loadJSON('data/batters.json'),
    loadJSON('data/league.json'),
    loadJSON('data/meta.json'),
  ]).then(([p, b, l, m]) => {
    out.pitchers = Array.isArray(p) ? p : [];
    out.batters  = Array.isArray(b) ? b : [];
    out.league   = l;
    out.meta     = m;
    return out;
  });

  window.MatchupData = out;
})();
