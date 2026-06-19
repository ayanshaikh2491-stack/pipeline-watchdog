#!/usr/bin/env python3
"""Create pipeline-watchdog service on Render via API."""
import json
import urllib.request
import urllib.error

API_KEY="rnd_...R_ID = "tea-d5lqk2m3jp1c739lrfeg"

payload = {
    "type": "web_service",
    "name": "pipeline-watchdog",
    "ownerId": OWNER_ID,
    "repo": "https://github.com/ayanshaikh2491-stack/agency-platform",
    "branch": "master",
    "rootDir": "watchdog",
    "autoDeploy": "yes",
    "runtime": "python",
    "envVars": [
        {"key": "EC2_URL", "value": "http://18.213.66.136:8000"},
        {"key": "N8N_URL", "value": "https://nexus-n8n-x17d.onrender.com"},
        {"key": "CHECK_INTERVAL", "value": "90"},
        {"key": "GMAIL_ADDRESS", "value": "ayanagency@gmail.com"}
    ],
    "serviceDetails": {
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": "uvicorn main:app --host 0.0.0.0 --port 10000",
        "healthCheckPath": "/health",
        "plan": "free",
        "runtime": "python"
    }
}

body = json.dumps(payload).encode()
req = urllib.request.Request(
    "https://api.render.com/v1/services",
    data=body,
    method="POST"
)
req.add_header("Authorization", f"Bearer {API_KEY}")
req.add_header("Content-Type", "application/json")
req.add_header("Accept", "application/json")

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        print("SUCCESS!")
        print(json.dumps(result, indent=2))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
