"""Web UI for the GPX telemetry overlay renderer.

Run:  python app.py   (serves http://127.0.0.1:8765 and opens the browser)

Upload a GPX + an SVG design (or pick a bundled preset), choose a start/end
clock time, preview a frame, then render a transparent qtrle .mov for CapCut.
"""
import os, queue, threading, time, uuid, webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import engine

BASE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(BASE, "designs")
GPX_DIR = os.path.join(BASE, "data", "gpx")
DESIGN_DIR = os.path.join(BASE, "data", "designs")
OUT_DIR = os.path.join(BASE, "data", "output")
for d in (GPX_DIR, DESIGN_DIR, OUT_DIR):
    os.makedirs(d, exist_ok=True)

RIDES = {}     # gpx_id -> {"ride": engine.Ride, "name": original filename}
DESIGNS = {}   # design_id -> {"name": display name, "path": svg path, "preset": bool}
JOBS = {}      # job_id -> status dict
JOB_QUEUE = queue.Queue()

for f in sorted(os.listdir(PRESET_DIR)):
    if f.lower().endswith(".svg"):
        stem = os.path.splitext(f)[0]
        DESIGNS[stem] = {"name": stem.replace("_", " "),
                         "path": os.path.join(PRESET_DIR, f), "preset": True}


def _hhmmss(sec):
    sec = int(sec)
    return f"{sec // 3600 % 24:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _ride_info(gpx_id):
    r = RIDES[gpx_id]["ride"]
    return {
        "id": gpx_id,
        "name": RIDES[gpx_id]["name"],
        "date": r.start_date_local,
        "tz": r.tz,
        "start_clock": _hhmmss(r.start_sec_local),
        "end_clock": _hhmmss(r.start_sec_local + r.T - 1),
        "duration_s": r.T,
        "duration_hms": f"{r.T // 3600}:{r.T % 3600 // 60:02d}:{r.T % 60:02d}",
        "dist_km": round(float(r.dist[-1]) / 1000.0, 2),
        "has_hr": r.has_hr,
    }


def _get_ride(gpx_id):
    if gpx_id not in RIDES:
        raise HTTPException(404, "GPX not found — upload it again")
    return RIDES[gpx_id]["ride"]


def _get_template(design_id):
    if design_id not in DESIGNS:
        raise HTTPException(404, "design not found — upload it again")
    with open(DESIGNS[design_id]["path"], "r", encoding="utf-8") as f:
        return engine.load_template(f.read())


def _parse_range(ride, start, end):
    try:
        start_sec = engine.parse_when(start, ride)
        end_sec = engine.parse_when(end, ride)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if start_sec is None:
        start_sec = 0.0
    if end_sec is None:
        end_sec = float(ride.T - 1)
    start_sec = max(0.0, min(start_sec, ride.T - 2))
    end_sec = max(start_sec + 1, min(end_sec, ride.T - 1))
    return start_sec, end_sec


# ---------------------------------------------------------------- render worker
def _job_worker():
    while True:
        jid = JOB_QUEUE.get()
        job = JOBS.get(jid)
        if job is None or job["status"] != "queued":
            continue
        job["status"] = "running"
        job["started"] = time.time()

        def on_progress(done, total, fps, eta):
            job["done"] = done
            job["fps"] = round(fps, 1)
            job["eta"] = int(eta)

        try:
            ok = engine.render_video(
                job["_ride"], job["_tmpl"], job["_start_sec"], job["_end_sec"],
                job["out_path"], alpha=job["alpha"],
                progress=on_progress,
                cancelled=lambda: job["status"] == "cancelling")
            if ok:
                job["done"] = job["total"]
                job["status"] = "done"
                job["size"] = os.path.getsize(job["out_path"])
            else:
                job["status"] = "cancelled"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=_job_worker, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Static files: always revalidate, so UI updates land on plain reload."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------- api
@app.get("/api/health")
def health():
    return {"ffmpeg": engine.find_ffmpeg() is not None}


@app.post("/api/gpx")
async def upload_gpx(file: UploadFile, tz: float = Form(7.0)):
    gpx_id = uuid.uuid4().hex[:12]
    path = os.path.join(GPX_DIR, gpx_id + ".gpx")
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        ride = engine.load_gpx(path, tz=tz)
    except Exception as e:
        os.remove(path)
        raise HTTPException(400, f"could not parse GPX: {e}")
    RIDES[gpx_id] = {"ride": ride, "name": file.filename or "ride.gpx"}
    return _ride_info(gpx_id)


@app.post("/api/gpx/{gpx_id}/tz")
def set_tz(gpx_id: str, tz: float = Form(...)):
    _get_ride(gpx_id).set_tz(tz)
    return _ride_info(gpx_id)


@app.get("/api/designs")
def list_designs():
    return [{"id": k, "name": v["name"], "preset": v["preset"]}
            for k, v in DESIGNS.items()]


@app.post("/api/design")
async def upload_design(file: UploadFile):
    raw = (await file.read()).decode("utf-8", errors="replace")
    tmpl = engine.load_template(raw)
    try:
        engine.validate_design(tmpl)
    except (engine.TemplateError, ValueError) as e:
        raise HTTPException(400, str(e))
    design_id = uuid.uuid4().hex[:12]
    path = os.path.join(DESIGN_DIR, design_id + ".svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
    name = os.path.splitext(file.filename or "custom")[0]
    DESIGNS[design_id] = {"name": name, "path": path, "preset": False}
    return {"id": design_id, "name": name, "preset": False}


@app.post("/api/preview")
def preview(gpx_id: str = Form(...), design_id: str = Form(...),
            start: str = Form(None), end: str = Form(None), at: str = Form(None)):
    ride = _get_ride(gpx_id)
    tmpl = _get_template(design_id)
    start_sec, end_sec = _parse_range(ride, start, end)
    try:
        at_sec = engine.parse_when(at, ride)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if at_sec is None:
        at_sec = start_sec
    try:
        png = engine.render_png_bytes(ride, tmpl, start_sec, end_sec, at_sec)
    except engine.TemplateError as e:
        raise HTTPException(400, str(e))
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/render")
def render(gpx_id: str = Form(...), design_id: str = Form(...),
           start: str = Form(...), end: str = Form(...),
           alpha: str = Form("qtrle")):
    if alpha not in engine.ENCODERS:
        raise HTTPException(400, f"unknown format: {alpha}")
    ride = _get_ride(gpx_id)
    tmpl = _get_template(design_id)
    start_sec, end_sec = _parse_range(ride, start, end)
    nframes = int(end_sec * engine.FPS) - int(start_sec * engine.FPS)

    c0 = int(ride.start_sec_local + start_sec)
    c1 = int(ride.start_sec_local + end_sec)
    tag = (f"{c0 // 3600 % 24:02d}{c0 % 3600 // 60:02d}{c0 % 60:02d}"
           f"-{c1 // 3600 % 24:02d}{c1 % 3600 // 60:02d}{c1 % 60:02d}")
    stem = os.path.splitext(RIDES[gpx_id]["name"])[0]
    fname = f"{stem}_{DESIGNS[design_id]['name'].replace(' ', '_')}_{tag}" \
            + engine.ENCODERS[alpha]["ext"]

    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {
        "id": jid, "status": "queued", "done": 0, "total": nframes,
        "fps": 0, "eta": None, "error": None, "size": None,
        "file": fname, "out_path": os.path.join(OUT_DIR, jid + "_" + fname),
        "alpha": alpha, "created": time.time(),
        "label": f"{stem} · {DESIGNS[design_id]['name']} · "
                 f"{_hhmmss(c0)}–{_hhmmss(c1)}",
        "_ride": ride, "_tmpl": tmpl,
        "_start_sec": start_sec, "_end_sec": end_sec,
    }
    JOB_QUEUE.put(jid)
    return _job_public(JOBS[jid])


def _job_public(job):
    return {k: v for k, v in job.items() if not k.startswith("_") and k != "out_path"}


@app.get("/api/jobs")
def jobs():
    return [_job_public(j) for j in
            sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)]


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "job not found")
    return _job_public(JOBS[jid])


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "job not found")
    j = JOBS[jid]
    if j["status"] == "queued":
        j["status"] = "cancelled"
    elif j["status"] == "running":
        j["status"] = "cancelling"
    return _job_public(j)


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    j = JOBS.get(jid)
    if j is None or j["status"] != "done":
        raise HTTPException(404, "no finished file for this job")
    return FileResponse(j["out_path"], filename=j["file"],
                        media_type="video/quicktime")


app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True),
          name="static")


if __name__ == "__main__":
    import uvicorn
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
