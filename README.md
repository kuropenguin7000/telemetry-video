# GPX Telemetry Studio

Local web app that turns a **GPX ride + an SVG design template** into a
**transparent telemetry overlay video** (QuickTime Animation / qtrle `.mov`,
1080x1920 @ 30 fps) ready to composite in **CapCut desktop**.

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
   clock, same as the CLI). Quick buttons set end = start + 30 s / 1 min / 3 min.
4. **Preview** — live PNG frame over a transparency checkerboard, scrubbable
   within the clip, with TikTok/Reels/Shorts **safe-zone guides** overlay.
5. **Render** — qtrle `.mov` (default, transparent in CapCut) or ProRes 4444
   (huge). Progress bar with fps/ETA, cancel button, download when done.
   Outputs are also written to `data/output\`.

## Map tracking channels (extension over gpx-overlay's DESIGNS.md)

This app's engine adds route-map channels on top of the original contract
(used by the `map_track` preset):

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
  time in a queue.
- Uploaded rides/designs live in `data/` and the registry is in-memory:
  after restarting the server, re-upload (the page prompts naturally).
- To add a permanent preset, drop the SVG into `designs/` and restart.
