"""Where the app lives and how its pieces are started. One place, so the console, the fleet tools and the
installer cannot disagree (10 Nov 2026: a stale agent on one companion stopped a two-aircraft run)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the app checkout / install root
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


def local_module(mod: str) -> list[str]:
    """Run one of the app's modules on this machine, with this interpreter."""
    return [sys.executable, "-m", mod]
