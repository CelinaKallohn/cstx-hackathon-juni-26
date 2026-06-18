# Self-assessment

An reflection on the ChargeCast prototype built for this hackathon — what
we set out to do, how far we got, the role AI played, how we validated it, and
where it could go next.

## 1. The problem we tried to solve

EV charging hubs face two pressures at once: demand is **peaky and uneven** over
the day, and the **wholesale (spot) energy cost is volatile**. Flat pricing wastes
grid capacity during peaks and leaves cheap-energy troughs empty. We wanted a tool
that recommends a per-15-minute price for the day ahead that **smooths demand**
— flattens the load shape — while **never selling below cost**, and that
**improves itself** as real days accumulate, with no manual retuning. The same
commands should work on day 1 and on day 500.

## 2. Maturity of this prototype

Prototype-grade, and we're explicit about that. What works:

- A clean daily loop as a CLI — `seed` → `recommend` → `ingest` → `status` —
  with a learning model behind it (v2.2.0).
- 43 passing unit + end-to-end tests.
- An Angular dashboard that consumes the pipeline's CSV outputs.

What it is **not** yet:

- It is validated on **synthetic / simulated data**, not a live hub.
- Economics are **energy-only**: capacity charges (Leistungspreis) and fixed
  monthly fees are not modelled per slot.
- The demand-shape model currently wins as a **simple weekday × slot profile**;
  the gradient-boosting upgrade does not yet beat it (the day-to-day variation is
  largely unexplained by the features we have).
- **Early recommendations lean heavily on the prior** until each price block has
  seen enough varied pricing to learn its elasticity.

## 3. Why it makes sense, and how we used AI/LLM

**AI inside the product — classical ML, on purpose.** The product is built from
two cooperating learned parts: a **demand-shape model** (profile / gradient
boosting) and a **Bayesian per-block price-effect**
that learns how price moves volume at different times of day. We deliberately did
**not** put an LLM in the product. A thin-data, daily-loop pricing problem rewards
methods that are **interpretable, data-frugal, and quantify their own
uncertainty** — the Bayesian model reports a credible interval and explores precisely where it's unsure, which an LLM cannot do
responsibly here. Where an LLM *could* earn its place later: natural-language
operator summaries of the daily plan, or ingesting unstructured weather/event
text as demand features.

**How we built it with AI.** AI tooling accelerated the build:

- **Design scaffolding** — sketching and pressure-testing the modeling approach.
- **Generating synthetic test data** — the simulated charging days used to
  exercise the loop end to end.
- **Claude Code for fast code prototyping** — turning the design into working,
  tested code quickly.

## 4. How we validated / tested / simulated

- **43 automated tests** covering the model and the CLI. Highlights: the
  price-effect engine recovers known elasticities with a shrinking credible
  interval (and an un-varied block correctly keeps its prior); the recommender
  **never dips below the cost floor**, keeps total day margin **≥ 0**, and
  demonstrably **flattens demand** versus a flat-reference baseline; the demand
  shape is pinned byte-for-byte against the reference implementation; and the CLI
  flow is tested end to end, including rejection of invalid config.
- **Synthetic day generator** that produces realistic charging days.
- **`run_simulation.py`** replays a multi-day operator loop through the *real*
  CLI — seed, recommend, then ingest + recommend day after day — so the output
  mirrors what a live operator would see.

## 5. Options to continue / expand

- **v3 hierarchical price effect** — per-hour betas that borrow strength via a
  global hyper-prior, so low-data hours lean on the overall pattern. Supersedes
  fixed time blocks once months of varied data exist.
- **Better demand features** — weather, events, and utilisation are the path to
  making the gradient-boosting model actually win.
- **Fuller economics** — model capacity charges (Leistungspreis) and fixed fees
  per slot, not just energy.
- **Live-data integration** — replace the simulation with a real metering/spot
  feed and close the loop on production days.
- **Richer dashboard** and an **optional LLM layer** for operator-facing,
  natural-language explanations of each day's plan.
