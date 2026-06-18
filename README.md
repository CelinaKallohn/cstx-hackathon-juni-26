# cstx-hackathon-juni-26

Self-improving daily demand forecasting and dynamic pricing for an EV charging hub,
with a dashboard to visualise the result.

> For an honest look at the problem, the prototype's maturity, our use of AI, how
> we validated it, and what's next, see [`SELFASSESSMENT.md`](SELFASSESSMENT.md).

## Repository layout

| Path          | What it is |
|---------------|------------|
| `chargecast/` | Python package + CLI: the demand-forecast model, the Bayesian price-effect learner, and the demand-smoothing price recommender. See `chargecast/README.md`. |
| `dashboard/`  | Angular web UI (`hackathon-energy/`) that reads the pipeline's CSV outputs (prices, forecasts, simulated actuals). |
| `data/`       | The real-world source data: the cleaned 15-minute dataset (`collected_and_cleaned/collected_cleaned_data.csv`), the original `.xlsx` workbooks, and the grid-tariff (`Netzentgelte/`) documents. |

## Quick start

```bash
cd chargecast
pip install -e .
python run_simulation.py     # replays the simulated days end to end, regenerating state/
```

See `chargecast/README.md` for the CLI commands (`seed`, `recommend`, `ingest`, `status`)
