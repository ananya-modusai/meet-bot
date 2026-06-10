#!/bin/bash
set -e

# Virtual display
if ! pgrep -x "Xvfb" > /dev/null; then
    Xvfb :99 -screen 0 1280x720x24 &
    sleep 1
    echo "Xvfb started on :99"
else
    echo "Xvfb already running."
fi
export DISPLAY=:99

# PulseAudio
pulseaudio --start --log-target=syslog 2>/dev/null
sleep 1

# Remove any existing virtual devices to avoid duplicates
for idx in $(pactl list short modules | grep -E "VirtualSpeaker|VirtualMic|TTSSink" | awk '{print $1}'); do
    pactl unload-module "$idx" 2>/dev/null || true
done

# VirtualSpeaker — null sink where Chrome/Zoom routes meeting audio output
# Our STT captures from VirtualSpeaker.monitor (what participants say)
pactl load-module module-null-sink \
    sink_name=VirtualSpeaker \
    sink_properties=device.description=VirtualSpeaker > /dev/null

# TTSSink — separate null sink for our TTS audio output
# Chrome uses TTSSink.monitor as Zoom's microphone input (what participants hear)
pactl load-module module-null-sink \
    sink_name=TTSSink \
    sink_properties=device.description=TTSSink > /dev/null

# VirtualMic — virtual source monitoring TTSSink.monitor
# Chrome picks this up as Zoom's microphone device
pactl load-module module-virtual-source \
    source_name=VirtualMic \
    master=TTSSink.monitor \
    source_properties=device.description=VirtualMic > /dev/null

# Route Zoom's audio output to VirtualSpeaker, bot's voice from VirtualMic
pactl set-default-sink VirtualSpeaker
pactl set-default-source VirtualMic

echo "Done. Virtual display :99 and audio devices ready."
echo "  STT reads from : VirtualSpeaker.monitor  (meeting audio)"
echo "  TTS writes to  : TTSSink                 (bot voice -> Zoom mic)"
