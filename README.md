# PercfectStudios

Image & video generation powered by xAI, deployed on Render, with Supabase for database + storage.

---

## Stack

| Layer | Service |
|-------|---------|
| Frontend / domain | Vercel → percfectai.com |
| Backend (Flask) | Render |
| Database + auth | Supabase (Postgres) |
| File storage | Supabase Storage |
| AI | xAI (Grok image + Aurora video) |

---

## Local development

```bash
# 1. Clone / copy files
cd percfectstudios

# 2. Create virtual env
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install deps
pip install -r requirements.txt

# 4. Set up env
cp .env.example .env
# Fill in XAI_API_KEY, SUPABASE_URL, SUPABASE_KEY

# 5. Run Supabase schema
# → Open your Supabase project → SQL Editor → paste supabase_schema.sql → Run

# 6. Start Flask
python app.py
# → http://localhost:5000
```

---

## Deploy to Render

1. Push your code to GitHub.
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo.
3. Render will detect `render.yaml` automatically.
4. Add environment variables in Render dashboard:
   - `XAI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `SECRET_KEY` (Render can auto-generate this)
5. Deploy. Your Flask app will be live at `https://percfectstudios.onrender.com`.

---

## Point percfectai.com/percfectstudios to Render

Since percfectai.com is on **Vercel** and the Flask backend is on **Render**, you have two options:

### Option A — Vercel Rewrite (recommended, seamless URL)

In your Vercel project add a `vercel.json`:

```json
{
  "rewrites": [
    {
      "source": "/percfectstudios/:path*",
      "destination": "https://your-app.onrender.com/percfectstudios/:path*"
    },
    {
      "source": "/login",
      "destination": "https://your-app.onrender.com/login"
    },
    {
      "source": "/register",
      "destination": "https://your-app.onrender.com/register"
    },
    {
      "source": "/logout",
      "destination": "https://your-app.onrender.com/logout"
    },
    {
      "source": "/dashboard",
      "destination": "https://your-app.onrender.com/dashboard"
    },
    {
      "source": "/buy-credits",
      "destination": "https://your-app.onrender.com/buy-credits"
    }
  ]
}
```

Users visit `percfectai.com/percfectstudios` — Vercel proxies to Render behind the scenes. URL stays clean.

### Option B — Subdomain

Point `studios.percfectai.com` directly to your Render service via a CNAME record.

---

## Supabase setup

1. Create a new Supabase project at [supabase.com](https://supabase.com).
2. Go to **SQL Editor** → paste `supabase_schema.sql` → Run.
3. Go to **Storage** → New bucket → name it `generations` → set to Public.
4. Copy your **Project URL** and **anon key** from Settings → API.

---

## Adding Stripe payments (buy credits)

The `/buy-credits` page is a placeholder. When ready:

1. `pip install stripe`
2. Add `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` to `.env`.
3. Create products/prices in Stripe dashboard.
4. Add a `/create-checkout-session` route and a `/webhook` route to `app.py`.
5. On successful payment, increment `picture_credits` or `video_credits` in Supabase.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session secret (any long random string) |
| `XAI_API_KEY` | Your xAI API key |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Your Supabase anon or service-role key |
