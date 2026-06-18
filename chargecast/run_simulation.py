#!/usr/bin/env python
"""Replay the simulated days through ChargeCast end to end.

What it does:
  1. delete the state directory (start clean)
  2. seed from the combined historic 15-minute CSV
  3. predict (recommend) the first new day
  4. then, consecutively for each simulated day: ingest that day's actuals
     (scores the forecast, retrains) and recommend the following day
  5. copy the outputs into the dashboard's public/ tree so they display

Each step calls the real `chargecast` CLI, so the output mirrors a live
operator's daily loop. Plans land in state/plan_<date>.csv.

Run:  python run_simulation.py        (from the package directory)
"""
from __future__ import annotations
import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state')
DATA = os.path.join(HERE, '..', 'data',
                    'collected_and_cleaned', 'collected_cleaned_data.csv')
SIMULATED_DIR = os.path.join(HERE, 'simulated_data')
DASHBOARD_PUBLIC = os.path.join(HERE, '..', 'dashboard', 'hackathon-energy', 'public')


def run(*args):
    """Invoke a chargecast CLI subcommand, echoing it first."""
    print('\n' + '=' * 70)
    print('$ chargecast ' + ' '.join(args))
    print('=' * 70)
    subprocess.run([sys.executable, '-m', 'chargecast.cli', *args],
                   check=True, cwd=HERE)


def next_day(date_str: str) -> str:
    return (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')


def sync_to_dashboard():
    """Copy the run's outputs into the dashboard's public/ tree.

    The Angular app serves public/ at the web root and fetches files by name:
    /collected_cleaned_data.csv, /prediction/plan_<date>.csv, and
    /simulation/simulated_data_<date>.csv. Filenames are kept exactly as produced.
    Skipped silently if the dashboard isn't checked out next to this package.
    """
    if not os.path.isdir(DASHBOARD_PUBLIC):
        print('\n(dashboard not found at', DASHBOARD_PUBLIC, '- skipping sync)')
        return

    print('\n' + '=' * 70)
    print('Syncing outputs to dashboard:', DASHBOARD_PUBLIC)
    print('=' * 70)

    prediction_dir = os.path.join(DASHBOARD_PUBLIC, 'prediction')
    simulation_dir = os.path.join(DASHBOARD_PUBLIC, 'simulation')
    os.makedirs(prediction_dir, exist_ok=True)
    os.makedirs(simulation_dir, exist_ok=True)

    plans = glob.glob(os.path.join(STATE, 'plan_*.csv'))
    for path in plans:
        shutil.copy2(path, prediction_dir)
    sims = glob.glob(os.path.join(SIMULATED_DIR, 'simulated_data_*.csv'))
    for path in sims:
        shutil.copy2(path, simulation_dir)
    if os.path.exists(DATA):
        shutil.copy2(DATA, os.path.join(DASHBOARD_PUBLIC, 'collected_cleaned_data.csv'))

    print(f'  prediction/: {len(plans)} plan files')
    print(f'  simulation/: {len(sims)} simulated-day files')
    print('  collected_cleaned_data.csv: historic data')


def main():
    # 1. fresh state
    if os.path.exists(STATE):
        shutil.rmtree(STATE)

    # 2. seed from the historic data
    run('seed', '--state', STATE, '--data', DATA)

    # discover the simulated days (date parsed from each filename), sorted
    files = {}
    for path in glob.glob(os.path.join(SIMULATED_DIR, '*.csv')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path))
        if m:
            files[m.group(1)] = path
    dates = sorted(files)
    if not dates:
        sys.exit(f'no dated simulated CSVs found in {SIMULATED_DIR}')

    # 3. predict the first new day (before any of its actuals are known)
    run('recommend', '--state', STATE, '--date', dates[0])

    # 4. consecutively: ingest each day's actuals, then recommend the next day
    for d in dates:
        run('ingest', '--state', STATE, '--actuals', files[d])
        run('recommend', '--state', STATE, '--date', next_day(d))

    # final state of the learner
    run('status', '--state', STATE)

    # make the outputs visible to the dashboard
    sync_to_dashboard()

    print('\nDone. Per-day plans are in', os.path.join(STATE, 'plan_<date>.csv'))


if __name__ == '__main__':
    main()
