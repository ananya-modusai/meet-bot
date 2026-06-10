import asyncio
from playwright.async_api import Page

SHARE_KEYWORDS = {"share", "show", "display", "present", "pull up", "open the doc"}

seen_messages = set()
screen_shared = False


async def join_meet(browser, meet_url: str, pdf_tab):
    """Open Google Meet in a new tab and join the call."""
    context = browser.contexts[0]
    meet_tab = await context.new_page()
    await meet_tab.goto(meet_url)

    # Enter name if prompted
    try:
        name_input = meet_tab.locator("input[placeholder='Your name']")
        await name_input.wait_for(timeout=5000)
        await name_input.fill("Doc Agent")
    except Exception:
        pass  # Already signed in or no name prompt

    # Turn off camera if button is visible
    try:
        cam_btn = meet_tab.locator("[data-is-muted='false'][aria-label*='camera']")
        await cam_btn.click(timeout=3000)
    except Exception:
        pass

    # Click "Join now"
    join_btn = meet_tab.locator("button:has-text('Join now')")
    await join_btn.wait_for(timeout=15000)
    await join_btn.click()

    print("[Meet] Joined the call.")
    return meet_tab


async def poll_chat(meet_tab: Page, on_message):
    """Poll Google Meet chat box every 2 seconds for new messages."""
    global seen_messages

    # Open chat panel if not open
    try:
        chat_btn = meet_tab.locator("[aria-label*='chat'], [data-tooltip*='chat']").first
        await chat_btn.click(timeout=3000)
        await asyncio.sleep(1)
    except Exception:
        pass

    while True:
        try:
            msgs = await meet_tab.locator("[data-message-text], .GDhqjd").all_inner_texts()
            for text in msgs:
                text = text.strip()
                if text and text not in seen_messages:
                    seen_messages.add(text)
                    await on_message(text)
        except Exception:
            pass
        await asyncio.sleep(2)


async def maybe_start_screenshare(meet_tab: Page, pdf_tab: Page):
    """Start screen sharing the PDF tab if not already sharing."""
    global screen_shared
    if screen_shared:
        return

    try:
        present_btn = meet_tab.locator("[aria-label*='present'], button:has-text('Present now')").first
        await present_btn.click(timeout=5000)
        await asyncio.sleep(1)

        # Select "A window" or "A tab" option — pick the PDF.js tab
        tab_option = meet_tab.locator("text=A tab").first
        await tab_option.click(timeout=3000)
        await asyncio.sleep(1)

        # Confirm share
        share_confirm = meet_tab.locator("button:has-text('Share')").last
        await share_confirm.click(timeout=3000)

        screen_shared = True
        print("[Meet] Screen share started.")
    except Exception as e:
        print(f"[Meet] Screen share failed: {e}")


def has_share_intent(text: str) -> bool:
    """Return True if the text contains a share trigger keyword."""
    lower = text.lower()
    return any(kw in lower for kw in SHARE_KEYWORDS)
