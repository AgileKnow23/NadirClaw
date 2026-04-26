"""NadirClaw ecosystem sentinel — self-healing watchdog for the LLM router stack.

Monitors NadirClaw (8856), SurrealDB (8000 via Docker), and Ollama (11434).
Detects service failures via TCP/HTTP health checks, applies targeted
remediation (restart the specific service that failed), rate-limits to
prevent restart loops, and pushes Telegram notifications on every
recovery + status transition.

Architecture mirrors C:/transcribe/watchdog.py (proven pattern).

Services and their restart methods:
  NadirClaw (8856)  -> subprocess: python -m nadirclaw (backgrounded)
  SurrealDB (8000)  -> docker restart surrealdb
  Ollama (11434)    -> kill + relaunch ollama.exe serve

Usage (development):
    python C:/Users/Agile/Respositories/NadirClaw/sentinel.py

Usage (production):
    Register as NSSM service:
    nssm install NadirClawSentinel python.exe C:/Users/Agile/Respositories/NadirClaw/sentinel.py
    nssm set NadirClawSentinel AppDirectory C:/Users/Agile/Respositories/NadirClaw
    nssm set NadirClawSentinel AppStdout C:/Users/Agile/Respositories/NadirClaw/sentinel.log
    nssm set NadirClawSentinel AppStderr C:/Users/Agile/Respositories/NadirClaw/sentinel.log
    nssm set NadirClawSentinel AppEnvironmentExtra PYTHONUNBUFFERED=1
    nssm start NadirClawSentinel
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS = 60
COOLDOWN_SECONDS = 90
MAX_RECOVERIES_PER_HOUR = 4

NADIRCLAW_DIR = Path(__file__).parent
# Sentinel runs as LocalSystem under NSSM; Path.home() resolves to
# C:\Windows\System32\config\systemprofile which is NOT Bryan's profile.
# Anchor user-profile paths to USER_HOME explicitly.
USER_HOME = Path(os.environ.get("NADIRCLAW_USER_HOME", r"C:\Users\Agile"))
CONFIG_FILE = USER_HOME / ".nadirclaw" / ".env"
TRANSCRIBE_CONFIG = Path("C:/transcribe/config.json")  # Telegram creds
LOG_FILE = NADIRCLAW_DIR / "sentinel.log"
STATE_FILE = NADIRCLAW_DIR / "sentinel_state.json"

AK_DASHBOARD_DIR = USER_HOME / "ak_dashboard"
OLLAMA_EXE = USER_HOME / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"

SERVICES = {
    "nadirclaw": {
        "port": 8856,
        "health_url": "http://127.0.0.1:8856/",
        "health_key": "status",  # expect {"status": "ok"}
        "health_value": "ok",
    },
    "surrealdb": {
        "port": 8000,
        "health_url": "http://127.0.0.1:8000/health",
        "health_key": None,  # any 200 is fine
        "health_value": None,
    },
    "ak_dashboard": {
        "port": 3000,
        "health_url": "http://127.0.0.1:3000/",
        "health_key": None,
        "health_value": None,
    },
    "ollama": {
        "port": 11434,
        "health_url": "http://127.0.0.1:11434/api/tags",
        "health_key": None,
        "health_value": None,
    },
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def _load_telegram_config() -> tuple[str, str]:
    try:
        cfg = json.loads(TRANSCRIBE_CONFIG.read_text(encoding="utf-8-sig"))
        return cfg.get("telegram_bot_token", ""), cfg.get("telegram_chat_id", "")
    except Exception:
        return "", ""


def _telegram_post(token: str, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def send_telegram(msg: str) -> None:
    token, chat_id = _load_telegram_config()
    if not token or not chat_id:
        log("WARN: Telegram config missing")
        return
    plain = msg.replace("*", "").replace("_", "").replace("`", "")
    try:
        _telegram_post(token, {"chat_id": chat_id, "text": plain})
    except Exception as exc:
        log(f"WARN: Telegram send failed: {exc}")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_recovery_timestamps: deque[float] = deque()


def _can_recover() -> bool:
    now = time.time()
    cutoff = now - 3600
    while _recovery_timestamps and _recovery_timestamps[0] < cutoff:
        _recovery_timestamps.popleft()
    return len(_recovery_timestamps) < MAX_RECOVERIES_PER_HOUR


def _record_recovery() -> None:
    _recovery_timestamps.append(time.time())


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


def check_service(name: str, cfg: dict) -> tuple[bool, str]:
    """Return (healthy, reason). Tries HTTP first, falls back to port check."""
    url = cfg["health_url"]
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            if cfg["health_key"]:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get(cfg["health_key"]) != cfg["health_value"]:
                    return False, f"{cfg['health_key']}={body.get(cfg['health_key'])!r} (expected {cfg['health_value']!r})"
            return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:100]}"


def check_all() -> dict[str, tuple[bool, str]]:
    """Check every service. Returns {name: (healthy, reason)}."""
    return {name: check_service(name, cfg) for name, cfg in SERVICES.items()}


# ---------------------------------------------------------------------------
# Accountant Monthly heartbeat watcher
# ---------------------------------------------------------------------------
#
# accountant_monthly.py runs on the 3rd of each month and writes a
# heartbeat JSON. We want the package in Gueto's inbox by the 3rd, so
# from the 4th onward we expect a recent "ok" heartbeat. A missing or
# stale heartbeat means Bryan didn't get an email and the cron job died
# silently — exactly the dark-period scenario we got bit by during the
# SurrealDB namespace wipe.
#
# Telegram alerts are rate-limited to once per calendar day so a stuck
# failure doesn't blast 1440 messages.

ACCOUNTANT_HEARTBEAT_DIR = (
    USER_HOME / "Documents" / "Agile Know" / "Finance"
)
ACCOUNTANT_ALERT_STATE_FILE = NADIRCLAW_DIR / "accountant_alert_state.json"
ACCOUNTANT_DUE_DAY = 4  # By the 4th of each month, heartbeat must be OK
ACCOUNTANT_STALE_HOURS = 36  # heartbeat older than this is stale


def _heartbeat_path(year: int) -> Path:
    return (
        ACCOUNTANT_HEARTBEAT_DIR
        / str(year)
        / "Monthly Reconciliation"
        / "Logs"
        / "accountant_monthly_heartbeat.json"
    )


def check_accountant_monthly() -> tuple[bool, str]:
    """Return (healthy, reason) for the monthly Gueto package.

    Healthy when:
      - Today is before the ACCOUNTANT_DUE_DAY of the current month
        (cron hasn't run yet, nothing to check)
      - OR the heartbeat exists, was written within ACCOUNTANT_STALE_HOURS,
        and status == 'ok' (or 'dry_run_ok' for manual tests)
    """
    now = datetime.now()
    if now.day < ACCOUNTANT_DUE_DAY:
        return True, f"not due yet (day < {ACCOUNTANT_DUE_DAY})"

    # Cron is supposed to run on the 3rd against the PRIOR month, so the
    # heartbeat lands in the prior-month's year folder.
    target_year = now.year - 1 if now.month == 1 else now.year
    hb_path = _heartbeat_path(target_year)
    if not hb_path.exists():
        return False, f"heartbeat missing: {hb_path}"

    try:
        payload = json.loads(hb_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"heartbeat unreadable: {exc}"

    ts_str = payload.get("ts")
    status = payload.get("status", "")
    detail = payload.get("detail", "")
    try:
        ts = datetime.fromisoformat(ts_str)
    except Exception:
        return False, f"heartbeat ts unparseable: {ts_str!r}"

    age_hours = (now - ts).total_seconds() / 3600
    if age_hours > ACCOUNTANT_STALE_HOURS:
        return False, (
            f"heartbeat stale ({age_hours:.1f}h old, status={status!r}). "
            f"Last detail: {detail}"
        )
    if status not in ("ok", "dry_run_ok"):
        return False, f"heartbeat status={status!r} ({detail})"
    return True, f"ok ({age_hours:.1f}h ago, {status})"


def _accountant_alert_state() -> dict:
    if not ACCOUNTANT_ALERT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ACCOUNTANT_ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_accountant_alert_state(state: dict) -> None:
    try:
        ACCOUNTANT_ALERT_STATE_FILE.write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log(f"WARN: accountant alert state write failed: {exc}")


def maybe_alert_accountant_monthly() -> None:
    """Call once per main-loop tick. Fires telegram only on the FIRST
    unhealthy assessment of the calendar day, then again the next day
    if still unhealthy. Sends a one-shot 'recovered' note when the
    state flips back to healthy."""
    healthy, reason = check_accountant_monthly()
    state = _accountant_alert_state()
    today = datetime.now().strftime("%Y-%m-%d")
    last_alert_date = state.get("last_unhealthy_alert_date")
    last_status = state.get("last_status", "ok")

    if healthy:
        # Recovery transition
        if last_status == "unhealthy":
            log(f"accountant_monthly: RECOVERED ({reason})")
            send_telegram(
                "*Accountant Monthly*: heartbeat recovered -> green\n"
                f"{reason}"
            )
        state.update({"last_status": "ok", "last_assessed": today})
        _save_accountant_alert_state(state)
        return

    # Unhealthy
    if last_alert_date != today:
        log(f"accountant_monthly: UNHEALTHY -> {reason}")
        send_telegram(
            "*Accountant Monthly*: monthly Gueto package did NOT land.\n"
            f"{reason}\n"
            "Run manually:  python C:/Users/Agile/reconcile/accountant_monthly.py"
        )
        state["last_unhealthy_alert_date"] = today
    state.update({"last_status": "unhealthy", "last_assessed": today})
    _save_accountant_alert_state(state)


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def start_detached(file_path: str, args: list[str], cwd: str) -> bool:
    """Launch a process fully decoupled from this one using PowerShell Start-Process.

    More reliable on Windows than Popen + DETACHED_PROCESS, which has historically
    allowed children to die silently.
    """
    arg_list = ",".join(_ps_quote(a) for a in args) if args else ""
    arg_clause = f" -ArgumentList @({arg_list})" if args else ""
    ps_cmd = (
        f"Start-Process -FilePath {_ps_quote(file_path)}"
        f"{arg_clause}"
        f" -WorkingDirectory {_ps_quote(cwd)}"
        f" -WindowStyle Hidden"
    )
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        log(f"  Start-Process failed: {res.stderr.strip()[:300]}")
        return False
    return True


def restart_nadirclaw() -> bool:
    """Start NadirClaw in the background. Returns True on apparent success."""
    log("  restarting NadirClaw...")
    try:
        ok = start_detached(
            sys.executable,
            [
                "-c",
                "import sys; sys.argv=['nadirclaw','serve']; "
                "from nadirclaw.cli import main; main()",
            ],
            cwd=str(NADIRCLAW_DIR),
        )
        if not ok:
            return False
        # Uvicorn + model load takes a few seconds
        time.sleep(10)
        healthy, reason = check_service("nadirclaw", SERVICES["nadirclaw"])
        if healthy:
            log("  NadirClaw restart OK")
            return True
        log(f"  NadirClaw restart — still unhealthy: {reason}")
        return False
    except Exception as exc:
        log(f"  NadirClaw restart failed: {exc}")
        return False


def restart_surrealdb() -> bool:
    """Restart the SurrealDB Docker container."""
    log("  restarting SurrealDB (docker restart surrealdb)...")
    try:
        res = subprocess.run(
            ["docker", "restart", "surrealdb"],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode == 0:
            time.sleep(5)
            healthy, reason = check_service("surrealdb", SERVICES["surrealdb"])
            if healthy:
                log("  SurrealDB restart OK")
                return True
            log(f"  SurrealDB restart — still unhealthy: {reason}")
            return False
        log(f"  docker restart failed: {res.stderr.strip()[:200]}")
        return False
    except Exception as exc:
        log(f"  SurrealDB restart failed: {exc}")
        return False


def restart_ollama() -> bool:
    """Kill and relaunch Ollama."""
    log("  restarting Ollama...")
    try:
        # Kill existing
        subprocess.run(
            ["taskkill", "/F", "/IM", "ollama.exe"],
            capture_output=True, timeout=10,
        )
        time.sleep(3)
        # Relaunch
        if not OLLAMA_EXE.exists():
            log(f"  Ollama exe not found at {OLLAMA_EXE}")
            return False
        ok = start_detached(str(OLLAMA_EXE), ["serve"], cwd=str(OLLAMA_EXE.parent))
        if not ok:
            return False
        time.sleep(8)  # Ollama takes a few seconds to bind
        healthy, reason = check_service("ollama", SERVICES["ollama"])
        if healthy:
            log("  Ollama restart OK")
            return True
        log(f"  Ollama restart — still unhealthy: {reason}")
        return False
    except Exception as exc:
        log(f"  Ollama restart failed: {exc}")
        return False


def restart_ak_dashboard() -> bool:
    """Restart the AK Dashboard Next.js app."""
    log("  restarting AK Dashboard (next start)...")
    try:
        # Kill existing next process on port 3000
        subprocess.run(
            ["taskkill", "/F", "/FI", f"WINDOWTITLE eq next*", "/IM", "node.exe"],
            capture_output=True, timeout=10,
        )
        time.sleep(3)
        # Relaunch — resolve node on PATH so we don't depend on cwd
        node_exe = shutil.which("node") or "node"
        ok = start_detached(
            node_exe,
            ["node_modules/next/dist/bin/next", "start"],
            cwd=str(AK_DASHBOARD_DIR),
        )
        if not ok:
            return False
        time.sleep(8)
        healthy, reason = check_service("ak_dashboard", SERVICES["ak_dashboard"])
        if healthy:
            log("  AK Dashboard restart OK")
            return True
        log(f"  AK Dashboard restart — still unhealthy: {reason}")
        return False
    except Exception as exc:
        log(f"  AK Dashboard restart failed: {exc}")
        return False


RESTART_FNS = {
    "nadirclaw": restart_nadirclaw,
    "surrealdb": restart_surrealdb,
    "ollama": restart_ollama,
    "ak_dashboard": restart_ak_dashboard,
}


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def write_state(results: dict[str, tuple[bool, str]], recovery_count: int) -> None:
    payload = {
        "services": {
            name: {"healthy": ok, "reason": reason}
            for name, (ok, reason) in results.items()
        },
        "all_healthy": all(ok for ok, _ in results.values()),
        "recovery_count_since_start": recovery_count,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "sentinel_pid": os.getpid(),
    }
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:
        log(f"WARN: state file write failed: {exc}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    log(f"sentinel starting — poll={POLL_SECONDS}s max_recoveries/hr={MAX_RECOVERIES_PER_HOUR}")
    svc_summary = ", ".join(f"{n} (:{c['port']})" for n, c in SERVICES.items())
    log(f"  monitoring: {svc_summary}")
    send_telegram("*NadirClaw Sentinel*: started, monitoring every 60s")

    previous_all_healthy = True
    recovery_count = 0

    while True:
        try:
            results = check_all()

            # Compact summary line
            parts = []
            for name, (ok, reason) in results.items():
                port = SERVICES[name]["port"]
                parts.append(f"{name}(:{port})={'ok' if ok else 'DOWN'}")
            log(" ".join(parts))

            write_state(results, recovery_count)

            all_healthy = all(ok for ok, _ in results.values())

            # Transition: unhealthy -> healthy (self-healed)
            if all_healthy and not previous_all_healthy:
                log("STATUS TRANSITION: DOWN -> ALL GREEN (self-healed)")
                send_telegram("*NadirClaw Sentinel*: all services recovered -> GREEN")

            # Fix anything that's down
            cooldown = POLL_SECONDS
            for name, (ok, reason) in results.items():
                if ok:
                    continue
                if not _can_recover():
                    log(f"RATE LIMITED — {name} is down but recovery budget exhausted")
                    send_telegram(
                        f"*NadirClaw Sentinel*: {name} is DOWN but rate-limited "
                        f"({MAX_RECOVERIES_PER_HOUR}/hr exceeded). Manual fix needed.\n"
                        f"Reason: {reason}"
                    )
                    continue

                log(f"RECOVERY: {name} is down ({reason})")
                restart_fn = RESTART_FNS.get(name)
                if not restart_fn:
                    log(f"  no restart function for {name}")
                    send_telegram(f"*NadirClaw Sentinel*: {name} DOWN, no auto-fix available\n{reason}")
                    continue

                success = restart_fn()
                _record_recovery()
                recovery_count += 1
                cooldown = COOLDOWN_SECONDS

                if success:
                    send_telegram(f"*NadirClaw Sentinel*: {name} was DOWN, restarted successfully")
                else:
                    send_telegram(
                        f"*NadirClaw Sentinel*: {name} restart FAILED. Manual fix needed.\n"
                        f"Reason: {reason}"
                    )

            previous_all_healthy = all_healthy

            # Independent check (rate-limited to once-per-day alerting):
            # the monthly Gueto package heartbeat. Decoupled from the
            # service polling above because the cadence is monthly.
            try:
                maybe_alert_accountant_monthly()
            except Exception as exc:
                log(f"WARN accountant_monthly check failed: {exc!r}")

        except Exception as exc:
            log(f"ERROR in sentinel loop: {exc!r}")
            cooldown = POLL_SECONDS

        time.sleep(cooldown)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("sentinel stopping (SIGINT)")
        sys.exit(0)
