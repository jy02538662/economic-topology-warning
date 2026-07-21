"""Phase-dynamics diagnostics for topology risk monitoring.

The functions in this module are intentionally standard-library only. They add a
lightweight phase/synchrony layer on top of the existing rolling return windows
without replacing the original topology warning rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


NumberRow = dict[str, float]


@dataclass(frozen=True)
class PhaseDiagnostics:
    avg_phase: float
    phase_sync: float
    topo_potential: float
    top_defect_nodes: list[tuple[str, float]]
    top_risk_links: list[tuple[str, str, float]]


def phase_diagnostics(
    window: Sequence[NumberRow],
    symbols: Sequence[str],
    beta1: int,
    redundancy_weight: float = 0.3,
    coupling_strength: float = 0.1,
) -> PhaseDiagnostics:
    if not window or not symbols:
        return PhaseDiagnostics(0.0, 0.0, 0.0, [], [])

    corr = _correlation_matrix(window, symbols)
    weights = _positive_abs_weights(corr)
    phases = _initial_phases_from_drawdowns(window, symbols)
    phases = _coupled_phase_step(phases, weights, coupling_strength)
    avg_phase = sum(phases) / len(phases)
    phase_sync = _phase_sync(phases)
    coupling_energy = _coupling_energy(phases, weights)
    lambda_term = abs(coupling_energy) * redundancy_weight
    topo_potential = coupling_energy + lambda_term / max(1, beta1)
    centrality = _betweenness_centrality(_threshold_edges(weights), len(symbols))
    defects = _defect_scores(symbols, phases, weights, centrality)
    links = _risk_links(symbols, phases, weights)

    return PhaseDiagnostics(
        avg_phase=avg_phase,
        phase_sync=phase_sync,
        topo_potential=topo_potential,
        top_defect_nodes=defects[:5],
        top_risk_links=links[:3],
    )


def _initial_phases_from_drawdowns(window: Sequence[NumberRow], symbols: Sequence[str]) -> list[float]:
    phases: list[float] = []
    for symbol in symbols:
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for row in window:
            equity *= 1.0 + float(row[symbol])
            peak = max(peak, equity)
            drawdown = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)
            max_drawdown = max(max_drawdown, drawdown)
        phases.append(math.pi * max_drawdown)
    max_phase = max(phases) or 1.0
    return [math.pi * phase / max_phase for phase in phases]


def _coupled_phase_step(phases: list[float], weights: list[list[float]], coupling_strength: float) -> list[float]:
    updated: list[float] = []
    for i, phase in enumerate(phases):
        row_sum = sum(weights[i]) or 1.0
        drift = sum((weights[i][j] / row_sum) * math.sin(phases[j] - phase) for j in range(len(phases)))
        updated.append(_wrap_phase(phase + coupling_strength * drift))
    return updated


def _phase_sync(phases: Sequence[float]) -> float:
    if not phases:
        return 0.0
    real = sum(math.cos(phase) for phase in phases)
    imag = sum(math.sin(phase) for phase in phases)
    return math.sqrt(real * real + imag * imag) / len(phases)


def _coupling_energy(phases: Sequence[float], weights: list[list[float]]) -> float:
    total = 0.0
    for i in range(len(phases)):
        for j in range(i + 1, len(phases)):
            total += weights[i][j] * math.cos(phases[i] - phases[j])
    return -total


def _defect_scores(
    symbols: Sequence[str],
    phases: Sequence[float],
    weights: list[list[float]],
    centrality: Sequence[float],
) -> list[tuple[str, float]]:
    scores: list[tuple[str, float]] = []
    for i, symbol in enumerate(symbols):
        row_sum = sum(weights[i]) or 1.0
        gradient = abs(sum(weights[i][j] * math.sin(phases[j] - phases[i]) for j in range(len(symbols))) / row_sum)
        scores.append((symbol, gradient * (1.0 + centrality[i])))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def _risk_links(symbols: Sequence[str], phases: Sequence[float], weights: list[list[float]]) -> list[tuple[str, str, float]]:
    links: list[tuple[str, str, float]] = []
    for i, left in enumerate(symbols):
        for j, right in enumerate(symbols[i + 1:], start=i + 1):
            phase_gap = abs(math.atan2(math.sin(phases[i] - phases[j]), math.cos(phases[i] - phases[j])))
            score = weights[i][j] * (1.0 - min(1.0, phase_gap / math.pi))
            if score > 0:
                links.append((left, right, score))
    return sorted(links, key=lambda item: item[2], reverse=True)


def _correlation_matrix(window: Sequence[NumberRow], symbols: Sequence[str]) -> list[list[float]]:
    values = [[float(row[symbol]) for row in window] for symbol in symbols]
    n = len(symbols)
    matrix = [[0.0 for _ in range(n)] for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            c = _correlation(values[i], values[j])
            matrix[i][j] = c
            matrix[j][i] = c
    return matrix


def _positive_abs_weights(corr: list[list[float]]) -> list[list[float]]:
    return [[0.0 if i == j else max(0.0, abs(value)) for j, value in enumerate(row)] for i, row in enumerate(corr)]


def _threshold_edges(weights: list[list[float]]) -> list[tuple[int, int]]:
    raw = [weights[i][j] for i in range(len(weights)) for j in range(i + 1, len(weights)) if weights[i][j] > 0]
    if not raw:
        return []
    threshold = sorted(raw)[max(0, int(0.75 * (len(raw) - 1)))]
    return [(i, j) for i in range(len(weights)) for j in range(i + 1, len(weights)) if weights[i][j] >= threshold]


def _betweenness_centrality(edges: list[tuple[int, int]], n: int) -> list[float]:
    neighbors = [[] for _ in range(n)]
    for left, right in edges:
        neighbors[left].append(right)
        neighbors[right].append(left)
    scores = [0.0] * n
    for source in range(n):
        stack: list[int] = []
        predecessors = [[] for _ in range(n)]
        sigma = [0.0] * n
        sigma[source] = 1.0
        distance = [-1] * n
        distance[source] = 0
        queue = [source]
        for vertex in queue:
            stack.append(vertex)
            for neighbor in neighbors[vertex]:
                if distance[neighbor] < 0:
                    queue.append(neighbor)
                    distance[neighbor] = distance[vertex] + 1
                if distance[neighbor] == distance[vertex] + 1:
                    sigma[neighbor] += sigma[vertex]
                    predecessors[neighbor].append(vertex)
        dependency = [0.0] * n
        while stack:
            vertex = stack.pop()
            for pred in predecessors[vertex]:
                if sigma[vertex] > 0:
                    dependency[pred] += (sigma[pred] / sigma[vertex]) * (1.0 + dependency[vertex])
            if vertex != source:
                scores[vertex] += dependency[vertex]
    normalizer = (n - 1) * (n - 2)
    if normalizer > 0:
        scores = [score / normalizer for score in scores]
    return scores


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def _wrap_phase(value: float) -> float:
    while value < 0:
        value += 2 * math.pi
    while value > math.pi:
        value -= math.pi
    return value
