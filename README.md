# Pitcher / Batter Matchup Analyzer

Single-PA matchup model. Pick a pitcher + a batter; get the predicted
outcome distribution, an expected wOBA, an edge score, and a "why" panel
that calls out the biggest drivers.

## Model in one paragraph

For each event type (K, BB, HR), blend the batter's career rate and the
pitcher's allowed rate against the league baseline using **log5 (odds-ratio
form)**. Then apply two adjustments: a **Stuff+ tilt** (each 10-point
Stuff+ above 100 bumps the predicted K rate by ~0.4 percentage points; mirrored
for HR via Location+), and a **handedness platoon split** (Tango / Lichtman /
Dolphin, *The Book*: same-handed matchup ⇒ ~+7% K rate, –6% BB rate, –10%
HR rate). Whatever PA probability is left over after K + BB + HR + HBP gets
split into 1B / 2B / 3B / out using a log5-blended BABIP. Expected wOBA is
computed from the resulting distribution with FG linear weights.

Everything is deterministic and inspectable — no training, no calibration set.

## Data

Pulled daily by `.github/workflows/fetch-data.yml` from FanGraphs:

| Endpoint | Used for |
|---|---|
| `/api/leaders/major-league/data?stats=bat&type=8` | batter standard stats |
| `/api/leaders/major-league/data?stats=pit&type=8` | pitcher standard stats |
| `/api/leaders/major-league/data?stats=pit&type=36` | Stuff+ / Location+ / Pitching+ |

Snapshots land in `data/{pitchers,batters,league,meta}.json`. The deploy
workflow ships only those + the static frontend.

## Run locally

```bash
node scripts/fetch-data.js     # pulls fresh data
npx serve .                    # serves at http://localhost:3000
```

## Deploy

GitHub Pages via the included `deploy.yml`. Note: **GitHub Pages from a
private repo requires GitHub Pro**. If you're on the free plan and want
this repo private, host on Cloudflare Pages or Vercel instead — both
deploy a static directory in one command.

## Status

v1. Doesn't yet model: starter-vs-reliever splits, count-state, ballpark,
weather, recent-form weighting. Those are on the v2 list.
