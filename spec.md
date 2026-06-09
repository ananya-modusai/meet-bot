# Google Meet AI Document Agent — Full Technical Specification

## Project Overview

Build an AI agent that joins a Google Meet, listens to participants via voice, reads chat messages, answers questions about a provided PDF document via voice, and navigates the PDF to the relevant page on screen share — all running on an AWS EC2 Ubuntu instance.

---

## MVP Scope

### What the Agent Does
1. Receives a Google Meet URL and a PDF document path as CLI arguments
2. Extracts all text from the PDF, page by page, with page markers
3. Opens Chromium browser, joins the Google Meet as a normal participant
4. Sits idle — listening, no screen share yet
5. Listens to participant voice via virtual audio capture → Deepgram STT
6. Also polls Google Meet chat box every 2 seconds for text questions
7. Sends questions to Claude Sonnet 4.6 API (full document in system prompt, prompt caching enabled)
8. Claude returns structured JSON: `{"answer": "...", "page": N}`
9. Deepgram Aura-2 TTS converts the answer to audio → plays into virtual mic → participants hear the agent speak
10. If a page number is returned, Playwright navigates PDF.js viewer to that page
11. Screen share is triggered ON DEMAND only — when someone says "share", "show", "display", "present", "pull up", "open the doc"
12. FFmpeg records the full session (virtual display + audio) in background
13. An `is_speaking` flag prevents echo — agent ignores STT input while TTS is playing

### What the Agent Does NOT Do (MVP)
- No video feed / webcam
- No SAP GUI integration (future phase)
- No RAG / vector DB — full document fits in Claude's context window
- No multi-platform support — Google Meet only
- No VPS spin-up automation — manual start for MVP

---

## Target Platform

- **OS:** Ubuntu 22.04 LTS on AWS EC2
- **Instance:** t3.large (2 vCPU, 8GB RAM, 30GB storage)
- **Python:** 3.10+
- **Async throughout** — Python asyncio

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│               AWS EC2 (Ubuntu 22.04)                │
│                                                     │
│  PulseAudio (built-in):                             │
│  ├── VirtualSpeaker ← Chrome outputs meet audio     │
│  └── VirtualMic     → Chrome inputs agent voice     │
│                                                     │
│  Xvfb — Virtual Display :99 (1280x720)              │
│                                                     │
│  Chromium (headless=False, on display :99):          │
│  ├── Tab 1: PDF.js viewer (screen shared on demand) │
│  └── Tab 2: Google Meet (audio routed via PA)       │
│                                                     │
│  Agent Core (Python async):                         │
│  ├── Voice Loop: PyAudio → Deepgram STT (Nova-3)    │
│  ├── Chat Loop: Playwright polls chat every 2s      │
│  ├── Brain: Claude Sonnet 4.6 API + prompt caching  │
│  ├── Voice Out: Deepgram TTS (Aura-2) → VirtualMic  │
│  ├── PDF Nav: Playwright → PDF.js page scroll       │
│  └── Echo Prevention: is_speaking flag               │
│                                                     │
│  FFmpeg (background):                               │
│  └── Captures Xvfb :99 + VirtualSpeaker → .mp4     │
└─────────────────────────────────────────────────────┘
```

---

## Audio Pipeline — Detailed

### Virtual Audio Setup (PulseAudio)
```bash
# Create virtual speaker — captures what meeting outputs
pactl load-module module-null-sink \
    sink_name=VirtualSpeaker \
    sink_properties=device.description=VirtualSpeaker

# Create virtual mic — injects agent voice into meeting
pactl load-module module-virtual-source \
    source_name=VirtualMic \
    master=VirtualSpeaker.monitor \
    source_properties=device.description=VirtualMic
```

### Audio Flow
```
Participants speak
    ↓
Google Meet audio output
    ↓
VirtualSpeaker (PulseAudio null sink)
    ↓
PyAudio captures from VirtualSpeaker.monitor
    ↓
Deepgram STT (Nova-3, streaming WebSocket)
    ↓
Text transcript (fires on utterance end via VAD)
    ↓
Claude Sonnet 4.6 API
    ↓
JSON response: {answer, page}
    ↓
Deepgram TTS (Aura-2, streaming WebSocket)
    ↓
PyAudio writes to VirtualMic
    ↓
Google Meet mic input
    ↓
Participants hear agent voice
```

### Echo Prevention
- `is_speaking` flag = True while TTS is playing
- STT handler ignores all input while `is_speaking` is True
- Set back to False after TTS finishes

### Voice Activity Detection (VAD)
- Use Deepgram's built-in VAD
- `vad_events=True` in Deepgram LiveOptions
- `utterance_end_ms=1000` — wait 1 second of silence before firing
- Only process `speech_final=True` transcripts

---

## Document Pipeline — Detailed

### PDF Extraction (PyMuPDF)
```python
import fitz

def prepare_document(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = ""
    for i, page in enumerate(doc):
        full_text += f"\n\n--- PAGE {i+1} ---\n{page.get_text()}"
    return full_text
```

### Claude System Prompt
```
You are a voice assistant in a Google Meet.
You have full knowledge of this document:

{full_document_text}

Rules:
- Keep answers concise — this is spoken audio, not text
- No bullet points, no markdown — plain conversational speech
- Always identify which page the answer is from
- If asked to share/show/display the document, respond with page 1

Respond ONLY in this JSON format:
{"answer": "conversational spoken response here", "page": 50}
```

### Prompt Caching
- The document text is static across all calls within a meeting
- Use Anthropic prompt caching to avoid re-processing the document every call
- First call: full price (~$0.127)
- Subsequent calls: ~$0.019 (90% cheaper on cached input)

---

## Google Meet Integration — Detailed

### Browser Launch Flags (Critical for EC2)
```python
browser = await p.chromium.launch(
    headless=False,
    args=[
        "--display=:99",
        "--no-sandbox",
        "--disable-dev-shm-usage",      # critical on EC2
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
        "--auto-accept-camera-and-microphone-capture",
    ]
)
```

### Join Flow
1. Navigate to meet URL
2. Handle name entry if prompted
3. Turn off camera (no video feed)
4. Ensure mic is using VirtualMic
5. Click "Join now"
6. Wait for join confirmation

### Chat Polling
- Poll chat box every 2 seconds via Playwright
- Track `seen_messages` set to avoid duplicate processing
- Extract message text from chat DOM elements
- Feed new messages to the same handler as STT transcripts

### Screen Share (On Demand)
- NOT started on join — agent sits idle
- Triggered when voice/chat input matches share intent keywords:
  - "share", "show", "display", "present", "pull up", "open the doc"
- When triggered:
  - Playwright clicks "Present now" button
  - Selects the PDF.js tab/window for sharing
  - Agent confirms via voice: "Sure, sharing the document now."

---

## PDF Display — PDF.js

### viewer.html
- Simple HTML page embedding Mozilla's PDF.js viewer
- PDF loaded on startup from local file path
- Controllable via JavaScript: `PDFViewerApplication.page = N`

### Page Navigation
```python
async def go_to_page(pdf_tab, page_num):
    await pdf_tab.evaluate(f"PDFViewerApplication.page = {page_num}")
```

---

## Recording — FFmpeg

### Background Recording (starts with agent)
```bash
ffmpeg \
  -f x11grab -r 30 -s 1280x720 -i :99 \
  -f pulse -i VirtualSpeaker.monitor \
  -c:v libx264 -c:a aac \
  /recordings/$(date +%Y%m%d_%H%M%S).mp4 &
```

---

## File Structure

```
sap-agent-mvp/
├── agent.py           ← main entry point, CLI args, event loop
├── audio.py           ← PulseAudio setup, Deepgram STT/TTS, PyAudio
├── document.py        ← PyMuPDF PDF extraction
├── meeting.py         ← Playwright Google Meet control (join, chat, share)
├── viewer.html        ← PDF.js viewer page
├── setup.sh           ← Xvfb + PulseAudio startup script
├── requirements.txt   ← all pip dependencies
├── .env.example       ← API key template
└── .gitignore         ← ignore .env, *.pdf, __pycache__, venv/
```

---

## Dependencies (requirements.txt)

```
playwright
anthropic
pymupdf
deepgram-sdk
pyaudio
python-dotenv
```

---

## Environment Variables (.env.example)

```
DEEPGRAM_API_KEY=your_deepgram_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

---

## CLI Usage

```bash
# Start infrastructure
bash setup.sh

# Run agent
python agent.py \
  --meet "https://meet.google.com/xxx-yyy-zzz" \
  --doc "document.pdf"
```

---

## Setup Script (setup.sh)

```bash
#!/bin/bash

# Start virtual display
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 &
sleep 1

# Start PulseAudio
pulseaudio --start --log-target=syslog 2>/dev/null
sleep 1

# Create virtual audio devices
pactl load-module module-null-sink \
    sink_name=VirtualSpeaker \
    sink_properties=device.description=VirtualSpeaker

pactl load-module module-virtual-source \
    source_name=VirtualMic \
    master=VirtualSpeaker.monitor \
    source_properties=device.description=VirtualMic

echo "Virtual display :99 and audio devices ready."
```

---

## Cost Estimation (Claude Sonnet 4.6 API Only)

| Metric | Value |
|--------|-------|
| Input price | $3.00 / million tokens |
| Output price | $15.00 / million tokens |
| Cached input price | $0.30 / million tokens (90% off) |
| Doc size (100 pages) | ~40,000 tokens |
| Cost per question (cached) | ~$0.019 |
| Cost per meeting (30 min, 20 questions) | ~$0.49 |
| Monthly cost (1 meeting/day) | ~$14.70 |

---

## Key Technical Decisions

1. **No RAG** — 100-page doc (~40K tokens) fits in Claude's 200K context window. Simpler, more accurate.
2. **Single vendor for voice** — Deepgram handles both STT (Nova-3) and TTS (Aura-2). One API key, one SDK, lower latency.
3. **Prompt caching** — Document is identical every call. 90% cheaper after first call.
4. **No VPS spin-up** — For MVP, manually start the agent. Automation is future phase.
5. **Google Meet only** — No Zoom/Teams for MVP. Reduces complexity.
6. **Screen share on demand** — Agent joins without sharing. Shares only when asked.
7. **Chat + Voice input** — Both paths feed into the same handler. Participants can speak or type.
8. **Voice output only** — Agent responds via voice, not chat text (unless specifically asked).
9. **Echo prevention** — Simple `is_speaking` flag. No complex echo cancellation needed.

---

## Future Phases (Not MVP)

- Phase 2: SAP GUI integration (win32com scripting on Windows)
- Phase 3: Zoom and Teams support
- Phase 4: Auto VPS spin-up via webhook + cron
- Phase 5: Multi-document support / RAG for large corpora
- Phase 6: OpenClaw/Hermes agent framework integration