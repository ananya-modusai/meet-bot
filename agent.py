import asyncio
import argparse
import logging
import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from anthropic import AsyncAnthropic

from audio import stream_stt, speak
import meeting as meet_mod

load_dotenv()

os.environ.setdefault("DISPLAY", ":99")

# --- Logging setup ---
os.makedirs("logs", exist_ok=True)
log_file = f"logs/agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("agent")
log.info(f"Logging to {log_file}")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

SYSTEM_PROMPT = (
    "You are a voice assistant in a Google Meet call. "
    "Answer questions conversationally and concisely — responses will be spoken aloud. "
    "No bullet points, no markdown, plain spoken sentences only."
)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
meet_tab = None


async def ask_claude(question: str) -> str:
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return response.content[0].text.strip()


async def handle_input(text: str):
    log.info(f"[Input] {text}")
    try:
        answer = await ask_claude(text)
        log.info(f"[Claude] {answer}")
        await speak(answer)
    except Exception as e:
        log.error(f"[Claude] Error: {e}")
        await speak("Sorry, I ran into an issue answering that.")


async def main():
    global meet_tab

    parser = argparse.ArgumentParser()
    parser.add_argument("--meet", required=True, help="Google Meet URL")
    args = parser.parse_args()

    # 1. Launch Chrome via subprocess and connect over CDP
    _chrome_candidates = [
        os.getenv("CHROME_EXEC", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        str(Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
    ]
    CHROME_EXEC = next((c for c in _chrome_candidates if c and Path(c).exists()), None)
    if not CHROME_EXEC:
        raise RuntimeError("No Chrome/Chromium binary found. Set CHROME_EXEC env var.")
    log.info(f"[Browser] Using: {CHROME_EXEC}")

    chrome_args = [
        CHROME_EXEC,
        "--remote-debugging-port=9222",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    chrome_log = open("logs/chrome.log", "w")
    chrome_proc = subprocess.Popen(
        chrome_args,
        env={**os.environ, "DISPLAY": ":99"},
        stdout=chrome_log,
        stderr=chrome_log,
    )
    log.info(f"[Browser] Chrome launched (pid={chrome_proc.pid}), waiting for CDP...")

    for _ in range(40):
        await asyncio.sleep(0.5)
        try:
            s = socket.create_connection(("127.0.0.1", 9222), timeout=1)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            continue
    else:
        raise RuntimeError("Chrome did not open port 9222 within 20 seconds")
    log.info("[Browser] CDP port 9222 is open.")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        log.info("[Browser] Connected via CDP.")

        context = await browser.new_context(permissions=["camera", "microphone"])
        await context.grant_permissions(
            ["camera", "microphone"], origin="https://meet.google.com"
        )

        # 2. Navigate to Google Meet
        meet_tab = await context.new_page()
        log.info(f"[Meet] Navigating to {args.meet} ...")
        await meet_tab.goto(args.meet, wait_until="domcontentloaded", timeout=60000)
        log.info("[Meet] Page loaded.")

        # 3. Enter name if prompted (guest flow)
        try:
            name_input = meet_tab.locator("input[placeholder='Your name']")
            await name_input.wait_for(timeout=8000)
            await name_input.fill("Doc Agent")
            log.info("[Meet] Name entered.")
        except Exception:
            log.info("[Meet] No name prompt (signed in).")

        # 4. Turn off camera if the button is present
        try:
            cam_btn = meet_tab.locator("[aria-label*='Turn off camera']").first
            await cam_btn.click(timeout=3000)
            log.info("[Meet] Camera turned off.")
        except Exception:
            pass

        # 5. Click Join / Ask to join
        # Meet disables the button until it verifies media devices. On a headless VM
        # the check never passes, so we strip the disabled attribute via JS then click.
        join_btn = meet_tab.locator(
            "button:has-text('Join now'), button:has-text('Ask to join')"
        ).first
        await join_btn.wait_for(timeout=20000)
        log.info("[Meet] Join button found, sending join request...")
        await asyncio.sleep(3)
        await meet_tab.evaluate("""() => {
            const btn = document.querySelector(
                'button[data-promo-anchor-id], button[jscontroller="O626Fe"]'
            );
            if (btn) {
                btn.disabled = false;
                btn.removeAttribute('disabled');
                btn.click();
            }
        }""")
        log.info("[Meet] Join request sent.")

        # 6. Listen for voice and chat simultaneously
        log.info("[Agent] Listening for voice and chat...")
        await asyncio.gather(
            stream_stt(handle_input),
            meet_mod.poll_chat(meet_tab, handle_input),
        )


if __name__ == "__main__":
    asyncio.run(main())
