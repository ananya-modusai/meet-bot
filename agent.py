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

# Anthropic client
client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Will be set after PDF is loaded
system_prompt = ""
pdf_tab = None
meet_tab = None


async def go_to_page(page_num: int):
    """Navigate the PDF.js viewer to a given page."""
    if pdf_tab:
        try:
            await pdf_tab.evaluate(f"window.goToPage({page_num})")
        except Exception as e:
            print(f"[PDF] Page nav error: {e}")


async def ask_claude(question: str) -> dict:
    """Send question to Claude with prompt caching on the document."""
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
    # Strip markdown code fences if Claude wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


async def handle_input(text: str):
    """Central handler for both voice and chat input."""
    log.info(f"[Input] {text}")

    # Check for share intent first
    if meet_mod.has_share_intent(text):
        await meet_mod.maybe_start_screenshare(meet_tab, pdf_tab)
        await speak("Sure, sharing the document now.")
        await go_to_page(1)
        return

    # Ask Claude
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
    """Start FFmpeg recording in background."""
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

    # 3. Launch browser
    # We launch Chrome manually and connect via CDP to avoid the --remote-debugging-pipe
    # SIGTRAP crash that occurs on EC2 when Playwright manages the process directly.
    # Resolve Chrome binary: prefer env override, then common system paths
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
    log.info(f"[Browser] Using Chrome binary: {CHROME_EXEC}")

    chrome_args = [
        CHROME_EXEC,
        "--remote-debugging-port=9222",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    chrome_env = {**os.environ, "DISPLAY": ":99"}
    os.makedirs("logs", exist_ok=True)
    chrome_log = open("logs/chrome.log", "w")
    chrome_proc = subprocess.Popen(
        chrome_args,
        env=chrome_env,
        stdout=chrome_log,
        stderr=chrome_log,
    )
    log.info(f"[Browser] Chrome launched (pid={chrome_proc.pid}), waiting for CDP...")
    # Poll port 9222 until Chrome is ready (up to 20 seconds)
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
        context = await browser.new_context(
            permissions=["camera", "microphone"],
        )
        # Grant permissions scoped to Google Meet origin so no browser popup appears
        await context.grant_permissions(
            ["camera", "microphone"],
            origin="https://meet.google.com",
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
        await meet_tab.goto(args.meet, wait_until="domcontentloaded", timeout=60000)

        # Handle name prompt (shown when joining without a Google account)
        try:
            name_input = meet_tab.locator("input[placeholder='Your name']")
            await name_input.wait_for(timeout=8000)
            await name_input.click()
            await name_input.fill("")
            await name_input.type("Doc Agent", delay=50)
            log.info("[Meet] Name entered.")
        except Exception:
            pass  # Already signed in — no name prompt shown

        # Turn off camera
        try:
            cam_btn = meet_tab.locator("[aria-label*='Turn off camera']").first
            await cam_btn.click(timeout=3000)
        except Exception:
            pass

        # Click Join — works for both signed-in ("Join now") and guest ("Ask to join")
        join_btn = meet_tab.locator(
            "button:has-text('Join now'), button:has-text('Ask to join')"
        ).first
        await join_btn.wait_for(timeout=15000)
        await join_btn.click()
        log.info("[Meet] Joined the call.")

        # 6. Start chat polling and STT concurrently
        await asyncio.gather(
            stream_stt(handle_input),
            meet_mod.poll_chat(meet_tab, handle_input),
        )


if __name__ == "__main__":
    asyncio.run(main())
