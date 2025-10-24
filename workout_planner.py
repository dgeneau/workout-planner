from intervas_util import IntervalsICUClient

import pandas as pd
import numpy as np
import streamlit as st

from datetime import datetime, date 
from dateutil.relativedelta import relativedelta
import plotly.graph_objs as go
from plotly.subplots import make_subplots
import os
from zoneinfo import ZoneInfo

st.set_page_config(layout= 'wide')
st.title('Intervals Testing')

# --- Secrets / config ---
def get_api_key() -> str:
    # Prefer Streamlit secrets, fall back to env var for non-Streamlit hosts
    return st.secrets.get("INTERVALS_API_KEY") or os.getenv("INTERVALS_API_KEY")

def get_tz() -> ZoneInfo:
    tz_name = st.secrets.get("TZ") or os.getenv("TZ") or "America/Vancouver"
    return ZoneInfo(tz_name)


@st.cache_resource
def get_client():
    api_key = get_api_key()
    if not api_key:
        st.stop()  # stops execution with a friendly message on the page
    return IntervalsICUClient(api_key=api_key)

client = get_client()
TZ = get_tz()

# Only cache *data*, and avoid passing unhashable objects like clients
@st.cache_data(ttl=300, show_spinner=False)
def load_athlete_map():
    try:
        df = client.list_athletes()
        if not df.empty and {"id", "display_name"}.issubset(df.columns):
            return dict(zip(df["display_name"], df["id"].astype(str)))
    except Exception as e:
        st.warning(f"Could not list athletes: {e}")

    me = client.get_athlete_profile("0")
    # handle DataFrame or dict gracefully
    if isinstance(me, pd.DataFrame):
        name = me.get("display_name", pd.Series(["Me"])).iloc[0]
        aid = me["id"].astype(str).iloc[0]
    else:
        name = me.get("display_name") or "Me"
        aid = str(me.get("id", "0"))
    return {name: aid}

athlete_dict = load_athlete_map()

###################################

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  

TZ = ZoneInfo("America/Vancouver")

st.header('Push Workouts to Intervals')

def next_monday(today=None):
    today = today or datetime.now(TZ).date()
    # weekday(): Mon=0 ... Sun=6
    days_to_next_mon = 7 - today.weekday()
    if days_to_next_mon == 0:  # today is Monday -> "upcoming" means next week
        days_to_next_mon = 7
    return today + timedelta(days=days_to_next_mon)

def week_label(monday_date):
    sunday = monday_date + timedelta(days=6)
    # e.g., "Week of Mon Oct 20 → Sun Oct 26 (ISO 2025-43)"
    iso_year, iso_week, _ = monday_date.isocalendar()
    return f"Week of {monday_date:%a %b %d} → {sunday:%a %b %d} (ISO {iso_year}-{iso_week:02d})"

def week_days(monday_date):
    names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    return {names[i]: (monday_date + timedelta(days=i)) for i in range(7)}

# Build the options: upcoming week + next 11
first_monday = next_monday()
monday_options = [first_monday + timedelta(weeks=i) for i in range(12)]
labels = [week_label(m) for m in monday_options]

# Default to the upcoming week (index 0)
choice = st.selectbox("Select a week", options=range(len(monday_options)),
                      format_func=lambda i: labels[i], index=0)

selected_monday = monday_options[choice]
days = week_days(selected_monday)



athlete_list = st.multiselect('Select Athelte to Push Workouts', athlete_dict)


athlete_ids = [athlete_dict[name] for name in athlete_list]

d1, d2, d3, d4, d5, d6, d7 = st.columns(7)
activity_dict = {'Rowing': 'Rowing', 
                 'Ergometer': 'VirtualRow', 
                 'Ride': 'Ride', 
                 'Run': 'Run',
                 'XT': 'Crossfit',
                 'Weights': 'WeightTraining', 
                 'Other': 'Other'}


DAY_ORDER = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
SLOTS = ["AM1","AM2","PM1","PM2"]

def render_day(day_name: str, day_label: str, activity_dict: dict):
    st.subheader(day_name)
    st.caption(f"{day_label}")  # safer than f'{days['Monday']}' quoting

    tabs = st.tabs(SLOTS)  # horizontal tabs within the day column

    # optional: store values in a dict you can use later
    day_values = {}

    for tab, slot in zip(tabs, SLOTS):
        with tab:
            # unique, stable keys: <day3>_<slot>_<field>
            kpref = f"{day_name[:3].lower()}_{slot.lower()}"
            wo_name = st.text_input(f"{slot} Workout Name", key=f"{kpref}_name")
            details = st.text_area(f"Enter {slot} Workout Details", key=f"{kpref}_details")
            act_sel = st.selectbox(
                f"Select {slot} Activity", 
                list(activity_dict),           # pass option labels, not the dict
                key=f"{kpref}_act_sel"
            )
            act_val = activity_dict[act_sel]

            # pack for later use (optional)
            day_values[slot] = {
                "name": wo_name,
                "details": details,
                "activity_label": act_sel,
                "activity_value": act_val,
            }
    return day_values

# ---- LAYOUT: 7 columns by day, tabs inside each ----
cols = st.columns(len(DAY_ORDER), gap="small")

weekly_plan = {}

for col, day in zip(cols, DAY_ORDER):
    with col:
        weekly_plan[day] = render_day(day, days[day], activity_dict)
    

SLOT_TIMES = {
    "AM1": "07:00:00",
    "AM2": "11:00:00",
    "PM1": "13:00:00",
    "PM2": "15:00:00",
}

SLOTS = ["AM1","AM2","PM1","PM2"]

def kpref(day_name: str, slot: str) -> str:
    return f"{day_name[:3].lower()}_{slot.lower()}"

def reset_workout_fields():
    """Clear all workout-related widget keys from session_state."""
    fields = ("name", "details", "act_sel")
    for day in DAY_ORDER:
        for slot in SLOTS:
            base = kpref(day, slot)
            for f in fields:
                st.session_state.pop(f"{base}_{f}", None)

def _normalize_date_str(dval) -> str:
    """Return YYYY-MM-DD for either a string,date,datetime."""
    if isinstance(dval, datetime):
        return dval.strftime("%Y-%m-%d")
    if isinstance(dval, date):
        return dval.strftime("%Y-%m-%d")
    # assume string already like 'YYYY-MM-DD'
    return str(dval)

def _schedule_one(client, athlete_id, start_iso, name, details, act_type):
    """Call API; return (ok, err_text)."""
    try:
        client.schedule_workout(
            athlete_id=athlete_id,
            start_date_local=start_iso,  # e.g., '2025-10-20T07:00:00'
            name=name or "",             # API usually accepts empty strings; adjust if required
            description=details or "",
            type=act_type,               # from activity_dict value
        )
        return True, ""
    except Exception as e:
        return False, str(e)

col_push, col_reset = st.columns([1, 6])
with col_push:
    push_wo = st.button("🚀 Push Workouts")
with col_reset:
    reset_wo = st.button("🧹 Reset All Fields")

if reset_wo:
    reset_workout_fields()
    st.success("All workout input fields have been cleared!")
    st.rerun()   # this refreshes the app and shows blank fields



if push_wo:
    logs = []  # list of dicts for a quick table
    total_ok = 0
    total_err = 0

    with st.spinner("Scheduling workouts..."):
        for athlete_id in athlete_ids:
            for day in DAY_ORDER:
                day_iso = _normalize_date_str(days[day])

                for slot in SLOTS:
                    row = weekly_plan[day][slot]  # {"name", "details", "activity_label", "activity_value"}

                    # Skip if nothing meaningful entered (adjust logic to your needs)
                    if not (row["name"] or row["details"] or row["activity_value"]):
                        continue

                    start_iso = f"{day_iso}T{SLOT_TIMES[slot]}"

                    ok, err = _schedule_one(
                        client=client,
                        athlete_id=athlete_id,
                        start_iso=start_iso,
                        name=row["name"],
                        details=row["details"],
                        act_type=row["activity_value"],  # this is the value from activity_dict
                    )
                    
                    id_to_name = {v: k for k, v in athlete_dict.items()}

                    if ok:
                        total_ok += 1
                        logs.append({
                            "athlete": id_to_name.get(athlete_id, str(athlete_id)),
                            "day": day,
                            "slot": slot,
                            "start_local": start_iso,
                            "status": "OK"
                        })
                    else:
                        total_err += 1
                        logs.append({
                            "athlete": id_to_name.get(athlete_id, str(athlete_id)),
                            "day": day,
                            "slot": slot,
                            "start_local": start_iso,
                            "status": "ERROR",
                            "error": err
                        })

    st.success(f"Done. Scheduled: {total_ok} • Errors: {total_err}")

    # Lightweight results table (no pandas needed)
    st.write("Results")
    st.dataframe(logs, use_container_width=True)

