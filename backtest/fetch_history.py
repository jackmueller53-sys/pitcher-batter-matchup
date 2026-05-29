"""
Fetch 2024 season-final + 2025 PA-level outcomes for the backtest.

Train set (frozen features): 2024 season-final stats per player via FanGraphs.
Test set (labels):           2025 PA outcomes via Statcast (pybaseball).

Caches to backtest/cache/*.parquet so re-runs are fast.
"""
import os
import json
import time
import sys
from pathlib import Path
from datetime import date

import pandas as pd
from pybaseball import statcast, cache as pb_cache

HERE = Path(__file__).parent
CACHE_DIR = HERE / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
pb_cache.enable()

# ──────────────────────────── Statcast PA-level ────────────────────────────
def pull_statcast(start: str, end: str, label: str) -> pd.DataFrame:
    """Pull pitch-level Statcast for a date range, collapse to one row per PA."""
    out = CACHE_DIR / f'statcast_{label}.parquet'
    if out.exists():
        df = pd.read_parquet(out)
        print(f'  cached: {label} → {len(df):,} pitches')
        return df
    print(f'  pulling Statcast {start}..{end} (this can take 5-20 min)...')
    t0 = time.time()
    df = statcast(start_dt=start, end_dt=end)
    print(f'  pulled {len(df):,} pitches in {time.time()-t0:.0f}s')
    df.to_parquet(out, index=False)
    return df


def to_pa_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse pitch-level rows to one row per PA, with a labeled outcome."""
    # Only keep the last pitch of each PA (it carries the event label)
    df = df.sort_values(['game_pk', 'at_bat_number', 'pitch_number'])
    last = df.groupby(['game_pk', 'at_bat_number'], as_index=False).tail(1).copy()

    # Map Statcast `events` → our 8-bucket schema.
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
        # All non-K/BB/HBP/HR/X-base hits are outs-in-play
        if e in ('field_out','grounded_into_double_play','force_out','fielders_choice',
                 'fielders_choice_out','sac_fly','sac_bunt','sac_fly_double_play',
                 'double_play','triple_play','sac_bunt_double_play','field_error',
                 'batter_interference','catcher_interf'):
            return 'out_in_play'
        return None  # intentional walks (drop), unknown

    last['outcome'] = last['events'].map(label)
    out = last[last['outcome'].notna()].copy()
    out = out[['game_pk', 'at_bat_number', 'game_date', 'home_team', 'away_team',
               'pitcher', 'batter', 'p_throws', 'stand', 'outcome',
               'inning', 'inning_topbot', 'balls', 'strikes']].rename(
        columns={'p_throws': 'throws', 'stand': 'bats'})
    return out


# ──────────────────────────── FanGraphs season-final ───────────────────────
# We deliberately re-use the same scrape format as scripts/fetch-data.js so
# the backtest features look exactly like what production uses.
import urllib.request
def fg_url(stats: str, qual: int, ftype: int, season: int) -> str:
    return (f'https://www.fangraphs.com/api/leaders/major-league/data'
            f'?pos=all&stats={stats}&lg=all&qual={qual}&type={ftype}'
            f'&season={season}&season1={season}&ind=0&team=0&pageitems=2000&pagenum=1')

def fg_pull(stats: str, qual: int, ftype: int, season: int, label: str) -> pd.DataFrame:
    out = CACHE_DIR / f'fg_{label}.parquet'
    if out.exists():
        return pd.read_parquet(out)
    print(f'  → FG {label}')
    req = urllib.request.Request(fg_url(stats, qual, ftype, season),
        headers={'User-Agent': 'backtest/1.0'})
    raw = urllib.request.urlopen(req, timeout=60).read()
    j = json.loads(raw.decode())
    df = pd.DataFrame(j.get('data', []))
    df.to_parquet(out, index=False)
    return df


def main():
    print('=== Backtest data pull ===')
    # ── 2024 season-final stats (used as "frozen features" for predicting 2025) ──
    print('FanGraphs 2024 season-final:')
    bat24 = fg_pull('bat', 50, 8, 2024, '2024_bat')
    pit24 = fg_pull('pit', 20, 8, 2024, '2024_pit')
    sp24  = fg_pull('pit',  0, 36, 2024, '2024_stuffplus')
    print(f'  batters={len(bat24)} pitchers={len(pit24)} stuffplus={len(sp24)}')

    # ── 2025 Statcast PA outcomes ──
    print('Statcast 2025 (PA outcomes):')
    sc25 = pull_statcast('2025-03-27', '2025-09-29', '2025')
    pa25 = to_pa_outcomes(sc25)
    pa25.to_parquet(CACHE_DIR / 'pa_2025.parquet', index=False)
    print(f'  PA labeled: {len(pa25):,}')
    print(f'  outcome mix: {pa25["outcome"].value_counts(normalize=True).round(3).to_dict()}')

    print('\nWrote:')
    for f in sorted(CACHE_DIR.glob('*.parquet')):
        print(f'  {f.relative_to(HERE)}  {f.stat().st_size/1024:.0f} KB')


if __name__ == '__main__':
    main()
