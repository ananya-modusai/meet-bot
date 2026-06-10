To connect the agent to a Google Meet, first activate the virtual environment (if not already active) and ensure the setup script has been run:

```bash
source venv/bin/activate
bash setup.sh
```

Then run the agent with your Meet link and the path to your PDF document:

```bash
python agent.py --meet "https://meet.google.com/YOUR_MEETING_LINK" --doc "YOUR_DOCUMENT.pdf"
```
https://meet.google.com/cpr-sfrz-zoy?authuser=0

Note: Because the agent joins as a guest (without a Google account), the meeting host will need to click "Admit" to let it in.
