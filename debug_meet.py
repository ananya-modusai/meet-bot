"""
Standalone debug script: opens Meet, takes screenshots at each step.
Usage: python3 debug_meet.py --meet "https://meet.google.com/xxx-xxx-xxx"
Screenshots saved to /tmp/meet_debug_*.png
"""
import asyncio
import argparse
import os
import socket
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
os.environ.setdefault("DISPLAY", ":99")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--meet", required=True)
    args = parser.parse_args()

    _chrome_candidates = [
        os.getenv("CHROME_EXEC", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        str(Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    ]
    CHROME_EXEC = next((c for c in _chrome_candidates if c and Path(c).exists()), None)
    print(f"[+] Chrome: {CHROME_EXEC}")

    proc = subprocess.Popen(
        [CHROME_EXEC, "--remote-debugging-port=9222", "--no-sandbox",
         "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
         "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
         "--no-first-run", "--no-default-browser-check"],
        env={**os.environ, "DISPLAY": ":99"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        await asyncio.sleep(0.5)
        try:
            s = socket.create_connection(("127.0.0.1", 9222), timeout=1)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            continue
    print("[+] CDP ready")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = await browser.new_context(permissions=["camera", "microphone"])
        await context.grant_permissions(["camera", "microphone"], origin="https://meet.google.com")
        page = await context.new_page()

        print(f"[+] Navigating to {args.meet}")
        await page.goto(args.meet, wait_until="domcontentloaded", timeout=60000)

        for i in range(1, 6):
            await page.screenshot(path=f"/tmp/meet_{i}.png")
            print(f"[+] Screenshot {i} -> /tmp/meet_{i}.png")
            await asyncio.sleep(1)

        # Try name input
        try:
            name_input = page.locator("input[placeholder='Your name']")
            await name_input.wait_for(timeout=5000)
            await name_input.fill("Doc Agent")
            print("[+] Name entered")
        except Exception:
            print("[+] No name prompt")

        await asyncio.sleep(1)
        await page.screenshot(path="/tmp/meet_6_pre_join.png")
        print("[+] Screenshot 6: pre-join -> /tmp/meet_6_pre_join.png")

        # Log all visible buttons
        buttons = await page.locator("button").all()
        print(f"[+] Buttons on page ({len(buttons)}):")
        for btn in buttons:
            try:
                txt = await btn.inner_text()
                lbl = await btn.get_attribute("aria-label")
                dis = await btn.get_attribute("disabled")
                print(f"    text={repr(txt.strip()[:40])} aria-label={repr(lbl)} disabled={dis}")
            except Exception:
                pass

        proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
