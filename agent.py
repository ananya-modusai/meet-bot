import asyncio
import argparse
import json
import logging
import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from anthropic import AsyncAnthropic

from document import prepare_document
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

SYSTEM_PROMPT_TEMPLATE = """You are a voice assistant in a Google Meet.
You have full knowledge of this document:

{full_document_text}

Rules:
- Keep answers concise — this is spoken audio, not text
- No bullet points, no markdown — plain conversational speech
- Always identify which page the answer is from
- If asked to share/show/display the document, respond with page 1

Respond ONLY in this JSON format:
{{"answer": "conversational spoken response here", "page": 1}}"""

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

system_prompt = ""
pdf_tab = None
meet_tab = None


async def go_to_page(page_num: int):
    if pdf_tab:
        try:
            await pdf_tab.evaluate(f"window.goToPage({page_num})")
        except Exception as e:
            log.warning(f"[PDF] Page nav error: {e}")


async def ask_claude(question: str) -> dict:
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": question}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


async def handle_input(text: str):
    log.info(f"[Input] {text}")

    if meet_mod.has_share_intent(text):
        await meet_mod.maybe_start_screenshare(meet_tab, pdf_tab)
        await speak("Sure, sharing the document now.")
        await go_to_page(1)
        return

    try:
        result = await ask_claude(text)
        answer = result.get("answer", "Sorry, I couldn't find that.")
        page = result.get("page")
        log.info(f"[Claude] page={page} answer={answer}")
        if page:
            await go_to_page(page)
        await speak(answer)
    except Exception as e:
        log.error(f"[Claude] Error: {e}")
        await speak("Sorry, I ran into an issue answering that.")


def start_recording():
    os.makedirs("recordings", exist_ok=True)
    cmd = (
        "ffmpeg -f x11grab -r 30 -s 1280x720 -i :99 "
        "-f pulse -i VirtualSpeaker.monitor "
        "-c:v libx264 -c:a aac "
        f"recordings/$(date +%Y%m%d_%H%M%S).mp4"
    )
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("[Recording] FFmpeg started.")


async def main():
    global system_prompt, pdf_tab, meet_tab

    parser = argparse.ArgumentParser()
    parser.add_argument("--meet", required=True, help="Google Meet URL")
    parser.add_argument("--doc", required=True, help="Path to PDF document")
    args = parser.parse_args()

    # 1. Extract PDF text
    log.info(f"[Doc] Loading {args.doc}...")
    doc_text = prepare_document(args.doc)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(full_document_text=doc_text)
    log.info(f"[Doc] Loaded. ~{len(doc_text.split())} words.")

    # 2. Start recording
    start_recording()

    # 3. Launch Chrome via subprocess, connect over CDP
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
            ["camera", "microphone"], origin="https://meet.google.com"
        )

        # 4. Open PDF viewer tab
        viewer_path = Path(__file__).parent / "viewer.html"
        pdf_url = f"file://{viewer_path}?file={Path(args.doc).resolve()}"
        pdf_tab = await context.new_page()
        try:
            await pdf_tab.goto(pdf_url, wait_until="domcontentloaded", timeout=15000)
            log.info("[PDF] Viewer opened.")
        except Exception as e:
            log.warning(f"[PDF] Viewer load warning (non-fatal): {e}")

        # 5. Join Google Meet
        meet_tab = await context.new_page()
        log.info(f"[Meet] Navigating to {args.meet} ...")
        await meet_tab.goto(args.meet, wait_until="domcontentloaded", timeout=60000)
        log.info("[Meet] Page loaded.")

        os.makedirs("screenshots", exist_ok=True)
        for i in range(1, 6):
            await meet_tab.screenshot(path=f"screenshots/meet_{i}.png")
            log.info(f"[Screenshot] screenshots/meet_{i}.png")
            await asyncio.sleep(1)

        # Enter name (guest flow)
        try:
            name_input = meet_tab.locator("input[placeholder='Your name']")
            await name_input.wait_for(timeout=8000)
            await name_input.fill("Doc Agent")
            log.info("[Meet] Name entered.")
        except Exception:
            log.info("[Meet] No name prompt (signed in).")

        # Turn off camera
        try:
            cam_btn = meet_tab.locator("[aria-label*='Turn off camera']").first
            await cam_btn.click(timeout=3000)
            log.info("[Meet] Camera turned off.")
        except Exception:
            pass

        await meet_tab.screenshot(path="screenshots/meet_6_pre_join.png")
        log.info("[Screenshot] screenshots/meet_6_pre_join.png")

        # Dismiss tooltip if present, then click Join
        try:
            await meet_tab.locator("button:has-text('Got it')").click(timeout=2000)
        except Exception:
            pass

        join_btn = meet_tab.locator(
            "button:has-text('Join now'), button:has-text('Ask to join')"
        ).first
        await join_btn.wait_for(timeout=20000)
        log.info("[Meet] Join button found, clicking...")
        await join_btn.click(timeout=15000)
        log.info("[Meet] Join request sent.")

        await asyncio.sleep(2)
        await meet_tab.screenshot(path="screenshots/meet_7_post_join.png")
        log.info("[Screenshot] screenshots/meet_7_post_join.png")

        # 6. Listen for voice and chat simultaneously
        log.info("[Agent] Listening for voice and chat...")
        await asyncio.gather(
            stream_stt(handle_input),
            meet_mod.poll_chat(meet_tab, handle_input),
        )


if __name__ == "__main__":
    asyncio.run(main())
