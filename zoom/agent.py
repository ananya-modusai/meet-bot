import asyncio
import argparse
import logging
import os
import re
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from anthropic import AsyncAnthropic

from audio import stream_stt, speak
import meeting as meet_mod

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

os.environ.setdefault("DISPLAY", ":99")

os.makedirs("logs", exist_ok=True)
log_file = f"logs/zoom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("zoom-agent")
log.info(f"Logging to {log_file}")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

SYSTEM_PROMPT = (
    "You are a voice assistant in a Zoom call. "
    "Answer questions conversationally and concisely — responses will be spoken aloud. "
    "No bullet points, no markdown, plain spoken sentences only."
)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
zoom_tab = None


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


def build_web_client_url(meeting_url: str) -> str:
    """Convert a Zoom meeting URL to the web client URL to avoid app install prompts."""
    # Extract meeting ID from URLs like:
    # https://zoom.us/j/12345678901
    # https://us04web.zoom.us/j/12345678901?pwd=xxx
    match = re.search(r"/j/(\d+)", meeting_url)
    if not match:
        return meeting_url  # Return as-is if we can't parse it
    meeting_id = match.group(1)
    pwd_match = re.search(r"[?&]pwd=([^&]+)", meeting_url)
    pwd = f"?pwd={pwd_match.group(1)}" if pwd_match else ""
    return f"https://zoom.us/wc/{meeting_id}/join{pwd}"


async def main():
    global zoom_tab

    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", required=True, help="Zoom meeting URL")
    parser.add_argument("--name", default="Doc Agent", help="Display name in meeting")
    parser.add_argument("--password", default="", help="Meeting password if required")
    args = parser.parse_args()

    # 1. Launch Chrome
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

    os.makedirs("logs", exist_ok=True)
    chrome_log = open("logs/chrome.log", "w")
    chrome_proc = subprocess.Popen(
        [
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
        ],
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
            ["camera", "microphone"], origin="https://zoom.us"
        )

        zoom_tab = await context.new_page()
        os.makedirs("screenshots", exist_ok=True)

        # 2. Navigate to Zoom web client URL
        web_url = build_web_client_url(args.meeting)
        log.info(f"[Zoom] Navigating to web client: {web_url}")
        await zoom_tab.goto(web_url, wait_until="domcontentloaded", timeout=60000)

        await zoom_tab.screenshot(path="screenshots/zoom_1_loaded.png")
        log.info("[Screenshot] zoom_1_loaded.png")
        await asyncio.sleep(2)
        await zoom_tab.screenshot(path="screenshots/zoom_2_after2s.png")

        # 3. Handle password prompt if present
        try:
            pwd_input = zoom_tab.locator("input#inputpasscode, input[type='password']").first
            await pwd_input.wait_for(timeout=3000)
            await pwd_input.fill(args.password or "")
            submit = zoom_tab.locator("button#passcodeBtn, button:has-text('Join Meeting')").first
            await submit.click(timeout=3000)
            log.info("[Zoom] Password entered.")
            await asyncio.sleep(2)
        except Exception:
            pass

        # 4. Enter name
        try:
            name_input = zoom_tab.locator(
                "input#inputname, "
                "input[aria-label='Your Name'], "
                "input[aria-label*='name' i], "
                "input[placeholder*='name' i], "
                "input[type='text']"
            ).first
            await name_input.wait_for(timeout=20000)
            await name_input.fill(args.name)
            log.info(f"[Zoom] Name entered: {args.name}")
        except Exception as e:
            log.warning(f"[Zoom] Name input not found: {e}")

        await zoom_tab.screenshot(path="screenshots/zoom_3_pre_join.png")
        log.info("[Screenshot] zoom_3_pre_join.png")

        # 5. Click Join
        try:
            join_btn = zoom_tab.locator("button#joinBtn, button:has-text('Join'), button:has-text('Join Meeting')").first
            await join_btn.wait_for(timeout=10000)
            await join_btn.click()
            log.info("[Zoom] Join clicked.")
        except Exception as e:
            log.error(f"[Zoom] Could not click Join: {e}")

        await asyncio.sleep(3)
        await zoom_tab.screenshot(path="screenshots/zoom_4_post_join.png")
        log.info("[Screenshot] zoom_4_post_join.png")

        # 6. Handle "Join Audio by Computer" dialog
        try:
            audio_btn = zoom_tab.locator(
                "button:has-text('Join Audio by Computer'), "
                "button:has-text('Join with Computer Audio'), "
                "button[aria-label*='computer audio']"
            ).first
            await audio_btn.wait_for(timeout=8000)
            await audio_btn.click()
            log.info("[Zoom] Joined computer audio.")
        except Exception:
            log.info("[Zoom] No audio dialog (or already handled).")

        await asyncio.sleep(2)
        await zoom_tab.screenshot(path="screenshots/zoom_5_in_meeting.png")
        log.info("[Screenshot] zoom_5_in_meeting.png")

        # 7. Start listening
        log.info("[Agent] Listening for voice and chat...")
        await asyncio.gather(
            stream_stt(handle_input),
            meet_mod.poll_chat(zoom_tab, handle_input),
        )


if __name__ == "__main__":
    asyncio.run(main())
