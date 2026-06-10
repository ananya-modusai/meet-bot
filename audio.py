import asyncio
import json
import os
import pyaudio
import httpx
import websockets

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000

is_speaking = False


async def stream_stt(on_transcript):
    """
    Capture audio from VirtualSpeaker.monitor via PyAudio and stream to
    Deepgram Nova-3 STT over raw WebSocket. Calls on_transcript(text) when
    an utterance ends (speech_final=True).
    """
    global is_speaking

    url = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-2"
        "&language=en-US"
        "&smart_format=true"
        "&endpointing=500"
        "&encoding=linear16"
        f"&sample_rate={RATE}"
        "&channels=1"
    )

    pa = pyaudio.PyAudio()

    device_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if "VirtualSpeaker" in info["name"] and info["maxInputChannels"] > 0:
            device_index = i
            break

    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=CHUNK,
    )

    print("[STT] Listening...")

    try:
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
        ) as ws:

            async def send_audio():
                try:
                    while True:
                        data = stream.read(CHUNK, exception_on_overflow=False)
                        await ws.send(data)
                        await asyncio.sleep(0)
                except Exception as e:
                    print(f"[STT] Send error: {e}")

            async def receive_transcripts():
                try:
                    async for message in ws:
                        if is_speaking:
                            continue
                        msg = json.loads(message)
                        if msg.get("type") == "Results":
                            alts = msg.get("channel", {}).get("alternatives", [])
                            if alts and msg.get("speech_final"):
                                transcript = alts[0].get("transcript", "").strip()
                                if transcript:
                                    await on_transcript(transcript)
                except Exception as e:
                    print(f"[STT] Receive error: {e}")

            await asyncio.gather(send_audio(), receive_transcripts())
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


async def speak(text: str):
    """
    Send text to Deepgram Aura-2 TTS, receive audio bytes, play through
    VirtualMic via PyAudio so meeting participants hear the agent.
    """
    global is_speaking
    is_speaking = True

    url = "https://api.deepgram.com/v1/speak?model=aura-2-en&encoding=linear16&sample_rate=16000"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "application/json",
    }

    pa = pyaudio.PyAudio()

    device_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if "VirtualMic" in info["name"] and info["maxOutputChannels"] > 0:
            device_index = i
            break

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        output=True,
        output_device_index=device_index,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream("POST", url, headers=headers, json={"text": text}) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=CHUNK):
                    stream.write(chunk)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        is_speaking = False
