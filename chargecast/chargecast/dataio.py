"""Reading the input data and deriving per-slot spot prices.

These helpers used to live (private) inside ``cli.py`` but are also needed by the
simulated-data generator, so they live here as a small, explicit, reusable data
layer:

  - read_collected_csv  : parse the combined 15-minute dataset (German CSV).
  - mean_spot_by_slot   : mean spot price per 15-min slot-of-day from history.
  - day_spot_vector     : the 96-slot spot vector to forecast a given day with.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from .demand_forecast_model import SLOTS_PER_DAY, INTERVAL_MIN


def read_collected_csv(path):
    """Read the combined 15-minute dataset (collected_cleaned_data.csv).

    Semicolon-separated, German decimal comma, UTF-8 BOM. Columns:
      Datum; Uhrzeit; Profilwert kWh; Profilwert kW; Spotmarktpreis in ct/kWh;
      Endkundenpreis in ct/kwh; Arbeitspreis Umspannung ct/kwh;
      Steuern&Abgaben in ct/kwh; Gewinn
    The 'Gewinn' and 'Profilwert kW' columns are ignored. Returns a tidy frame
    with hourstamp, spot_ct, target_kwh, charged_price_ct at 15-minute steps.
    """
    df = pd.read_csv(path, sep=';', decimal=',', encoding='utf-8-sig')
    # tolerate the multi-line header ("Profilwert\nkWh"); match by substring
    def col(*needles):
        for c in df.columns:
            cs = str(c).replace('\n', ' ').lower()
            if all(n.lower() in cs for n in needles):
                return c
        raise ValueError(f"column matching {needles} not found in {list(df.columns)}")
    c_date, c_time = col('datum'), col('uhrzeit')
    c_kwh = col('profilwert', 'kwh')
    c_spot = col('spotmarktpreis')
    c_price = col('endkundenpreis')
    d = df.dropna(subset=[c_date, c_time]).copy()
    d['hourstamp'] = pd.to_datetime(
        d[c_date].astype(str).str.strip() + ' ' + d[c_time].astype(str).str.strip(),
        dayfirst=True, errors='coerce')
    d = d.dropna(subset=['hourstamp'])
    d['target_kwh'] = pd.to_numeric(d[c_kwh], errors='coerce')
    d['spot_ct'] = pd.to_numeric(d[c_spot], errors='coerce')
    d['charged_price_ct'] = pd.to_numeric(d[c_price], errors='coerce')
    d = d.dropna(subset=['target_kwh'])
    out = d[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct']]
    return out.groupby('hourstamp', as_index=False).agg(
        {'spot_ct': 'mean', 'target_kwh': 'sum', 'charged_price_ct': 'mean'})


def mean_spot_by_slot(store):
    """Mean spot price per 15-min slot-of-day (0-95) from history (fallback 8.0 ct)."""
    hist = store.load_history()
    if len(hist) == 0:
        return pd.Series([8.0] * SLOTS_PER_DAY, index=range(SLOTS_PER_DAY))
    ts = pd.to_datetime(hist['hourstamp'])
    slot = (ts.dt.hour * 60 + ts.dt.minute) // INTERVAL_MIN
    sbs = hist.assign(s=slot.values).groupby('s')['spot_ct'].mean()
    return sbs.reindex(range(SLOTS_PER_DAY)).fillna(sbs.mean() if sbs.notna().any() else 8.0)


def day_spot_vector(store, prices_df=None):
    """96-vector (15-min) of spot prices for the forecast day."""
    sbs = mean_spot_by_slot(store)
    if prices_df is not None and 'spot_ct' in prices_df and prices_df['spot_ct'].notna().any():
        return prices_df['spot_ct'].fillna(prices_df['slot'].map(sbs)).values
    return np.array([float(sbs.get(s, 8.0)) for s in range(SLOTS_PER_DAY)])
