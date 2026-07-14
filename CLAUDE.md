# GPX Telemetry Studio (telemetry-video)

Local web app that turns a **GPX ride + an SVG design template** into a
**transparent telemetry overlay video** (qtrle `.mov`, 1080x1920 @ 30 fps) for
compositing in CapCut desktop. Web wrapper around the render engine of the
user's CLI project at `C:\Users\rrahman.c\Videos\gpx-overlay`
(github.com/kuropenguin7000/gpx-overlay).

## Run

`run.bat`, or `%LOCALAPPDATA%\Programs\Python\Python312\python.exe app.py` —
serves http://127.0.0.1:8765 and opens the browser. `.claude\launch.json` has
the uvicorn config for the Claude browser pane. ffmpeg is auto-detected
(FFMPEG_PATH / PATH / winget Gyan.FFmpeg). Deps: `requirements.txt`
(fastapi, uvicorn, python-multipart, numpy, pillow, resvg-py).

## Architecture

- **`engine.py`** — render engine, adapted from gpx-overlay's
  `render_overlay_svg.py`: identical channel math and `{{ expr | fmt }}`
  template contract (full contract: that repo's **DESIGNS.md**), refactored
  from script globals into a `Ride` dataclass. Renders via resvg-py → ffmpeg
  qtrle pipe, frames parallelized with `mp.Pool` (worker fns must stay
  module-level for Windows spawn pickling). **Keep in sync with gpx-overlay
  if either changes.**
- **`app.py`** — FastAPI: upload/parse GPX, upload/validate designs, PNG
  preview, render jobs (in-memory queue, ONE worker thread, cancellable),
  download. Static frontend mounted at `/`.
- **`static/`** — vanilla HTML/CSS/JS, no build step. Drag-drop, live preview
  over a checkerboard with TikTok/Reels safe-zone guides, progress polling.
- **`designs/`** — bundled presets, scanned at startup: `retro_analog`,
  `minimal` (copies from gpx-overlay) + `map_track` (route map, this repo).

## Map channels (extension over the gpx-overlay contract)

`load_gpx` projects the track (equirectangular, north up) into a 0..100 box:
`track_points` / `track_len` (constants), `pos_x` / `pos_y` / `track_dist`
(per frame). Traveled-trail = full route with
`stroke-dasharray="{{ track_dist }} {{ track_len + 5 }}"` (arc-length based,
so out-and-back overlaps behave). **Not yet ported to the gpx-overlay CLI
renderer** — designs using them are web-app-only. Details in README.md.

## Gotchas (learned here)

- **CapCut formats**: qtrle `.mov` (pix_fmt argb) is the only confirmed
  transparent import. VP9 alpha is ignored by CapCut; ProRes 4444 works but
  is huge. Never make green/chroma the default.
- **Registries are in-memory** (rides/designs/jobs); files live in `data/`
  but a server restart requires re-uploading in the UI.
- **Restart rules**: `engine.py` / `app.py` changes and *new* preset files
  need a server restart (presets scanned at import). Design SVG *content* and
  `static/` files are re-read per request — no restart.
- **Browser cache**: static responses send `Cache-Control: no-cache`, but
  browsers cached copies from before that header existed — hence the
  `?v=N` on `app.js`/`style.css` in index.html. Bump `N` when editing them.
- **Designs with intro fades** (`opacity` driven by `clip_t`) render an
  invisible first frame; the preview scrubber therefore defaults to
  mid-clip. Don't "fix" a blank preview at t=0 — it's the fade.
- **Preview validation**: uploaded designs are test-rendered with
  `engine.sample_ctx()` so template errors surface at upload, not mid-render.
- Render throughput on this machine: ~44 fps plain designs, ~27-30 fps with
  `feDropShadow` (20 cores). Jobs run sequentially; one all-core render each.

## Workflow preferences (same user as gpx-overlay)

- Clips are short (~3 min max); render only requested ranges.
- Rides are in Yogyakarta (WIB, UTC+7); GPX timestamps are UTC @ 1 Hz.
- New/changed designs: verify a PNG preview (safe zones!) before video.
- Safe zones in 405x720 space: keep content in x ∈ [16, 345], y ∈ [56, 585].
