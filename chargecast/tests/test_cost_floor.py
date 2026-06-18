"""Step 4: cost floor (break-even price, no margin)."""
import numpy as np
import pytest

from chargecast.core import cost_floor_ct

GRID = 8.24
CONC = 0.11
FIXED = GRID + CONC


def test_positive_spot_is_spot_plus_fixed():
    assert float(cost_floor_ct(20.0, GRID, CONC)) == pytest.approx(20.0 + FIXED)


def test_zero_spot_equals_fixed():
    assert float(cost_floor_ct(0.0, GRID, CONC)) == pytest.approx(FIXED)


def test_negative_spot_clamped_to_fixed():
    # negative spot must not drag the floor below the fixed grid+concession cost
    assert float(cost_floor_ct(-15.0, GRID, CONC)) == pytest.approx(FIXED)


def test_array_input_and_never_below_fixed():
    spot = np.array([-30.0, -0.001, 0.0, 5.0, 50.0])
    floor = cost_floor_ct(spot, GRID, CONC)
    assert floor.shape == spot.shape
    assert np.all(floor >= FIXED - 1e-9)
    # only the non-negative spot hours add to the fixed cost
    assert np.allclose(floor, FIXED + np.clip(spot, 0, None))
