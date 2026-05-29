"""
Score the enhanced (v1.1) matchup model. Mirrors score.py but loads the
extra feature files and passes them as ctx to the model.
"""
import sys
import json
import math
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from model_v5 import matchup
from score import (BAT_MAP, PIT_MAP, fg_to_records, merge_stuff_plus,
                   compute_league, log_loss, per_event_calibration,
                   per_event_mae, EVENTS)


def main(tag='v2', limit=None):
    print(f'Loading features (tag={tag})...')
    bat24 = pd.read_parquet(HERE / 'cache' / 'fg_2024_bat.parquet')
    pit24 = pd.read_parquet(HERE / 'cache' / 'fg_2024_pit.parquet')
    sp24  = pd.read_parquet(HERE / 'cache' / 'fg_2024_stuffplus.parquet')
    pa25  = pd.read_parquet(HERE / 'cache' / 'pa_2025.parquet')

    FEAT = HERE / 'features'
    bat_splits   = {int(k): v for k, v in json.loads((FEAT / 'bat_splits.json').read_text()).items()}
    pit_splits   = {int(k): v for k, v in json.loads((FEAT / 'pit_splits.json').read_text()).items()}
    bat_whiff    = {int(k): v for k, v in json.loads((FEAT / 'bat_whiff_by_pitch.json').read_text()).items()}
    pit_arsenal  = {int(k): v for k, v in json.loads((FEAT / 'pit_arsenal.json').read_text()).items()}
    park_factors = json.loads((FEAT / 'park_factors.json').read_text())

    print(f'  bat_splits={len(bat_splits)} pit_splits={len(pit_splits)}')
    print(f'  bat_whiff={len(bat_whiff)} pit_arsenal={len(pit_arsenal)}')

    batters = fg_to_records(bat24, BAT_MAP)
    pitchers = merge_stuff_plus(fg_to_records(pit24, PIT_MAP), sp24)
    league = compute_league(batters, pitchers)

    pa = pa25.copy()
    if limit:
        pa = pa.sample(n=min(limit, len(pa)), random_state=42)

    pred_probs, actuals = [], []
    used_splits = used_whiff = used_park = 0
    skipped_no_pit = skipped_no_bat = 0

    for row in pa.itertuples(index=False):
        pit_id, bat_id = int(row.pitcher), int(row.batter)
        pit = pitchers.get(pit_id)
        bat = batters.get(bat_id)
        if not pit: skipped_no_pit += 1; continue
        if not bat: skipped_no_bat += 1; continue
        if not pit.get('throws') and row.throws: pit['throws'] = row.throws
        if not bat.get('bats')   and row.bats:   bat['bats']   = row.bats

        ctx = {
            'bat_split':    bat_splits.get(bat_id),
            'pit_split':    pit_splits.get(pit_id),
            'bat_whiff':    bat_whiff.get(bat_id),
            'pit_arsenal':  pit_arsenal.get(pit_id),
            'park_factors': park_factors,
            'home_team':    row.home_team,
        }
        m = matchup(pit, bat, league, ctx)
        if not m: continue
        u = m['_used']
        if u['splits']: used_splits += 1
        if u.get('whiff_signal') is not None: used_whiff += 1
        if u['park_hr'] != 1.0: used_park += 1
        pred_probs.append(m['p'])
        actuals.append(row.outcome)

    n = len(pred_probs)
    print(f'\n  scored {n:,} PAs (skipped: no_pit={skipped_no_pit:,} no_bat={skipped_no_bat:,})')
    print(f'  features used: splits={used_splits:,} ({used_splits/n:.1%}) '
          f'whiff_signal={used_whiff:,} ({used_whiff/n:.1%}) '
          f'park={used_park:,} ({used_park/n:.1%})')

    ll = log_loss(pred_probs, actuals)
    actual_dist = Counter(actuals)
    pred_means = {e: float(np.mean([(p.get(e) or 0) for p in pred_probs])) for e in EVENTS}
    actual_rates = {e: actual_dist.get(e, 0) / n for e in EVENTS}

    print(f'\n=== Metrics (tag={tag}) ===')
    print(f'  Multinomial log loss:  {ll:.4f}')
    print(f'  Per-event predicted vs actual:')
    print(f'  {"event":<14}{"predicted":>10}{"actual":>10}{"diff":>10}')
    for e in EVENTS:
        diff = pred_means[e] - actual_rates[e]
        print(f'  {e:<14}{pred_means[e]:>9.3%}{actual_rates[e]:>10.3%}{diff:>+10.3%}')

    rel_K = per_event_calibration(pred_probs, actuals, 'K')
    rel_BB = per_event_calibration(pred_probs, actuals, 'BB')
    rel_HR = per_event_calibration(pred_probs, actuals, 'HR')

    print(f'\n  K reliability (10 bins):')
    print(f'  {"bin":<6}{"n":>8}{"predicted":>12}{"actual":>10}{"diff":>10}')
    for b in rel_K:
        print(f'  {b["bin"]:<6}{b["n"]:>8}{b["predicted"]:>11.2%}{b["actual"]:>10.2%}{b["diff"]:>+10.2%}')

    report = HERE / f'report_{tag}.md'
    with open(report, 'w') as f:
        f.write(f'# Backtest — `{tag}` (Tier A enhanced)\n\n')
        f.write(f'- PAs scored: **{n:,}** (skipped no_pit={skipped_no_pit:,}, no_bat={skipped_no_bat:,})\n')
        f.write(f'- vs-handedness splits applied: {used_splits/n:.1%}\n')
        f.write(f'- per-pitch whiff bump applied: {used_whiff/n:.1%}\n')
        f.write(f'- park factor applied: {used_park/n:.1%}\n')
        f.write(f'## Headline\n\n- **Multinomial log loss: {ll:.4f}**\n\n')
        f.write(f'## Per-event predicted vs actual\n\n')
        f.write('| event | predicted | actual | diff |\n|---|---:|---:|---:|\n')
        for e in EVENTS:
            f.write(f'| {e} | {pred_means[e]:.2%} | {actual_rates[e]:.2%} | {pred_means[e]-actual_rates[e]:+.2%} |\n')
        for name, rel in [('K', rel_K), ('BB', rel_BB), ('HR', rel_HR)]:
            f.write(f'\n## Reliability — {name}\n\n')
            f.write('| bin | n | predicted | actual | diff |\n|---|---:|---:|---:|---:|\n')
            for b in rel:
                f.write(f'| {b["bin"]} | {b["n"]:,} | {b["predicted"]:.2%} | {b["actual"]:.2%} | {b["diff"]:+.2%} |\n')
    print(f'\nWrote {report.relative_to(HERE.parent)}')
    return {'log_loss': ll}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='v2')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    main(tag=args.tag, limit=args.limit)
