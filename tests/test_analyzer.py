from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econ_topology import EconomicTopoAnalyzer, EconomicTopoConfig
from demo import FEATURES, synthetic_economic_rows


def test_analyzer_detects_states() -> None:
    analyzer = EconomicTopoAnalyzer(
        FEATURES,
        EconomicTopoConfig(window_size=12, edge_quantile=0.30),
    )
    states = analyzer.analyze(synthetic_economic_rows())
    assert states
    assert all(state.beta0 >= 1 for state in states)
    assert all(state.beta1 >= 0 for state in states)
    assert all(state.s_topo >= 0 for state in states)
    assert states[-1].alert_level in {"green", "yellow", "orange", "red"}
    assert states[-1].drivers


def test_invalid_rows_are_rejected() -> None:
    analyzer = EconomicTopoAnalyzer(["gdp"], EconomicTopoConfig(window_size=4))
    try:
        analyzer.analyze([{"gdp": 1.0}, {"gdp": 2.0}, {"bad": 3.0}, {"gdp": 4.0}])
    except ValueError:
        return
    assert False, "missing feature should raise ValueError"


def test_config_rejects_too_small_window() -> None:
    try:
        EconomicTopoConfig(window_size=3)
    except ValueError:
        return
    assert False, "window_size < 4 should raise ValueError"


if __name__ == "__main__":
    test_analyzer_detects_states()
    test_invalid_rows_are_rejected()
    test_config_rejects_too_small_window()
    print("all tests passed")
