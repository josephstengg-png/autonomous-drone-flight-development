"""ARTEX console as a desktop app: a native window (pywebview over WebKitGTK), no web server, no network port.

    artex-console                  # the desktop app (the default)
    artex-console --demo           # simulated aircraft
    artex-console --serve          # the web server instead (http://127.0.0.1:8787), as before
    artex-console --check          # open the page in a hidden window, prove the bridge works, exit 0/1

The page is the same file the web server serves. Its one `api()` function talks to Python through pywebview's bridge
(`Bridge.call`), which goes to `gs_server.dispatch` -- the router the web server and the local Python API use too.

Closing the window during a run is refused: the console holds the agents' link, and without it each aircraft returns to
launch on its own after its failsafe time (60 s). The operator can still close after an explicit confirmation.
docs/local-app-plan.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

from artex.common import layout
from artex.console import gs_core as core
from artex.console import gs_server

TITLE = "ARTEX Ground Station"
CLOSE_WARNING = ("A run is active. Closing the console ends its link to the companions: each aircraft then returns to "
                 "launch on its own after about 60 s. Stop or finish the run first.\n\nClose anyway?")


def _ensure_gi() -> None:
    """pywebview's GTK backend needs the system's GObject bindings (python3-gi, WebKitGTK). A venv does not see them;
    add the system path LAST, so nothing in the venv is shadowed (same Python version required)."""
    try:
        import gi  # noqa: F401
    except ImportError:
        for p in ("/usr/lib/python3/dist-packages",):
            if os.path.isdir(os.path.join(p, "gi")) and p not in sys.path:
                sys.path.append(p)


def run_active(app: gs_server.App) -> bool:
    return bool((app.run and app.run.active) or (app.fleet and app.fleet.active))


class Bridge:
    """What the page can call. Only `call` reaches the console; `force_close` only after the page's own confirmation."""

    def __init__(self, app: gs_server.App):
        self._app = app
        self._window = None
        self._force = False

    def call(self, method: str, path: str, body=None, session=None) -> dict:
        if not isinstance(path, str) or not path.startswith("/api/"):
            return dict(status=404, body=dict(error="not an API path"))
        status, out = gs_server.dispatch(self._app, str(method), path, body if isinstance(body, dict) else None,
                                         session if isinstance(session, str) else None)
        return dict(status=status, body=json.loads(json.dumps(out, default=str)))

    def force_close(self) -> dict:
        """The operator confirmed closing during a run."""
        self._force = True
        threading.Thread(target=self._window.destroy, daemon=True).start()
        return dict(ok=True)


def _page_uri() -> str:
    return gs_server.GUI.resolve().as_uri()


def run_desktop(app: gs_server.App, *, hidden: bool = False, check: bool = False) -> int:
    _ensure_gi()
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        import webview
    except (ImportError, ValueError) as exc:
        print(f"[desktop] the desktop window cannot start ({exc}). It needs pywebview and the system packages python3-gi "
              f"and gir1.2-webkit2-4.1 (or -4.0): sudo apt install python3-gi gir1.2-webkit2-4.1\n"
              f"Meanwhile the same console runs in a browser: artex-console --serve", file=sys.stderr)
        return 3

    bridge = Bridge(app)
    window = webview.create_window(TITLE, _page_uri(), js_api=bridge, width=1440, height=920, min_size=(900, 600),
                                   hidden=hidden, text_select=True)
    bridge._window = window

    def on_closing():
        if bridge._force or not run_active(app):
            return True
        # never block the GUI thread here: cancel the close and let the page ask the operator
        threading.Thread(target=lambda: window.evaluate_js(
            f"window.__artexCloseBlocked && window.__artexCloseBlocked({json.dumps(CLOSE_WARNING)})"), daemon=True).start()
        return False

    window.events.closing += on_closing
    result = {"ok": None, "detail": ""}

    def self_check():
        end = time.time() + 30
        while time.time() < end:
            time.sleep(0.5)
            try:
                v = window.evaluate_js("window.__artexCheck ? JSON.stringify(window.__artexCheck) : ''")
            except Exception as exc:          # page still loading
                v, result["detail"] = "", str(exc)
            if v:
                d = json.loads(v)
                result["ok"], result["detail"] = bool(d.get("ok")), v
                break
        window.destroy()

    storage = layout.DATA / ".desktop-webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(self_check if check else None, gui="gtk", private_mode=False, storage_path=str(storage))
    if check:
        print(("[desktop] check passed: " if result["ok"] else "[desktop] check FAILED: ") + result["detail"], flush=True)
        return 0 if result["ok"] else 1
    if run_active(app):
        print("[desktop] closed during a run; the agents keep their failsafe (return to launch without the console).",
              file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="simulated aircraft; contacts nothing")
    ap.add_argument("--no-signin", action="store_true", help="lab use only: roles not enforced")
    ap.add_argument("--serve", action="store_true", help="run the web server instead of the desktop window")
    ap.add_argument("--port", type=int, default=8787, help="with --serve")
    ap.add_argument("--check", action="store_true", help="hidden window self-test of page + bridge, then exit")
    ap.add_argument("--safe-graphics", action="store_true",
                    help="software compositing (WEBKIT_DISABLE_COMPOSITING_MODE=1): use if the window stays blank")
    a = ap.parse_args()
    if a.safe_graphics:
        os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"
    if a.serve:
        sys.argv = [sys.argv[0]] + (["--demo"] if a.demo else []) + (["--no-signin"] if a.no_signin else []) \
            + ["--port", str(a.port)]
        return gs_server.main()
    layout.ensure_data()
    app = gs_server.App(a.demo or a.check, require_signin=not a.no_signin)
    mode = "DEMO (simulated aircraft)" if (a.demo or a.check) else (
        "SIMULATED aircraft (PX4 SITL)" if all(ac.get("sim") for ac in core.AIRCRAFT.values()) else "LIVE (real aircraft over ssh)")
    print(f"[desktop] {mode} -- desktop window, no network port", file=sys.stderr, flush=True)
    return run_desktop(app, hidden=a.check, check=a.check)


if __name__ == "__main__":
    raise SystemExit(main())
