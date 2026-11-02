"""Entry point: python -m gppcc [--port 8765] [--no-browser] [--profile NAME] [--no-menubar]"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
import webbrowser

from . import __version__, config, output

FROZEN = getattr(sys, "frozen", False)  # running from the packaged gppcc.app


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profiles", timeout=1) as r:
            return "names" in json.load(r)
    except Exception:
        return False


def _log_to_file() -> None:
    """A Finder-launched app has no console; keep output in ~/.gppcc/gppcc.log."""
    config.HOME.mkdir(parents=True, exist_ok=True)
    f = open(config.HOME / "gppcc.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = f


def main() -> int:
    ap = argparse.ArgumentParser(prog="gppcc", description="Control your PC with a gamepad.")
    ap.add_argument("--port", type=int, default=8765, help="web UI port (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the UI automatically")
    ap.add_argument("--profile", help="profile to start with")
    ap.add_argument("--no-menubar", action="store_true", help="macOS: don't show the menu-bar icon")
    # parse_known_args: Finder may pass extra args such as -psn_0_12345
    args, _ = ap.parse_known_args()

    if FROZEN and output.IS_MAC:
        _log_to_file()

    url = f"http://127.0.0.1:{args.port}/"
    if _already_running(args.port):
        # Second launch (e.g. double-clicking the app again): just show the UI.
        print(f"gppcc is already running — opening {url}")
        webbrowser.open(url)
        return 0

    output.setup_platform()
    if output.IS_WIN:
        # 1 ms timer resolution so the 125 Hz loop doesn't stutter (default is ~15.6 ms).
        import ctypes

        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

    config.ensure_home()
    if args.profile and args.profile not in config.list_profiles():
        print(f"unknown profile {args.profile!r}; available: {', '.join(config.list_profiles())}")
        return 2

    # Engine (SDL) must live on the main thread – required on macOS.
    from .engine import Engine
    from .server import start_server

    engine = Engine(args.profile)
    try:
        httpd = start_server(engine, args.port)
    except OSError as e:
        print(f"could not bind 127.0.0.1:{args.port}: {e} (is gppcc already running? try --port)")
        return 1

    menubar = None
    if output.IS_MAC and not args.no_menubar:
        try:
            from .macmenu import MenuBar

            menubar = MenuBar(engine, url)
        except Exception as e:
            print(f"menu bar unavailable: {e}")

    print(f"gppcc {__version__} — profile '{engine.active_name}'")
    print(f"UI:       {url}")
    print(f"Gamepad:  {engine.pads.active_name or 'none connected (plug one in any time)'}")
    print(f"Profiles: {config.PROFILE_DIR}")
    if not output.has_permissions():
        who = "gppcc" if FROZEN else "your terminal/Python"
        print(f"\n⚠  macOS: allow {who} in System Settings → Privacy & Security → Accessibility.\n")
        output.request_permissions()
    print("Quit from the menu-bar icon." if menubar else "Ctrl+C to quit.")

    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()

    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.running = False
        httpd.shutdown()
        print("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
