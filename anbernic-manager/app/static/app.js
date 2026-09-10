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
function focusTab(tab) {
  $$(".tab-btn").forEach((b) => b.classList.remove("active"));
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $(`.tab-btn[data-tab="${tab}"]`).classList.add("active");
  $(`#tab-${tab}`).classList.add("active");
}

$$(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    focusTab(btn.dataset.tab);
    if (btn.dataset.tab === "history") loadHistory();
    if (btn.dataset.tab === "device" && deviceConfigEntries === null) loadDeviceConfig();
    if (btn.dataset.tab === "library") loadLibrary();
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

    if (sys.rom_count === 0) {
      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete empty folder";
      delBtn.className = "danger";
      delBtn.title = "Permanently deletes this folder from the share -- only shown because it has 0 ROMs.";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Permanently delete the empty folder "${sys.folder}" from the share? This cannot be undone.`)) return;
        delBtn.disabled = true;
        try {
          await api(`/api/systems/${encodeURIComponent(sys.folder)}`, { method: "DELETE" });
          await loadSystems();
        } catch (e) {
          showSystemsError(e.message);
          delBtn.disabled = false;
        }
      });
      actionTd.appendChild(delBtn);
    }

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

function showSystemsError(message) {
  const errEl = $("#systems-error");
  errEl.textContent = message;
  errEl.classList.remove("hidden");
}

async function startJob(systems) {
  if (systems.length === 0) {
    showSystemsError("No systems with a resolved platform are selected.");
    return;
  }
  $("#systems-error").classList.add("hidden");
  const only_missing = $("#only-missing").checked;
  const unpack = $("#unpack").checked;
  try {
    const { job_id } = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ systems, only_missing, unpack }),
    });
    watchJob(job_id);
  } catch (e) {
    showSystemsError(`Couldn't start job: ${e.message}`);
  }
}

$("#scrape-selected-btn").addEventListener("click", () => startJob(selectedSystems()));
$("#scrape-all-btn").addEventListener("click", () => {
  const all = systemsCache
    .filter((s) => s.platform && s.enabled !== false)
    .map((s) => ({ folder: s.folder, platform: s.platform }));
  startJob(all);
});

// ---------- job log verbosity ----------
// "quiet" lines are job/system boundaries and errors -- always shown.
// "normal" adds per-game found/not-found and summary lines.
// "verbose" is every raw Skyscraper line, unfiltered.
const LOG_LEVELS = { quiet: 0, normal: 1, verbose: 2 };
function lineLevel(text) {
  if (/^(===|---|!!!)/.test(text)) return "quiet";
  if (/found! :\)|not found|Successfully processed|Skipped games|requests remaining/i.test(text)) return "normal";
  return "verbose";
}

let jobLogLines = [];

function renderJobLog() {
  const logEl = $("#job-log");
  const verbosity = $("#log-verbosity").value;
  logEl.textContent = jobLogLines
    .filter((text) => LOG_LEVELS[lineLevel(text)] <= LOG_LEVELS[verbosity])
    .join("\n");
  logEl.scrollTop = logEl.scrollHeight;
}

$("#log-verbosity").addEventListener("change", renderJobLog);

function setJobStatus(text, cls) {
  const badge = $("#job-status-badge");
  badge.textContent = text;
  badge.className = "status-badge" + (cls ? ` status-${cls}` : "");
}

async function watchJob(jobId) {
  const panel = $("#job-panel");
  const progressEl = $("#job-progress");
  panel.classList.remove("hidden");
  $("#job-title").textContent = `Job #${jobId}`;
  jobLogLines = [];
  renderJobLog();
  progressEl.textContent = "starting...";
  setJobStatus("running", "running");

  try {
    // Reconnecting to an already-running (or just-finished) job: seed the
    // log from what's already recorded instead of starting blank.
    const existing = await api(`/api/jobs/${jobId}`);
    if (existing.log) {
      jobLogLines = existing.log.split("\n").filter((l) => l.length > 0);
      renderJobLog();
    }
    if (existing.status !== "running") {
      setJobStatus(existing.status, existing.status === "completed" ? "done" : existing.status);
      progressEl.textContent = existing.summary ? JSON.stringify(existing.summary) : "";
      return; // already finished -- no point opening a WebSocket for it
    }
  } catch (e) {
    // fall through to live updates only
  }

  // Resolve relative to the current document (not just location.host) so
  // this still lands on the right path behind Home Assistant Ingress.
  const wsUrl = new URL(`ws/jobs/${jobId}`, location.href);
  wsUrl.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(wsUrl.href);
  const perSystem = {};

  ws.onerror = () => showSystemsError("Lost the job log connection -- the job may still be running; check History.");

  ws.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    if (event.type === "line") {
      jobLogLines.push(event.text);
      renderJobLog();
    } else if (event.type === "progress") {
      perSystem[event.folder] = `${event.folder}: ${event.current}/${event.total}`;
      progressEl.textContent = Object.values(perSystem).join("  |  ");
    } else if (event.type === "system_done") {
      const s = event.summary;
      perSystem[event.folder] = `${event.folder}: done (found ${s.found}, missing ${s.not_found})`;
      progressEl.textContent = Object.values(perSystem).join("  |  ");
    } else if (event.type === "done") {
      progressEl.textContent += `  --  ${event.status}`;
      setJobStatus(event.status, event.status === "completed" ? "done" : event.status);
      loadSystems();
      if (libraryGames.length > 0) loadLibrary(); // refresh art/scraped-status if the browser's been opened
    }
  };

  $("#cancel-job-btn").onclick = async () => {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  };
}

// ---------- settings ----------
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

// ---------- library (ROM browser) ----------
let libraryGames = [];

function showLibraryError(message) {
  const el = $("#library-error");
  el.textContent = message;
  el.classList.remove("hidden");
}

function populateLibrarySystemPicker() {
  const sel = $("#library-system");
  const previous = sel.value;
  sel.innerHTML = "";
  for (const sys of systemsCache) {
    const opt = document.createElement("option");
    opt.value = sys.folder;
    opt.textContent = `${sys.folder} (${sys.rom_count})`;
    opt.dataset.platform = sys.platform || "";
    sel.appendChild(opt);
  }
  if (previous && [...sel.options].some((o) => o.value === previous)) sel.value = previous;
}

async function loadLibrary() {
  $("#library-error").classList.add("hidden");
  if (systemsCache.length === 0) await loadSystems();
  populateLibrarySystemPicker();
  const folder = $("#library-system").value;
  if (!folder) {
    libraryGames = [];
    renderLibrary();
    return;
  }
  try {
    const data = await api(`/api/systems/${encodeURIComponent(folder)}/games`);
    libraryGames = data.games;
  } catch (e) {
    libraryGames = [];
    showLibraryError(e.message);
  }
  renderLibrary();
}

function renderLibrary() {
  const grid = $("#library-grid");
  grid.innerHTML = "";
  const filter = $("#library-filter").value.trim().toLowerCase();
  const folder = $("#library-system").value;
  const platform = $("#library-system").selectedOptions[0]?.dataset.platform || "";

  for (const game of libraryGames) {
    const label = (game.name || game.filename).toLowerCase();
    if (filter && !label.includes(filter)) continue;

    const card = document.createElement("div");
    card.className = "game-card";

    const img = document.createElement("img");
    img.className = "game-thumb";
    const thumbPath = game.thumbnail || game.image;
    if (thumbPath) {
      img.src = `api/systems/${encodeURIComponent(folder)}/media?path=${encodeURIComponent(thumbPath)}`;
      img.alt = game.name || game.filename;
    } else {
      img.classList.add("game-thumb-empty");
      img.alt = "no art";
    }
    card.appendChild(img);

    const info = document.createElement("div");
    info.className = "game-info";
    const title = document.createElement("div");
    title.className = "game-title";
    title.textContent = game.name || game.filename;
    info.appendChild(title);
    const file = document.createElement("div");
    file.className = "game-file dim";
    file.textContent = game.filename;
    info.appendChild(file);
    if (!game.scraped) {
      const badge = document.createElement("span");
      badge.className = "status-badge";
      badge.textContent = "not scraped";
      info.appendChild(badge);
    }
    card.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "game-actions";

    const rescrapeBtn = document.createElement("button");
    rescrapeBtn.textContent = "Rescrape";
    rescrapeBtn.title = "Re-fetch metadata/art for just this game, ignoring 'only fetch missing'.";
    rescrapeBtn.addEventListener("click", async () => {
      if (!platform) {
        showLibraryError("This system has no platform mapped -- set one in the Systems tab first.");
        return;
      }
      rescrapeBtn.disabled = true;
      try {
        const { job_id } = await api("/api/jobs", {
          method: "POST",
          body: JSON.stringify({
            systems: [{ folder, platform, rom_filename: game.filename }],
            only_missing: false,
            unpack: true,
          }),
        });
        focusTab("systems"); // job progress panel lives there -- otherwise it'd start invisibly
        watchJob(job_id);
      } catch (e) {
        showLibraryError(e.message);
      } finally {
        rescrapeBtn.disabled = false;
      }
    });
    actions.appendChild(rescrapeBtn);

    const delBtn = document.createElement("button");
    delBtn.textContent = "Delete";
    delBtn.className = "danger";
    delBtn.title = "Permanently deletes this ROM and its scraped art.";
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Permanently delete "${game.filename}" (and its scraped art) from "${folder}"? This cannot be undone.`)) return;
      delBtn.disabled = true;
      try {
        await api(`/api/systems/${encodeURIComponent(folder)}/games/${encodeURIComponent(game.filename)}`, { method: "DELETE" });
        await loadLibrary();
      } catch (e) {
        showLibraryError(e.message);
        delBtn.disabled = false;
      }
    });
    actions.appendChild(delBtn);

    card.appendChild(actions);
    grid.appendChild(card);
  }
}

$("#library-system").addEventListener("change", loadLibrary);
$("#library-reload-btn").addEventListener("click", loadLibrary);
$("#library-filter").addEventListener("input", renderLibrary);

// ---------- device config (Knulli/batocera.conf) ----------
let deviceConfigEntries = null; // null = not loaded yet

function showDeviceError(message) {
  const el = $("#device-error");
  el.textContent = message;
  el.classList.remove("hidden");
}

async function loadDeviceConfig() {
  $("#device-error").classList.add("hidden");
  try {
    const data = await api("/api/device-config");
    deviceConfigEntries = data.entries;
    $("#device-path").textContent = data.path;
    renderDeviceConfig();
  } catch (e) {
    deviceConfigEntries = [];
    showDeviceError(e.message);
  }
}

function renderDeviceConfig() {
  const body = $("#device-body");
  body.innerHTML = "";
  const filter = $("#device-filter").value.trim().toLowerCase();
  for (const entry of deviceConfigEntries || []) {
    if (entry.type === "comment") {
      if (filter) continue; // comments aren't searchable, hide while filtering
      const tr = document.createElement("tr");
      tr.className = "device-comment";
      const td = document.createElement("td");
      td.colSpan = 3;
      td.textContent = entry.text;
      tr.appendChild(td);
      body.appendChild(tr);
      continue;
    }
    if (filter && !entry.key.toLowerCase().includes(filter)) continue;

    const tr = document.createElement("tr");
    if (!entry.enabled) tr.className = "device-disabled";

    const onTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = entry.enabled;
    cb.title = entry.enabled ? "Uncheck to disable this setting" : "Re-enable this setting";
    cb.addEventListener("change", async () => {
      cb.disabled = true;
      try {
        if (cb.checked) {
          await api("/api/device-config", {
            method: "PUT",
            body: JSON.stringify({ key: entry.key, value: entry.value, enabled: true }),
          });
        } else {
          await api(`/api/device-config/${encodeURIComponent(entry.key)}`, { method: "DELETE" });
        }
        entry.enabled = cb.checked;
        tr.className = entry.enabled ? "" : "device-disabled";
      } catch (e) {
        showDeviceError(e.message);
        cb.checked = !cb.checked;
      } finally {
        cb.disabled = false;
      }
    });
    onTd.appendChild(cb);
    tr.appendChild(onTd);

    const keyTd = document.createElement("td");
    keyTd.textContent = entry.key;
    keyTd.className = "device-key";
    tr.appendChild(keyTd);

    const valTd = document.createElement("td");
    const valInput = document.createElement("input");
    valInput.type = "text";
    valInput.value = entry.value;
    valInput.addEventListener("change", async () => {
      valInput.disabled = true;
      try {
        await api("/api/device-config", {
          method: "PUT",
          body: JSON.stringify({ key: entry.key, value: valInput.value, enabled: entry.enabled }),
        });
        entry.value = valInput.value;
      } catch (e) {
        showDeviceError(e.message);
        valInput.value = entry.value;
      } finally {
        valInput.disabled = false;
      }
    });
    valTd.appendChild(valInput);
    tr.appendChild(valTd);

    body.appendChild(tr);
  }
}

$("#device-reload-btn").addEventListener("click", loadDeviceConfig);
$("#device-filter").addEventListener("input", renderDeviceConfig);

$("#device-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const keyEl = $("#device-add-key");
  const valEl = $("#device-add-value");
  try {
    await api("/api/device-config", {
      method: "PUT",
      body: JSON.stringify({ key: keyEl.value.trim(), value: valEl.value, enabled: true }),
    });
    keyEl.value = "";
    valEl.value = "";
    await loadDeviceConfig();
  } catch (e) {
    showDeviceError(e.message);
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
  focusTab("systems");
  $("#job-panel").classList.remove("hidden");
  $("#job-title").textContent = `Job #${j.id} (${j.status})`;
  $("#job-log").textContent = j.log;
  $("#job-progress").textContent = j.summary ? JSON.stringify(j.summary) : "";
}

// ---------- init ----------
loadSystems();
loadSettings();

// Reconnect to an already-running job (e.g. after reopening the Ingress
// panel) instead of leaving no visible sign it's running.
api("/api/jobs/current").then(({ job_id }) => {
  if (job_id != null) watchJob(job_id);
});
