import asyncio
import json
import os
import httpx
import websockets

def _get_key():
    return os.getenv("DEEPGRAM_API_KEY")

RATE = 16000
CHUNK = 2048  # bytes (1024 s16le samples = 64ms at 16kHz)

is_speaking = False


async def stream_stt(on_transcript):
    """
    Capture meeting audio from VirtualSpeaker.monitor via parec and stream to
    Deepgram Nova-2 STT. VirtualSpeaker is the PulseAudio sink where Chrome/Meet
    routes all meeting audio output.
    """
    global is_speaking

    url = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-2"
        "&language=en-US"
        "&smart_format=true"
        "&endpointing=200"
        "&encoding=linear16"
        f"&sample_rate={RATE}"
        "&channels=1"
    )

    proc = await asyncio.create_subprocess_exec(
        "parec",
        "--device=VirtualSpeaker.monitor",
        "--format=s16le",
        f"--rate={RATE}",
        "--channels=1",
        stdout=asyncio.subprocess.PIPE,
    )
    print("[STT] parec started, connecting to Deepgram...")

    transcript_queue = asyncio.Queue(maxsize=1)

    try:
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {_get_key()}"},
        ) as ws:
            print("[STT] Connected to Deepgram.")

            async def send_audio():
                try:
                    while True:
                        data = await proc.stdout.read(CHUNK)
                        if not data:
                            break
                        if not is_speaking:
                            await ws.send(data)
                except Exception as e:
                    print(f"[STT] Send error: {e}")

            async def receive_transcripts():
                try:
                    async for message in ws:
                        msg = json.loads(message)
                        if msg.get("type") == "Results":
                            alts = msg.get("channel", {}).get("alternatives", [])
                            if alts and msg.get("speech_final"):
                                transcript = alts[0].get("transcript", "").strip()
                                if transcript and not is_speaking:
                                    while not transcript_queue.empty():
                                        try:
                                            transcript_queue.get_nowait()
                                        except asyncio.QueueEmpty:
                                            break
                                    try:
                                        transcript_queue.put_nowait(transcript)
                                    except asyncio.QueueFull:
                                        pass
                except Exception as e:
                    print(f"[STT] Receive error: {e}")

            async def process_transcripts():
                while True:
                    transcript = await transcript_queue.get()
                    await on_transcript(transcript)

            await asyncio.gather(send_audio(), receive_transcripts(), process_transcripts())
    finally:
        proc.kill()
        await proc.wait()


async def speak(text: str):
    """
    Fetch TTS audio from Deepgram and play it to TTSSink via pacat.
    VirtualMic monitors TTSSink.monitor, so Chrome's Zoom/Meet mic hears the bot.
    """
    global is_speaking
    is_speaking = True

    url = "https://api.deepgram.com/v1/speak?model=aura-odysseus-en&encoding=linear16&sample_rate=16000"
    headers = {
        "Authorization": f"Token {_get_key()}",
        "Content-Type": "application/json",
    }

    proc = await asyncio.create_subprocess_exec(
        "pacat",
        "--playback",
        "--device=TTSSink",
        "--format=s16le",
        "--rate=16000",
        "--channels=1",
        stdin=asyncio.subprocess.PIPE,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream("POST", url, headers=headers, json={"text": text}) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=CHUNK):
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
        proc.stdin.close()
        await proc.wait()
    except Exception as e:
        print(f"[TTS] Error: {e}")
        proc.kill()
        await proc.wait()
    finally:
        is_speaking = False
