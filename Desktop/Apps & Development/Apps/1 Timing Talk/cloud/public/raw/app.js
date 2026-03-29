const state = {
  devices: [],
  deviceId: "",
  view: "runs",
  racerName: "",
  runsFilters: {
    name: "",
    category: "",
    round: "",
    class_index: "",
  },
};

const els = {
  deviceSearch: document.getElementById("deviceSearch"),
  deviceSelect: document.getElementById("deviceSelect"),
  deviceSummary: document.getElementById("deviceSummary"),
  viewTabs: document.getElementById("viewTabs"),
  runsView: document.getElementById("runsView"),
  timeslipView: document.getElementById("timeslipView"),
  scheduleView: document.getElementById("scheduleView"),
  noshowsView: document.getElementById("noshowsView"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function formatWhole(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.round(number)) : "-";
}

function formatTimestamp(value) {
  return value || "-";
}

function deviceLabel(device) {
  const main = device.trackName || device.name || device.deviceId;
  const extra = [device.promoter, device.raceName].filter(Boolean).join(" · ");
  return extra ? `${main} - ${extra}` : main;
}

function setUrlState() {
  const params = new URLSearchParams(window.location.search);
  if (state.deviceId) params.set("deviceId", state.deviceId);
  else params.delete("deviceId");
  if (state.view) params.set("view", state.view);
  if (state.racerName) params.set("racer", state.racerName);
  else params.delete("racer");
  history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
}

function getSelectedDevice() {
  return state.devices.find((device) => device.deviceId === state.deviceId) || null;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

function renderDeviceSummary(device) {
  if (!device) {
    els.deviceSummary.innerHTML = '<div class="raw-empty">Select a device to load parsed raw data.</div>';
    return;
  }

  const chips = [
    `<span class="raw-chip"><strong>Track</strong> ${escapeHtml(device.trackName || device.name || device.deviceId)}</span>`,
    device.promoter ? `<span class="raw-chip"><strong>Promoter</strong> ${escapeHtml(device.promoter)}</span>` : "",
    device.raceName ? `<span class="raw-chip"><strong>Race</strong> ${escapeHtml(device.raceName)}</span>` : "",
    device.trackLocation ? `<span class="raw-chip"><strong>Location</strong> ${escapeHtml(device.trackLocation)}</span>` : "",
    `<span class="raw-chip"><strong>Device</strong> ${escapeHtml(device.deviceId)}</span>`,
    `<span class="raw-chip"><strong>Status</strong> <span class="raw-badge ${device.online ? "live" : "offline"}">${device.online ? "LIVE" : "OFFLINE"}</span></span>`,
  ].filter(Boolean);

  els.deviceSummary.innerHTML = chips.join("");
}

async function loadDevices(search = "") {
  const data = await fetchJson(`/api/raw/devices${search ? `?q=${encodeURIComponent(search)}` : ""}`);
  state.devices = data.devices || [];

  els.deviceSelect.innerHTML = [
    '<option value="">Choose a track or promoter</option>',
    ...state.devices.map((device) => `<option value="${escapeHtml(device.deviceId)}">${escapeHtml(deviceLabel(device))}</option>`),
  ].join("");

  const preferredId =
    state.deviceId ||
    new URLSearchParams(window.location.search).get("deviceId") ||
    state.devices[0]?.deviceId ||
    "";

  const nextDeviceId = state.devices.some((device) => device.deviceId === preferredId)
    ? preferredId
    : (search ? "" : (state.devices[0]?.deviceId || ""));

  state.deviceId = nextDeviceId;
  els.deviceSelect.value = nextDeviceId;
  renderDeviceSummary(getSelectedDevice());
}

function setActiveView(view) {
  state.view = view;
  document.querySelectorAll(".raw-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
  els.runsView.classList.toggle("hidden", view !== "runs");
  els.timeslipView.classList.toggle("hidden", view !== "timeslip");
  els.scheduleView.classList.toggle("hidden", view !== "schedule");
  els.noshowsView.classList.toggle("hidden", view !== "noshows");
  setUrlState();
}

function renderLoading(target, message = "Loading...") {
  target.innerHTML = `<div class="raw-empty">${escapeHtml(message)}</div>`;
}

function renderRuns(response) {
  const { runs = [], total = 0, filters = {} } = response;

  const categoryOptions = ['<option value="">All categories</option>', ...(filters.categories || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join("");
  const roundOptions = ['<option value="">All rounds</option>', ...(filters.rounds || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join("");
  const classOptions = ['<option value="">All classes</option>', ...(filters.classes || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join("");

  els.runsView.innerHTML = `
    <div class="raw-stack">
      <div class="card">
        <div class="raw-section-title">
          <h2>Runs</h2>
          <div class="raw-meta">${total} rows from parsed timing data</div>
        </div>
        <div class="raw-filter-grid">
          <div>
            <label for="runNameFilter">Racer Name</label>
            <input id="runNameFilter" type="text" placeholder="Search racer" value="${escapeHtml(state.runsFilters.name)}">
          </div>
          <div>
            <label for="runCategoryFilter">Category</label>
            <select id="runCategoryFilter">${categoryOptions}</select>
          </div>
          <div>
            <label for="runRoundFilter">Round</label>
            <select id="runRoundFilter">${roundOptions}</select>
          </div>
          <div>
            <label for="runClassFilter">Class</label>
            <select id="runClassFilter">${classOptions}</select>
          </div>
          <button class="btn btn-primary" id="runFilterApply">Apply</button>
        </div>
      </div>

      <div class="raw-kpis">
        <div class="raw-kpi"><div class="raw-kpi-label">Run Rows</div><div class="raw-kpi-value">${total}</div></div>
        <div class="raw-kpi"><div class="raw-kpi-label">Categories</div><div class="raw-kpi-value">${(filters.categories || []).length}</div></div>
        <div class="raw-kpi"><div class="raw-kpi-label">Rounds</div><div class="raw-kpi-value">${(filters.rounds || []).length}</div></div>
        <div class="raw-kpi"><div class="raw-kpi-label">Classes</div><div class="raw-kpi-value">${(filters.classes || []).length}</div></div>
      </div>

      <div class="raw-table-card">
        <div class="table-scroll">
          <table class="table-spreadsheet">
            <thead>
              <tr>
                <th>Time</th>
                <th>Round</th>
                <th>Category</th>
                <th>Lane</th>
                <th>Racer</th>
                <th>Car #</th>
                <th>Dial</th>
                <th>RT</th>
                <th>60</th>
                <th>330</th>
                <th>660</th>
                <th>660 MPH</th>
                <th>1000</th>
                <th>1000 MPH</th>
                <th>ET</th>
                <th>MPH</th>
                <th>MOV</th>
                <th>Winner</th>
              </tr>
            </thead>
            <tbody>
              ${runs.length ? runs.map((run) => `
                <tr>
                  <td class="raw-mono">${escapeHtml(formatTimestamp(run.timestamp))}</td>
                  <td>${escapeHtml(run.round || "-")}</td>
                  <td>${escapeHtml(run.category || "-")}</td>
                  <td>${escapeHtml(run.lane || "-")}</td>
                  <td>${escapeHtml(run.name || "-")}</td>
                  <td>${escapeHtml(run.car_number || "-")}</td>
                  <td>${formatNumber(run.dial_in)}</td>
                  <td>${formatNumber(run.rt)}</td>
                  <td>${formatNumber(run.ft60)}</td>
                  <td>${formatNumber(run.ft330)}</td>
                  <td>${formatNumber(run.ft660)}</td>
                  <td>${formatWhole(run.mph_660)}</td>
                  <td>${formatNumber(run.ft1000)}</td>
                  <td>${formatWhole(run.mph_1000)}</td>
                  <td>${formatNumber(run.ft1320)}</td>
                  <td>${formatWhole(run.mph_1320)}</td>
                  <td>${formatNumber(run.mov)}</td>
                  <td>${run.is_winner ? '<span class="raw-badge winner">WIN</span>' : "-"}</td>
                </tr>
              `).join("") : '<tr><td colspan="18" class="raw-empty">No runs matched the selected filters.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;

  document.getElementById("runCategoryFilter").value = state.runsFilters.category;
  document.getElementById("runRoundFilter").value = state.runsFilters.round;
  document.getElementById("runClassFilter").value = state.runsFilters.class_index;

  document.getElementById("runFilterApply").addEventListener("click", async () => {
    state.runsFilters.name = document.getElementById("runNameFilter").value.trim();
    state.runsFilters.category = document.getElementById("runCategoryFilter").value;
    state.runsFilters.round = document.getElementById("runRoundFilter").value;
    state.runsFilters.class_index = document.getElementById("runClassFilter").value;
    await refreshActiveView();
  });
}

function renderTimeslipPair(runs) {
  if (!runs.length) {
    return '<div class="raw-empty">No timeslip data found yet for this device.</div>';
  }

  const grouped = new Map();
  runs.forEach((run) => {
    const group = grouped.get(run.pair_id) || [];
    group.push(run);
    grouped.set(run.pair_id, group);
  });

  return Array.from(grouped.values())
    .sort((left, right) => (right[0]?.timestamp_ms || 0) - (left[0]?.timestamp_ms || 0))
    .map((pair) => {
      const lanes = [...pair].sort((a, b) => String(a.lane).localeCompare(String(b.lane)));
      return `
        <div class="card">
          <div class="raw-section-title">
            <h3>${escapeHtml(pair[0]?.category || "Pair")} ${escapeHtml(pair[0]?.round || "")}</h3>
            <div class="raw-meta">${escapeHtml(pair[0]?.timestamp || "")}</div>
          </div>
          <div class="raw-timeslip-grid">
            ${lanes.map((run) => `
              <div class="raw-timeslip">
                <div class="raw-timeslip-header">
                  <div>
                    <div class="raw-timeslip-title">${escapeHtml(run.name || "Unknown Racer")}</div>
                    <div>${escapeHtml(run.car_number || "No car number")} · Lane ${escapeHtml(run.lane || "-")}</div>
                  </div>
                  <div>${run.is_winner ? '<span class="raw-badge winner">WINNER</span>' : ''}</div>
                </div>
                <table>
                  <tbody>
                    <tr><th>Dial</th><td>${formatNumber(run.dial_in)}</td></tr>
                    <tr><th>Reaction</th><td>${formatNumber(run.rt)}</td></tr>
                    <tr><th>60 FT</th><td>${formatNumber(run.ft60)}</td></tr>
                    <tr><th>330 FT</th><td>${formatNumber(run.ft330)}</td></tr>
                    <tr><th>660 FT</th><td>${formatNumber(run.ft660)}</td></tr>
                    <tr><th>660 MPH</th><td>${formatWhole(run.mph_660)}</td></tr>
                    <tr><th>1000 FT</th><td>${formatNumber(run.ft1000)}</td></tr>
                    <tr><th>1000 MPH</th><td>${formatWhole(run.mph_1000)}</td></tr>
                    <tr><th>ET</th><td>${formatNumber(run.ft1320)}</td></tr>
                    <tr><th>MPH</th><td>${formatWhole(run.mph_1320)}</td></tr>
                    <tr><th>MOV</th><td>${formatNumber(run.mov)}</td></tr>
                  </tbody>
                </table>
              </div>
            `).join("")}
          </div>
        </div>
      `;
    })
    .join("");
}

function renderTimeslips(response, searchResults = []) {
  const runs = response?.runs || response?.pair || [];
  const title = response?.name ? `Timeslips For ${response.name}` : "Latest Pair";

  els.timeslipView.innerHTML = `
    <div class="raw-stack">
      <div class="card">
        <div class="raw-section-title">
          <h2>Timeslips</h2>
          <div class="raw-meta">${escapeHtml(title)}</div>
        </div>
        <div class="raw-search-row">
          <div>
            <label for="racerSearchInput">Racer Name</label>
            <input id="racerSearchInput" type="text" list="racerSuggestions" placeholder="Search racer name or car number" value="${escapeHtml(state.racerName)}">
            <datalist id="racerSuggestions">
              ${searchResults.map((racer) => `<option value="${escapeHtml(racer.name)}">${escapeHtml(racer.car_number || "")}</option>`).join("")}
            </datalist>
          </div>
          <button class="btn btn-primary" id="racerSearchButton">Load Racer</button>
          <button class="btn btn-outline" id="latestPairButton">Latest Pair</button>
        </div>
      </div>
      ${renderTimeslipPair(runs)}
    </div>
  `;

  const racerSearchInput = document.getElementById("racerSearchInput");
  racerSearchInput.addEventListener("input", debounce(loadRacerSuggestions, 250));
  document.getElementById("racerSearchButton").addEventListener("click", async () => {
    state.racerName = racerSearchInput.value.trim();
    setUrlState();
    await refreshActiveView();
  });
  document.getElementById("latestPairButton").addEventListener("click", async () => {
    state.racerName = "";
    setUrlState();
    await refreshActiveView();
  });
}

function renderSchedule(response) {
  const schedule = response.schedule || [];
  els.scheduleView.innerHTML = `
    <div class="raw-stack">
      <div class="card">
        <div class="raw-section-title">
          <h2>Schedule</h2>
          <div class="raw-meta">${schedule.length} session blocks</div>
        </div>
        <p>Blocks are split when there is a 10 minute gap between pairs in the same class and round.</p>
      </div>
      <div class="raw-schedule-list">
        ${schedule.length ? schedule.map((entry) => `
          <div class="raw-schedule-item">
            <div class="raw-schedule-item-top">
              <div>
                <div class="raw-schedule-title">${escapeHtml(entry.category)} · ${escapeHtml(entry.round)}</div>
                <div class="raw-schedule-subtitle">${escapeHtml(entry.firstTimestamp)} to ${escapeHtml(entry.lastTimestamp)}</div>
              </div>
              <div class="raw-inline-stats">
                <span>${entry.pairCount} pairs</span>
                <span>${entry.totalRuns} lane rows</span>
                <span>${entry.durationMinutes} min</span>
              </div>
            </div>
          </div>
        `).join("") : '<div class="raw-empty">No schedule data was derived from parsed raw runs.</div>'}
      </div>
    </div>
  `;
}

function renderNoShows(noShowsResponse, didNotRaceResponse) {
  const noShows = noShowsResponse.noShows || [];
  const didNotRace = didNotRaceResponse.didNotRace || [];

  els.noshowsView.innerHTML = `
    <div class="raw-stack">
      <div class="raw-grid-2">
        <div class="card">
          <div class="raw-section-title">
            <h2>No Shows</h2>
            <div class="raw-meta">${noShows.length} detected</div>
          </div>
          <div class="raw-table-card">
            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Racer</th>
                    <th>Car #</th>
                    <th>Won</th>
                    <th>Missed</th>
                  </tr>
                </thead>
                <tbody>
                  ${noShows.length ? noShows.map((row) => `
                    <tr>
                      <td>${escapeHtml(row.category)}</td>
                      <td>${escapeHtml(row.name)}</td>
                      <td>${escapeHtml(row.car_number)}</td>
                      <td>${escapeHtml(row.wonRound)}</td>
                      <td>${escapeHtml(row.missedRound)}</td>
                    </tr>
                  `).join("") : '<tr><td colspan="5" class="raw-empty">No no-shows detected from elimination data.</td></tr>'}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="raw-section-title">
            <h2>Did Not Race</h2>
            <div class="raw-meta">${didNotRace.length} racers</div>
          </div>
          <div class="raw-table-card">
            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Racer</th>
                    <th>Car #</th>
                    <th>Last Round</th>
                  </tr>
                </thead>
                <tbody>
                  ${didNotRace.length ? didNotRace.map((row) => `
                    <tr>
                      <td>${escapeHtml(row.category)}</td>
                      <td>${escapeHtml(row.name)}</td>
                      <td>${escapeHtml(row.car_number)}</td>
                      <td>${escapeHtml(row.lastRound)}</td>
                    </tr>
                  `).join("") : '<tr><td colspan="4" class="raw-empty">No qualifier-only racers were found.</td></tr>'}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

async function loadRunsView() {
  if (!state.deviceId) {
    renderDeviceSummary(null);
    renderLoading(els.runsView, "Choose a device to browse runs.");
    return;
  }

  renderLoading(els.runsView, "Loading runs...");
  const params = new URLSearchParams({
    deviceId: state.deviceId,
    limit: "200",
    sort_by: "timestamp",
    sort_dir: "DESC",
  });
  Object.entries(state.runsFilters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });

  const response = await fetchJson(`/api/raw/runs?${params.toString()}`);
  renderDeviceSummary(response.device || getSelectedDevice());
  renderRuns(response);
}

async function loadRacerSuggestions() {
  const input = document.getElementById("racerSearchInput");
  const datalist = document.getElementById("racerSuggestions");
  if (!input || !state.deviceId) return;
  if (input.value.trim().length < 2) {
    if (datalist) datalist.innerHTML = "";
    return;
  }

  const response = await fetchJson(`/api/raw/racers?deviceId=${encodeURIComponent(state.deviceId)}&search=${encodeURIComponent(input.value.trim())}`);
  if (datalist) {
    datalist.innerHTML = (response.racers || [])
      .map((racer) => `<option value="${escapeHtml(racer.name)}">${escapeHtml(racer.car_number || "")}</option>`)
      .join("");
  }
}

async function loadTimeslipView() {
  if (!state.deviceId) {
    renderLoading(els.timeslipView, "Choose a device to browse timeslips.");
    return;
  }

  renderLoading(els.timeslipView, "Loading timeslips...");
  const [response, searchResults] = await Promise.all([
    state.racerName
      ? fetchJson(`/api/raw/racer?deviceId=${encodeURIComponent(state.deviceId)}&name=${encodeURIComponent(state.racerName)}`)
      : fetchJson(`/api/raw/latest-pair?deviceId=${encodeURIComponent(state.deviceId)}`),
    state.racerName
      ? fetchJson(`/api/raw/racers?deviceId=${encodeURIComponent(state.deviceId)}&search=${encodeURIComponent(state.racerName)}`)
      : Promise.resolve({ racers: [] }),
  ]);
  renderDeviceSummary(response.device || getSelectedDevice());
  renderTimeslips(response, searchResults.racers || []);
}

async function loadScheduleView() {
  if (!state.deviceId) {
    renderLoading(els.scheduleView, "Choose a device to browse schedule data.");
    return;
  }

  renderLoading(els.scheduleView, "Loading schedule...");
  const response = await fetchJson(`/api/raw/schedule?deviceId=${encodeURIComponent(state.deviceId)}`);
  renderDeviceSummary(response.device || getSelectedDevice());
  renderSchedule(response);
}

async function loadNoShowsView() {
  if (!state.deviceId) {
    renderLoading(els.noshowsView, "Choose a device to browse no-show data.");
    return;
  }

  renderLoading(els.noshowsView, "Loading no-show data...");
  const [noShows, didNotRace] = await Promise.all([
    fetchJson(`/api/raw/noshows?deviceId=${encodeURIComponent(state.deviceId)}`),
    fetchJson(`/api/raw/didnotrace?deviceId=${encodeURIComponent(state.deviceId)}`),
  ]);
  renderDeviceSummary(noShows.device || didNotRace.device || getSelectedDevice());
  renderNoShows(noShows, didNotRace);
}

async function refreshActiveView() {
  try {
    if (state.view === "runs") return await loadRunsView();
    if (state.view === "timeslip") return await loadTimeslipView();
    if (state.view === "schedule") return await loadScheduleView();
    return await loadNoShowsView();
  } catch (error) {
    const target =
      state.view === "runs" ? els.runsView :
      state.view === "timeslip" ? els.timeslipView :
      state.view === "schedule" ? els.scheduleView :
      els.noshowsView;
    target.innerHTML = `<div class="alert alert-error">${escapeHtml(error.message || "Something went wrong loading data.")}</div>`;
  }
}

function debounce(fn, waitMs) {
  let timeout = null;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), waitMs);
  };
}

async function init() {
  const params = new URLSearchParams(window.location.search);
  state.view = params.get("view") || "runs";
  state.deviceId = params.get("deviceId") || "";
  state.racerName = params.get("racer") || "";

  setActiveView(state.view);

  els.deviceSearch.addEventListener("input", debounce(async () => {
    await loadDevices(els.deviceSearch.value.trim());
    if (!state.deviceId) {
      await refreshActiveView();
    }
  }, 250));

  els.deviceSelect.addEventListener("change", async (event) => {
    state.deviceId = event.target.value;
    state.racerName = "";
    renderDeviceSummary(getSelectedDevice());
    setUrlState();
    await refreshActiveView();
  });

  els.viewTabs.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    setActiveView(button.dataset.view);
    await refreshActiveView();
  });

  await loadDevices();
  await refreshActiveView();
}

init();
