#!/usr/bin/env python3
"""
Production daily Statcast feature builder.

Pulls the CURRENT season's pitch-level data via pybaseball, derives the
v1.1 model features, and writes JSON into data/:

  - bat_splits.json       — vs-handedness rates per batter
  - pit_splits.json       — vs-handedness rates per pitcher
  - bat_whiff.json        — per-pitch-type whiff% per batter
  - pit_arsenal.json      — per-pitch-type usage per pitcher
  - park_factors.json     — static recent 3-yr park factors

Runs daily after fetch-data.js. CI installs pybaseball+pandas+numpy.

Env vars:
  SEASON   = 2026 (default = current year)
  PB_DAYS  = look back this many days only (for faster reruns during season)
"""
import os
import sys
import json
import time
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

import pandas as pd
from pybaseball import statcast, cache as pb_cache

HERE = Path(__file__).parent
DATA = HERE.parent / 'data'
DATA.mkdir(exist_ok=True)
pb_cache.enable()

SEASON = int(os.environ.get('SEASON', date.today().year))
PB_DAYS = int(os.environ.get('PB_DAYS', '0'))

# Date range — full season or rolling N days
if PB_DAYS > 0:
    end = date.today()
    start = end - timedelta(days=PB_DAYS)
else:
    # Spring training out, regular season only
    start = date(SEASON, 3, 20)
    end = min(date.today(), date(SEASON, 10, 5))

if end <= start:
    print(f'Date range {start}..{end} is empty (season {SEASON} not started?).')
    sys.exit(0)


PITCH_GROUPS = {
    'FF': 'FF', 'FT': 'FF', 'SI': 'SI', 'FC': 'FC',
    'SL': 'SL', 'ST': 'SL', 'SV': 'SL', 'CU': 'CU', 'KC': 'CU', 'CS': 'CU',
    'CH': 'CH', 'FS': 'FS', 'FO': 'FS', 'SC': 'CH',
    'KN': 'OT', 'EP': 'OT',
}
PITCH_TYPES = ['FF', 'SI', 'FC', 'SL', 'CU', 'CH', 'FS']

# 2022-2024 3-yr park factors (FanGraphs basic)
PARK_FACTORS = {
    'COL': {'r': 116, 'hr': 117}, 'CIN': {'r': 110, 'hr': 119},
    'BOS': {'r': 108, 'hr': 95},  'BAL': {'r': 103, 'hr': 105},
    'TEX': {'r': 102, 'hr': 102}, 'TOR': {'r': 101, 'hr': 102},
    'CWS': {'r': 101, 'hr': 102}, 'KCR': {'r': 100, 'hr': 95},
    'KC':  {'r': 100, 'hr': 95},  'PHI': {'r': 100, 'hr': 102},
    'WSN': {'r': 99,  'hr': 99},  'MIN': {'r': 99,  'hr': 102},
    'CHC': {'r': 98,  'hr': 100}, 'ARI': {'r': 99,  'hr': 102},
    'HOU': {'r': 98,  'hr': 98},  'LAA': {'r': 97,  'hr': 95},
    'ATL': {'r': 97,  'hr': 100}, 'SDP': {'r': 96,  'hr': 92},
    'SD':  {'r': 96,  'hr': 92},  'CLE': {'r': 96,  'hr': 92},
    'STL': {'r': 96,  'hr': 92},  'NYY': {'r': 95,  'hr': 98},
    'NYM': {'r': 95,  'hr': 93},  'MIA': {'r': 94,  'hr': 90},
    'DET': {'r': 94,  'hr': 92},  'PIT': {'r': 96,  'hr': 92},
    'OAK': {'r': 94,  'hr': 92},  'ATH': {'r': 94,  'hr': 92},
    'TBR': {'r': 96,  'hr': 95},  'TB':  {'r': 96,  'hr': 95},
    'SEA': {'r': 92,  'hr': 90},  'SFG': {'r': 92,  'hr': 88},
    'SF':  {'r': 92,  'hr': 88},  'LAD': {'r': 98,  'hr': 102},
    'MIL': {'r': 99,  'hr': 100},
}


def to_pa_outcomes(df):
    df = df.sort_values(['game_pk', 'at_bat_number', 'pitch_number'])
    last = df.groupby(['game_pk', 'at_bat_number'], as_index=False).tail(1).copy()
    def label(evt):
        if pd.isna(evt): return None
        e = str(evt)
        if e in ('strikeout', 'strikeout_double_play'): return 'K'
        if e == 'walk': return 'BB'
        if e == 'hit_by_pitch': return 'HBP'
        if e == 'home_run': return 'HR'
        if e == 'triple': return 'triple'
        if e == 'double': return 'double'
        if e == 'single': return 'single'
        if e in ('field_out', 'grounded_into_double_play', 'force_out',
                 'fielders_choice', 'fielders_choice_out', 'sac_fly', 'sac_bunt',
                 'sac_fly_double_play', 'double_play', 'triple_play',
                 'sac_bunt_double_play', 'field_error'):
            return 'out_in_play'
        return None
    last['outcome'] = last['events'].map(label)
    return last[last['outcome'].notna()].copy()


def compute_splits(pa, by_col, vs_col):
    out = defaultdict(dict)
    for (pid, hand), g in pa.groupby([by_col, vs_col]):
        n = len(g)
        if n < 30: continue
        k = (g['outcome'] == 'K').mean()
        bb = (g['outcome'] == 'BB').mean()
        hr = (g['outcome'] == 'HR').mean()
        h_ip = g['outcome'].isin(['single', 'double', 'triple']).sum()
        n_ip = g['outcome'].isin(['single', 'double', 'triple', 'out_in_play']).sum()
        babip = (h_ip / n_ip) if n_ip > 0 else None
        out[int(pid)][hand] = {
            'pa': int(n), 'k_pct': float(k), 'bb_pct': float(bb),
            'hr_per_pa': float(hr),
            'babip': float(babip) if babip is not None else None,
        }
    return dict(out)


def compute_whiff(pitches):
    SW = {'swinging_strike', 'foul_tip', 'swinging_strike_blocked',
          'hit_into_play', 'foul', 'foul_bunt', 'missed_bunt'}
    WH = {'swinging_strike', 'foul_tip', 'swinging_strike_blocked',
          'swinging_pitchout', 'missed_bunt'}
    p = pitches.copy()
    p['ptype'] = p['pitch_type'].map(PITCH_GROUPS).fillna('OT')
    p['is_swing'] = p['description'].isin(SW)
    p['is_whiff'] = p['description'].isin(WH)
    out = defaultdict(dict)
    for (bid, pt), g in p.groupby(['batter', 'ptype']):
        if pt not in PITCH_TYPES: continue
        sw = int(g['is_swing'].sum())
        if sw < 30: continue
        wh = int(g['is_whiff'].sum())
        out[int(bid)][pt] = {'swings': sw, 'whiffs': wh,
                             'whiff_pct': wh / sw}
    return dict(out)


def compute_arsenal(pitches):
    p = pitches.copy()
    p['ptype'] = p['pitch_type'].map(PITCH_GROUPS).fillna('OT')
    out = {}
    for pid, g in p.groupby('pitcher'):
        total = len(g)
        if total < 50: continue
        rec = {'total_pitches': total, 'arsenal': {}}
        for pt in PITCH_TYPES:
            sub = g[g['ptype'] == pt]
            if len(sub) == 0: continue
            rec['arsenal'][pt] = {
                'usage_pct': float(len(sub) / total),
                'avg_velo': float(sub['release_speed'].mean()) if 'release_speed' in sub else None,
            }
        out[int(pid)] = rec
    return out


def main():
    print(f'Season {SEASON} · pulling Statcast {start}..{end}')
    t0 = time.time()
    sc = statcast(start_dt=str(start), end_dt=str(end))
    print(f'  {len(sc):,} pitches in {time.time()-t0:.0f}s')
    if len(sc) == 0:
        print('No pitches — writing empty feature files so the UI degrades gracefully.')
        for f in ['bat_splits.json', 'pit_splits.json', 'bat_whiff.json', 'pit_arsenal.json']:
            (DATA / f).write_text('{}')
        (DATA / 'park_factors.json').write_text(json.dumps(PARK_FACTORS))
        return

    pa = to_pa_outcomes(sc)
    print(f'  {len(pa):,} labeled PAs')

    bat_splits  = compute_splits(pa, 'batter', 'p_throws')
    pit_splits  = compute_splits(pa, 'pitcher', 'stand')
    bat_whiff   = compute_whiff(sc)
    pit_arsenal = compute_arsenal(sc)

    print(f'  bat_splits={len(bat_splits)} pit_splits={len(pit_splits)} '
          f'bat_whiff={len(bat_whiff)} pit_arsenal={len(pit_arsenal)}')

    (DATA / 'bat_splits.json').write_text(json.dumps(bat_splits))
    (DATA / 'pit_splits.json').write_text(json.dumps(pit_splits))
    (DATA / 'bat_whiff.json').write_text(json.dumps(bat_whiff))
    (DATA / 'pit_arsenal.json').write_text(json.dumps(pit_arsenal))
    (DATA / 'park_factors.json').write_text(json.dumps(PARK_FACTORS))

    sizes = {f.name: f.stat().st_size for f in DATA.glob('*.json')}
    print('Wrote:')
    for n, s in sorted(sizes.items()):
        print(f'  {n:<25} {s/1024:>6.0f} KB')
    print(f'Total feature-builder time: {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
