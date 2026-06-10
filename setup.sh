#!/bin/bash

# Start virtual display if not running
export DISPLAY=:99
if ! pgrep -x "Xvfb" > /dev/null
then
    Xvfb :99 -screen 0 1280x720x24 &
    sleep 1
fi

# Start PulseAudio
pulseaudio --start --log-target=syslog 2>/dev/null
sleep 1

# Clear existing virtual devices to prevent duplicates
pactl unload-module module-null-sink 2>/dev/null || true
pactl unload-module module-virtual-source 2>/dev/null || true

# Create virtual audio devices
pactl load-module module-null-sink \
    sink_name=VirtualSpeaker \
    sink_properties=device.description=VirtualSpeaker > /dev/null

pactl load-module module-virtual-source \
    source_name=VirtualMic \
    master=VirtualSpeaker.monitor \
    source_properties=device.description=VirtualMic > /dev/null

echo "Virtual display :99 and audio devices ready."
