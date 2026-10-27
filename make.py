"""Cross-platform task runner (Windows has no `make`; the Makefile just calls this file).

    python make.py setup       create .venv and install the project + test dependencies
    python make.py run         process the demo day: live logs, alerts, summary
    python make.py test        run the pytest suite
    python make.py demo        scene-by-scene demo for the recorded video
    python make.py benchmark   scalability benchmark (10k / 50k / 100k frames)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python() -> str:
    """Use the project's .venv when it exists, otherwise whatever Python runs this script."""
    candidate = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate) if candidate.exists() else sys.executable


def sh(*args: str) -> None:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    print("$", " ".join(args), flush=True)
    result = subprocess.run(list(args), cwd=ROOT, env=env)
    if result.returncode:
        sys.exit(result.returncode)


def app(*args: str) -> None:
    sh(venv_python(), "-m", "drone_security", *args)


def setup() -> None:
    if not VENV.exists():
        sh(sys.executable, "-m", "venv", str(VENV))
    sh(venv_python(), "-m", "pip", "install", "--upgrade", "pip")
    sh(venv_python(), "-m", "pip", "install", "-e", ".[dev]")


TARGETS = {
    "setup": setup,
    "run": lambda: app("run"),
    "test": lambda: sh(venv_python(), "-m", "pytest"),
    "demo": lambda: app("run", "--verbose"),
    "benchmark": lambda: app("benchmark"),
}


def main() -> None:
    names = sys.argv[1:] or ["help"]
    for name in names:
        if name not in TARGETS:
            print(__doc__)
            sys.exit(0 if name == "help" else 2)
        TARGETS[name]()


if __name__ == "__main__":
    main()
