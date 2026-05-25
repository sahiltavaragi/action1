# Hybrid Agricultural Auction System

Flask web app for farmers to list crops and run **hybrid auctions** (open bidding + optional **Buy Now**), backed by **Supabase** (PostgreSQL + Storage).

## Stack

| Layer | Technology |
|-------|------------|
| Backend | Flask + Flask sessions (Werkzeug password hashing) |
| Database | Supabase (`supabase-py`, service role key on server only) |
| Images | Supabase Storage bucket `crop-images` |
| Scheduler | `flask-apscheduler` — closes expired auctions every 60s |

## Setup

### 1. Supabase project

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run the full script in [`supabase_schema.sql`](supabase_schema.sql).
3. Go to **Storage** → create a **public** bucket named `crop-images`.
4. For the college demo: **Authentication → Policies** — you can leave RLS **disabled** on tables and use only the **service role key** in Flask (never in the browser).

### 2. Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
FLASK_SECRET_KEY=your-long-random-secret
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # Settings → API → service_role
```

### 3. Python

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

## Usage flow

1. **Register** as **Farmer** or **Buyer**.
2. **Farmer** → Dashboard → **List Crop** → upload photo, set start price, optional buy-now price, duration.
3. **Buyer** → Home → open auction → **Place Bid** or **Buy Now**.
4. When `ends_at` passes, the background job marks the auction **closed**, sets crop **sold** (if there was a high bidder) or **expired**.

## Project layout

```
action/
├── app.py              # Routes, session auth, APScheduler job
├── database.py         # Supabase client, uploads, helpers
├── supabase_schema.sql # Tables + indexes
├── requirements.txt
├── templates/          # Jinja2 HTML
└── static/css/         # Styles
```

## Security notes (demo)

- **Service role key** bypasses RLS — keep it only in `.env` on the server.
- Do **not** ship `.env` or commit secrets.
- For production: enable RLS, use anon key + policies, HTTPS, and stronger secrets.

## API style (Supabase vs MySQL)

```python
# List active auctions
supabase.table("auctions").select("*, crops(*)").eq("status", "active").execute()

# Parse timestamps from API
from datetime import datetime
ends = datetime.fromisoformat(row["ends_at"].replace("Z", "+00:00"))
```

Primary keys are **UUID** (`gen_random_uuid()`), not auto-increment integers.
