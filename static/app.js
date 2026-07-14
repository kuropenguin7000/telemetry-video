"use strict";

const $ = (s) => document.querySelector(s);

const state = {
  gpx: null,        // ride info from /api/gpx
  designId: null,
  // 0..1 within [start,end]; default mid-clip so designs with intro fades
  // (opacity driven by clip_t) don't preview as a blank first frame
  previewAtFrac: 0.5,
  polling: {},      // job_id -> interval
};

/* ---------------- helpers ---------------- */
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 5000);
}

function clockToSec(s) {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec((s || "").trim());
  if (!m) return null;
  return (+m[1]) * 3600 + (+m[2]) * 60 + (+(m[3] || 0));
}
function secToClock(sec) {
  sec = ((Math.round(sec) % 86400) + 86400) % 86400;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(sec / 3600))}:${p(Math.floor(sec / 60) % 60)}:${p(sec % 60)}`;
}
/* elapsed ride-seconds for a clock string (wraps past midnight like the CLI) */
function relSec(clockStr) {
  const c = clockToSec(clockStr);
  if (c === null || !state.gpx) return null;
  const start = clockToSec(state.gpx.start_clock);
  let rel = c - start;
  if (rel < 0) rel += 86400;
  return rel;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r;
}

/* ---------------- ffmpeg banner ---------------- */
api("/api/health").then((r) => r.json()).then((h) => {
  if (!h.ffmpeg) $("#ffmpeg-warning").classList.remove("hidden");
}).catch(() => {});

/* ---------------- dropzones ---------------- */
function wireDrop(zoneSel, inputSel, onFile) {
  const zone = $(zoneSel), input = $(inputSel);
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
  });
}

/* ---------------- 1 · GPX ---------------- */
let lastGpxFile = null;

async function uploadGpx(file) {
  lastGpxFile = file;
  const zone = $("#drop-gpx");
  zone.querySelector("p").innerHTML = "Parsing <strong>" + file.name + "</strong>…";
  const fd = new FormData();
  fd.append("file", file);
  fd.append("tz", $("#tz").value || "7");
  try {
    const info = await (await api("/api/gpx", { method: "POST", body: fd })).json();
    state.gpx = info;
    zone.querySelector("p").innerHTML = `<strong>${info.name}</strong> — drop another to replace`;
    renderRideInfo();
    setDefaultTimes();
    refreshAll();
  } catch (e) {
    zone.querySelector("p").innerHTML = "<strong>Drop a .gpx here</strong> or click to browse";
    toast("GPX: " + e.message);
  }
}

function renderRideInfo() {
  const i = state.gpx;
  $("#ride-info").classList.remove("hidden");
  $("#ride-info").innerHTML = `
    <span class="pill">📅 <b>${i.date}</b></span>
    <span class="pill">🕐 <b>${i.start_clock}</b> – <b>${i.end_clock}</b></span>
    <span class="pill">⏱ <b>${i.duration_hms}</b></span>
    <span class="pill">📏 <b>${i.dist_km} km</b></span>
    <span class="pill">${i.has_hr ? "❤️ HR ✓" : "🩶 no HR"}</span>`;
}

function setDefaultTimes() {
  const start = clockToSec(state.gpx.start_clock);
  const endDefault = Math.min(60, state.gpx.duration_s - 1);
  $("#t-start").value = secToClock(start);
  $("#t-end").value = secToClock(start + endDefault);
}

$("#tz").addEventListener("change", async () => {
  if (!state.gpx) return;
  const fd = new FormData();
  fd.append("tz", $("#tz").value || "7");
  try {
    state.gpx = await (await api(`/api/gpx/${state.gpx.id}/tz`, { method: "POST", body: fd })).json();
    renderRideInfo();
    setDefaultTimes();
    refreshAll();
  } catch (e) { toast(e.message); }
});

wireDrop("#drop-gpx", "#file-gpx", uploadGpx);

/* ---------------- 2 · designs ---------------- */
async function loadDesigns() {
  const list = await (await api("/api/designs")).json();
  const box = $("#design-chips");
  for (const d of list) addChip(d);
  const first = box.querySelector(".chip");
  if (first && !state.designId) first.click();
}

function addChip(d) {
  const box = $("#design-chips");
  const b = document.createElement("button");
  b.className = "chip";
  b.dataset.id = d.id;
  b.innerHTML = d.name + (d.preset ? '<span class="tag">preset</span>' : '<span class="tag">custom</span>');
  b.addEventListener("click", () => {
    box.querySelectorAll(".chip").forEach((c) => c.classList.remove("sel"));
    b.classList.add("sel");
    state.designId = d.id;
    $("#design-error").classList.add("hidden");
    refreshAll();
  });
  box.appendChild(b);
  return b;
}

async function uploadDesign(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const d = await (await api("/api/design", { method: "POST", body: fd })).json();
    $("#design-error").classList.add("hidden");
    addChip(d).click();
  } catch (e) {
    $("#design-error").textContent = e.message;
    $("#design-error").classList.remove("hidden");
  }
}

wireDrop("#drop-design", "#file-design", uploadDesign);

/* ---------------- 3 · time range ---------------- */
function validTimes() {
  if (!state.gpx) return null;
  const s = relSec($("#t-start").value);
  const e = relSec($("#t-end").value);
  const err = $("#time-error");
  if (s === null || e === null) {
    err.textContent = "Times must be HH:MM:SS (wall clock, like the on-screen clock).";
    err.classList.remove("hidden");
    return null;
  }
  if (s >= state.gpx.duration_s - 1) {
    err.textContent = `Start is outside the ride (${state.gpx.start_clock} – ${state.gpx.end_clock}).`;
    err.classList.remove("hidden");
    return null;
  }
  if (e <= s) {
    err.textContent = "End must be after start.";
    err.classList.remove("hidden");
    return null;
  }
  err.classList.add("hidden");
  return { s, e: Math.min(e, state.gpx.duration_s - 1) };
}

for (const sel of ["#t-start", "#t-end"]) {
  $(sel).addEventListener("input", () => refreshAll());
}
document.querySelectorAll(".quick button").forEach((b) => {
  b.addEventListener("click", () => {
    const s = clockToSec($("#t-start").value);
    if (s === null) return;
    $("#t-end").value = secToClock(s + (+b.dataset.dur));
    refreshAll();
  });
});

/* ---------------- preview ---------------- */
let previewTimer = null;
let previewSeq = 0;

function refreshAll() {
  $("#btn-render").disabled = !(state.gpx && state.designId && validTimes());
  updateScrub();
  schedulePreview();
}

function updateScrub() {
  const t = validTimes();
  const slider = $("#preview-slider");
  if (!t) { slider.disabled = true; return; }
  slider.disabled = false;
  const start = clockToSec(state.gpx.start_clock);
  $("#scrub-start").textContent = secToClock(start + t.s);
  $("#scrub-end").textContent = secToClock(start + t.e);
  $("#scrub-at").textContent = secToClock(start + t.s + (t.e - t.s) * state.previewAtFrac);
}

$("#preview-slider").addEventListener("input", () => {
  state.previewAtFrac = $("#preview-slider").value / 1000;
  updateScrub();
  schedulePreview(150);
});

function schedulePreview(delay = 350) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(doPreview, delay);
}

async function doPreview() {
  const t = validTimes();
  if (!state.gpx || !state.designId || !t) return;
  const seq = ++previewSeq;
  $("#preview-empty").classList.add("hidden");
  $("#preview-spin").classList.remove("hidden");
  const at = t.s + (t.e - t.s) * state.previewAtFrac;
  const fd = new FormData();
  fd.append("gpx_id", state.gpx.id);
  fd.append("design_id", state.designId);
  fd.append("start", String(t.s));
  fd.append("end", String(t.e));
  fd.append("at", String(at));
  try {
    const blob = await (await api("/api/preview", { method: "POST", body: fd })).blob();
    if (seq !== previewSeq) return;           // stale response
    const img = $("#preview-img");
    const old = img.src;
    img.src = URL.createObjectURL(blob);
    img.classList.add("show");
    if (old) URL.revokeObjectURL(old);
  } catch (e) {
    if (seq === previewSeq) toast("Preview: " + e.message);
  } finally {
    if (seq === previewSeq) $("#preview-spin").classList.add("hidden");
  }
}

$("#safe-toggle").addEventListener("change", () =>
  $("#safe-overlay").classList.toggle("show", $("#safe-toggle").checked));
$("#safe-overlay").classList.add("show");
// set from state, not the HTML attribute — survives cached HTML and the
// browser's form-state restoration
$("#preview-slider").value = state.previewAtFrac * 1000;

/* ---------------- 4 · render jobs ---------------- */
$("#btn-render").addEventListener("click", async () => {
  const t = validTimes();
  if (!t) return;
  const fd = new FormData();
  fd.append("gpx_id", state.gpx.id);
  fd.append("design_id", state.designId);
  fd.append("start", $("#t-start").value);
  fd.append("end", $("#t-end").value);
  fd.append("alpha", $("#alpha").value);
  try {
    const job = await (await api("/api/render", { method: "POST", body: fd })).json();
    upsertJob(job);
    pollJob(job.id);
  } catch (e) { toast("Render: " + e.message); }
});

function jobEl(id) {
  let el = document.getElementById("job-" + id);
  if (!el) {
    el = document.createElement("div");
    el.className = "job";
    el.id = "job-" + id;
    $("#job-list").prepend(el);
  }
  return el;
}

function fmtSize(b) {
  if (b == null) return "";
  return b > 1 << 30 ? (b / (1 << 30)).toFixed(2) + " GB" : (b / (1 << 20)).toFixed(1) + " MB";
}

function upsertJob(j) {
  const el = jobEl(j.id);
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  const statTxt = {
    queued: "queued", running: pct + "%", cancelling: "cancelling…",
    done: "done ✓", error: "failed", cancelled: "cancelled",
  }[j.status] || j.status;

  let right = "";
  if (j.status === "done") {
    right = `<a class="dl" href="/api/jobs/${j.id}/download">Download ${fmtSize(j.size)}</a>`;
  } else if (j.status === "queued" || j.status === "running") {
    right = `<button class="cancel" onclick="cancelJob('${j.id}')">cancel</button>`;
  }

  let meta = "";
  if (j.status === "running") {
    meta = `<div class="meta"><span>${j.done}/${j.total} frames · ${j.fps} fps</span>
            <span>ETA ${j.eta != null ? j.eta + " s" : "—"}</span></div>`;
  } else if (j.status === "error") {
    meta = `<div class="meta"><span style="color:var(--danger)">${j.error || ""}</span></div>`;
  } else if (j.status === "done") {
    meta = `<div class="meta"><span>${j.file}</span></div>`;
  }

  el.innerHTML = `
    <div class="job-top">
      <span class="lbl" title="${j.label}">${j.label}</span>
      <span class="stat ${j.status}">${statTxt}</span>${right}
    </div>
    ${j.status === "running" || j.status === "queued"
      ? `<div class="bar"><div style="width:${pct}%"></div></div>` : ""}
    ${meta}`;
}

window.cancelJob = async (id) => {
  try { upsertJob(await (await api(`/api/jobs/${id}/cancel`, { method: "POST" })).json()); }
  catch (e) { toast(e.message); }
};

function pollJob(id) {
  if (state.polling[id]) return;
  state.polling[id] = setInterval(async () => {
    try {
      const j = await (await api("/api/jobs/" + id)).json();
      upsertJob(j);
      if (["done", "error", "cancelled"].includes(j.status)) {
        clearInterval(state.polling[id]);
        delete state.polling[id];
      }
    } catch (e) {
      clearInterval(state.polling[id]);
      delete state.polling[id];
    }
  }, 600);
}

/* resume any jobs from a previous page load of this server session */
api("/api/jobs").then((r) => r.json()).then((jobs) => {
  for (const j of jobs.reverse()) {
    upsertJob(j);
    if (["queued", "running", "cancelling"].includes(j.status)) pollJob(j.id);
  }
}).catch(() => {});

loadDesigns();
