"""
TTS only — Recall.ai handles STT via its transcription pipeline.
Generates s16le PCM audio from text using Deepgram Aura (Odysseus voice).
"""
import os
import httpx

RATE = 16000
CHUNK = 2048


def _get_deepgram_key():
    return os.getenv("DEEPGRAM_API_KEY")


async def tts(text: str) -> bytes:
    """
    Call Deepgram Aura TTS and return raw s16le PCM bytes at 16kHz mono.
    """
    url = "https://api.deepgram.com/v1/speak?model=aura-odysseus-en&encoding=linear16&sample_rate=16000"
    headers = {
        "Authorization": f"Token {_get_deepgram_key()}",
        "Content-Type": "application/json",
    }
    audio_chunks = []
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("POST", url, headers=headers, json={"text": text}) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=CHUNK):
                audio_chunks.append(chunk)
    return b"".join(audio_chunks)
