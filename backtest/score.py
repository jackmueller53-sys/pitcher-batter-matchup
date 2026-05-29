"""
Score the v1 matchup model against 2025 PA outcomes using 2024 season-final
features (no lookahead).

Outputs:
  - backtest/report_<tag>.md     human-readable summary
  - backtest/cache/preds_<tag>.parquet  raw predictions
"""
import sys
import json
import math
import argparse
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from model import matchup, pct


# ──────────────────────────── Field translation ────────────────────────────
# FanGraphs JSON column names → our internal model's snake_case fields.
# This mirrors mapBatter/mapPitcher in scripts/fetch-data.js.
BAT_MAP = {
    'xMLBAMID': 'id', 'Bats': 'bats', 'PA': 'pa', 'AB': 'ab', 'HR': 'hr',
    'AVG': 'avg', 'OBP': 'obp', 'SLG': 'slg', 'ISO': 'iso', 'BABIP': 'babip',
    'wOBA': 'woba', 'wRC+': 'wrc_plus',
    'K%': 'k_pct', 'BB%': 'bb_pct',
}
PIT_MAP = {
    'xMLBAMID': 'id', 'Throws': 'throws', 'IP': 'ip', 'GS': 'gs', 'G': 'g',
    'ERA': 'era', 'FIP': 'fip', 'xERA': 'xera', 'WAR': 'war', 'BABIP': 'babip',
    'K%': 'k_pct', 'BB%': 'bb_pct', 'HR/9': 'hr_per_9',
}


def fg_to_records(fg_df: pd.DataFrame, col_map: dict) -> dict:
    """FG → {mlbam_id: row_dict_in_model_format}"""
    out = {}
    for _, r in fg_df.iterrows():
        rd = {}
        for src, dst in col_map.items():
            if src in fg_df.columns:
                v = r[src]
                if pd.isna(v):
                    rd[dst] = None
                else:
                    rd[dst] = v
        mid = rd.get('id')
        if mid is None or pd.isna(mid):
            continue
        try:
            mid = int(mid)
        except Exception:
            continue
        out[mid] = rd
    return out


def merge_stuff_plus(pit_dict: dict, sp_df: pd.DataFrame) -> dict:
    for _, r in sp_df.iterrows():
        mid = r.get('xMLBAMID')
        if pd.isna(mid): continue
        try: mid = int(mid)
        except: continue
        if mid not in pit_dict: continue
        for fg_col, dst in [('sp_stuff', 'stuff_plus'),
                            ('sp_location', 'location_plus'),
                            ('sp_pitching', 'pitching_plus')]:
            v = r.get(fg_col)
            if pd.notna(v): pit_dict[mid][dst] = v
    return pit_dict


def compute_league(batters: dict, pitchers: dict) -> dict:
    """Match computeLeague() in scripts/fetch-data.js."""
    def wmean(rows, key, weight_key):
        n, d = 0, 0
        for r in rows:
            v, w = r.get(key), r.get(weight_key) or 1
            if v is not None and isinstance(v, (int, float)) and math.isfinite(v):
                n += v * w; d += w
        return n / d if d > 0 else None
    brows = list(batters.values())
    prows = list(pitchers.values())
    total_pa = sum((r.get('pa') or 0) for r in brows)
    total_hr = sum((r.get('hr') or 0) for r in brows)
    return {
        'bat': {
            'k_pct': wmean(brows, 'k_pct', 'pa'),
            'bb_pct': wmean(brows, 'bb_pct', 'pa'),
            'woba': wmean(brows, 'woba', 'pa'),
            'babip': wmean(brows, 'babip', 'ab'),
            'hr_per_pa': total_hr / max(1, total_pa),
        },
        'pit': {
            'k_pct': wmean(prows, 'k_pct', 'ip'),
            'bb_pct': wmean(prows, 'bb_pct', 'ip'),
            'babip': wmean(prows, 'babip', 'ip'),
            'hr_per_pa': (wmean(prows, 'hr_per_9', 'ip') or 0) / 38,
        },
    }


# ──────────────────────────── Metrics ────────────────────────────
EVENTS = ['K', 'BB', 'HBP', 'HR', 'triple', 'double', 'single', 'out_in_play']

def log_loss(pred_probs: list[dict], actuals: list[str]) -> float:
    """Multinomial log loss, base e."""
    n, s = 0, 0.0
    for p, y in zip(pred_probs, actuals):
        v = p.get(y, 0.0) or 0.0
        v = max(v, 1e-9)
        s -= math.log(v)
        n += 1
    return s / max(1, n)


def per_event_calibration(pred_probs, actuals, event: str, bins: int = 10):
    """Reliability bins for a single event type."""
    pairs = [(p.get(event, 0) or 0, 1.0 if y == event else 0.0)
             for p, y in zip(pred_probs, actuals)]
    pairs.sort(key=lambda x: x[0])
    n = len(pairs)
    out = []
    for i in range(bins):
        a = i * n // bins
        b = (i + 1) * n // bins
        chunk = pairs[a:b]
        if not chunk: continue
        avg_p = sum(p for p, _ in chunk) / len(chunk)
        actual = sum(y for _, y in chunk) / len(chunk)
        out.append({'bin': i, 'n': len(chunk),
                    'predicted': avg_p, 'actual': actual,
                    'diff': actual - avg_p})
    return out


def per_event_mae(pred_probs, actuals, event: str) -> float:
    p_arr = np.array([p.get(event, 0) or 0 for p in pred_probs])
    y_arr = np.array([1.0 if y == event else 0.0 for y in actuals])
    return float(np.abs(p_arr.mean() - y_arr.mean()))


# ──────────────────────────── Main ────────────────────────────
def main(tag='v1', limit=None):
    print(f'Loading 2024 features + 2025 PA labels (tag={tag})...')
    bat24 = pd.read_parquet(HERE / 'cache' / 'fg_2024_bat.parquet')
    pit24 = pd.read_parquet(HERE / 'cache' / 'fg_2024_pit.parquet')
    sp24  = pd.read_parquet(HERE / 'cache' / 'fg_2024_stuffplus.parquet')
    pa25  = pd.read_parquet(HERE / 'cache' / 'pa_2025.parquet')

    print(f'  batters={len(bat24)} pitchers={len(pit24)} stuffplus={len(sp24)} '
          f'PAs={len(pa25):,}')

    batters = fg_to_records(bat24, BAT_MAP)
    pitchers = merge_stuff_plus(fg_to_records(pit24, PIT_MAP), sp24)
    league = compute_league(batters, pitchers)
    print(f'  in-model batters={len(batters)} pitchers={len(pitchers)}')
    print(f'  league: K%={league["bat"]["k_pct"]:.3f} '
          f'BB%={league["bat"]["bb_pct"]:.3f} '
          f'HR/PA={league["bat"]["hr_per_pa"]:.4f}')

    pa = pa25.copy()
    if limit:
        pa = pa.sample(n=min(limit, len(pa)), random_state=42)
        print(f'  using sample of {len(pa):,} PAs')

    # For each PA, look up pitcher + batter; fall back if either is missing.
    pred_probs, actuals = [], []
    skipped_no_pit, skipped_no_bat = 0, 0
    for row in pa.itertuples(index=False):
        pit_id, bat_id = int(row.pitcher), int(row.batter)
        pit = pitchers.get(pit_id)
        bat = batters.get(bat_id)
        if not pit:
            skipped_no_pit += 1; continue
        if not bat:
            skipped_no_bat += 1; continue
        # Apply the live-game throws/bats from Statcast (rookies / mid-season trades
        # may have null in our FG dictionary).
        if not pit.get('throws') and row.throws: pit['throws'] = row.throws
        if not bat.get('bats')   and row.bats:   bat['bats']   = row.bats
        m = matchup(pit, bat, league)
        if not m: continue
        pred_probs.append(m['p'])
        actuals.append(row.outcome)

    print(f'\n  scored {len(pred_probs):,} PAs '
          f'(skipped: no_pit={skipped_no_pit:,} no_bat={skipped_no_bat:,})')

    # ── Metrics ──
    ll = log_loss(pred_probs, actuals)
    actual_dist = Counter(actuals)
    n = len(actuals)
    pred_means = {e: np.mean([(p.get(e) or 0) for p in pred_probs]) for e in EVENTS}
    actual_rates = {e: actual_dist.get(e, 0) / n for e in EVENTS}

    print(f'\n=== Metrics (tag={tag}) ===')
    print(f'  Multinomial log loss:  {ll:.4f}')
    print(f'\n  Per-event predicted vs actual rates:')
    print(f'  {"event":<14}{"predicted":>10}{"actual":>10}{"diff":>10}')
    for e in EVENTS:
        diff = pred_means[e] - actual_rates[e]
        print(f'  {e:<14}{pred_means[e]:>9.3%}{actual_rates[e]:>10.3%}{diff:>+10.3%}')

    # ── Reliability for top events ──
    rel_K = per_event_calibration(pred_probs, actuals, 'K')
    rel_BB = per_event_calibration(pred_probs, actuals, 'BB')
    rel_HR = per_event_calibration(pred_probs, actuals, 'HR')

    print(f'\n  K reliability (10 bins):')
    print(f'  {"bin":<6}{"n":>8}{"predicted":>12}{"actual":>10}{"diff":>10}')
    for b in rel_K:
        print(f'  {b["bin"]:<6}{b["n"]:>8}{b["predicted"]:>11.2%}{b["actual"]:>10.2%}{b["diff"]:>+10.2%}')

    # ── Write report ──
    report_path = HERE / f'report_{tag}.md'
    with open(report_path, 'w') as f:
        f.write(f'# Backtest report — `{tag}`\n\n')
        f.write(f'- Training features: 2024 season-final FanGraphs (bat qual=50, pit qual=20, Stuff+ qual=0)\n')
        f.write(f'- Test labels: 2025 Statcast PA outcomes\n')
        f.write(f'- PAs scored: **{len(actuals):,}** (skipped no_pit={skipped_no_pit:,}, no_bat={skipped_no_bat:,})\n\n')
        f.write(f'## Headline\n\n')
        f.write(f'- **Multinomial log loss: {ll:.4f}**\n\n')
        f.write(f'## Per-event predicted vs actual\n\n')
        f.write('| event | predicted | actual | diff |\n|---|---:|---:|---:|\n')
        for e in EVENTS:
            diff = pred_means[e] - actual_rates[e]
            f.write(f'| {e} | {pred_means[e]:.2%} | {actual_rates[e]:.2%} | {diff:+.2%} |\n')
        f.write(f'\n## Reliability — K\n\n')
        f.write('| bin | n | predicted | actual | diff |\n|---|---:|---:|---:|---:|\n')
        for b in rel_K:
            f.write(f'| {b["bin"]} | {b["n"]:,} | {b["predicted"]:.2%} | {b["actual"]:.2%} | {b["diff"]:+.2%} |\n')
        f.write(f'\n## Reliability — BB\n\n')
        f.write('| bin | n | predicted | actual | diff |\n|---|---:|---:|---:|---:|\n')
        for b in rel_BB:
            f.write(f'| {b["bin"]} | {b["n"]:,} | {b["predicted"]:.2%} | {b["actual"]:.2%} | {b["diff"]:+.2%} |\n')
        f.write(f'\n## Reliability — HR\n\n')
        f.write('| bin | n | predicted | actual | diff |\n|---|---:|---:|---:|---:|\n')
        for b in rel_HR:
            f.write(f'| {b["bin"]} | {b["n"]:,} | {b["predicted"]:.2%} | {b["actual"]:.2%} | {b["diff"]:+.2%} |\n')

    print(f'\nWrote {report_path.relative_to(HERE.parent)}')
    return {'log_loss': ll, 'pred_means': pred_means, 'actual_rates': actual_rates}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='v1')
    ap.add_argument('--limit', type=int, default=None,
                    help='Sample N PAs for a faster run')
    args = ap.parse_args()
    main(tag=args.tag, limit=args.limit)
