# Meeting AI Agent — Architecture

## System Overview

```mermaid
flowchart TD
    CLI["run.py\n--platform recall\n--meeting URL\n--doc file.pdf"]

    subgraph STARTUP["Startup"]
        DOC["document.py\nExtract PDF text\npage by page"]
        PROMPT["Build Claude\nSystem Prompt\n(PDF text injected)"]
        BOT_CREATE["recall/meeting.py\nPOST /api/v1/bot/\nCreate Recall.ai bot"]
        BOT_JOIN["Recall.ai\nHandles join\n(Zoom / Meet / Teams)"]
        WAIT["Wait for bot status\n→ in_call_recording"]
    end

    subgraph LISTEN["Real-time Listen Loop (poll every 1s)"]
        POLL["GET /bot/{id}/transcript/\nFull transcript so far"]
        DELTA["Delta tracker\nOnly new words since last poll"]
        SILENCE["Silence detector\nNo new words for 1.5s\n→ utterance complete"]
        UTTERANCE["on_utterance(speaker, text)"]
    end

    subgraph BRAIN["Claude Processing"]
        CLAUDE["claude-sonnet-4-6\nSystem: PDF context + rules\nTools: navigate_to_page, open_document\nHistory: full conversation"]
        TEXT_OUT["text block\nSpoken preamble"]
        TOOL_OUT["tool_use block\nAction to perform"]
    end

    subgraph RESPOND["Parallel Response (asyncio.gather)"]
        TTS["recall/audio.py\nDeepgram Aura TTS\naura-odysseus-en\n→ raw PCM bytes"]
        TOOL_EXEC["Tool execution\nnavigate_to_page(N)\nopen_document()"]
        AUDIO_OUT["recall/meeting.py\nPOST /bot/{id}/output_audio/\nWAV → injected into meeting"]
        PDF_NAV["PDF viewer\nScrolls to page N\n(future: screenshare)"]
    end

    subgraph MEETING["Live Meeting (any platform)"]
        PARTICIPANTS["Meeting participants\nhear bot voice"]
        RECALL_BOT["Recall.ai Bot\nin the call"]
    end

    CLI --> DOC
    DOC --> PROMPT
    PROMPT --> BOT_CREATE
    BOT_CREATE --> BOT_JOIN
    BOT_JOIN --> WAIT
    WAIT --> POLL

    POLL --> DELTA
    DELTA --> SILENCE
    SILENCE --> UTTERANCE
    UTTERANCE --> CLAUDE

    CLAUDE --> TEXT_OUT
    CLAUDE --> TOOL_OUT

    TEXT_OUT --> TTS
    TOOL_OUT --> TOOL_EXEC

    TTS --> AUDIO_OUT
    TOOL_EXEC --> PDF_NAV

    AUDIO_OUT --> RECALL_BOT
    RECALL_BOT --> PARTICIPANTS

    PARTICIPANTS -->|speak| RECALL_BOT
    RECALL_BOT -->|transcribe| POLL
```

---

## Conversation Turn — Detailed Sequence

```mermaid
sequenceDiagram
    participant P as Participant
    participant R as Recall.ai Bot
    participant A as agent.py
    participant C as Claude API
    participant D as Deepgram TTS
    participant M as meeting.py

    P->>R: speaks (audio)
    R->>R: transcribes (Deepgram STT via Recall)
    A->>M: poll /transcript/ every 1s
    M-->>A: new words detected
    A->>A: silence detected → utterance complete
    A->>C: messages + tools + PDF context
    C-->>A: text block + tool_use block
    
    par Parallel execution
        A->>D: POST /speak (text → PCM)
        D-->>A: raw audio bytes
        A->>M: POST /output_audio/ (WAV)
        M->>R: inject audio into meeting
        R->>P: bot speaks
    and
        A->>A: execute tool (navigate_to_page N)
        Note over A: PDF viewer scrolls to page N
    end
```

---

## Platform Isolation

```mermaid
flowchart LR
    RUN["run.py\n--platform X"]

    RUN -->|recall| RC["recall/\nagent.py\nmeeting.py\naudio.py\ndocument.py"]
    RUN -->|zoom| ZM["zoom/\nagent.py\naudio.py\nmeeting.py"]
    RUN -->|meet| GM["agent.py\naudio.py\nmeeting.py\ndocument.py"]

    RC -->|"Recall.ai API\n(any platform)"| ANY["Zoom / Meet / Teams\nlocked meetings OK"]
    ZM -->|"Playwright + Chrome CDP\n(Zoom web client)"| ZOOM["Zoom\nopen meetings only"]
    GM -->|"Playwright + Chrome CDP\n(Meet web client)"| MEET["Google Meet\nguest join only"]
```

---

## External Services

| Service | Purpose | Used in |
|---|---|---|
| **Recall.ai** | Bot joins meeting, STT transcription, audio injection | `recall/` |
| **Anthropic Claude** | Understands question, generates spoken reply + tool calls | all platforms |
| **Deepgram Aura** (TTS) | Converts Claude's text reply to audio (Odysseus voice) | all platforms |
| **Deepgram Nova** (STT) | Transcribes meeting audio | `zoom/`, `meet/` (via PulseAudio) |

---

## Tool Use — How Actions and Speech Sync

```mermaid
flowchart TD
    Q["User: What does the contract say\nabout termination on page 12?"]
    CLAUDE["Claude processes with full PDF context"]
    
    CLAUDE --> T["text block\n'The termination clause on page 12 states\nthat either party may exit with 30 days notice...'"]
    CLAUDE --> TC["tool_use\nnavigate_to_page(12)"]

    T --> TTS["Deepgram TTS → audio"]
    TC --> NAV["PDF viewer jumps to page 12"]

    TTS & NAV -->|asyncio.gather — simultaneous| DONE["Bot speaks while document scrolls"]
```
