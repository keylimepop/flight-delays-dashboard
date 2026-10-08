"""The analysis, in the order of the story:
  Q1 Where is delay born?    -> pareto_originating, pareto_by_dimension
  Q2 How far does it spread? -> propagation_by, hourly_profile
  Q3 Is the process stable?  -> sla_control_chart
  Q4 What should we fix?     -> simulate_cause_reduction, improvement_backlog
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_loader import CAUSE_LABELS, DELAY_CAUSES, ORIGINATING, SLA_THRESHOLD_MIN


# ------------------------------------------------------------- Headline KPIs
def kpis(df: pd.DataFrame) -> dict:
    completed = df[df["completed"]]
    originating = float(df["originating_delay"].sum())
    total = float(df["cause_delay_total"].sum())
    cause_minutes = df[ORIGINATING].sum()
    return {
        "total_flights": len(df),
        "completed_flights": len(completed),
        "sla_pass_rate": 1 - completed["sla_breach"].mean() if len(completed) else np.nan,
        "total_delay_minutes": total,
        "delay_multiplier": total / originating if originating else np.nan,
        "top_originating_cause": CAUSE_LABELS[cause_minutes.idxmax()] if originating else "None",
        "cancellation_rate": df["cancelled"].mean() if len(df) else np.nan,
    }


# ------------------------------------------------------------- Q1: where is delay born?
def pareto(series: pd.Series, threshold: float = 80.0) -> pd.DataFrame:
    """Sort biggest-first, add % of total and running %, flag the 'vital few'."""
    s = series.sort_values(ascending=False)
    total = s.sum()
    out = pd.DataFrame({"minutes": s})
    out["pct"] = out["minutes"] / total * 100 if total else 0.0
    out["cum_pct"] = out["pct"].cumsum()
    out["vital_few"] = out["cum_pct"].shift(fill_value=0) < threshold
    return out


def pareto_originating(df: pd.DataFrame) -> pd.DataFrame:
    return pareto(df[ORIGINATING].sum().rename(index=CAUSE_LABELS))


def pareto_all_causes(df: pd.DataFrame) -> pd.DataFrame:
    return pareto(df[DELAY_CAUSES].sum().rename(index=CAUSE_LABELS))


def pareto_by_dimension(df: pd.DataFrame, dim: str, col: str = "originating_delay") -> pd.DataFrame:
    return pareto(df.groupby(dim, observed=True)[col].sum())


# ------------------------------------------------------------- Q2: how far does it spread?
def propagation_by(df: pd.DataFrame, dim: str, min_flights: int = 1000) -> pd.DataFrame:
    """Delay multiplier = all delay minutes / originating minutes.
    2.0 means each newly created minute of delay became 2 minutes by the end of the day."""
    g = df.groupby(dim, observed=True).agg(
        flights=("completed", "size"),
        originating_min=("originating_delay", "sum"),
        late_aircraft_min=("late_aircraft_delay", "sum"),
    )
    g = g[(g["flights"] >= min_flights) & (g["originating_min"] > 0)].copy()
    total = g["originating_min"] + g["late_aircraft_min"]
    g["multiplier"] = total / g["originating_min"]
    g["late_share"] = g["late_aircraft_min"] / total
    return g.sort_values("multiplier", ascending=False)


def hourly_profile(df: pd.DataFrame, min_flights: int = 500) -> pd.DataFrame:
    """SLA breach rate and inherited vs new delay by scheduled departure hour."""
    c = df[df["completed"] & df["dep_hour"].notna()]
    h = c.groupby("dep_hour").agg(
        flights=("sla_breach", "size"),
        breach_rate=("sla_breach", "mean"),
        new_delay_per_flight=("originating_delay", "mean"),
        inherited_delay_per_flight=("late_aircraft_delay", "mean"),
    )
    return h[h["flights"] >= min_flights]


# ------------------------------------------------------------- Q3: is the process stable?
def sla_control_chart(df: pd.DataFrame) -> pd.DataFrame:
    """Daily breach rate with Laney p'-chart limits.

    A classic p-chart assumes the only randomness is coin-flip noise between flights. With
    ~20,000 flights a day that gives limits so tight that ordinary day-to-day swings (weekday
    vs weekend, normal weather) look "abnormal". Laney's p' chart measures how much days really
    vary from each other and widens the limits to match, so only genuinely unusual days get flagged.
    """
    c = df[df["completed"]]
    daily = c.groupby("fl_date")["sla_breach"].agg(n="size", breaches="sum")
    daily = daily[daily["n"] > 0]
    if daily.empty:
        return daily
    daily["p"] = daily["breaches"] / daily["n"]
    p_bar = daily["breaches"].sum() / daily["n"].sum()
    sigma_p = np.sqrt(p_bar * (1 - p_bar) / daily["n"])          # coin-flip noise only
    z = (daily["p"] - p_bar) / sigma_p
    sigma_z = z.diff().abs().mean() / 1.128 if len(daily) > 1 else 1.0  # real day-to-day variation
    sigma_z = 1.0 if not np.isfinite(sigma_z) else sigma_z
    daily["p_bar"] = p_bar
    daily["ucl"] = (p_bar + 3 * sigma_p * sigma_z).clip(upper=1)
    daily["lcl"] = (p_bar - 3 * sigma_p * sigma_z).clip(lower=0)
    daily["out_of_control"] = (daily["p"] > daily["ucl"]) | (daily["p"] < daily["lcl"])
    return daily


# ------------------------------------------------------------- Hub detail
def breach_heatmap(df: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    c = df[df["completed"] & df["dep_hour"].notna()]
    top = c["origin"].value_counts().head(top_n).index
    sub = c[c["origin"].isin(top)].copy()
    sub["origin"] = sub["origin"].astype(str)
    return sub.pivot_table(index="origin", columns="dep_hour", values="sla_breach",
                           aggfunc="mean").reindex(top.astype(str))


def taxi_friction(df: pd.DataFrame, baseline: float | None = None, min_flights: int = 500) -> pd.DataFrame:
    """Extra taxi-out minutes vs the network median = time wasted waiting on the tarmac."""
    d = df.dropna(subset=["taxi_out"])
    if d.empty:
        return pd.DataFrame()
    baseline = float(d["taxi_out"].median()) if baseline is None else baseline
    g = d.groupby("origin", observed=True)["taxi_out"].agg(flights="size", avg_taxi_out="mean")
    g = g[g["flights"] >= min_flights].copy()
    g["excess_per_flight"] = (g["avg_taxi_out"] - baseline).clip(lower=0)
    g["excess_taxi_minutes"] = g["excess_per_flight"] * g["flights"]
    g["network_baseline"] = baseline
    return g.sort_values("excess_taxi_minutes", ascending=False)


# ------------------------------------------------------------- Q4: what should we fix?
def simulate_cause_reduction(df: pd.DataFrame, cause: str, reduction: float) -> dict:
    """Remove `reduction` (0-1) of one cause's minutes and recompute the SLA pass rate.
    Assumes each removed minute comes straight off the arrival delay."""
    c = df[df["completed"]]
    n = len(c)
    if n == 0:
        return {"baseline_pass_rate": np.nan, "projected_pass_rate": np.nan, "flights_recovered": 0}
    base_breaches = int(c["sla_breach"].sum())
    new_breaches = int(((c["arr_delay"] - c[cause] * reduction) >= SLA_THRESHOLD_MIN).sum())
    return {
        "baseline_pass_rate": 1 - base_breaches / n,
        "projected_pass_rate": 1 - new_breaches / n,
        "flights_recovered": base_breaches - new_breaches,
    }


# Who would own each fix. No reduction is assumed: the backlog shows the payoff at several cut sizes.
PLAYBOOK = {
    "nas_delay": dict(fix="Spread out departure peaks", owner="Network Planning", control="Shared (FAA)"),
    "carrier_delay": dict(fix="Standardize turnaround tasks", owner="Ground Operations", control="Internal"),
    "late_aircraft_delay": dict(fix="Add buffer between flights", owner="Network Ops Control", control="Internal"),
    "weather_delay": dict(fix="Bad-weather and de-icing plans", owner="Ops Control Centre", control="External"),
    "security_delay": dict(fix="Checkpoint staffing", owner="Airport Partnerships", control="Shared (TSA)"),
}
CUT_LEVELS = [0.10, 0.20, 0.30, 0.40]


def improvement_backlog(df: pd.DataFrame) -> pd.DataFrame:
    """On-time gain (percentage points) if each cause were cut by 10/20/30/40%, sorted by impact."""
    rows = []
    for cause, play in PLAYBOOK.items():
        row = {
            "Fix": play["fix"],
            "Delay cause": CAUSE_LABELS[cause],
            "Owner": play["owner"],
            "Controllability": play["control"],
        }
        for lvl in CUT_LEVELS:
            sim = simulate_cause_reduction(df, cause, lvl)
            row[f"Gain at {lvl:.0%} cut (pp)"] = round(
                (sim["projected_pass_rate"] - sim["baseline_pass_rate"]) * 100, 2)
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("Gain at 20% cut (pp)", ascending=False)
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out


# ------------------------------------------------------------- One-paragraph story
CAUSE_DESCRIPTIONS = {
    "Airline issues": "airline issues (crew, maintenance, ground handling)",
    "Air traffic control": "air traffic control",
    "Weather": "weather",
    "Security": "security",
}


def executive_summary(df: pd.DataFrame) -> list[str]:
    """Plain-English findings for the current selection."""
    k = kpis(df)
    c = df[df["completed"]]
    if c.empty:
        return ["No completed flights in this selection."]
    po = pareto_originating(df)
    ap = pareto_by_dimension(df, "origin")
    n_vital, n_airports = int(ap["vital_few"].sum()), len(ap)
    late_share = df["late_aircraft_delay"].sum() / k["total_delay_minutes"] if k["total_delay_minutes"] else 0
    am = c.loc[c["dep_hour"].between(6, 9), "sla_breach"].mean()
    pm = c.loc[c["dep_hour"].between(17, 20), "sla_breach"].mean()
    cc = sla_control_chart(df)
    top = improvement_backlog(df).iloc[0]
    return [
        f"**{k['sla_pass_rate']:.1%}** of flights arrived on time.",
        f"**Origins of delay:** {CAUSE_DESCRIPTIONS.get(po.index[0], po.index[0])} cause "
        f"{po['pct'].iloc[0]:.0f}% of new delay, and {n_vital} of {n_airports} airports "
        f"({n_vital / n_airports:.0%}) create 80% of it.",
        f"**Knock-on delays:** {late_share:.0%} of all delay is passed on from a plane's previous flight. "
        f"Every new minute of delay grows to {k['delay_multiplier']:.2f} minutes.",
        f"**Crunch times of day:** {am:.0%} of 6–9am departures are late, versus {pm:.0%} of 5–8pm departures.",
        f"**Abnormal days:** {int(cc['out_of_control'].sum())} of {len(cc)} days were abnormally bad.",
        f"**Biggest lever:** {top['Delay cause']} delay. Cutting it by 10% would raise the on-time rate "
        f"by {top['Gain at 10% cut (pp)']:.1f} points, or by {top['Gain at 40% cut (pp)']:.1f} points at 40%.",
    ]
