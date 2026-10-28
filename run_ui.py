"""Start the web UI: python run_ui.py [--port 8000] [--no-browser]

Processes the scenario day into data/runs/ui.db, serves the API and the single-page UI, and
opens http://localhost:8000. No build step and no network needed (Gemini is optional).
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Drone Security Analyst web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    try:
        import uvicorn

        from drone_security.web import create_app
        from drone_security.web.state import AppState
    except ImportError as exc:
        sys.exit(f"Missing dependency ({exc.name}). Run `python make.py setup` first, or use `python make.py ui`.")

    print("Processing the scenario day into data/runs/ui.db ...", flush=True)
    state = AppState()
    print(f"{state.run_stats.frames} frames, {state.run_stats.alerts} alerts indexed. "
          f"Gemini: {state.gemini_status()['mode']}", flush=True)
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"UI on {url}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(create_app(state), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
