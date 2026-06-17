"""ChargeCast command line (v2).

Usage:
  python -m chargecast.cli seed      --state STATE --lastgang FILE.xlsx --spot FILE.xlsx
  python -m chargecast.cli recommend --state STATE --date YYYY-MM-DD [--prices prices.csv] [--no-explore] [--out plan.csv]
  python -m chargecast.cli ingest    --state STATE --actuals actuals.csv
  python -m chargecast.cli status    --state STATE

prices.csv  : columns hour(0-23), price_ct      (and optional spot_ct)
              If given to `recommend`, evaluates YOUR prices instead of optimising.
actuals.csv : columns hourstamp, actual_kwh, charged_price_ct [, spot_ct]
"""
from __future__ import annotations
import argparse, sys, os
import numpy as np
import pandas as pd
from .store import Store, train, margin_per_hour, ref_price
from .core import add_features, UnifiedForecaster, cost_floor_ct
from .recommend import recommend_day, day_margin_eur


def _read_lastgang(path):
    df = pd.read_excel(path, sheet_name=0, header=1)
    col_kwh = [c for c in df.columns if 'kWh' in str(c)][0]
    df = df[['Ab-Datum', 'Ab-Zeit', col_kwh]].dropna(subset=['Ab-Zeit'])
    df.columns = ['date', 'time', 'kwh']
    df['kwh'] = pd.to_numeric(df['kwh'], errors='coerce')
    df['dt'] = pd.to_datetime(df['date'].astype(str) + ' ' + df['time'].astype(str))
    df['hourstamp'] = df['dt'].dt.floor('h')
    return df.groupby('hourstamp')['kwh'].sum().reset_index().rename(columns={'kwh': 'target_kwh'})


def _read_spot(path):
    sp = pd.read_excel(path, sheet_name=0)
    sp.columns = ['d', 'von', 'tz1', 'bis', 'tz2', 'price'][:len(sp.columns)]
    def hr(t): return t.hour if hasattr(t, 'hour') else int(round(float(t) * 24)) % 24
    sp['hour'] = sp['von'].apply(hr)
    sp['date'] = pd.to_datetime(sp['d'])
    sp['hourstamp'] = sp['date'] + pd.to_timedelta(sp['hour'], unit='h')
    return sp.groupby('hourstamp')['price'].mean().reset_index().rename(columns={'price': 'spot_ct'})


def _spot_by_hour(store):
    """Mean spot price per hour-of-day from history (fallback 8.0 ct)."""
    hist = store.load_history()
    if len(hist) == 0:
        return pd.Series([8.0] * 24, index=range(24))
    sbh = hist.assign(h=pd.to_datetime(hist['hourstamp']).dt.hour).groupby('h')['spot_ct'].mean()
    return sbh.reindex(range(24)).fillna(sbh.mean() if sbh.notna().any() else 8.0)


def _day_spot(store, prices_df=None):
    """24-vector of spot prices for the forecast day."""
    sbh = _spot_by_hour(store)
    if prices_df is not None and 'spot_ct' in prices_df and prices_df['spot_ct'].notna().any():
        return prices_df['spot_ct'].fillna(prices_df['hour'].map(sbh)).values
    return np.array([float(sbh.get(h, 8.0)) for h in range(24)])


def cmd_seed(args):
    store = Store(args.state)
    dem = _read_lastgang(args.lastgang)
    spot = _read_spot(args.spot) if args.spot else None
    df = dem.merge(spot, on='hourstamp', how='left') if spot is not None else dem.assign(spot_ct=np.nan)
    df['spot_ct'] = df['spot_ct'].fillna(df['spot_ct'].median() if df['spot_ct'].notna().any() else 8.0)
    df['charged_price_ct'] = ref_price(store.cfg)        # history runs at the reference price
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

    user_prices = None
    if args.prices:
        pr = pd.read_csv(args.prices)
        pr.columns = [c.strip().lower() for c in pr.columns]
        if 'hour' not in pr or 'price_ct' not in pr:
            print("prices.csv needs columns: hour, price_ct", file=sys.stderr); sys.exit(1)
        pr = pd.DataFrame({'hour': range(24)}).merge(pr, on='hour', how='left')
        pr['price_ct'] = pr['price_ct'].fillna(ref_price(cfg))
        user_prices = pr

    spot = _day_spot(store, user_prices)
    frame = add_features(pd.DataFrame(
        {'hourstamp': [day + pd.Timedelta(hours=h) for h in range(24)], 'spot_ct': spot}))
    floors = cost_floor_ct(spot, cfg['grid_arbeitspreis_ct_per_kwh'], cfg['concession_ct_per_kwh'])

    if user_prices is not None:
        prices = np.maximum(user_prices['price_ct'].values, floors)   # never below floor
        out = fc.forecast(frame, prices)
        margin = day_margin_eur(out['kwh'], prices, spot,
                                cfg['grid_arbeitspreis_ct_per_kwh'], cfg['concession_ct_per_kwh'])
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
        'hour': range(24),
        'price_ct': np.round(prices, 2),
        'floor_ct': np.round(floors, 2),
        'spot_ct': np.round(spot, 3),
        'forecast_kwh': np.round(out['kwh'], 2),
        'forecast_lower': np.round(out['kwh_lower'], 2),
        'forecast_upper': np.round(out['kwh_upper'], 2),
        'margin_eur': np.round(margin_per_hour(out['kwh'], prices, spot, cfg), 2),
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
        print(f"scored {score['date']}: {score['pct_error']}% day error ({score['mae_kwh']} kWh MAE/hr)")
    print("retrained on all data:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def cmd_status(args):
    store = Store(args.state)
    hist = store.load_history()
    acc = store.load_accuracy()
    m = store.load_model()
    days = pd.to_datetime(hist['hourstamp']).dt.normalize().nunique() if len(hist) else 0
    print(f"history: {len(hist)} hourly rows across {days} days")
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
    s = sub.add_parser('seed'); s.add_argument('--state', required=True); s.add_argument('--lastgang', required=True); s.add_argument('--spot'); s.set_defaults(func=cmd_seed)
    s = sub.add_parser('recommend'); s.add_argument('--state', required=True); s.add_argument('--date', required=True); s.add_argument('--prices'); s.add_argument('--no-explore', action='store_true'); s.add_argument('--out'); s.set_defaults(func=cmd_recommend)
    s = sub.add_parser('ingest'); s.add_argument('--state', required=True); s.add_argument('--actuals', required=True); s.set_defaults(func=cmd_ingest)
    s = sub.add_parser('status'); s.add_argument('--state', required=True); s.set_defaults(func=cmd_status)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
