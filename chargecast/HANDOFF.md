# Handoff notes

Context for continuing this project in a fresh session. The README covers usage;
this file covers *why things are the way they are* and *what's next*.

## What this is

A self-improving daily pricing+forecasting tool for an EV charging hub. Daily
loop: **recommend** tomorrow's 15-minute prices that smooth demand (never below
cost) → deploy → **ingest** the actual kWh → score, accumulate, retrain. Same
commands work on day 1 and day 500.

## Update: 15-minute resolution (supersedes the hourly design below)

The model now runs at **15-minute** resolution (96 slots/day) instead of hourly,
driven by the combined `collected_cleaned_data.csv` dataset. What changed:

- **Within-day index** is now `slot` ∈ [0,96), `slot = (hour*60 + minute)//15`
  (`INTERVAL_MIN`/`SLOTS_PER_DAY` in `demand_forecast_model.py`). The demand-shape **profile groups
  by `(dayofweek, slot)`** and cyclic features are `slot_sin`/`slot_cos`.
- **Price-effect blocks stay hour-based (0–23)** by design — `PriceEffect` still
  maps each row's *hour* → block, so the block config and `validate_price_blocks`
  are unchanged and operator-facing. Only the shape + per-day vector length moved
  to 15-min. `recommend_prices`/`recommend_day` were already length-agnostic.
- **Cost floor / tariff** now `spot + Arbeitspreis (7.48) + Steuern&Abgaben`
  (config `grid_arbeitspreis_ct_per_kwh`, new `taxes_levies_ct_per_kwh` 6.986,
  `concession_ct_per_kwh` 0.0), matching the data's `Gewinn` column. Steuern&Abgaben
  changed 6.691→6.986 mid-dataset; future-day floors use the latest (config) value.
- **`seed`** takes a single `--data CSV` (the `read_collected_csv` loader in
  `dataio.py`: semicolon, German decimal comma, UTF-8 BOM; ignores `Gewinn`/`Profilwert
  kW`), replacing the two-xlsx `--lastgang`/`--spot` flow. `recommend` plans/`prices.csv`
  are keyed by `slot` (0–95). Test fixture is `tests/data/seed_dataset.csv` (90 days).

## Status: v2 is BUILT (with grouped price betas)

The v2 redesign in `BUILD_PLAN_v2.md`, plus the grouped-price-betas amendment
(`Handoff — grouped price betas`), is implemented and tested (43 tests in
`tests/`, all passing). v2 replaced the v0.1 split design (profile baseline +
separate frequentist elasticity layer with a day-30 hard switch). The old
`ElasticityLayer` is gone; `BaselineModel` survives only as an alias of the
renamed `DemandShapeModel`.

**Grouped betas:** the price effect is no longer one global coefficient. It is one
conjugate coefficient per time-of-day block (default blocks: overnight 0-6,
morning_peak 7-9, midday 10-14, afternoon 15-16, evening_peak 17-19, late 20-23;
configurable via `price_blocks`). Every block is seeded from the same shared prior
and sharpens independently as it sees varied prices; an un-varied block keeps its
prior. This lets sensitivity differ by time of day (flexible midday vs rigid
commuter peak) while staying learnable with realistic data.

### Architecture (one unified table, two parts)

Price (`charged_price_ct`) is a normal column of one table, identical for
historic and future rows. `forecast_kwh = demand_shape(hour) * exp(beta*price_dev)`.

1. **DemandShapeModel** (`demand_forecast_model.py`) — price-neutral demand. v0.1 profile/GBM
   auto-select logic, unchanged. Reads `SHAPE_FEATURES` (deliberately excludes
   `charged_price_ct`). A regression test pins its output to v0.1 exactly.
2. **BlockBeta** (`demand_forecast_model.py`) — the conjugate Normal–Normal engine for ONE block's
   coefficient: `update(price_dev, y)`, reporting (`beta`/`beta_ci`/`pct`/`pct_ci`),
   `sample_beta` (Thompson). No-signal → posterior stays at prior.
   **PriceEffect** (`demand_forecast_model.py`) — holds one BlockBeta per block (`coeffs` dict),
   the `hour → block` lookup, and `fit_from_history` (slices rows per block and
   updates each). Per-hour resolution via `beta_for_hours` / `beta_ci_for_hours` /
   `expected_multiplier(hours, …)` / `sample_beta_for_hours` (one draw per block).
   `% ↔ beta` in `pct_to_beta`/`beta_to_pct`; `validate_price_blocks` enforces the
   0-23 partition. Posterior recomputed from the original prior + ALL varied-price
   history each retrain (retrain-from-scratch, drift-free).
3. **UnifiedForecaster** (`demand_forecast_model.py`) — combines shape + grouped price effect;
   `forecast()` resolves each hour's beta from its block and returns central kwh
   plus a credible interval from the per-block posterior bounds.
4. **cost_floor_ct** (`demand_forecast_model.py`) — break-even `spot+grid+concession`; negative
   spot never floors below the fixed grid+concession cost.
5. **recommender** (`recommend.py`) — coordinate-descent search over bounded
   prices `[floor, price_cap]` minimising the demand curve's coefficient of
   variation (scale-invariant, so it can't game the objective by crushing
   volume). Explore/exploit is PER BLOCK: `recommend_day` draws one beta per block
   (`sample_beta_for_hours`) so wide-posterior blocks vary their hours' prices
   while pinned blocks barely move. `explore_fraction(coeff)` per block;
   `explore_fractions(price_effect)` returns the dict.

### Key design decisions (and why)

1. **Break-even floor makes the margin constraint nearly free.** With
   `floor = grid+conc+max(spot,0) ≥ true cost`, every at-or-above-floor hour has
   non-negative margin, so "total day margin ≥ 0" is implied by the floor. The
   recommender still computes and surfaces the day margin.
2. **CV, not variance, as the flatness objective.** Plain variance is minimised
   by pushing all prices to the cap (demand → 0). CV (std/mean) is scale-invariant
   and responds only to the demand *shape*.
3. **Exploration is per-block Thompson sampling, not a schedule.** Each block's
   wide posterior yields diverse day-to-day prices for its hours (learning) and
   fades automatically as that block sharpens. `recommend` seeds its RNG from the
   date (reproducible per day).
4. **Retrain-from-scratch each day**, not incremental — fast at this scale and
   drift-free.
5. **Economics are energy-only.** Margin = revenue − (spot + Arbeitspreis 7.48 +
   Steuern&Abgaben 6.986) ct/kWh. Leistungspreis and fixed fees are not per-slot.

### Verification highlights

- Demand shape matches v0.1 byte-for-byte (`test_demand_shape.py`).
- Engine recovers a known true elasticity with a shrinking interval; grouped fit
  recovers DIFFERENT per-block elasticities, an un-varied block keeps its prior,
  and `validate_price_blocks` rejects overlaps/gaps (`test_price_effect.py`).
- Recommender never dips below floor, keeps day margin ≥ 0, flattens demand vs
  the flat-reference baseline, and exploration is per-block (wide block varies
  far more than a pinned block) (`test_recommend.py`). End-to-end CLI flow incl.
  invalid-config rejection in `test_cli_e2e.py`.

Run: `pip install -e .[…]` then `pytest tests/` (deps: pandas, numpy,
scikit-learn, openpyxl, pytest).

## Honest limitations (don't paper over these)

- Early recommendations are only as good as the prior; with a wide posterior the
  forecast's **upper** credible bound can be very large (the exp link amplifies
  uncertainty at prices far from the reference). Central estimate + lower bound
  are the trustworthy parts until `beta` sharpens.
- Grouped betas need varied pricing *within* each block to learn it; a block you
  never vary stays at its prior. Block boundaries are a modeling choice (defaults
  suit a hub; override `price_blocks`). **v3** upgrade path (note, not built):
  hierarchical per-hour betas sharing strength via a global hyper-prior, so
  low-data hours borrow from the overall pattern — supersedes fixed blocks once
  months of varied data exist.
- Demand-shape day-to-day variation is largely unexplained by current features;
  weather/events/utilisation are the path to a real GBM win.

## Where the real data lives

The combined 15-minute dataset is `collected_cleaned_data.csv` (under the repo-root
`data/` folder, not the package). For `seed`, point `--data` at it. For
development/testing, `tests/data/seed_dataset.csv` is a 90-day prebuilt
unified table (used by the test fixtures); `examples/legacy/charging_hourly_dataset.csv`
is the retired hourly dataset, kept only for reference.
