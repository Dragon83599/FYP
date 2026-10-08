"""Small NumPy models used for the first CPU baseline."""

from __future__ import annotations

import numpy as np


class StandardScaler:
    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, x: np.ndarray) -> "StandardScaler":
        self.mean_ = x.mean(axis=0)
        scale = x.std(axis=0)
        self.scale_ = np.where(scale < 1e-6, 1.0, scale)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("scaler is not fitted")
        return (x - self.mean_) / self.scale_

    def fit_transform(self, x: np.ndarray) -> np.ndarray:
        return self.fit(x).transform(x)


class LogisticRegression:
    """L2 logistic regression trained with Adam and optional class weights."""

    def __init__(
        self,
        epochs: int = 500,
        learning_rate: float = 0.05,
        l2: float = 1e-3,
        balanced: bool = True,
        seed: int = 20261007,
    ) -> None:
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.l2 = l2
        self.balanced = balanced
        self.seed = seed
        self.weights_: np.ndarray | None = None
        self.bias_: float = 0.0
        self.loss_history_: list[float] = []

    def fit(self, x: np.ndarray, y: np.ndarray) -> "LogisticRegression":
        rng = np.random.default_rng(self.seed)
        n_samples, n_features = x.shape
        weights = np.zeros(n_features, dtype=np.float64)
        bias = 0.0
        m_w = np.zeros_like(weights)
        v_w = np.zeros_like(weights)
        m_b = 0.0
        v_b = 0.0

        if self.balanced:
            positives = max(int(np.sum(y == 1)), 1)
            negatives = max(int(np.sum(y == 0)), 1)
            sample_weights = np.where(y == 1, n_samples / (2.0 * positives), n_samples / (2.0 * negatives))
            sample_weights /= sample_weights.mean()
        else:
            sample_weights = np.ones(n_samples, dtype=np.float64)

        for step in range(1, self.epochs + 1):
            logits = x @ weights + bias
            probabilities = sigmoid(logits)
            error = (probabilities - y) * sample_weights
            grad_w = x.T @ error / n_samples + self.l2 * weights
            grad_b = float(np.mean(error))

            m_w = 0.9 * m_w + 0.1 * grad_w
            v_w = 0.999 * v_w + 0.001 * grad_w**2
            m_b = 0.9 * m_b + 0.1 * grad_b
            v_b = 0.999 * v_b + 0.001 * grad_b**2
            m_w_hat = m_w / (1 - 0.9**step)
            v_w_hat = v_w / (1 - 0.999**step)
            m_b_hat = m_b / (1 - 0.9**step)
            v_b_hat = v_b / (1 - 0.999**step)
            weights -= self.learning_rate * m_w_hat / (np.sqrt(v_w_hat) + 1e-8)
            bias -= self.learning_rate * m_b_hat / (np.sqrt(v_b_hat) + 1e-8)

            if step % 25 == 0 or step == 1:
                loss = binary_cross_entropy(y, probabilities, sample_weights, self.l2, weights)
                self.loss_history_.append(loss)

        self.weights_ = weights
        self.bias_ = float(bias)
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self.weights_ is None:
            raise RuntimeError("model is not fitted")
        return sigmoid(x @ self.weights_ + self.bias_)

    def predict(self, x: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        return (self.predict_proba(x) >= threshold).astype(int)


def sigmoid(value: np.ndarray | float) -> np.ndarray | float:
    clipped = np.clip(value, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def binary_cross_entropy(
    y: np.ndarray,
    probability: np.ndarray,
    sample_weights: np.ndarray,
    l2: float,
    weights: np.ndarray,
) -> float:
    probability = np.clip(probability, 1e-9, 1 - 1e-9)
    data_loss = -np.mean(
        sample_weights * (y * np.log(probability) + (1 - y) * np.log(1 - probability))
    )
    return float(data_loss + 0.5 * l2 * np.sum(weights**2))
