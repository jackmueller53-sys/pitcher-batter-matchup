"""
v1.2 matchup model — Tier A done right.

Diagnosed problems with v2:
  - Reliever K-bump and pitch-matchup bump both inflate K% on TOP of the
    pitcher's already-reliever-elevated K% rate. Double-counting → worse.
  - Log5 over-stacks in extreme matchups (top K bin off by -13%).

v3 changes:
  - Drop reliever / pitch bumps that operate on pre-aggregated K%.
  - Shrink log5 toward an additive baseline (α=0.6) to prevent extreme
    over-prediction in tail buckets — well-known practical fix.
  - Keep vs-handedness splits (genuine new info).
  - Keep park factor (genuine new info).
  - Use per-pitch matchup only as an explainability/edge signal, NOT to
    move the K prediction.
"""
import math
from typing import Optional, Dict


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


def additive(b, p, l):
    """Naive additive prediction: batter's deviation + pitcher's deviation."""
    if b is None or p is None or l is None: return None
    return max(0, min(1, b + p - l))


def blend(b, p, l, alpha=0.6):
    """Shrunk log5: α × log5 + (1-α) × additive. Less aggressive in extremes."""
    a = log5(b, p, l)
    n = additive(b, p, l)
    if a is None and n is None: return None
    if a is None: return n
    if n is None: return a
    return alpha * a + (1 - alpha) * n


def clip01(x): return max(0, min(1, x))


def platoon_factors(bats, throws):
    if not bats or not throws: return {'k': 1, 'bb': 1, 'hr': 1}
    if bats == 'S': return {'k': 1, 'bb': 1, 'hr': 1}
    same = (bats == throws)
    return ({'k': 1.04, 'bb': 0.97, 'hr': 0.94} if same
            else {'k': 0.97, 'bb': 1.03, 'hr': 1.06})


PITCH_TYPES = ['FF', 'SI', 'FC', 'SL', 'CU', 'CH', 'FS']

def pitch_matchup_signal(pit_arsenal, bat_whiff) -> Optional[float]:
    """Returns weighted batter whiff vs this pitcher's arsenal, or None.
    Used for explainability only — NOT as a K multiplier."""
    if not pit_arsenal or not bat_whiff: return None
    arsenal = pit_arsenal.get('arsenal', {})
    if not arsenal: return None
    weighted, w_total = 0.0, 0.0
    for pt in PITCH_TYPES:
        if pt not in arsenal: continue
        u = arsenal[pt].get('usage_pct', 0) or 0
        bw = bat_whiff.get(pt)
        if not bw: continue
        wp = bw.get('whiff_pct')
        if wp is None: continue
        weighted += u * wp
        w_total += u
    if w_total < 0.4: return None
    return weighted / w_total


def park_factor(park_factors, home_team):
    if not park_factors or not home_team: return {'r': 1.0, 'hr': 1.0}
    pf = park_factors.get(home_team)
    if not pf: return {'r': 1.0, 'hr': 1.0}
    return {'r': pf['r'] / 100.0, 'hr': pf['hr'] / 100.0}


def matchup(pit, bat, league, ctx=None):
    if not pit or not bat or not league: return None
    ctx = ctx or {}

    bK = pct(bat.get('k_pct')); bBB = pct(bat.get('bb_pct'))
    bBABIP = pct(bat.get('babip'))
    pK = pct(pit.get('k_pct')); pBB = pct(pit.get('bb_pct'))
    pBABIP = pct(pit.get('babip'))

    bHR = None
    if bat.get('hr') is not None and bat.get('pa'):
        bHR = bat['hr'] / bat['pa']
    elif bat.get('iso') is not None:
        bHR = pct(bat['iso']) * 0.25
    pHR = pit.get('hr_per_pa')
    if pHR is None and pit.get('hr_per_9') is not None:
        pHR = pit['hr_per_9'] / 38

    # ── vs-handedness splits override (when available + sufficient sample) ──
    bat_split = ctx.get('bat_split')
    pit_split = ctx.get('pit_split')
    p_throws = pit.get('throws'); b_bats = bat.get('bats')
    use_splits = False
    if bat_split and pit_split and p_throws and b_bats:
        bs = bat_split.get(p_throws)
        ps = pit_split.get(b_bats)
        if bs and ps and bs.get('pa', 0) >= 50 and ps.get('pa', 0) >= 50:
            bK     = bs.get('k_pct',     bK)
            bBB    = bs.get('bb_pct',    bBB)
            bHR    = bs.get('hr_per_pa', bHR)
            bBABIP = bs.get('babip',     bBABIP)
            pK     = ps.get('k_pct',     pK)
            pBB    = ps.get('bb_pct',    pBB)
            pHR    = ps.get('hr_per_pa', pHR)
            pBABIP = ps.get('babip',     pBABIP)
            use_splits = True

    lK     = pct(league['bat']['k_pct'])
    lBB    = pct(league['bat']['bb_pct'])
    lHR    = league['bat']['hr_per_pa']
    lBABIP = pct(league['bat'].get('babip'))

    # ── Shrunk log5 blend (α=0.6) ──
    pK_pred  = blend(bK, pK, lK)
    pBB_pred = blend(bBB, pBB, lBB)
    pHR_pred = blend(bHR, pHR, lHR)

    # ── Flat platoon only as fallback when splits absent ──
    if not use_splits:
        plat = platoon_factors(b_bats, p_throws)
        if pK_pred  is not None: pK_pred  *= plat['k']
        if pBB_pred is not None: pBB_pred *= plat['bb']
        if pHR_pred is not None: pHR_pred *= plat['hr']

    # ── Park factor — applies to HR rate (clear new info per game) ──
    park = park_factor(ctx.get('park_factors'), ctx.get('home_team'))
    if pHR_pred is not None: pHR_pred = clip01(pHR_pred * park['hr'])

    if pK_pred  is not None: pK_pred  = clip01(pK_pred)
    if pBB_pred is not None: pBB_pred = clip01(pBB_pred)

    pHBP = 0.009

    p_contact = clip01(1 - (pK_pred or 0) - (pBB_pred or 0) - (pHR_pred or 0) - pHBP)

    matchup_babip = blend(bBABIP, pBABIP, lBABIP) or lBABIP or 0.295
    matchup_babip = clip01(matchup_babip * (1 + (park['r'] - 1) * 0.3))

    p_hip = p_contact * matchup_babip
    p_oip = p_contact * (1 - matchup_babip)
    # Corrected MLB 1B/2B/3B distribution
    p1B = p_hip * 0.78
    p2B = p_hip * 0.185
    p3B = p_hip * 0.035

    xwOBA = ((pBB_pred or 0) * 0.690 + pHBP * 0.720
             + p1B * 0.890 + p2B * 1.271 + p3B * 1.616
             + (pHR_pred or 0) * 2.101)
    lg_wOBA = league['bat'].get('woba') or 0.318
    edge = max(-100, min(100, ((xwOBA - lg_wOBA) / 0.060) * 50))

    # Pitch-matchup as explainability signal (not used in K prediction)
    whiff_sig = pitch_matchup_signal(ctx.get('pit_arsenal'), ctx.get('bat_whiff'))

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
            'park_hr': park['hr'],
            'whiff_signal': whiff_sig,
        },
    }
