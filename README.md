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

### 1. Backend (Python)

Work inside a virtual environment so the install stays isolated from your system
Python:

```bash
cd chargecast
python3 -m venv .venv          # create the venv (once)
source .venv/bin/activate      # activate it (Windows: .venv\Scripts\activate)
pip install -e .               # install chargecast into the venv
python run_simulation.py       # replay the simulated days end to end, regenerating state/
                               # and copying outputs into the dashboard's public/ folder
```

Re-activate the venv (`source .venv/bin/activate`) in any new shell before running
the CLI. `deactivate` leaves it.

### 2. Dashboard (Angular)

```bash
cd dashboard/hackathon-energy
npm install                    # install dependencies (once)
ng serve                       # start the dev server at http://localhost:4200
```

`ng serve` needs the **Angular CLI**. If `ng` isn't found, install it globally
once with `npm install -g @angular/cli` (see the
[Angular CLI setup guide](https://angular.dev/tools/cli/setup-local)), or skip the
global install and run it via the local copy with `npm start`.

See `chargecast/README.md` for the CLI commands (`seed`, `recommend`, `ingest`, `status`)
