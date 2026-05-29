"""
v1.3 model — Tier A + regression to the mean.

The over-confidence at extreme matchups (top K bin off by -7%) is the
regression-to-the-mean failure: seasonal observed rates contain
player-specific noise that doesn't carry forward to next year. We shrink
each player's input rate toward league mean BEFORE log5 using standard
sabermetric shrinkage constants:

  regressed = (n × obs + k × lg) / (n + k)

Where k is the # of PAs needed to make the player's observed rate "half"
informative — k ≈ 175 for K%, 285 for BB%, 1000 for HR/PA, 1000 for BABIP
(see Tom Tango, Wayback regression weights).
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


def clip01(x): return max(0, min(1, x))


def regress(obs_rate, sample_n, lg_rate, k):
    """Empirical Bayes regression: shrink obs toward lg with weight k."""
    if obs_rate is None or sample_n is None or lg_rate is None:
        return obs_rate
    n = max(0, sample_n)
    return (n * obs_rate + k * lg_rate) / (n + k)


# Per-event shrinkage constants (PA-equivalent).
K_SHRINK     = {'k_pct': 175, 'bb_pct': 285, 'hr_per_pa': 900, 'babip': 1100}


def platoon_factors(bats, throws):
    if not bats or not throws: return {'k': 1, 'bb': 1, 'hr': 1}
    if bats == 'S': return {'k': 1, 'bb': 1, 'hr': 1}
    same = (bats == throws)
    return ({'k': 1.04, 'bb': 0.97, 'hr': 0.94} if same
            else {'k': 0.97, 'bb': 1.03, 'hr': 1.06})


PITCH_TYPES = ['FF', 'SI', 'FC', 'SL', 'CU', 'CH', 'FS']

def pitch_matchup_signal(pit_arsenal, bat_whiff):
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
    return (weighted / w_total) if w_total >= 0.4 else None


def park_factor(park_factors, home_team):
    if not park_factors or not home_team: return {'r': 1.0, 'hr': 1.0}
    pf = park_factors.get(home_team)
    if not pf: return {'r': 1.0, 'hr': 1.0}
    return {'r': pf['r'] / 100.0, 'hr': pf['hr'] / 100.0}


def matchup(pit, bat, league, ctx=None):
    if not pit or not bat or not league: return None
    ctx = ctx or {}

    # Pull raw rates
    bK_raw = pct(bat.get('k_pct'));  bBB_raw = pct(bat.get('bb_pct'))
    bBABIP_raw = pct(bat.get('babip'))
    pK_raw = pct(pit.get('k_pct'));  pBB_raw = pct(pit.get('bb_pct'))
    pBABIP_raw = pct(pit.get('babip'))

    bHR_raw = None
    if bat.get('hr') is not None and bat.get('pa'):
        bHR_raw = bat['hr'] / bat['pa']
    elif bat.get('iso') is not None:
        bHR_raw = pct(bat['iso']) * 0.25
    pHR_raw = pit.get('hr_per_pa')
    if pHR_raw is None and pit.get('hr_per_9') is not None:
        pHR_raw = pit['hr_per_9'] / 38

    bat_pa  = bat.get('pa') or 600
    pit_pa  = (pit.get('ip') or 100) * 4.2  # ~4.2 PA per IP

    # Sample size to use for splits
    bat_split = ctx.get('bat_split'); pit_split = ctx.get('pit_split')
    p_throws = pit.get('throws'); b_bats = bat.get('bats')
    use_splits = False
    if bat_split and pit_split and p_throws and b_bats:
        bs = bat_split.get(p_throws); ps = pit_split.get(b_bats)
        if bs and ps and bs.get('pa', 0) >= 50 and ps.get('pa', 0) >= 50:
            bK_raw     = bs.get('k_pct',     bK_raw)
            bBB_raw    = bs.get('bb_pct',    bBB_raw)
            bHR_raw    = bs.get('hr_per_pa', bHR_raw)
            bBABIP_raw = bs.get('babip',     bBABIP_raw)
            pK_raw     = ps.get('k_pct',     pK_raw)
            pBB_raw    = ps.get('bb_pct',    pBB_raw)
            pHR_raw    = ps.get('hr_per_pa', pHR_raw)
            pBABIP_raw = ps.get('babip',     pBABIP_raw)
            bat_pa = bs.get('pa', bat_pa)
            pit_pa = ps.get('pa', pit_pa)
            use_splits = True

    lK     = pct(league['bat']['k_pct'])
    lBB    = pct(league['bat']['bb_pct'])
    lHR    = league['bat']['hr_per_pa']
    lBABIP = pct(league['bat'].get('babip'))

    # Regress toward mean
    bK     = regress(bK_raw,     bat_pa, lK,     K_SHRINK['k_pct'])
    bBB    = regress(bBB_raw,    bat_pa, lBB,    K_SHRINK['bb_pct'])
    bHR    = regress(bHR_raw,    bat_pa, lHR,    K_SHRINK['hr_per_pa'])
    bBABIP = regress(bBABIP_raw, bat_pa, lBABIP, K_SHRINK['babip'])
    pK     = regress(pK_raw,     pit_pa, lK,     K_SHRINK['k_pct'])
    pBB    = regress(pBB_raw,    pit_pa, lBB,    K_SHRINK['bb_pct'])
    pHR    = regress(pHR_raw,    pit_pa, lHR,    K_SHRINK['hr_per_pa'])
    pBABIP = regress(pBABIP_raw, pit_pa, lBABIP, K_SHRINK['babip'])

    pK_pred  = log5(bK, pK, lK)
    pBB_pred = log5(bBB, pBB, lBB)
    pHR_pred = log5(bHR, pHR, lHR)

    if not use_splits:
        plat = platoon_factors(b_bats, p_throws)
        if pK_pred  is not None: pK_pred  *= plat['k']
        if pBB_pred is not None: pBB_pred *= plat['bb']
        if pHR_pred is not None: pHR_pred *= plat['hr']

    park = park_factor(ctx.get('park_factors'), ctx.get('home_team'))
    if pHR_pred is not None: pHR_pred = clip01(pHR_pred * park['hr'])

    if pK_pred  is not None: pK_pred  = clip01(pK_pred)
    if pBB_pred is not None: pBB_pred = clip01(pBB_pred)

    pHBP = 0.009
    p_contact = clip01(1 - (pK_pred or 0) - (pBB_pred or 0) - (pHR_pred or 0) - pHBP)

    matchup_babip = log5(bBABIP, pBABIP, lBABIP) or lBABIP or 0.295
    matchup_babip = clip01(matchup_babip * (1 + (park['r'] - 1) * 0.3))

    p_hip = p_contact * matchup_babip
    p_oip = p_contact * (1 - matchup_babip)
    p1B = p_hip * 0.78
    p2B = p_hip * 0.185
    p3B = p_hip * 0.035

    xwOBA = ((pBB_pred or 0) * 0.690 + pHBP * 0.720
             + p1B * 0.890 + p2B * 1.271 + p3B * 1.616
             + (pHR_pred or 0) * 2.101)
    lg_wOBA = league['bat'].get('woba') or 0.318
    edge = max(-100, min(100, ((xwOBA - lg_wOBA) / 0.060) * 50))

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
