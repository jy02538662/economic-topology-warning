# Economic Topology Warning

A non-commercial research prototype for topology-based financial crisis backtesting and 2026/2027 forward-looking market structure risk monitoring.

This project does **not** predict price direction. It studies whether the high-dimensional financial market return space is becoming topologically fragmented, unstable, and synchronized before or during crisis regimes.

Chinese version: [README.zh-CN.md](README.zh-CN.md)

## Core idea

Traditional crisis indicators often become strong after systemic stress is already visible:

```text
correlation rises -> many assets start moving together
volatility rises  -> market stress is already explicit
drawdown appears  -> prices have already moved
```

This prototype asks a different question:

```text
Does the market state space start to fracture before the crash becomes obvious?
```

It treats rolling windows of asset returns as high-dimensional point clouds, builds a lightweight Vietoris-Rips 1-skeleton graph proxy, and estimates structural topology signals such as `beta0`, `beta1`, and `S_topo`.

## What it does

- Detects whether market structure moves from a connected state toward a fragmented state.
- Compares topology signals against conventional controls such as average correlation `avg_corr` and average volatility `avg_vol`.
- Runs exploratory crisis-window backtests for 2000 dotcom, 2008 GFC, 2011 euro debt, and 2020 COVID.
- Adds phase-dynamics diagnostics: `avg_phase`, `phase_sync`, `topo_potential`, top defect nodes, and top risk links.
- Adds sector-level confirmation and an experimental sector-escalated red candidate overlay.
- Provides a live 2026/2027 forward-looking topology risk monitor.

## What it does not do

- It does not predict tomorrow's price direction.
- It does not provide trading advice.
- It does not claim production readiness for real risk systems.
- It does not claim that the current graph proxy is equivalent to full persistent homology.

## Current method

The current MVP is intentionally lightweight and standard-library first:

1. Load rolling windows of stock returns or economic indicator rows.
2. Standardize each window with z-score normalization.
3. Build a Vietoris-Rips 1-skeleton graph proxy.
4. Estimate:
   - `beta0`: connected components, used as a fragmentation proxy.
   - `beta1`: graph cycle rank, used as a feedback-loop proxy.
   - `S_topo`: an engineering proxy for topological stability.
5. Combine trend branch, fracture branch, warning-cycle reset, sector confirmation, and phase diagnostics.
6. Output warning levels:

```text
green -> yellow -> orange -> red
```

## Important limitations

This is a research prototype, not a validated financial risk product.

- `beta0` and `beta1` are estimated from a graph-level approximation.
- `S_topo` is an engineering proxy, not a mathematically complete persistence score.
- The current universe is exploratory: 16 representative S&P 500 stocks for US windows and 17 European ADR / international large-cap proxies for the euro-debt window.
- Results may be affected by sample size, survivorship bias, ticker availability, and threshold tuning.
- Thresholds are exploratory and have not yet passed broad out-of-sample validation.
- A future backend can replace the proxy with `ripser` or `gudhi` while keeping the outer pipeline stable.

## Multi-crisis timeline

Running the multi-crisis backtest generates:

```text
data/multi_crisis/multi_crisis_level_timeline.png
```

The figure shows the `final_level` timeline for the 2000, 2008, 2011, and 2020 windows, with crisis markers and an experimental sector-escalated red candidate overlay.

![Multi-crisis topology warning timeline](data/multi_crisis/multi_crisis_level_timeline.png)

## Backtest summary

The table below summarizes the current P0/P1/P2 and cross-market exploratory validation. The `sector red candidate` is an experimental overlay: when an active market-level warning is confirmed by at least two proxy sectors near the warning window, the system marks a sector-escalated red candidate. It does not overwrite the main `final_level`.

| Window | First active warning | Lead | First red | Red lead | Sector red candidate | Candidate lead | Sector confirmation | Consistency |
|--------|----------------------|------|-----------|----------|----------------------|----------------|---------------------|-------------|
| 2000 dotcom | 2000-01-31 | 39d | 2000-04-03 | -24d | 2000-01-31 | 39d | warn: consumer_defensive + industrial_health; red: financials | warn 2/4; red 1/4 |
| 2008 GFC | 2008-01-16 | 243d | 2008-01-16 | 243d | 2008-01-16 | 243d | warn/red: financials + technology | warn 2/4; red 2/4 |
| 2011 euro debt | 2011-01-18 | 202d | 2011-11-02 | -86d | 2011-01-18 | 202d | warn: europe_technology + europe_defensive; red: europe_financials | warn 2/4; red 1/4 |
| 2020 COVID | 2020-03-02 | -11d | 2020-03-02 | -11d | 2020-03-02 | -11d | broad: all four proxy sectors | warn 4/4; red 4/4 |

Interpretation:

- **2000 dotcom:** slow bubble unwind; trend warnings accumulated before the crash window.
- **2008 GFC:** systemic financial crisis; topology stress turned red very early, though the signal may be too early for direct operational use.
- **2011 euro debt:** the European proxy universe also produced a pre-crisis trend warning, reducing the chance that the signal is purely US-specific.
- **2020 COVID:** an external shock with limited pre-shock warning; the fracture branch reacted quickly after the shock became visible.

## Warning mechanism

The multi-crisis script combines several layers:

1. **Trend branch:** combines `beta0_z` and weakening `S_topo` into a trend score.
2. **Fracture branch:** detects rapid `beta0` jumps over a 5-trading-day lookback and confirms with a volatility spike.
3. **Cycle reset:** cancels short, weak, quickly recovered warning cycles to reduce signal fatigue.
4. **Sector confirmation:** checks which proxy sectors are active orange/red near market-level warning or red signals.
5. **Phase diagnostics:** maps rolling-window drawdowns into risk phases and computes `phase_sync`, `topo_potential`, `top_defect_nodes`, and `top_risk_links`.
6. **Sector consistency overlay:** emits `sector_escalated_red_candidate` when at least two sectors confirm around an active market warning. This is an experimental research field only.

## 2026/2027 forward-looking topology monitor

The repository includes a live forward-looking entry point:

```text
scripts/live_forecast.py
```

It fetches roughly two years of recent representative US equity data, maps the current market state into rolling return point clouds, and outputs a topology risk snapshot for the 2026/2027 window.

This is not a price forecast. It is a structural fragility monitor. The key question is:

```text
Around 2026/2027, is the market state space shifting from connected and recoverable toward fragmented, synchronized, and fragile?
```

### Forward-looking score inputs

`forecast_from_record` combines the following structural signals:

- `beta0_z`: abnormal rise in connected components, indicating stronger state-space fragmentation.
- `s_z`: weakening `S_topo`, indicating reduced topological stability.
- `phase_sync`: elevated phase synchronization, indicating cross-asset risk resonance.
- `sector_warn_count`: sector-level confirmation.
- `cycle_status` / `final_level`: whether the system is already inside an active warning cycle.

The live monitor outputs:

```text
forecast_score, forecast_level, confidence, reason
```

The forward-looking levels are:

```text
low -> watch -> elevated -> high
```

### Scenario interpretation

| Scenario | Topology state | Interpretation |
|----------|----------------|----------------|
| Baseline | `forecast_level = low` | The market point cloud remains relatively connected and stable. |
| Watch | `forecast_level = watch` | Mild fragmentation or phase synchronization appears; continued monitoring is needed. |
| Fragile | `forecast_level = elevated/high` | `beta0`, `S_topo`, phase synchrony, and sector confirmation jointly suggest a fragile structural regime. |

Recommended public wording:

> The 2026/2027 module does not forecast whether an index will rise or fall. It monitors whether the high-dimensional return space is entering a fragile topological regime that resembles historical pre-crisis or crisis-transition windows.

## Installation

This project requires Python 3.10 or newer. The core analyzer has no required third-party dependencies.

Optional plotting support:

```bash
pip install -e ".[plot]"
```

For basic usage without optional plotting:

```bash
pip install -e .
```

## Quick start

Run the small demo:

```bash
python demo.py
```

Run tests:

```bash
python tests/test_analyzer.py
```

Run the 2007-2009 S&P 500 representative backtest:

```bash
python scripts/sp500_2008_backtest.py
```

Generated outputs:

```text
data/sp500_representative_topology_2007_2009.csv
data/sp500_representative_topology_2007_2009.png
```

Run the multi-crisis backtest:

```bash
python scripts/multi_crisis_backtest.py
```

Generated curated outputs kept in the repository:

```text
data/multi_crisis/summary.csv
data/multi_crisis/multi_crisis_level_timeline.png
data/multi_crisis/risk_reports/*_risk_report.txt
```

The script can also regenerate detailed per-window and sector CSV files. Those generated intermediate files are ignored by `.gitignore` to keep the public repository small:

```text
data/multi_crisis/dotcom_2000.csv
data/multi_crisis/gfc_2008.csv
data/multi_crisis/euro_debt_2011.csv
data/multi_crisis/covid_2020.csv
data/multi_crisis/sectors/
data/yahoo_cache/
data/stooq_cache/
```

Run the 2026/2027 live topology monitor:

```bash
python scripts/live_forecast.py
```

Typical live output includes:

```text
latest date
current beta0 / beta1 / S_topo
current green/yellow/orange/red topology warning level
phase_sync / topo_potential
forecast_score / forecast_level / confidence / reason
best forecast over the recent 60-day window
recent orange/red warning statistics
combined forward-looking topology judgment
```

## Project structure

```text
economic_topology/
  econ_topology/
    analyzer.py          # rolling topology analyzer
    phase_dynamics.py    # phase synchronization and risk-link diagnostics
  scripts/
    sp500_2008_backtest.py
    multi_crisis_backtest.py
    live_forecast.py
  tests/
    test_analyzer.py
  README.md
  README.zh-CN.md
  STORY_BRIEF.md
  LICENSE
  pyproject.toml
```

## Suggested research roadmap

1. Expand the universe to S&P 100, STOXX Europe, or larger historical constituent sets.
2. Add more cross-market windows, such as the Asian financial crisis, Brexit, and additional euro-debt subperiods.
3. Replace the graph proxy with true persistent homology via `ripser` or `gudhi`.
4. Add stronger baselines: VIX, VSTOXX, max drawdown, rolling covariance eigenvalues, and correlation-network metrics.
5. Run leave-one-crisis-out validation to reduce threshold overfitting concerns.
6. Promote sector consistency from an explanatory overlay to an optional trigger rule only after out-of-sample validation.
7. Keep a separate forward-looking log for 2026/2027 snapshots instead of freezing one market judgment inside the README.

## License

This project is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). Non-commercial use is permitted. Commercial use requires separate permission.

## Disclaimer

This repository is for research and educational purposes only. It is not financial advice, investment advice, trading advice, or a production risk-management system. Historical exploratory results do not imply future performance.
