"""
Pipeline Watchdog Agent
━━━━━━━━━━━━━━━━━━━━━━
Runs on Render as a background worker.
Every 30s checks EC2 backend & pipeline health.
Auto-fixes issues, emails alerts on problems.
"""
import os
import sys
import time
import json
import smtplib
import logging
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────
EC2_URL = os.getenv("EC2_URL", "http://18.213.66.136:8000")
N8N_URL = os.getenv("N8N_URL", "https://nexus-n8n-x17d.onrender.com")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))  # seconds
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "ayanagency@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ── Setup ───────────────────────────────────────────────────────────
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


class PipelineWatchdog:
    """Watches the pipeline, auto-fixes errors, sends alerts."""

    def __init__(self):
        self.consecutive_errors = 0
        self.last_alert_sent = {}
        self.pipeline_runs = 0
        self.fixes_applied = 0

    # ── HTTP helpers ─────────────────────────────────────────────

    def _http_get(self, url, timeout=15):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read().decode()
                return json.loads(data), r.getcode()
        except Exception as e:
            return {"error": str(e)}, 0

    def _http_post(self, url, timeout=15):
        try:
            req = urllib.request.Request(url, data=b"{}", method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read().decode()
                return json.loads(data), r.getcode()
        except Exception as e:
            return {"error": str(e)}, 0

    # ── Health Checks ────────────────────────────────────────────

    def check_ec2(self):
        """Check if EC2 backend is alive."""
        data, code = self._http_get(f"{EC2_URL}/api/pipeline/status")
        if code == 200:
            return data, None
        return data, f"EC2 backend returned HTTP {code}"

    def check_pipeline(self):
        """Get pipeline status."""
        data, code = self._http_get(f"{EC2_URL}/api/pipeline/status")
        if code != 200:
            return None, f"Pipeline status endpoint failed: HTTP {code}"
        pipeline = data.get("data", {}) if "data" in data else data
        return pipeline, None

    def check_n8n(self):
        """Check if n8n is alive."""
        data, code = self._http_get(f"{N8N_URL}/healthz", timeout=10)
        if code == 200:
            return True, None
        return False, f"n8n returned HTTP {code}"

    # ── Auto-Fix Actions ─────────────────────────────────────────

    def restart_pipeline(self):
        """Trigger one pipeline cycle via trigger-cycle endpoint."""
        logger.info("🔧 Auto-fix: Triggering pipeline cycle...")
        data, code = self._http_post(f"{EC2_URL}/api/pipeline/trigger-cycle")
        if code == 200 and data.get("success"):
            self.fixes_applied += 1
            logger.info(f"✅ Pipeline cycle triggered successfully (fix #{self.fixes_applied})")
            return True, None
        return False, data.get("error", f"HTTP {code}")

    def reset_pipeline(self):
        """Full reset: stop + trigger cycle."""
        logger.info("🔧 Auto-fix: Full pipeline reset...")
        # Stop current
        data1, code1 = self._http_post(f"{EC2_URL}/api/pipeline/stop")
        time.sleep(2)
        # Trigger new cycle
        data2, code2 = self._http_post(f"{EC2_URL}/api/pipeline/trigger-cycle")
        if data2.get("success"):
            self.fixes_applied += 1
            logger.info(f"✅ Full reset done (fix #{self.fixes_applied})")
            return True, None
        return False, data2.get("error", "Reset failed")

    # ── Alerts ───────────────────────────────────────────────────

    def send_email_alert(self, subject, body):
        """Send alert via Gmail SMTP."""
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

    def should_alert(self, alert_type, cooldown_minutes=30):
        """Rate-limit alerts."""
        now = time.time()
        last = self.last_alert_sent.get(alert_type, 0)
        if now - last > cooldown_minutes * 60:
            self.last_alert_sent[alert_type] = now
            return True
        return False

    # ── Main Loop ────────────────────────────────────────────────

    def run_cycle(self):
        """One full watchdog cycle."""
        logger.info("─" * 40)
        logger.info(f"🔄 Watchdog cycle #{self.pipeline_runs + 1}")
        alerts = []
        fix_needed = False

        # ── Check 1: EC2 Health ──
        pipeline, err = self.check_pipeline()
        if err:
            logger.error(f"❌ EC2/Pipeline check failed: {err}")
            self.consecutive_errors += 1
            alerts.append(f"EC2 Error: {err}")
            fix_needed = True
        else:
            self.consecutive_errors = 0
            cycles = pipeline.get("cycles_run", "?")
            phase = pipeline.get("current_phase", "?")
            errors = pipeline.get("errors", [])
            active = pipeline.get("pipeline_active", False)
            leads = pipeline.get("total_leads_found", 0)
            qualified = pipeline.get("total_qualified", 0)

            logger.info(f"✅ Pipeline: {cycles} cycles | phase={phase} | leads={leads} | qualified={qualified} | active={active}")

            # Show recent errors
            if errors:
                for e in errors[-3:]:
                    logger.warning(f"⚠️ Pipeline error: {e.get('error', str(e))}")

            # ── Auto-fix: Pipeline stopped but shouldn't be ──
            if not active:
                logger.warning("⚠️ Pipeline is stopped — triggering restart")
                fix_needed = True
                ok, fix_err = self.restart_pipeline()
                if ok:
                    alerts.append("Pipeline was stopped → Auto-restarted ✅")
                else:
                    alerts.append(f"Pipeline restart failed: {fix_err} ❌")

            # ── Auto-fix: Pipeline in error state ──
            if phase == "error":
                logger.warning("⚠️ Pipeline in error state — triggering reset")
                fix_needed = True
                ok, fix_err = self.reset_pipeline()
                if ok:
                    alerts.append("Pipeline was in error state → Reset done ✅")
                else:
                    alerts.append(f"Pipeline reset failed: {fix_err} ❌")

        # ── Check 2: n8n Health ──
        n8n_ok, n8n_err = self.check_n8n()
        if n8n_ok:
            logger.info("✅ n8n is alive")
        else:
            logger.error(f"❌ n8n health check: {n8n_err}")
            alerts.append(f"n8n down: {n8n_err}")

        # ── Send Alert if needed ──
        if alerts and self.should_alert("pipeline_issue"):
            body = f"""Pipeline Watchdog Report
━━━━━━━━━━━━━━━━━━━━━━
Time: {datetime.now().isoformat()}
Cycle: #{self.pipeline_runs + 1}

Issues Found:
{chr(10).join(f'  • {a}' for a in alerts)}

Fixes Applied: {self.fixes_applied}
Consecutive Errors: {self.consecutive_errors}
"""
            self.send_email_alert(f"Pipeline Issues ({len(alerts)} problems)", body)

        self.pipeline_runs += 1
        logger.info(f"✅ Cycle done — {len(alerts)} alerts, {self.fixes_applied} total fixes")
        return len(alerts) == 0

    def run(self):
        """Main watchdog loop."""
        logger.info("=" * 50)
        logger.info("🤖 PIPELINE WATCHDOG AGENT STARTED")
        logger.info(f"   EC2: {EC2_URL}")
        logger.info(f"   n8n: {N8N_URL}")
        logger.info(f"   Interval: {CHECK_INTERVAL}s")
        logger.info(f"   Gmail: {GMAIL_ADDRESS}")
        logger.info("=" * 50)

        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"💥 Watchdog cycle crashed: {e}")
                self.consecutive_errors += 1
                if self.should_alert("watchdog_crash", cooldown_minutes=60):
                    self.send_email_alert("Watchdog Crashed", str(e))

            # Sleep with periodic checks
            for _ in range(CHECK_INTERVAL // 5):
                time.sleep(5)


if __name__ == "__main__":
    watchdog = PipelineWatchdog()
    watchdog.run()
