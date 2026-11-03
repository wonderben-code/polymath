# Demo recording and film tools

Used on 2 and 3 November 2026 to record the live site as a fresh visitor and to cut the demo film. They need Node with Playwright (Chromium) and ffmpeg; none of them is part of the product.

- `demo-record.mjs <converge|solve|frontier|studio|ask|surprise> [minutes] [custom text]`: opens askpolymath.com in a fresh browser context with the video recorder on, drives one feature as a visitor would, keeps the camera on the result while it works, and writes `demo/<feature>/<feature>.webm` plus a journey log.
- `demo-queue.sh`: records several features one after another (the research worker runs one investigation at a time per account).
- `highlight.sh <in.webm> <out.mp4> [head] [tail] [speed]`: a 2–3 minute highlight of one recording (start and end at real speed, the middle as a time-lapse).
- `transcode.sh`: seekable MP4 copies of the raw recordings (Playwright's WebM has no index).
- `build.py [A|B|both]`: the three-minute film from the shot list in `docs/demo/VIDEO_SCRIPT.md`, as cut A (best bits) and cut B (unfold).
