const state = {
  sessionId: null,
  slides: [],
  currentIndex: 0,
  mediaRecorder: null,
  audioChunks: [],
  timestamps: [],
  recordStart: null,
  timerInterval: null,
};

const el = (id) => document.getElementById(id);

// ── Step navigation ──
let maxStep = 0;

function updateStepNav(activeStep) {
  [0, 1, 2].forEach(i => {
    const btn = el(`step-btn-${i}`);
    if (!btn) return;
    btn.classList.toggle('is-active', i === activeStep);
    btn.classList.toggle('is-locked', i > maxStep);
  });
}

function goToStep(index) {
  if (index > maxStep) return;
  el('screen-upload').hidden  = index !== 0;
  el('screen-present').hidden = index !== 1;
  el('screen-results').hidden = index !== 2;
  updateStepNav(index);
}

[0, 1, 2].forEach(i => {
  const btn = el(`step-btn-${i}`);
  if (btn) btn.addEventListener('click', () => goToStep(i));
});

updateStepNav(0);

// ---------- Screen 1: upload ----------

el("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pptx = el("input-pptx").files[0];
  if (!pptx) {
    el("label-pptx").classList.add("has-error");
    el("pptx-error").hidden = false;
    el("input-pptx").focus();
    return;
  }
  el("label-pptx").classList.remove("has-error");
  el("pptx-error").hidden = true;

  const form = new FormData();
  form.append("pptx", pptx);
  if (el("input-xlsx").files[0]) form.append("xlsx", el("input-xlsx").files[0]);
  for (const f of el("input-context").files) form.append("context", f);

  el("btn-upload").disabled = true;
  el("upload-status").textContent = "Parsing deck and rendering slides — this can take a bit for larger decks…";

  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    if (!res.ok) {
      let msg; try { msg = (await res.json()).error; } catch { msg = await res.text(); }
      throw new Error(msg || "Upload failed");
    }
    const data = await res.json();
    state.sessionId = data.session_id;
    state.slides = data.slides;
    state.currentIndex = 0;

    maxStep = Math.max(maxStep, 1);
    goToStep(1);
    showSlide(0);
  } catch (err) {
    el("upload-status").textContent = "Error: " + err.message;
    el("btn-upload").disabled = false;
  }
});

el("input-pptx").addEventListener("change", () => {
  if (el("input-pptx").files[0]) {
    el("label-pptx").classList.remove("has-error");
    el("pptx-error").hidden = true;
  }
});

// ---------- Screen 2: present + record ----------

function showSlide(index) {
  state.currentIndex = index;
  el("slide-image").src = `/api/slide_image/${state.sessionId}/${index}`;
  el("slide-counter").textContent = `Slide ${index + 1} of ${state.slides.length}`;
  el("btn-prev").disabled = index === 0;
  el("btn-next").disabled = index === state.slides.length - 1;
}

function logSlideChange(index) {
  if (state.recordStart === null) return;
  const t = (Date.now() - state.recordStart) / 1000;
  state.timestamps.push({ slideIndex: index, timestamp: t });
}

el("btn-prev").addEventListener("click", () => {
  if (state.currentIndex === 0) return;
  const idx = state.currentIndex - 1;
  showSlide(idx);
  logSlideChange(idx);
});

el("btn-next").addEventListener("click", () => {
  if (state.currentIndex === state.slides.length - 1) return;
  const idx = state.currentIndex + 1;
  showSlide(idx);
  logSlideChange(idx);
});

el("btn-record").addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    state.mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    state.audioChunks = [];
    state.timestamps = [{ slideIndex: state.currentIndex, timestamp: 0 }];
    state.recordStart = Date.now();

    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = onRecordingStopped;
    state.mediaRecorder.start();

    el("btn-record").disabled = true;
    el("btn-stop").disabled = false;
    el("record-status").textContent = "Recording…";
    state.timerInterval = setInterval(updateTimer, 250);
  } catch (err) {
    el("record-status").textContent = "Microphone access failed: " + err.message;
  }
});

el("btn-stop").addEventListener("click", () => {
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
    state.mediaRecorder.stream.getTracks().forEach((t) => t.stop());
  }
  clearInterval(state.timerInterval);
  el("btn-stop").disabled = true;
});

function updateTimer() {
  const secs = Math.floor((Date.now() - state.recordStart) / 1000);
  const mm = String(Math.floor(secs / 60)).padStart(2, "0");
  const ss = String(secs % 60).padStart(2, "0");
  el("record-timer").textContent = `${mm}:${ss}`;
}

async function onRecordingStopped() {
  el("record-status").textContent = "Processing your recording — transcribing and analyzing…";
  const blob = new Blob(state.audioChunks, { type: "audio/webm" });

  const form = new FormData();
  form.append("audio", blob, "recording.webm");
  form.append("timestamps", JSON.stringify(state.timestamps));

  try {
    const audioRes = await fetch(`/api/upload_audio/${state.sessionId}`, { method: "POST", body: form });
    if (!audioRes.ok) {
      let msg; try { msg = (await audioRes.json()).error; } catch { msg = await audioRes.text(); }
      throw new Error(msg || "Audio upload failed");
    }
    el("record-status").textContent = "Running the full analysis with Claude — this can take a minute…";
    const res = await fetch(`/api/analyze/${state.sessionId}`, { method: "POST" });
    if (!res.ok) {
      let msg; try { msg = (await res.json()).error; } catch { msg = await res.text(); }
      throw new Error(msg || "Analysis failed");
    }
    const results = await res.json();
    renderResults(results);
    maxStep = Math.max(maxStep, 2);
    goToStep(2);
  } catch (err) {
    el("record-status").textContent = "Error: " + err.message;
    el("btn-record").disabled = false;
  }
}

// ---------- Screen 3: results ----------

function renderResults(data) {
  const container = el("results-content");
  container.innerHTML = "";

  const summary = document.createElement("div");
  summary.className = "slide-result";
  summary.innerHTML = `
    <h3>Overall</h3>
    <p>${escapeHtml(data.overall_summary || "")}</p>
    <div class="section-label">Narrative arc</div>
    <p>${escapeHtml(data.narrative_arc_assessment || "")}</p>
  `;
  container.appendChild(summary);

  for (const slide of data.per_slide || []) {
    const div = document.createElement("div");
    div.className = "slide-result";

    const severityOrder = { high: 0, medium: 1, low: 2 };
    const sortedFlags = [...(slide.consistency_flags || [])].sort(
      (a, b) => (severityOrder[a.severity] ?? 3) - (severityOrder[b.severity] ?? 3)
    );

    const flagsHtml = sortedFlags
      .map(
        (f) => `<div class="flag ${f.severity || ""}">
          <strong>${escapeHtml(f.claim || "")}</strong> — ${escapeHtml(f.issue || "")}
          <div class="metrics-row"><span class="metric-chip">${escapeHtml(f.source || "")}</span>
          <span class="metric-chip severity-chip ${f.severity || ""}">${escapeHtml(f.severity || "")}</span></div>
        </div>`
      )
      .join("");

    div.innerHTML = `
      <h3>Slide ${slide.slide_index + 1}</h3>
      <div class="section-label">Consistency issues</div>
      ${flagsHtml || "<p class='status'>No consistency issues flagged.</p>"}
      <div class="section-label">Narrative</div>
      <p>${escapeHtml(slide.narrative_notes || "")}</p>
      <div class="section-label">Talk track</div>
      <p>${escapeHtml(slide.talk_track_notes || "")}</p>
      ${slide.delivery_notes ? `<div class="section-label">Delivery</div><p>${escapeHtml(slide.delivery_notes)}</p>` : ""}
    `;
    container.appendChild(div);
  }
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}
