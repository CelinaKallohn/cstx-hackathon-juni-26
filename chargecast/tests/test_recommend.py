"""Step 5 (+ grouped-beta amendment): demand-smoothing recommender."""
import numpy as np
import pandas as pd
import pytest

from chargecast.core import (DemandShapeModel, PriceEffect, UnifiedForecaster,
                             add_features, cost_floor_ct, pct_to_beta)
from chargecast.recommend import (flatness_penalty, recommend_prices,
                                  recommend_day, day_margin_eur,
                                  forecast_kwh_for_prices, explore_fraction)

REF = 59.0
GRID = 8.24
CONC = 0.11
CAP = 150.0
CFG = {'price_cap_ct': CAP, 'grid_arbeitspreis_ct_per_kwh': GRID,
       'concession_ct_per_kwh': CONC}


def _fitted_shape(seed_df):
    feat = add_features(seed_df)
    feat['target_kwh'] = seed_df['target_kwh'].values
    return DemandShapeModel().fit(feat)


def _day_frame(spot=2.0, date='2026-06-18'):
    return add_features(pd.DataFrame({
        'hourstamp': [pd.Timestamp(date) + pd.Timedelta(hours=h) for h in range(24)],
        'spot_ct': spot,
    }))


def test_flatness_penalty_is_scale_invariant():
    k = np.array([10.0, 20, 30, 40, 50, 5])
    assert flatness_penalty(k) == pytest.approx(flatness_penalty(2 * k))
    assert flatness_penalty(np.full(24, 7.0)) == pytest.approx(0.0)


def test_recommended_prices_respect_floor_and_cap(seed_df):
    shape = _fitted_shape(seed_df)
    spot = np.array([(-20.0 if h < 6 else 30.0) for h in range(24)])  # incl. negative spot
    floors = cost_floor_ct(spot, GRID, CONC)
    prices = recommend_prices(shape.predict(_day_frame()).copy(), spot,
                              ref_price=REF, price_cap_ct=CAP,
                              grid_arbeitspreis_ct=GRID, concession_ct=CONC,
                              beta=pct_to_beta(50))
    assert np.all(prices >= floors - 1e-9)
    assert np.all(prices <= CAP + 1e-9)


def test_recommendation_flattens_demand_vs_flat_reference(seed_df):
    shape = _fitted_shape(seed_df)
    fc = UnifiedForecaster(shape, PriceEffect(50, 'medium', REF))
    frame = _day_frame()
    base_cv = flatness_penalty(shape.predict(frame))
    rec = recommend_day(fc, frame, np.full(24, 2.0), CFG, explore=False)
    rec_cv = flatness_penalty(rec['forecast']['kwh'])
    assert rec_cv < base_cv
    assert rec_cv < 0.6 * base_cv


def test_total_day_margin_non_negative(seed_df):
    shape = _fitted_shape(seed_df)
    fc = UnifiedForecaster(shape, PriceEffect(50, 'medium', REF))
    frame = _day_frame()
    spot = np.array([(-25.0 if 0 <= h < 5 else 20.0) for h in range(24)])
    rec = recommend_day(fc, frame, spot, CFG, explore=False)
    assert rec['day_margin_eur'] >= -1e-9
    beta_h = fc.price.beta_for_hours(frame['hour'].values)
    per_hour = forecast_kwh_for_prices(shape.predict(frame), rec['prices'], REF, beta_h)
    hourly_margin = per_hour * (rec['prices'] - (spot + GRID + CONC)) / 100.0
    assert np.all(hourly_margin >= -1e-9)


def test_exploit_is_deterministic(seed_df):
    shape = _fitted_shape(seed_df)
    fc = UnifiedForecaster(shape, PriceEffect(50, 'medium', REF))
    frame = _day_frame()
    a = recommend_day(fc, frame, np.full(24, 2.0), CFG, explore=False)
    b = recommend_day(fc, frame, np.full(24, 2.0), CFG, explore=False)
    assert np.array_equal(a['prices'], b['prices'])
    assert np.array_equal(a['beta_used'], fc.price.beta_for_hours(frame['hour'].values))


def test_exploration_is_per_block(seed_df):
    """A wide-posterior block explores (variable prices); a tight block barely moves."""
    shape = _fitted_shape(seed_df)
    frame = _day_frame()
    spot = np.full(24, 2.0)
    pe = PriceEffect(50, 'loose', REF)
    for c in pe.coeffs.values():           # pin every block tight...
        c.var = 0.02 ** 2
    pe.coeffs['midday'].var = pe.coeffs['midday'].s0 ** 2   # ...except midday (wide)
    fc = UnifiedForecaster(shape, pe)

    runs = np.array([recommend_day(fc, frame, spot, CFG, explore=True,
                                   rng=np.random.default_rng(s))['prices'] for s in range(10)])
    spread = np.std(runs, axis=0)
    midday_hours = pe.blocks['midday']
    tight_hours = pe.blocks['overnight']
    # the wide block explores far more; tight-block residual is just grid-step jitter
    assert spread[midday_hours].mean() > 3 * spread[tight_hours].mean()


def test_explore_fraction_decreases_with_data():
    pe = PriceEffect(50, 'loose', REF)
    c = pe.coeffs['midday']
    assert explore_fraction(c) == pytest.approx(1.0)     # prior only
    c.var = (0.5 * c.s0) ** 2
    assert explore_fraction(c) == pytest.approx(0.5)
