#!/usr/bin/env bash
# highlight.sh <in.webm> <out.mp4> [head_s=45] [tail_s=50] [speed=30]
# Keeps the start and the end at real speed and compresses the middle into a time-lapse.
# Three separate passes keep memory low (a single filter graph buffers the whole file).
set -euo pipefail
in=$1; out=$2; head=${3:-45}; tail=${4:-50}; speed=${5:-30}
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in" | cut -d. -f1)
if ! [ "$dur" -ge 0 ] 2>/dev/null; then
  dur=$(ffmpeg -v info -i "$in" -f null - 2>&1 | grep -oE "time=[0-9:.]+" | tail -1 | sed 's/time=//' | awk -F: '{print int($1*3600+$2*60+$3)}')
fi
tmp=$(mktemp -d); enc=(-an -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p -r 30)
if [ "$dur" -le $((head + tail + 10)) ]; then
  ffmpeg -y -v error -i "$in" -vf "scale=1280:-2" "${enc[@]}" -movflags +faststart "$out"
else
  mid=$((dur - head - tail))
  ffmpeg -y -v error -i "$in" -t "$head" -vf "scale=1280:-2" "${enc[@]}" "$tmp/a.mp4"
  ffmpeg -y -v error -ss "$head" -i "$in" -t "$mid" -vf "setpts=PTS/${speed},scale=1280:-2" "${enc[@]}" "$tmp/b.mp4"
  ffmpeg -y -v error -ss "$((dur - tail))" -i "$in" -vf "scale=1280:-2" "${enc[@]}" "$tmp/c.mp4"
  printf "file '%s'\nfile '%s'\nfile '%s'\n" "$tmp/a.mp4" "$tmp/b.mp4" "$tmp/c.mp4" > "$tmp/list.txt"
  ffmpeg -y -v error -f concat -safe 0 -i "$tmp/list.txt" -c copy -movflags +faststart "$out"
fi
rm -rf "$tmp"
ffprobe -v error -show_entries format=duration:format=size -of csv=p=0 "$out"
