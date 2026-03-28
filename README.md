# 🏏 IPL Fantasy Backend

A production-ready Python backend for a real-time IPL Dream11-style fantasy app.

---

## Architecture

```
ipl_schedule_export.json   ← local match schedule (no DB read needed)
          │
  schedule_service.py      ← time-based match detection (IST)
          │
     worker.py             ← infinite poll loop (runs as separate dyno)
      ├──→ fetch Innings1 / Innings2 from AWS S3 JSONP feeds
      ├──→ hash-compare, skip if unchanged
      ├──→ store raw data → MongoDB (matches collection)
      └──→ fantasy_engine.py → store points → MongoDB (points collection)
          │
       app.py              ← Flask REST API (gunicorn)
```

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask REST API |
| `worker.py` | Background polling worker |
| `fantasy_engine.py` | Dream11 scoring logic |
| `db.py` | MongoDB singleton connection |
| `schedule_service.py` | Time-based match detection |
| `utils.py` | Hash, JSONP cleaning, IST time |
| `ipl_schedule_export.json` | Full IPL 2026 schedule |
| `requirements.txt` | Python dependencies |
| `Procfile` | Heroku process definitions |
| `runtime.txt` | Heroku Python version |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MONGO_URI` | ✅ | MongoDB Atlas or any Mongo connection string |

Set on Heroku:
```bash
heroku config:set MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
```

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set env variable
$env:MONGO_URI = "mongodb+srv://..."   # PowerShell
# or
export MONGO_URI="mongodb+srv://..."   # bash

# Start API server (dev)
python app.py

# Start worker (separate terminal)
python worker.py
```

---

## Heroku Deployment

```bash
# Login and create app
heroku login
heroku create your-app-name

# Set mongo URI
heroku config:set MONGO_URI="..."

# Push code
git add .
git commit -m "initial deploy"
git push heroku main

# Scale dynos
heroku ps:scale web=1 worker=1

# View logs
heroku logs --tail
```

---

## API Endpoints

### `GET /`
Health check.
```json
{"status": "API running"}
```

### `GET /match`
Current match info (time-based detection).
```json
{
  "match_id": 2417,
  "match_name": "Royal Challengers Bengaluru vs Sunrisers Hyderabad",
  "teams": { "home": "Royal Challengers Bengaluru", "away": "Sunrisers Hyderabad" },
  "venue": "M Chinnaswamy Stadium",
  "city": "Bengaluru",
  "start_time": "2026-03-28T19:30:00+05:30",
  "end_time": "2026-03-29T00:30:00+05:30",
  "is_active": true
}
```

### `GET /data`
Raw innings data stored in MongoDB.
```json
{
  "match_id": 2417,
  "updated_at": "...",
  "innings1": { ... },
  "innings2": { ... }
}
```

### `GET /points` ⭐
Ranked fantasy leaderboard.
```json
{
  "match_id": 2417,
  "updated_at": "...",
  "data": [
    {
      "rank": 1,
      "player": "Virat Kohli",
      "team": "Royal Challengers Bengaluru",
      "bat": 132,
      "bowl": 0,
      "field": 8,
      "play": 4,
      "total": 144,
      "catches": 1,
      "stumpings": 0,
      "run_out_direct": 0,
      "run_out_indirect": 0
    }
  ]
}
```

---

## Fantasy Point Rules

### Batting
| Event | Points |
|---|---|
| Run | +1 |
| Four | +4 |
| Six | +6 |
| 25 runs | +4 |
| 50 runs | +8 |
| 75 runs | +12 |
| 100 runs | +16 |
| Duck (dismissed for 0) | -2 |

**Strike Rate** (min 20 runs OR 10 balls):

| SR | Points |
|---|---|
| < 50 | -6 |
| 50–59 | -4 |
| 60–69 | -2 |
| 70–129 | 0 |
| 130–149 | +2 |
| 150–169 | +4 |
| 170+ | +6 |

### Bowling
| Event | Points |
|---|---|
| Dot ball | +1 |
| Wicket | +30 |
| Maiden | +12 |
| LBW / Bowled | +8 |
| 3 wickets | +4 |
| 4 wickets | +8 |
| 5 wickets | +12 |

**Economy Rate** (min 2 overs):

| Economy | Points |
|---|---|
| < 5 | +6 |
| 5–5.99 | +4 |
| 6–6.99 | +2 |
| 7–9.99 | 0 |
| 10–10.99 | -2 |
| 11–11.99 | -4 |
| 12+ | -6 |

### Fielding
| Event | Points |
|---|---|
| Catch | +8 |
| 3 catches bonus | +4 |
| Stumping | +12 |
| Run-out (direct) | +12 |
| Run-out (indirect) | +6 |

### Other
| Event | Points |
|---|---|
| Playing XI | +4 |

---

## MongoDB Schema

**Database:** `dream11`

**Collection: `matches`**
```json
{
  "match_id": 2417,
  "innings1": { ... },
  "innings2": { ... },
  "updated_at": "ISODate",
  "start_time": "ISODate",
  "end_time": "ISODate"
}
```

**Collection: `points`**
```json
{
  "match_id": 2417,
  "updated_at": "ISODate",
  "data": [ { "rank": 1, "player": "...", ... } ]
}
```

---

## Worker Behaviour

1. Reads schedule to find current match (time-based, IST)
2. If outside active window (±5h from start): sleeps 60s
3. During match: polls every **2 seconds**
4. Fetches Innings1 → after 5 unchanged polls → also fetches Innings2
5. MD5 hash comparison — **no DB write if data unchanged**
6. On change: saves raw data + recalculates fantasy points
7. Catches ALL exceptions → never crashes
