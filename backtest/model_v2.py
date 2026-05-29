"""
Enhanced matchup model (v1.1 / Tier A).

Changes vs model.py (v1):
  1. Vs-handedness splits replace the flat platoon multiplier when both
     batter splits + pitcher splits are available for the relevant hand.
  2. Per-pitch-type matchup: batter's whiff% vs each pitch group is weighted
     by the pitcher's arsenal usage to produce a "whiff_weighted_bump" on K%.
  3. Park factors multiply HR% by park_hr/100 and tilt the global runs
     environment via park_r/100 (light tilt on all in-play hit rates).
  4. Reliever vs starter: relievers get +3.5% absolute K (well-documented
     reliever-K-uplift effect).
  5. Triple split fix: 78/18.5/3.5 (matches actual MLB 1B/2B/3B distribution).
"""
import math
from typing import Optional, Dict


# ─────────────── helpers (shared with v1) ─────────────────
def pct(v) -> Optional[float]:
    if v is None: return None
    try: f = float(v)
    except: return None
    if not math.isfinite(f): return None
    return f / 100 if f > 1 else f

def log5(b, p, l) -> Optional[float]:
    if b is None or p is None or l is None: return None
    if not (0 < l < 1): return None
    eps = 1e-6
    b = min(max(b, eps), 1 - eps)
    p = min(max(p, eps), 1 - eps)
    l = min(max(l, eps), 1 - eps)
    num = (b * p) / l
    den = num + ((1 - b) * (1 - p)) / (1 - l)
    return num / den

def clip01(x): return max(0, min(1, x))


# ─────────────── platoon (fallback when splits absent) ─────────────────
def platoon_factors(bats, throws):
    if not bats or not throws: return {'k': 1, 'bb': 1, 'hr': 1}
    if bats == 'S': return {'k': 1, 'bb': 1, 'hr': 1}
    same = (bats == throws)
    return ({'k': 1.04, 'bb': 0.97, 'hr': 0.94} if same
            else {'k': 0.97, 'bb': 1.03, 'hr': 1.06})


# ─────────────── per-pitch matchup ─────────────────
PITCH_TYPES = ['FF', 'SI', 'FC', 'SL', 'CU', 'CH', 'FS']

def pitch_matchup_k_bump(pit_arsenal: Dict, bat_whiff: Dict) -> float:
    """
    Returns a multiplicative K bump.

    Compute sum_t (pit_usage_t × bat_whiff_t). Compare against the league-avg
    whiff per swing (~24%). Translate the delta into a K factor: +5% whiff
    above league → +5% multiplicative K. Capped at ±15%.
    """
    if not pit_arsenal or not bat_whiff: return 1.0
    arsenal = pit_arsenal.get('arsenal', {})
    if not arsenal: return 1.0
    weighted, weight_total = 0.0, 0.0
    for pt in PITCH_TYPES:
        if pt not in arsenal: continue
        usage = arsenal[pt].get('usage_pct', 0) or 0
        bw = bat_whiff.get(pt)
        if not bw: continue
        w = bw.get('whiff_pct')
        if w is None: continue
        weighted += usage * w
        weight_total += usage
    if weight_total < 0.4:  # need ≥40% of pitches matched to be reliable
        return 1.0
    avg_w = weighted / weight_total
    LG_WHIFF = 0.24
    delta = avg_w - LG_WHIFF
    bump = 1.0 + delta * 1.5
    return max(0.85, min(1.15, bump))


# ─────────────── reliever uplift ─────────────────
def reliever_factor(pit_row) -> Dict:
    """+3.5% K absolute, slightly elevated HR (relievers throw harder but
    more mistakes). FG: ~+3.5% K, +0.4% HR/PA, +0.1% BB."""
    g, gs = pit_row.get('g') or 0, pit_row.get('gs') or 0
    is_relief = g > 0 and (gs / g) < 0.5
    if not is_relief:
        return {'k_add': 0.0, 'hr_mul': 1.0, 'bb_add': 0.0, 'is_relief': False}
    return {'k_add': 0.035, 'hr_mul': 1.06, 'bb_add': 0.001, 'is_relief': True}


# ─────────────── park factor ─────────────────
def park_factor(park_factors: Dict, home_team: str) -> Dict:
    if not park_factors or not home_team: return {'r': 1.0, 'hr': 1.0}
    pf = park_factors.get(home_team)
    if not pf: return {'r': 1.0, 'hr': 1.0}
    return {'r': pf['r'] / 100.0, 'hr': pf['hr'] / 100.0}


# ─────────────── core matchup ─────────────────
def matchup(pit, bat, league, ctx=None):
    """
    ctx = optional dict:
      - bat_split:    {'L': {...}, 'R': {...}} for THIS batter
      - pit_split:    {'L': {...}, 'R': {...}} for THIS pitcher
      - bat_whiff:    per-pitch whiff dict for THIS batter
      - pit_arsenal:  arsenal dict for THIS pitcher
      - park:         park factor dict for the home team
    """
    if not pit or not bat or not league: return None
    ctx = ctx or {}

    # ── 1. Pick the right rate basis ──
    # If we have vs-handedness splits for both, use them — they're already
    # platoon-adjusted, so we skip the flat multiplier.
    use_splits = False
    bK, bBB, bHR, bBABIP = pct(bat.get('k_pct')), pct(bat.get('bb_pct')), None, pct(bat.get('babip'))
    pK, pBB, pHR, pBABIP = pct(pit.get('k_pct')), pct(pit.get('bb_pct')), None, pct(pit.get('babip'))

    # Default HR rates from overall stats
    if bat.get('hr') and bat.get('pa'):
        bHR = bat['hr'] / bat['pa']
    elif bat.get('iso') is not None:
        bHR = pct(bat['iso']) * 0.25
    if pit.get('hr_per_pa') is not None:
        pHR = pct(pit['hr_per_pa'])
    elif pit.get('hr_per_9') is not None:
        pHR = pit['hr_per_9'] / 38

    bat_split = ctx.get('bat_split')
    pit_split = ctx.get('pit_split')
    p_throws = pit.get('throws')
    b_bats = bat.get('bats')
    if bat_split and pit_split and p_throws and b_bats:
        bs = bat_split.get(p_throws)  # batter's stats vs this hand of pitcher
        ps = pit_split.get(b_bats)    # pitcher's stats vs this hand of batter
        if bs and ps and bs.get('pa', 0) >= 50 and ps.get('pa', 0) >= 50:
            bK    = bs.get('k_pct',  bK)
            bBB   = bs.get('bb_pct', bBB)
            bHR   = bs.get('hr_per_pa', bHR)
            bBABIP= bs.get('babip',  bBABIP)
            pK    = ps.get('k_pct',  pK)
            pBB   = ps.get('bb_pct', pBB)
            pHR   = ps.get('hr_per_pa', pHR)
            pBABIP= ps.get('babip',  pBABIP)
            use_splits = True

    lK    = pct(league['bat']['k_pct'])
    lBB   = pct(league['bat']['bb_pct'])
    lHR   = league['bat']['hr_per_pa']
    lBABIP= pct(league['bat'].get('babip'))

    # ── 2. Log5 ──
    pK_pred  = log5(bK, pK, lK)
    pBB_pred = log5(bBB, pBB, lBB)
    pHR_pred = log5(bHR, pHR, lHR)

    # ── 3. Tilts ──
    if not use_splits:
        plat = platoon_factors(b_bats, p_throws)
        if pK_pred  is not None: pK_pred  *= plat['k']
        if pBB_pred is not None: pBB_pred *= plat['bb']
        if pHR_pred is not None: pHR_pred *= plat['hr']

    # Pitch-type K bump
    bump = pitch_matchup_k_bump(ctx.get('pit_arsenal'), ctx.get('bat_whiff'))
    if pK_pred is not None: pK_pred *= bump

    # Reliever bump
    rel = reliever_factor(pit)
    if pK_pred  is not None: pK_pred  = clip01(pK_pred  + rel['k_add'])
    if pBB_pred is not None: pBB_pred = clip01(pBB_pred + rel['bb_add'])
    if pHR_pred is not None: pHR_pred = clip01(pHR_pred * rel['hr_mul'])

    # Park factor
    park = park_factor(ctx.get('park_factors'), ctx.get('home_team'))
    if pHR_pred is not None: pHR_pred = clip01(pHR_pred * park['hr'])

    # Hit-by-pitch (flat)
    pHBP = 0.009

    p_contact = clip01(1 - (pK_pred or 0) - (pBB_pred or 0) - (pHR_pred or 0) - pHBP)

    matchup_babip = log5(bBABIP, pBABIP, lBABIP) or lBABIP or 0.295
    # Slight runs-env tilt on BABIP via park (Coors BABIP is up too)
    matchup_babip = clip01(matchup_babip * (1 + (park['r'] - 1) * 0.3))

    p_hip = p_contact * matchup_babip
    p_oip = p_contact * (1 - matchup_babip)
    # Corrected 1B/2B/3B distribution: 78/18.5/3.5 (matches MLB 2020-2024 avg).
    # Adjust slightly for hitter type: high-iso → more 2B share. v1.1 keeps flat.
    p1B = p_hip * 0.78
    p2B = p_hip * 0.185
    p3B = p_hip * 0.035

    xwOBA = ((pBB_pred or 0) * 0.690 + pHBP * 0.720
             + p1B * 0.890 + p2B * 1.271 + p3B * 1.616
             + (pHR_pred or 0) * 2.101)
    lg_wOBA = league['bat'].get('woba') or 0.318
    edge = max(-100, min(100, ((xwOBA - lg_wOBA) / 0.060) * 50))

    return {
        'p': {
            'K': pK_pred, 'BB': pBB_pred, 'HR': pHR_pred,
            'HBP': pHBP, 'single': p1B, 'double': p2B, 'triple': p3B,
            'out_in_play': p_oip,
        },
        'xwOBA': xwOBA,
        'edge': edge,
        '_used': {
            'splits': use_splits,
            'pitch_bump': bump,
            'park_hr': park['hr'],
            'is_relief': rel['is_relief'],
        },
    }
