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
