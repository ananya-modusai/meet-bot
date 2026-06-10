import asyncio
import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from anthropic import AsyncAnthropic

from document import prepare_document
from audio import tts
import meeting as recall

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

os.makedirs("logs", exist_ok=True)
log_file = f"logs/recall_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger("recall-agent")
log.info(f"Logging to {log_file}")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Claude tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "navigate_to_page",
        "description": (
            "Navigate the PDF document to a specific page. Use this whenever "
            "your answer references a particular page, or the user asks to see "
            "a section of the document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "description": "1-indexed page number to navigate to.",
                }
            },
            "required": ["page"],
        },
    },
    {
        "name": "open_document",
        "description": "Open and display the PDF document (e.g. when the user asks to see or share it).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

SYSTEM_PROMPT_TEMPLATE = """You are a voice assistant in a meeting. You have full knowledge of this document:

{doc_text}

Rules:
- Responses will be spoken aloud — be concise, conversational, no bullet points or markdown.
- When your answer comes from a specific page, use the navigate_to_page tool.
- If asked to show or share the document, use the open_document tool.
- Always answer in plain spoken language suitable for text-to-speech.
"""

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

bot_id: str = ""
doc_text: str = ""
conversation: list = []
responding = False  # prevent overlapping responses


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def execute_tool(name: str, inputs: dict) -> str:
    if name == "navigate_to_page":
        page = inputs.get("page", 1)
        log.info(f"[Tool] navigate_to_page({page})")
        # In a future screenshare extension, this would drive a PDF viewer.
        # For now, log and confirm so Claude gets a valid tool result.
        return f"Navigated to page {page}."

    if name == "open_document":
        log.info("[Tool] open_document()")
        return "Document is now open and visible."

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Main response handler
# ---------------------------------------------------------------------------

async def handle_utterance(speaker: str, text: str) -> None:
    global responding

    if responding:
        log.info(f"[Input] Dropped (busy): [{speaker}] {text}")
        return

    responding = True
    log.info(f"[Input] [{speaker}]: {text}")

    try:
        conversation.append({"role": "user", "content": f"[{speaker}]: {text}"})

        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT_TEMPLATE.format(doc_text=doc_text),
            tools=TOOLS,
            messages=conversation,
        )

        # Collect text parts and tool calls from Claude's response
        spoken_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                spoken_parts.append(block.text.strip())
            elif block.type == "tool_use":
                tool_calls.append(block)

        spoken_text = " ".join(spoken_parts)
        log.info(f"[Claude] text='{spoken_text}' tools={[t.name for t in tool_calls]}")

        # Add Claude's response to conversation history
        conversation.append({"role": "assistant", "content": response.content})

        # Fire TTS and tool calls in parallel so speech and action happen together
        tasks = []
        if spoken_text:
            tasks.append(_speak(spoken_text))
        for tc in tool_calls:
            tasks.append(_run_tool(tc))

        if tasks:
            await asyncio.gather(*tasks)

        # If Claude stopped because of tool use, send tool results back and
        # get a follow-up response (in case it wants to say something after)
        if tool_calls and response.stop_reason == "tool_use":
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": await execute_tool(tc.name, tc.input),
                }
                for tc in tool_calls
            ]
            conversation.append({"role": "user", "content": tool_results})

    except Exception as e:
        log.error(f"[Agent] Error: {e}")
        try:
            await _speak("Sorry, I ran into an issue with that.")
        except Exception:
            pass
    finally:
        responding = False


async def _speak(text: str) -> None:
    try:
        pcm = await tts(text)
        await recall.output_audio(bot_id, pcm)
    except Exception as e:
        log.error(f"[TTS] {e}")


async def _run_tool(tc) -> None:
    result = await execute_tool(tc.name, tc.input)
    log.info(f"[Tool] {tc.name} → {result}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    global bot_id, doc_text

    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", required=True, help="Meeting URL (Zoom, Meet, Teams)")
    parser.add_argument("--doc", default="", help="Path to PDF document")
    args = parser.parse_args()

    # Load document if provided
    if args.doc:
        log.info(f"[Doc] Loading {args.doc}...")
        doc_text = prepare_document(args.doc)
        log.info(f"[Doc] Loaded ~{len(doc_text.split())} words.")
    else:
        doc_text = "No document loaded."

    # Create and join via Recall.ai
    bot_id = await recall.create_bot(args.meeting, bot_name="Doc Agent")
    log.info(f"[Recall] Waiting for bot {bot_id} to join...")
    await recall.wait_for_join(bot_id, timeout=120)
    log.info("[Recall] Bot is live. Listening...")

    try:
        await recall.poll_transcript(bot_id, handle_utterance, poll_interval=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        await recall.leave_meeting(bot_id)


if __name__ == "__main__":
    asyncio.run(main())
