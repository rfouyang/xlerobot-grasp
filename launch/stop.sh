#!/bin/bash
# Stops the pages (app/main.py). With --all: the arms and head back to rest, torque off, then the robot process and the
# ROS bringup too. Matches app/main.py by its program (python) and script and its working directory - never a shell
# whose command line only mentions it. It takes ~20 s to exit; one still there after 40 s is killed with its workers
# (2026-10-10: one hung on exit, the next start ran beside it and filled the memory).
cd "$(dirname "$0")/.." || exit 1
P=$(pwd -P)
ours() {
  ps -eo pid=,args= | awk '$2 ~ /python3?$/ && $3 ~ /(^|\/)app\/main\.py$/ {print $1}' | while read -r p; do
    [ "$(readlink /proc/$p/cwd)" = "$P" ] && echo "$p"
  done
}
for p in $(ours); do kill -INT "$p"; done
for i in $(seq 40); do [ -z "$(ours)" ] && break; sleep 1; done
# Its workers go with it, but never the robot process (its child too): an arm holding with torque would drop.
workers() { ps -o pid=,args= --ppid "$1" | awk '$0 !~ /app\/robot_main\.py/ {print $1}'; }
for p in $(ours); do
  echo "app/main.py ($p) did not exit after 40 s: killed (the robot process keeps running)"
  for w in $(workers "$p"); do pkill -9 -P "$w"; kill -9 "$w"; done
  kill -9 "$p"
done
[ "$1" = "--all" ] && .venv/bin/python app/main.py --stop-all
exit 0
