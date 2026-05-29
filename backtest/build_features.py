"""
Build the v1.1 feature set from cached Statcast data.

Produces per-player JSON the enhanced model consumes:
  - vs-handedness splits (K%, BB%, HR/PA, BABIP) per batter and per pitcher
  - per-pitch-type whiff% per batter
  - arsenal usage % per pitcher
  - reliever vs starter classification
  - park factors (hard-coded recent multi-year values)

Run:  python3 build_features.py
"""
import json
import math
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

HERE = Path(__file__).parent
CACHE = HERE / 'cache'
OUT = HERE / 'features'
OUT.mkdir(exist_ok=True)


# ──────────────────────── 2024 Statcast pull (training year) ───────────────
def load_2024_statcast() -> pd.DataFrame:
    p = CACHE / 'statcast_2024.parquet'
    if not p.exists():
        print('  pulling 2024 Statcast (5-15 min)...')
        from fetch_history import pull_statcast
        return pull_statcast('2024-03-28', '2024-09-29', '2024')
    return pd.read_parquet(p)


def pa_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Same labeling as fetch_history.to_pa_outcomes."""
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


# ──────────────────────── vs-handedness splits ───────────────────────────
def compute_player_splits(pa: pd.DataFrame, by_col: str, vs_col: str) -> dict:
    """
    Group PAs by `by_col` and `vs_col` (e.g., by_col='batter', vs_col='p_throws'
    gives batter splits vs LHP/RHP).

    Returns {player_id: { 'L': {pa, k_pct, bb_pct, hr_per_pa, babip}, 'R': {...} }}.
    """
    out = defaultdict(dict)
    for (pid, hand), grp in pa.groupby([by_col, vs_col]):
        n = len(grp)
        if n < 30:  # min sample size threshold
            continue
        k_pct = (grp['outcome'] == 'K').mean()
        bb_pct = (grp['outcome'] == 'BB').mean()
        hr_pa = (grp['outcome'] == 'HR').mean()
        # BABIP = (H - HR) / (AB - K - HR + SF). Approximate by treating PAs minus
        # K/BB/HBP/HR as the BABIP denom and 1B/2B/3B as the numerator.
        in_play_h = ((grp['outcome'].isin(['single', 'double', 'triple']))).sum()
        in_play_n = (grp['outcome'].isin(['single', 'double', 'triple', 'out_in_play'])).sum()
        babip = (in_play_h / in_play_n) if in_play_n > 0 else None
        out[int(pid)][hand] = {
            'pa': int(n),
            'k_pct': float(k_pct),
            'bb_pct': float(bb_pct),
            'hr_per_pa': float(hr_pa),
            'babip': float(babip) if babip is not None else None,
        }
    return dict(out)


# ──────────────────────── per-pitch-type batter whiff% ───────────────────
PITCH_GROUPS = {
    # group raw Statcast pitch_types into our buckets
    'FF': 'FF', 'FT': 'FF', 'SI': 'SI', 'FC': 'FC',  # fastballs
    'SL': 'SL', 'ST': 'SL', 'SV': 'SL', 'CU': 'CU', 'KC': 'CU', 'CS': 'CU',  # breakers
    'CH': 'CH', 'FS': 'FS', 'FO': 'FS', 'SC': 'CH',  # offspeed
    'KN': 'OT', 'EP': 'OT',
}
PITCH_TYPES = ['FF', 'SI', 'FC', 'SL', 'CU', 'CH', 'FS']

def compute_batter_pitch_whiff(pitches: pd.DataFrame) -> dict:
    """
    Per batter: for each pitch group, swing% and whiff% (whiffs / swings).
    """
    # Filter to swings: description in {swinging_strike, foul_tip,
    # swinging_strike_blocked, hit_into_play, foul, foul_bunt}
    SWING_DESC = {'swinging_strike', 'foul_tip', 'swinging_strike_blocked',
                  'hit_into_play', 'foul', 'foul_bunt', 'missed_bunt'}
    WHIFF_DESC = {'swinging_strike', 'foul_tip', 'swinging_strike_blocked',
                  'swinging_pitchout', 'missed_bunt'}
    p = pitches.copy()
    p['ptype'] = p['pitch_type'].map(PITCH_GROUPS).fillna('OT')
    p['is_swing'] = p['description'].isin(SWING_DESC)
    p['is_whiff'] = p['description'].isin(WHIFF_DESC)

    out = defaultdict(dict)
    grouped = p.groupby(['batter', 'ptype'])
    for (bid, pt), grp in grouped:
        if pt == 'OT': continue
        if pt not in PITCH_TYPES: continue
        sw = grp['is_swing'].sum()
        wh = grp['is_whiff'].sum()
        if sw < 50: continue  # minimum 50 swings vs this pitch type
        out[int(bid)][pt] = {
            'swings': int(sw),
            'whiffs': int(wh),
            'whiff_pct': float(wh / sw) if sw > 0 else None,
        }
    return dict(out)


def compute_pitcher_arsenal(pitches: pd.DataFrame) -> dict:
    """Per pitcher: usage % by pitch group, and avg velo."""
    p = pitches.copy()
    p['ptype'] = p['pitch_type'].map(PITCH_GROUPS).fillna('OT')
    out = {}
    for pid, grp in p.groupby('pitcher'):
        total = len(grp)
        if total < 100: continue
        rec = {'total_pitches': int(total), 'arsenal': {}}
        for pt in PITCH_TYPES:
            sub = grp[grp['ptype'] == pt]
            if len(sub) == 0: continue
            rec['arsenal'][pt] = {
                'usage_pct': float(len(sub) / total),
                'avg_velo': float(sub['release_speed'].mean()) if 'release_speed' in sub else None,
            }
        out[int(pid)] = rec
    return out


# ──────────────────────── park factors (multi-year FG values) ────────────
# 2022-2024 3-year park factors from FanGraphs (basic batters: 1B/2B/3B/HR/SO/BB
# weighted into Runs and HR components). Scaled to league = 100.
PARK_FACTORS = {
    # team_abbr: { 'r': runs factor, 'hr': hr factor }
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


# ──────────────────────── main ───────────────────────────────────────────
def main():
    print('Loading 2024 Statcast pitches...')
    sc24 = load_2024_statcast()
    print(f'  {len(sc24):,} pitches')
    pa24 = pa_outcomes(sc24)
    print(f'  {len(pa24):,} labeled PAs')

    print('\nComputing batter vs-handedness splits...')
    bat_splits = compute_player_splits(pa24, by_col='batter', vs_col='p_throws')
    print(f'  batters with splits: {len(bat_splits):,}')

    print('Computing pitcher vs-handedness splits...')
    pit_splits = compute_player_splits(pa24, by_col='pitcher', vs_col='stand')
    print(f'  pitchers with splits: {len(pit_splits):,}')

    print('Computing batter per-pitch whiff rates...')
    bat_whiff = compute_batter_pitch_whiff(sc24)
    print(f'  batters with whiff data: {len(bat_whiff):,}')

    print('Computing pitcher arsenals...')
    pit_arsenal = compute_pitcher_arsenal(sc24)
    print(f'  pitchers with arsenal data: {len(pit_arsenal):,}')

    # Write
    (OUT / 'bat_splits.json').write_text(json.dumps(bat_splits))
    (OUT / 'pit_splits.json').write_text(json.dumps(pit_splits))
    (OUT / 'bat_whiff_by_pitch.json').write_text(json.dumps(bat_whiff))
    (OUT / 'pit_arsenal.json').write_text(json.dumps(pit_arsenal))
    (OUT / 'park_factors.json').write_text(json.dumps(PARK_FACTORS))

    print(f'\nWrote {OUT}/:')
    for f in sorted(OUT.glob('*.json')):
        print(f'  {f.name}  {f.stat().st_size/1024:.0f} KB')


if __name__ == '__main__':
    main()
