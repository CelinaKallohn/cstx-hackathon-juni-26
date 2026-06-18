"""Step 2 (+ grouped-beta amendment): Bayesian price coefficients.

The conjugate engine lives in BlockBeta (one coefficient); PriceEffect groups one
BlockBeta per time block. Covers the % <-> beta conversion, the engine's day-one
prior dominance and synthetic recovery with a shrinking interval, plus the
grouped behaviour: per-block recovery, an un-varied block keeping its prior, and
config validation of the block partition.
"""
import numpy as np
import pandas as pd
import pytest

from chargecast.core import (BlockBeta, PriceEffect, pct_to_beta, beta_to_pct,
                             CONFIDENCE_S0, DEFAULT_PRICE_BLOCKS,
                             validate_price_blocks)

REF = 59.0


def _block(pct=50, confidence='medium', sigma2=0.25):
    return BlockBeta(pct_to_beta(pct), CONFIDENCE_S0[confidence], sigma2)


# ---------- conversion ----------

def test_pct_beta_roundtrip():
    for pct in [-20, 0, 10, 25, 50, 75, 90]:
        assert beta_to_pct(pct_to_beta(pct)) == pytest.approx(pct, abs=1e-9)


def test_known_conversions():
    assert pct_to_beta(50) == pytest.approx(-6.931, abs=1e-3)
    assert pct_to_beta(25) == pytest.approx(-2.877, abs=1e-3)


# ---------- conjugate engine (BlockBeta) ----------

def test_blockbeta_starts_at_prior():
    c = _block(pct=25, confidence='tight')
    assert c.m0 == pytest.approx(pct_to_beta(25))
    assert c.s0 == CONFIDENCE_S0['tight']
    assert c.mean == pytest.approx(c.m0)
    assert c.beta_sd == pytest.approx(c.s0)
    assert c.pct() == pytest.approx(25, abs=1e-6)


def test_blockbeta_no_signal_keeps_prior():
    c = _block(pct=50)
    c.update(np.zeros(200), np.random.default_rng(0).normal(0, 0.3, 200))  # all price_dev == 0
    assert c.n_points == 0
    assert c.mean == pytest.approx(c.m0)
    assert c.beta_sd == pytest.approx(c.s0)


def _synthetic_xy(true_pct, n, noise_sd=0.05, seed=0):
    rng = np.random.default_rng(seed)
    price_dev = rng.uniform(-0.30, 0.30, size=n)
    y = pct_to_beta(true_pct) * price_dev + rng.normal(0, noise_sd, size=n)
    return price_dev, y


@pytest.mark.parametrize('true_pct', [30, 50])
def test_blockbeta_recovers_truth(true_pct):
    c = _block(pct=10, confidence='loose')          # deliberately wrong, loose prior
    c.update(*_synthetic_xy(true_pct, 2000, seed=1))
    assert c.pct() == pytest.approx(true_pct, abs=4.0)
    lo, hi = c.pct_ci()
    assert lo <= true_pct <= hi


def test_blockbeta_ci_shrinks_with_data():
    small = _block(pct=10, confidence='loose'); small.update(*_synthetic_xy(40, 120, seed=2))
    large = _block(pct=10, confidence='loose'); large.update(*_synthetic_xy(40, 3000, seed=2))
    assert large.beta_sd < small.beta_sd
    assert large.beta_sd < 0.5 * small.beta_sd


def test_blockbeta_expected_multiplier_direction():
    c = _block(pct=50)
    assert c.expected_multiplier(0.10) < 1.0
    assert c.expected_multiplier(-0.10) > 1.0
    assert c.expected_multiplier(0.0) == pytest.approx(1.0)


def test_blockbeta_sample_beta_centers_on_posterior():
    c = _block(pct=50)
    draws = c.sample_beta(rng=np.random.default_rng(0), size=20000)
    assert np.mean(draws) == pytest.approx(c.mean, abs=0.05)
    assert np.std(draws) == pytest.approx(c.beta_sd, rel=0.05)


# ---------- block partition validation ----------

def test_validate_accepts_default_blocks():
    clean = validate_price_blocks(DEFAULT_PRICE_BLOCKS)
    covered = sorted(h for hours in clean.values() for h in hours)
    assert covered == list(range(24))


def test_validate_rejects_overlap():
    bad = {'a': list(range(0, 13)), 'b': list(range(12, 24))}   # hour 12 in both
    with pytest.raises(ValueError, match='both'):
        validate_price_blocks(bad)


def test_validate_rejects_gap():
    bad = {'a': list(range(0, 12)), 'b': list(range(13, 24))}   # hour 12 missing
    with pytest.raises(ValueError, match='not covered'):
        validate_price_blocks(bad)


def test_validate_rejects_out_of_range():
    with pytest.raises(ValueError, match='outside'):
        validate_price_blocks({'a': list(range(0, 24)) + [24]})


# ---------- grouped PriceEffect ----------

def test_all_blocks_seeded_from_shared_prior():
    pe = PriceEffect(prior_pct=40, prior_confidence='medium', ref_price=REF)
    assert set(pe.coeffs) == set(DEFAULT_PRICE_BLOCKS)
    for c in pe.coeffs.values():
        assert c.mean == pytest.approx(pct_to_beta(40))
        assert c.beta_sd == pytest.approx(CONFIDENCE_S0['medium'])


def test_priceeffect_day_one_constant_price_keeps_all_priors():
    n = 24 * 30
    hist = pd.DataFrame({
        'hourstamp': pd.date_range('2025-01-01', periods=n, freq='h'),
        'charged_price_ct': REF,
        'shape_pred': 20.0,
        'target_kwh': 18.0,
    })
    pe = PriceEffect(prior_pct=50, prior_confidence='medium', ref_price=REF)
    pe.fit_from_history(hist)
    assert pe.varied_days == 0
    for c in pe.coeffs.values():
        assert c.n_points == 0
        assert c.mean == pytest.approx(c.m0)
        assert c.beta_sd == pytest.approx(c.s0)


def _synthetic_grouped(true_pct_by_block, blocks, n_days, *, vary=None,
                       ref=REF, noise_sd=0.05, seed=0):
    """Per-row demand = shape * exp(true_beta_block * price_dev) * noise.

    Blocks not in `vary` are always priced at the reference (no price signal).
    """
    rng = np.random.default_rng(seed)
    hour_block = {h: name for name, hours in blocks.items() for h in hours}
    true_beta = {n: pct_to_beta(p) for n, p in true_pct_by_block.items()}
    vary = set(blocks) if vary is None else set(vary)
    rows = []
    start = pd.Timestamp('2025-01-01')
    for d in range(n_days):
        day = start + pd.Timedelta(days=d)
        for h in range(24):
            name = hour_block[h]
            pdv = rng.uniform(-0.30, 0.30) if name in vary else 0.0
            shape = rng.uniform(10, 60)
            target = shape * np.exp(true_beta[name] * pdv) * np.exp(rng.normal(0, noise_sd))
            rows.append({'hourstamp': day + pd.Timedelta(hours=h),
                         'charged_price_ct': ref * (1 + pdv),
                         'shape_pred': shape, 'target_kwh': target})
    return pd.DataFrame(rows)


def test_each_block_recovers_its_own_elasticity():
    true_pct = {'overnight': 40, 'morning_peak': 5, 'midday': 70,
                'afternoon': 40, 'evening_peak': 10, 'late': 40}
    hist = _synthetic_grouped(true_pct, DEFAULT_PRICE_BLOCKS, n_days=120, seed=1)
    pe = PriceEffect(prior_pct=30, prior_confidence='loose', ref_price=REF)  # wrong shared prior
    pe.fit_from_history(hist)
    for name, want in true_pct.items():
        got = pe.coeffs[name].pct()
        assert got == pytest.approx(want, abs=6.0), (name, got, want)
        assert pe.coeffs[name].varied_days == 120


def test_unvaried_block_keeps_prior_while_others_learn():
    true_pct = {n: 40 for n in DEFAULT_PRICE_BLOCKS}
    # vary every block EXCEPT morning_peak
    varied = [n for n in DEFAULT_PRICE_BLOCKS if n != 'morning_peak']
    hist = _synthetic_grouped(true_pct, DEFAULT_PRICE_BLOCKS, n_days=120,
                              vary=varied, seed=3)
    pe = PriceEffect(prior_pct=10, prior_confidence='loose', ref_price=REF)
    pe.fit_from_history(hist)

    mp = pe.coeffs['morning_peak']
    assert mp.n_points == 0
    assert mp.varied_days == 0
    assert mp.mean == pytest.approx(mp.m0)          # still the prior guess (10%)
    assert mp.beta_sd == pytest.approx(mp.s0)        # interval did NOT shrink
    # a varied block did learn and tightened
    learned = pe.coeffs['midday']
    assert learned.beta_sd < mp.beta_sd
    assert learned.pct() == pytest.approx(40, abs=6.0)
