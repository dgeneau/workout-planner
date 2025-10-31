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

#API_KEY = 'wqot62gf851at7ygdvaqp57l'
API_KEY = '69yu8aiqme8lwh0v20vg3lbrm'

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

st.write(plot_metric)


###############
PALETTE = ["#1f77b4","#ff7f0e","#2ca02c","#d62728",
           "#9467bd","#8c564b","#e377c2","#7f7f7f",
           "#bcbd22","#17becf"]

# all sports present across metrics (after your filter)
all_sports_present = sorted(
    set().union(*[dfm["sport"].dropna().unique() for dfm in plot_metric.values()])
)
#sport_to_color = {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(all_sports_present)}
sport_to_color = {'Other': PALETTE[7], 
                  'VirtualRide': PALETTE[8], 
                  'VirtualRow': PALETTE[0], 
                  'Row': PALETTE[3], 
                  'WeightTraining': PALETTE[2], 
                  'Run': PALETTE[6], 
                  'HighIntensityIntervalTraining':PALETTE[1], 
                  'Pickleball': PALETTE[9]}

# y categories (dates) — union across metrics, sorted, to keep rows aligned
all_dates = sorted(
    set().union(*[pd.to_datetime(dfm["start_day"]).tolist() for dfm in plot_metric.values() if not dfm.empty])
)

# x-max per metric so both Planned & Actual columns use the same range
xmax_by_metric = {}
for m, dfm in plot_metric.items():
    if dfm.empty:
        xmax_by_metric[m] = 0.0
    else:
        xmax_by_metric[m] = float(dfm[["planned", "actual"]].max().max())

# build figure: 3 rows (metrics), 2 cols (Planned, Actual)
col_labels = ["Planned", "Actual"]
row_labels = [METRICS[m]["label"] for m in METRIC_ORDER]

fig = make_subplots(
    rows=len(METRIC_ORDER),
    cols=2,
    shared_yaxes=True,
    horizontal_spacing=0.10,
    vertical_spacing=0.06,
    column_titles=col_labels,
    row_titles=row_labels,
)

# add stacked bars: for each metric row, add one trace per sport in each column
for r, m in enumerate(METRIC_ORDER, start=1):
    dfm = plot_metric[m].copy()
    if not dfm.empty:
        dfm["start_day"] = pd.to_datetime(dfm["start_day"])
    # Build a tidy per-sport slice once to reuse in both columns
    for c, which in enumerate(["planned", "actual"], start=1):
        # first cell shows legend only
        show_leg = (r == 1 and c == 1)

        for s in all_sports_present:
            sub = dfm[dfm["sport"] == s]
            # If a sport has no data for this metric, add an empty trace to keep legend color consistent
            y_vals = sub["start_day"] if not sub.empty else []
            x_vals = sub[which] if not sub.empty else []

            fig.add_trace(
                go.Bar(
                    y=y_vals,
                    x=x_vals,
                    name=s,
                    legendgroup=s,
                    showlegend=show_leg,
                    marker=dict(color=sport_to_color[s]),
                    orientation="h",
                ),
                row=r, col=c
            )

        # consistent x range per metric across both columns
        xr = xmax_by_metric[m] * 1.10 if xmax_by_metric[m] > 0 else 1.0
        fig.update_xaxes(range=[0, xr], row=r, col=c)

# make all y-axes share the same ordered categories (dates)
for r in range(1, len(METRIC_ORDER) + 1):
    for c in range(1, 3):
        fig.update_yaxes(
            type="category",
            categoryorder="array",
            categoryarray=all_dates,  # keeps date row order consistent
            row=r, col=c
        )

# layout: stacked bars
fig.update_layout(
    barmode="stack",
    height=max(540, 220 * len(all_dates) * 0.35 + 220),  # adaptive height
    margin=dict(t=90, b=40, l=60, r=30),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)

st.plotly_chart(fig, use_container_width=True)


_='''

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
            
            fig.add_trace(
                go.Bar(
                    y=sub["start_day"],
                    x=sub["actual"]/sub['planned'],
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
        barmode="stack",
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
'''