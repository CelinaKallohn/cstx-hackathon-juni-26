"""Core forecasting engine for the charging hub.

The forecast is two cooperating parts over ONE unified table where price is a
normal input column (identical columns for historic and future rows):

  1. DEMAND SHAPE  - price-neutral demand per hour, learned from data (profile
                     early, GBM once data is rich enough). Reads SHAPE_FEATURES;
                     deliberately does NOT read charged_price_ct.
  2. PRICE EFFECT  - a Bayesian coefficient on relative price deviation, fed by
                     charged_price_ct. Starts from the user's prior and sharpens
                     with varied-price data.

`charged_price_ct` is a first-class column of the unified table everywhere; it is
simply routed to the price-effect part rather than the demand-shape part.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

# Intraday resolution. The model runs on 15-minute slots: 96 slots per day,
# indexed 0..95 by slot = (hour*60 + minute) // INTERVAL_MIN.
INTERVAL_MIN = 15
SLOTS_PER_DAY = 24 * 60 // INTERVAL_MIN     # 96

# The unified input schema. Historic and future rows carry identical columns.
INPUT_COLUMNS = ['charged_price_ct', 'spot_ct', 'slot', 'hour', 'dayofweek', 'month',
                 'is_weekend', 'dayofyear', 'trend', 'slot_sin', 'slot_cos']

# Features the DEMAND-SHAPE model reads. charged_price_ct is intentionally
# excluded: the shape model predicts price-neutral demand; price is handled by
# the Bayesian price-effect coefficient instead.
SHAPE_FEATURES = ['spot_ct', 'slot', 'hour', 'dayofweek', 'month', 'is_weekend',
                  'dayofyear', 'trend', 'slot_sin', 'slot_cos']

# Back-compat alias (v0.1 name).
FEATURES = SHAPE_FEATURES

GBM_MIN_DAYS = 120          # below this, the profile baseline is used
PRICE_VARIATION_THRESHOLD = 0.02  # >2% intraday price spread = a "varied-price" day
GBM_TRAIN_FRACTION = 0.85   # chronological train share; the rest is the GBM-vs-profile holdout
GBM_IMPROVEMENT_RATIO = 0.98  # GBM is adopted only if its holdout MAE < this * profile MAE
RANDOM_SEED = 42            # fixed seed so the GBM is reproducible

# --- price-effect (Bayesian coefficient) constants ---
PRICE_DEV_EPS = 1e-4        # |price_dev| below this carries no price signal
SIGMA2_DEFAULT = 0.25       # log-multiplier noise variance before data informs it
SIGMA2_MIN_POINTS = 20      # varied points needed before estimating sigma^2 from residuals
# prior_confidence -> prior sd (s0) on beta. Wider = less sure of the guess.
# As a feel for the widths, with a prior centred on pct=50 (beta=-6.93) the 95%
# prior band on the elasticity is roughly: tight ~[39%,59%], medium ~[18%,69%],
# loose ~[-33%,81%]. Loose lets the data move beta a lot; tight anchors it.
CONFIDENCE_S0 = {'loose': 5.0, 'medium': 2.5, 'tight': 1.0}

# Price sensitivity is grouped by time-of-day block (one beta per block), so it
# can differ between e.g. a flexible midday and a rigid commuter peak while
# staying learnable with realistic varied-price data. Blocks must cover hours
# 0-23 with no gaps and no overlaps. Override via config 'price_blocks'.
DEFAULT_PRICE_BLOCKS = {
    'overnight':    [0, 1, 2, 3, 4, 5, 6],
    'morning_peak': [7, 8, 9],
    'midday':       [10, 11, 12, 13, 14],
    'afternoon':    [15, 16],
    'evening_peak': [17, 18, 19],
    'late':         [20, 21, 22, 23],
}


def pct_to_beta(pct: float) -> float:
    """User's '% volume shift per +10% price' -> beta (log-demand per unit price_dev).

    A +10% price (price_dev=0.10) should multiply demand by (1 - pct/100), so
        beta = ln(1 - pct/100) / 0.10
    pct is clipped just below 100 (a 100% drop would be ln(0), undefined).
    """
    frac = 1.0 - float(pct) / 100.0
    frac = min(max(frac, 1e-6), 1e6)   # keep the log well-defined
    return math.log(frac) / 0.10


def beta_to_pct(beta: float) -> float:
    """Inverse of pct_to_beta: beta -> '% volume shift per +10% price'."""
    return 100.0 * (1.0 - math.exp(float(beta) * 0.10))


def validate_price_blocks(blocks: dict) -> dict:
    """Ensure the time-blocks partition hours 0-23 exactly (no gaps/overlaps).

    Returns the blocks with hours coerced to plain ints. Raises ValueError on a
    duplicate hour, an out-of-range hour, or a missing hour.
    """
    seen = {}
    clean = {}
    for name, hours in blocks.items():
        clean[name] = [int(h) for h in hours]
        for h in clean[name]:
            if not 0 <= h <= 23:
                raise ValueError(f"price_blocks: hour {h} in '{name}' is outside 0-23")
            if h in seen:
                raise ValueError(f"price_blocks: hour {h} is in both '{seen[h]}' and '{name}'")
            seen[h] = name
    missing = [h for h in range(24) if h not in seen]
    if missing:
        raise ValueError(f"price_blocks: hours not covered by any block: {missing}")
    return clean


def cost_floor_ct(spot_ct, grid_arbeitspreis_ct, concession_ct, taxes_levies_ct=0.0):
    """Break-even price per kWh (no margin): spot + grid + concession + taxes.

    Pure arithmetic, no learning. On negative-spot slots the floor never drops
    below the fixed grid+concession+taxes cost the operator still pays, i.e.
        floor = (grid + concession + taxes) + max(spot, 0)
    Accepts a scalar or an array of spot prices; returns the same shape.
    """
    fixed = float(grid_arbeitspreis_ct) + float(concession_ct) + float(taxes_levies_ct)
    return fixed + np.clip(np.asarray(spot_ct, float), 0.0, None)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive the time features of the unified table.

    df needs: hourstamp (datetime), spot_ct, and (optionally) target_kwh.
    charged_price_ct, when present, is carried through untouched as a first-class
    column of the unified table; it is consumed by the price-effect part.
    """
    df = df.copy()
    ts = pd.to_datetime(df['hourstamp'])
    df['hour'] = ts.dt.hour
    df['minute'] = ts.dt.minute
    df['slot'] = (ts.dt.hour * 60 + ts.dt.minute) // INTERVAL_MIN     # 0..95
    df['dayofweek'] = ts.dt.dayofweek
    df['month'] = ts.dt.month
    df['is_weekend'] = (ts.dt.dayofweek >= 5).astype(int)
    df['dayofyear'] = ts.dt.dayofyear
    df['trend'] = (ts - ts.min()).dt.total_seconds() / 3600.0
    df['slot_sin'] = np.sin(2 * np.pi * df['slot'] / SLOTS_PER_DAY)
    df['slot_cos'] = np.cos(2 * np.pi * df['slot'] / SLOTS_PER_DAY)
    return df


class DemandShapeModel:
    """Predicts price-neutral demand per hour (the demand SHAPE).

    Starts as an hour x weekday profile (robust on little data), upgrades to a
    gradient-boosting model automatically once enough days have accumulated and
    the GBM actually beats the profile on a holdout.
    """

    def __init__(self):
        self.kind = 'profile'          # 'profile' or 'gbm'
        self.profile = None            # dict[(dow, slot)] -> mean kwh
        self.global_slot = None        # dict[slot] -> mean kwh (fallback)
        self.gbm = None
        self.n_days = 0

    def _fit_profile(self, df):
        g = df.groupby(['dayofweek', 'slot'])['target_kwh'].mean()
        self.profile = {k: float(v) for k, v in g.items()}
        gs = df.groupby('slot')['target_kwh'].mean()
        self.global_slot = {int(k): float(v) for k, v in gs.items()}

    def _profile_predict(self, df):
        out = []
        for _, r in df.iterrows():
            key = (int(r['dayofweek']), int(r['slot']))
            if key in self.profile:
                out.append(self.profile[key])
            else:
                out.append(self.global_slot.get(int(r['slot']), 0.0))
        return np.array(out)

    def fit(self, df: pd.DataFrame):
        """df: hourly rows with features + target_kwh."""
        df = df.dropna(subset=['target_kwh'])
        self.n_days = df['hourstamp'].dt.normalize().nunique() if 'hourstamp' in df else len(df)//SLOTS_PER_DAY
        self._fit_profile(df)

        if self.n_days >= GBM_MIN_DAYS and len(df) > 500:
            self._try_gbm(df)
        else:
            self.kind = 'profile'
        return self

    def _try_gbm(self, df):
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.metrics import mean_absolute_error
        d = df.sort_values('hourstamp')
        sp = int(len(d) * GBM_TRAIN_FRACTION)
        tr, te = d.iloc[:sp], d.iloc[sp:]
        if len(te) < 50:
            self.kind = 'profile'; return
        gbm = HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.05, max_depth=6,
            l2_regularization=1.0, random_state=RANDOM_SEED)
        gbm.fit(tr[SHAPE_FEATURES], tr['target_kwh'])
        gbm_mae = mean_absolute_error(te['target_kwh'], np.clip(gbm.predict(te[SHAPE_FEATURES]), 0, None))
        # profile holdout
        prof_pred = self._profile_predict(te)
        prof_mae = mean_absolute_error(te['target_kwh'], prof_pred)
        # only adopt GBM if it genuinely beats the profile
        if gbm_mae < prof_mae * GBM_IMPROVEMENT_RATIO:
            gbm.fit(d[SHAPE_FEATURES], d['target_kwh'])  # refit on all data
            self.gbm = gbm
            self.kind = 'gbm'
        else:
            self.kind = 'profile'

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.kind == 'gbm' and self.gbm is not None:
            return np.clip(self.gbm.predict(df[SHAPE_FEATURES]), 0, None)
        return self._profile_predict(df)


# Back-compat alias (v0.1 name). The demand-shape model is unchanged in behaviour.
BaselineModel = DemandShapeModel


# NOTE: v0.1's ElasticityLayer (assumed-%-then-flip frequentist gate) has been
# replaced by the Bayesian PriceEffect below, per BUILD_PLAN_v2.md.


class BlockBeta:
    """Conjugate Normal-Normal price coefficient for ONE time block.

    Models log(demand multiplier) as a linear function of relative price
    deviation, through the origin (price_dev=0 -> multiplier 1 -> log 0):

        log(multiplier) = beta * price_dev + noise,   noise ~ Normal(0, sigma^2)

    beta has a Normal prior (m0, s0^2). update() recomputes the posterior from
    that ORIGINAL prior + all supplied data (retrain-from-scratch). With no
    price signal the posterior stays at the prior -- the day-one fix, per block.
    """

    def __init__(self, m0: float, s0: float, sigma2: float = SIGMA2_DEFAULT):
        self.m0 = float(m0)
        self.s0 = float(s0)
        self.sigma2 = float(sigma2)          # noise var (estimated once data allows)
        self.mean = self.m0                  # posterior starts at the prior
        self.var = self.s0 ** 2
        self.n_points = 0                    # varied-price points informing beta
        self.varied_days = 0                 # distinct days that supplied signal

    def update(self, price_dev, y):
        """Recompute the posterior from the original prior + all supplied data.

        price_dev, y are arrays (y = log multiplier). Only rows with
        |price_dev| > PRICE_DEV_EPS carry signal; the rest are dropped.
        """
        price_dev = np.asarray(price_dev, float)
        y = np.asarray(y, float)
        mask = np.abs(price_dev) > PRICE_DEV_EPS
        x, yy = price_dev[mask], y[mask]
        self.n_points = int(mask.sum())
        if self.n_points == 0:
            self.mean, self.var = self.m0, self.s0 ** 2   # no signal: posterior == prior
            return self
        sxx = float(np.sum(x * x))
        if self.n_points >= SIGMA2_MIN_POINTS and sxx > 0:
            beta_ols = float(np.sum(x * yy) / sxx)
            resid = yy - beta_ols * x
            if len(resid) > 1:
                self.sigma2 = max(float(np.var(resid, ddof=1)), 1e-6)
        precision = 1.0 / self.s0 ** 2 + sxx / self.sigma2
        mean = (self.m0 / self.s0 ** 2 + float(np.sum(x * yy)) / self.sigma2) / precision
        self.mean = float(mean)
        self.var = float(1.0 / precision)
        return self

    @property
    def beta(self) -> float:
        return self.mean

    @property
    def beta_sd(self) -> float:
        return math.sqrt(self.var)

    def beta_ci(self, z: float = 1.96):
        sd = self.beta_sd
        return (self.mean - z * sd, self.mean + z * sd)

    def pct(self) -> float:
        """Posterior-mean elasticity on the user's '% per +10% price' scale."""
        return beta_to_pct(self.mean)

    def pct_ci(self, z: float = 1.96):
        """Elasticity credible interval, low-to-high on the % scale.

        beta_to_pct is decreasing in beta, so the more-negative beta bound maps
        to the higher %.
        """
        lo_beta, hi_beta = self.beta_ci(z)
        return (beta_to_pct(hi_beta), beta_to_pct(lo_beta))

    def expected_multiplier(self, price_dev):
        return np.exp(self.mean * np.asarray(price_dev, float))

    def sample_beta(self, rng=None, size=None):
        """Draw beta from its posterior (Thompson-sampling for the recommender)."""
        rng = rng if rng is not None else np.random.default_rng()
        return rng.normal(self.mean, self.beta_sd, size=size)


class PriceEffect:
    """The PRICE EFFECT part of the v2 model: one BlockBeta per time block.

    A single global beta cannot say "flexible at midday, rigid at the commuter
    peak". So price sensitivity is grouped by time-of-day block, each block a
    contiguous set of hours with its own conjugate coefficient. Every block is
    seeded from the SAME shared prior (the user's guess + confidence) and then
    sharpens independently as that block sees varied prices. A block with no
    varied-price data keeps its prior.
    """

    def __init__(self, prior_pct: float = 50.0, prior_confidence: str = 'medium',
                 ref_price: float = 59.0, blocks: dict = None,
                 sigma2: float = SIGMA2_DEFAULT):
        self.prior_pct = float(prior_pct)
        self.prior_confidence = prior_confidence
        self.ref_price = float(ref_price)
        self.blocks = validate_price_blocks(blocks or DEFAULT_PRICE_BLOCKS)
        m0 = pct_to_beta(prior_pct)
        s0 = CONFIDENCE_S0.get(prior_confidence, CONFIDENCE_S0['medium'])
        self.coeffs = {name: BlockBeta(m0, s0, sigma2) for name in self.blocks}
        # hour -> block name (single source of truth for the lookup)
        self.hour_block = {h: name for name, hours in self.blocks.items() for h in hours}

    # ---- lookup ----
    def block_of(self, hour) -> str:
        return self.hour_block[int(hour)]

    def coeff_of(self, hour) -> BlockBeta:
        return self.coeffs[self.block_of(hour)]

    # ---- fit ----
    def fit_from_history(self, history: pd.DataFrame):
        """history needs: hourstamp, charged_price_ct, target_kwh, shape_pred.

        For each block, gathers only that block's rows and runs its conjugate
        update; blocks with no varied-price rows stay at the prior.
        """
        if history is None or len(history) == 0:
            for c in self.coeffs.values():
                c.update([], [])
            return self
        h = history.dropna(subset=['charged_price_ct', 'target_kwh', 'shape_pred']).copy()
        h = h[h['shape_pred'] > 1.0]
        if len(h) == 0:
            for c in self.coeffs.values():
                c.update([], [])
            return self
        ts = pd.to_datetime(h['hourstamp'])
        h = h.assign(
            _hour=ts.dt.hour.values,
            _day=ts.dt.normalize().values,
            _price_dev=(h['charged_price_ct'].values - self.ref_price) / self.ref_price,
            _y=np.log(np.clip(h['target_kwh'].values / h['shape_pred'].values, 1e-3, 50.0)),
        )
        h['_block'] = h['_hour'].map(self.hour_block)
        for name, coeff in self.coeffs.items():
            rows = h[h['_block'] == name]
            coeff.update(rows['_price_dev'].values, rows['_y'].values)
            varied = rows[np.abs(rows['_price_dev'].values) > PRICE_DEV_EPS]
            coeff.varied_days = int(varied['_day'].nunique())
        return self

    # ---- per-hour resolution ----
    def beta_for_hours(self, hours) -> np.ndarray:
        return np.array([self.coeff_of(h).mean for h in np.asarray(hours)], float)

    def beta_ci_for_hours(self, hours, z: float = 1.96):
        ci = [self.coeff_of(h).beta_ci(z) for h in np.asarray(hours)]
        return np.array([c[0] for c in ci], float), np.array([c[1] for c in ci], float)

    def expected_multiplier(self, hours, price_dev) -> np.ndarray:
        return np.exp(self.beta_for_hours(hours) * np.asarray(price_dev, float))

    def sample_beta_for_hours(self, hours, rng=None) -> np.ndarray:
        """One posterior draw per block (Thompson), mapped onto each hour."""
        rng = rng if rng is not None else np.random.default_rng()
        draw = {name: float(c.sample_beta(rng=rng)) for name, c in self.coeffs.items()}
        return np.array([draw[self.block_of(h)] for h in np.asarray(hours)], float)

    # ---- reporting ----
    @property
    def varied_days(self) -> int:
        """Max varied-price days across blocks (distinct days with any signal)."""
        return max((c.varied_days for c in self.coeffs.values()), default=0)

    @property
    def n_points(self) -> int:
        return sum(c.n_points for c in self.coeffs.values())

    def block_summary(self) -> dict:
        """name -> {hours, beta, beta_ci, pct, pct_ci, varied_days, n_points}."""
        out = {}
        for name, c in self.coeffs.items():
            out[name] = {
                'hours': self.blocks[name],
                'beta': round(c.beta, 3),
                'beta_ci': tuple(round(x, 3) for x in c.beta_ci()),
                'pct': round(c.pct(), 1),
                'pct_ci': tuple(round(x, 1) for x in c.pct_ci()),
                'varied_days': c.varied_days,
                'n_points': c.n_points,
            }
        return out


class UnifiedForecaster:
    """Combines the demand SHAPE and the grouped PRICE EFFECT into a forecast.

        forecast_kwh(hour, price) = shape_prediction(hour) * exp(beta_block(hour) * price_dev)

    The per-hour beta comes from the hour's time block. Reports a central
    forecast (posterior-mean betas) plus a credible interval from each block's
    posterior bounds. Because price_dev can be either sign, the per-hour kwh
    bounds are the min/max of the two beta-bound multipliers, so the interval is
    correct hour by hour and collapses to the shape when price == ref.
    """

    def __init__(self, shape: 'DemandShapeModel', price: PriceEffect):
        self.shape = shape
        self.price = price

    @property
    def ref_price(self) -> float:
        return self.price.ref_price

    def forecast(self, frame: pd.DataFrame, prices, z: float = 1.96) -> dict:
        """frame: rows with hourstamp + spot_ct (features added if missing).
        prices: charged_price_ct per row. Returns a dict of per-hour arrays.
        """
        if not set(SHAPE_FEATURES).issubset(frame.columns):
            frame = add_features(frame)
        shape_kwh = np.asarray(self.shape.predict(frame), float)
        prices = np.asarray(prices, float)
        price_dev = (prices - self.ref_price) / self.ref_price
        hours = np.asarray(frame['hour'].values)

        mult = self.price.expected_multiplier(hours, price_dev)
        lo_beta, hi_beta = self.price.beta_ci_for_hours(hours, z)
        mult_a = np.exp(lo_beta * price_dev)
        mult_b = np.exp(hi_beta * price_dev)
        mult_lo = np.minimum(mult_a, mult_b)
        mult_hi = np.maximum(mult_a, mult_b)

        return {
            'shape_kwh': shape_kwh,
            'price_dev': price_dev,
            'multiplier': mult,
            'kwh': shape_kwh * mult,
            'kwh_lower': shape_kwh * mult_lo,
            'kwh_upper': shape_kwh * mult_hi,
        }
