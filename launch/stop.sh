#!/bin/bash
# Stops the pages (app/main.py). With --all: the arms and head back to rest, torque off, then the robot process and the
# ROS bringup too. Matches app/main.py by its working directory, never this shell.
cd "$(dirname "$0")/.." || exit 1
P=$(pwd -P)
for p in $(pgrep -f 'python .*app/main\.py'); do [ "$(readlink /proc/$p/cwd)" = "$P" ] && kill -INT $p; done
[ "$1" = "--all" ] && .venv/bin/python app/main.py --stop-all
exit 0
