#!/usr/bin/env bash
# Final takes on the build with the short finish toast (the worker runs one investigation at a time).
cd "$(dirname "$0")"
run() { echo "== $(date -u +%H:%M:%S) start $*"; node demo-record.mjs "$@" 2>&1 | grep -E "submitted|started|paused|terminal|done after|video|HTTP [45]|all stages" | cut -c1-200; }
run surprise 12
run frontier 20 "How could we build a space elevator with materials we can make today?"
run studio 40
run studio 40 "A Dyson swarm"
echo "== $(date -u +%H:%M:%S) queue done"
