"""Flight Delay Dashboard."""
import pandas as pd
import streamlit as st

import charts
import opex_engine as oe
from data_loader import CAUSE_LABELS, DELAY_CAUSES, SLA_THRESHOLD_MIN, load_flights

st.set_page_config(page_title="Flight Delay Dashboard", layout="wide")
st.markdown(
    "<style>.block-container, [data-testid='stMainBlockContainer'] {max-width: 1200px;}</style>",
    unsafe_allow_html=True,
)
DEFAULT_DATA = "data/flights_sample.parquet"


@st.cache_data(show_spinner="Loading flight data...")
def get_data(source):
    return load_flights(source)


# ------------------------------------------------------------ Data + filters
upload = st.sidebar.file_uploader("Upload your own BTS CSV (optional)", type="csv")
try:
    df = get_data(upload if upload is not None else DEFAULT_DATA)
except FileNotFoundError:
    st.error(f"No data at `{DEFAULT_DATA}`. Run `python prepare_data.py data/raw/YOUR_FILE.csv` first.")
    st.stop()
except ValueError as err:
    st.error(str(err))
    st.stop()

st.sidebar.header("Filters")
carriers = st.sidebar.multiselect("Airline", sorted(df["op_unique_carrier"].dropna().astype(str).unique()))
origins = st.sidebar.multiselect("Departure airport", df["origin"].value_counts().index.astype(str).tolist(),
                                 help="Busiest airports first")
dmin, dmax = df["fl_date"].min().date(), df["fl_date"].max().date()
dates = st.sidebar.date_input("Date range", (dmin, dmax), min_value=dmin, max_value=dmax)
st.sidebar.caption("Leave a filter empty to include everything.")
if upload is None:
    st.sidebar.caption("This demo uses a random 10% of the data. Percentages hold up; "
                       "flight counts are about a tenth of the real totals.")

fdf = df
if carriers:
    fdf = fdf[fdf["op_unique_carrier"].isin(carriers)]
if origins:
    fdf = fdf[fdf["origin"].isin(origins)]
if isinstance(dates, tuple) and len(dates) == 2:
    fdf = fdf[(fdf["fl_date"] >= pd.Timestamp(dates[0])) & (fdf["fl_date"] <= pd.Timestamp(dates[1]))]
if fdf.empty:
    st.warning("No flights match these filters. Try widening the airline, airport or date selection.")
    st.stop()

# ------------------------------------------------------------ Header
st.title("Flight Delay Dashboard")
st.caption(f"2024 U.S. domestic flights. A flight counts as on time if it lands less than {SLA_THRESHOLD_MIN} "
           "minutes late (the U.S. DOT definition). Cancelled and diverted flights aren't counted in the on-time rate.")

k = oe.kpis(fdf)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Flights", f"{k['total_flights']:,}")
c2.metric("On-time rate", f"{k['sla_pass_rate']:.1%}")
c3.metric("Top source of new delay", k["top_originating_cause"])
c4.metric("Delay multiplier", f"{k['delay_multiplier']:.2f}x",
          help="Total delay minutes ÷ new delay minutes. 1.68x means each new minute of delay "
               "grows to 1.68 minutes as planes run late on later flights.")
c5.metric("Cancellation rate", f"{k['cancellation_rate']:.1%}")

with st.expander("Summary", expanded=True):
    for line in oe.executive_summary(fdf):
        st.markdown(f"- {line}")

t1, t2, t3, t4, t5 = st.tabs(["1. Origins of delay", "2. Knock-on delays", "3. Abnormal days",
                              "4. Runway delays", "5. Potential fixes"])

# ------------------------------------------------------------ 1. Origins of delay
with t1:
    st.markdown("Airlines report the cause of every flight that's 15+ minutes late. \"Late aircraft\" "
                "(the plane arrived late from its previous flight) is excluded here, because it's a "
                "knock-on effect rather than where the delay started.")
    st.plotly_chart(charts.pareto_fig(oe.pareto_originating(fdf), "New delay minutes by cause", "Cause"))
    dims = {"origin": "Departure airport", "op_unique_carrier": "Airline", "dest": "Arrival airport"}
    dim = st.selectbox("Break new delay down by", list(dims), format_func=dims.get)
    dp = oe.pareto_by_dimension(fdf, dim)
    if dp["minutes"].sum() > 0:
        nv = int(dp["vital_few"].sum())
        st.markdown(f"**{nv} of {len(dp)} ({nv / len(dp):.0%})** create 80% of new delay minutes.")
        st.plotly_chart(charts.pareto_fig(dp.head(25), f"Top 25: {dims[dim]}", dims[dim]))
    with st.expander("Compare: all 5 causes, including late aircraft"):
        st.plotly_chart(charts.pareto_fig(oe.pareto_all_causes(fdf), "All delay minutes by cause", "Cause"))

# ------------------------------------------------------------ 2. Knock-on delays
with t2:
    st.markdown("When a plane is late, its next flight usually is too. These charts show how much "
                "delay gets passed on, and when.")
    st.plotly_chart(charts.hourly_fig(oe.hourly_profile(fdf)))
    st.caption("Amber = knock-on delay passed on from the plane's previous flight. It builds up through the day.")
    prop = oe.propagation_by(fdf, "op_unique_carrier")
    if not prop.empty:
        st.plotly_chart(charts.propagation_fig(prop, "Airline"))
        st.caption("Differences may come from how much spare time each airline leaves between flights, "
                   "as well as route networks and how airlines label their delays.")

# ------------------------------------------------------------ 3. Abnormal days
with t3:
    cc = oe.sla_control_chart(fdf)
    if len(cc) < 5:
        st.info("This chart needs at least 5 days of data. Try widening the date range.")
    else:
        st.plotly_chart(charts.control_chart_fig(cc))
        st.caption("Each dot is one day. Days outside the dashed lines had abnormally high (or low) levels of "
                   "delay; everything inside is normal day-to-day variation.")
        ooc = cc[cc["out_of_control"] & (cc["p"] > cc["p_bar"])].sort_values("p", ascending=False)
        if not ooc.empty:
            show = ooc[["n", "p"]].head(10).rename(columns={"n": "Flights", "p": "Flights late"})
            show.index = show.index.date
            st.markdown("**Worst days**")
            st.dataframe(show.style.format({"Flights late": "{:.1%}", "Flights": "{:,}"}))

# ------------------------------------------------------------ 4. Runway delays
with t4:
    tf = oe.taxi_friction(fdf, baseline=float(df["taxi_out"].median()), min_flights=200)
    if not tf.empty:
        st.markdown(f"The typical plane spends **{tf['network_baseline'].iloc[0]:.0f} minutes** between leaving "
                    "the gate and taking off. These airports add the most extra waiting time.")
        st.plotly_chart(charts.taxi_fig(tf))
    hm = oe.breach_heatmap(fdf)
    if not hm.empty:
        st.plotly_chart(charts.heatmap_fig(hm))
        st.caption("Darker = more late flights. Late flights pile up in the evening at most busy airports.")

# ------------------------------------------------------------ 5. Potential fixes
with t5:
    st.subheader("What if one cause of delay were cut?")
    a, b = st.columns(2)
    cause = a.selectbox("Cause of delay", DELAY_CAUSES, format_func=CAUSE_LABELS.get)
    pct = b.slider("Cut it by (%)", 0, 100, 25, step=5)
    sim = oe.simulate_cause_reduction(fdf, cause, pct / 100)
    m1, m2, m3 = st.columns(3)
    m1.metric("Current on-time rate", f"{sim['baseline_pass_rate']:.1%}")
    m2.metric("New on-time rate", f"{sim['projected_pass_rate']:.1%}",
              delta=f"{(sim['projected_pass_rate'] - sim['baseline_pass_rate']) * 100:+.1f} points")
    m3.metric("Flights brought back on time", f"{sim['flights_recovered']:,}")
    st.caption("Assumes each minute removed comes straight off the arrival delay. A rough estimate, not a forecast.")

    st.subheader("Potential fixes")
    st.caption("How much the on-time rate would rise (in percentage points) if each cause of delay "
               "were cut by 10%, 20%, 30% or 40%.")
    bl = oe.improvement_backlog(fdf)
    gains = {f"Gain at {lvl:.0%} cut (pp)": f"{lvl:.0%}" for lvl in oe.CUT_LEVELS}
    table = bl[["Fix", "Delay cause", "Owner", *gains]].rename(columns=gains)
    st.dataframe(table.style.format({v: "{:+.2f}" for v in gains.values()}), hide_index=True)
    st.download_button("Download table (CSV)", table.to_csv(index=False), "potential_fixes.csv", "text/csv")
