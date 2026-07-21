"""2007-2009 S&P 500 representative constituent topology backtest.

Data source: Yahoo Chart JSON endpoint, fetched with Python stdlib urllib.
This is an exploratory MVP, not investment advice.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econ_topology import EconomicTopoAnalyzer, EconomicTopoConfig


SYMBOLS = [
    "aapl", "msft", "xom", "jpm", "ge", "wmt", "pg", "jnj",
    "bac", "c", "gs", "ibm", "ko", "pep", "mcd", "hd",
]
START = date(2007, 1, 1)
END = date(2009, 12, 31)
CACHE_DIR = ROOT / "data" / "stooq_cache"


def fetch_stooq_daily(symbol: str) -> list[tuple[date, float]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}.json"
    if cache_file.exists():
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        yahoo_symbol = symbol.upper()
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
            "?period1=1167609600&period2=1262304000&interval=1d&events=history"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

    result = payload.get("chart", {}).get("result") or []
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"failed to fetch {symbol}: {error}")
    series = result[0]
    timestamps = series.get("timestamp") or []
    quote = (series.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
             or series.get("indicators", {}).get("quote", [{}])[0].get("close")
             or [])
    rows: list[tuple[date, float]] = []
    for ts, close in zip(timestamps, quote):
        if close is None:
            continue
        day = datetime.utcfromtimestamp(int(ts)).date()
        if START <= day <= END:
            rows.append((day, float(close)))
    if len(rows) < 120:
        raise ValueError(f"not enough data for {symbol}: {len(rows)} rows")
    return rows

def build_return_rows(symbols: list[str]) -> list[dict[str, float]]:
    series = {symbol: fetch_stooq_daily(symbol) for symbol in symbols}
    close_by_symbol = {
        symbol: {day: close for day, close in values}
        for symbol, values in series.items()
    }
    common_days = sorted(set.intersection(*(set(v.keys()) for v in close_by_symbol.values())))

    previous: dict[str, float] | None = None
    rows: list[dict[str, float]] = []
    for day in common_days:
        closes = {symbol: close_by_symbol[symbol][day] for symbol in symbols}
        if previous is not None:
            item = {"date": day.toordinal()}
            for symbol in symbols:
                item[symbol] = closes[symbol] / previous[symbol] - 1.0
            rows.append(item)
        previous = closes
    return rows


def write_results(states, rows: list[dict[str, float]], output: Path) -> None:
    baseline = _baseline(states, rows)
    controls = _control_metrics(states, rows)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "end_index", "beta0", "beta1", "s_topo", "raw_level",
            "calibrated_level", "beta0_z", "s_topo_drop", "avg_corr", "avg_vol",
            "drivers", "explanation",
        ])
        for state in states:
            calibrated_level, beta0_z, s_drop = _calibrated_alert(state, baseline)
            avg_corr, avg_vol = controls[state.end_index]
            drivers = ";".join(f"{name}:{score:.6f}" for name, score in state.drivers)
            writer.writerow([
                date.fromordinal(int(rows[state.end_index]["date"])).isoformat(),
                state.end_index,
                state.beta0,
                state.beta1,
                f"{state.s_topo:.6f}",
                state.alert_level,
                calibrated_level,
                f"{beta0_z:.3f}",
                f"{s_drop:.3f}",
                f"{avg_corr:.6f}",
                f"{avg_vol:.6f}",
                drivers,
                state.explanation,
            ])


def _baseline(states, rows: list[dict[str, float]]) -> dict[str, float]:
    baseline_states = [
        s for s in states
        if date.fromordinal(int(rows[s.end_index]["date"])) < date(2008, 1, 1)
    ]
    if not baseline_states:
        baseline_states = states[:120]
    beta0_values = [float(s.beta0) for s in baseline_states]
    s_values = [float(s.s_topo) for s in baseline_states]
    beta0_mean = sum(beta0_values) / len(beta0_values)
    beta0_std = _std(beta0_values) or 1.0
    s_mean = sum(s_values) / len(s_values)
    return {"beta0_mean": beta0_mean, "beta0_std": beta0_std, "s_mean": s_mean}


def _calibrated_alert(state, baseline: dict[str, float]) -> tuple[str, float, float]:
    beta0_z = (state.beta0 - baseline["beta0_mean"]) / baseline["beta0_std"]
    s_drop = max(0.0, (baseline["s_mean"] - state.s_topo) / baseline["s_mean"])
    if beta0_z >= 2.0 or s_drop >= 0.20:
        return "red", beta0_z, s_drop
    if beta0_z >= 1.25 or s_drop >= 0.12:
        return "orange", beta0_z, s_drop
    if beta0_z >= 0.75 or s_drop >= 0.08:
        return "yellow", beta0_z, s_drop
    return "green", beta0_z, s_drop


def _std(xs: list[float]) -> float:
    mean = sum(xs) / len(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def _control_metrics(states, rows: list[dict[str, float]]) -> dict[int, tuple[float, float]]:
    metrics: dict[int, tuple[float, float]] = {}
    for state in states:
        start = state.end_index - 59
        window = rows[start:state.end_index + 1]
        avg_corr = _average_pairwise_correlation(window, SYMBOLS)
        avg_vol = sum(_std([float(row[symbol]) for row in window]) for symbol in SYMBOLS) / len(SYMBOLS)
        metrics[state.end_index] = (avg_corr, avg_vol)
    return metrics


def _average_pairwise_correlation(window: list[dict[str, float]], symbols: list[str]) -> float:
    values = {symbol: [float(row[symbol]) for row in window] for symbol in symbols}
    corrs: list[float] = []
    for i, left in enumerate(symbols):
        for right in symbols[i + 1:]:
            corrs.append(_correlation(values[left], values[right]))
    return sum(corrs) / len(corrs)


def _correlation(xs: list[float], ys: list[float]) -> float:
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / ((vx * vy) ** 0.5)


def plot_results(states, rows: list[dict[str, float]], output: Path) -> bool:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return False

    controls = _control_metrics(states, rows)
    dates = [date.fromordinal(int(rows[state.end_index]["date"])) for state in states]
    beta0 = [state.beta0 for state in states]
    s_topo = [state.s_topo for state in states]
    avg_corr = [controls[state.end_index][0] for state in states]
    avg_vol = [controls[state.end_index][1] for state in states]

    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    axes[0].plot(dates, beta0, color="tab:red", label="beta0")
    axes[0].set_ylabel("beta0")
    axes[0].legend(loc="upper left")

    axes[1].plot(dates, s_topo, color="tab:blue", label="S_topo")
    axes[1].set_ylabel("S_topo")
    axes[1].legend(loc="upper left")

    axes[2].plot(dates, avg_corr, color="tab:green", label="avg pairwise corr")
    axes[2].set_ylabel("avg corr")
    axes[2].legend(loc="upper left")

    axes[3].plot(dates, avg_vol, color="tab:purple", label="avg volatility")
    axes[3].set_ylabel("avg vol")
    axes[3].legend(loc="upper left")

    for ax in axes:
        ax.axvline(date(2008, 9, 15), color="black", linestyle="--", linewidth=1)
        ax.grid(alpha=0.25)
    fig.suptitle("S&P 500 Representative Topology Backtest 2007-2009")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    return True


def main() -> None:
    rows = build_return_rows(SYMBOLS)
    analyzer = EconomicTopoAnalyzer(
        SYMBOLS,
        EconomicTopoConfig(window_size=60, edge_quantile=0.25, s_topo_drop_warn=0.20),
    )
    states = analyzer.analyze(rows)
    output = ROOT / "data" / "sp500_representative_topology_2007_2009.csv"
    chart_output = ROOT / "data" / "sp500_representative_topology_2007_2009.png"
    write_results(states, rows, output)
    plotted = plot_results(states, rows, chart_output)

    baseline = _baseline(states, rows)
    controls = _control_metrics(states, rows)
    print(f"symbols={len(SYMBOLS)} rows={len(rows)} states={len(states)}")
    print(f"baseline_beta0={baseline['beta0_mean']:.2f}±{baseline['beta0_std']:.2f} baseline_S={baseline['s_mean']:.3f}")
    print(f"wrote={output}")
    print(f"chart={chart_output if plotted else 'matplotlib not installed, skipped'}")
    print("date\tend\tbeta0\tbeta1\tS_topo\tlevel\tbeta0_z\tS_drop\tavg_corr\tavg_vol\tdrivers")
    for state in states:
        calibrated_level, beta0_z, s_drop = _calibrated_alert(state, baseline)
        if calibrated_level != "green" or state.end_index % 80 == 0:
            day = date.fromordinal(int(rows[state.end_index]["date"])).isoformat()
            avg_corr, avg_vol = controls[state.end_index]
            drivers = ", ".join(f"{name}:{score:.4f}" for name, score in state.drivers)
            print(
                f"{day}\t{state.end_index}\t{state.beta0}\t{state.beta1}\t"
                f"{state.s_topo:.3f}\t{calibrated_level}\t{beta0_z:.2f}\t{s_drop:.2%}\t"
                f"{avg_corr:.3f}\t{avg_vol:.4f}\t{drivers}"
            )


if __name__ == "__main__":
    main()

