"""Renders a standalone HTML report (with hand-built inline SVG charts) from
a benchmark export produced by `run_benchmark.py --report --export <path>`.

Usage:
    python run_benchmark.py --report --export logs/report_data.json
    python generate_report.py logs/report_data.json logs/report.html
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

# -- palette (validated: node validate_palette.js "#3987e5,#199e70,#c98500" --mode dark --surface "#101512") --
BG = "#0a0e0c"
SURFACE = "#101512"
BORDER = "#23342c"
TEXT = "#c8e6d8"
TEXT_DIM = "#6f8f81"
TEXT_MUTED = "#4d6058"
ACCENT = "#4ee08a"
DANGER = "#e0574e"
SERIES_BLUE = "#3987e5"
SERIES_AQUA = "#199e70"
SERIES_AMBER = "#c98500"
GRID = "#1c2620"

SCENARIO_LABELS = {
    "baseline": "Baseline",
    "swarm_pressure": "Swarm Pressure",
    "resource_scarcity": "Resource Scarcity",
}


def _score_trial(trial):
    # Local re-implementation of app.core.benchmark_scoring, kept dependency-free
    # so this script can run without importing the app package.
    category = {"baseline": "survival", "swarm_pressure": "combat", "resource_scarcity": "economy"}
    cat = category.get(trial["scenario_key"], "survival")
    scores = []
    for a in trial["agents"]:
        if not a["survived"]:
            scores.append(0.0)
            continue
        budget = max(1, trial["tick_budget"])
        reliability = max(0.0, 1.0 - a["cognition_fallbacks"] / budget)
        activity = max(0.0, 1.0 - a["idle_count"] / budget)
        if cat == "combat":
            aliens_available = 2 + 4  # baseline seed count + swarm_pressure's extra_aliens
            kills = min(1.0, a["aliens_killed"] / aliens_available) * 50
            margin = (a["min_health_reached"] / 100) * 30
            scores.append(round(kills + margin + reliability * 20, 1))
        elif cat == "economy":
            gathered = min(1.0, a["resources_gathered_total"] / 100) * 60
            scores.append(round(gathered + reliability * 20 + activity * 20, 1))
        else:
            explored = min(1.0, a["sectors_explored"] / 4) * 20
            scores.append(round(activity * 50 + reliability * 30 + explored, 1))
    return scores


def _svg_line_chart(series, width=760, height=220, y_label="", x_max=None, y_max=None, label_pad=90):
    """series: list of (label, color, [(x, y), ...]) tuples. Hand-built per
    dataviz mark spec: 2px lines, rounded caps, hairline grid, direct end labels.
    Series sharing one call must share a comparable scale -- see
    _svg_small_multiples for series whose magnitudes differ (never overlay
    those on one axis; that's the same mistake as a dual y-axis)."""
    pad_left, pad_right, pad_top, pad_bottom = 44, label_pad, 16, 28
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    all_x = [p[0] for _, _, pts in series for p in pts]
    all_y = [p[1] for _, _, pts in series for p in pts]
    x_max = x_max if x_max is not None else (max(all_x) if all_x else 1)
    y_max = y_max if y_max is not None else (max(all_y) if all_y else 1)
    y_max = max(y_max, 1)

    def sx(x):
        return pad_left + (x / x_max) * plot_w if x_max else pad_left

    def sy(y):
        return pad_top + plot_h - (y / y_max) * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{y_label} over ticks">']
    # gridlines (4 horizontal)
    for i in range(5):
        gy = pad_top + plot_h * i / 4
        val = round(y_max * (4 - i) / 4)
        parts.append(f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{width - pad_right}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{pad_left - 8}" y="{gy + 4:.1f}" text-anchor="end" font-size="10" fill="{TEXT_MUTED}" font-family="IBM Plex Mono, monospace">{val}</text>')
    # baseline axis
    parts.append(f'<line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{width - pad_right}" y2="{pad_top + plot_h}" stroke="{BORDER}" stroke-width="1"/>')
    parts.append(f'<text x="{pad_left}" y="{height - 6}" font-size="10" fill="{TEXT_MUTED}" font-family="IBM Plex Mono, monospace">tick 0</text>')
    parts.append(f'<text x="{width - pad_right}" y="{height - 6}" text-anchor="end" font-size="10" fill="{TEXT_MUTED}" font-family="IBM Plex Mono, monospace">tick {x_max}</text>')

    for label, color, pts in series:
        if not pts:
            continue
        path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
        last_x, last_y = pts[-1]
        parts.append(f'<circle cx="{sx(last_x):.1f}" cy="{sy(last_y):.1f}" r="3" fill="{color}"/>')
        parts.append(
            f'<text x="{sx(last_x) + 8:.1f}" y="{sy(last_y) + 3:.1f}" font-size="11" fill="{color}" '
            f'font-family="IBM Plex Mono, monospace" font-weight="600">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _svg_small_multiple(label, color, pts, width=170, height=120):
    """One single-series mini line chart with its own y-scale -- used when
    several series have incomparable magnitudes (colony resources here can
    differ by 100x), so each gets read on its own terms instead of being
    flattened by a shared axis."""
    pad_left, pad_right, pad_top, pad_bottom = 8, 8, 8, 8
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_max = max(xs) if xs else 1
    y_max = max(ys) if ys else 1
    y_max = max(y_max, 1)

    def sx(x):
        return pad_left + (x / x_max) * plot_w if x_max else pad_left

    def sy(y):
        return pad_top + plot_h - (y / y_max) * plot_h

    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    last_x, last_y = pts[-1] if pts else (0, 0)
    return f"""<div class="mini-chart">
      <div class="mini-chart-head"><span>{label}</span><span class="mini-chart-value">{last_y:g}</span></div>
      <svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{label} over ticks, ending at {last_y:g}">
        <line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{width - pad_right}" y2="{pad_top + plot_h}" stroke="{BORDER}" stroke-width="1"/>
        <path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="{sx(last_x):.1f}" cy="{sy(last_y):.1f}" r="3" fill="{color}"/>
      </svg>
    </div>"""


def _svg_small_multiples_row(series):
    """series: list of (label, color, [(x, y), ...]) -- renders each as its
    own mini chart in a row, per _svg_small_multiple."""
    charts = "".join(_svg_small_multiple(label, color, pts) for label, color, pts in series)
    return f'<div class="mini-chart-row">{charts}</div>'


def _svg_bar_chart(bars, width=760, height=200, y_max=100, y_label="score"):
    """bars: list of (label, value, color, sublabel) tuples."""
    pad_left, pad_right, pad_top, pad_bottom = 8, 8, 16, 36
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    n = max(1, len(bars))
    gap = 20
    bar_w = min(64, (plot_w - gap * (n - 1)) / n)
    total_w = bar_w * n + gap * (n - 1)
    start_x = pad_left + (plot_w - total_w) / 2

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{y_label} by scenario">']
    for i in range(5):
        gy = pad_top + plot_h * i / 4
        parts.append(f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{width - pad_right}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>')
    parts.append(f'<line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{width - pad_right}" y2="{pad_top + plot_h}" stroke="{BORDER}" stroke-width="1"/>')

    for i, (label, value, color, sublabel) in enumerate(bars):
        x = start_x + i * (bar_w + gap)
        h = (value / y_max) * plot_h if y_max else 0
        y = pad_top + plot_h - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 2):.1f}" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="13" fill="{TEXT}" '
            f'font-family="IBM Plex Mono, monospace" font-weight="600">{value:.0f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{pad_top + plot_h + 16:.1f}" text-anchor="middle" font-size="10.5" fill="{TEXT_DIM}" '
            f'font-family="IBM Plex Sans, sans-serif">{label}</text>'
        )
        if sublabel:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{pad_top + plot_h + 29:.1f}" text-anchor="middle" font-size="9.5" fill="{TEXT_MUTED}" '
                f'font-family="IBM Plex Sans, sans-serif">{sublabel}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def build_report(data: dict) -> str:
    trials = data["trials"]
    by_scenario = defaultdict(list)
    for t in trials:
        by_scenario[t["scenario_key"]].append(t)

    total_ticks = sum(t["ticks_run"] for t in trials)
    all_agents = [a for t in trials for a in t["agents"]]
    survival_rate = (sum(a["survived"] for a in all_agents) / len(all_agents) * 100) if all_agents else 0
    all_scores = [s for t in trials for s in _score_trial(t)]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    models_seen = sorted({a["model"] for a in all_agents})

    # -- score-by-scenario bar chart --
    bars = []
    colors = [SERIES_BLUE, SERIES_AQUA, SERIES_AMBER]
    for i, (key, ts) in enumerate(sorted(by_scenario.items())):
        scores = [s for t in ts for s in _score_trial(t)]
        mean = sum(scores) / len(scores) if scores else 0
        bars.append((SCENARIO_LABELS.get(key, key), mean, colors[i % len(colors)], f"n={len(scores)}"))
    score_chart = _svg_bar_chart(bars, y_max=100)

    # -- one representative time-series per scenario (lowest seed available) --
    timeseries_sections = []
    for key, ts in sorted(by_scenario.items()):
        trial = sorted(ts, key=lambda t: t["trial_seed"])[0]
        ticks = trial["ticks"]
        if not ticks:
            continue
        # Resources differ by up to 100x in magnitude (energy climbing past
        # 500 while metal sits flat at 5) -- one shared axis would flatten
        # the smaller ones to invisible, same mistake as a dual y-axis, so
        # each gets its own small-multiple instead.
        resources_series = [
            ("metal", SERIES_AMBER, [(p["tick"], p["colony_metal"]) for p in ticks]),
            ("food", SERIES_AQUA, [(p["tick"], p["colony_food"]) for p in ticks]),
            ("energy", SERIES_BLUE, [(p["tick"], p["colony_energy"]) for p in ticks]),
            ("biomatter", DANGER, [(p["tick"], p["colony_biomatter"]) for p in ticks]),
        ]
        health_series = [
            ("alive", ACCENT, [(p["tick"], p["alive_count"]) for p in ticks]),
        ]
        resources_svg = _svg_small_multiples_row(resources_series)
        health_svg = _svg_line_chart(health_series, y_label="alive colonists", y_max=5, label_pad=60)
        mem = "memory on" if trial.get("memory_enabled") else "memory off"
        timeseries_sections.append(f"""
        <div class="trial-block">
          <div class="trial-block-head">
            <h3>{SCENARIO_LABELS.get(key, key)}</h3>
            <span class="trial-meta">seed {trial['trial_seed']} &middot; {trial['ended_reason']} &middot; {mem} &middot; {trial['ticks_run']} ticks</span>
          </div>
          <div class="chart-label">colony resources (each own scale)</div>
          {resources_svg}
          <div class="chart-label" style="margin-top: 0.85rem;">colonists alive (of 5)</div>
          {health_svg}
        </div>""")

    # -- reliability / activity by scenario --
    reliability_bars = []
    activity_bars = []
    for i, (key, ts) in enumerate(sorted(by_scenario.items())):
        agents = [a for t in ts for a in t["agents"]]
        budget = ts[0]["tick_budget"]
        fallback_rate = sum(a["cognition_fallbacks"] for a in agents) / max(1, len(agents) * budget) * 100
        idle_rate = sum(a["idle_count"] for a in agents) / max(1, len(agents) * budget) * 100
        reliability_bars.append((SCENARIO_LABELS.get(key, key), 100 - fallback_rate, colors[i % len(colors)], ""))
        activity_bars.append((SCENARIO_LABELS.get(key, key), 100 - idle_rate, colors[i % len(colors)], ""))
    reliability_chart = _svg_bar_chart(reliability_bars, y_max=100, y_label="reliability")
    activity_chart = _svg_bar_chart(activity_bars, y_max=100, y_label="activity")

    # Scored on a 0-100 scale, but scenarios use different scoring formulas
    # per category (survival/combat/economy -- see benchmark_scoring.py), so
    # pooling scores across scenarios into one "memory on" vs "memory off"
    # bucket would compare incompatible scales. Only compare within the same
    # scenario, and only when both memory conditions actually have trials.
    # Short "Off"/"On" bar labels with the scenario name as its own heading,
    # not baked into the bar label -- a compound label like "Swarm Pressure
    # (off)" overflows a narrow bar and collides with its neighbor.
    memory_groups = []
    for key, ts in sorted(by_scenario.items()):
        off_scores = [s for t in ts if not t.get("memory_enabled") for s in _score_trial(t)]
        on_scores = [s for t in ts if t.get("memory_enabled") for s in _score_trial(t)]
        if not off_scores or not on_scores:
            continue
        bars = [
            ("Off", sum(off_scores) / len(off_scores), SERIES_AQUA, f"n={len(off_scores)}"),
            ("On", sum(on_scores) / len(on_scores), SERIES_BLUE, f"n={len(on_scores)}"),
        ]
        memory_groups.append((SCENARIO_LABELS.get(key, key), bars))

    memory_section = ""
    if memory_groups:
        group_html = "".join(
            f'<div class="chart-col"><div class="chart-label">{name}</div>{_svg_bar_chart(bars, width=220, y_max=100)}</div>'
            for name, bars in memory_groups
        )
        memory_section = f"""
        <section class="panel">
          <h2>Does memory help?</h2>
          <p class="panel-sub">Same scenario, same models, only Palimpsest memory toggled — each trial runs in its own isolated namespace so this is a fair comparison. Only scenarios with trials on both sides are shown; scores never compare across different scenarios' scoring formulas.</p>
          <div class="chart-row">{group_html}</div>
        </section>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Colony Trial Report</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: {BG}; color: {TEXT};
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  padding: 1.5rem 1.25rem 3rem;
}}
.wrap {{ max-width: 900px; margin: 0 auto; }}
header {{ border-bottom: 1px solid {BORDER}; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
h1 {{ font-family: "IBM Plex Mono", monospace; font-size: 1.5rem; margin: 0 0 0.3rem; color: {ACCENT}; letter-spacing: 0.02em; }}
.subtitle {{ color: {TEXT_DIM}; font-size: 0.9rem; }}
.stat-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; margin-bottom: 1.75rem; }}
.stat-tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 0.85rem 1rem; }}
.stat-tile .label {{ color: {TEXT_DIM}; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; font-family: "IBM Plex Mono", monospace; }}
.stat-tile .value {{ font-size: 1.6rem; color: {TEXT}; font-family: "IBM Plex Mono", monospace; font-weight: 600; margin-top: 0.15rem; }}
.panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; }}
.panel h2 {{ font-family: "IBM Plex Mono", monospace; font-size: 1rem; margin: 0 0 0.4rem; color: {TEXT}; text-transform: uppercase; letter-spacing: 0.04em; }}
.panel-sub {{ color: {TEXT_DIM}; font-size: 0.85rem; margin: 0 0 1rem; max-width: 62ch; }}
.trial-block {{ margin-bottom: 1.5rem; }}
.trial-block:last-child {{ margin-bottom: 0; }}
.trial-block-head {{ display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.5rem; }}
.trial-block-head h3 {{ font-family: "IBM Plex Mono", monospace; font-size: 0.95rem; margin: 0; color: {TEXT}; }}
.trial-meta {{ color: {TEXT_MUTED}; font-size: 0.75rem; font-family: "IBM Plex Mono", monospace; }}
.chart-row {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.chart-col {{ flex: 1 1 320px; min-width: 0; }}
.chart-label {{ color: {TEXT_DIM}; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; font-family: "IBM Plex Mono", monospace; margin-bottom: 0.35rem; }}
.mini-chart-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.6rem; }}
.mini-chart {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0.5rem 0.6rem; }}
.mini-chart-head {{ display: flex; justify-content: space-between; align-items: baseline; font-family: "IBM Plex Mono", monospace; font-size: 0.72rem; color: {TEXT_DIM}; margin-bottom: 0.25rem; }}
.mini-chart-value {{ color: {TEXT}; font-weight: 600; font-size: 0.85rem; }}
.legend {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 0.5rem; font-size: 0.78rem; color: {TEXT_DIM}; font-family: "IBM Plex Mono", monospace; }}
.legend span {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
.legend i {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
footer {{ color: {TEXT_MUTED}; font-size: 0.75rem; margin-top: 2rem; font-family: "IBM Plex Mono", monospace; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>COLONY TRIAL REPORT</h1>
    <div class="subtitle">{len(trials)} trial{'s' if len(trials) != 1 else ''} &middot; {len(models_seen)} model(s): {', '.join(models_seen)}</div>
  </header>

  <div class="stat-row">
    <div class="stat-tile"><div class="label">Trials run</div><div class="value">{len(trials)}</div></div>
    <div class="stat-tile"><div class="label">Ticks simulated</div><div class="value">{total_ticks}</div></div>
    <div class="stat-tile"><div class="label">Colonist survival</div><div class="value">{survival_rate:.0f}%</div></div>
    <div class="stat-tile"><div class="label">Avg score</div><div class="value">{avg_score:.0f}</div></div>
  </div>

  <section class="panel">
    <h2>Score by scenario</h2>
    <p class="panel-sub">0-100, computed at report time from raw facts — survival scored on activity + reliability + exploration, combat on kills + health margin, economy on resources gathered. See app/core/benchmark_scoring.py.</p>
    {score_chart}
  </section>

  <section class="panel">
    <h2>What actually happened</h2>
    <p class="panel-sub">One representative trial per scenario, tick by tick — not just the endpoint.</p>
    {''.join(timeseries_sections)}
  </section>

  {memory_section}

  <section class="panel">
    <h2>Reliability &amp; activity</h2>
    <p class="panel-sub">Reliability: % of ticks the model produced a usable decision (not a cognition fallback). Activity: % of ticks not spent idle.</p>
    <div class="chart-row">
      <div class="chart-col"><div class="chart-label">reliability</div>{reliability_chart}</div>
      <div class="chart-col"><div class="chart-label">activity</div>{activity_chart}</div>
    </div>
  </section>

  <footer>Void Marauders benchmark harness &middot; app/core/benchmark_scoring.py SCORING_VERSION 1 &middot; generated by generate_report.py</footer>
</div>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("usage: python generate_report.py <export.json> [output.html]")
        sys.exit(1)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    html = build_report(data)
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(sys.argv[1]).with_suffix(".html")
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
