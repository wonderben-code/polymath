#!/usr/bin/env bash
# Seekable, compact copies of the raw recordings (the webm files carry no index, so every cut would decode from the start).
set -e
cd "$(dirname "$0")/.."
t() { [ -f "film/src/$1.mp4" ] || nice ffmpeg -v error -y -i "$2" -an -vf "scale=1280:-2" -r 30 -c:v libx264 -preset veryfast -crf 24 -g 60 -pix_fmt yuv420p -movflags +faststart "film/src/$1.mp4"; echo "$1 done $(date -u +%H:%M:%S)"; }
t ask ask-why-does-time-only-run-forward/ask-why-does-time-only-run-forward.webm &
t solve solve-how-could-a-spacecraft-shield-its-crew-f/solve-how-could-a-spacecraft-shield-its-crew-f.webm &
wait
t converge converge/converge.webm &
t frontier frontier-how-could-we-build-a-space-elevator-with/frontier-how-could-we-build-a-space-elevator-with.webm &
wait
t whale studio/studio.webm &
t dyson studio-dyson-take1/studio-a-dyson-swarm.webm &
wait
ls -la film/src
