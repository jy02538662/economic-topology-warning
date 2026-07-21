"""Multi-crisis topology warning backtest.

Runs the proxy topology analyzer on representative S&P 500 constituents across
multiple crisis windows and writes per-window CSV plus a summary CSV.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http.client import IncompleteRead
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econ_topology import EconomicTopoAnalyzer, EconomicTopoConfig
from econ_topology.phase_dynamics import phase_diagnostics


US_SYMBOLS = [
    "aapl", "msft", "xom", "jpm", "ge", "wmt", "pg", "jnj",
    "bac", "c", "gs", "ibm", "ko", "pep", "mcd", "hd",
]
US_SECTOR_GROUPS = {
    "technology": ["aapl", "msft", "ibm"],
    "financials": ["jpm", "bac", "c", "gs"],
    "consumer_defensive": ["wmt", "pg", "ko", "pep", "mcd"],
    "industrial_health": ["ge", "jnj", "hd"],
}
EUROPE_SYMBOLS = [
    "san", "bbva", "db", "ubs", "ing", "bcs",
    "bp", "tte", "shel", "sap", "asml", "nok",
    "nvs", "azn", "ul", "deo", "vod",
]
EUROPE_SECTOR_GROUPS = {
    "europe_financials": ["san", "bbva", "db", "ubs", "ing", "bcs"],
    "europe_energy": ["bp", "tte", "shel"],
    "europe_technology": ["sap", "asml", "nok"],
    "europe_defensive": ["nvs", "azn", "ul", "deo", "vod"],
}
CACHE_DIR = ROOT / "data" / "yahoo_cache"
OUT_DIR = ROOT / "data" / "multi_crisis"
WINDOW_SIZE = 60
EDGE_QUANTILE = 0.25
TREND_CONFIRM_DAYS = 5
RESET_GRACE_DAYS = 30
SECTOR_CONFIRM_WINDOW_DAYS = 30
SECTOR_RED_ESCALATION_ENABLED = True
SECTOR_RED_ESCALATION_MIN_GROUPS = 2
VOL_CONFIRM_Z = 2.0
FRACTURE_LOOKBACK_DAYS = 5
FRACTURE_QUANTILE = 0.995


@dataclass(frozen=True)
class CrisisWindow:
    name: str
    start: date
    end: date
    crisis_date: date
    symbols: list[str]
    sector_groups: dict[str, list[str]]


WINDOWS = [
    CrisisWindow("dotcom_2000", date(1999, 1, 1), date(2002, 12, 31), date(2000, 3, 10), US_SYMBOLS, US_SECTOR_GROUPS),
    CrisisWindow("gfc_2008", date(2007, 1, 1), date(2009, 12, 31), date(2008, 9, 15), US_SYMBOLS, US_SECTOR_GROUPS),
    CrisisWindow("euro_debt_2011", date(2010, 1, 1), date(2012, 12, 31), date(2011, 8, 8), EUROPE_SYMBOLS, EUROPE_SECTOR_GROUPS),
    CrisisWindow("covid_2020", date(2019, 1, 1), date(2021, 12, 31), date(2020, 2, 20), US_SYMBOLS, US_SECTOR_GROUPS),
]


def fetch_yahoo_daily(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}_{start.isoformat()}_{end.isoformat()}.json"
    if cache_file.exists():
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}"
            f"?period1={period1}&period2={period2}&interval=1d&events=history"
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=35) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except (IncompleteRead, TimeoutError, OSError) as exc:
                last_error = exc
                time.sleep(1.0 + attempt)
        else:
            raise RuntimeError(f"failed to fetch {symbol}: {last_error}")
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"failed to fetch {symbol}: {payload.get('chart', {}).get('error')}")
    series = result[0]
    timestamps = series.get("timestamp") or []
    closes = (series.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
              or series.get("indicators", {}).get("quote", [{}])[0].get("close")
              or [])
    rows: list[tuple[date, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        if start <= day <= end:
            rows.append((day, float(close)))
    return rows


def build_return_rows(symbols: list[str], window: CrisisWindow) -> tuple[list[str], list[dict[str, float]]]:
    raw = {}
    for symbol in symbols:
        try:
            values = fetch_yahoo_daily(symbol, window.start, window.end)
        except Exception as exc:
            print(f"skip {window.name}:{symbol} fetch failed: {exc}")
            continue
        if len(values) >= 180:
            raw[symbol] = {day: close for day, close in values}
    usable_symbols = sorted(raw)
    if len(usable_symbols) < 8:
        raise ValueError(f"{window.name}: not enough usable symbols: {usable_symbols}")
    common_days = sorted(set.intersection(*(set(v.keys()) for v in raw.values())))

    rows: list[dict[str, float]] = []
    previous: dict[str, float] | None = None
    for day in common_days:
        closes = {symbol: raw[symbol][day] for symbol in usable_symbols}
        if previous is not None:
            item = {"date": day.toordinal()}
            for symbol in usable_symbols:
                item[symbol] = closes[symbol] / previous[symbol] - 1.0
            rows.append(item)
        previous = closes
    return usable_symbols, rows


def baseline(states, rows: list[dict[str, float]], symbols: list[str], crisis_date: date) -> dict[str, float]:
    controls = control_metrics(states, rows, symbols)
    candidates = [
        state for state in states
        if date.fromordinal(int(rows[state.end_index]["date"])) < crisis_date
    ]
    if len(candidates) > 160:
        candidates = candidates[:160]
    if not candidates:
        candidates = states[:120]

    beta0_values = [float(s.beta0) for s in candidates]
    s_values = [float(s.s_topo) for s in candidates]
    vol_values = [controls[s.end_index][1] for s in candidates]
    deltas = [
        max(0.0, float(candidates[i].beta0 - candidates[i - 1].beta0))
        for i in range(1, len(candidates))
    ] or [0.0]

    return {
        "beta0_mean": _mean(beta0_values),
        "beta0_std": _std(beta0_values) or 1.0,
        "s_mean": _mean(s_values),
        "s_std": _std(s_values) or 1.0,
        "vol_mean": _mean(vol_values),
        "vol_std": _std(vol_values) or 1.0,
        "beta0_delta_q": _quantile(deltas, FRACTURE_QUANTILE),
    }


def trend_alert(state, base: dict[str, float]) -> tuple[str, float, float, float]:
    beta0_z = (state.beta0 - base["beta0_mean"]) / base["beta0_std"]
    s_z = max(0.0, (base["s_mean"] - state.s_topo) / base["s_std"])
    score = 0.6 * beta0_z + 0.4 * s_z
    if score >= 3.0:
        return "red", score, beta0_z, s_z
    if score >= 2.0:
        return "orange", score, beta0_z, s_z
    if score >= 1.0:
        return "yellow", score, beta0_z, s_z
    return "green", score, beta0_z, s_z


def fracture_alert(
    beta0_history: list[int],
    avg_vol: float,
    base: dict[str, float],
) -> tuple[bool, float, float]:
    if len(beta0_history) <= FRACTURE_LOOKBACK_DAYS:
        return False, 0.0, 0.0
    current_beta0 = beta0_history[-1]
    previous_beta0 = beta0_history[-1 - FRACTURE_LOOKBACK_DAYS]
    beta0_delta = max(0.0, float(current_beta0 - previous_beta0))
    vol_z = (avg_vol - base["vol_mean"]) / base["vol_std"]
    delta_threshold = max(2.0, base["beta0_delta_q"])
    return beta0_delta >= delta_threshold and vol_z >= VOL_CONFIRM_Z, beta0_delta, vol_z


def confirmed_level(raw_level: str, trend_streak: int, fracture: bool) -> str:
    if fracture:
        return "red"
    if raw_level == "yellow" and trend_streak >= TREND_CONFIRM_DAYS:
        return "orange"
    if raw_level in {"orange", "red"} and trend_streak < TREND_CONFIRM_DAYS:
        return "yellow"
    return raw_level


def control_metrics(
    states,
    rows: list[dict[str, float]],
    symbols: list[str],
) -> dict[int, tuple[float, float]]:
    metrics: dict[int, tuple[float, float]] = {}
    for state in states:
        start = state.end_index - WINDOW_SIZE + 1
        window = rows[start:state.end_index + 1]
        avg_corr = average_pairwise_correlation(window, symbols)
        avg_vol = sum(_std([float(row[symbol]) for row in window]) for symbol in symbols) / len(symbols)
        metrics[state.end_index] = (avg_corr, avg_vol)
    return metrics


def average_pairwise_correlation(window: list[dict[str, float]], symbols: list[str]) -> float:
    values = {symbol: [float(row[symbol]) for row in window] for symbol in symbols}
    corrs: list[float] = []
    for i, left in enumerate(symbols):
        for right in symbols[i + 1:]:
            corrs.append(correlation(values[left], values[right]))
    return _mean(corrs)


def correlation(xs: list[float], ys: list[float]) -> float:
    mx = _mean(xs)
    my = _mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / ((vx * vy) ** 0.5)


def analyze_symbol_group(
    symbols: list[str],
    rows: list[dict[str, float]],
    crisis_date: date,
) -> tuple[list[dict[str, object]], list[dict[str, object]], date | None]:
    analyzer = EconomicTopoAnalyzer(
        symbols,
        EconomicTopoConfig(window_size=WINDOW_SIZE, edge_quantile=EDGE_QUANTILE, s_topo_drop_warn=0.20),
    )
    states = analyzer.analyze(rows)
    base = baseline(states, rows, symbols, crisis_date)
    controls = control_metrics(states, rows, symbols)

    records: list[dict[str, object]] = []
    trend_streak = 0
    beta0_history: list[int] = []
    first_fracture: date | None = None

    for state in states:
        day = date.fromordinal(int(rows[state.end_index]["date"]))
        raw_level, score, beta0_z, s_z = trend_alert(state, base)
        avg_corr, avg_vol = controls[state.end_index]
        window = rows[state.end_index - WINDOW_SIZE + 1:state.end_index + 1]
        phase = phase_diagnostics(window, symbols, state.beta1)
        beta0_history.append(state.beta0)
        fracture, beta0_delta, vol_z = fracture_alert(beta0_history, avg_vol, base)
        trend_streak = trend_streak + 1 if raw_level in {"yellow", "orange", "red"} else 0
        final_level = confirmed_level(raw_level, trend_streak, fracture)
        if fracture and first_fracture is None:
            first_fracture = day
        records.append({
            "date": day,
            "beta0": state.beta0,
            "beta1": state.beta1,
            "s_topo": state.s_topo,
            "raw_level": raw_level,
            "final_level": final_level,
            "score": score,
            "beta0_z": beta0_z,
            "s_z": s_z,
            "avg_corr": avg_corr,
            "avg_vol": avg_vol,
            "fracture": fracture,
            "beta0_delta": beta0_delta,
            "vol_z": vol_z,
            "avg_phase": phase.avg_phase,
            "phase_sync": phase.phase_sync,
            "topo_potential": phase.topo_potential,
            "top_defect_nodes": ";".join(f"{name}:{value:.6f}" for name, value in phase.top_defect_nodes),
            "top_risk_links": ";".join(f"{left}-{right}:{value:.6f}" for left, right, value in phase.top_risk_links),
            "drivers": ";".join(f"{name}:{value:.6f}" for name, value in state.drivers),
            "cycle_id": "",
            "cycle_status": "none",
        })
    cycles = annotate_warning_cycles(records)
    return records, cycles, first_fracture


def warning_summary(
    records: list[dict[str, object]],
    cycles: list[dict[str, object]],
    crisis_date: date,
) -> dict[str, object]:
    active_cycles = [cycle for cycle in cycles if not cycle["cancelled"]]
    first_warn = active_cycles[0]["start"] if active_cycles else None
    first_red = next((cycle["first_red"] for cycle in active_cycles if cycle["first_red"] is not None), None)
    return {
        "first_warn": first_warn,
        "first_red": first_red,
        "cycles_total": len(cycles),
        "cycles_active": len(active_cycles),
        "cycles_cancelled": len(cycles) - len(active_cycles),
        "pre_crisis_warn_days": sum(
            1 for record in records
            if record["cycle_status"] == "active"
            and record["final_level"] in {"orange", "red"}
            and record["date"] < crisis_date
        ),
        "post_crisis_warn_days": sum(
            1 for record in records
            if record["cycle_status"] == "active"
            and record["final_level"] in {"orange", "red"}
            and record["date"] >= crisis_date
        ),
    }


def write_records(records: list[dict[str, object]], output: Path) -> None:
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "beta0", "beta1", "s_topo", "raw_level", "final_level", "score",
            "beta0_z", "s_z", "avg_corr", "avg_vol", "fracture", "beta0_delta", "vol_z",
            "avg_phase", "phase_sync", "topo_potential", "top_defect_nodes", "top_risk_links",
            "cycle_id", "cycle_status", "drivers",
        ])
        for record in records:
            writer.writerow([
                record["date"].isoformat(), record["beta0"], record["beta1"],
                f"{record['s_topo']:.6f}", record["raw_level"], record["final_level"],
                f"{record['score']:.3f}", f"{record['beta0_z']:.3f}", f"{record['s_z']:.3f}",
                f"{record['avg_corr']:.6f}", f"{record['avg_vol']:.6f}", int(record["fracture"]),
                f"{record['beta0_delta']:.3f}", f"{record['vol_z']:.3f}",
                f"{record['avg_phase']:.6f}", f"{record['phase_sync']:.6f}",
                f"{record['topo_potential']:.6f}", record["top_defect_nodes"], record["top_risk_links"],
                record["cycle_id"], record["cycle_status"], record["drivers"],
            ])


def _parse_score_list(text: object) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for part in str(text or "").split(";"):
        if ":" not in part:
            continue
        name, value = part.rsplit(":", 1)
        try:
            items.append((name, float(value)))
        except ValueError:
            continue
    return items


def _gini(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(max(0.0, value) for value in values)
    total = sum(ordered)
    if total == 0:
        return 0.0
    n = len(ordered)
    weighted = sum((idx + 1) * value for idx, value in enumerate(ordered))
    return (2 * weighted) / (n * total) - (n + 1) / n


def topology_design_suggestions(
    trigger: dict[str, object],
    centralization_risk: float,
    redundancy_risk: float,
    sync_risk: float,
) -> list[str]:
    defects = _parse_score_list(trigger.get("top_defect_nodes", ""))
    links = _parse_score_list(trigger.get("top_risk_links", ""))
    suggestions: list[str] = []

    if defects:
        targets = ",".join(name for name, _ in defects[:3])
        suggestions.append(f"reduce_coupling_on_defect_nodes:{targets}; action=review/highest-correlated exposures")
    if links:
        link_targets = ",".join(name for name, _ in links[:3])
        suggestions.append(f"split_strong_synchronous_links:{link_targets}; action=cap/rebalance 20-30pct link weight proxy")
    if centralization_risk > 0.60:
        suggestions.append("centralization_control: diversify risk contribution away from top defect nodes")
    if redundancy_risk > 0:
        suggestions.append("redundancy_repair: add weak cross-cluster hedging links to raise beta1 proxy")
    if sync_risk > 0:
        suggestions.append("phase_resonance_control: reduce broad beta exposure and monitor cross-sector phase lock")
    if not suggestions:
        suggestions.append("no_immediate_rewire: keep monitoring; topology design intervention not indicated")
    return suggestions[:5]


def forecast_from_record(
    record: dict[str, object] | None,
    sector_warn_count: int,
    sector_total: int,
) -> dict[str, object]:
    if record is None:
        return {"score": 0.0, "level": "none", "confidence": 0.0, "reason": "no_record"}

    beta0_component = max(0.0, min(2.0, float(record["beta0_z"])))
    s_component = max(0.0, min(2.0, float(record["s_z"])))
    phase_component = max(0.0, min(2.0, (float(record["phase_sync"]) - 0.65) / 0.15))
    sector_component = 0.0 if sector_total <= 0 else 2.0 * sector_warn_count / sector_total
    vuln_component = 0.0
    if str(record["cycle_status"]) == "active":
        vuln_component += 0.5
    if str(record["final_level"]) in {"orange", "red"}:
        vuln_component += 0.5
    if float(record["phase_sync"]) >= 0.80:
        vuln_component += 0.5

    score = (
        0.25 * beta0_component
        + 0.20 * s_component
        + 0.25 * phase_component
        + 0.15 * sector_component
        + 0.15 * min(2.0, vuln_component)
    ) * 2.0

    if score >= 3.5:
        level = "high"
    elif score >= 2.5:
        level = "elevated"
    elif score >= 1.5:
        level = "watch"
    else:
        level = "low"

    reasons: list[str] = []
    if beta0_component >= 1.0:
        reasons.append("beta0 fragmentation rising")
    if s_component >= 1.0:
        reasons.append("S_topo weakening")
    if phase_component >= 1.0:
        reasons.append("phase synchrony elevated")
    if sector_component >= 1.0:
        reasons.append("sector confirmation present")
    if vuln_component >= 1.0:
        reasons.append("active vulnerability flags")
    return {
        "score": score,
        "level": level,
        "confidence": min(0.95, max(0.05, score / 4.0)),
        "reason": "; ".join(reasons) or "no strong topology forecast signal",
    }


def record_on_or_before(records: list[dict[str, object]], target: date) -> dict[str, object] | None:
    candidates = [record for record in records if record["date"] <= target]
    return candidates[-1] if candidates else None


def evaluate_forecast_horizon(
    records: list[dict[str, object]],
    crisis_date: date,
    horizon_days: int,
    sector_warn_count: int,
    sector_total: int,
) -> dict[str, object]:
    target = crisis_date.toordinal() - horizon_days
    record = record_on_or_before(records, date.fromordinal(target))
    forecast = forecast_from_record(record, sector_warn_count, sector_total)
    hit = forecast["level"] in {"elevated", "high"}
    return {
        "date": "" if record is None else record["date"].isoformat(),
        "level": forecast["level"],
        "score": f"{float(forecast['score']):.3f}",
        "confidence": f"{float(forecast['confidence']):.3f}",
        "reason": forecast["reason"],
        "hit": int(hit),
    }


def confirmed_near(records: list[dict[str, object]], target: date | None) -> str:
    if target is None:
        return ""
    for record in records:
        delta = abs((record["date"] - target).days)
        if delta <= SECTOR_CONFIRM_WINDOW_DAYS and record["cycle_status"] == "active":
            if record["final_level"] in {"orange", "red"}:
                return str(record["final_level"])
    return ""


def build_risk_report(
    window_name: str,
    crisis_date: date,
    records: list[dict[str, object]],
    first_warn: date | None,
    first_red: date | None,
    sector_warn_confirmed: str,
    sector_red_confirmed: str,
    forecast_20d: dict[str, object],
    forecast_60d: dict[str, object],
    output_dir: Path,
) -> Path | None:
    trigger_date = first_red or first_warn
    if trigger_date is None:
        return None
    trigger = next((record for record in records if record["date"] == trigger_date), None)
    if trigger is None:
        return None

    defect_scores = [value for _, value in _parse_score_list(trigger.get("top_defect_nodes", ""))]
    redundancy_scores = [float(record["beta1"]) for record in records]
    beta1_threshold = _quantile(redundancy_scores, 0.20) if redundancy_scores else 0.0
    centralization_risk = _gini(defect_scores)
    redundancy_risk = 1.0 if float(trigger["beta1"]) <= beta1_threshold else 0.0
    sync_risk = 1.0 if float(trigger["phase_sync"]) >= 0.80 else 0.0
    design_suggestions = topology_design_suggestions(trigger, centralization_risk, redundancy_risk, sync_risk)

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{window_name}_risk_report.txt"
    lines = [
        f"window: {window_name}",
        f"crisis_date: {crisis_date.isoformat()}",
        f"trigger_date: {trigger_date.isoformat()}",
        f"trigger_level: {trigger['final_level']}",
        f"phase_sync: {trigger['phase_sync']}",
        f"topo_potential: {trigger['topo_potential']}",
        f"top_defect_nodes: {trigger['top_defect_nodes']}",
        f"top_risk_links: {trigger['top_risk_links']}",
        f"sector_warn_confirmed: {sector_warn_confirmed}",
        f"sector_red_confirmed: {sector_red_confirmed}",
        f"centralization_risk_gini: {centralization_risk:.6f}",
        f"redundancy_threshold_beta1_p20: {beta1_threshold:.6f}",
        f"redundancy_risk: {redundancy_risk:.0f}",
        f"sync_risk: {sync_risk:.0f}",
        "vulnerability_flags: " + ",".join([
            flag for flag, enabled in [
                ("centralized_defect", centralization_risk > 0.60),
                ("redundancy_shortage", redundancy_risk > 0),
                ("phase_resonance", sync_risk > 0),
            ] if enabled
        ]),
        "design_suggestions:",
        *[f"- {item}" for item in design_suggestions],
        "forecast:",
        f"- 20d date={forecast_20d['date']} level={forecast_20d['level']} score={forecast_20d['score']} confidence={forecast_20d['confidence']} hit={forecast_20d['hit']}",
        f"  reason={forecast_20d['reason']}",
        f"- 60d date={forecast_60d['date']} level={forecast_60d['level']} score={forecast_60d['score']} confidence={forecast_60d['confidence']} hit={forecast_60d['hit']}",
        f"  reason={forecast_60d['reason']}",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def sector_confirmations(
    window_name: str,
    all_symbols: list[str],
    sector_groups: dict[str, list[str]],
    rows: list[dict[str, float]],
    crisis_date: date,
    market_first_warn: date | None,
    market_first_red: date | None,
) -> tuple[list[dict[str, object]], str, str, str]:
    confirmations: list[dict[str, object]] = []
    sector_dir = OUT_DIR / "sectors"
    sector_dir.mkdir(parents=True, exist_ok=True)

    for sector, configured_symbols in sector_groups.items():
        sector_symbols = [symbol for symbol in configured_symbols if symbol in all_symbols]
        if len(sector_symbols) < 3:
            continue
        records, cycles, first_fracture = analyze_symbol_group(sector_symbols, rows, crisis_date)
        summary = warning_summary(records, cycles, crisis_date)
        output = sector_dir / f"{window_name}_{sector}.csv"
        write_records(records, output)
        first_warn = summary["first_warn"]
        first_red = summary["first_red"]
        warn_anchor_level = confirmed_near(records, market_first_warn)
        red_anchor_level = confirmed_near(records, market_first_red)
        confirmations.append({
            "sector": sector,
            "symbols": "+".join(sector_symbols),
            "market_warn_anchor_level": warn_anchor_level,
            "market_red_anchor_level": red_anchor_level,
            "first_warn": None if first_warn is None else first_warn.isoformat(),
            "warning_lead_days": None if first_warn is None else (crisis_date - first_warn).days,
            "first_red": None if first_red is None else first_red.isoformat(),
            "red_lead_days": None if first_red is None else (crisis_date - first_red).days,
            "first_fracture": None if first_fracture is None else first_fracture.isoformat(),
            "fracture_lead_days": None if first_fracture is None else (crisis_date - first_fracture).days,
            "cycles_active": summary["cycles_active"],
            "cycles_total": summary["cycles_total"],
            "cycles_cancelled": summary["cycles_cancelled"],
            "csv": str(output),
        })

    confirmations.sort(key=lambda item: item["warning_lead_days"] if item["warning_lead_days"] is not None else -9999, reverse=True)
    sector_summary = sector_dir / f"{window_name}_sector_summary.csv"
    if confirmations:
        with sector_summary.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(confirmations[0].keys()))
            writer.writeheader()
            writer.writerows(confirmations)
    warn_confirmed = [
        item["sector"] for item in confirmations
        if item["market_warn_anchor_level"] in {"orange", "red"}
    ]
    red_confirmed = [
        item["sector"] for item in confirmations
        if item["market_red_anchor_level"] in {"orange", "red"}
    ]
    return confirmations, str(sector_summary), "+".join(warn_confirmed), "+".join(red_confirmed)


def run_window(window: CrisisWindow) -> dict[str, object]:
    symbols, rows = build_return_rows(window.symbols, window)
    records, cycles, first_fracture = analyze_symbol_group(symbols, rows, window.crisis_date)
    summary = warning_summary(records, cycles, window.crisis_date)
    first_warn = summary["first_warn"]
    first_red = summary["first_red"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"{window.name}.csv"
    write_records(records, output)
    sector_rows, sector_summary_csv, sector_warn_confirmed, sector_red_confirmed = sector_confirmations(
        window.name, symbols, window.sector_groups, rows, window.crisis_date, first_warn, first_red
    )
    sector_warn_count = len([x for x in sector_warn_confirmed.split("+") if x])
    sector_red_count = len([x for x in sector_red_confirmed.split("+") if x])
    sector_total = len(sector_rows)
    sector_escalated_red = (
        first_warn
        if SECTOR_RED_ESCALATION_ENABLED and sector_warn_count >= SECTOR_RED_ESCALATION_MIN_GROUPS
        else None
    )
    forecast_20d = evaluate_forecast_horizon(records, window.crisis_date, 20, sector_warn_count, sector_total)
    forecast_60d = evaluate_forecast_horizon(records, window.crisis_date, 60, sector_warn_count, sector_total)
    forecast_hits_20d = forecast_20d["hit"]
    forecast_hits_60d = forecast_60d["hit"]
    first_warn_record = next((record for record in records if record["date"] == first_warn), None) if first_warn else None
    max_phase_sync = max(float(record["phase_sync"]) for record in records) if records else 0.0
    min_topo_potential = min(float(record["topo_potential"]) for record in records) if records else 0.0

    return {
        "name": window.name,
        "symbols": len(symbols),
        "rows": len(rows),
        "states": len(records),
        "crisis_date": window.crisis_date.isoformat(),
        "first_orange_or_red": None if first_warn is None else first_warn.isoformat(),
        "warning_lead_days": None if first_warn is None else (window.crisis_date - first_warn).days,
        "first_red": None if first_red is None else first_red.isoformat(),
        "red_lead_days": None if first_red is None else (window.crisis_date - first_red).days,
        "first_fracture": None if first_fracture is None else first_fracture.isoformat(),
        "fracture_lead_days": None if first_fracture is None else (window.crisis_date - first_fracture).days,
        "cycles_total": summary["cycles_total"],
        "cycles_active": summary["cycles_active"],
        "cycles_cancelled": summary["cycles_cancelled"],
        "pre_crisis_warn_days": summary["pre_crisis_warn_days"],
        "post_crisis_warn_days": summary["post_crisis_warn_days"],
        "max_phase_sync": f"{max_phase_sync:.6f}",
        "min_topo_potential": f"{min_topo_potential:.6f}",
        "first_warn_phase_sync": "" if first_warn_record is None else f"{float(first_warn_record['phase_sync']):.6f}",
        "first_warn_top_defect_nodes": "" if first_warn_record is None else first_warn_record["top_defect_nodes"],
        "forecast_20d_date": forecast_20d["date"],
        "forecast_20d_level": forecast_20d["level"],
        "forecast_20d_score": forecast_20d["score"],
        "forecast_20d_confidence": forecast_20d["confidence"],
        "forecast_20d_reason": forecast_20d["reason"],
        "forecast_20d_hit": forecast_hits_20d,
        "forecast_60d_date": forecast_60d["date"],
        "forecast_60d_level": forecast_60d["level"],
        "forecast_60d_score": forecast_60d["score"],
        "forecast_60d_confidence": forecast_60d["confidence"],
        "forecast_60d_reason": forecast_60d["reason"],
        "forecast_60d_hit": forecast_hits_60d,
        "sector_warn_confirmed": sector_warn_confirmed,
        "sector_red_confirmed": sector_red_confirmed,
        "sector_warn_count": sector_warn_count,
        "sector_red_count": sector_red_count,
        "sector_consistency": f"{sector_warn_count}/{sector_total}",
        "sector_red_consistency": f"{sector_red_count}/{sector_total}",
        "sector_escalated_red_candidate": None if sector_escalated_red is None else sector_escalated_red.isoformat(),
        "sector_escalated_red_lead_days": None if sector_escalated_red is None else (window.crisis_date - sector_escalated_red).days,
        "sector_escalation_rule": f"warn_count>={SECTOR_RED_ESCALATION_MIN_GROUPS}/{sector_total}" if SECTOR_RED_ESCALATION_ENABLED else "disabled",
        "sector_groups": sector_total,
        "sector_summary_csv": sector_summary_csv,
        "risk_report": None if risk_report is None else str(risk_report),
        "csv": str(output),
    }


def annotate_warning_cycles(records: list[dict[str, object]]) -> list[dict[str, object]]:
    cycles: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    cycle_id = 0

    for idx, record in enumerate(records):
        level = str(record["final_level"])
        is_warning = level in {"orange", "red"}
        if current is None and is_warning:
            cycle_id += 1
            current = {
                "id": cycle_id,
                "start_idx": idx,
                "end_idx": idx,
                "start": record["date"],
                "first_red": record["date"] if level == "red" else None,
                "max_score": float(record["score"]),
                "cancelled": False,
            }
        elif current is not None:
            current["end_idx"] = idx
            current["max_score"] = max(float(current["max_score"]), float(record["score"]))
            if level == "red" and current["first_red"] is None:
                current["first_red"] = record["date"]
            if level == "green":
                duration = idx - int(current["start_idx"])
                orange_days = sum(
                    1 for cycle_idx in range(int(current["start_idx"]), idx + 1)
                    if records[cycle_idx]["final_level"] in {"orange", "red"}
                )
                current["cancelled"] = (
                    current["first_red"] is None
                    and duration <= RESET_GRACE_DAYS
                    and orange_days < TREND_CONFIRM_DAYS
                )
                cycles.append(current)
                current = None

    if current is not None:
        cycles.append(current)

    for cycle in cycles:
        status = "cancelled" if cycle["cancelled"] else "active"
        for idx in range(int(cycle["start_idx"]), int(cycle["end_idx"]) + 1):
            records[idx]["cycle_id"] = cycle["id"]
            records[idx]["cycle_status"] = status
    return cycles


def write_summary(rows: list[dict[str, object]]) -> Path:
    output = OUT_DIR / "summary.csv"
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return output


def plot_multi_crisis_timeline(summary_rows: list[dict[str, object]]) -> Path | None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return None

    level_value = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    output = OUT_DIR / "multi_crisis_level_timeline.png"
    fig, axes = plt.subplots(len(summary_rows), 1, figsize=(12, 8), sharex=False)
    if len(summary_rows) == 1:
        axes = [axes]

    for ax, summary in zip(axes, summary_rows):
        csv_path = Path(str(summary["csv"]))
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        days = [datetime.strptime(row["date"], "%Y-%m-%d").date() for row in rows]
        levels = [level_value[row["final_level"]] for row in rows]
        crisis_day = datetime.strptime(str(summary["crisis_date"]), "%Y-%m-%d").date()
        candidate = summary.get("sector_escalated_red_candidate")
        candidate_day = None if not candidate or candidate == "None" else datetime.strptime(str(candidate), "%Y-%m-%d").date()
        ax.step(days, levels, where="post", linewidth=1.4, label=str(summary["name"]))
        ax.axvline(crisis_day, color="black", linestyle="--", linewidth=1, label="crisis marker")
        if candidate_day is not None:
            ax.axvline(candidate_day, color="#dc2626", linestyle=":", linewidth=1.2, label="sector red candidate")
        ax.set_yticks([0, 1, 2, 3], ["green", "yellow", "orange", "red"])
        ax.set_ylim(-0.2, 3.2)
        ax.grid(alpha=0.25)
        ax.set_title(
            f"{summary['name']} | first={summary['first_orange_or_red']} "
            f"lead={summary['warning_lead_days']}d | red={summary['first_red']} "
            f"| sector-red*={summary.get('sector_escalated_red_candidate')}"
        )
    fig.suptitle("Topology Warning Level Timeline Across Crisis Windows")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float:
    mean = _mean(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    ordered = sorted(xs)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> None:
    summaries = [run_window(window) for window in WINDOWS]
    summary_path = write_summary(summaries)
    timeline_path = plot_multi_crisis_timeline(summaries)
    print(f"summary={summary_path}")
    print(f"timeline={timeline_path if timeline_path else 'matplotlib not installed, skipped'}")
    for row in summaries:
        print(
            f"{row['name']} crisis={row['crisis_date']} symbols={row['symbols']} "
            f"first_warn={row['first_orange_or_red']} lead={row['warning_lead_days']}d "
            f"first_red={row['first_red']} red_lead={row['red_lead_days']}d "
            f"first_fracture={row['first_fracture']} fracture_lead={row['fracture_lead_days']}d "
            f"cycles={row['cycles_active']}/{row['cycles_total']} cancelled={row['cycles_cancelled']} "
            f"sector_warn={row['sector_warn_confirmed']} sector_red={row['sector_red_confirmed']} "
            f"consistency={row['sector_consistency']} red_consistency={row['sector_red_consistency']} "
            f"sector_red_candidate={row['sector_escalated_red_candidate']} "
            f"candidate_lead={row['sector_escalated_red_lead_days']}d "
            f"pre_warn_days={row['pre_crisis_warn_days']}"
        )


if __name__ == "__main__":
    main()
