# Meet-Bot Development Log

## What This Project Is
A Python-based AI agent that joins a Google Meet call as a guest, listens to the conversation via speech-to-text, answers questions about a loaded PDF document using Claude, and speaks answers back into the call using text-to-speech. It can also screen-share the PDF on demand.

**Spec file:** `spec.md` — read this first for the full product requirements.

---

## Architecture

```
agent.py        — entry point. Orchestrates everything.
audio.py        — Deepgram STT (live mic stream) + TTS (REST speak endpoint). Uses PyAudio to write to VirtualMic.
meeting.py      — Playwright: joins Meet as guest, polls chat, triggers screen share.
document.py     — PyMuPDF: extracts full text from PDF with page markers.
viewer.html     — PDF.js viewer loaded in a hidden browser tab for screen sharing.
setup.sh        — Starts Xvfb (virtual screen :99) and PulseAudio virtual devices.
```

**Flow:**
1. `setup.sh` starts Xvfb + PulseAudio virtual mic/speaker on the EC2 instance.
2. `agent.py` extracts PDF text → builds Claude system prompt with full document in context.
3. Playwright launches Chromium (headless=False on DISPLAY=:99), opens PDF viewer tab, opens Meet tab.
4. Agent joins as guest ("Doc Agent"), waits for host to admit.
5. `asyncio.gather()` runs two concurrent loops: Deepgram STT stream + Meet chat polling.
6. Any spoken/typed input hits `handle_input()` → checks for share intent → else asks Claude → speaks answer back.

---

## Infrastructure (EC2)

- **Instance:** Ubuntu 26.04 on AWS (ip: 43.204.140.197)
- **Python:** 3.14 (system). Uses a `venv/` at `~/meet-bot/venv`.
- **Repo:** cloned to `~/meet-bot/`
- **Key packages:** playwright, anthropic, pymupdf, deepgram-sdk, pyaudio, python-dotenv

**Required system packages (already installed):**
```
python3.14-venv, python3-pip, portaudio19-dev, xvfb, pulseaudio, pulseaudio-utils
```

**Env vars** (in `.env`, not committed):
```
DEEPGRAM_API_KEY=...
ANTHROPIC_API_KEY=...
```

---

## Decision Log

| Decision | Reason |
|---|---|
| Claude Haiku 4.5 | Low latency for voice responses. Sonnet is overkill for doc QA. |
| Prompt caching (ephemeral) | Document can be 40K+ tokens. Caching avoids re-sending it on every turn. |
| Deepgram for STT + TTS | Real-time streaming STT. REST TTS for simplicity (no WS needed for MVP). |
| PyAudio → VirtualMic | Feeds TTS audio into PulseAudio virtual device so Meet hears the bot speak. |
| Playwright not Selenium | Better async support, built-in CDP, easier media permission grants. |
| Guest join (no Google account) | Simpler. Host must click Admit. Acceptable for MVP. |
| File logging (`logs/`) | Each run creates `logs/agent_YYYYMMDD_HHMMSS.log` for remote debugging. |

---

## Fixes & Issues Log

### 2026-06-09
- **PyAudio build fail** — needed `portaudio19-dev` system lib. Fixed with `sudo apt install -y portaudio19-dev`.
- **pip blocked on Ubuntu 26** — PEP 668 externally-managed-environment. Fixed with venv (`python3.14-venv` package required separately).
- **setup.sh permission denied** — script lacked execute bit. Fixed by running `bash setup.sh` instead of `./setup.sh`.
- **Xvfb/pactl not found** — `xvfb` and `pulseaudio` not installed initially. Fixed with `sudo apt install -y xvfb pulseaudio pulseaudio-utils`.

### 2026-06-10
- **Duplicate PulseAudio modules** — Running `setup.sh` twice stacked extra VirtualMic/VirtualSpeaker modules. First attempted `pactl unload-module <name>` which broke things. Fixed by unloading by index: `pactl list short modules | grep -E "VirtualSpeaker|VirtualMic" | awk '{print $1}'` then unloading each index.
- **Guest join flow** — Added handling for: (1) `--use-fake-ui-for-media-stream` + `context.grant_permissions()` to skip camera/mic browser popup on every fresh Chromium launch. (2) Name entry (`input[placeholder='Your name']`) with `fill("")` + `type("Doc Agent")`. (3) Both `"Join now"` and `"Ask to join"` button variants.
- **Indentation bug** — `log.info` in `handle_input()` was double-indented after a find-replace. Fixed manually.

---

## Current State (as of 2026-06-10)

- All code written and pushed to GitHub.
- EC2 instance set up with all dependencies.
- `setup.sh` idempotent — safe to run multiple times.
- Logging to `logs/` on every run.
- **NOT YET TESTED end-to-end** — no real Meet call has been run yet.

## What Needs Testing Next

1. Run `python agent.py --meet <url> --doc <file>` with a real Meet link.
2. Verify bot appears as "Doc Agent" in the waiting room.
3. Host admits → verify bot joins silently (camera off).
4. Speak a question → verify Deepgram captures it → Claude answers → TTS plays back into Meet.
5. Type "share the document" in chat → verify screen share starts and PDF appears.
6. Check `logs/` after the run for any errors.

---

## How to Run

```bash
# On EC2
cd ~/meet-bot
source venv/bin/activate
bash setup.sh
python agent.py --meet "https://meet.google.com/xxx-yyy-zzz" --doc "document.pdf"
```

Use `tmux` to keep the agent running if your SSH connection drops:
```bash
tmux            # start session
# run the agent inside tmux
# if SSH drops: reconnect then run:
tmux attach     # resume
```
