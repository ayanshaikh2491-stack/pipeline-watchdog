"""
Pipeline Watchdog Service — hosted on Render
FastAPI web service + background watchdog thread

Checks:
  - n8n health (/healthz)
  - n8n webhook endpoints
  - EC2 backend health (/health)

Alerts via Gmail SMTP
"""
import os
import sys
import json
import time
import smtplib
import logging
import threading
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel

# ── Config (from env vars) ──────────────────────────────────────────
EC2_URL = os.getenv("EC2_URL", "http://18.213.66.136:8000")
N8N_URL = os.getenv("N8N_URL", "https://nexus-n8n-x17d.onrender.com")
N8N_WEBHOOK_PATHS = os.getenv("N8N_WEBHOOK_PATHS", "/webhook/test,/webhook/prod").split(",")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "90"))  # seconds
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "ayanagency@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ── Logging ─────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, f"watchdog_{datetime.now().strftime('%Y%m%d')}.log"))
    ]
)
logger = logging.getLogger("watchdog")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="Pipeline Watchdog Agent", version="2.0.0")


# ── Watchdog Core ───────────────────────────────────────────────────
class WatchdogState:
    """Thread-safe state tracker for the watchdog."""
    def __init__(self):
        self._lock = threading.Lock()
        self.consecutive_errors = 0
        self.total_cycles = 0
        self.last_cycle_time = None
        self.last_alert_sent = {}
        self.recent_logs = []
        # Per-service status
        self.n8n_ok = False
        self.ec2_ok = False
        self.webhooks_ok = {}

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def get_report(self):
        with self._lock:
            return {
                "total_cycles": self.total_cycles,
                "consecutive_errors": self.consecutive_errors,
                "last_cycle_time": str(self.last_cycle_time) if self.last_cycle_time else None,
                "n8n_ok": self.n8n_ok,
                "ec2_ok": self.ec2_ok,
                "webhooks_ok": self.webhooks_ok,
                "recent_logs": self.recent_logs[-20:],
            }


state = WatchdogState()


class WatchdogAgent:
    """Watches n8n + EC2 backend, sends alerts on failure."""

    # ── HTTP helpers ─────────────────────────────────────────────

    @staticmethod
    def _http_get(url, timeout=15):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read().decode()
                try:
                    return json.loads(data), r.getcode()
                except json.JSONDecodeError:
                    return {"raw": data[:200]}, r.getcode()
        except Exception as e:
            return {"error": str(e)}, 0

    # ── Health Checks ────────────────────────────────────────────

    def check_n8n(self):
        """Check if n8n is alive via /healthz."""
        data, code = self._http_get(f"{N8N_URL}/healthz", timeout=10)
        return code == 200, None if code == 200 else f"n8n returned HTTP {code}"

    def check_n8n_webhooks(self):
        """Test key n8n webhook endpoints."""
        results = {}
        all_ok = True
        for path in N8N_WEBHOOK_PATHS:
            path = path.strip()
            if not path:
                continue
            url = f"{N8N_URL}{path}"
            data, code = self._http_get(url, timeout=10)
            ok = code != 0
            results[path] = {"ok": ok, "http_code": code}
            if ok:
                logger.info(f"  ✅ Webhook {path} → HTTP {code}")
            else:
                logger.warning(f"  ⚠️ Webhook {path} → HTTP {code} ({data.get('error', '')})")
                all_ok = False
        return all_ok, results

    def check_ec2_backend(self):
        """Check EC2 backend health via /health."""
        data, code = self._http_get(f"{EC2_URL}/health", timeout=10)
        if code == 200:
            return True, None
        return False, f"EC2 backend returned HTTP {code}"

    # ── Email Alerts ─────────────────────────────────────────────

    def send_email(self, subject, body):
        """Send via Gmail SMTP."""
        try:
            msg = MIMEText(body)
            msg["Subject"] = f"[Watchdog] {subject}"
            msg["From"] = GMAIL_ADDRESS
            msg["To"] = GMAIL_ADDRESS
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.starttls()
                server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
                server.send_message(msg)
            logger.info(f"📧 Alert sent: {subject}")
            return True
        except Exception as e:
            logger.warning(f"📧 Email failed: {e}")
            return False

    def should_alert(self, alert_type, cooldown=30):
        """Rate-limit alerts (minutes)."""
        now = time.time()
        last = state.last_alert_sent.get(alert_type, 0)
        if now - last > cooldown * 60:
            state.last_alert_sent[alert_type] = now
            return True
        return False

    # ── Main Cycle ───────────────────────────────────────────────

    def run_cycle(self):
        """One watchdog cycle: check n8n, webhooks, EC2 backend."""
        log_entry = {"time": datetime.now().isoformat(), "cycle": state.total_cycles + 1}
        alerts = []
        errors_this_cycle = 0

        # ── Check 1: n8n health ──
        n8n_ok, n8n_err = self.check_n8n()
        state.update(n8n_ok=n8n_ok)
        log_entry["n8n_ok"] = n8n_ok
        if n8n_ok:
            logger.info("✅ n8n alive")
        else:
            logger.error(f"❌ n8n health: {n8n_err}")
            alerts.append(f"n8n down: {n8n_err}")
            errors_this_cycle += 1

        # ── Check 2: n8n webhooks ──
        webhooks_ok, webhook_results = self.check_n8n_webhooks()
        state.update(webhooks_ok=webhook_results)
        log_entry["webhooks"] = webhook_results
        if not webhooks_ok:
            failing = [p for p, r in webhook_results.items() if not r["ok"]]
            logger.error(f"❌ n8n webhooks failing: {failing}")
            alerts.append(f"n8n webhook failures: {failing}")
            errors_this_cycle += 1
        else:
            logger.info("✅ All n8n webhooks responsive")

        # ── Check 3: EC2 backend ──
        ec2_ok, ec2_err = self.check_ec2_backend()
        state.update(ec2_ok=ec2_ok)
        log_entry["ec2_ok"] = ec2_ok
        if ec2_ok:
            logger.info("✅ EC2 backend healthy")
        else:
            logger.error(f"❌ EC2 backend: {ec2_err}")
            alerts.append(f"EC2 backend down: {ec2_err}")
            errors_this_cycle += 1

        # ── Update consecutive errors ──
        if errors_this_cycle > 0:
            state.update(consecutive_errors=state.consecutive_errors + 1)
        else:
            state.update(consecutive_errors=0)

        # ── Alert ──
        log_entry["alerts"] = alerts
        if alerts and self.should_alert("pipeline"):
            body = f"""Pipeline Watchdog Report
━━━━━━━━━━━━━━━━━━
Time: {datetime.now().isoformat()}
Cycle: #{state.total_cycles + 1}

Issues:
{chr(10).join(f'  • {a}' for a in alerts)}

n8n:          {'OK' if n8n_ok else 'DOWN'}
n8n Webhooks: {'OK' if webhooks_ok else 'FAILING'}
EC2 Backend:  {'OK' if ec2_ok else 'DOWN'}
Consecutive errors: {state.consecutive_errors}
"""
            self.send_email(f"Pipeline Issues ({len(alerts)} problems)", body)

        # ── Save ──
        state.update(
            total_cycles=state.total_cycles + 1,
            last_cycle_time=datetime.now(),
        )
        with state._lock:
            state.recent_logs.append(log_entry)
            if len(state.recent_logs) > 100:
                state.recent_logs = state.recent_logs[-100:]

        logger.info(f"✅ Cycle done — {len(alerts)} alerts, {state.consecutive_errors} consecutive errors")
        return len(alerts) == 0


agent = WatchdogAgent()


# ── Background Thread ───────────────────────────────────────────────
def watchdog_loop():
    """Run in background thread."""
    logger.info("=" * 50)
    logger.info("🤖 PIPELINE WATCHDOG AGENT STARTED (v2 — n8n focused)")
    logger.info(f"   EC2: {EC2_URL}  |  n8n: {N8N_URL}")
    logger.info(f"   Webhooks: {N8N_WEBHOOK_PATHS}")
    logger.info(f"   Interval: {CHECK_INTERVAL}s  |  Gmail: {GMAIL_ADDRESS}")
    logger.info("=" * 50)

    while True:
        try:
            agent.run_cycle()
        except Exception as e:
            logger.error(f"💥 Watchdog cycle crashed: {e}")
            state.update(consecutive_errors=state.consecutive_errors + 1)
            if agent.should_alert("watchdog_crash", 60):
                agent.send_email("Watchdog Crashed", str(e))

        for _ in range(CHECK_INTERVAL // 5):
            time.sleep(5)


# ── FastAPI Routes ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Start watchdog thread when the app boots."""
    thread = threading.Thread(target=watchdog_loop, daemon=True, name="watchdog")
    thread.start()
    logger.info("🚀 Watchdog background thread started")


@app.get("/")
async def root():
    return {
        "service": "Pipeline Watchdog Agent",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "/health": "Health check",
            "/status": "Watchdog status report (n8n, EC2, last check)",
            "/logs": "Recent logs",
        }
    }


@app.get("/health")
async def health():
    """Render health check — keeps the service alive."""
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/status")
async def status():
    """Full watchdog status: EC2, n8n, webhooks, last check time."""
    report = state.get_report()
    return {
        "watchdog": {
            "total_cycles": report["total_cycles"],
            "consecutive_errors": report["consecutive_errors"],
            "last_cycle_time": report["last_cycle_time"],
        },
        "services": {
            "n8n": {
                "status": "ok" if report["n8n_ok"] else "down",
                "healthy": report["n8n_ok"],
            },
            "n8n_webhooks": {
                "status": "ok" if all(r["ok"] for r in report["webhooks_ok"].values()) else "degraded",
                "webhooks": report["webhooks_ok"],
            },
            "ec2_backend": {
                "status": "ok" if report["ec2_ok"] else "down",
                "healthy": report["ec2_ok"],
            },
        },
        "last_check_time": report["last_cycle_time"],
    }


@app.get("/logs")
async def logs(limit: int = 20):
    """Get recent watchdog logs."""
    report = state.get_report()
    return {"logs": report["recent_logs"][-limit:]}
