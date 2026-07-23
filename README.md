# GPX Telemetry Studio

Local web app that turns a **GPX ride + an SVG design template** into a
**transparent telemetry overlay video** (QuickTime Animation / qtrle `.mov`
@ 30 fps, 1080x1920 or 1920x1080 depending on the design) ready to composite in
**CapCut desktop** — or any NLE, for YouTube.

Web wrapper around the render engine from
[gpx-overlay](https://github.com/kuropenguin7000/gpx-overlay) — same channel
math, same SVG `{{ expr | fmt }}` template contract (see that repo's
`DESIGNS.md`), same qtrle output confirmed to import into CapCut with alpha.

## Run

```
run.bat
```

or

```
%LOCALAPPDATA%\Programs\Python\Python312\python.exe app.py
```

Serves http://127.0.0.1:8765 and opens the browser. Requires ffmpeg on PATH
(`winget install Gyan.FFmpeg`) or `FFMPEG_PATH`.

Dependencies (already present on this machine):
`pip install -r requirements.txt`

## Usage

1. **Ride** — drop a `.gpx` (Strava export). Timezone defaults to UTC+7 (WIB);
   the parsed ride card shows date, wall-clock range, duration, distance, HR.
2. **Design** — pick a bundled preset (`designs/*.svg`) or drop a custom SVG
   template. Templates are validated with sample telemetry on upload.
3. **Clip time** — start/end as wall-clock `HH:MM:SS` (matches the on-screen
   clock, same as the CLI). Quick buttons set end = start + 30 s / 1 min /
   3 min / 10 min / 1 h.
4. **Preview** — live PNG frame over a transparency checkerboard, scrubbable
   within the clip, with **safe-zone guides** (TikTok/Reels/Shorts crops for
   portrait designs, YouTube title-safe + player-controls strip for landscape).
   Click the frame (or ⤢) to enlarge it to the full window — the scrubber comes
   along, so you can step through the clip zoomed in. Esc closes it. Landscape
   designs also widen the preview column automatically.
5. **Render** — qtrle `.mov` (default, transparent in CapCut) or ProRes 4444
   (huge). Progress bar with fps/ETA, cancel button, download when done.
   Outputs are also written to `data/output\`.

## Aspect ratio

The output canvas comes from the design's own `<svg viewBox>`: its short side
is scaled to 1080, so a `405x720` template renders **1080x1920 (9:16)**, a
`720x405` one renders **1920x1080 (16:9)** and a square one 1080x1080 (long
side capped at 3840). The chip next to each design shows its ratio; the preview
box, the safe-zone guides and the column width all follow it.

Safe zones to design inside:

| | portrait (405x720 units) | landscape (720x405 units) |
|---|---|---|
| keep content in | x 16..345, y 56..585 | x 36..684, y 20..385 |
| why | TikTok/Reels/Shorts UI crops | YouTube title-safe (5%); also stay above y=356, where the player controls sit |

## Bundled designs

| design | ratio | notes |
|--------|-------|-------|
| `retro_analog` | 9:16 | dial gauges, copy of the gpx-overlay preset |
| `minimal` | 9:16 | plain stat stack, copy of the gpx-overlay preset |
| `elegant_line` | 9:16 | thin-line HUD |
| `map_track` | 9:16 | HUD + route map in a translucent card |
| `landscape_map` | 16:9 | long-form YouTube: fully transparent map tracker (no card — dark halo strokes keep the route legible), hero speed, DIST/ELEV/CLIMB/HR, clock + elapsed, route-% readout |

Long clips: on this machine `landscape_map` renders ~39 fps at 1920x1080 and
~37 KB/frame, so a **1-hour export takes ~46 min and lands around 4 GB**.
(`landscape_map` avoids a canvas-wide `feDropShadow`, which is what drops the
older designs to ~27-30 fps.)

## Map tracking channels (extension over gpx-overlay's DESIGNS.md)

This app's engine adds route-map channels on top of the original contract
(used by the `map_track` and `landscape_map` presets):

| name | meaning |
|------|---------|
| `track_points` | full-ride polyline `"x,y x,y …"` in a 0..100 box, north up (constant) |
| `track_len` | total arc length of that polyline (constant) |
| `track_dist` | arc length traveled so far (per frame) |
| `pos_x`, `pos_y` | current position in the same 0..100 box (per frame) |

Draw the traveled trail by dashing the full route:
`stroke-dasharray="{{ track_dist | .2f }} {{ track_len + 5 | .2f }}"`.
Note these channels don't exist in the gpx-overlay CLI renderer (yet), so
designs using them are web-app-only until ported.

## Notes

- qtrle `.mov` is the CapCut-compatible alpha format (VP9 webm alpha is
  ignored by CapCut; ProRes 4444 works but is ~far larger).
- Renders use all CPU cores (frames are independent) and run one job at a
  time in a queue. Frames are fed to the pool in batches (`engine.BATCH`) so an
  hour-long clip doesn't queue 100k+ frame contexts in memory at once.
- Uploaded rides/designs live in `data/` and the registry is in-memory:
  after restarting the server, re-upload (the page prompts naturally).
- To add a permanent preset, drop the SVG into `designs/` and restart.
