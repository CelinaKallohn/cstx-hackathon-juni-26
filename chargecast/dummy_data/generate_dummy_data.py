#!/usr/bin/env python
"""Generate dummy daily actuals for the first days under DYNAMIC pricing.

These days demonstrate what happens once the dynamic-pricing model is switched on
in the network: each day is no longer billed at the flat reference price but at
the model's *recommended* per-slot prices (high in peaks, low in troughs), and
customers shift their charging in response.

How each day is built:
  1. Seed a model on the historic data and ask it to RECOMMEND that day's
     96-slot price vector, clamped to a realistic dynamic-pricing band
     (reference +/- 35%) -- the prices actually deployed.
  2. Simulate the customer response with a *true* per-block elasticity that the
     operator does not yet know:
        weight(slot)     = exp(true_beta_block * price_dev) * slot_noise
        actual_kwh(slot) = shape[dow,slot] * weight(slot), then renormalised so
                           the day's TOTAL energy is conserved (load shifting:
                           customers move *when* they charge, not how much).
     price_dev = (price - ref) / ref. Flexible blocks (overnight, midday) shift a
     lot; rigid commuter peaks barely move -- so demand flattens toward the
     cheaper hours while daily volume stays in the historic range. Ingesting
     these varied-price days is what lets the model learn the true elasticity.

Spot prices are drawn from the historic per-slot spot mean plus noise. Output:
one file per date, dummy_data/ev_charging_<date>.csv with columns
hourstamp, actual_kwh, charged_price_ct, spot_ct (96 rows, 15-minute).
"""
from __future__ import annotations
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from chargecast.cli import _read_collected                       # noqa: E402
from chargecast.core import (SLOTS_PER_DAY, INTERVAL_MIN, add_features,      # noqa: E402
                             UnifiedForecaster, pct_to_beta)
from chargecast.store import Store, train, ref_price             # noqa: E402
from chargecast.recommend import recommend_day                   # noqa: E402

DATA = os.path.join(HERE, '..', '..', 'KI-Hackathon Juni2026',
                    'collected_and_cleaned', 'collected_cleaned_data.csv')

DATES = ['2026-06-08', '2026-06-09', '2026-06-10', '2026-06-11',
         '2026-06-12', '2026-06-13', '2026-06-14']

# The customers' TRUE price sensitivity per time block (% volume shift per +10%
# price) -- unknown to the model, which starts from a flat 50% prior. Flexible
# off-peak vs rigid commuter peaks: this is what the model will discover. Values
# are realistic short-run charging elasticities (well below the 50% prior).
TRUE_PCT_BY_BLOCK = {
    'overnight':    35,    # flexible -- can delay/advance overnight charging
    'morning_peak': 5,     # rigid -- commuters must charge
    'midday':       40,    # flexible
    'afternoon':    20,
    'evening_peak': 8,     # rigid -- evening commuter peak
    'late':         25,
}

PRICE_BAND = 0.35          # deployed dynamic prices stay within ref +/- 35%
DAY_SCALE_SD = 0.10        # day-to-day total volume variation (~10%)
SLOT_NOISE_SD = 0.12       # within-day per-slot jitter


def _seed_model():
    """Seed a model on historic data; return (forecaster, cfg, spot_by_slot)."""
    tmp = tempfile.mkdtemp(prefix='chargecast_dummy_')
    try:
        store = Store(tmp)
        hist = _read_collected(DATA)
        hist['spot_ct'] = hist['spot_ct'].fillna(hist['spot_ct'].median())
        hist['charged_price_ct'] = hist['charged_price_ct'].fillna(ref_price(store.cfg))
        hist['baseline_pred'] = np.nan
        store.append_history(hist[['hourstamp', 'spot_ct', 'target_kwh',
                                   'charged_price_ct', 'baseline_pred']])
        train(store)
        m = store.load_model()
        fc = UnifiedForecaster(m['shape'], m['price'])
        cfg = dict(store.cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ts = pd.to_datetime(hist['hourstamp'])
    slot = (ts.dt.hour * 60 + ts.dt.minute) // INTERVAL_MIN
    spot_by_slot = hist.assign(slot=slot.values).groupby('slot')['spot_ct'].mean()
    return fc, cfg, spot_by_slot


def _true_beta_by_slot(price_effect):
    """Map each slot's hour-block to its TRUE beta."""
    beta = {name: pct_to_beta(pct) for name, pct in TRUE_PCT_BY_BLOCK.items()}
    return np.array([beta[price_effect.block_of(h % 24)]
                     for h in (np.arange(SLOTS_PER_DAY) * INTERVAL_MIN // 60)], float)


def generate():
    fc, cfg, spot_by_slot = _seed_model()
    ref = ref_price(cfg)
    true_beta = _true_beta_by_slot(fc.price)

    for date in DATES:
        rng = np.random.default_rng(abs(hash(date)) % (2**32))
        day = pd.Timestamp(date)

        # spot for the day: historic per-slot mean + mild noise
        spot = np.array([float(spot_by_slot.get(s, 9.0)) for s in range(SLOTS_PER_DAY)])
        frame = add_features(pd.DataFrame({
            'hourstamp': [day + pd.Timedelta(minutes=INTERVAL_MIN * s) for s in range(SLOTS_PER_DAY)],
            'spot_ct': spot,
        }))

        # 1. the dynamic prices the model recommends, clamped to a realistic band
        #    (still never below the cost floor) and deployed to the network
        rec = recommend_day(fc, frame, spot, cfg, explore=False)
        prices = np.clip(rec['prices'], ref * (1 - PRICE_BAND), ref * (1 + PRICE_BAND))
        prices = np.maximum(prices, rec['floor'])
        price_dev = (prices - ref) / ref

        # 2. the customers' TRUE response: shift charging toward cheaper slots,
        #    conserving the day's total energy (load shifting, not extra volume)
        shape = np.asarray(fc.shape.predict(frame), float)
        day_scale = float(np.exp(rng.normal(0, DAY_SCALE_SD)))
        slot_noise = np.exp(rng.normal(0, SLOT_NOISE_SD, SLOTS_PER_DAY))
        weight = np.exp(true_beta * price_dev) * slot_noise
        raw = shape * weight
        target_total = shape.sum() * day_scale                 # historic-level daily volume
        actual = raw * (target_total / raw.sum())

        out = pd.DataFrame({
            'hourstamp': frame['hourstamp'],
            'actual_kwh': np.round(np.clip(actual, 0, None), 3),
            'charged_price_ct': np.round(prices, 2),
            'spot_ct': np.round(spot, 3),
        })
        path = os.path.join(HERE, f'ev_charging_{date}.csv')
        out.to_csv(path, index=False)

        # report the smoothing effect (CV of demand: lower = flatter)
        cv_flat = float(shape.std() / shape.mean())
        cv_dyn = float(actual.std() / actual.mean())
        print(f'{date}: {out.actual_kwh.sum():>4.0f} kWh/day | '
              f'price {prices.min():.0f}-{prices.max():.0f} ct | '
              f'demand CV {cv_flat:.2f} -> {cv_dyn:.2f} (flatter) -> {os.path.basename(path)}')


if __name__ == '__main__':
    generate()
