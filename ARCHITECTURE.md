# Meeting AI Agent — Architecture

Paste each block directly into [mermaid.live](https://mermaid.live) — no wrapper needed.

---

## 1. Full System Flow

```mermaid
flowchart TD
    CLI["run.py\n--platform recall\n--meeting URL\n--doc file.pdf"]

    CLI --> DOC["document.py\nExtract PDF text\nwith page markers"]
    DOC --> PROMPT["Build Claude System Prompt\nFull PDF text injected"]
    PROMPT --> CREATE["recall/meeting.py\nPOST Recall.ai API\nCreate bot"]
    CREATE --> JOIN["Recall.ai joins meeting\nZoom or Meet or Teams\nHandles locked rooms"]
    JOIN --> WAIT["Wait for bot status\nin_call_recording"]

    WAIT --> POLL["Poll /transcript/ every 1s"]
    POLL --> DELTA["Delta tracker\nOnly new words since last poll"]
    DELTA --> SILENCE{"Silence more than 1.5s?"}
    SILENCE -->|No| POLL
    SILENCE -->|Yes| UTT["Utterance complete\nspeaker + full text"]

    UTT --> CLAUDE["claude-sonnet-4-6\nTools: navigate_to_page, open_document\nHistory: full conversation\nContext: entire PDF"]

    CLAUDE --> TEXTBLOCK["text block\nSpoken reply"]
    CLAUDE --> TOOLBLOCK["tool_use block\nAction to perform"]

    TEXTBLOCK --> TTS["Deepgram Aura TTS\naura-odysseus-en\nReturns raw PCM bytes"]
    TOOLBLOCK --> TOOLEXEC["Execute tool\nnavigate_to_page N\nor open_document"]

    TTS --> WAVWRAP["PCM wrapped to WAV\nPOST /output_audio/"]
    TOOLEXEC --> PDFNAV["PDF viewer scrolls\nto page N"]

    WAVWRAP --> GATHER["asyncio.gather\nboth fire simultaneously"]
    PDFNAV --> GATHER

    GATHER --> DONE["Bot speaks\nDocument scrolls\nat the same time"]
    DONE --> POLL
```

---

## 2. One Conversation Turn

```mermaid
sequenceDiagram
    participant P as Participant
    participant R as Recall.ai Bot
    participant A as agent.py
    participant C as Claude API
    participant D as Deepgram TTS

    P->>R: speaks in meeting
    R->>R: STT transcription via Deepgram
    A->>R: poll /transcript/ every 1s
    R-->>A: new words returned
    A->>A: silence detected, utterance complete
    A->>C: history + tools + full PDF context
    C-->>A: text block + tool_use block
    par TTS and tool run together
        A->>D: text to speech
        D-->>A: PCM audio bytes
        A->>R: POST /output_audio/ WAV
        R->>P: bot voice played in meeting
    and
        A->>A: navigate_to_page N
        Note over A: PDF viewer scrolls to page N
    end
```

---

## 3. Platform Isolation

```mermaid
flowchart LR
    RUN["run.py\n--platform X"]

    RUN -->|platform recall| RC["recall/\nagent.py\nmeeting.py\naudio.py\ndocument.py"]
    RUN -->|platform zoom| ZM["zoom/\nagent.py\naudio.py\nmeeting.py"]
    RUN -->|platform meet| GM["root/\nagent.py\naudio.py\nmeeting.py\ndocument.py"]

    RC --> ANY["Any platform\nZoom, Meet, Teams\nLocked meetings supported\nRecall.ai API"]
    ZM --> ZONLY["Zoom only\nPlaywright and Chrome\nUnlocked meetings"]
    GM --> MONLY["Google Meet\nPlaywright and Chrome\nGuest join only"]
```
