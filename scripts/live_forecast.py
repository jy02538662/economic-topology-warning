"""Live topology risk forecast for current market state.

Fetches recent stock data and runs the topology analyzer to produce
a forward-looking topology risk assessment for the 2026-2027 window.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econ_topology import EconomicTopoAnalyzer, EconomicTopoConfig
from econ_topology.phase_dynamics import phase_diagnostics
from scripts.multi_crisis_backtest import (
    US_SYMBOLS,
    US_SECTOR_GROUPS,
    fetch_yahoo_daily,
    build_return_rows,
    control_metrics,
    baseline,
    trend_alert,
    fracture_alert,
    confirmed_level,
    annotate_warning_cycles,
    forecast_from_record,
    WINDOW_SIZE,
    EDGE_QUANTILE,
    CrisisWindow,
)


def main() -> None:
    today = date.today()
    # 取最近约2年数据用于分析
    start = date(today.year - 2, 1, 1)
    end = today

    print(f"=== 实时拓扑风险前瞻 ===")
    print(f"数据范围: {start} ~ {end}")
    print(f"标的: {', '.join(s.upper() for s in US_SYMBOLS)}")
    print()

    # 构造一个 CrisisWindow（crisis_date 设为 end，只用于 baseline 计算）
    window = CrisisWindow(
        name="live_2025",
        start=start,
        end=end,
        crisis_date=end,  # 占位，用于 baseline 取 crisis 前数据
        symbols=US_SYMBOLS,
        sector_groups=US_SECTOR_GROUPS,
    )

    # 获取数据
    print("正在获取最新市场数据...")
    from scripts.multi_crisis_backtest import build_return_rows as _build
    # build_return_rows 需要 CrisisWindow 但只用 start/end
    symbols, rows = _build(window.symbols, window)
    print(f"获取到 {len(symbols)} 只标的, {len(rows)} 个交易日")
    print()

    # 运行拓扑分析
    analyzer = EconomicTopoAnalyzer(
        symbols,
        EconomicTopoConfig(window_size=WINDOW_SIZE, edge_quantile=EDGE_QUANTILE, s_topo_drop_warn=0.20),
    )
    states = analyzer.analyze(rows)
    base = baseline(states, rows, symbols, end)
    controls = control_metrics(states, rows, symbols)

    # 构建完整记录
    records = []
    trend_streak = 0
    beta0_history: list[int] = []
    for state in states:
        day = date.fromordinal(int(rows[state.end_index]["date"]))
        raw_level, score, beta0_z, s_z = trend_alert(state, base)
        avg_corr, avg_vol = controls[state.end_index]
        win = rows[state.end_index - WINDOW_SIZE + 1:state.end_index + 1]
        phase = phase_diagnostics(win, symbols, state.beta1)
        beta0_history.append(state.beta0)
        fracture, beta0_delta, vol_z = fracture_alert(beta0_history, avg_vol, base)
        trend_streak = trend_streak + 1 if raw_level in {"yellow", "orange", "red"} else 0
        final_level = confirmed_level(raw_level, trend_streak, fracture)
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
            "top_defect_nodes": ";".join(f"{n}:{v:.6f}" for n, v in phase.top_defect_nodes),
            "top_risk_links": ";".join(f"{l}-{r}:{v:.6f}" for l, r, v in phase.top_risk_links),
            "drivers": ";".join(f"{n}:{v:.6f}" for n, v in state.drivers),
            "cycle_id": "",
            "cycle_status": "none",
        })

    cycles = annotate_warning_cycles(records)

    # === 输出最新状态 ===
    latest = records[-1]
    print(f"最新日期: {latest['date']}")
    print(f"当前拓扑状态:")
    print(f"  beta0 = {latest['beta0']} (连通分支数, >1 表示分裂)")
    print(f"  beta1 = {latest['beta1']} (循环秩, 反馈回路)")
    print(f"  S_topo = {float(latest['s_topo']):.4f} (拓扑稳定性)")
    print(f"  预警级别 = {latest['final_level']}")
    print(f"  phase_sync = {float(latest['phase_sync']):.4f}")
    print(f"  topo_potential = {float(latest['topo_potential']):.4f}")
    print()

    # === 行业确认（简化版） ===
    sector_warn_count = 0
    sector_total = 0
    for sector, sector_symbols_cfg in US_SECTOR_GROUPS.items():
        sector_symbols = [s for s in sector_symbols_cfg if s in symbols]
        if len(sector_symbols) < 3:
            continue
        sector_total += 1
        # 取最近 WINDOW_SIZE 条该行业数据
        sector_values = [{s: row[s] for s in sector_symbols} for row in rows[-(WINDOW_SIZE + 1):]]
        if len(sector_values) < WINDOW_SIZE + 1:
            continue
        # 简化: 计算行业平均波动率
        avg_vol_sector = sum(
            (sum((row[s] - sum(r[s] for r in sector_values[-WINDOW_SIZE:]) / WINDOW_SIZE) ** 2 for row in sector_values[-WINDOW_SIZE:]) / WINDOW_SIZE) ** 0.5
            for s in sector_symbols
        ) / len(sector_symbols)
        # 简化判断: 如果最近30天有任何 orange/red 记录就算 sector warn
        recent_30 = records[-30:]
        # 这里用整体 final_level 作为近似
        if any(r["final_level"] in {"orange", "red"} for r in recent_30):
            sector_warn_count += 1

    # === 前瞻评分 ===
    print(f"=== 拓扑风险前瞻 ===")
    # 用最新记录做前瞻
    forecast = forecast_from_record(latest, sector_warn_count, sector_total)
    print(f"当前快照前瞻:")
    print(f"  forecast_score = {float(forecast['score']):.3f}")
    print(f"  forecast_level = {forecast['level']}")
    print(f"  confidence = {float(forecast['confidence']):.3f}")
    print(f"  reason: {forecast['reason']}")
    print()

    # 扫描最近60天最佳前瞻
    recent_60 = records[-60:] if len(records) >= 60 else records
    best_score_60 = 0.0
    best_level_60 = "low"
    best_reason_60 = ""
    for r in recent_60:
        f = forecast_from_record(r, sector_warn_count, sector_total)
        if float(f["score"]) > best_score_60:
            best_score_60 = float(f["score"])
            best_level_60 = f["level"]
            best_reason_60 = f["reason"]

    print(f"最近60天窗口最佳前瞻:")
    print(f"  best_score = {best_score_60:.3f}")
    print(f"  best_level = {best_level_60}")
    print(f"  reason: {best_reason_60}")
    print()

    # === 近30天预警历史 ===
    recent_warns = [r for r in records[-60:] if r["final_level"] in {"orange", "red"}]
    print(f"=== 近60天预警统计 ===")
    print(f"  orange/red天数: {len(recent_warns)}")
    active_cycles = [c for c in cycles if not c["cancelled"]]
    print(f"  活跃预警周期: {len(active_cycles)}/{len(cycles)}")
    if active_cycles:
        last_cycle = active_cycles[-1]
        print(f"  最近周期: 起始={last_cycle['start']} 最高分={last_cycle['max_score']:.3f}")
    print()

    # === 综合判断 ===
    print(f"=== 综合风险前瞻判断 ===")
    if best_level_60 in {"elevated", "high"}:
        print(f"  ⚠ 60天窗口内曾达到 {best_level_60} 级别")
        print(f"  建议关注拓扑结构变化，可能存在系统性风险积累")
    elif best_level_60 == "watch":
        print(f"  △ 60天窗口最高达到 watch 级别")
        print(f"  市场拓扑结构有轻微变化，需持续监控")
    else:
        print(f"  ✓ 60天窗口内拓扑结构稳定 (level={best_level_60})")
        print(f"  当前市场状态空间连通性良好")
    print()
    print("注意: 这是拓扑结构风险前瞻，不是价格预测。")
    print("它评估的是市场状态空间是否正在发生结构性分裂。")


if __name__ == "__main__":
    main()
