# ChargeCast

A small, self-improving pricing+forecasting tool for an EV charging hub. It
starts from the historic data you already have, **recommends prices that smooth
demand** without ever selling below cost, and gets sharper as you feed it each
real day. Everything runs at **15-minute resolution** (96 slots per day).

## The loop

```
1. recommend  propose tomorrow's 96-slot (15-min) price vector that flattens
              demand (or evaluate a price set you supply), with a demand
              forecast and its uncertainty
2. deploy     you run those prices for a day; customers charge
3. ingest     you hand it the day's actual kWh; it scores its own forecast,
              adds the day to its memory, and retrains
```

Each day the model reflects all prior days. The same commands work on day 1 and
on day 500 — nothing is rebuilt by hand.

## How it works — one table, two cooperating parts

Everything lives in one unified table where price (`charged_price_ct`) is a
normal input column, identical for historic and future rows. The forecast is:

```
forecast_kwh(slot, price) = demand_shape(slot) * exp(beta_block(hour) * price_dev)
price_dev = (price - ref_price) / ref_price
```

`slot` is the 15-minute slot of the day (0–95). Price effect is still grouped by
*hour*-of-day block; the demand shape resolves to 15-minute slots.

- **Demand shape** — *learned from data*. Price-neutral demand per 15-min slot.
  Starts as a slot × weekday profile (robust when data is thin) and auto-upgrades
  to a gradient-boosting model **only if** it beats the profile on a holdout. On
  the supplied data the profile wins, so that's what it uses.
- **Price effect** — *Bayesian coefficients `beta`, one per time-of-day block*.
  Price sensitivity differs by time of day (flexible at midday, rigid at the
  08:00 commuter peak), so `beta` is grouped into blocks (overnight, morning_peak,
  midday, afternoon, evening_peak, late by default — configurable). Your
  elasticity guess (“% volume shift per +10% price”) and confidence
  (`loose`/`medium`/`tight`) seed **every block's prior** identically; each block
  then updates its own **posterior** by conjugate Normal–Normal regression of
  `log(actual/shape)` on `price_dev`, using only that block's rows. A block you
  never vary keeps its prior (no evidence → belief unchanged); blocks you vary
  sharpen independently. No hard switch — today's posterior is tomorrow's prior.
  Each block reports a point estimate **plus a credible interval** that narrows as
  that block sees varied prices.

### Price recommender (the new core)

`recommend` chooses a 96-slot (15-minute) price vector that **smooths demand**
(minimises the coefficient of variation of forecast demand — scale-invariant, so
it flattens the *shape* rather than just crushing volume), subject to hard rules:

- every price ≥ **cost floor** = `spot + grid (Arbeitspreis) + taxes&levies`
  (break-even, no margin); negative-spot slots never floor below the fixed
  grid+taxes cost,
- **total day margin ≥ 0** (automatically met given the break-even floor),
- prices may rise **above** the reference in peaks and fall **below** it in troughs.

**Explore vs exploit (per block):** while a block's posterior is wide the
recommender draws that block's `beta` from its posterior (Thompson sampling), so
its hours' prices vary deliberately — that variation is how it *learns* that
block's elasticity. Blocks already pinned down barely move. As each block's
posterior sharpens, its prices converge to the exploit optimum. Pass
`--no-explore` to force the exploit optimum everywhere.

## Install

```bash
pip install -e .          # from the package directory
# requires: pandas, numpy, scikit-learn, openpyxl
```

## Commands

Seed once with your combined 15-minute history CSV (history runs at the
reference price; the loader reads the `collected_cleaned_data.csv` format —
semicolon-separated, German decimal comma):
```bash
python -m chargecast.cli seed --state ./state --data collected_cleaned_data.csv
```

Recommend a day's prices:
```bash
python -m chargecast.cli recommend --state ./state --date 2026-06-18
# evaluate your own prices instead of optimising (clamped up to the floor):
python -m chargecast.cli recommend --state ./state --date 2026-06-18 --prices prices.csv
# force the exploit optimum (no exploration draw):
python -m chargecast.cli recommend --state ./state --date 2026-06-18 --no-explore
```
Writes a per-slot plan (96 rows): `slot`, `hour`, `price_ct`, `floor_ct`,
`spot_ct`, `forecast_kwh` with `forecast_lower`/`forecast_upper` (95% credible
interval), and `margin_eur`. A user-supplied `prices.csv` uses columns
`slot`(0–95), `price_ct` (and optional `spot_ct`).

Ingest a real day (`actuals.csv`: `hourstamp,actual_kwh[,charged_price_ct,spot_ct]`):
```bash
python -m chargecast.cli ingest --state ./state --actuals actuals.csv
```
Scores the forecast *before* retraining (honest, out-of-sample), appends the day,
then retrains the shape and recomputes the `beta` posterior.

Check progress (shape model, plus per block: `beta` + credible interval,
elasticity %, varied-price day count, explore/exploit lean):
```bash
python -m chargecast.cli status --state ./state
```

## State directory

```
state/history.csv        every 15-min outcome accumulated (the unified table)
state/model.pkl          trained DemandShapeModel + Bayesian PriceEffect
state/accuracy_log.csv   one row per scored day (watch error shrink)
state/config.json        reference price, price-effect prior, price cap, tariff
```

`config.json` keys you may tune: `reference_price_ct`/`ref_price_ct` (default 59),
`prior_elasticity_pct` (your guess, default 50), `prior_confidence`
(`loose`/`medium`/`tight`), `price_cap_ct` (recommended-price upper bound),
`price_blocks` (the time-of-day partition — must cover hours 0–23 with no gaps or
overlaps; validated on load), and the tariff rates:
`grid_arbeitspreis_ct_per_kwh` (7.48), `taxes_levies_ct_per_kwh` (6.986, the
Steuern&Abgaben), `concession_ct_per_kwh` (0.0). The cost floor sums all three
with the spot price.

## Honest limitations

- Early recommendations are only as good as the prior; exploration is how the
  system earns the right to trust its own optimisation. With a wide posterior the
  forecast's *upper* credible bound can be very large (the `exp(beta·price_dev)`
  link amplifies uncertainty at prices far from the reference) — the central
  estimate and lower bound are the trustworthy parts until `beta` sharpens.
- Grouped betas need varied pricing *within* a block to learn that block; a block
  you never vary stays at its prior. Spread exploration across blocks over time.
  Block boundaries are a modeling choice — the defaults suit a charging hub, but
  redefine `price_blocks` from operational knowledge. A future **v3** could use
  hierarchical per-hour betas that borrow strength via a global hyper-prior,
  superseding fixed blocks once months of varied data exist.
- Demand-shape day-to-day variation is largely unexplained by current features
  (slot-of-day and weekday carry nearly all the signal). Weather/events/utilisation
  are the path to a real GBM win.
- Margin is energy-only. Capacity charges (Leistungspreis) and fixed monthly fees
  are not modelled per slot.
- The Steuern&Abgaben rate changed during the dataset (6.691 → 6.986 ct/kWh);
  the cost floor for future days uses the latest value from config.
```
