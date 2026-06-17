"""Step 6: end-to-end CLI flow (seed-equivalent -> recommend -> ingest -> status).

`seed` needs xlsx inputs that aren't in the repo, so we seed history directly
(exactly what cmd_seed builds) and then drive the real CLI subcommands.
"""
import os
import numpy as np
import pandas as pd
import pytest

from chargecast.store import Store, train, ref_price
from chargecast.cli import main
from chargecast.core import DEFAULT_PRICE_BLOCKS


def _seed_state(tmp_path, seed_df):
    state = str(tmp_path / 'state')
    store = Store(state)
    df = seed_df.copy()
    df['charged_price_ct'] = ref_price(store.cfg)
    df['baseline_pred'] = np.nan
    store.append_history(df[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct', 'baseline_pred']])
    summary = train(store)
    return state, store, summary


def test_seed_summary_has_v2_keys(tmp_path, seed_df):
    _, _, summary = _seed_state(tmp_path, seed_df)
    assert summary['shape_kind'] in ('profile', 'gbm')
    # day one: no varied prices, so every block is still on the prior
    assert summary['varied_days'] == 0
    assert set(summary['blocks']) == set(DEFAULT_PRICE_BLOCKS)


def test_recommend_writes_plan_respecting_floor(tmp_path, seed_df, capsys):
    state, store, _ = _seed_state(tmp_path, seed_df)
    out = str(tmp_path / 'plan.csv')
    main(['recommend', '--state', state, '--date', '2026-06-18', '--out', out])
    assert os.path.exists(out)
    plan = pd.read_csv(out)
    assert len(plan) == 96
    assert set(['slot', 'price_ct', 'floor_ct', 'forecast_kwh', 'forecast_lower',
                'forecast_upper', 'margin_eur']).issubset(plan.columns)
    assert np.all(plan['price_ct'] >= plan['floor_ct'] - 1e-6)      # hard floor
    assert np.all(plan['price_ct'] <= store.cfg['price_cap_ct'] + 1e-6)
    assert plan['margin_eur'].sum() >= -1e-6                        # day margin >= 0
    # interval brackets the central forecast
    assert np.all(plan['forecast_lower'] <= plan['forecast_kwh'] + 1e-6)
    assert np.all(plan['forecast_kwh'] <= plan['forecast_upper'] + 1e-6)


def test_recommend_evaluate_user_prices_clamps_to_floor(tmp_path, seed_df):
    state, store, _ = _seed_state(tmp_path, seed_df)
    # deliberately below-floor prices; recommend must clamp them up
    pf = str(tmp_path / 'prices.csv')
    pd.DataFrame({'slot': range(96), 'price_ct': 1.0}).to_csv(pf, index=False)
    out = str(tmp_path / 'eval.csv')
    main(['recommend', '--state', state, '--date', '2026-06-18', '--prices', pf, '--out', out])
    plan = pd.read_csv(out)
    assert np.all(plan['price_ct'] >= plan['floor_ct'] - 1e-6)


def test_ingest_scores_and_retrains_with_varied_prices(tmp_path, seed_df, capsys):
    state, store, _ = _seed_state(tmp_path, seed_df)
    # a day of varied prices and actuals -> should register a varied-price day
    day = pd.Timestamp('2026-06-18')
    act = pd.DataFrame({
        'hourstamp': [day + pd.Timedelta(minutes=15 * i) for i in range(96)],
        'actual_kwh': np.linspace(5, 60, 96),
        'charged_price_ct': np.linspace(20, 90, 96),   # clearly varied
        'spot_ct': 2.0,
    })
    af = str(tmp_path / 'actuals.csv')
    act.to_csv(af, index=False)
    main(['ingest', '--state', state, '--actuals', af])
    captured = capsys.readouterr().out
    assert 'scored 2026-06-18' in captured
    m = store.load_model()
    assert m['price'].varied_days >= 1
    assert m['price'].n_points > 0          # varied prices now inform beta


def test_status_runs(tmp_path, seed_df, capsys):
    state, _, _ = _seed_state(tmp_path, seed_df)
    main(['status', '--state', state])
    out = capsys.readouterr().out
    assert 'by time block' in out
    assert 'midday' in out                      # a block is listed
    assert 'elasticity' in out
    assert ('explore' in out or 'exploit' in out)


def test_invalid_price_blocks_config_is_rejected(tmp_path, seed_df):
    import json
    state, _, _ = _seed_state(tmp_path, seed_df)
    # corrupt the config with overlapping blocks; reloading must fail fast
    cfgp = os.path.join(state, 'config.json')
    cfg = json.load(open(cfgp))
    cfg['price_blocks'] = {'a': list(range(0, 13)), 'b': list(range(12, 24))}
    json.dump(cfg, open(cfgp, 'w'))
    with pytest.raises(ValueError):
        Store(state)
