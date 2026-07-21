# Topological Market Fracture: One-Page Story Brief

## One-line message

This prototype does not predict price direction; it detects whether the high-dimensional market return space is structurally splitting before or during a crisis.

## Why it matters

Traditional crisis indicators often strengthen after systemic co-movement becomes visible:

- `avg_corr` rises when assets start moving together.
- `avg_vol` rises when stress is already explicit.
- drawdown is visible after prices have already moved.

The topology signal asks a different question:

> Is the market state space losing connectivity before the crisis becomes obvious?

## Method in one slide

```text
Rolling stock returns
-> z-score state points
-> Vietoris-Rips graph proxy
-> beta0 / beta1 / S_topo
-> phase_sync / topo_potential / defect nodes
-> vulnerability flags / sector consistency
-> lightweight topology design suggestions
-> trend branch + fracture branch + cycle reset + sector confirmation + sector red candidate
-> green / yellow / orange / red warning level
```

## Crisis storyline

| Crisis | Type | Observed topology behavior | Current result | Sector confirmation |
|--------|------|----------------------------|----------------|---------------------|
| 2000 dotcom | slow bubble unwind | trend warnings accumulate before the crash window | first active warning: 2000-01-31, 39d lead | consumer/industrial near warning; financials near red |
| 2008 GFC | systemic financial crisis | topology turns red well before the Lehman event | first red: 2008-01-16, 243d lead | financials + technology near warning/red |
| 2011 euro debt | sovereign/financial stress | Europe proxy universe shows pre-crisis trend warning | first warning: 2011-01-18, 202d lead | technology/defensive near warning; financials near red |
| 2020 COVID | external shock | little pre-shock deformation; rapid post-shock fracture | fracture red: 2020-03-02, 11d after marker | broad all-sector confirmation |

## Key visual

Use this generated figure in presentations:

```text
data/multi_crisis/multi_crisis_level_timeline.png
```

What to point out:

1. 2008 shows sustained topology stress before the peak crisis marker.
2. 2011 euro debt adds a non-US proxy window, reducing the chance that the signal is US-only.
3. 2020 does not show meaningful pre-shock warning, but the fracture branch reacts quickly after the external shock.
4. Sector consistency distinguishes broad synchronized shocks, such as COVID, from narrower sector-confirmed stress episodes.
5. Phase diagnostics add a first-stage bridge to the Quantum Tide framing: phase synchrony measures risk co-movement, while defect nodes and risk links provide localization targets.
6. The red dotted line in the generated chart marks an experimental sector-escalated red candidate; it is an overlay, not a replacement for the main warning level.

## Strong claim to make

> Topological warning is not a price predictor. It is a structural risk detector: it searches for market-state fragmentation that may precede or accompany systemic stress.

## Claims not to make yet

Avoid saying:

- “This predicts financial crises.”
- “This is ready for live trading.”
- “This beats all traditional risk models.”

Say instead:

- “This is an exploratory topology-based risk prototype.”
- “The 2008 window shows topology stress earlier than traditional correlation/volatility spikes.”
- “More validation is required with larger universes, historical constituents, and true persistent homology.”

## Next milestone before public release

Expand validation beyond the current proxy sets and attach a short risk report to each crisis window:

```text
market-level warning
-> sector-level beta0
-> sector consistency score
-> phase_sync / topo_potential
-> top defect nodes / top risk links
-> lightweight design suggestions
-> cross-market crisis windows
-> larger historical universes
```

This turns the project from “interesting signal” into “interpretable cross-market risk research.”
