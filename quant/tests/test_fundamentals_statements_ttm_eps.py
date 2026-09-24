"""TTM EPS is computed from TTM net income and average diluted shares when
SEC supplies no Q4 per-share fact."""

import numpy as np
import pandas as pd

from ohcamel_quant.fundamentals import statements as st


def test_ttm_eps_falls_back_to_income_over_average_shares():
    idx = pd.to_datetime(["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"])
    q = pd.DataFrame({"net_income": [10.0, 20.0, 30.0, 40.0],
                      "eps_diluted": [1.0, 2.0, 3.0, np.nan],
                      "shares_diluted": [10.0, 10.0, 10.0, np.nan]}, index=idx)
    row = st.ttm_at(q, idx[-1])
    assert row["shares_diluted"] == 10.0
    assert row["eps_diluted"] == 100.0 / 10.0
