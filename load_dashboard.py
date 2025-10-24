from intervas_util import IntervalsICUClient

import pandas as pd
import numpy as np
import streamlit as st

import datetime
from dateutil.relativedelta import relativedelta
import plotly.graph_objs as go
from plotly.subplots import make_subplots
import math

# Generic account login info
# Username CSI Monitoring
# password Monitoring2025

st.set_page_config(layout= 'wide')
st.title('Intervals Load Monitoring')

API_KEY = 'wqot62gf851at7ygdvaqp57l'

client = IntervalsICUClient(api_key=API_KEY)


@st.cache_data(ttl=300, show_spinner=False)
def load_athlete_map(_client):  # leading underscore tells Streamlit not to hash it
    try:
        df = _client.list_athletes()
        if not df.empty and {"id","display_name"}.issubset(df.columns):
            return dict(zip(df["display_name"], df["id"].astype(str)))
    except Exception as e:
        st.warning(f"Could not list athletes: {e}")

    me = _client.get_athlete_profile("0")
    name = me.get("display_name", pd.Series(["Me"])).iloc[0]
    aid  = me["id"].astype(str).iloc[0]
    return {name: aid}

athlete_dict = load_athlete_map(client)


date1, date2, = st.columns(2)

today = datetime.datetime.now()
start_date = today - relativedelta(weeks=2)




athlete_name = st.selectbox("Select Athlete", options=list(athlete_dict.keys()))
athlete_id   = athlete_dict[athlete_name]  

with date1:
    oldest = st.date_input('Select Start Date', start_date)
with date2:
    newest = st.date_input('Select End Date')





metric = st.radio(
    "Metric to compare",
    ["Duration (min)", "Distance (km)", "Load"],
    horizontal=True,
)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_actual(athlete_id, oldest, newest):
    df = client.get_activities(athlete_id=athlete_id, oldest=oldest, newest=newest, limit=1000, format="json")
    if df.empty:
        return df

    # Prefer local time if available
    ts = "start_date_local" if "start_date_local" in df.columns else "start_date"
    if ts in df.columns:
        df["start_day"] = pd.to_datetime(df[ts], errors="coerce").dt.floor("D")
    else:
        df["start_day"] = pd.NaT

    # Normalize sport
    if "sport" not in df.columns:
        df["sport"] = df.get("type", "Other").fillna("Other")

    # Actual metrics
    if "elapsed_time" in df.columns:
        df["duration_min_actual"] = pd.to_numeric(df["elapsed_time"], errors="coerce") / 60.0
    elif "moving_time" in df.columns:
        df["duration_min_actual"] = pd.to_numeric(df["moving_time"], errors="coerce") / 60.0
    else:
        df["duration_min_actual"] = np.nan

    if "distance" in df.columns:
        df["distance_km_actual"] = pd.to_numeric(df["distance"], errors="coerce") / 1000.0
    else:
        df["distance_km_actual"] = np.nan

    load_actual = None
    for cand in ["icu_training_load", "training_load", "load"]:
        if cand in df.columns:
            load_actual = cand
            break
    df["load_actual"] = pd.to_numeric(df[load_actual], errors="coerce") if load_actual else np.nan
    return df

@st.cache_data(ttl=300, show_spinner=False)
def fetch_planned(athlete_id, oldest, newest):
    # Planned workouts are calendar events with category=WORKOUT
    df = client.get_events(athlete_id=athlete_id, oldest=oldest, newest=newest, category="WORKOUT", resolve=False)
    return df

athlete_id = athlete_dict[athlete_name]
act_df = fetch_actual(athlete_id, oldest, newest)
evt_df = fetch_planned(athlete_id, oldest, newest)
st.write(act_df)
st.write(evt_df)
if len(evt_df)>0:
    evt_df["workout_doc.distance"] = evt_df["workout_doc.distance"] /1000
    evt_df["workout_doc.duration"] = evt_df["workout_doc.duration"]/60


def summarize_daily(df: pd.DataFrame, which: str, metric_label: str) -> pd.DataFrame:
    """
    which: 'planned' or 'actual'
    metric_label: radio text ("Duration (min)" | "Distance (km)" | "Load")
    """
    st.write(which)
    st.write(metric_label)
    if df.empty:
        return pd.DataFrame(columns=["start_day","sport",which])

    value_col = {
        "Duration (min)": f"duration_min_{which}",
        "Distance (km)": f"distance_km_{which}",
        "Load":          f"load_{which}",
    }[metric_label]
    if value_col not in df.columns:
        df[value_col] = np.nan

    out = (
        df.groupby(["start_day","sport"], as_index=False)[value_col]
          .sum(min_count=1)
          .rename(columns={value_col: which})
    )
    return out

st.write(evt_df)



METRICS = {
    "distance": {"label": "Distance (km)", "planned": "workout_doc.distance", "actual": "distance_km_actual"},
    "duration": {"label": "Duration (min)", "planned": "workout_doc.duration", "actual": "duration_min_actual"},
    "load":     {"label": "Load",           "planned": "load_planned",        "actual": "load_actual"},
}
METRIC_ORDER = ["distance", "duration", "load"]  # controls column order

SPORT_COLORS = {
    "Rowing": "blue",
    "VirtualRow": "red",
    "WeightTraining": "green",
    "other": "purple",
}

def sport_color(s: str) -> str:
    return SPORT_COLORS.get(s, "grey")

def summarize_daily(df: pd.DataFrame, which: str, value_col: str) -> pd.DataFrame:
    """
    Group-sum a single value column by day x sport.
    which: 'planned' or 'actual' (becomes the output column name)
    value_col: exact column name to aggregate (e.g., 'distance_km_planned')
    """
    if df.empty:
        return pd.DataFrame(columns=["start_day", "sport", which])

    if value_col not in df.columns:
        # keep df immutable; create missing col as NaN if absent
        df = df.copy()
        df[value_col] = np.nan

    out = (
        df.groupby(["start_day", "sport"], as_index=False)[value_col]
          .sum(min_count=1)
          .rename(columns={value_col: which})
    )
    return out

# --- compute per-metric daily summaries & merged tables ---
per_metric = {}
for m in METRIC_ORDER:
    plan_day_m = summarize_daily(evt_df, "planned", METRICS[m]["planned"])
    act_day_m  = summarize_daily(act_df,  "actual",  METRICS[m]["actual"])
    merged_m = (
        plan_day_m.merge(act_day_m, on=["start_day", "sport"], how="outer")
                  .fillna(0.0)
                  .sort_values(["start_day", "sport"])
    )
    per_metric[m] = merged_m

# union of sports across all metrics, then allow user filter
all_sports = sorted(
    set().union(*[dfm["sport"].dropna().unique() for dfm in per_metric.values()])
)
sel_sports = st.multiselect("Filter modalities", options=all_sports, default=all_sports)

# apply filter once per metric
plot_metric = {m: dfm[dfm["sport"].isin(sel_sports)].copy() for m, dfm in per_metric.items()}

# early out if everything is empty after filtering
if all(dfm.empty for dfm in plot_metric.values()):
    st.info("No data for the selected window/modalities.")
else:
    # order rows by sport present in any metric (keeps consistent row ordering)
    sports_order = sorted(set().union(*[dfm["sport"].unique() for dfm in plot_metric.values()]))

    fig = make_subplots(
        rows=len(sports_order),
        cols=3,
        shared_yaxes=True,     # share dates across the 3 metric columns in a row
        column_titles=[METRICS[m]["label"] for m in METRIC_ORDER],
        row_titles=sports_order,
        horizontal_spacing=0.12,
        vertical_spacing=0.06,
    )

    # add bars: for each sport (row), for each metric (col)
    for r, s in enumerate(sports_order, start=1):
        color = sport_color(s)
        for c, m in enumerate(METRIC_ORDER, start=1):
            sub = plot_metric[m][plot_metric[m]["sport"] == s]
            # Only show legend once (top-left subplot) to avoid duplicates
            show_leg = (r == 1 and c == 1)
            _='''
            fig.add_trace(
                go.Bar(
                    y=sub["start_day"],
                    x=sub["planned"],
                    name="Planned",
                    legendgroup="Planned",
                    showlegend=show_leg,
                    opacity=0.5,
                    orientation="h",
                    marker=dict(color=color),
                  
                ),
                row=r, col=c
            )
            '''
            fig.add_trace(
                go.Bar(
                    y=sub["start_day"],
                    x=sub["actual"]/sub['planned']*100,
                    name="Percent Completion",
                    legendgroup="Actual",
                    showlegend=show_leg,
                    opacity=0.95,
                    orientation="h",
                    marker=dict(color=color),
                  
                ),
                row=r, col=c
            )

    fig.update_layout(
        barmode="group",
        height=max(420, 240 * len(sports_order)),
        margin=dict(t=90, b=40, l=60, r=30),
    )
    # Label the x-axes per column with the metric label for clarity
    for c, m in enumerate(METRIC_ORDER, start=1):
        fig.update_xaxes(title_text=METRICS[m]["label"], row=len(sports_order), col=c)

    # y-axis on the first column can read as dates; others can hide tick labels to reduce clutter
    for r in range(1, len(sports_order) + 1):
        for c in range(2, 4):  # hide y tick labels on columns 2 and 3
            fig.update_yaxes(showticklabels=False, row=r, col=c)

    st.plotly_chart(fig, use_container_width=True)

with st.expander("Raw data (debug)"):
    st.write("Activities (actual):", act_df.head(10))
    st.write("Events (planned):", evt_df.head(10))
    for m in METRIC_ORDER:
        st.write(f"Merged daily – {METRICS[m]['label']}:", per_metric[m].head(10))


_='''
if plot_df.empty:
    st.info("No data for the selected window/modalities.")
else:
    sports_order = sorted(plot_df["sport"].unique())
    fig = make_subplots(
        rows=len(sports_order),
        cols=1,
        shared_xaxes=True,
        subplot_titles=[f"{s}" for s in sports_order],
        vertical_spacing=0.1,
    )

    for r, s in enumerate(sports_order, start=1):
        if s == 'Rowing': 
            color = 'blue'
        elif s == 'VirtualRow': 
            color = 'red'
        elif s == 'WeightTraining': 
            color = 'green'

        else: 
            color = 'purple'
        sub = plot_df[plot_df["sport"] == s]
        fig.add_trace(go.Bar(
            y=sub["start_day"], x=sub["planned"],
            name=f"{s} — Planned", legendgroup=s, showlegend=(True), opacity=0.3, 
            orientation='h',
            marker = dict(color = color)
        ), row=r, col=1)
        fig.add_trace(go.Bar(
            y=sub["start_day"], x=sub["actual"],
            name=f"{s} — Actual", legendgroup=s, showlegend=(True), opacity=1, 
            orientation='h',
            marker = dict(color = color)
        ), row=r, col=1)

    fig.update_layout(
        barmode="overlay",
        height=max(360, 220 * len(sports_order)),
        #margin=dict(t=60, b=40, l=40, r=20),
        #xaxis_title="Date",
        yaxis_title=metric,
    )
    st.plotly_chart(fig, use_container_width=True)

with st.expander("Raw data (debug)"):
    st.write("Activities (actual):", act_df.head(10))
    st.write("Events (planned):", evt_df.head(10))
'''