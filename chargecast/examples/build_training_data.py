"""Transform the supplied KI-Hackathon Excel files into ChargeCast training data.

The hub data ships as three workbooks; this script reads the two the model needs
and writes the unified hourly table (hourstamp, spot_ct, target_kwh,
charged_price_ct) that `seed`/`train` consume. The third workbook (Netzentgelte)
defines grid tariff rates that live in config.json, not in the per-hour table.

It handles the real-file quirks the bare CLI readers don't:
  - Lastgang has one sheet per year (2025 full, 2026 partial); BOTH are used.
  - Spot has multiple data sheets at mixed resolution (hourly AND 15-min) plus a
    'Quelle' source sheet; data sheets are auto-detected and 15-min rows are
    aggregated to hourly.
  - Demand can extend past spot coverage; those hours get a per-hour-of-day spot
    fallback so no row is dropped.

Usage:
  python -m examples.build_training_data                  # defaults below
  python examples/build_training_data.py --out train.csv
  python examples/build_training_data.py --seed ./state   # also seed + train a model

Defaults point at the 'KI-Hackathon Juni2026' folder beside the package.
"""
from __future__ import annotations
import argparse
import os
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
# make the chargecast package importable when this file is run directly
_PKG_ROOT = os.path.normpath(os.path.join(HERE, '..'))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
DEFAULT_DATA_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'KI-Hackathon Juni2026'))
DEFAULT_LASTGANG = os.path.join(DEFAULT_DATA_DIR, 'Lastgang_Ladeinfrastruktur_Beispiel_Ladehub.xlsx')
DEFAULT_SPOT = os.path.join(DEFAULT_DATA_DIR, 'Spotmarktpreis_.xlsx')
DEFAULT_REF_PRICE = 59.0


def _hour_of(t) -> int:
    """Hour-of-day from a time/datetime, an Excel day-fraction, or a string."""
    if hasattr(t, 'hour'):
        return int(t.hour)
    try:
        return int(round(float(t) * 24)) % 24
    except (TypeError, ValueError):
        return int(pd.to_datetime(str(t)).hour)


def load_demand(path: str) -> pd.DataFrame:
    """All Lastgang sheets -> hourly target_kwh (sum of 15-min Profilwert kWh)."""
    xl = pd.ExcelFile(path)
    parts = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet, header=1)
        kwh_cols = [c for c in df.columns if 'kWh' in str(c)]
        if 'Ab-Datum' not in df.columns or not kwh_cols:
            continue  # not a profile-values sheet
        d = df[['Ab-Datum', 'Ab-Zeit', kwh_cols[0]]].dropna(subset=['Ab-Zeit']).copy()
        d.columns = ['date', 'time', 'kwh']
        d['kwh'] = pd.to_numeric(d['kwh'], errors='coerce')
        d['hourstamp'] = pd.to_datetime(
            d['date'].astype(str) + ' ' + d['time'].astype(str)).dt.floor('h')
        parts.append(d.groupby('hourstamp')['kwh'].sum().reset_index())
    if not parts:
        raise ValueError(f"No Lastgang profile sheets found in {path}")
    dem = pd.concat(parts, ignore_index=True)
    return (dem.groupby('hourstamp', as_index=False)['kwh'].sum()
               .rename(columns={'kwh': 'target_kwh'}).sort_values('hourstamp'))


def load_spot(path: str) -> pd.DataFrame:
    """All spot data sheets -> hourly spot_ct (mean of any 15-min rows)."""
    xl = pd.ExcelFile(path)
    parts = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        price_cols = [c for c in df.columns if 'Spotmarktpreis' in str(c)]
        date_cols = [c for c in df.columns if str(c).strip().lower() == 'datum']
        von_cols = [c for c in df.columns if str(c).strip().lower() == 'von']
        if not (price_cols and date_cols and von_cols):
            continue  # e.g. the 'Quelle' source sheet
        d = df[[date_cols[0], von_cols[0], price_cols[0]]].copy()
        d.columns = ['datum', 'von', 'price']
        d = d.dropna(subset=['datum', 'von'])
        d['price'] = pd.to_numeric(d['price'], errors='coerce')
        hour = d['von'].apply(_hour_of)
        d['hourstamp'] = pd.to_datetime(d['datum']) + pd.to_timedelta(hour, unit='h')
        parts.append(d.groupby('hourstamp')['price'].mean().reset_index())
    if not parts:
        raise ValueError(f"No spot-price data sheets found in {path}")
    spot = pd.concat(parts, ignore_index=True)
    return (spot.groupby('hourstamp', as_index=False)['price'].mean()
                .rename(columns={'price': 'spot_ct'}).sort_values('hourstamp'))


def build(lastgang: str, spot_path: str, ref_price: float = DEFAULT_REF_PRICE):
    """Return (unified_table, summary_dict). Demand drives the row set."""
    dem = load_demand(lastgang)
    spot = load_spot(spot_path)
    df = dem.merge(spot, on='hourstamp', how='left')

    n_missing = int(df['spot_ct'].isna().sum())
    if n_missing:
        # fall back to the per-hour-of-day mean spot (then global mean) so the
        # week of demand beyond spot coverage is kept rather than dropped.
        by_hour = df.assign(h=df['hourstamp'].dt.hour).groupby('h')['spot_ct'].mean()
        fill = df['hourstamp'].dt.hour.map(by_hour)
        df['spot_ct'] = df['spot_ct'].fillna(fill).fillna(df['spot_ct'].mean())

    df['charged_price_ct'] = float(ref_price)   # history runs at the reference price
    df = df[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct']].reset_index(drop=True)

    summary = {
        'rows': len(df),
        'date_range': (str(df['hourstamp'].min()), str(df['hourstamp'].max())),
        'spot_filled': n_missing,
        'target_kwh_mean': round(float(df['target_kwh'].mean()), 2),
        'target_kwh_max': round(float(df['target_kwh'].max()), 2),
        'spot_ct_mean': round(float(df['spot_ct'].mean()), 3),
    }
    return df, summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lastgang', default=DEFAULT_LASTGANG, help='Lastgang xlsx path')
    p.add_argument('--spot', default=DEFAULT_SPOT, help='Spotmarktpreis xlsx path')
    p.add_argument('--out', default='training_data.csv', help='output CSV path')
    p.add_argument('--ref-price', type=float, default=DEFAULT_REF_PRICE,
                   help='reference charged price for history (ct/kWh)')
    p.add_argument('--seed', metavar='STATE',
                   help='also seed + train a ChargeCast state directory from the table')
    args = p.parse_args(argv)

    for path, label in [(args.lastgang, 'Lastgang'), (args.spot, 'Spot')]:
        if not os.path.exists(path):
            print(f"{label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    df, summary = build(args.lastgang, args.spot, args.ref_price)
    df.to_csv(args.out, index=False)
    print(f"wrote {args.out}")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    if args.seed:
        import numpy as np
        from chargecast.store import Store, train
        store = Store(args.seed)
        seed_df = df.assign(baseline_pred=np.nan)
        store.append_history(seed_df[['hourstamp', 'spot_ct', 'target_kwh',
                                      'charged_price_ct', 'baseline_pred']])
        print(f"seeded + trained state at {args.seed}:")
        for k, v in train(store).items():
            print(f"  {k}: {v}")


if __name__ == '__main__':
    main()
