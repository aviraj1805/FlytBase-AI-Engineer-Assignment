"""Cross-platform task runner (Windows has no `make`; the Makefile just calls this file).

    python make.py setup       create .venv and install the project + test dependencies
    python make.py run         process the demo day: live logs, alerts, summary
    python make.py test        run the pytest suite
    python make.py demo        scene-by-scene demo for the recorded video (offline)
    python make.py eval        offline experiments: ground-truth scoring, retrieval, Q&A -> docs/results
    python make.py eval-gemini Gemini experiments (needs GEMINI_API_KEY): models, VLM, retrieval, Q&A
    python make.py benchmark   scalability benchmark (10k / 50k / 100k / 200k frames) -> docs/results
    python make.py report      docs/REPORT.md -> docs/REPORT.pdf (needs Edge, Chrome or Chromium)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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


def evaluate_offline() -> None:
    app("eval", "--seeds", "10")
    app("eval-retrieval")
    app("ablation")
    app("eval-qa")
    app("eval-qa", "--heldout")
    if (ROOT / "data" / "eval" / "paraphrases.json").exists():  # created once by eval-gemini
        app("eval-paraphrase")


def evaluate_gemini() -> None:
    app("models")
    app("vlm")
    app("eval-retrieval", "--gemini")
    app("eval", "--seeds", "3", "--analyzer", "gemini")
    app("eval-paraphrase", "--make", "--gemini")
    app("eval-qa", "--gemini", "--pause", "1")
    app("eval-qa", "--gemini", "--heldout", "--pause", "1")


REPORT_CSS = """
@page { size: A4; margin: 16mm 15mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; line-height: 1.4; color: #1a1a1a; }
h1 { font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 13.5pt; margin-top: 18px; border-bottom: 1px solid #bbb; padding-bottom: 2px; page-break-after: avoid; }
h3 { font-size: 11pt; margin-top: 14px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #c8c8c8; padding: 3px 5px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
code, pre { font-family: Consolas, monospace; font-size: 8.5pt; background: #f6f6f6; }
pre { padding: 6px; white-space: pre-wrap; page-break-inside: avoid; }
blockquote { border-left: 3px solid #999; margin: 6px 0; padding: 2px 10px; color: #333; background: #fafafa; }
img { max-width: 100%; max-height: 215mm; display: block; margin: 8px auto; page-break-inside: avoid; }
"""


def find_browser() -> str | None:
    """Edge or Chrome/Chromium, used headless to print HTML to PDF."""
    candidates = [shutil.which(name) for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")]
    candidates += [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def report() -> None:
    try:
        from markdown_it import MarkdownIt  # installed with rich, i.e. inside the project's .venv
    except ImportError:
        if Path(sys.executable).resolve() == Path(venv_python()).resolve():
            raise
        sh(venv_python(), str(ROOT / "make.py"), "report")  # re-run inside the venv
        return

    source = ROOT / "docs" / "REPORT.md"
    target = ROOT / "docs" / "REPORT.pdf"
    body = MarkdownIt("commonmark").enable("table").render(source.read_text(encoding="utf-8"))
    base = (ROOT / "docs").as_uri() + "/"  # so relative image paths resolve from docs/
    page = (f'<!doctype html><html><head><meta charset="utf-8"><base href="{base}">'
            f"<style>{REPORT_CSS}</style></head><body>{body}</body></html>")
    browser = find_browser()
    if browser is None:
        sys.exit("No Edge/Chrome/Chromium found to print the PDF; open docs/REPORT.md in any Markdown viewer instead.")
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "report.html"
        html.write_text(page, encoding="utf-8")
        sh(browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
           f"--user-data-dir={tmp}", f"--print-to-pdf={target}", html.as_uri())
    print(f"wrote {target}")


TARGETS = {
    "setup": setup,
    "run": lambda: app("run"),
    "test": lambda: sh(venv_python(), "-m", "pytest"),
    "demo": lambda: app("demo", "--offline"),
    "eval": evaluate_offline,
    "eval-gemini": evaluate_gemini,
    "benchmark": lambda: app("benchmark"),
    "report": report,
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
