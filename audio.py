import asyncio
import os
import pyaudio
import httpx
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# PyAudio config
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000

# Shared flag — agent sets this True while TTS is playing
is_speaking = False


async def stream_stt(on_transcript):
    """
    Capture audio from VirtualSpeaker.monitor via PyAudio and stream to
    Deepgram Nova-3 STT. Calls on_transcript(text) when an utterance ends.
    """
    global is_speaking

    dg = DeepgramClient(DEEPGRAM_API_KEY)
    connection = dg.listen.asyncwebsocket.v("1")

    async def on_message(self, result, **kwargs):
        if is_speaking:
            return
        alt = result.channel.alternatives[0]
        if result.speech_final and alt.transcript.strip():
            await on_transcript(alt.transcript.strip())

    connection.on(LiveTranscriptionEvents.Transcript, on_message)

    options = LiveOptions(
        model="nova-3",
        language="en-US",
        smart_format=True,
        vad_events=True,
        utterance_end_ms=1000,
    )

    await connection.start(options)

    pa = pyaudio.PyAudio()

    # Find VirtualSpeaker.monitor input device index
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
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            await connection.send(data)
            await asyncio.sleep(0)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        await connection.finish()


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
    payload = {"text": text}

    pa = pyaudio.PyAudio()

    # Find VirtualMic output device index
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
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=CHUNK):
                    stream.write(chunk)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        is_speaking = False
