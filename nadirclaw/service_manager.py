"""Windows service management for NadirClaw using NSSM.

NSSM (Non-Sucking Service Manager) wraps any executable as a Windows
service with auto-restart, logging, and no console window.

Services managed:
  - NadirClaw-SurrealDB: SurrealDB data store
  - NadirClaw-Router:    NadirClaw LLM router (depends on SurrealDB)
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nadirclaw.service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NADIRCLAW_DIR = Path.home() / ".nadirclaw"
NSSM_DIR = NADIRCLAW_DIR / "bin"
NSSM_EXE = NSSM_DIR / "nssm.exe"
LOG_DIR = NADIRCLAW_DIR / "logs"

NSSM_DOWNLOAD_URL = "https://nssm.cc/release/nssm-2.24.zip"


def _find_nssm() -> Optional[Path]:
    """Locate nssm.exe — check our bin dir first, then PATH."""
    if NSSM_EXE.exists():
        return NSSM_EXE
    found = shutil.which("nssm")
    if found:
        return Path(found)
    return None


def _find_surreal() -> Optional[Path]:
    """Locate surreal.exe — check common locations then PATH."""
    candidates = [
        Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe",
        Path.home() / ".surrealdb" / "surreal.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    found = shutil.which("surreal")
    if found:
        return Path(found)
    return None


def _find_python_venv() -> Path:
    """Return the Python executable inside NadirClaw's venv, or sys.executable."""
    import sys

    venv_python = NADIRCLAW_DIR / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

def get_service_configs() -> Dict[str, Dict[str, Any]]:
    """Return service configurations, resolving paths dynamically."""
    surreal = _find_surreal()
    python = _find_python_venv()
    data_dir = NADIRCLAW_DIR / "surrealdb-data"

    configs = {
        "NadirClaw-SurrealDB": {
            "exe": str(surreal) if surreal else "surreal.exe",
            "args": (
                f"start --log info --user root --pass root "
                f"--bind 0.0.0.0:8000 "
                f"file://{str(data_dir).replace(chr(92), '/')}"
            ),
            "description": "NadirClaw SurrealDB instance",
            "depends_on": None,
        },
        "NadirClaw-Router": {
            "exe": str(python),
            "args": "-m nadirclaw.server",
            "description": "NadirClaw LLM Router",
            "depends_on": "NadirClaw-SurrealDB",
        },
    }
    return configs


# ---------------------------------------------------------------------------
# NSSM command builders (for testability)
# ---------------------------------------------------------------------------

def build_install_commands(name: str, config: Dict[str, Any]) -> List[List[str]]:
    """Build the list of NSSM commands to install and configure a service.

    Returns a list of command lists (each suitable for subprocess.run).
    """
    nssm = str(_find_nssm() or "nssm.exe")
    commands = []

    # Install the service
    commands.append([nssm, "install", name, config["exe"], config["args"]])

    # Description
    commands.append([nssm, "set", name, "Description", config.get("description", "")])

    # Logging
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_log = str(LOG_DIR / f"{name}.log")
    stderr_log = str(LOG_DIR / f"{name}-error.log")
    commands.append([nssm, "set", name, "AppStdout", stdout_log])
    commands.append([nssm, "set", name, "AppStderr", stderr_log])

    # Log rotation (10 MB)
    commands.append([nssm, "set", name, "AppRotateFiles", "1"])
    commands.append([nssm, "set", name, "AppRotateBytes", "10485760"])

    # Restart delay (5 seconds)
    commands.append([nssm, "set", name, "AppRestartDelay", "5000"])

    # Auto-start on boot
    commands.append([nssm, "set", name, "Start", "SERVICE_AUTO_START"])

    # Working directory
    commands.append([nssm, "set", name, "AppDirectory", str(NADIRCLAW_DIR)])

    # Service dependency
    if config.get("depends_on"):
        commands.append([nssm, "set", name, "DependOnService", config["depends_on"]])

    return commands


def build_uninstall_command(name: str) -> List[str]:
    """Build the NSSM command to remove a service."""
    nssm = str(_find_nssm() or "nssm.exe")
    return [nssm, "remove", name, "confirm"]


def build_start_command(name: str) -> List[str]:
    nssm = str(_find_nssm() or "nssm.exe")
    return [nssm, "start", name]


def build_stop_command(name: str) -> List[str]:
    nssm = str(_find_nssm() or "nssm.exe")
    return [nssm, "stop", name]


def build_status_command(name: str) -> List[str]:
    nssm = str(_find_nssm() or "nssm.exe")
    return [nssm, "status", name]


# ---------------------------------------------------------------------------
# Service operations
# ---------------------------------------------------------------------------

def _run(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def install_service(name: str, config: Dict[str, Any]) -> bool:
    """Install a Windows service via NSSM. Requires admin privileges."""
    nssm = _find_nssm()
    if not nssm:
        logger.error("NSSM not found. Install it or run scripts/windows_service.ps1")
        return False

    commands = build_install_commands(name, config)
    for cmd in commands:
        result = _run(cmd)
        if result.returncode != 0:
            # "install" failure is critical; "set" failures are warnings
            if "install" in cmd:
                logger.error("Failed to install %s: %s", name, result.stderr.strip())
                return False
            else:
                logger.warning("NSSM set warning for %s: %s", name, result.stderr.strip())

    logger.info("Service %s installed successfully", name)
    return True


def uninstall_service(name: str) -> bool:
    """Remove a Windows service via NSSM."""
    nssm = _find_nssm()
    if not nssm:
        logger.error("NSSM not found.")
        return False

    # Stop first
    _run(build_stop_command(name))

    result = _run(build_uninstall_command(name))
    if result.returncode != 0:
        logger.error("Failed to uninstall %s: %s", name, result.stderr.strip())
        return False

    logger.info("Service %s removed", name)
    return True


def start_service(name: str) -> bool:
    """Start a service."""
    result = _run(build_start_command(name))
    return result.returncode == 0


def stop_service(name: str) -> bool:
    """Stop a service."""
    result = _run(build_stop_command(name))
    return result.returncode == 0


def get_service_status(name: str) -> str:
    """Get service status: 'running', 'stopped', 'paused', or 'not_installed'."""
    nssm = _find_nssm()
    if not nssm:
        return "not_installed"

    result = _run(build_status_command(name))
    output = result.stdout.strip().lower()

    if "running" in output or "service_running" in output:
        return "running"
    elif "stopped" in output or "service_stopped" in output:
        return "stopped"
    elif "paused" in output or "service_paused" in output:
        return "paused"
    elif result.returncode != 0:
        return "not_installed"
    return output or "unknown"


def install_all() -> bool:
    """Install both SurrealDB and NadirClaw services."""
    configs = get_service_configs()
    success = True
    # SurrealDB first (NadirClaw depends on it)
    for name in ("NadirClaw-SurrealDB", "NadirClaw-Router"):
        if name in configs:
            if not install_service(name, configs[name]):
                success = False
    return success


def uninstall_all() -> bool:
    """Remove both services (reverse order)."""
    success = True
    for name in ("NadirClaw-Router", "NadirClaw-SurrealDB"):
        if not uninstall_service(name):
            success = False
    return success


def start_all() -> bool:
    """Start both services."""
    success = True
    for name in ("NadirClaw-SurrealDB", "NadirClaw-Router"):
        if not start_service(name):
            success = False
    return success


def stop_all() -> bool:
    """Stop both services (reverse order)."""
    success = True
    for name in ("NadirClaw-Router", "NadirClaw-SurrealDB"):
        if not stop_service(name):
            success = False
    return success


def get_all_status() -> Dict[str, str]:
    """Get status of all managed services."""
    configs = get_service_configs()
    return {name: get_service_status(name) for name in configs}
