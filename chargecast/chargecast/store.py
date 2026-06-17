"""Persistence + economics for ChargeCast.

State lives in a directory:
  state/history.csv      - all hourly outcomes accumulated so far
  state/model.pkl        - trained DemandShapeModel + Bayesian PriceEffect
  state/accuracy_log.csv - one row per day the model was scored
  state/config.json      - reference price, price-effect prior, tariff
"""
from __future__ import annotations
import os, json, pickle
import numpy as np
import pandas as pd
from .core import (DemandShapeModel, PriceEffect, add_features,
                   DEFAULT_PRICE_BLOCKS, validate_price_blocks)

DEFAULT_CONFIG = {
    "reference_price_ct": 59.0,
    "ref_price_ct": 59.0,                 # alias used by the v2 price-effect model
    "assumed_elasticity_pct": 50.0,       # (legacy v0.1 ElasticityLayer)
    # v2 Bayesian price-effect prior (shared across all time blocks at the start)
    "prior_elasticity_pct": 50.0,         # the user's guess: % volume shift per +10% price
    "prior_confidence": "medium",         # 'loose' | 'medium' | 'tight' -> prior sd on beta
    "price_cap_ct": 150.0,                # sane upper bound for recommended prices
    # time-of-day blocks for grouped price betas (must partition hours 0-23)
    "price_blocks": DEFAULT_PRICE_BLOCKS,
    # BS Netz tariff (Umspannung 20/0,4 kV, < 2500 h/a) used in the supplied calc
    "grid_arbeitspreis_ct_per_kwh": 8.24,
    "grid_leistungspreis_eur_per_kw_a": 19.76,
    "concession_ct_per_kwh": 0.11,
}


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.cfg = self._load_cfg()

    # ---- config ----
    def _cfg_path(self): return os.path.join(self.path, 'config.json')
    def _load_cfg(self):
        p = self._cfg_path()
        if os.path.exists(p):
            with open(p) as f:
                cfg = {**DEFAULT_CONFIG, **json.load(f)}
        else:
            cfg = dict(DEFAULT_CONFIG)
            with open(p, 'w') as f: json.dump(DEFAULT_CONFIG, f, indent=2)
        # fail fast on a malformed price_blocks (overlap / gap / out-of-range)
        cfg['price_blocks'] = validate_price_blocks(cfg['price_blocks'])
        return cfg
    def save_cfg(self):
        with open(self._cfg_path(), 'w') as f: json.dump(self.cfg, f, indent=2)

    # ---- history ----
    def _hist_path(self): return os.path.join(self.path, 'history.csv')
    def load_history(self) -> pd.DataFrame:
        p = self._hist_path()
        if not os.path.exists(p):
            return pd.DataFrame(columns=['hourstamp','spot_ct','target_kwh','charged_price_ct','baseline_pred'])
        df = pd.read_csv(p, parse_dates=['hourstamp'])
        return df
    def append_history(self, df: pd.DataFrame):
        cur = self.load_history()
        # avoid concatenating an empty frame (pandas FutureWarning on dtype inference)
        out = df.copy() if len(cur) == 0 else pd.concat([cur, df], ignore_index=True)
        out = out.drop_duplicates(subset=['hourstamp'], keep='last').sort_values('hourstamp')
        out.to_csv(self._hist_path(), index=False)
        return out

    # ---- model ----
    def _model_path(self): return os.path.join(self.path, 'model.pkl')
    def load_model(self):
        p = self._model_path()
        if os.path.exists(p):
            with open(p, 'rb') as f: return pickle.load(f)
        return None
    def save_model(self, obj):
        with open(self._model_path(), 'wb') as f: pickle.dump(obj, f)

    # ---- accuracy log ----
    def _acc_path(self): return os.path.join(self.path, 'accuracy_log.csv')
    def log_accuracy(self, row: dict):
        p = self._acc_path()
        df = pd.DataFrame([row])
        if os.path.exists(p):
            df = pd.concat([pd.read_csv(p), df], ignore_index=True)
        df.to_csv(p, index=False)
    def load_accuracy(self) -> pd.DataFrame:
        p = self._acc_path()
        return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()


def margin_per_hour(demand_kwh, charged_price_ct, spot_ct, cfg):
    """Energy margin per hour in EUR (excludes capacity/Leistungspreis, which is monthly)."""
    revenue = demand_kwh * charged_price_ct / 100.0
    energy_cost = demand_kwh * (spot_ct + cfg['grid_arbeitspreis_ct_per_kwh'] + cfg['concession_ct_per_kwh']) / 100.0
    return revenue - energy_cost


def ref_price(cfg) -> float:
    """The reference price (v2 key, falling back to the v0.1 name)."""
    return float(cfg.get('ref_price_ct', cfg.get('reference_price_ct', 59.0)))


def train(store: Store) -> dict:
    """Retrain from scratch on all history. Returns a summary dict.

    Fits the demand SHAPE, then recomputes the Bayesian PRICE EFFECT posterior
    from the original prior + all varied-price history (retrain-from-scratch).
    """
    hist = store.load_history()
    if len(hist) == 0:
        raise RuntimeError("No history to train on. Seed with historic data first.")
    feat = add_features(hist)
    feat['target_kwh'] = hist['target_kwh'].values
    # spot must be present for GBM features
    feat['spot_ct'] = hist['spot_ct'].fillna(hist['spot_ct'].median()).values

    shape = DemandShapeModel().fit(feat)
    # price-neutral shape prediction over history (informs the price effect)
    hist = hist.copy()
    hist['baseline_pred'] = shape.predict(feat)

    price = PriceEffect(prior_pct=store.cfg['prior_elasticity_pct'],
                        prior_confidence=store.cfg['prior_confidence'],
                        ref_price=ref_price(store.cfg),
                        blocks=store.cfg['price_blocks'])
    price.fit_from_history(hist.rename(columns={'baseline_pred': 'shape_pred'}))

    store.save_model({'shape': shape, 'price': price})
    # persist shape prediction back into history so the posterior compounds
    store.append_history(hist[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct', 'baseline_pred']])

    return {
        'shape_kind': shape.kind,
        'n_days': shape.n_days,
        'varied_days': price.varied_days,
        'blocks': {name: f"{b['pct']}% (CI {b['pct_ci'][0]}..{b['pct_ci'][1]}), "
                         f"varied days {b['varied_days']}"
                   for name, b in price.block_summary().items()},
    }
