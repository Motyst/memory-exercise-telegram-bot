# Web Dashboard — Build Guide

Goal: a private web dashboard showing all users' progress with shareable graphics
(PNG charts you can post to your Skool community).

Recommended stack: **Streamlit + Plotly**, reading the bot's SQLite database
directly. No changes to the bot needed — the dashboard is a separate process
that only *reads* the DB. Roughly 1–2 hours to a working version.

## Why Streamlit

- One Python file, no HTML/JS/auth framework needed
- Built-in password gate (see below)
- Plotly charts have a built-in 📷 button that downloads a PNG — that's your
  "easy to share graphics" story
- Runs fine on the same VPS next to the bot

## Step 1 — Install (on the VPS)

```bash
cd /root/mental_training_bot
venv/bin/pip install streamlit plotly pandas
```

## Step 2 — Create `dashboard.py`

```python
import sqlite3
import json
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Mental Training Dashboard", layout="wide")

# --- simple password gate ---
PASSWORD = st.secrets.get("password", "changeme")
if st.text_input("Password", type="password") != PASSWORD:
    st.stop()

# --- load data (read-only; WAL mode makes concurrent reads safe) ---
DB = "file:mental_training.db?mode=ro"
conn = sqlite3.connect(DB, uri=True)

users = pd.read_sql_query("""
    SELECT id, telegram_id, first_name, username, current_streak,
           longest_streak, created_at, last_active_at
    FROM users
""", conn)

sessions = pd.read_sql_query("""
    SELECT s.user_id, u.first_name AS name, s.difficulty, s.score,
           s.max_score, s.started_at, s.parameters
    FROM exercise_sessions s
    JOIN users u ON u.id = s.user_id
    WHERE s.max_score IS NOT NULL AND s.max_score > 0
""", conn)
sessions["pct"] = sessions.score / sessions.max_score * 100
sessions["date"] = pd.to_datetime(sessions.started_at).dt.date
sessions["pairs"] = sessions.parameters.map(lambda p: json.loads(p).get("count"))

# --- KPIs ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Users", len(users))
c2.metric("Tests", len(sessions))
c3.metric("Avg score", f"{sessions.pct.mean():.0f}%")
c4.metric("Active 7d", (pd.to_datetime(users.last_active_at)
          > pd.Timestamp.now() - pd.Timedelta(days=7)).sum())

# --- charts (each has a camera icon → downloads PNG for sharing) ---
st.plotly_chart(px.line(
    sessions.groupby("date").pct.mean().reset_index(),
    x="date", y="pct", title="Average score over time", markers=True))

st.plotly_chart(px.box(
    sessions, x="difficulty", y="pct", title="Scores by difficulty"))

progress = sessions.groupby("name").agg(
    tests=("pct", "size"), avg=("pct", "mean"), best=("pct", "max")
).reset_index().sort_values("avg", ascending=False)
st.plotly_chart(px.bar(
    progress, x="name", y="avg", hover_data=["tests", "best"],
    title="User comparison — average score"))

# --- per-user drilldown ---
who = st.selectbox("User detail", progress.name)
st.plotly_chart(px.scatter(
    sessions[sessions.name == who], x="started_at", y="pct",
    color="difficulty", size="pairs", title=f"{who} — progress"))

st.dataframe(progress, use_container_width=True)
```

## Step 3 — Run it

```bash
# .streamlit/secrets.toml on the VPS:
#   password = "your-strong-password"
venv/bin/streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```

Open `http://5.78.218.169:8501`. To keep it running, copy the bot's systemd
service to `mental_training_dashboard.service` and change `ExecStart` to the
streamlit command above.

## Sharing graphics

- Hover any chart → camera icon → downloads a PNG sized for posting.
- For fully automatic weekly share images, add `kaleido`
  (`pip install kaleido`) and `fig.write_image("weekly.png")` in a cron
  script — can even send the PNG to you via the bot with `bot.send_photo`.

## Security notes

- Password gate above is fine for a single-admin tool. If exposing publicly,
  put it behind HTTPS (Caddy/nginx reverse proxy) or bind to `127.0.0.1` and
  access via SSH tunnel: `ssh -L 8501:localhost:8501 root@5.78.218.169`.
- Keep the read-only `mode=ro` connection string — the dashboard must never
  write to the bot's DB.

## After the PostgreSQL migration

Same dashboard, swap `sqlite3.connect` for `pd.read_sql` over a
`postgresql://` SQLAlchemy engine. Queries stay identical because scores live
in real columns (not JSON) as of the current schema.
