# ChargeCast

> For an honest look at the problem, the prototype's maturity, our use of AI, how
> we validated it, and what's next, see [`SELFASSESSMENT.md`](../SELFASSESSMENT.md).

## What it is

A self-improving daily demand forecaster and dynamic price recommender for an EV
charging hub. It works at **15-minute resolution** (96 slots per day),
**recommends prices that smooth demand** without ever selling below cost, and
gets sharper every day you feed it the real actuals. The same commands work on
day 1 and on day 500 — nothing is rebuilt by hand.

## How to use it

### Install

```bash
pip install -e .          # from the package directory
# requires: pandas, numpy, scikit-learn, openpyxl
```

### The daily loop

```
recommend  →  deploy  →  ingest  →  (repeat)
```

**Seed** once from your combined 15-minute history (history runs at the
reference price; the loader reads the `collected_cleaned_data.csv` format —
semicolon-separated, German decimal comma):

```bash
python -m chargecast.cli seed --state ./state --data collected_cleaned_data.csv
```

**Recommend** a day's prices:

```bash
python -m chargecast.cli recommend --state ./state --date 2026-06-18
# evaluate your own prices instead of optimising (clamped up to the floor):
python -m chargecast.cli recommend --state ./state --date 2026-06-18 --prices prices.csv
# force the exploit optimum (no exploration draw):
python -m chargecast.cli recommend --state ./state --date 2026-06-18 --no-explore
```

Writes a per-slot plan (96 rows) with columns `slot`, `hour`, `price_ct`,
`floor_ct`, `spot_ct`, `forecast_kwh`, `forecast_lower`/`forecast_upper` (95%
credible interval), and `margin_eur`. A user-supplied `prices.csv` uses columns
`slot` (0–95), `price_ct` (and optional `spot_ct`).

**Ingest** a real day (`actuals.csv`: `hourstamp,actual_kwh[,charged_price_ct,spot_ct]`).
It scores the forecast *before* retraining (honest, out-of-sample), appends the
day, then retrains the shape and recomputes the `beta` posterior:

```bash
python -m chargecast.cli ingest --state ./state --actuals actuals.csv
```

**Status** — check progress (shape model; per block: `beta` + credible interval,
elasticity %, varied-price day count, explore/exploit lean):

```bash
python -m chargecast.cli status --state ./state
```

### Run the whole thing end to end

```bash
python run_simulation.py        # from the package directory
```

Replays the simulated days through the real CLI — seed, recommend the first day,
then ingest+recommend each subsequent day, finishing with `status`. Per-day plans
land in `state/plan_<date>.csv`.

### Feeding the dashboard

`run_simulation.py` copies its outputs into the dashboard's `public/` tree as its
last step, so the UI shows them automatically (restart `ng serve` or refresh to
pick up new files). The Angular app serves `public/` at the web root and fetches
by filename — `/collected_cleaned_data.csv`, `/prediction/plan_<date>.csv`, and
`/simulation/simulated_data_<date>.csv` — so files just need to land in those
folders with their names unchanged. To wire up plans from a standalone `recommend`
run, copy `state/plan_<date>.csv` into `dashboard/hackathon-energy/public/prediction/`.

## How it works

Everything lives in one unified table where price (`charged_price_ct`) is a normal
input column, identical for historic and future rows. The forecast is:

```
forecast_kwh(slot, price) = demand_shape(slot) * exp(beta_block(hour) * price_dev)
price_dev = (price - ref_price) / ref_price
```

Two cooperating parts:

- **Demand shape** — *learned from data*. Price-neutral demand per 15-min slot.
  Starts as a slot × weekday profile (robust when data is thin) and auto-upgrades
  to a gradient-boosting model only if it beats the profile on a holdout.

- **Price effect** — *Bayesian coefficients `beta`, one per time-of-day block*.
  Price sensitivity differs by time of day (flexible at midday, rigid at the
  08:00 commuter peak), so `beta` is grouped into blocks (overnight, morning_peak,
  midday, afternoon, evening_peak, late by default — configurable). Your
  elasticity guess and confidence seed every block's prior identically; each block
  then updates its own posterior by conjugate Normal–Normal regression of
  `log(actual/shape)` on `price_dev`, using only that block's rows. A block you
  never vary keeps its prior; blocks you vary sharpen independently. Today's
  posterior is tomorrow's prior.

**The recommender** chooses the 96-slot price vector that **smooths demand**
(minimises the coefficient of variation of forecast demand — scale-invariant, so
it flattens the *shape* rather than just crushing volume), subject to hard rules:
every price ≥ the **cost floor** (`spot + grid + taxes&levies`, break-even, no
margin) and **total day margin ≥ 0**. Prices may rise above the reference in peaks
and fall below it in troughs. While a block's posterior is wide, the recommender
draws that block's `beta` from its posterior (Thompson sampling) so its prices
vary deliberately — that variation is how it learns the block's elasticity; as the
posterior sharpens, prices converge to the exploit optimum (`--no-explore` forces
the exploit optimum everywhere).

## State directory

```
state/history.csv        every 15-min outcome accumulated (the unified table)
state/model.pkl          trained DemandShapeModel + Bayesian PriceEffect
state/accuracy_log.csv   one row per scored day (watch error shrink)
state/config.json        reference price, price-effect prior, price cap, tariff
```

Tunable `config.json` keys: `reference_price_ct` (default 59),
`prior_elasticity_pct` (your guess, default 50), `prior_confidence`
(`loose`/`medium`/`tight`), `price_cap_ct`, `price_blocks` (the time-of-day
partition — must cover hours 0–23 with no gaps or overlaps), and the tariff rates
`grid_arbeitspreis_ct_per_kwh` (7.48), `taxes_levies_ct_per_kwh` (6.986),
`concession_ct_per_kwh` (0.0). The cost floor sums all three with the spot price.

## Limitations

- Early recommendations are only as good as the prior; exploration is how the
  system earns the right to trust its own optimisation. With a wide posterior the
  forecast's *upper* bound can be very large — the central estimate and lower bound
  are the trustworthy parts until `beta` sharpens.
- Grouped betas need varied pricing *within* a block to learn it; a block you
  never vary stays at its prior. Spread exploration across blocks over time.
- Margin is energy-only. Capacity charges (Leistungspreis) and fixed monthly fees
  are not modelled per slot.
- The Steuern&Abgaben rate changed during the dataset (6.691 → 6.986 ct/kWh); the
  cost floor for future days uses the latest value from config.
