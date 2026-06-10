import asyncio
from playwright.async_api import Page

seen_messages = set()


async def poll_chat(page: Page, on_message):
    """Poll Zoom in-meeting chat every 2 seconds for new messages."""
    # Open chat panel
    try:
        chat_btn = page.locator("[aria-label*='Chat'], button:has-text('Chat')").first
        await chat_btn.click(timeout=5000)
        await asyncio.sleep(1)
    except Exception:
        pass

    while True:
        try:
            msgs = await page.locator(".chat-message__text, .zmWebChatMessage").all_inner_texts()
            for text in msgs:
                text = text.strip()
                if text and text not in seen_messages:
                    seen_messages.add(text)
                    await on_message(text)
        except Exception:
            pass
        await asyncio.sleep(2)
