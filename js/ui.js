/* Matchup analyzer UI. Reads from window.MatchupData; renders into #app. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const escHTML = (s) => s == null ? '' : String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  function setupSelects() {
    const pitchers = window.MatchupData.pitchers
      .slice().sort((a, b) => (b.ip || 0) - (a.ip || 0));
    const batters = window.MatchupData.batters
      .slice().sort((a, b) => (b.pa || 0) - (a.pa || 0));

    const pSel = $('pitcher-select');
    const bSel = $('batter-select');

    pSel.innerHTML = '<option value="">— select pitcher —</option>'
      + pitchers.map((p, i) => `<option value="${i}">${escHTML(p.name)}`
        + ` (${escHTML(p.team || '?')}, ${escHTML(p.throws || '?')}HP)</option>`).join('');

    bSel.innerHTML = '<option value="">— select batter —</option>'
      + batters.map((b, i) => `<option value="${i}">${escHTML(b.name)}`
        + ` (${escHTML(b.team || '?')}${b.bats ? ', ' + escHTML(b.bats) + 'HB' : ''})</option>`).join('');

    pSel.addEventListener('change', render);
    bSel.addEventListener('change', render);

    return { pitchers, batters };
  }

  function pct(v, digits = 1) { return v == null ? '—' : (v * 100).toFixed(digits) + '%'; }
  function dec(v, digits = 3) { return v == null ? '—' : v.toFixed(digits); }

  function edgeBar(edge) {
    // edge: -100 (pitcher) ... +100 (hitter)
    const pct = ((edge + 100) / 2);
    const dir = edge < -10 ? 'pitcher' : (edge > 10 ? 'hitter' : 'neutral');
    return `<div class="edge-bar" aria-label="Edge ${edge.toFixed(0)}">
      <div class="edge-bar-fill edge-${dir}" style="width:${pct}%"></div>
      <div class="edge-bar-center"></div>
      <div class="edge-label">${edge > 0 ? '+' : ''}${edge.toFixed(0)} ${dir.toUpperCase()}</div>
    </div>`;
  }

  function render() {
    const pi = parseInt($('pitcher-select').value, 10);
    const bi = parseInt($('batter-select').value, 10);
    const out = $('result');
    if (isNaN(pi) || isNaN(bi)) {
      out.innerHTML = '<div class="hint">Pick a pitcher and a batter to see the matchup.</div>';
      return;
    }
    const p = _pitchers[pi];
    const b = _batters[bi];
    const lg = window.MatchupData.league;
    const m = window.MatchupModel.matchup(p, b, lg);
    if (!m) {
      out.innerHTML = '<div class="hint err">Could not compute matchup (missing fields).</div>';
      return;
    }

    out.innerHTML = `
      <div class="matchup-head">
        <div class="head-p">
          <div class="head-name">${escHTML(p.name)}</div>
          <div class="head-meta">${escHTML(p.team || '?')} · ${escHTML(p.throws || '?')}HP · ${p.ip ? p.ip.toFixed(1) + ' IP' : ''}</div>
          <div class="head-marks">
            ${p.stuff_plus != null ? `<span class="mk">Stuff+ <b>${p.stuff_plus.toFixed(0)}</b></span>` : ''}
            ${p.location_plus != null ? `<span class="mk">Loc+ <b>${p.location_plus.toFixed(0)}</b></span>` : ''}
            ${p.era != null ? `<span class="mk">ERA <b>${p.era.toFixed(2)}</b></span>` : ''}
            ${p.k_pct != null ? `<span class="mk">K% <b>${(p.k_pct * (p.k_pct > 1 ? 1 : 100)).toFixed(1)}</b></span>` : ''}
          </div>
        </div>
        <div class="head-vs">VS</div>
        <div class="head-b">
          <div class="head-name">${escHTML(b.name)}</div>
          <div class="head-meta">${escHTML(b.team || '?')} · ${b.bats ? escHTML(b.bats) + 'HB' : '?HB'} · ${b.pa ? b.pa.toFixed(0) + ' PA' : ''}</div>
          <div class="head-marks">
            ${b.wrc_plus != null ? `<span class="mk">wRC+ <b>${b.wrc_plus.toFixed(0)}</b></span>` : ''}
            ${b.woba != null ? `<span class="mk">wOBA <b>${b.woba.toFixed(3)}</b></span>` : ''}
            ${b.k_pct != null ? `<span class="mk">K% <b>${(b.k_pct * (b.k_pct > 1 ? 1 : 100)).toFixed(1)}</b></span>` : ''}
            ${b.bb_pct != null ? `<span class="mk">BB% <b>${(b.bb_pct * (b.bb_pct > 1 ? 1 : 100)).toFixed(1)}</b></span>` : ''}
          </div>
        </div>
      </div>

      ${edgeBar(m.edge)}

      <div class="grid">
        <div class="card">
          <div class="card-h">Predicted PA outcome</div>
          <table class="pa-table">
            <tr><td>Strikeout</td><td>${pct(m.p.K)}</td></tr>
            <tr><td>Walk</td><td>${pct(m.p.BB)}</td></tr>
            <tr><td>Single</td><td>${pct(m.p.single)}</td></tr>
            <tr><td>Double</td><td>${pct(m.p.double)}</td></tr>
            <tr><td>Triple</td><td>${pct(m.p.triple, 2)}</td></tr>
            <tr><td>Home run</td><td>${pct(m.p.HR)}</td></tr>
            <tr><td>HBP</td><td>${pct(m.p.HBP, 2)}</td></tr>
            <tr><td>Out in play</td><td>${pct(m.p.out_in_play)}</td></tr>
          </table>
        </div>
        <div class="card">
          <div class="card-h">Summary</div>
          <table class="pa-table">
            <tr><td>Expected wOBA</td><td><b>${dec(m.xwOBA)}</b></td></tr>
            <tr><td>League average wOBA</td><td>${dec(m.lgWOBA)}</td></tr>
            <tr><td>Edge (− pit / + bat)</td><td><b>${m.edge > 0 ? '+' : ''}${m.edge.toFixed(0)}</b></td></tr>
          </table>
        </div>
        <div class="card">
          <div class="card-h">Why</div>
          ${m.reasons.length === 0
            ? '<div class="hint">Average matchup — no major edges either way.</div>'
            : m.reasons.map(r => `<div class="reason reason-${r.favors}">
                <span class="reason-lbl">${escHTML(r.label)}</span>
                <span class="reason-detail">${escHTML(r.detail)}</span>
                <span class="reason-tag">→ ${escHTML(r.favors)}</span>
              </div>`).join('')}
        </div>
      </div>
    `;
  }

  let _pitchers = [], _batters = [];

  document.addEventListener('DOMContentLoaded', function () {
    const meta = $('meta-line');
    window.MatchupData.ready.then(() => {
      if (!window.MatchupData.league) {
        $('result').innerHTML = '<div class="hint err">Data not available. '
          + 'Run <code>node scripts/fetch-data.js</code> locally or wait for the daily refresh.</div>';
        return;
      }
      const { pitchers, batters } = setupSelects();
      _pitchers = pitchers;
      _batters = batters;
      if (meta && window.MatchupData.meta) {
        const m = window.MatchupData.meta;
        meta.textContent = `Season ${m.season} · ${pitchers.length} pitchers · ${batters.length} batters · fetched ${new Date(m.fetchedAt).toLocaleString()}`;
      }
      render();
    });
  });
})();
