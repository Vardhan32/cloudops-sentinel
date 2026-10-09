/* CloudOps Sentinel dashboard: plain JS, no dependencies. */
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;

function fmtTime(ts) {
  try { return new Date(ts).toLocaleTimeString(); } catch { return "–"; }
}

function setBar(barId, valId, pct, warn, crit) {
  const bar = $(barId), val = $(valId);
  const v = Number(pct) || 0;
  bar.style.width = Math.min(100, Math.max(0, v)) + "%";
  bar.style.background = v >= crit ? "var(--crit)" : v >= warn ? "var(--warn)" : "var(--ok)";
  val.textContent = v.toFixed(1) + "%";
}

function drawSpark(points) {
  const c = $("spark"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!points.length) return;
  const max = Math.max(100, ...points);
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = (i / Math.max(1, points.length - 1)) * (c.width - 8) + 4;
    const y = c.height - 6 - (p / max) * (c.height - 16);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#4aa8ff"; ctx.lineWidth = 2; ctx.stroke();
}

async function api(path, opts) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

async function loadSummary() {
  const badge = $("healthBadge"), state = $("summaryState");
  try {
    const [summary, current] = await Promise.all([
      api("/api/metrics/summary"), api("/api/metrics/current"),
    ]);
    badge.className = "badge " + summary.status;
    badge.textContent = summary.status.toUpperCase() +
      ` · ${summary.active_total} active (${summary.active_critical} crit)`;
    setBar("cpuBar", "cpuVal", current.cpu_percent, 75, 90);
    setBar("memBar", "memVal", current.memory_percent, 80, 90);
    setBar("diskBar", "diskVal", current.disk_percent, 80, 90);
    const up = Math.floor(current.uptime_seconds || 0);
    $("uptimeVal").textContent = `${Math.floor(up / 3600)}h ${Math.floor((up % 3600) / 60)}m`;
    $("sourceVal").textContent = current.source;
    $("updatedVal").textContent = fmtTime(current.timestamp);
    state.textContent = "";
    state.className = "state hidden";
  } catch (e) {
    badge.className = "badge error"; badge.textContent = "UNREACHABLE";
    state.className = "state error"; state.textContent = "Backend error: " + e.message;
  }
}

async function loadHistory() {
  const state = $("historyState");
  try {
    const rows = await api("/api/metrics/history?limit=20");
    if (!rows.length) {
      state.textContent = "No metrics recorded yet.";
      state.className = "state";
      return;
    }
    state.className = "state hidden";
    $("historyTable").classList.remove("hidden");
    $("historyBody").innerHTML = rows.slice(-10).reverse().map((r) =>
      `<tr><td>${fmtTime(r.timestamp)}</td><td>${r.cpu_percent.toFixed(1)}</td>` +
      `<td>${r.memory_percent.toFixed(1)}</td><td>${r.disk_percent.toFixed(1)}</td>` +
      `<td>${r.source}</td></tr>`).join("");
    drawSpark(rows.map((r) => r.cpu_percent));
  } catch (e) {
    state.className = "state error"; state.textContent = "History error: " + e.message;
  }
}

async function loadIncidents() {
  const state = $("incidentState"), list = $("incidentList");
  const filter = $("incidentFilter").value;
  try {
    const rows = await api("/api/incidents?limit=100" + (filter ? `&status=${filter}` : ""));
    const active = rows.filter((r) => r.status !== "resolved").length;
    $("incidentCount").textContent = active;
    if (!rows.length) {
      state.textContent = "No incidents — system looks healthy.";
      state.className = "state"; list.innerHTML = ""; return;
    }
    state.className = "state hidden";
    list.innerHTML = rows.map((i) =>
      `<li><div><span class="sev ${i.severity}">${i.severity}</span>` +
      `<span class="title">${escapeHtml(i.title)}</span>` +
      (i.source === "simulated" ? `<span class="src">simulated</span>` : "") + "</div>" +
      `<div class="desc">${escapeHtml(i.description || "")}</div>` +
      `<div class="status">#${i.id} · ${i.type} · ${i.status}` +
      (i.observed_value != null ? ` · value ${i.observed_value}` : "") +
      (i.threshold != null ? ` vs threshold ${i.threshold}` : "") +
      ` · ${fmtTime(i.created_at)}</div>` +
      `<div class="actions">` +
      (i.status === "open" ? `<button data-ack="${i.id}">Acknowledge</button>` : "") +
      (i.status !== "resolved" ? `<button data-res="${i.id}" class="ok">Resolve</button>` : "") +
      `</div></li>`).join("");
    list.querySelectorAll("[data-ack]").forEach((b) =>
      b.addEventListener("click", () => mutateIncident(b.dataset.ack, "acknowledge", b)));
    list.querySelectorAll("[data-res]").forEach((b) =>
      b.addEventListener("click", () => mutateIncident(b.dataset.res, "resolve", b)));
  } catch (e) {
    state.className = "state error"; state.textContent = "Incidents error: " + e.message;
  }
}

async function mutateIncident(id, action, btn) {
  btn.disabled = true;
  try { await api(`/api/incidents/${id}/${action}`, { method: "POST" }); }
  catch (e) { alert("Failed: " + e.message); }
  finally { await loadIncidents(); await loadSummary(); }
}

async function loadServices() {
  const state = $("serviceState"), list = $("serviceList");
  try {
    const rows = await api("/api/services");
    if (!rows.length) {
      state.textContent = "No services configured yet — add one below.";
      state.className = "state"; list.innerHTML = ""; return;
    }
    state.className = "state hidden";
    list.innerHTML = rows.map((s) => {
      const l = s.latest;
      const dot = !l ? "unknown" : l.healthy ? "up" : "down";
      const detail = !l ? "never checked" :
        `${l.healthy ? "healthy" : "UNHEALTHY"} · ${l.status_code ?? "no response"}` +
        (l.response_time_ms != null ? ` · ${l.response_time_ms} ms` : "") +
        (l.error ? ` · ${escapeHtml(l.error)}` : "");
      return `<li><div><span class="dot ${dot}"></span><strong>${escapeHtml(s.target.name)}</strong></div>` +
        `<div class="desc">${escapeHtml(s.target.url)}<br/>${detail}</div>` +
        `<div class="actions"><button data-check="${s.target.id}">Check now</button>` +
        `<button data-del="${s.target.id}" class="danger">Remove</button></div></li>`;
    }).join("");
    list.querySelectorAll("[data-check]").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await api(`/api/services/${b.dataset.check}/check`, { method: "POST" }); }
        catch (e) { alert("Check failed: " + e.message); }
        finally { await loadServices(); await loadIncidents(); await loadSummary(); }
      }));
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Remove this service target?")) return;
        await api(`/api/services/${b.dataset.del}`, { method: "DELETE" });
        await loadServices();
      }));
  } catch (e) {
    state.className = "state error"; state.textContent = "Services error: " + e.message;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("addService").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/services", { method: "POST",
      body: JSON.stringify({ name: $("svcName").value, url: $("svcUrl").value }) });
    $("svcName").value = ""; $("svcUrl").value = "";
    await loadServices();
  } catch (err) { alert("Could not add service: " + err.message); }
});

$("refreshIncidents").addEventListener("click", loadIncidents);
$("incidentFilter").addEventListener("change", loadIncidents);

document.querySelectorAll("[data-sim]").forEach((b) =>
  b.addEventListener("click", async () => {
    $("simState").textContent = "Running scenario…";
    try {
      const res = await api("/api/simulate", { method: "POST",
        body: JSON.stringify({ scenario: b.dataset.sim }) });
      $("simState").textContent = "Done: " + JSON.stringify(res);
    } catch (e) { $("simState").textContent = "Failed: " + e.message; }
    await loadIncidents(); await loadHistory(); await loadSummary();
  }));

async function refreshAll() { await loadSummary(); await loadHistory(); await loadIncidents(); await loadServices(); }
refreshAll();
setInterval(async () => { await loadSummary(); await loadHistory(); }, REFRESH_MS);
