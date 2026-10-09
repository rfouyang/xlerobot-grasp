#!/bin/bash
# The only start: app/main.py brings up the robot process and the ROS bringup itself (reusing them when running).
# Windows first: launch/attach_usb.ps1 (the USB devices into WSL).
cd "$(dirname "$0")/.." || exit 1
mkdir -p log
setsid nohup .venv/bin/python app/main.py > log/main_stdout.log 2>&1 < /dev/null & disown
for i in $(seq 120); do sleep 2; curl -s -m 2 -o /dev/null localhost:8010/ && { echo "XLeRobot up after $((i*2))s: http://localhost:8010"; exit 0; }; done
echo "not up after 240 s: see log/main_stdout.log"; exit 1
