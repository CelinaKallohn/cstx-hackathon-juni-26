# Build plan — v2 redesign (option B: unified model with Bayesian price effect)

This is the agreed redesign. The current package (v0.1) uses a split design:
a profile baseline plus a separate elasticity layer where price is NOT a model
column. We are replacing that with a unified design where price is a normal
input column and its effect is learned as a Bayesian coefficient that starts
from the user's prior and sharpens with varied-price data.

Read this top to bottom before writing code. Build in the numbered order; each
step is testable before the next.

## Locked decisions (confirmed by the user)

- Architecture: option B — unified model, price is a normal column, price effect
  is a Bayesian coefficient starting from the user's prior. CONFIRMED.
- Bayesian engine: conjugate Bayesian regression (Normal-Normal). Lightweight,
  numpy-only, no PyMC. CONFIRMED.
- Cost floor: break-even = spot + grid + concession, no extra margin. CONFIRMED.
- When smoothing vs margin conflict: smooth as hard as possible; only rule is
  total day margin >= 0. CONFIRMED.
- Prices may rise above 59 in peak and fall below in troughs. CONFIRMED.
- Build location: this will be built in Claude Code locally, not here.

## Why this design (the reasoning, so you don't undo it)

- The user's goal is to SET PRICES THAT SMOOTH DEMAND while never selling below
  cost. Demand prediction is the means, not the end. So the system must
  *recommend* prices, not just forecast demand for prices the user types.
- Column consistency: historic rows and future rows must have identical columns.
  Price is one of them. Historic price is fixed at 59; future price varies.
- The day-one problem: with price constant at 59 in all history, a plain model
  concludes price has no effect. We fix this with a Bayesian prior on the price
  coefficient — the user's guess (with a confidence) gives price a believed
  effect immediately, and real varied-price days update it. No hard switch; the
  posterior compounds day over day (today's posterior = tomorrow's prior).

## The model = two cooperating parts

1. DEMAND SHAPE (learned from data, works day one):
   predicts price-neutral demand per hour from hour, dayofweek, month,
   is_weekend, dayofyear, trend, hour_sin, hour_cos. Reuse the existing
   profile/GBM auto-select logic from v0.1 BaselineModel — it already works and
   tested as profile-wins on the seed data.

2. PRICE EFFECT (Bayesian coefficient):
   a conjugate Bayesian linear regression of log(demand multiplier) on relative
   price deviation. The "demand multiplier" for an hour is
   actual_kwh / shape_prediction. The model:
       log(multiplier) = beta * price_dev + noise
       price_dev = (charged_price_ct - ref_price) / ref_price
   beta is the price coefficient (negative: higher price -> less demand).
   The user's elasticity guess maps to a prior mean for beta; their confidence
   maps to the prior variance.

   Mapping from the user's "% volume shift per +10% price" to beta:
       a +10% price (price_dev = 0.10) should multiply demand by (1 - pct/100)
       so beta ≈ ln(1 - pct/100) / 0.10
   e.g. pct=50 -> beta ≈ ln(0.5)/0.1 ≈ -6.93; pct=25 -> beta ≈ ln(0.75)/0.1 ≈ -2.88
   (Keep this conversion in one helper, both directions, with tests.)

## Conjugate Bayesian regression (the engine)

Use a Normal prior on beta with known-ish noise variance (Normal-Normal
conjugacy) for simplicity and zero dependencies. Per update:

   prior:      beta ~ Normal(m0, s0^2)
   data:       y_i = log(multiplier_i), x_i = price_dev_i, noise var = sigma^2
   posterior precision = 1/s0^2 + (sum x_i^2)/sigma^2
   posterior mean      = (m0/s0^2 + (sum x_i*y_i)/sigma^2) / posterior precision
   posterior var       = 1 / posterior precision

- m0, s0 come from the user's prior (guess + confidence) on the very first fit.
- On each retrain, recompute the posterior from the ORIGINAL prior + ALL
  varied-price data so far (retrain-from-scratch keeps it simple and drift-free,
  consistent with v0.1's chosen update style).
- sigma^2: estimate from residuals once there's enough data; before that use a
  fixed moderate default (document it).
- Only rows where price actually deviates (|price_dev| > small eps) inform beta.
  Constant-59 historic rows contribute nothing to beta (correct — they carry no
  price signal), so the prior dominates until varied days exist. This is the
  day-one fix working as intended.

Expose beta as: point estimate, plus a credible interval (mean ± 1.96*sqrt(var)).
Convert back to the "% per +10%" scale for display.

## Combining into a forecast (with uncertainty)

   forecast_kwh(hour, price) = shape_prediction(hour) * exp(beta * price_dev)
- Use posterior mean beta for the central forecast.
- Propagate uncertainty: sample beta from its posterior (or use beta ± 1.96 sd)
  to produce a forecast credible interval. The recommender and the daily plan
  should surface this interval, not just a point number.

## Cost floor (pure arithmetic, no learning)

   floor_ct(hour) = spot_ct(hour) + grid_arbeitspreis_ct + concession_ct
   (from config: grid_arbeitspreis_ct_per_kwh=8.24, concession_ct_per_kwh=0.11)
- User decision on file: floor = break-even (spot + grid only, no extra margin).
- On negative-spot hours the floor is still the grid+concession sum, never < that.
- HARD constraint: the recommender may never propose a price below floor_ct.

## Price recommender (step 1 in detail)

Goal: choose a 24-hour price vector that SMOOTHS demand (flattens the peak),
subject to:
  (a) every price >= floor_ct(hour)            [never at a loss — hard]
  (b) total day margin >= 0                     [user: margin just has to stay positive]
  (c) prices may go ABOVE 59 in peak, BELOW 59 in troughs   [user confirmed]
Objective: minimize a flatness penalty on the resulting demand curve, e.g.
variance of forecast_kwh across hours (or peak-to-mean ratio).

Search: prices are continuous and bounded [floor, sane_cap]. A simple, robust
approach: coordinate descent or scipy.optimize.minimize (SLSQP) with the
constraints; if avoiding scipy, a bounded random/greedy search is acceptable for
v2 (document the tradeoff). Keep the objective and constraints in clearly named
functions so the method can be swapped.

EXPLORE vs EXPLOIT (important — don't omit):
  When beta's posterior is still wide (early days), the recommender should add
  deliberate price variation to LEARN, not just exploit the current best guess.
  Implement as: inflate exploration when posterior sd is high (e.g. add a bonus
  for price diversity, or sample beta from the posterior per-candidate —
  Thompson-sampling style). As data sharpens beta, exploration fades naturally.
  Document this clearly; it's the honest resolution of "can't optimize well
  until elasticity is known."

## Unified table (replaces the v0.1 feature split)

INPUT columns (identical for historic and future rows):
  charged_price_ct, spot_ct, hour, dayofweek, month, is_weekend,
  dayofyear, trend, hour_sin, hour_cos
OUTPUT: target_kwh
Note: charged_price_ct feeds the price-effect part (via price_dev), the rest
feed the demand-shape part. Keep add_features() but ensure charged_price_ct is
carried through as a first-class column everywhere.

## CLI changes

- seed     : unchanged in spirit; seeds history (price=59) + trains both parts.
- recommend: NEW (replaces manual predict). Args: --state, --date.
             Outputs the recommended 24h price set, the forecast (with interval),
             per-hour floor, and expected margin. Optional --prices to still
             evaluate a user-supplied set instead of optimizing.
- ingest   : unchanged in spirit; score vs forecast, append, retrain BOTH parts
             (demand shape + Bayesian beta posterior).
- status   : add beta point estimate + credible interval + varied-price-day count
             + current explore/exploit lean.

## Config additions (config.json)

  prior_elasticity_pct        (the guess, default 50)
  prior_confidence            ('loose'|'medium'|'tight' -> s0 mapping; document)
  price_cap_ct                (sane upper bound for recommended prices)
  ref_price_ct                (59)
  grid_arbeitspreis_ct_per_kwh (8.24), concession_ct_per_kwh (0.11)

## Build order (each step testable before the next)

1. Unified table + demand-shape model: refactor so charged_price_ct is a normal
   column; demand shape reuses v0.1 BaselineModel logic. Verify seed+train runs
   and shape predictions match v0.1 (price has no effect yet).
2. Bayesian price coefficient: implement the % <-> beta conversion (with tests),
   the conjugate update, and prior config. Unit-test: fed synthetic data from a
   known true pct, the posterior recovers it and the credible interval shrinks
   with more days. (v0.1 already proved ~25% recovered from true 30% with the old
   estimator — match or beat that, and now WITH error bars.)
3. Combine into forecast(hour, price) with a credible interval.
4. Cost floor: pure function + unit test (incl. negative-spot hour).
5. Price recommender: objective + constraints + search + explore/exploit. Test
   that recommended prices never dip below floor and total margin >= 0, and that
   the resulting curve is flatter than the flat-59 baseline.
6. Rewire CLIs (seed/recommend/ingest/status), refresh README + this HANDOFF.

## Honest limitations to keep documented

- Recommended prices early on are only as good as the prior; exploration is how
  the system earns the right to trust its own optimization.
- beta is a single global price sensitivity (one number for all hours) in v2.
  A future v3 could let sensitivity vary by hour (commuters vs midday) — note it.
- Margin is energy-only; capacity (Leistungspreis) and fixed fees not modelled.
- Demand-shape day-to-day variation is largely unexplained by current features
  (proven in v0.1). Adding weather/events/utilisation is the path to a real GBM win.
