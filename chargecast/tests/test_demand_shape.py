"""Step 1: unified table + demand-shape model.

The demand-shape model is the v0.1 BaselineModel logic unchanged, so its
predictions must match v0.1 exactly. charged_price_ct must be a first-class
column of the unified table but must NOT influence the demand shape.
"""
import numpy as np
import pytest

from chargecast.core import (DemandShapeModel, BaselineModel, add_features,
                             SHAPE_FEATURES, INPUT_COLUMNS)

# v0.1 demand-shape output on the example seed dataset (regression anchor).
V01_KIND = 'profile'
V01_N_DAYS = 365
V01_PRED_SUM = 271214.55
V01_PRED_HEAD = [1.876415, 1.25, 1.064151, 0.719811, 0.332075, 0.666981,
                 4.759434, 19.606604, 29.342453, 30.435849, 30.639623,
                 24.659434, 35.673585, 32.64434, 37.995283, 42.445283,
                 62.168868, 79.259434, 61.038679, 57.023585, 40.496226,
                 23.151887, 7.864151, 4.55283]


def _fit(seed_df):
    feat = add_features(seed_df)
    feat['target_kwh'] = seed_df['target_kwh'].values
    return DemandShapeModel().fit(feat), feat


def test_predictions_match_v01(seed_df):
    m, feat = _fit(seed_df)
    pred = m.predict(feat)
    assert m.kind == V01_KIND
    assert int(m.n_days) == V01_N_DAYS
    assert float(pred.sum()) == pytest.approx(V01_PRED_SUM, rel=1e-6)
    assert np.allclose([float(x) for x in pred[:24]], V01_PRED_HEAD, atol=1e-6)


def test_charged_price_is_first_class_but_not_a_shape_feature(seed_df):
    feat = add_features(seed_df)
    assert 'charged_price_ct' in feat.columns          # carried through
    assert (feat['charged_price_ct'] == 59.0).all()    # untouched
    assert 'charged_price_ct' not in SHAPE_FEATURES     # not a shape input
    assert 'charged_price_ct' in INPUT_COLUMNS          # part of unified schema


def test_shape_is_price_neutral(seed_df):
    """Changing charged_price_ct must not change the demand-shape prediction."""
    m, feat = _fit(seed_df)
    base = m.predict(feat)
    hi = feat.copy(); hi['charged_price_ct'] = 120.0
    assert np.array_equal(base, m.predict(hi))


def test_baseline_alias_preserved():
    assert BaselineModel is DemandShapeModel
