"""Demo for the economic topology warning MVP."""
from __future__ import annotations

from econ_topology import EconomicTopoAnalyzer, EconomicTopoConfig


FEATURES = ["gdp", "inflation", "employment", "rate", "pmi", "consumption"]


def synthetic_economic_rows() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for t in range(36):
        if t < 24:
            rows.append({
                "gdp": 2.0 + 0.04 * t,
                "inflation": 2.1 + 0.02 * (t % 4),
                "employment": 95.0 + 0.05 * t,
                "rate": 3.0 + 0.01 * (t % 6),
                "pmi": 52.0 + 0.1 * (t % 5),
                "consumption": 100.0 + 0.3 * t,
            })
        else:
            rows.append({
                "gdp": 3.0 - 0.20 * (t - 24),
                "inflation": 2.4 + 0.28 * (t - 24),
                "employment": 96.0 - 0.35 * (t - 24),
                "rate": 3.4 + 0.16 * (t - 24),
                "pmi": 51.5 - 0.65 * (t - 24),
                "consumption": 108.0 - 0.75 * (t - 24),
            })
    return rows


def main() -> None:
    analyzer = EconomicTopoAnalyzer(
        FEATURES,
        EconomicTopoConfig(window_size=12, edge_quantile=0.30),
    )
    states = analyzer.analyze(synthetic_economic_rows())
    print("end\tbeta0\tbeta1\tS_topo\tlevel\tdrivers")
    for state in states[-12:]:
        drivers = ", ".join(f"{name}:{score:.2f}" for name, score in state.drivers)
        print(
            f"{state.end_index}\t{state.beta0}\t{state.beta1}\t"
            f"{state.s_topo:.3f}\t{state.alert_level}\t{drivers}"
        )


if __name__ == "__main__":
    main()
