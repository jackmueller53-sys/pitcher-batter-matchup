/* Matchup analyzer UI. Reads from window.MatchupData; renders into #app. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const escHTML = (s) => s == null ? '' : String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  // ─── Selected players (current matchup) ───
  let _pickedPitcher = null;
  let _pickedBatter = null;

  function setupSelects() {
    const pitchers = window.MatchupData.pitchers
      .slice().sort((a, b) => (b.ip || 0) - (a.ip || 0));
    const batters = window.MatchupData.batters
      .slice().sort((a, b) => (b.pa || 0) - (a.pa || 0));

    // Pitcher typeahead
    window.Typeahead.attach({
      inputEl: $('pitcher-input'),
      items: pitchers,
      renderRow: (p, q) => `
        <div class="ta-row-name">${highlight(p.name, q)}</div>
        <div class="ta-row-meta">
          <span class="ta-team">${escHTML(p.team || '?')}</span>
          <span class="ta-hand">${escHTML(p.throws || '?')}HP</span>
          ${p.ip != null ? `<span class="ta-stat">${p.ip.toFixed(1)} IP</span>` : ''}
          ${p.stuff_plus != null ? `<span class="ta-stat">Stuff+ ${p.stuff_plus.toFixed(0)}</span>` : ''}
          ${p.k_pct != null ? `<span class="ta-stat">${pctText(p.k_pct, 1)} K%</span>` : ''}
        </div>
      `,
      onSelect: (p) => { _pickedPitcher = p; render(); },
    });

    // Batter typeahead
    window.Typeahead.attach({
      inputEl: $('batter-input'),
      items: batters,
      renderRow: (b, q) => `
        <div class="ta-row-name">${highlight(b.name, q)}</div>
        <div class="ta-row-meta">
          <span class="ta-team">${escHTML(b.team || '?')}</span>
          <span class="ta-hand">${escHTML(b.bats || '?')}HB</span>
          ${b.pa != null ? `<span class="ta-stat">${b.pa.toFixed(0)} PA</span>` : ''}
          ${b.wrc_plus != null ? `<span class="ta-stat">wRC+ ${b.wrc_plus.toFixed(0)}</span>` : ''}
          ${b.woba != null ? `<span class="ta-stat">${b.woba.toFixed(3)} wOBA</span>` : ''}
        </div>
      `,
      onSelect: (b) => { _pickedBatter = b; render(); },
    });

    return { pitchers, batters };
  }

  // Used by typeahead row-renderers — highlights matched substring.
  function highlight(text, query) {
    if (!query) return escHTML(text);
    const t = text || '';
    const tn = t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    const qn = String(query).toLowerCase();
    const i = tn.indexOf(qn);
    if (i < 0) return escHTML(t);
    return escHTML(t.slice(0, i))
      + '<mark>' + escHTML(t.slice(i, i + qn.length)) + '</mark>'
      + escHTML(t.slice(i + qn.length));
  }

  function pctText(v, d) {
    if (v == null) return '—';
    return (v > 1 ? v : v * 100).toFixed(d ?? 1);
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
    const out = $('result');
    const p = _pickedPitcher, b = _pickedBatter;
    if (!p || !b) {
      out.innerHTML = '<div class="hint">Search for a pitcher and a batter to see the matchup.</div>';
      return;
    }
    const lg = window.MatchupData.league;
    const D = window.MatchupData;
    const ctx = {
      bat_split:    D.bat_splits[b.id],
      pit_split:    D.pit_splits[p.id],
      bat_whiff:    D.bat_whiff[b.id],
      pit_arsenal:  D.pit_arsenal[p.id],
      park_factors: D.park_factors,
      home_team:    p.team || null, // pitcher's team is home in current single-matchup view
    };
    const m = window.MatchupModel.matchup(p, b, lg, ctx);
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

      ${renderH2H(D.h2h && D.h2h[p.id + '|' + b.id], p, b)}
    `;
  }

  // ─── Previous matchups (actual AB results, pitcher vs this batter) ───
  // Reads the enriched h2h entry: full line + a recent PA log. Degrades to the
  // rate-only fields on older data, and to a hint when the pair is unseen.
  const _OUT_LABEL = { single:'1B', double:'2B', triple:'3B', HR:'HR', BB:'BB', HBP:'HBP', K:'K', out_in_play:'Out' };
  const _OUT_CLASS = { single:'hit', double:'hit', triple:'hit', HR:'hit', BB:'walk', HBP:'walk', K:'out', out_in_play:'out' };
  function renderH2H(h, p, b) {
    const title = `<div class="card-h">Previous matchups <span class="h2h-sub">${escHTML(b.name)} vs. ${escHTML(p.name)}</span></div>`;
    if (!h || !h.pa) {
      return `<div class="card card-h2h">${title}<div class="hint">No prior batter-vs-pitcher history (fewer than 10 career PA in the Statcast window).</div></div>`;
    }
    const slash = (h.avg != null && h.ab)
      ? `<span class="h2h-slash">${dec3(h.avg)}/${dec3(h.obp)}/${dec3(h.slg)}</span>` : '';
    const forLine = (h.ab != null)
      ? `<b>${h.h}-for-${h.ab}</b>` : `<b>${h.pa} PA</b>`;
    const xb = [];
    if (h.hr) xb.push(`${h.hr} HR`);
    if (h.b3) xb.push(`${h.b3} 3B`);
    if (h.b2) xb.push(`${h.b2} 2B`);
    if (h.bb) xb.push(`${h.bb} BB`);
    if (h.so != null) xb.push(`${h.so} K`);
    if (h.hbp) xb.push(`${h.hbp} HBP`);
    const extra = xb.length ? `<div class="h2h-extra">${xb.join(' · ')}</div>` : '';
    const line = `<div class="h2h-line">${forLine} ${slash} <span class="h2h-pa">${h.pa} PA</span></div>${extra}`;
    let log = '';
    if (Array.isArray(h.recent) && h.recent.length) {
      const chips = h.recent.map(([d, o]) => {
        const lbl = _OUT_LABEL[o] || o;
        const cls = _OUT_CLASS[o] || 'out';
        return `<span class="h2h-ab h2h-${cls}" title="${escHTML(d)}">${escHTML(lbl)}<em>${escHTML((d || '').slice(5))}</em></span>`;
      }).join('');
      log = `<div class="h2h-log-lbl">Recent PAs (newest first)</div><div class="h2h-log">${chips}</div>`;
    }
    return `<div class="card card-h2h">${title}${line}${log}</div>`;
  }
  function dec3(v) { return v == null ? '—' : Number(v).toFixed(3).replace(/^0\./, '.'); }

  document.addEventListener('DOMContentLoaded', function () {
    const meta = $('meta-line');
    window.MatchupData.ready.then(() => {
      if (!window.MatchupData.league) {
        $('result').innerHTML = '<div class="hint err">Data not available. '
          + 'Run <code>node scripts/fetch-data.js</code> locally or wait for the daily refresh.</div>';
        return;
      }
      const { pitchers, batters } = setupSelects();
      if (meta && window.MatchupData.meta) {
        const m = window.MatchupData.meta;
        meta.textContent = `Season ${m.season} · ${pitchers.length} pitchers · ${batters.length} batters · fetched ${new Date(m.fetchedAt).toLocaleString()}`;
      }
      // Initialize runs predictor (if present)
      if (window.RunsPredictorUI) window.RunsPredictorUI.init(pitchers, batters);
      render();
      setupModeTabs();
    });
  });

  function setupModeTabs() {
    const tabs = document.querySelectorAll('.mode-tab');
    tabs.forEach((t) => {
      t.addEventListener('click', () => {
        tabs.forEach((x) => {
          x.classList.remove('active');
          x.setAttribute('aria-selected', 'false');
        });
        t.classList.add('active');
        t.setAttribute('aria-selected', 'true');
        const mode = t.dataset.mode;
        document.querySelectorAll('.mode-section').forEach((s) => {
          const on = s.id === 'mode-' + mode;
          s.classList.toggle('active', on);
          s.hidden = !on;
        });
      });
    });
  }
})();
