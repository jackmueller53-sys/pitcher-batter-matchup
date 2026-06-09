#!/usr/bin/env node
/**
 * Pull pitcher + batter + league-baseline data from FanGraphs (and Stuff+
 * grades from type=36). Adapted from baseball-hub/scripts/fetch-2026.js,
 * trimmed to only the fields the matchup model needs.
 *
 * Output: data/pitchers.json, data/batters.json, data/league.json, data/meta.json
 *
 * Usage:
 *   node scripts/fetch-data.js          # current season
 *   SEASON=2025 node scripts/fetch-data.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');

const SEASON = parseInt(process.env.SEASON || new Date().getFullYear(), 10);
const DATA_DIR = path.join(__dirname, '..', 'data');
fs.mkdirSync(DATA_DIR, { recursive: true });

// ─────────────────────────── HTTP helper ──────────────────────────────
// FanGraphs is behind Cloudflare; CI requests get 403'd without browser-y
// headers. Real Chrome 124 macOS header set + CORS-proxy fallback on 4xx.

const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'application/json, text/csv;q=0.9, */*;q=0.1',
  'Accept-Language': 'en-US,en;q=0.9',
  'Accept-Encoding': 'identity',
  'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
  'Sec-Ch-Ua-Mobile': '?0',
  'Sec-Ch-Ua-Platform': '"macOS"',
  'Sec-Fetch-Dest': 'empty',
  'Sec-Fetch-Mode': 'cors',
  'Sec-Fetch-Site': 'same-site',
};
const PROXIES = [
  (u) => `https://corsproxy.io/?${encodeURIComponent(u)}`,
  (u) => `https://api.allorigins.win/raw?url=${encodeURIComponent(u)}`,
];

function directFetch(url, maxRedirects = 5) {
  return new Promise((resolve, reject) => {
    if (maxRedirects <= 0) return reject(new Error('too many redirects'));
    const parsed = new URL(url);
    const req = https.get(url, {
      headers: { ...BROWSER_HEADERS, 'Referer': `${parsed.origin}/` },
      timeout: 30000,
    }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        let next = res.headers.location;
        if (next.startsWith('/')) next = parsed.origin + next;
        return resolve(directFetch(next, maxRedirects - 1));
      }
      if (res.statusCode !== 200) { res.resume(); return reject(new Error(`HTTP ${res.statusCode}`)); }
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

async function fetchText(url) {
  try { return await directFetch(url); }
  catch (e) {
    const is4xx = /HTTP 4\d\d/.test(e.message || '');
    if (!is4xx) {
      try { await new Promise(r => setTimeout(r, 300)); return await directFetch(url); }
      catch (_) { /* fall through */ }
    }
    for (let i = 0; i < PROXIES.length; i++) {
      try {
        const txt = await directFetch(PROXIES[i](url));
        console.warn(`    (recovered via proxy ${i + 1}/${PROXIES.length})`);
        return txt;
      } catch (_) { /* try next */ }
    }
    throw new Error(`${e.message} ${url.slice(0, 100)} (proxies also failed)`);
  }
}

// ─────────────────────────── FanGraphs ────────────────────────────────
// `type` 8 = standard, 7 = plate discipline, 36 = Stuff+/Pitching+
async function fetchFG(stats, qual, type = 8) {
  const url = `https://www.fangraphs.com/api/leaders/major-league/data`
    + `?pos=all&stats=${stats}&lg=all&qual=${qual}&type=${type}`
    + `&season=${SEASON}&season1=${SEASON}&ind=0&team=0&pageitems=2000&pagenum=1`;
  console.log(`  → FG ${stats} type=${type} qual=${qual}`);
  const text = await fetchText(url);
  let j;
  try { j = JSON.parse(text); } catch (e) {
    throw new Error(`FG ${stats}/${type} bad JSON: ${text.slice(0, 200)}`);
  }
  return j.data || [];
}

function stripHTML(s) { return s ? String(s).replace(/<[^>]*>/g, '').trim() : ''; }
function num(v) { const n = parseFloat(v); return isFinite(n) ? n : null; }

// ─────────────────────────── Field maps ───────────────────────────────
// Keep only the fields the model + UI need. Removes ~90% of FG payload.
function mapBatter(r) {
  return {
    id: r.xMLBAMID || r.playerid || null,
    name: stripHTML(r.PlayerName || r.Name),
    team: stripHTML(r.TeamNameAbb || r.Team || ''),
    bats: r.Bats || null, // FG sometimes returns this; otherwise filled later
    pa: num(r.PA), ab: num(r.AB), hr: num(r.HR),
    avg: num(r.AVG), obp: num(r.OBP), slg: num(r.SLG),
    iso: num(r.ISO), babip: num(r.BABIP),
    woba: num(r.wOBA), wrc_plus: num(r['wRC+']),
    k_pct: num(r['K%']), bb_pct: num(r['BB%']),
    swstr_pct: num(r['SwStr%']),
    contact_pct: num(r['Contact%']), o_swing_pct: num(r['O-Swing%']),
    hard_pct: num(r['Hard%']),
  };
}

function mapPitcher(r) {
  return {
    id: r.xMLBAMID || r.playerid || null,
    name: stripHTML(r.PlayerName || r.Name),
    team: stripHTML(r.TeamNameAbb || r.Team || ''),
    throws: r.Throws || null,
    ip: num(r.IP), gs: num(r.GS), g: num(r.G),
    era: num(r.ERA), fip: num(r.FIP), xfip: num(r.xFIP), xera: num(r.xERA),
    war: num(r.WAR), whip: num(r.WHIP), babip: num(r.BABIP),
    k_pct: num(r['K%']), bb_pct: num(r['BB%']),
    hr_per_9: num(r['HR/9']), hr_per_fb: num(r['HR/FB']),
    swstr_pct: num(r['SwStr%']), csw_pct: num(r['C+SwStr%']),
    gb_pct: num(r['GB%']), lob_pct: num(r['LOB%']),
  };
}

function mergeStuffPlus(pitchers, sp) {
  const idx = {};
  sp.forEach(r => {
    const id = r.xMLBAMID || r.playerid;
    if (id) idx[id] = r;
  });
  pitchers.forEach(p => {
    const s = idx[p.id];
    if (!s) return;
    p.stuff_plus = num(s.sp_stuff);
    p.location_plus = num(s.sp_location);
    p.pitching_plus = num(s.sp_pitching);
    // Per-pitch-type Stuff+ — useful for explainability later
    p.sp_FF = num(s.sp_s_FF); p.sp_SI = num(s.sp_s_SI);
    p.sp_FC = num(s.sp_s_FC); p.sp_SL = num(s.sp_s_SL);
    p.sp_CU = num(s.sp_s_CU); p.sp_KC = num(s.sp_s_KC);
    p.sp_CH = num(s.sp_s_CH); p.sp_FS = num(s.sp_s_FS);
  });
  return pitchers;
}

// ─────────────────────────── League baselines ─────────────────────────
function computeLeague(batters, pitchers) {
  // Weighted by PA / TBF where available, otherwise simple mean.
  function wMean(rows, valKey, weightKey) {
    let n = 0, d = 0;
    rows.forEach(r => {
      const v = r[valKey], w = r[weightKey] || 1;
      if (v != null && isFinite(v)) { n += v * w; d += w; }
    });
    return d > 0 ? n / d : null;
  }
  return {
    season: SEASON,
    bat: {
      pa_sum: batters.reduce((s, r) => s + (r.pa || 0), 0),
      k_pct: wMean(batters, 'k_pct', 'pa'),
      bb_pct: wMean(batters, 'bb_pct', 'pa'),
      avg: wMean(batters, 'avg', 'ab'),
      obp: wMean(batters, 'obp', 'pa'),
      slg: wMean(batters, 'slg', 'ab'),
      woba: wMean(batters, 'woba', 'pa'),
      babip: wMean(batters, 'babip', 'ab'),
      iso: wMean(batters, 'iso', 'ab'),
      // HR per PA — denominator is PA, numerator is HR
      hr_per_pa: (batters.reduce((s, r) => s + (r.hr || 0), 0)
                 / Math.max(1, batters.reduce((s, r) => s + (r.pa || 0), 0))),
    },
    pit: {
      ip_sum: pitchers.reduce((s, r) => s + (r.ip || 0), 0),
      k_pct: wMean(pitchers, 'k_pct', 'ip'),
      bb_pct: wMean(pitchers, 'bb_pct', 'ip'),
      babip: wMean(pitchers, 'babip', 'ip'),
      lob_pct: wMean(pitchers, 'lob_pct', 'ip'),
      gb_pct: wMean(pitchers, 'gb_pct', 'ip'),
      stuff_plus: 100, // by definition normalized
      // HR/9 → HR per PA via ~38 PA / 9 IP
      hr_per_pa: wMean(pitchers, 'hr_per_9', 'ip') / 38.0,
    },
    // Linear-weights anchor for wOBA → runs scaling (FG 2024 values; close
    // enough year over year that we hard-code; can be re-calibrated later).
    woba_scale: 1.24,        // FG's wOBA scale (varies 1.20-1.28 by year)
    runs_per_woba: 1.24,     // 1 wOBA above league ≈ 1.24 runs per 600 PA
  };
}

// ─────────────────────────── Main ─────────────────────────────────────
async function main() {
  console.log(`Fetching season ${SEASON}...`);

  const t0 = Date.now();
  let batters = [], pitchers = [], stuffplus = [];
  const errors = [];

  try { batters = (await fetchFG('bat', 50)).map(mapBatter); }
  catch (e) { errors.push('bat: ' + e.message); console.error('  ERR bat:', e.message); }

  try { pitchers = (await fetchFG('pit', 20)).map(mapPitcher); }
  catch (e) { errors.push('pit: ' + e.message); console.error('  ERR pit:', e.message); }

  try { stuffplus = await fetchFG('pit', 0, 36); }
  catch (e) { errors.push('stuffplus: ' + e.message); console.error('  ERR stuffplus:', e.message); }

  pitchers = mergeStuffPlus(pitchers, stuffplus);

  // Drop rows without an MLBAM id — we need it to join with MLB Stats API.
  batters = batters.filter(b => b.id);
  pitchers = pitchers.filter(p => p.id);

  const league = computeLeague(batters, pitchers);

  // Write outputs. When a particular fetch came back empty (Cloudflare 403,
  // etc.), preserve whatever's on disk so build_features.py runs next and
  // overwrites with Statcast-derived data. fg-* files are written as the
  // raw FG payload so build_features can pick up wRC+/Stuff+ augmentation.
  function writeOrPreserve(name, rows) {
    if (Array.isArray(rows) && rows.length > 0) {
      fs.writeFileSync(path.join(DATA_DIR, name), JSON.stringify(rows));
    } else {
      console.warn(`  preserving ${name} (this run returned empty)`);
    }
  }
  writeOrPreserve('batters.json',  batters);
  writeOrPreserve('pitchers.json', pitchers);
  if (Object.keys(league.bat || {}).length) {
    fs.writeFileSync(path.join(DATA_DIR, 'league.json'), JSON.stringify(league));
  }
  // FG raw snapshots (consumed by build_features.augment_from_fg)
  writeOrPreserve('fg-bat.json',       batters);
  writeOrPreserve('fg-pit.json',       pitchers);
  writeOrPreserve('fg-stuffplus.json', stuffplus);

  fs.writeFileSync(path.join(DATA_DIR, 'meta.json'),
    JSON.stringify({
      fetchedAt: new Date().toISOString(),
      season: SEASON,
      counts: { batters: batters.length, pitchers: pitchers.length, stuffplus: stuffplus.length },
      errors,
      durationMs: Date.now() - t0,
    }, null, 2));

  console.log(`Done in ${((Date.now() - t0) / 1000).toFixed(1)}s. `
    + `batters=${batters.length} pitchers=${pitchers.length} errors=${errors.length}`);

  // Don't exit 1 even when everything failed — build_features.py runs next
  // and is the primary data source. Just warn loudly.
  if (batters.length === 0 && pitchers.length === 0) {
    console.warn('⚠️  FanGraphs returned 0 rows on every endpoint. '
      + 'build_features.py will derive primary stats from Statcast instead.');
  }
}

main().catch(e => { console.error(e); process.exit(1); });
