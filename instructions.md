# Meet Agent — Run Instructions

## Setup (one-time, on EC2)

```bash
source venv/bin/activate
bash setup.sh
```

---

## Google Meet (root agent)

Joins as a guest. The host must click **Admit** to let the agent in.
Google Meet on personal accounts may block guest joins — use a workspace account or Zoom instead.

```bash
cd /home/ubuntu/meet-agent
source venv/bin/activate
python agent.py --meet "https://meet.google.com/XXX-XXX-XXX" --doc "/path/to/document.pdf"
```

---

## Zoom (zoom/ directory)

Joins via the Zoom web client (no app install needed, no account needed).

```bash
cd /home/ubuntu/meet-agent/zoom
source ../venv/bin/activate
python agent.py --meeting "https://zoom.us/j/XXXXXXXXXX?pwd=XXXXXX"
```

Optional flags:
- `--name "My Bot"` — display name (default: Doc Agent)
- `--password "xxxx"` — meeting password if not in URL

---

## Environment variables (.env in project root)

```
ANTHROPIC_API_KEY=...
DEEPGRAM_API_KEY=...
```

---

## Infrastructure

- EC2: Ubuntu 22.04 LTS, t3.large, IP: 15.207.113.73
- SSH: `ssh -i signoz-log.pem ubuntu@15.207.113.73` (PEM is in the project folder)
- Virtual display: Xvfb on :99
- Virtual audio: PulseAudio with VirtualSpeaker (STT source) and VirtualMic (TTS output)
- Chrome launched manually via subprocess, Playwright connects over CDP on port 9222

---

## Architecture per platform

Each platform is fully isolated — no shared code, even if it means duplication.

| Directory      | Platform     | Status  |
|----------------|--------------|---------|
| `./`           | Google Meet  | Working (needs host admit; personal accounts may block) |
| `zoom/`        | Zoom         | Working |
| `google_meet/` | Google Meet  | Mirror of root (legacy) |

---

## Known issues / notes

- `--use-fake-device-for-media-stream` causes beeping + green circle video in Zoom — removed from zoom/agent.py
- Google Meet on personal Gmail blocks unsigned-in guest joins — use Zoom or a workspace Meet
- Deepgram API key must be read at call-time (not import-time) — handled via `_get_key()` in audio.py
- If Chrome crashes on EC2, check `logs/chrome.log`; instance may OOM on t3.large with multiple Chrome instances
