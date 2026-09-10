const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

async function api(path, opts = {}) {
  // Relative to the current document so this keeps working when served
  // behind Home Assistant Ingress's dynamic per-session sub-path.
  const res = await fetch(path.replace(/^\//, ""), {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

// ---------- tabs ----------
$$(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab-btn").forEach((b) => b.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "history") loadHistory();
  });
});

// ---------- systems ----------
let platformCodes = [];
let systemsCache = [];

function coveragePct(sys) {
  if (!sys.rom_count) return 0;
  return Math.min(100, Math.round((sys.gamelist_entries / sys.rom_count) * 100));
}

function renderSystems() {
  const body = $("#systems-body");
  body.innerHTML = "";
  for (const sys of systemsCache) {
    const tr = document.createElement("tr");

    const selTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "sys-select";
    cb.dataset.folder = sys.folder;
    cb.checked = sys.enabled !== false;
    selTd.appendChild(cb);
    tr.appendChild(selTd);

    const folderTd = document.createElement("td");
    folderTd.textContent = sys.folder;
    tr.appendChild(folderTd);

    const platTd = document.createElement("td");
    const select = document.createElement("select");
    select.className = "platform-select" + (sys.platform ? "" : " unmapped");
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "(unmapped -- pick one)";
    select.appendChild(noneOpt);
    for (const code of platformCodes) {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = code;
      if (code === sys.platform) opt.selected = true;
      select.appendChild(opt);
    }
    select.addEventListener("change", async () => {
      await api("/api/systems/override", {
        method: "POST",
        body: JSON.stringify({ folder: sys.folder, platform: select.value || null, enabled: cb.checked }),
      });
      sys.platform = select.value || null;
      select.className = "platform-select" + (sys.platform ? "" : " unmapped");
    });
    platTd.appendChild(select);
    tr.appendChild(platTd);

    const romTd = document.createElement("td");
    romTd.textContent = sys.rom_count;
    tr.appendChild(romTd);

    const scrapedTd = document.createElement("td");
    scrapedTd.textContent = sys.gamelist_entries;
    tr.appendChild(scrapedTd);

    const covTd = document.createElement("td");
    const pct = coveragePct(sys);
    covTd.innerHTML = `<span class="coverage-bar"><div style="width:${pct}%"></div></span>${pct}%`;
    tr.appendChild(covTd);

    const actionTd = document.createElement("td");
    const fixBtn = document.createElement("button");
    fixBtn.textContent = "Rewrite gamelist";
    fixBtn.title = "Re-write gamelist.xml from the local cache (fixes EmulationStation overwriting it), no network calls.";
    fixBtn.addEventListener("click", async () => {
      fixBtn.disabled = true;
      try {
        await api(`/api/systems/${encodeURIComponent(sys.folder)}/rewrite-gamelist`, { method: "POST" });
        await loadSystems();
      } catch (e) {
        alert(e.message);
      } finally {
        fixBtn.disabled = false;
      }
    });
    actionTd.appendChild(fixBtn);
    tr.appendChild(actionTd);

    body.appendChild(tr);
  }
}

async function loadSystems() {
  const errEl = $("#systems-error");
  errEl.classList.add("hidden");
  try {
    if (platformCodes.length === 0) platformCodes = await api("/api/platforms");
    const data = await api("/api/systems");
    systemsCache = data.systems;
    renderSystems();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
}

$("#scan-btn").addEventListener("click", loadSystems);
$("#select-all").addEventListener("change", (e) => {
  $$(".sys-select").forEach((cb) => (cb.checked = e.target.checked));
});

function selectedSystems() {
  const selected = [];
  $$(".sys-select").forEach((cb) => {
    if (!cb.checked) return;
    const sys = systemsCache.find((s) => s.folder === cb.dataset.folder);
    if (sys && sys.platform) selected.push({ folder: sys.folder, platform: sys.platform });
  });
  return selected;
}

async function startJob(systems) {
  if (systems.length === 0) {
    alert("No systems with a resolved platform are selected.");
    return;
  }
  const only_missing = $("#only-missing").checked;
  const unpack = $("#unpack").checked;
  const { job_id } = await api("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ systems, only_missing, unpack }),
  });
  watchJob(job_id);
}

$("#scrape-selected-btn").addEventListener("click", () => startJob(selectedSystems()));
$("#scrape-all-btn").addEventListener("click", () => {
  const all = systemsCache
    .filter((s) => s.platform && s.enabled !== false)
    .map((s) => ({ folder: s.folder, platform: s.platform }));
  startJob(all);
});

function watchJob(jobId) {
  const panel = $("#job-panel");
  const logEl = $("#job-log");
  const progressEl = $("#job-progress");
  panel.classList.remove("hidden");
  $("#job-title").textContent = `Job #${jobId}`;
  logEl.textContent = "";
  progressEl.textContent = "starting...";

  // Resolve relative to the current document (not just location.host) so
  // this still lands on the right path behind Home Assistant Ingress.
  const wsUrl = new URL(`ws/jobs/${jobId}`, location.href);
  wsUrl.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(wsUrl.href);
  const perSystem = {};

  ws.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    if (event.type === "line") {
      logEl.textContent += event.text + "\n";
      logEl.scrollTop = logEl.scrollHeight;
    } else if (event.type === "progress") {
      perSystem[event.folder] = `${event.folder}: ${event.current}/${event.total}`;
      progressEl.textContent = Object.values(perSystem).join("  |  ");
    } else if (event.type === "system_done") {
      const s = event.summary;
      perSystem[event.folder] = `${event.folder}: done (found ${s.found}, missing ${s.not_found})`;
      progressEl.textContent = Object.values(perSystem).join("  |  ");
    } else if (event.type === "done") {
      progressEl.textContent += `  --  ${event.status}`;
      loadSystems();
    }
  };

  $("#cancel-job-btn").onclick = async () => {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  };
}

// ---------- settings ----------
const sourceTypeSel = $("#source-type");
function updateSourceFields() {
  $("#local-fields").classList.toggle("hidden", sourceTypeSel.value !== "local");
  $("#smb-fields").classList.toggle("hidden", sourceTypeSel.value !== "smb");
}
sourceTypeSel.addEventListener("change", updateSourceFields);

async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("#settings-form");
  for (const [key, value] of Object.entries(s)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = !!value;
    else if (key.endsWith("password")) field.placeholder = s[`${key}_is_set`] ? "(saved -- leave blank to keep)" : "";
    else field.value = value;
  }
  updateSourceFields();
}

function collectSettingsPatch() {
  const form = $("#settings-form");
  const patch = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") patch[el.name] = el.checked;
    else if (el.type === "password" && el.value === "") continue; // keep stored secret
    else patch[el.name] = el.value;
  }
  return patch;
}

async function saveSettings() {
  await api("/api/settings", { method: "POST", body: JSON.stringify(collectSettingsPatch()) });
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings();
  $("#settings-saved").textContent = "Saved.";
  setTimeout(() => ($("#settings-saved").textContent = ""), 2000);
});

$("#test-ss-btn").addEventListener("click", async () => {
  const el = $("#test-ss-result");
  el.textContent = "testing...";
  try {
    await saveSettings(); // so the test uses what's currently on screen, not stale saved values
    const r = await api("/api/settings/test-screenscraper", { method: "POST" });
    el.textContent = r.ok ? `OK (threads: ${r.threads ?? "?"})` : "Login failed -- check credentials.";
  } catch (e) {
    el.textContent = e.message;
  }
});

$("#test-smb-btn").addEventListener("click", async () => {
  const el = $("#test-smb-result");
  el.textContent = "testing...";
  try {
    await saveSettings(); // so the test uses what's currently on screen, not stale saved values
    const r = await api("/api/settings/test-smb", { method: "POST" });
    el.textContent = r.message;
  } catch (e) {
    el.textContent = e.message;
  }
});

// ---------- history ----------
async function loadHistory() {
  const jobs = await api("/api/jobs");
  const body = $("#history-body");
  body.innerHTML = "";
  for (const j of jobs) {
    const tr = document.createElement("tr");
    const started = new Date(j.started_at * 1000).toLocaleString();
    const systems = j.systems.map((s) => s.folder).join(", ");
    tr.innerHTML = `<td>#${j.id}</td><td>${started}</td><td>${j.status}</td><td>${systems}</td>`;
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => watchExistingJob(j.id));
    body.appendChild(tr);
  }
}

async function watchExistingJob(jobId) {
  const j = await api(`/api/jobs/${jobId}`);
  $$(".tab-btn").forEach((b) => b.classList.remove("active"));
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $('.tab-btn[data-tab="systems"]').classList.add("active");
  $("#tab-systems").classList.add("active");
  $("#job-panel").classList.remove("hidden");
  $("#job-title").textContent = `Job #${j.id} (${j.status})`;
  $("#job-log").textContent = j.log;
  $("#job-progress").textContent = j.summary ? JSON.stringify(j.summary) : "";
}

// ---------- init ----------
loadSystems();
loadSettings();
