"""ChargeCast command line (15-minute resolution).

Usage:
  python -m chargecast.cli seed      --state STATE --data collected_cleaned_data.csv
  python -m chargecast.cli recommend --state STATE --date YYYY-MM-DD [--prices prices.csv] [--no-explore] [--out plan.csv]
  python -m chargecast.cli ingest    --state STATE --actuals actuals.csv
  python -m chargecast.cli status    --state STATE

prices.csv  : columns slot(0-95), price_ct      (and optional spot_ct)
              If given to `recommend`, evaluates YOUR prices instead of optimising.
actuals.csv : columns hourstamp, actual_kwh, charged_price_ct [, spot_ct]
              (hourstamp at 15-minute resolution)
"""
from __future__ import annotations
import argparse, sys, os
import numpy as np
import pandas as pd
from .store import Store, train, margin_per_slot, ref_price
from .core import add_features, UnifiedForecaster, cost_floor_ct, SLOTS_PER_DAY, INTERVAL_MIN
from .recommend import recommend_day, day_margin_eur


def _read_collected(path):
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


def _spot_by_slot(store):
    """Mean spot price per 15-min slot-of-day (0-95) from history (fallback 8.0 ct)."""
    hist = store.load_history()
    if len(hist) == 0:
        return pd.Series([8.0] * SLOTS_PER_DAY, index=range(SLOTS_PER_DAY))
    ts = pd.to_datetime(hist['hourstamp'])
    slot = (ts.dt.hour * 60 + ts.dt.minute) // INTERVAL_MIN
    sbs = hist.assign(s=slot.values).groupby('s')['spot_ct'].mean()
    return sbs.reindex(range(SLOTS_PER_DAY)).fillna(sbs.mean() if sbs.notna().any() else 8.0)


def _day_spot(store, prices_df=None):
    """96-vector (15-min) of spot prices for the forecast day."""
    sbs = _spot_by_slot(store)
    if prices_df is not None and 'spot_ct' in prices_df and prices_df['spot_ct'].notna().any():
        return prices_df['spot_ct'].fillna(prices_df['slot'].map(sbs)).values
    return np.array([float(sbs.get(s, 8.0)) for s in range(SLOTS_PER_DAY)])


def cmd_seed(args):
    store = Store(args.state)
    df = _read_collected(args.data)
    df['spot_ct'] = df['spot_ct'].fillna(df['spot_ct'].median() if df['spot_ct'].notna().any() else 8.0)
    df['charged_price_ct'] = df['charged_price_ct'].fillna(ref_price(store.cfg))
    df['baseline_pred'] = np.nan
    store.append_history(df[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct', 'baseline_pred']])
    summary = train(store)
    print("Seeded and trained.")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def cmd_recommend(args):
    store = Store(args.state)
    m = store.load_model()
    if m is None:
        print("No model. Run seed first.", file=sys.stderr); sys.exit(1)
    fc = UnifiedForecaster(m['shape'], m['price'])
    cfg = store.cfg
    day = pd.to_datetime(args.date)

    taxes = cfg.get('taxes_levies_ct_per_kwh', 0.0)
    user_prices = None
    if args.prices:
        pr = pd.read_csv(args.prices)
        pr.columns = [c.strip().lower() for c in pr.columns]
        if 'slot' not in pr or 'price_ct' not in pr:
            print("prices.csv needs columns: slot, price_ct", file=sys.stderr); sys.exit(1)
        pr = pd.DataFrame({'slot': range(SLOTS_PER_DAY)}).merge(pr, on='slot', how='left')
        pr['price_ct'] = pr['price_ct'].fillna(ref_price(cfg))
        user_prices = pr

    spot = _day_spot(store, user_prices)
    frame = add_features(pd.DataFrame(
        {'hourstamp': [day + pd.Timedelta(minutes=INTERVAL_MIN * i) for i in range(SLOTS_PER_DAY)],
         'spot_ct': spot}))
    floors = cost_floor_ct(spot, cfg['grid_arbeitspreis_ct_per_kwh'],
                           cfg['concession_ct_per_kwh'], taxes)

    if user_prices is not None:
        prices = np.maximum(user_prices['price_ct'].values, floors)   # never below floor
        out = fc.forecast(frame, prices)
        margin = day_margin_eur(out['kwh'], prices, spot,
                                cfg['grid_arbeitspreis_ct_per_kwh'],
                                cfg['concession_ct_per_kwh'], taxes)
        mode = 'evaluated your prices (clamped to floor)'
    else:
        # reproducible-per-date exploration draw
        rng = np.random.default_rng(abs(hash(args.date)) % (2**32))
        rec = recommend_day(fc, frame, spot, cfg, explore=not args.no_explore, rng=rng)
        prices, out, margin = rec['prices'], rec['forecast'], rec['day_margin_eur']
        n_explore = sum(1 for f in rec['explore_fraction'].values() if f > 0.5)
        lean = 'exploit only' if not rec['explore'] else f"{n_explore}/{len(rec['explore_fraction'])} blocks exploring"
        mode = f"optimised to smooth demand ({lean})"

    plan = pd.DataFrame({
        'hourstamp': frame['hourstamp'],
        'slot': range(SLOTS_PER_DAY),
        'hour': frame['hour'].values,
        'price_ct': np.round(prices, 2),
        'floor_ct': np.round(floors, 2),
        'spot_ct': np.round(spot, 3),
        'forecast_kwh': np.round(out['kwh'], 2),
        'forecast_lower': np.round(out['kwh_lower'], 2),
        'forecast_upper': np.round(out['kwh_upper'], 2),
        'margin_eur': np.round(margin_per_slot(out['kwh'], prices, spot, cfg), 2),
    })
    dest = args.out or os.path.join(args.state, f'plan_{args.date}.csv')
    plan.to_csv(dest, index=False)

    from .recommend import explore_fractions
    summary = m['price'].block_summary()
    exploring = [n for n, f in explore_fractions(m['price']).items() if f > 0.5]
    print(f"shape model: {m['shape'].kind}")
    print(f"elasticity by block (% per +10% price): " +
          " | ".join(f"{n} {b['pct']:.0f}%" for n, b in summary.items()))
    print(f"plan: {mode}")
    if exploring:
        print(f"wide-posterior blocks still needing varied pricing: {', '.join(exploring)}")
    print(f"price range: {prices.min():.1f}..{prices.max():.1f} ct/kWh (floor respected)")
    print(f"forecast total: {out['kwh'].sum():.0f} kWh "
          f"(95% CI {out['kwh_lower'].sum():.0f}..{out['kwh_upper'].sum():.0f})")
    print(f"expected energy margin: EUR {margin:.2f}")
    print(f"plan written: {dest}")


def cmd_ingest(args):
    store = Store(args.state)
    m = store.load_model()
    act = pd.read_csv(args.actuals)
    act.columns = [c.strip().lower() for c in act.columns]
    if 'hourstamp' not in act or 'actual_kwh' not in act:
        print("actuals.csv needs columns: hourstamp, actual_kwh", file=sys.stderr); sys.exit(1)
    act['hourstamp'] = pd.to_datetime(act['hourstamp'])
    act = act.rename(columns={'actual_kwh': 'target_kwh'})
    if 'charged_price_ct' not in act:
        act['charged_price_ct'] = ref_price(store.cfg)

    # score the forecast BEFORE retraining (honest out-of-sample error)
    score = None
    if m is not None:
        fc = UnifiedForecaster(m['shape'], m['price'])
        frame = add_features(act[['hourstamp']].assign(
            spot_ct=act['spot_ct'] if 'spot_ct' in act else 8.0))
        pred = fc.forecast(frame, act['charged_price_ct'].values)['kwh']
        mae = float(np.mean(np.abs(pred - act['target_kwh'].values)))
        denom = act['target_kwh'].sum()
        mape = float(np.sum(np.abs(pred - act['target_kwh'].values)) / denom * 100) if denom > 0 else np.nan
        day = act['hourstamp'].dt.normalize().iloc[0]
        score = {'date': str(day.date()), 'mae_kwh': round(mae, 2),
                 'pct_error': round(mape, 1), 'actual_total_kwh': round(float(denom), 1),
                 'shape_kind': m['shape'].kind, 'varied_days': m['price'].varied_days}
        store.log_accuracy(score)

    if 'spot_ct' not in act:
        act['spot_ct'] = np.nan
    act['baseline_pred'] = np.nan
    store.append_history(act[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct', 'baseline_pred']])
    summary = train(store)

    if score:
        print(f"scored {score['date']}: {score['pct_error']}% day error ({score['mae_kwh']} kWh MAE/slot)")
    print("retrained on all data:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def cmd_status(args):
    store = Store(args.state)
    hist = store.load_history()
    acc = store.load_accuracy()
    m = store.load_model()
    days = pd.to_datetime(hist['hourstamp']).dt.normalize().nunique() if len(hist) else 0
    print(f"history: {len(hist)} 15-min rows across {days} days")
    if m is not None:
        from .recommend import explore_fraction
        pe = m['price']
        print(f"shape model: {m['shape'].kind} ({m['shape'].n_days} days)")
        print(f"price effect (Bayesian beta) by time block:")
        for name, b in pe.block_summary().items():
            c = pe.coeffs[name]
            ef = explore_fraction(c)
            lean = 'explore' if ef > 0.5 else 'exploit'
            hrs = b['hours']
            hrange = f"{min(hrs):02d}-{max(hrs):02d}"
            print(f"  {name:<13} h{hrange}: beta {b['beta']:+.3f} "
                  f"CI[{b['beta_ci'][0]:+.2f},{b['beta_ci'][1]:+.2f}] | "
                  f"elasticity {b['pct']:.1f}% CI[{b['pct_ci'][0]:.1f},{b['pct_ci'][1]:.1f}] | "
                  f"varied days {b['varied_days']} | {lean} ({ef:.2f})")
    if len(acc):
        recent = acc.tail(7)
        print(f"recent day errors: {', '.join(str(x)+'%' for x in recent['pct_error'].tolist())}")
        print(f"mean error last {len(recent)} days: {recent['pct_error'].mean():.1f}%")


def main(argv=None):
    p = argparse.ArgumentParser(prog='chargecast')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('seed'); s.add_argument('--state', required=True); s.add_argument('--data', required=True); s.set_defaults(func=cmd_seed)
    s = sub.add_parser('recommend'); s.add_argument('--state', required=True); s.add_argument('--date', required=True); s.add_argument('--prices'); s.add_argument('--no-explore', action='store_true'); s.add_argument('--out'); s.set_defaults(func=cmd_recommend)
    s = sub.add_parser('ingest'); s.add_argument('--state', required=True); s.add_argument('--actuals', required=True); s.set_defaults(func=cmd_ingest)
    s = sub.add_parser('status'); s.add_argument('--state', required=True); s.set_defaults(func=cmd_status)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
