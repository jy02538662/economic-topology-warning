"""Rolling-window economic topology warning MVP.

This module deliberately starts with a standard-library approximation:
- Build a Vietoris-Rips 1-skeleton from normalized observations in each window.
- Estimate beta_0 as connected components.
- Estimate beta_1 as graph cycle rank E - V + C.

It is not a full persistent homology implementation. The API is shaped so a later
backend can replace the proxy with ripser/gudhi without changing the outer pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Sequence


NumberRow = dict[str, float]


@dataclass(frozen=True)
class EconomicTopoConfig:
    window_size: int = 12
    edge_quantile: float = 0.35
    beta1_drop_warn: float = 0.35
    s_topo_drop_warn: float = 0.20
    epsilon: float = 1e-9
    feature_weights: dict[str, float] = field(default_factory=dict)
    persistence_weights: dict[str, float] = field(
        default_factory=lambda: {"beta0": 1.0, "beta1": 0.7}
    )

    def __post_init__(self) -> None:
        if self.window_size < 4:
            raise ValueError("window_size must be >= 4")
        if not 0.0 < self.edge_quantile <= 1.0:
            raise ValueError("edge_quantile must be in (0, 1]")
        if self.beta1_drop_warn < 0 or self.s_topo_drop_warn < 0:
            raise ValueError("drop thresholds must be non-negative")


@dataclass(frozen=True)
class WindowTopoState:
    end_index: int
    beta0: int
    beta1: int
    s_topo: float
    dimensional_fluctuation: float
    alert_level: str
    explanation: str
    drivers: list[tuple[str, float]]


class EconomicTopoAnalyzer:
    """Analyze rolling topological state changes in economic indicator windows."""

    def __init__(self, features: Sequence[str], config: EconomicTopoConfig | None = None):
        if not features:
            raise ValueError("features must not be empty")
        self.features = list(features)
        self.config = config or EconomicTopoConfig()

    def analyze(self, rows: Sequence[NumberRow]) -> list[WindowTopoState]:
        if len(rows) < self.config.window_size:
            raise ValueError("not enough rows for one window")
        self._validate_rows(rows)

        states: list[WindowTopoState] = []
        previous: WindowTopoState | None = None
        previous_window: list[NumberRow] | None = None

        for end in range(self.config.window_size, len(rows) + 1):
            window = list(rows[end - self.config.window_size:end])
            state = self._analyze_window(end - 1, window, previous, previous_window)
            states.append(state)
            previous = state
            previous_window = window
        return states

    def _validate_rows(self, rows: Sequence[NumberRow]) -> None:
        for idx, row in enumerate(rows):
            for feature in self.features:
                if feature not in row:
                    raise ValueError(f"row {idx} missing feature {feature!r}")
                value = row[feature]
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"row {idx} feature {feature!r} must be finite number")

    def _analyze_window(
        self,
        end_index: int,
        window: list[NumberRow],
        previous: WindowTopoState | None,
        previous_window: list[NumberRow] | None,
    ) -> WindowTopoState:
        points = self._zscore_points(window)
        distances = _pairwise_distances(points)
        threshold = _positive_quantile(distances, self.config.edge_quantile)
        edges = _edges_under_threshold(distances, threshold)
        beta0 = _connected_components(len(points), edges)
        beta1 = max(0, len(edges) - len(points) + beta0)

        dim_fluct = self._dimensional_fluctuation(window, previous_window)
        s_topo = self._s_topo(beta0, beta1, dim_fluct)
        alert_level, explanation = self._classify(beta0, beta1, s_topo, previous)
        drivers = self._drivers(window, previous_window)

        return WindowTopoState(
            end_index=end_index,
            beta0=beta0,
            beta1=beta1,
            s_topo=s_topo,
            dimensional_fluctuation=dim_fluct,
            alert_level=alert_level,
            explanation=explanation,
            drivers=drivers,
        )

    def _zscore_points(self, window: Sequence[NumberRow]) -> list[list[float]]:
        means: dict[str, float] = {}
        stds: dict[str, float] = {}
        for feature in self.features:
            values = [float(row[feature]) for row in window]
            mean = sum(values) / len(values)
            var = sum((x - mean) ** 2 for x in values) / len(values)
            means[feature] = mean
            stds[feature] = math.sqrt(var) or 1.0
        return [
            [(float(row[f]) - means[f]) / stds[f] for f in self.features]
            for row in window
        ]

    def _effective_dimension(self, window: Sequence[NumberRow]) -> int:
        variances = []
        for feature in self.features:
            values = [float(row[feature]) for row in window]
            mean = sum(values) / len(values)
            variances.append(sum((x - mean) ** 2 for x in values) / len(values))
        total = sum(variances)
        if total <= self.config.epsilon:
            return 1
        shares = [v / total for v in variances]
        return max(1, sum(share >= 0.08 for share in shares))

    def _dimensional_fluctuation(
        self,
        window: Sequence[NumberRow],
        previous_window: Sequence[NumberRow] | None,
    ) -> float:
        current_dim = self._effective_dimension(window)
        if previous_window is None:
            return 0.0
        previous_dim = self._effective_dimension(previous_window)
        return abs(current_dim - previous_dim) / max(1, len(self.features))

    def _s_topo(self, beta0: int, beta1: int, dim_fluct: float) -> float:
        w0 = self.config.persistence_weights.get("beta0", 1.0)
        w1 = self.config.persistence_weights.get("beta1", 0.7)
        connectivity = 1.0 / beta0
        feedback = math.log1p(beta1)
        persistence_proxy = w0 * connectivity + w1 * feedback
        return persistence_proxy / (dim_fluct + self.config.epsilon + 1.0)

    def _classify(
        self,
        beta0: int,
        beta1: int,
        s_topo: float,
        previous: WindowTopoState | None,
    ) -> tuple[str, str]:
        if beta0 > 2:
            return "red", "beta0 > 2: economic manifold splits into multiple islands"
        if beta0 == 2:
            return "orange", "beta0 = 2: structural separation detected"
        if previous is not None:
            s_drop = _relative_drop(previous.s_topo, s_topo, self.config.epsilon)
            beta1_drop = _relative_drop(float(previous.beta1), float(beta1), self.config.epsilon)
            if s_drop >= self.config.s_topo_drop_warn:
                return "red", f"S_topo dropped by {s_drop:.1%}: topology stability shock"
            if beta1_drop >= self.config.beta1_drop_warn:
                return "yellow", f"beta1 dropped by {beta1_drop:.1%}: feedback loops weakening"
        return "green", "topological state remains connected and stable"

    def _drivers(
        self,
        window: Sequence[NumberRow],
        previous_window: Sequence[NumberRow] | None,
    ) -> list[tuple[str, float]]:
        if previous_window is None:
            return []
        scores: list[tuple[str, float]] = []
        for feature in self.features:
            current = [float(row[feature]) for row in window]
            previous = [float(row[feature]) for row in previous_window]
            mean_shift = abs(_mean(current) - _mean(previous))
            vol_shift = abs(_std(current) - _std(previous))
            weight = self.config.feature_weights.get(feature, 1.0)
            scores.append((feature, weight * (mean_shift + vol_shift)))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:3]


def _pairwise_distances(points: Sequence[Sequence[float]]) -> list[tuple[int, int, float]]:
    distances: list[tuple[int, int, float]] = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            distances.append((i, j, _euclidean(points[i], points[j])))
    return distances


def _positive_quantile(distances: Sequence[tuple[int, int, float]], quantile: float) -> float:
    if not distances:
        return 0.0
    values = sorted(d for _, _, d in distances if d > 0)
    if not values:
        return 0.0
    idx = min(len(values) - 1, max(0, int(math.ceil(quantile * len(values))) - 1))
    return values[idx]


def _edges_under_threshold(
    distances: Sequence[tuple[int, int, float]], threshold: float
) -> list[tuple[int, int]]:
    return [(i, j) for i, j, d in distances if d <= threshold]


def _connected_components(n: int, edges: Iterable[tuple[int, int]]) -> int:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in edges:
        union(a, b)
    return len({find(i) for i in range(n)})


def _relative_drop(previous: float, current: float, epsilon: float) -> float:
    if previous <= epsilon:
        return 0.0
    return max(0.0, (previous - current) / previous)


def _euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
