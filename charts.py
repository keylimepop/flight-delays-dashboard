"""Plotly chart builders shared by the notebook and the Streamlit app."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

NAVY, LIGHT, RED, GREY, AMBER = "#1f4e79", "#a9c1d9", "#d62728", "#7f7f7f", "#e8a33d"


def _legend_top(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(title=title, legend=dict(orientation="h", y=1.12), margin=dict(t=80))
    return fig


def pareto_fig(p: pd.DataFrame, title: str, x_title: str) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=p.index.astype(str), y=p["minutes"], name="Delay minutes",
                marker_color=[NAVY if v else LIGHT for v in p["vital_few"]], secondary_y=False)
    fig.add_scatter(x=p.index.astype(str), y=p["cum_pct"], name="Cumulative %", mode="lines+markers",
                    line=dict(color=RED, width=2), secondary_y=True)
    fig.add_shape(type="line", xref="x", x0=-0.5, x1=len(p) - 0.5, yref="y2", y0=80, y1=80,
                  line=dict(color=GREY, dash="dash"))
    fig.update_xaxes(title_text=x_title)
    fig.update_yaxes(title_text="Delay minutes", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative %", range=[0, 105], tickmode="array",
                     tickvals=[0, 20, 40, 60, 80, 100], ticksuffix="%", showgrid=False, secondary_y=True)
    fig.update_layout(bargap=0.4)
    return _legend_top(fig, title)


def propagation_fig(prop: pd.DataFrame, label: str) -> go.Figure:
    d = prop.reset_index()
    d[d.columns[0]] = d[d.columns[0]].astype(str)
    fig = px.bar(d, x=d.columns[0], y="multiplier", color_discrete_sequence=[NAVY],
                 hover_data={"flights": ":,", "late_share": ":.0%"},
                 labels={d.columns[0]: label, "multiplier": "Delay multiplier (x)",
                         "late_share": "Share of delay passed on"})
    fig.add_hline(y=1, line_dash="dash", line_color=GREY)
    return _legend_top(fig, f"Delay multiplier by {label.lower()}: how much each new minute of delay grows")


def hourly_fig(h: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    x = h.index.astype(int)
    fig.add_bar(x=x, y=h["new_delay_per_flight"], name="New delay (min/flight)", marker_color=NAVY,
                secondary_y=False)
    fig.add_bar(x=x, y=h["inherited_delay_per_flight"], name="Knock-on delay (min/flight)",
                marker_color=AMBER, secondary_y=False)
    fig.add_scatter(x=x, y=h["breach_rate"] * 100, name="Flights late (%)", mode="lines+markers",
                    line=dict(color=RED, width=2), secondary_y=True)
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title_text="Scheduled departure hour", dtick=1)
    fig.update_yaxes(title_text="Minutes per flight", secondary_y=False)
    fig.update_yaxes(title_text="Flights late", ticksuffix="%", rangemode="tozero", secondary_y=True)
    return _legend_top(fig, "Knock-on delay builds up through the day")


def control_chart_fig(cc: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=cc.index, y=cc["p"] * 100, mode="lines+markers", name="Flights late that day",
                    line=dict(color=NAVY), marker=dict(size=4))
    fig.add_scatter(x=cc.index, y=cc["p_bar"] * 100, mode="lines", name="Average", line=dict(color=GREY))
    for lim, name in (("ucl", "Upper limit"), ("lcl", "Lower limit")):
        fig.add_scatter(x=cc.index, y=cc[lim] * 100, mode="lines", name=name,
                        line=dict(color=RED, dash="dash", shape="hv"))
    ooc = cc[cc["out_of_control"]]
    fig.add_scatter(x=ooc.index, y=ooc["p"] * 100, mode="markers", name="Abnormal day",
                    marker=dict(color=RED, size=10, symbol="x"))
    fig.update_yaxes(title_text="Flights late (%)")
    return _legend_top(fig, "Share of flights late each day, with normal range")


def heatmap_fig(hm: pd.DataFrame) -> go.Figure:
    fig = px.imshow(hm * 100, aspect="auto", color_continuous_scale="Reds",
                    labels=dict(x="Scheduled departure hour", y="Origin", color="% late"))
    return _legend_top(fig, "Share of late flights by airport and hour (15 busiest airports)")


def taxi_fig(tf: pd.DataFrame) -> go.Figure:
    top = tf.head(15).reset_index()
    top["origin"] = top["origin"].astype(str)
    fig = px.bar(top, x="excess_taxi_minutes", y="origin", orientation="h", color_discrete_sequence=[NAVY],
                 hover_data={"flights": ":,", "avg_taxi_out": ":.1f"},
                 labels={"excess_taxi_minutes": "Total extra waiting minutes vs a typical airport", "origin": "Origin"})
    fig.update_yaxes(autorange="reversed")
    return _legend_top(fig, f"Extra time between gate and takeoff (typical = {tf['network_baseline'].iloc[0]:.0f} min)")
