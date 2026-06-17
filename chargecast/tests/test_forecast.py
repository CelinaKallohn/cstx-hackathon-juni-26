"""Step 3 (+ grouped-beta amendment): forecast with a per-block credible interval."""
import numpy as np
import pandas as pd
import pytest

from chargecast.core import (DemandShapeModel, PriceEffect, UnifiedForecaster,
                             add_features, pct_to_beta)

REF = 59.0


def _fitted_shape(seed_df):
    feat = add_features(seed_df)
    feat['target_kwh'] = seed_df['target_kwh'].values
    return DemandShapeModel().fit(feat)


def _day_frame(date='2026-06-18', spot=8.0):
    return add_features(pd.DataFrame({
        'hourstamp': [pd.Timestamp(date) + pd.Timedelta(hours=h) for h in range(24)],
        'spot_ct': spot,
    }))


def test_central_forecast_is_shape_times_block_multiplier(seed_df):
    shape = _fitted_shape(seed_df)
    pe = PriceEffect(prior_pct=50, prior_confidence='medium', ref_price=REF)
    fc = UnifiedForecaster(shape, pe)
    frame = _day_frame()
    prices = np.full(24, 70.0)
    out = fc.forecast(frame, prices)
    beta_h = pe.beta_for_hours(frame['hour'].values)
    expected = out['shape_kwh'] * np.exp(beta_h * out['price_dev'])
    assert np.allclose(out['kwh'], expected)


def test_per_hour_beta_follows_blocks(seed_df):
    """Different sensitivity in two blocks must change only those hours' demand."""
    shape = _fitted_shape(seed_df)
    pe = PriceEffect(prior_pct=50, prior_confidence='medium', ref_price=REF)
    # make midday very flexible, morning_peak rigid
    pe.coeffs['midday'].mean = pct_to_beta(80)
    pe.coeffs['morning_peak'].mean = pct_to_beta(2)
    fc = UnifiedForecaster(shape, pe)
    frame = _day_frame()
    prices = np.full(24, 75.0)             # same above-ref price everywhere
    out = fc.forecast(frame, prices)
    midday_mult = out['multiplier'][[10, 11, 12, 13, 14]]
    peak_mult = out['multiplier'][[7, 8, 9]]
    # flexible block sheds far more demand at the same price
    assert midday_mult.max() < peak_mult.min()


def test_interval_brackets_central(seed_df):
    shape = _fitted_shape(seed_df)
    fc = UnifiedForecaster(shape, PriceEffect(50, 'loose', REF))
    out = fc.forecast(_day_frame(), np.linspace(30, 100, 24))
    assert np.all(out['kwh_lower'] <= out['kwh'] + 1e-9)
    assert np.all(out['kwh'] <= out['kwh_upper'] + 1e-9)


def test_interval_collapses_at_reference_price(seed_df):
    shape = _fitted_shape(seed_df)
    fc = UnifiedForecaster(shape, PriceEffect(50, 'loose', REF))
    out = fc.forecast(_day_frame(), np.full(24, REF))
    assert np.allclose(out['kwh'], out['shape_kwh'])
    assert np.allclose(out['kwh_lower'], out['shape_kwh'])
    assert np.allclose(out['kwh_upper'], out['shape_kwh'])


def test_interval_narrows_as_posterior_sharpens(seed_df):
    shape = _fitted_shape(seed_df)
    frame = _day_frame()
    prices = np.full(24, 80.0)

    wide = UnifiedForecaster(shape, PriceEffect(50, 'loose', REF)).forecast(frame, prices)
    narrow_pe = PriceEffect(50, 'loose', REF)
    for c in narrow_pe.coeffs.values():
        c.var = 0.2 ** 2                       # simulate sharpened posteriors
    narrow = UnifiedForecaster(shape, narrow_pe).forecast(frame, prices)

    wide_w = (wide['kwh_upper'] - wide['kwh_lower']).sum()
    narrow_w = (narrow['kwh_upper'] - narrow['kwh_lower']).sum()
    assert narrow_w < wide_w
