"""Shared test fixtures."""
import os
import pandas as pd
import pytest

EXAMPLE_CSV = os.path.join(os.path.dirname(__file__), '..', 'examples',
                           'charging_hourly_dataset.csv')


@pytest.fixture
def seed_df():
    """The real seed dataset (one year of hourly demand) as a DataFrame.

    Mirrors what `seed` builds: a unified table with charged_price_ct fixed at
    the reference price (no price variation in history).
    """
    df = pd.read_csv(EXAMPLE_CSV, parse_dates=['hourstamp'])
    df['charged_price_ct'] = 59.0
    return df[['hourstamp', 'spot_ct', 'target_kwh', 'charged_price_ct']].copy()
