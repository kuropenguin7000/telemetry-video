"""GPX telemetry render engine for the web app.

Adapted from gpx-overlay/render_overlay_svg.py: identical channel math,
`{{ expr | fmt }}` SVG template contract (see that repo's DESIGNS.md) and
transparent qtrle .mov output. Refactored from script globals into a Ride
object so the server can hold several rides, run renders off the request
thread, and report progress / honour cancellation.
"""
import glob, io, math, multiprocessing as mp, os, re, shutil, subprocess, time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import numpy as np
from PIL import Image
import resvg_py

W, H, FPS = 1080, 1920, 30
HR_MAX = 190.0

NS = {"g": "http://www.topografix.com/GPX/1/1",
      "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"}


class TemplateError(Exception):
    pass


def find_ffmpeg():
    """FFMPEG_PATH env var, then PATH, then the winget Gyan.FFmpeg install."""
    p = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if p:
        return p
    hits = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"),
        recursive=True)
    return hits[0] if hits else None


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- gpx -> telemetry
@dataclass
class Ride:
    path: str
    t0_utc: float          # UTC timestamp of first trackpoint
    T: int                 # duration in seconds (1 Hz grid length)
    secs: np.ndarray
    spd: np.ndarray        # km/h
    hr: np.ndarray         # bpm
    ele: np.ndarray        # m
    dist: np.ndarray       # m
    grade: np.ndarray      # %
    incl: np.ndarray       # deg
    acc: np.ndarray        # g
    climb: np.ndarray      # m cumulative
    map_x: np.ndarray      # position in the 0..100 map box (north up)
    map_y: np.ndarray
    map_d: np.ndarray      # arc length along track_points up to each second
    track_points: str      # decimated full-ride polyline, "x,y x,y ..." in 0..100
    track_len: float       # total arc length of track_points, same units
    has_hr: bool
    tz: float = 7.0
    start_sec_local: int = 0
    start_date_local: str = ""

    def set_tz(self, tz):
        self.tz = tz
        local = datetime.fromtimestamp(self.t0_utc, tz=timezone(timedelta(hours=tz)))
        self.start_sec_local = local.hour * 3600 + local.minute * 60 + local.second
        self.start_date_local = local.strftime("%Y/%m/%d")


def load_gpx(path, tz=7.0):
    """Parse GPX, resample to 1 Hz, derive all display channels."""
    root = ET.parse(path).getroot()
    pts = root.findall(".//g:trkseg/g:trkpt", NS)
    if not pts:
        raise ValueError("no trackpoints found in the GPX file")
    la, lo, el, h, t = [], [], [], [], []
    for p in pts:
        la.append(float(p.get("lat")))
        lo.append(float(p.get("lon")))
        e = p.find("g:ele", NS)
        el.append(float(e.text) if e is not None else 0.0)
        hh = p.find(".//tpx:hr", NS)
        h.append(float(hh.text) if hh is not None else 0.0)
        tm = p.find("g:time", NS)
        if tm is None:
            raise ValueError("trackpoints have no <time> — timestamps are required")
        t.append(datetime.fromisoformat(tm.text.replace("Z", "+00:00")).timestamp())
    la = np.array(la); lo = np.array(lo); el = np.array(el)
    h = np.array(h); t = np.array(t)
    sec = (t - t[0]).astype(int)
    if sec[-1] < 2:
        raise ValueError("GPX track is shorter than 2 seconds")

    step = np.zeros(len(la))
    for i in range(1, len(la)):
        step[i] = haversine(la[i - 1], lo[i - 1], la[i], lo[i])
    cum = np.cumsum(step)

    T = int(sec[-1]) + 1
    grid = np.arange(T)
    ele_r = np.interp(grid, sec, el)
    hr = np.interp(grid, sec, h)
    dist = np.interp(grid, sec, cum)

    # speed km/h from smoothed distance derivative
    kern = np.ones(7) / 7
    ds = np.convolve(dist, kern, mode="same")
    ds[:7] = dist[:7]; ds[-7:] = dist[-7:]
    spd = np.clip(np.gradient(ds) * 3.6, 0, None)
    spd = np.convolve(spd, np.ones(3) / 3, mode="same")

    ele = np.convolve(ele_r, np.ones(9) / 9, mode="same")
    ele[:9] = ele_r[:9]; ele[-9:] = ele_r[-9:]

    grade = np.zeros(T)
    w = 5
    for i in range(T):
        a, b = max(0, i - w), min(T - 1, i + w)
        dd = dist[b] - dist[a]
        if dd > 5:
            grade[i] = (ele[b] - ele[a]) / dd * 100.0
    grade = np.convolve(grade, np.ones(5) / 5, mode="same")
    incl = np.degrees(np.arctan(np.clip(grade, -25, 25) / 100.0))
    grade_disp = np.tan(np.radians(incl)) * 100.0

    climb = np.cumsum(np.clip(np.diff(ele, prepend=ele[0]), 0, None))

    acc = np.gradient(spd / 3.6)
    acc = np.convolve(acc, np.ones(5) / 5, mode="same") / 9.81

    # map track: equirectangular projection, north up, fit into a 0..100 box
    latg = np.interp(grid, sec, la)
    long = np.interp(grid, sec, lo)
    px = (long - long.min()) * math.cos(math.radians(float(latg.mean())))
    py = latg.max() - latg
    mw, mh = float(px.max()), float(py.max())
    s = 100.0 / max(mw, mh, 1e-9)
    map_x = px * s + (100.0 - mw * s) / 2
    map_y = py * s + (100.0 - mh * s) / 2
    # decimate to <=400 vertices for the static polyline string
    idx = np.unique(np.linspace(0, T - 1, min(T, 400)).round().astype(int))
    seg = np.hypot(np.diff(map_x[idx]), np.diff(map_y[idx]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    map_d = np.interp(grid, idx, cum)
    track_points = " ".join(f"{x:.2f},{y:.2f}"
                            for x, y in zip(map_x[idx], map_y[idx]))

    ride = Ride(path=path, t0_utc=float(t[0]), T=T, secs=grid, spd=spd, hr=hr,
                ele=ele, dist=dist, grade=grade_disp, incl=incl, acc=acc,
                climb=climb, map_x=map_x, map_y=map_y, map_d=map_d,
                track_points=track_points, track_len=float(cum[-1]),
                has_hr=bool(h.max() > 0))
    ride.set_tz(tz)
    return ride


def parse_when(s, ride):
    """HH:MM[:SS] local clock time (matches on-screen clock) or ride-seconds."""
    if s is None or s == "":
        return None
    s = str(s).strip()
    if ":" in s:
        parts = [int(p) for p in s.split(":")]
        if len(parts) < 2:
            raise ValueError(f"bad time: {s}")
        clock = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
        rel = clock - ride.start_sec_local
        if rel < 0:
            rel += 86400
        return float(rel)
    return float(s)


# ---------------------------------------------------------------- template engine
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)   # stripped so doc examples aren't evaluated

_ZONE_EDGES = [0.60, 0.70, 0.80, 0.90]
_ZONE_COLORS = ["#9aa0a6", "#3b82f6", "#22c55e", "#f59e0b", "#ef4444"]


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _lerp(a, b, t):
    return a + (b - a) * t


def _pick(i, *vals):
    """Categorical select: pick(index, v0, v1, ...) clamped to range."""
    i = int(i)
    return vals[max(0, min(i, len(vals) - 1))]


def _zone(hr_bpm):
    frac = hr_bpm / HR_MAX
    z = 1
    for e in _ZONE_EDGES:
        if frac >= e:
            z += 1
    return min(z, 5)


def _zone_color(hr_bpm):
    return _ZONE_COLORS[_zone(hr_bpm) - 1]


def _zone_name(hr_bpm):
    return "Z%d" % _zone(hr_bpm)


SAFE_ENV = {
    "__builtins__": {},
    "clamp": _clamp, "lerp": _lerp, "pick": _pick,
    "zone": _zone, "zone_color": _zone_color, "zone_name": _zone_name,
    "min": min, "max": max, "abs": abs, "round": round,
    "int": int, "float": float,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "radians": math.radians, "hypot": math.hypot, "sqrt": math.sqrt,
    "pi": math.pi, "HR_MAX": HR_MAX,
}


def load_template(text):
    """Strip comments (they may hold doc examples with {{ }})."""
    return _COMMENT.sub("", text)


def _fmt_default(val):
    if isinstance(val, float):
        s = ("%f" % val).rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(val)


def render_template(tmpl, ctx):
    """Substitute every {{ expr | fmt }} in tmpl using channel dict ctx."""
    def repl(m):
        expr, sep, fmt = m.group(1).partition("|")
        try:
            val = eval(expr.strip(), dict(SAFE_ENV), ctx)  # noqa: S307 (trusted template)
        except Exception as e:
            raise TemplateError(f"template error in {{{{ {m.group(1).strip()} }}}}: {e}")
        fmt = fmt.strip()
        try:
            return format(val, fmt) if fmt else _fmt_default(val)
        except Exception as e:
            raise TemplateError(f"bad format spec in {{{{ {m.group(1).strip()} }}}}: {e}")
    return _PLACEHOLDER.sub(repl, tmpl)


def frame_ctx(start_sec_local, i, ft, ch, nframes, consts=None):
    """Build the channel dict for frame i from precomputed per-frame arrays."""
    tsec = int(ft[i])
    clk = int(start_sec_local + tsec)
    h24 = clk // 3600 % 24
    h12 = h24 % 12 or 12
    mm = clk % 3600 // 60
    ss = clk % 60
    ampm = "AM" if h24 < 12 else "PM"
    ctx = {
        "speed": float(ch["spd"][i]),
        "hr": float(ch["hr"][i]),
        "ele": float(ch["ele"][i]),
        "dist_m": float(ch["dist"][i]),
        "dist_km": float(ch["dist"][i]) / 1000.0,
        "grade": float(ch["grade"][i]),
        "incl": float(ch["incl"][i]),
        "acc": float(ch["acc"][i]),
        "climb": float(ch["climb"][i] - ch["climb"][0]),
        "climb_total": float(ch["climb"][i]),
        "duration": tsec,
        "dur_h": tsec // 3600, "dur_m": tsec % 3600 // 60, "dur_s": tsec % 60,
        "duration_hms": "%d:%02d:%02d" % (tsec // 3600, tsec % 3600 // 60, tsec % 60),
        "clock_s": clk, "hour24": h24, "hour12": h12, "minute": mm, "second": ss,
        "ampm": ampm,
        "clock12": "%d:%02d:%02d %s" % (h12, mm, ss, ampm),
        "clock24": "%02d:%02d:%02d" % (h24, mm, ss),
        "frame": i, "nframes": nframes, "fps": FPS,
        "clip_t": i / FPS, "clip_dur": nframes / FPS,
        # map tracking (0..100 box, north up; see designs/map_track.svg)
        "pos_x": float(ch["map_x"][i]), "pos_y": float(ch["map_y"][i]),
        "track_dist": float(ch["map_d"][i]),
    }
    if consts:
        ctx.update(consts)
    return ctx


def sample_ctx():
    """Plausible mid-ride values for validating a design without a GPX."""
    return {
        "speed": 23.4, "hr": 142.0, "ele": 156.0,
        "dist_m": 5432.1, "dist_km": 5.4321,
        "grade": 3.2, "incl": 1.83, "acc": 0.02,
        "climb": 42.0, "climb_total": 123.0,
        "duration": 754, "dur_h": 0, "dur_m": 12, "dur_s": 34,
        "duration_hms": "0:12:34",
        "clock_s": 21754, "hour24": 6, "hour12": 6, "minute": 2, "second": 34,
        "ampm": "AM", "clock12": "6:02:34 AM", "clock24": "06:02:34",
        "frame": 450, "nframes": 900, "fps": FPS,
        "clip_t": 15.0, "clip_dur": 30.0,
        "pos_x": 58.0, "pos_y": 44.0, "track_dist": 78.0, "track_len": 160.0,
        "track_points": "12,88 20,70 34,64 42,48 58,44 70,30 82,26 90,12",
    }


def rasterize(svg_str):
    """SVG string -> RGBA PIL image at W x H (transparent background)."""
    data = bytes(resvg_py.svg_to_bytes(svg_string=svg_str, width=W, height=H))
    img = Image.open(io.BytesIO(data))
    return img if img.mode == "RGBA" else img.convert("RGBA")


def validate_design(tmpl):
    """Render one frame with sample values; raises TemplateError/ValueError."""
    try:
        rasterize(render_template(tmpl, sample_ctx()))
    except TemplateError:
        raise
    except Exception as e:
        raise ValueError(f"SVG failed to rasterize: {e}")


def clip_arrays(ride, start_sec, end_sec):
    """Per-frame channel arrays for [start_sec, end_sec) of the ride."""
    f0, f1 = int(start_sec * FPS), int(end_sec * FPS)
    nframes = f1 - f0
    ft = np.arange(f0, f1) / FPS
    ch = {k: np.interp(ft, ride.secs, getattr(ride, k))
          for k in ("spd", "hr", "ele", "dist", "grade", "incl", "acc", "climb",
                    "map_x", "map_y", "map_d")}
    return ft, ch, nframes


def _consts(ride):
    """Per-ride constants merged into every frame ctx."""
    return {"track_points": ride.track_points, "track_len": ride.track_len}


def render_png_bytes(ride, tmpl, start_sec, end_sec, at_sec):
    """One preview frame as PNG bytes; clip_t/frame reflect position in the clip."""
    ft, ch, nframes = clip_arrays(ride, start_sec, end_sec)
    i = max(0, min(int((at_sec - start_sec) * FPS), nframes - 1))
    img = rasterize(render_template(
        tmpl, frame_ctx(ride.start_sec_local, i, ft, ch, nframes, _consts(ride))))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# worker globals set once per process (template is constant across frames)
_W_TMPL = None


def _worker_init(tmpl):
    global _W_TMPL
    _W_TMPL = tmpl


def _worker_render(ctx):
    return rasterize(render_template(_W_TMPL, ctx)).tobytes()


ENCODERS = {
    "qtrle":  {"ext": ".mov", "args": ["-c:v", "qtrle"]},
    "prores": {"ext": ".mov", "args": ["-c:v", "prores_ks", "-profile:v", "4444",
                                       "-pix_fmt", "yuva444p10le"]},
}


def render_video(ride, tmpl, start_sec, end_sec, out_path, alpha="qtrle",
                 jobs=0, progress=None, cancelled=None):
    """Render the clip to out_path. Returns True if completed, False if cancelled.

    progress(done, total, fps, eta_s) is called per frame from the caller's
    thread; cancelled() is polled between frames.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found: install it (winget install Gyan.FFmpeg) "
                           "or set FFMPEG_PATH")
    ft, ch, nframes = clip_arrays(ride, start_sec, end_sec)
    if nframes < 1:
        raise ValueError("empty time range")

    cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"] + ENCODERS[alpha]["args"] + [out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    njobs = jobs or (os.cpu_count() or 1)
    njobs = max(1, min(njobs, nframes))
    consts = _consts(ride)
    ctxs = (frame_ctx(ride.start_sec_local, i, ft, ch, nframes, consts)
            for i in range(nframes))
    t_start = time.time()
    ok = True
    try:
        # frames are independent -> render across a process pool, write in order
        with mp.Pool(njobs, initializer=_worker_init, initargs=(tmpl,)) as pool:
            for k, buf in enumerate(pool.imap(_worker_render, ctxs, chunksize=4)):
                if cancelled and cancelled():
                    ok = False
                    pool.terminate()
                    break
                proc.stdin.write(buf)
                if progress:
                    el = time.time() - t_start
                    fps = (k + 1) / el if el > 0 else 0
                    eta = (nframes - k - 1) / fps if fps > 0 else 0
                    progress(k + 1, nframes, fps, eta)
    except BaseException:
        proc.kill()
        raise
    finally:
        if ok:
            proc.stdin.close()
            proc.wait()
        else:
            proc.kill()
            proc.wait()
            try:
                os.remove(out_path)
            except OSError:
                pass
    if ok and proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")
    return ok
