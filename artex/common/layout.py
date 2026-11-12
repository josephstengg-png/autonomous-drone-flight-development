"""Where the app lives and how its pieces are started. One place, so the console, the fleet tools and the
installer cannot disagree (10 Nov 2026: a stale agent on one companion stopped a two-aircraft run)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # where the code is: the checkout, or site-packages when installed
# Where the DATA is (profiles, runs, sectors, missions, operators, site files). A checkout keeps using itself, so nothing
# changes for development; an installed app uses ARTEX_HOME, default ~/.local/share/artex.
DATA = (Path(os.environ["ARTEX_HOME"]).expanduser() if os.environ.get("ARTEX_HOME")
        else ROOT if (ROOT / "pyproject.toml").exists() else Path.home() / ".local" / "share" / "artex")
STARTER = Path(__file__).resolve().parents[1] / "starter"     # shipped sample data, copied into DATA on first run
REMOTE_DIR = "~/artex_app"                           # the app's root on each companion (Jetson)
AGENT = ["python3", "-m", "artex.agent.drone_agent"]  # how a companion starts the aircraft agent

# What a companion needs: the edge runtime plus the fleet tools that run ON it. Paths relative to ROOT.
COMPANION_PAYLOAD = [
    "artex/__init__.py", "artex/common/__init__.py", "artex/common/layout.py", "artex/common/version.py",
    "artex/agent/__init__.py", "artex/agent/drone_agent.py", "artex/agent/safety_rules.py",
    "artex/agent/offboard_control.py", "artex/agent/elrs_link.py", "artex/agent/restore_downlink.py",
    "artex/fleetops/__init__.py", "artex/fleetops/preflight_probe.py", "artex/fleetops/mav_shell.py",
    "artex/fleetops/param_dump.py", "artex/fleetops/param_baseline.py", "artex/fleetops/pull_fc_logs.py",
    "artex/fleetops/elrs_service.sh", "artex/fleetops/jetson_net.sh", "artex/fleetops/fallback.py",
    "artex/diagnostics/__init__.py", "artex/diagnostics/motor_test.py", "artex/diagnostics/no_gps_check.py",
]


def ensure_data() -> Path:
    """First run of an installed app: create DATA and copy in the starter set. Never overwrites anything already there."""
    if DATA == ROOT or not STARTER.exists():
        return DATA
    DATA.mkdir(parents=True, exist_ok=True)
    for item in STARTER.iterdir():
        if item.name == "__pycache__" or (DATA / item.name).exists():
            continue
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, DATA / item.name)
    return DATA


def local_module(mod: str) -> list[str]:
    """Run one of the app's modules on this machine, with this interpreter."""
    return [sys.executable, "-m", mod]
