"""
Python port of js/matchup-model.js — kept structurally identical so that any
metric improvements measured here translate 1:1 to the production model.

`matchup(pitcher_row, batter_row, league)` returns a dict with the same shape
as the JS version: { 'p': {...probabilities}, 'xwOBA': float, 'edge': float }.
"""
import math
from typing import Optional


def pct(v) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
    except Exception:
        return None
    if not math.isfinite(f): return None
    return f / 100 if f > 1 else f


def log5(pBat, pPit, pLg) -> Optional[float]:
    if pBat is None or pPit is None or pLg is None: return None
    if not (0 < pLg < 1): return None
    eps = 1e-6
    b = min(max(pBat, eps), 1 - eps)
    p = min(max(pPit, eps), 1 - eps)
    l = min(max(pLg,  eps), 1 - eps)
    num = (b * p) / l
    den = num + ((1 - b) * (1 - p)) / (1 - l)
    return num / den


def platoon_factors(bats, throws):
    if not bats or not throws: return {'k': 1, 'bb': 1, 'hr': 1}
    if bats == 'S': return {'k': 1, 'bb': 1, 'hr': 1}
    same = (bats == throws)
    return ({'k': 1.04, 'bb': 0.97, 'hr': 0.94} if same
            else {'k': 0.97, 'bb': 1.03, 'hr': 1.06})


def stuff_factors(stuff_plus, location_plus):
    stf = 100 if stuff_plus is None else stuff_plus
    loc = 100 if location_plus is None else location_plus
    return {
        'k': 1.0,
        'bb': 1 - 0.02 * ((loc - 100) / 10),
        'hr': 1 - 0.015 * ((stf - 100) / 10),
    }


def matchup(pit, bat, league):
    if not pit or not bat or not league: return None

    bK  = pct(bat.get('k_pct'));  pK  = pct(pit.get('k_pct'));  lK  = pct(league['bat']['k_pct'])
    bBB = pct(bat.get('bb_pct')); pBB = pct(pit.get('bb_pct')); lBB = pct(league['bat']['bb_pct'])

    bHR = bat.get('hr_per_pa')
    if bHR is None:
        if bat.get('hr') is not None and bat.get('pa'):
            bHR = bat['hr'] / bat['pa']
        elif bat.get('iso') is not None:
            bHR = pct(bat['iso']) * 0.25
    pHR = pit.get('hr_per_pa')
    if pHR is None and pit.get('hr_per_9') is not None:
        pHR = pit['hr_per_9'] / 38
    lHR = league['bat'].get('hr_per_pa')

    platoon = platoon_factors(bat.get('bats'), pit.get('throws'))
    stuff = stuff_factors(pit.get('stuff_plus'), pit.get('location_plus'))

    pK_pred = log5(bK, pK, lK)
    pBB_pred = log5(bBB, pBB, lBB)
    pHR_pred = log5(bHR, pHR, lHR)

    pK_pred  = clip01(pK_pred  * platoon['k']  * stuff['k'])  if pK_pred  is not None else None
    pBB_pred = clip01(pBB_pred * platoon['bb'] * stuff['bb']) if pBB_pred is not None else None
    pHR_pred = clip01(pHR_pred * platoon['hr'] * stuff['hr']) if pHR_pred is not None else None

    pHBP = 0.009

    p_contact = clip01(1 - (pK_pred or 0) - (pBB_pred or 0) - (pHR_pred or 0) - pHBP)

    bBABIP = pct(bat.get('babip'))
    pBABIP = pct(pit.get('babip'))
    lBABIP = pct(league['bat'].get('babip'))
    babip = log5(bBABIP, pBABIP, lBABIP) or lBABIP or 0.295

    p_hit_in_play = p_contact * babip
    p_out_in_play = p_contact * (1 - babip)
    p1B = p_hit_in_play * 0.75
    p2B = p_hit_in_play * 0.21
    p3B = p_hit_in_play * 0.04

    xwOBA = ((pBB_pred or 0) * 0.690
             + pHBP * 0.720
             + p1B * 0.890
             + p2B * 1.271
             + p3B * 1.616
             + (pHR_pred or 0) * 2.101)

    lg_wOBA = league['bat'].get('woba') or 0.318
    edge = max(-100, min(100, ((xwOBA - lg_wOBA) / 0.060) * 50))

    return {
        'p': {
            'K': pK_pred, 'BB': pBB_pred, 'HR': pHR_pred,
            'HBP': pHBP, 'single': p1B, 'double': p2B, 'triple': p3B,
            'out_in_play': p_out_in_play,
        },
        'xwOBA': xwOBA,
        'edge': edge,
    }


def clip01(x): return max(0, min(1, x))
