"""
Recall.ai API wrapper.
Handles bot lifecycle: create → join → stream transcripts → output audio.
"""
import asyncio
import io
import logging
import os
import struct
import time
import httpx

log = logging.getLogger("recall.meeting")

BASE_URL = "https://api.recall.ai/api/v1"


def _headers():
    return {
        "Authorization": f"Token {os.getenv('RECALL_API_KEY')}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------

async def create_bot(meeting_url: str, bot_name: str = "Doc Agent") -> str:
    """Create a Recall.ai bot and return its ID."""
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "transcription_options": {
            "provider": "deepgram",
            "deepgram_api_token": os.getenv("DEEPGRAM_API_KEY"),
        },
        "recording_config": {
            "transcript": {"speaker_labels": True},
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/bot/", headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()
    bot_id = data["id"]
    log.info(f"[Recall] Bot created: {bot_id}")
    return bot_id


async def get_bot_status(bot_id: str) -> str:
    """Return the bot's current status string."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE_URL}/bot/{bot_id}/", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    # status_changes is a list; latest entry has the current status
    status_changes = data.get("status_changes", [])
    if status_changes:
        return status_changes[-1]["code"]
    return data.get("status", {}).get("code", "unknown")


async def wait_for_join(bot_id: str, timeout: int = 120) -> None:
    """Block until the bot is in the call (or raise on timeout/fatal)."""
    joined = {"in_call_not_recording", "in_call_recording"}
    fatal = {"call_ended", "done", "fatal"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = await get_bot_status(bot_id)
        log.info(f"[Recall] Bot status: {status}")
        if status in joined:
            log.info("[Recall] Bot is in the meeting.")
            return
        if status in fatal:
            raise RuntimeError(f"Bot reached terminal status before joining: {status}")
        await asyncio.sleep(3)
    raise TimeoutError(f"Bot did not join within {timeout}s")


async def leave_meeting(bot_id: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/bot/{bot_id}/leave_call/", headers=_headers(), json={}
        )
        resp.raise_for_status()
    log.info("[Recall] Bot left the meeting.")


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------

async def poll_transcript(bot_id: str, on_utterance, poll_interval: float = 1.0) -> None:
    """
    Poll the bot's transcript endpoint and call on_utterance(speaker, text)
    for each new completed utterance.

    Recall returns the full transcript list on every call; we track the last
    seen word index to surface only new content.
    """
    last_seen_count = 0   # total words processed so far
    pending: dict[str, list] = {}  # speaker → [word, ...] building up
    silence_thresh = 1.5  # seconds of no new words before we fire the utterance

    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{BASE_URL}/bot/{bot_id}/transcript/", headers=_headers()
                )
                resp.raise_for_status()
                segments = resp.json()  # list of {speaker, words: [{text, start_time, end_time}]}
        except Exception as e:
            log.warning(f"[Recall] Transcript poll error: {e}")
            await asyncio.sleep(poll_interval)
            continue

        # Flatten to (speaker, word, end_time) triples
        all_words = []
        for seg in segments:
            speaker = seg.get("speaker") or "Unknown"
            for w in seg.get("words", []):
                all_words.append((speaker, w.get("text", ""), w.get("end_time", 0)))

        new_words = all_words[last_seen_count:]
        if new_words:
            last_seen_count = len(all_words)
            # Group consecutive words by speaker
            for speaker, text, end_time in new_words:
                if speaker not in pending:
                    pending[speaker] = []
                pending[speaker].append((text, end_time))

        # Fire any speaker whose last word was > silence_thresh seconds ago
        now = time.time()
        for speaker, words in list(pending.items()):
            if not words:
                continue
            last_end = words[-1][1]
            # end_time from Recall is seconds since meeting start; use wall-clock gap
            # as a proxy (last_end is relative, so track by lack of new words instead)
            text = " ".join(w[0] for w in words).strip()
            if text and (now - poll_interval - 0.5) > last_end:
                del pending[speaker]
                await on_utterance(speaker, text)

        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Output audio
# ---------------------------------------------------------------------------

def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw s16le PCM bytes in a minimal WAV container."""
    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits,
        b"data", len(pcm),
    )
    return header + pcm


async def output_audio(bot_id: str, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    """
    Send TTS audio (raw s16le PCM) into the meeting via Recall.ai output_audio API.
    Wraps PCM in a WAV container before upload.
    """
    wav = _pcm_to_wav(pcm_bytes, sample_rate)
    headers_no_ct = {
        "Authorization": f"Token {os.getenv('RECALL_API_KEY')}",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{BASE_URL}/bot/{bot_id}/output_audio/",
            headers=headers_no_ct,
            files={"file": ("response.wav", io.BytesIO(wav), "audio/wav")},
        )
        if resp.status_code not in (200, 201, 204):
            log.error(f"[Recall] output_audio failed {resp.status_code}: {resp.text}")
        else:
            log.info("[Recall] Audio sent to meeting.")
