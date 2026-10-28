"""Headless browser check of every UI view: python make.py ui-check

Starts the UI server on a spare port, opens each view in Chromium (Playwright), fails on console
errors or missing key elements, runs the main flows (replay raises an alert, a query returns frames,
the agent answers with citations, the test suite runs, the guided demo advances) and saves
screenshots to docs/screenshots/.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
FALLBACK_CHROMIUM = "/opt/pw-browsers/chromium"  # preinstalled browser in some sandboxes


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        if Path(FALLBACK_CHROMIUM).exists():
            return p.chromium.launch(executable_path=FALLBACK_CHROMIUM)
        raise


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright is not installed: pip install playwright && python -m playwright install chromium")
    SHOTS.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, DSA_OFFLINE=os.environ.get("DSA_OFFLINE", "1"))  # deterministic run unless overridden
    server = subprocess.Popen([sys.executable, str(ROOT / "run_ui.py"), "--no-browser", "--port", str(port)], cwd=ROOT, env=env)
    failures: list[str] = []
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/api/status", timeout=1)
                break
            except OSError:
                time.sleep(0.3)
        else:
            sys.exit("UI server did not start")

        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            def check(name: str, fn) -> None:
                errors.clear()
                try:
                    fn()
                    page.wait_for_timeout(900)  # let entry animations and count-ups finish
                    page.screenshot(path=str(SHOTS / f"{name}.png"))
                    if errors:
                        raise AssertionError(f"console errors: {errors}")
                    print(f"  ok   {name}")
                except Exception as exc:  # keep going: report every broken screen at once
                    failures.append(f"{name}: {exc}")
                    print(f"  FAIL {name}: {exc}")

            def live():
                page.goto(f"{base}/#/mission")  # a link from the earlier layout redirects to its new view
                page.wait_for_selector("#map #drone")
                assert page.url.endswith("#/live"), page.url
                page.click("#play")
                page.wait_for_selector("#mc-feed .feed-row .dot", timeout=15000)  # the 00:01 loitering alert, raised live
                page.click("#play")  # pause
                page.evaluate("[...document.querySelectorAll('.marker')].find(m => m.title.includes('CRITICAL')).click()")
                page.wait_for_function("document.querySelector('#frame-panel .sev-CRITICAL') !== null", timeout=10000)
                page.wait_for_timeout(1500)

            def alerts():
                page.goto(f"{base}/#/live/alerts")
                page.wait_for_selector("#al-list .alert-item")
                page.click("#al-list .alert-item .msg")
                page.wait_for_selector("#al-list .alert-item.open .detail .flink")
                page.click("#al-list .ack")
                page.wait_for_selector("#al-list .alert-item.acked")

            def events():
                page.goto(f"{base}/#/search/events")
                page.wait_for_selector("#ev-table tr.clickable")
                page.fill("#ev-q", "F150")
                assert page.locator("#ev-table tr.clickable").count() >= 5

            def search_trucks():
                page.goto(f"{base}/#/search")
                page.click('[data-preset="0"]')
                page.wait_for_selector("#qx-out .sql", state="attached")
                assert "truck" in page.text_content("#qx-out .sql")
                assert page.locator("#qx-out tr.clickable").count() > 0
                page.click("#qx-out details.more > summary")
                page.evaluate("document.getElementById('view').scrollTop = 0")

            def search_gate():
                page.click('[data-preset="1"]')
                page.wait_for_function("document.querySelector('#qx-out .sql') && document.querySelector('#qx-out .sql').textContent.includes('main_gate')")
                assert "main gate" in page.inner_text("#qx-active")

            def frame_drawer():
                page.click("#qx-out tr.clickable")
                page.wait_for_selector("#drawer-body .det")

            def ask():
                page.keyboard.press("Escape")
                page.goto(f"{base}/#/ask")
                page.click("#reset")
                page.wait_for_selector(".starter")
                page.click('.starter[data-q="Did the same truck enter twice?"]')
                page.wait_for_selector(".msg.bot .grounding", timeout=20000)
                page.fill("#q", "When did it leave?")
                page.click("#send")
                page.wait_for_function("document.querySelectorAll('.msg.bot .grounding').length >= 2", timeout=20000)
                assert page.locator(".msg.bot .flink").count() >= 3, "answers should cite clickable frames"
                assert page.locator(".msg.bot .tool-call").count() >= 2
                page.locator(".msg.bot details.more > summary").first.click()

            def identities():
                page.goto(f"{base}/#/insights/identities?id=V-002")
                page.wait_for_selector("#detail .trait")

            def briefing():
                page.goto(f"{base}/#/insights")
                page.wait_for_selector(".hero-quote")
                page.locator("#hour-detail details.node > summary").first.click()
                page.wait_for_function("!document.querySelector('#hour-detail details.node .frames').textContent.includes('Loading')")
                page.evaluate("document.getElementById('view').scrollTop = 0")

            def memory():
                page.goto(f"{base}/#/insights/memory")
                page.wait_for_selector(".ctx-bar")

            def tests():
                page.goto(f"{base}/#/proof/tests")
                page.click("#run")
                page.wait_for_selector(".test-group", timeout=180000)
                assert "PASS" in page.inner_text("#qa"), page.inner_text("#qa")[:500]

            def results():
                page.goto(f"{base}/#/proof")
                page.wait_for_selector("#c-prf")
                page.click('#ev-tabs [data-v="scale"]')
                page.wait_for_selector("#c-fps")
                page.click('#ev-tabs [data-v="accuracy"]')
                page.wait_for_selector("#c-prf")
                page.wait_for_timeout(700)

            def architecture():
                page.goto(f"{base}/#/proof/architecture")
                page.wait_for_selector(".arch img")
                page.wait_for_function("[...document.querySelectorAll('.arch img')].every(i => i.complete && i.naturalWidth > 0)")

            def demo():
                page.goto(f"{base}/#/live")
                page.wait_for_selector("#tele")
                page.click("#demo-btn")
                page.wait_for_selector("#demo .step")
                page.keyboard.press("ArrowRight")  # moves the tour, not the replay
                page.wait_for_function("document.querySelector('#demo .step').textContent.startsWith('Step 2')")
                page.wait_for_selector(".demo-highlight")

            def light_theme():
                page.click("#d-stop")
                page.goto(f"{base}/#/insights/identities?id=V-002")
                page.wait_for_selector("#detail .trait")
                page.click("#theme-btn")
                page.wait_for_selector("#detail .trait")
                assert page.evaluate("document.documentElement.dataset.theme") == "light"

            checks = [("01_live_mission", live), ("02_live_alerts", alerts), ("03_event_log", events),
                      ("04_search_trucks", search_trucks), ("05_search_main_gate", search_gate), ("06_frame_drawer", frame_drawer),
                      ("07_ask_ai", ask), ("08_identities", identities), ("09_briefing", briefing), ("10_memory", memory),
                      ("11_tests", tests), ("12_results", results), ("13_architecture", architecture), ("14_demo_mode", demo),
                      ("15_light_theme", light_theme)]
            for name, fn in checks:
                check(name, fn)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} UI checks passed; screenshots in {SHOTS.relative_to(ROOT)}")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
