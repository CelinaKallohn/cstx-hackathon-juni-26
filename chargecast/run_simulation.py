#!/usr/bin/env python
"""Replay the simulated days through ChargeCast end to end.

What it does:
  1. delete the state directory (start clean)
  2. seed from the combined historic 15-minute CSV
  3. predict (recommend) the first new day
  4. then, consecutively for each simulated day: ingest that day's actuals
     (scores the forecast, retrains) and recommend the following day

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


def run(*args):
    """Invoke a chargecast CLI subcommand, echoing it first."""
    print('\n' + '=' * 70)
    print('$ chargecast ' + ' '.join(args))
    print('=' * 70)
    subprocess.run([sys.executable, '-m', 'chargecast.cli', *args],
                   check=True, cwd=HERE)


def next_day(date_str: str) -> str:
    return (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')


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
    print('\nDone. Per-day plans are in', os.path.join(STATE, 'plan_<date>.csv'))


if __name__ == '__main__':
    main()
