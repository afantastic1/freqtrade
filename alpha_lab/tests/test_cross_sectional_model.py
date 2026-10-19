import numpy as np
import pandas as pd

from alpha_lab.models.cross_sectional import ridge_predict


def test_ridge_fits_scaling_only_on_training_rows():
    train = pd.DataFrame({"a": [0., 1., 2., 3.], "b": [0., 0., 1., 1.],
                          "relative_return": [-1., 0., 0., 1.]})
    test = pd.DataFrame({"a": [4., 5.], "b": [1., 1.]})
    first = ridge_predict(train, test, ("a", "b"), alpha=1.0)
    altered = test.copy()
    altered.loc[1, "a"] = 1e9
    second = ridge_predict(train, altered, ("a", "b"), alpha=1.0)
    assert np.isfinite(first).all()
    assert first.iloc[0] == second.iloc[0]
