"""Plots from the existing rule grid, without reading inputs or printing results.

Dual fractions are taken from the already merged event union. Never add the
fractions of the high/short and lower/long branches: they may overlap.
"""

import math


def dual_heatmap_data(grid):
    dual = [row for row in grid if row["method"] == "dual"]
    pair_key = lambda r: (r["lower_threshold"], r["upper_threshold"],
                          r["lower_quantile"], r["upper_quantile"])
    pairs = sorted({pair_key(r) for r in dual},
        key=lambda p: (p[0], p[1], -1 if p[2] is None else p[2], -1 if p[3] is None else p[3]))
    durations = sorted({(r["min_duration_s"], r["long_duration_s"]) for r in dual})
    panels = {}
    for width in (2, 4):
        lookup = {}
        for row in (r for r in dual if r["width_s"] == width):
            key = pair_key(row), (row["min_duration_s"], row["long_duration_s"])
            value = row["candidate_fraction_valid"]
            if key in lookup or (value is not None and (not math.isfinite(value) or not 0 <= value <= 1)):
                raise ValueError("Invalid or duplicated dual plot cell.")
            lookup[key] = row
        panels[width] = [[lookup.get((pair, duration)) for duration in durations] for pair in pairs]
    return {"pairs": pairs, "durations": durations, "panels": panels}


def _pair_label(pair):
    low, high, low_q, high_q = pair
    label = lambda value, q: f"{value:.3g}" + (f" (q{q * 100:.3g}%)" if q is not None else "")
    return f"Low {label(low, low_q)} | High {label(high, high_q)}"


def dual_heatmap_figure(grid, *, fake=False):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    data = dual_heatmap_data(grid)
    figure = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=.09,
                          subplot_titles=["Dual rule: 2s windows", "Dual rule: 4s windows"])
    tag = "SYNTHETIC / NOT RESEARCH RESULTS" if fake else "LOCAL EXPLORATION / NOT VALIDATED SYNCHRONY"
    if not data["pairs"]:
        figure.add_annotation(text="No ordered low/high threshold pairs with valid short/long durations.",
                              x=.5, y=.5, xref="paper", yref="paper", showarrow=False)
        figure.update_layout(title=f"{tag}<br>Dual parameter scan: no valid combinations", height=430)
        return figure
    for column, width in enumerate((2, 4), 1):
        cells = data["panels"][width]
        z = [[None if row is None else row["candidate_fraction_valid"] for row in line] for line in cells]
        details = [[None if r is None else [r["lower_threshold"], r["upper_threshold"],
            r["min_duration_s"], r["long_duration_s"], r["effective_min_duration_s"],
            r["effective_long_duration_s"], r["n_events"], r["total_candidate_s"], r["valid_s"]]
            for r in line] for line in cells]
        figure.add_trace(go.Heatmap(x=list(range(len(data["durations"]))), y=list(range(len(data["pairs"]))),
            z=z, customdata=details, coloraxis="coloraxis", hoverongaps=False,
            hovertemplate=(f"{width}s averaging windows<br>"
                "Low threshold: %{customdata[0]:.6g}<br>High threshold: %{customdata[1]:.6g}<br>"
                "High/short requested: %{customdata[2]:g}s; effective: %{customdata[4]:g}s<br>"
                "Low/long requested: %{customdata[3]:g}s; effective: %{customdata[5]:g}s<br>"
                "Merged candidate events: %{customdata[6]:g}<br>"
                "Merged candidate time: %{customdata[7]:g}s / valid time: %{customdata[8]:g}s<br>"
                "Fraction of valid time selected: %{z:.1%}<extra></extra>")), row=1, col=column)
        figure.update_xaxes(tickmode="array", tickvals=list(range(len(data["durations"]))),
            ticktext=[f"High ≥{short:g}s<br>OR Low ≥{long:g}s" for short, long in data["durations"]],
            title_text="Requested duration pair", row=1, col=column)
        figure.update_yaxes(tickmode="array", tickvals=list(range(len(data["pairs"]))),
            ticktext=[_pair_label(pair) for pair in data["pairs"]], row=1, col=column)
    figure.update_yaxes(title_text="Energy threshold pair", row=1, col=1)
    figure.update_layout(title=f"{tag}<br>DUAL: fraction of valid time selected — not accuracy",
        height=max(540, 55 * len(data["pairs"]) + 230), margin={"l": 245, "r": 85, "b": 110, "t": 110},
        coloraxis={"colorscale": "Viridis", "cmin": 0, "cmax": 1,
                   "colorbar": {"title": "Fraction", "tickformat": ".0%"}})
    return figure


def save_dual_heatmap(path, grid, *, fake=False):
    figure = dual_heatmap_figure(grid, fake=fake)
    html = figure.to_html(full_html=True, include_plotlyjs=True,
                          config={"responsive": True, "displaylogo": False})
    note = ('<div style="font-family:Arial,sans-serif;max-width:1300px;margin:20px auto;line-height:1.6">'
        '<h2>Dual parameter heatmaps / 双门槛参数热力图</h2>'
        '<p><b>Rule:</b> (mean energy ≥ HIGH for SHORT duration) OR '
        '(mean energy ≥ LOW for LONG duration).</p>'
        '<p>纵轴：一对低、高能量门槛。横轴：一对高能量短时长、低能量长时长。'
        '每格使用全部四个参数；左右分别为2秒、4秒平均。q表示原能量曲线的分位位置，不是能量值。</p>'
        '<p><b>Color:</b> merged candidate time / valid time. Bright = more time selected, not better accuracy. '
        'Overlapping branches count once. Blank = no evaluated rule or no valid time.</p>'
        '<p>颜色只表示合并后的候选时间占有效时间的比例，重叠部分只算一次，不是准确率。'
        '鼠标停在格子上可查看四个参数、实际量化时长、候选数和有效时间。'
        '4秒窗的最短候选长度至少4秒，因此请求的2秒条件在该窗下实际为4秒。</p></div>')
    html = html.replace("<body>", "<body>" + note, 1)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(html)
