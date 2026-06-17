"""Demand-smoothing price recommender (v2 build step 5).

Chooses a 24-hour price vector that flattens demand subject to hard constraints:
  (a) every price >= cost floor (break-even)            -- never sell at a loss
  (b) total day margin >= 0                              -- only rule on margin
  (c) prices may rise above ref in peaks, fall below in troughs

With the break-even floor (floor = grid+concession+max(spot,0) >= true cost),
every at-or-above-floor hour already has non-negative margin, so (b) is implied
by (a). We still compute and surface the day margin.

Objective: coefficient of variation (std/mean) of the forecast demand curve.
It is SCALE-INVARIANT on purpose -- plain variance would be minimised by simply
crushing all demand toward zero (push every price to the cap), which is not
smoothing. CV responds only to the demand *shape*, so the optimiser flattens by
raising peak prices and lowering trough prices.

Search: coordinate descent with a per-hour line search over a bounded price grid
(no scipy dependency; objective and constraints are isolated functions so the
method can be swapped for e.g. scipy.optimize.minimize SLSQP).

EXPLORE vs EXPLOIT (per block): the recommender evaluates with a beta drawn from
each block's posterior (Thompson sampling), mapped onto that block's hours. A
block whose posterior is still wide produces variable day-to-day prices --
deliberate variation that teaches that block's elasticity -- while a block
already pinned down barely moves. As each block's posterior sharpens, its draws
cluster on the mean and its prices converge to the exploit optimum.
"""
from __future__ import annotations
import numpy as np

from .core import cost_floor_ct


def flatness_penalty(kwh) -> float:
    """Coefficient of variation (std/mean) of the demand curve. Lower = flatter."""
    kwh = np.asarray(kwh, float)
    mean = kwh.mean()
    if mean <= 1e-9:
        return 0.0
    return float(kwh.std() / mean)


def forecast_kwh_for_prices(shape_kwh, prices, ref_price, beta) -> np.ndarray:
    """Demand at a given price vector for a given beta: shape * exp(beta*price_dev)."""
    price_dev = (np.asarray(prices, float) - ref_price) / ref_price
    return np.asarray(shape_kwh, float) * np.exp(beta * price_dev)


def day_margin_eur(kwh, prices, spot_ct, grid_arbeitspreis_ct, concession_ct) -> float:
    """Total energy margin over the day in EUR (excludes capacity/fixed fees)."""
    kwh = np.asarray(kwh, float)
    prices = np.asarray(prices, float)
    revenue = kwh * prices / 100.0
    cost = kwh * (np.asarray(spot_ct, float) + grid_arbeitspreis_ct + concession_ct) / 100.0
    return float((revenue - cost).sum())


def recommend_prices(shape_kwh, spot_ct, *, ref_price, price_cap_ct,
                     grid_arbeitspreis_ct, concession_ct, beta,
                     n_sweeps=8, n_grid=41) -> np.ndarray:
    """Coordinate-descent search for the flattest demand curve within bounds.

    Each price is bounded to [floor_ct(hour), price_cap_ct]. Returns the price
    vector (ct/kWh) minimising the flatness penalty for the supplied beta.
    """
    shape_kwh = np.asarray(shape_kwh, float)
    n = len(shape_kwh)
    lo = cost_floor_ct(spot_ct, grid_arbeitspreis_ct, concession_ct)
    lo = np.broadcast_to(lo, (n,)).astype(float).copy()
    hi = np.maximum(np.full(n, float(price_cap_ct)), lo)   # cap never below floor

    def objective(p):
        return flatness_penalty(forecast_kwh_for_prices(shape_kwh, p, ref_price, beta))

    # start at the reference price, clamped into bounds
    best = np.clip(np.full(n, float(ref_price)), lo, hi)
    best_obj = objective(best)

    for _ in range(n_sweeps):
        improved = False
        for h in range(n):
            grid = np.linspace(lo[h], hi[h], n_grid)
            trial = best.copy()
            chosen, chosen_obj = best[h], best_obj
            for cand in grid:
                trial[h] = cand
                o = objective(trial)
                if o < chosen_obj - 1e-12:
                    chosen, chosen_obj = cand, o
            if chosen != best[h]:
                best[h], best_obj = chosen, chosen_obj
                improved = True
        if not improved:
            break
    return best


def explore_fraction(coeff) -> float:
    """Remaining prior uncertainty for ONE block coefficient, in [0,1].

    1 = posterior still as wide as the prior (full explore); -> 0 as it sharpens.
    """
    if coeff.s0 <= 0:
        return 0.0
    return float(min(coeff.beta_sd / coeff.s0, 1.0))


def explore_fractions(price_effect) -> dict:
    """Per-block explore fraction (block_name -> [0,1])."""
    return {name: explore_fraction(c) for name, c in price_effect.coeffs.items()}


def recommend_day(forecaster, frame, spot_ct, cfg, *, explore=True, rng=None) -> dict:
    """Recommend a 24h price vector and report the forecast + economics.

    Exploration is PER BLOCK: explore=True draws one beta per block from its
    posterior (Thompson sampling) and maps it onto that block's hours, so a block
    whose posterior is still wide gets more price exploration than one already
    pinned down. explore=False uses the posterior-mean betas (pure exploit). The
    reported forecast interval always uses the full posterior, not the draw.
    """
    pe = forecaster.price
    hours = np.asarray(frame['hour'].values)
    shape_kwh = np.asarray(forecaster.shape.predict(frame), float)
    if explore:
        rng = rng if rng is not None else np.random.default_rng()
        beta_used = pe.sample_beta_for_hours(hours, rng=rng)
    else:
        beta_used = pe.beta_for_hours(hours)

    prices = recommend_prices(
        shape_kwh, spot_ct,
        ref_price=pe.ref_price, price_cap_ct=cfg['price_cap_ct'],
        grid_arbeitspreis_ct=cfg['grid_arbeitspreis_ct_per_kwh'],
        concession_ct=cfg['concession_ct_per_kwh'], beta=beta_used)

    fc = forecaster.forecast(frame, prices)
    floors = cost_floor_ct(spot_ct, cfg['grid_arbeitspreis_ct_per_kwh'],
                           cfg['concession_ct_per_kwh'])
    margin = day_margin_eur(fc['kwh'], prices, spot_ct,
                            cfg['grid_arbeitspreis_ct_per_kwh'],
                            cfg['concession_ct_per_kwh'])
    return {
        'prices': prices,
        'floor': np.broadcast_to(floors, (len(shape_kwh),)).astype(float),
        'beta_used': beta_used,
        'explore': explore,
        'explore_fraction': explore_fractions(pe),
        'forecast': fc,
        'day_margin_eur': margin,
    }
