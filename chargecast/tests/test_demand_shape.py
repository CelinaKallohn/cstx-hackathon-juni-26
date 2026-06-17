"""Unified table + demand-shape model (15-minute resolution).

The demand-shape model groups demand by (dayofweek, slot) at 15-minute
resolution. charged_price_ct must be a first-class column of the unified table
but must NOT influence the demand shape.
"""
import numpy as np
import pytest

from chargecast.core import (DemandShapeModel, BaselineModel, add_features,
                             SHAPE_FEATURES, INPUT_COLUMNS)

# demand-shape output on the 15-minute example fixture (90 days) -- regression anchor.
EXP_KIND = 'profile'
EXP_N_DAYS = 90
EXP_PRED_SUM = 62731.5
EXP_PRED_HEAD = [0.673077, 0.415385, 0.115385, 0.069231,
                 0.084615, 0.084615, 0.169231, 2.238462]


def _fit(seed_df):
    feat = add_features(seed_df)
    feat['target_kwh'] = seed_df['target_kwh'].values
    return DemandShapeModel().fit(feat), feat


def test_predictions_match_anchor(seed_df):
    m, feat = _fit(seed_df)
    pred = m.predict(feat)
    assert m.kind == EXP_KIND
    assert int(m.n_days) == EXP_N_DAYS
    assert float(pred.sum()) == pytest.approx(EXP_PRED_SUM, rel=1e-6)
    assert np.allclose([float(x) for x in pred[:8]], EXP_PRED_HEAD, atol=1e-6)


def test_profile_groups_by_slot(seed_df):
    """The profile must resolve at 15-minute slots, not hours (96 distinct slots)."""
    m, feat = _fit(seed_df)
    slots = sorted({s for (_dow, s) in m.profile.keys()})
    assert max(slots) == 95 and len(slots) == 96


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
