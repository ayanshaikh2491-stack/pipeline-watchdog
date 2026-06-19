# Pipeline Watchdog — Deploy to Render

Deploy the watchdog in 3 steps:

## Prerequisites

- A GitHub account
- A Render account (free tier works)
- Render CLI installed: `npm i -g @renderinc/cli` or `render --version` to verify

## Step 1: Push code to GitHub

```bash
# From the watchdog/ directory
git init
git add -A
git commit -m "Pipeline Watchdog v2 — n8n health checks"

# Create a repo on GitHub (https://github.com/new) then:
git remote add origin https://github.com/YOUR_USER/pipeline-watchdog.git
git push -u origin main
```

Or use GitHub CLI:
```bash
gh repo create pipeline-watchdog --public --source=. --push
```

## Step 2: Create the service on Render

### Option A — Render CLI (fastest)

```bash
render services create \
  --name pipeline-watchdog \
  --type web_service \
  --repo https://github.com/YOUR_USER/pipeline-watchdog \
  --runtime python \
  --region singapore \
  --plan free \
  --build-command "pip install -r requirements.txt" \
  --start-command "uvicorn main:app --host 0.0.0.0 --port 10000" \
  --health-check-path "/health" \
  --env-var "EC2_URL=http://18.213.66.136:8000" \
  --env-var "N8N_URL=https://nexus-n8n-x17d.onrender.com" \
  --env-var "N8N_WEBHOOK_PATHS=/webhook/test,/webhook/prod" \
  --env-var "CHECK_INTERVAL=90" \
  --auto-deploy \
  --output json
```

### Option B — Render Dashboard

1. Go to https://dashboard.render.com
2. Click **New +** → **Web Service**
3. Connect your `pipeline-watchdog` GitHub repo
4. Use these settings:

| Setting | Value |
|---|---|
| **Name** | `pipeline-watchdog` |
| **Runtime** | Python |
| **Region** | Singapore |
| **Plan** | Free |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port 10000` |
| **Health Check Path** | `/health` |

5. Add these environment variables:

| Key | Value |
|---|---|
| `EC2_URL` | `http://18.213.66.136:8000` |
| `N8N_URL` | `https://nexus-n8n-x17d.onrender.com` |
| `N8N_WEBHOOK_PATHS` | `/webhook/test,/webhook/prod` |
| `CHECK_INTERVAL` | `90` |

6. **Set secrets manually** (do not commit these):
   - `GMAIL_ADDRESS` — your Gmail address for alerts
   - `GMAIL_APP_PASSWORD` — Gmail app password (not your regular password)

7. Click **Create Web Service**

## Step 3: Verify it's running

Once deployed, visit:

```
https://pipeline-watchdog.onrender.com
https://pipeline-watchdog.onrender.com/health
https://pipeline-watchdog.onrender.com/status
```

- `/health` — simple alive check for Render
- `/status` — shows n8n health, EC2 backend health, webhook status, and last check time
- `/logs` — recent watchdog cycle logs

## Updating

Push to the `main` branch of your GitHub repo. Render auto-deploys when `autoDeploy` is enabled.

To update env vars or secrets later, use **Dashboard → Your Service → Environment** or:

```bash
render services update srv-xxxxx --env-var "KEY=newvalue"
```

## Troubleshooting

- **Build fails**: Check build logs in the Render dashboard. Ensure `requirements.txt` is at the repo root.
- **Service crashes**: Run `render logs srv-xxxxx` to see runtime logs.
- **No alerts**: Ensure `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` are set as secret env vars.
- **Webhook checks fail**: Update `N8N_WEBHOOK_PATHS` to match your actual n8n webhook endpoints.
