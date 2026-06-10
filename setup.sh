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
for idx in $(pactl list short modules | grep -E "VirtualSpeaker|VirtualMic" | awk '{print $1}'); do
    pactl unload-module "$idx" 2>/dev/null || true
done

# Create virtual speaker (captures Meet audio output)
pactl load-module module-null-sink \
    sink_name=VirtualSpeaker \
    sink_properties=device.description=VirtualSpeaker > /dev/null

# Create virtual mic (injects agent voice into Meet)
pactl load-module module-virtual-source \
    source_name=VirtualMic \
    master=VirtualSpeaker.monitor \
    source_properties=device.description=VirtualMic > /dev/null

echo "Done. Virtual display :99 and audio devices ready."
