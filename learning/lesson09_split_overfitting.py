"""Lesson 09: train/validation/test split and overfitting.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson09_split_overfitting.py
"""

import warnings

import numpy as np
from numpy.polynomial import Polynomial

warnings.filterwarnings("ignore", category=np.exceptions.RankWarning)

np.random.seed(0)

# 30 points from y = 2x + 1 plus random noise.
x = np.linspace(-3.0, 3.0, 30)
y = 2.0 * x + 1.0 + np.random.normal(0.0, 1.0, size=x.shape)

# Fixed split: 15 train, 8 validation, 7 test.
train_index = np.arange(0, 15)
val_index = np.arange(15, 23)
test_index = np.arange(23, 30)


def mean_squared_error(y_true, y_pred):
    """Average squared error."""
    return float(np.mean((y_true - y_pred) ** 2))


print("train points =", len(train_index))
print("val points   =", len(val_index))
print("test points  =", len(test_index))
print()

for degree in (1, 3, 9):
    model = Polynomial.fit(x[train_index], y[train_index], degree)

    train_prediction = model(x[train_index])
    val_prediction = model(x[val_index])
    test_prediction = model(x[test_index])

    print(f"=== polynomial degree = {degree} ===")
    print(f"train MSE = {mean_squared_error(y[train_index], train_prediction):.4f}")
    print(f"val MSE   = {mean_squared_error(y[val_index], val_prediction):.4f}")
    print(f"test MSE  = {mean_squared_error(y[test_index], test_prediction):.4f}")
    print()
