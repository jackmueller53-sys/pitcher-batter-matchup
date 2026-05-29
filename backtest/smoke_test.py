"""Quick smoke test: pull 14 days of Statcast, verify PA outcome labeling."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from fetch_history import pull_statcast, to_pa_outcomes

if __name__ == '__main__':
    sc = pull_statcast('2025-09-01', '2025-09-14', 'smoke_2wk')
    pa = to_pa_outcomes(sc)
    print(f'\n=== Smoke test ===')
    print(f'Pitches: {len(sc):,}')
    print(f'PAs: {len(pa):,}')
    print(f'Outcome mix:')
    for k, v in pa['outcome'].value_counts(normalize=True).round(4).items():
        print(f'  {k:<14}{v:.2%}')
