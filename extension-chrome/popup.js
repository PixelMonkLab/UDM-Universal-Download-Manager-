/* UDM add-on – panel (works as toolbar popup, docked side panel, and full-page download manager) */
const api = VG.api;
const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const MODE = params.get("full") ? "full" : params.get("panel") ? "panel" : "popup";
const IS_FIREFOX = typeof browser !== "undefined" && /Firefox\//.test(navigator.userAgent);
let settings = Object.assign({}, VG.DEFAULTS);
let info = null, currentTab = null, foundKey = "", online = false, filter = "all";

document.body.classList.add(MODE);
function show(id, on) { $(id).classList.toggle("hidden", !on); }
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function fmtSize(b) { if (!b) return ""; const u = ["B", "KB", "MB", "GB"]; let i = 0; while (b >= 1024 && i < 3) { b /= 1024; i++; } return b.toFixed(i > 1 ? 1 : 0) + " " + u[i]; }
function shortName(u) { try { const x = new URL(u); return decodeURIComponent(x.pathname.split("/").filter(Boolean).slice(-2).join("/")) || x.hostname; } catch (e) { return u; } }
function fmtDur(s) { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); }

const manifest = api.runtime.getManifest();
$("ver").textContent = "v" + VG.VERSION_LABEL;
$("ver2").textContent = VG.VERSION_LABEL;

/* ---------------- tabs ---------------- */
function openTab(t) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  ["dl", "mgr", "set"].forEach(x => show("tab-" + x, x === t));
  if (t === "mgr") refreshJobs();
  if (t === "set") paintSettings();
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => openTab(b.dataset.tab));

/* ---------------- header buttons ---------------- */
$("fullBtn").onclick = async () => { await VG.send({ type: "openManager" }).catch(() => {}); if (MODE === "popup") window.close(); };
$("dockBtn").onclick = () => dockNow();
if (MODE === "full") $("fullBtn").classList.add("hidden");
if (MODE === "panel") { $("dockBtn").textContent = "✕"; $("dockBtn").title = "Close the side panel"; $("dockBtn").onclick = () => window.close(); }
const canDock = !!(api.sidePanel && api.sidePanel.open) || !!(IS_FIREFOX && api.sidebarAction);
if (!canDock && MODE !== "panel") $("dockBtn").classList.add("hidden");

async function dockNow() {
  try {
    if (api.sidePanel && api.sidePanel.open) {
      const w = await api.windows.getCurrent();
      await api.sidePanel.open({ windowId: w.id });
    } else if (api.sidebarAction) {
      await api.sidebarAction.open();
    }
    if (MODE === "popup") window.close();
  } catch (e) { alert("Couldn't open the side panel: " + e.message); }
}

/* ---------------- start ---------------- */
async function hasHostAccess() {
  if (!api.permissions || !api.permissions.contains) return true;
  try { return await api.permissions.contains({ origins: ["http://127.0.0.1:5757/*"] }); } catch (e) { return true; }
}

async function init() {
  settings = await VG.getSettings();
  applySettingsToUI();
  if (MODE === "full") openTab("mgr");
  if (!(await hasHostAccess())) { $("status").textContent = "needs access"; show("perm", true); show("tab-dl", false); return; }
  await checkApp();
  loadTab();
  refreshJobs();
  setInterval(refreshJobs, 1000);
  setInterval(checkApp, 5000);
  if (MODE === "panel") {           // the side panel stays open: follow the tab you're looking at
    api.tabs.onActivated.addListener(() => setTimeout(loadTab, 150));
    api.tabs.onUpdated.addListener((id, ch, tab) => { if (tab.active && (ch.url || ch.status === "complete")) loadTab(); });
  }
  setInterval(refreshFound, 1500);
}

async function checkApp() {
  try {
    const p = await VG.send({ type: "ping" });
    online = true;
    $("status").textContent = "● app running"; $("status").className = "status on";
    $("appInfo").textContent = `App ${p.version_label || p.version} · engine ${p.engine || "?"} · saving to ${p.folder}`;
    show("offline", false);
  } catch (e) {
    online = false;
    $("status").textContent = "● app not running"; $("status").className = "status off";
    show("offline", true);
  }
}

async function loadTab() {
  try {
    const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab || (currentTab && tab.id === currentTab.id && tab.url === currentTab.url)) return;
    currentTab = tab; foundKey = ""; info = null;
    show("preview", false); show("perr", false); show("pageEmpty", true);
    $("pageEmpty").textContent = "Open a YouTube, Instagram or Pinterest video, or paste a link:";
    if (/^https?:/.test(tab.url || "") && !tab.url.startsWith(VG.BASE)) {
      $("url").value = VG.cleanUrl(tab.url);
      if (VG.siteOf(tab.url)) { show("pageEmpty", false); if (online) fetchInfo(); }
      else $("pageEmpty").textContent = "Not a YouTube/Instagram/Pinterest video page – many other sites still work, try Fetch.";
    } else $("url").value = "";
    refreshFound();
  } catch (e) {}
}

/* ---------------- download tab ---------------- */
function applySettingsToUI() {
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.v === settings.mode));
  show("qWrap", settings.mode === "video"); show("aWrap", settings.mode === "audio");
  $("quality").value = settings.quality; if (!$("quality").value) $("quality").value = "best";
  $("afmt").value = settings.audio_format;
  $("cookies").value = settings.cookies;
  $("compat").checked = settings.mac_compat;
  $("all").checked = settings.all_items;
}
async function save(patch) { settings = await VG.saveSettings(patch); applySettingsToUI(); paintSettings(); }

document.querySelectorAll("#mode button").forEach(b => b.onclick = () => save({ mode: b.dataset.v }));
$("quality").onchange = () => save({ quality: $("quality").value });
$("afmt").onchange = () => save({ audio_format: $("afmt").value });
$("cookies").onchange = () => save({ cookies: $("cookies").value });
$("compat").onchange = () => save({ mac_compat: $("compat").checked });
$("all").onchange = () => save({ all_items: $("all").checked });
$("retry").onclick = () => location.reload();
$("grant").onclick = async () => {
  try { await api.permissions.request({ origins: ["http://127.0.0.1:5757/*", "http://localhost:5757/*", "<all_urls>"] }); } catch (e) {}
  location.reload();
};
$("fetch").onclick = fetchInfo;
$("url").addEventListener("keydown", e => { if (e.key === "Enter") fetchInfo(); });
$("more").onclick = () => VG.send({ type: "openApp", url: $("url").value.trim() }).catch(() => {});

async function fetchInfo() {
  const url = $("url").value.trim();
  if (!url) return;
  show("perr", false); show("preview", false);
  $("fetch").disabled = true; $("fetch").textContent = "…";
  try {
    info = await VG.send({ type: "info", url });
    $("thumb").src = info.thumbnail || ""; $("thumb").classList.toggle("hidden", !info.thumbnail);
    $("title").textContent = info.title || "Untitled";
    $("meta").textContent = [info.platform, info.uploader, info.duration ? fmtDur(info.duration) : "", info.items > 1 ? info.items + " items" : ""].filter(Boolean).join(" · ");
    show("preview", true); show("pageEmpty", false);
    if (info.heights && info.heights.length) {
      const q = $("quality"), keep = settings.quality;
      q.textContent = "";
      q.append(new Option(`Best (${info.heights[0]}p)`, "best"));
      info.heights.forEach(h => q.append(new Option(h + "p", String(h))));
      q.value = keep; if (!q.value) q.value = "best";
    }
  } catch (e) { $("perr").textContent = e.message; show("perr", true); }
  $("fetch").disabled = false; $("fetch").textContent = "Fetch";
}

$("dl").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) { $("perr").textContent = "Paste a link first."; show("perr", true); return; }
  $("dl").disabled = true;
  try {
    await VG.send({ type: "download", url, source: "popup", opts: { title: info && info.title || "", thumbnail: info && info.thumbnail || "" } });
    openTab("mgr");
  } catch (e) { $("perr").textContent = e.message; show("perr", true); }
  setTimeout(() => $("dl").disabled = false, 800);
};

async function refreshFound() {
  if (!currentTab) return;
  let r; try { r = await VG.send({ type: "media", tabId: currentTab.id }); } catch (e) { return; }
  const list = r.media || [];
  const key = list.map(x => x.url).join("|");
  if (key === foundKey) return; foundKey = key;
  show("found", list.length > 0);
  $("foundN").textContent = list.length ? list.length + " found" : "";
  const box = $("foundList"); box.textContent = "";
  for (const m of list) {
    const row = el("div", "media");
    const top = el("div", "mt");
    top.append(el("span", "kind" + (m.url === r.best ? " best" : ""), (m.url === r.best ? "★ " : "") + m.kind));
    const nm = el("span", "nm", shortName(m.url)); nm.title = m.url; top.append(nm);
    if (m.size) top.append(el("span", "muted", fmtSize(m.size)));
    const acts = el("div", "ma");
    const d = el("button", "sm", "⬇ Download");
    d.onclick = async () => {
      d.disabled = true;
      try {
        await VG.send({ type: "downloadMedia", tabId: currentTab.id, url: m.url, page: m.page, title: m.title,
                        opts: { title: m.title || currentTab.title } });
        openTab("mgr");
      } catch (e) { $("perr").textContent = e.message; show("perr", true); }
      setTimeout(() => d.disabled = false, 800);
    };
    const t = el("button", "link", "✂ Trim / more options");
    t.onclick = () => VG.send({ type: "openApp", url: m.url, referer: m.page, name: currentTab.title || "" }).catch(() => {});
    acts.append(d, t);
    row.append(top, acts); box.append(row);
  }
}

/* ---------------- download manager tab ---------------- */
document.querySelectorAll("#filters button").forEach(b => b.onclick = () => { filter = b.dataset.f;
  document.querySelectorAll("#filters button").forEach(x => x.classList.toggle("on", x === b)); refreshJobs(); });
$("search").addEventListener("input", () => refreshJobs());
$("clear").onclick = async () => { await VG.send({ type: "clearJobs" }).catch(() => {}); refreshJobs(); };
const ACTIVE = j => !["done", "error", "cancelled", "paused"].includes(j.status);

async function refreshJobs() {
  let jobs = [];
  try { jobs = (await VG.send({ type: "appJobs" })).jobs || []; } catch (e) { return; }
  const act = jobs.filter(ACTIVE).length;
  $("activeN").textContent = act; show("activeN", act > 0);
  $("jobCount").textContent = act ? `${act} active · ${jobs.length} total` : (jobs.length ? `${jobs.length} total` : "");
  if ($("tab-mgr").classList.contains("hidden")) return;
  const q = $("search").value.trim().toLowerCase();
  const shown = jobs.filter(j => filter === "all" || (filter === "active" && ACTIVE(j)) || (filter === "done" && j.status === "done") || (filter === "failed" && ["error", "cancelled"].includes(j.status)))
    .filter(j => !q || [j.title, j.filename, j.url].join(" ").toLowerCase().includes(q));
  const box = $("jobs"); box.textContent = "";
  if (!shown.length) { box.append(el("div", "muted", jobs.length ? "No downloads match." : "Nothing yet. Downloads from the app, this add-on and your phone all show up here.")); return; }
  for (const j of shown.slice(0, MODE === "full" ? 200 : 40)) {
    const row = el("div", "job " + j.status);
    const th = el("img", "th"); th.alt = "";
    if (j.thumbnail) { th.src = j.thumbnail; th.onerror = () => th.remove(); }
    const b = el("div", "jb");
    const st = { done: "✓ Done", error: "Failed", cancelled: "Cancelled", cancelling: "Cancelling…", converting: "Converting…",
      paused: "⏸ Paused", pausing: "Pausing…", connecting: "Connecting…",
      processing: "Finishing…", queued: "Waiting…", starting: "Starting…" }[j.status] || (j.indeterminate ? "Downloading…" : Math.round(j.percent) + "%");
    const label = j.status === "done" ? (j.filename || j.title) : (j.title || j.filename);
    const top = el("div", "jt"); const nm = el("span", "nm", label || j.url); nm.title = j.filename || j.url;
    top.append(nm, el("span", "muted", st)); b.append(top);
    const bar = el("div", "bar" + ((j.indeterminate && j.status === "downloading") || ["converting", "processing", "starting"].includes(j.status) ? " ind" : ""));
    const f = el("div"); f.style.width = (["done", "error", "cancelled"].includes(j.status) ? 100 : (j.percent || 2)) + "%"; bar.append(f); b.append(bar);
    const acts = el("div", "acts");
    const meta = [j.trim ? "✂ " + j.trim : "", fmtSize(j.size), j.status === "downloading" ? [j.speed, j.eta && "ETA " + j.eta].filter(Boolean).join(" · ") : ""].filter(Boolean).join(" · ");
    if (meta) acts.append(el("span", "muted", meta));
    const btn = (t, fn) => { const x = el("button", "link", t); x.onclick = fn; acts.append(x); };
    const act = (a) => VG.send({ type: "jobAction", action: a, job_id: j.id }).then(refreshJobs).catch(e => alert(e.message));
    if (j.status === "done") {
      if (j.exists) { btn("▶ Open", () => act("open")); btn("📂 Show", () => act("reveal")); }
      else acts.append(el("span", "muted", "file moved/deleted"));
    } else if (j.status === "paused") { btn("▶ Resume", () => act("resume")); btn("✕ Cancel", () => act("cancel")); }
    else if (j.status === "error" && j.needs_refresh) btn("🔄 Refresh link", () => api.tabs.create({ url: VG.BASE + "/manager?refresh=" + encodeURIComponent(j.id) }));
    else if (j.status === "error") btn(j.kind === "file" && j.resumable ? "▶ Resume" : "↻ Retry", () => act(j.kind === "file" ? "resume" : "retry"));
    else if (j.status === "cancelled") btn("↻ Retry", () => act("retry"));
    else if (!["cancelling", "pausing"].includes(j.status)) { btn("⏸ Pause", () => act("pause")); btn("✕ Cancel", () => act("cancel")); }
    if (!ACTIVE(j)) btn("Remove", () => act("remove"));
    if (j.status === "error") b.append(el("div", "err", (j.error || "").slice(0, 300)));
    if (j.warning) b.append(el("div", "warn", "⚠ " + j.warning));
    b.append(acts); if (j.thumbnail) row.append(th); row.append(b); box.append(row);
  }
}

/* ---------------- settings tab ---------------- */
async function paintSettings() {
  $("dock").checked = !!settings.dock;
  $("btn").checked = settings.show_button;
  $("nStart").checked = settings.notify_start; $("nDone").checked = settings.notify_done;
  $("catchDl").checked = settings.catch_downloads; $("catchTypes").value = settings.catch_types;
  $("catchMode").value = settings.catch_mode || "all"; paintCatch();
  if (!canDock) { $("dock").disabled = true; $("dockLbl").textContent = "Side panel isn't supported in this browser"; }
  if (IS_FIREFOX) $("dockLbl").textContent = "Open in the sidebar (docked) when I click the UDM icon";
  try {
    if (api.action.getUserSettings) {
      const us = await api.action.getUserSettings();
      show("pinTip", !us.isOnToolbar); show("pinOk", !!us.isOnToolbar);
      $("pinOk").textContent = "📌 UDM is pinned to your toolbar.";
    }
  } catch (e) {}
  const ua = navigator.userAgent;
  $("scPage").textContent = IS_FIREFOX ? "about:addons → ⚙ → Manage Extension Shortcuts"
    : /Edg\//.test(ua) ? "edge://extensions/shortcuts" : /OPR\//.test(ua) ? "opera://extensions/shortcuts"
    : navigator.brave ? "brave://extensions/shortcuts" : "chrome://extensions/shortcuts";
  show("scCopy", !IS_FIREFOX);
}
$("dock").onchange = async () => { await save({ dock: $("dock").checked }); VG.send({ type: "applyDock" }).catch(() => {}); };
$("btn").onchange = () => save({ show_button: $("btn").checked });
$("nStart").onchange = () => save({ notify_start: $("nStart").checked });
$("nDone").onchange = () => save({ notify_done: $("nDone").checked });
$("catchDl").onchange = async () => { await save({ catch_downloads: $("catchDl").checked }); paintCatch(); };
$("catchMode").onchange = async () => { await save({ catch_mode: $("catchMode").value }); paintCatch(); };
$("catchTypes").onchange = () => save({ catch_types: $("catchTypes").value.trim() });
$("catchTest").onclick = () => api.tabs.create({ url: VG.BASE + "/api/test-download", active: false });
async function paintCatch() {
  show("typesWrap", settings.catch_mode === "types");
  let r = { log: [], online: false };
  try { r = await VG.send({ type: "takeoverLog" }); } catch (e) {}
  $("catchStatus").textContent = !settings.catch_downloads ? "⏸ Off – the browser downloads files itself."
    : r.online ? "✅ On – new browser downloads go to UDM." : "⚠️ On, but the UDM app isn't running, so the browser downloads files itself.";
  const box = $("catchLog"); box.textContent = "";
  if (r.log.length) box.append(el("div", "", "Recent:"));
  for (const x of r.log.slice(0, 5)) {
    box.append(el("div", "", (x.taken ? "✓ UDM: " : "↩ Browser: ") + (x.name || "") + (x.why ? " – " + x.why : "")));
  }
}
$("nTest").onclick = () => VG.send({ type: "testNotify" }).catch(e => alert(e.message));
$("scCopy").onclick = () => navigator.clipboard.writeText($("scPage").textContent).catch(() => {});
$("folder").onclick = () => VG.send({ type: "reveal" }).catch(() => {});
$("app").onclick = () => VG.send({ type: "openApp" }).catch(() => {});
$("phone").onclick = () => VG.send({ type: "openApp", hash: "phone" }).catch(() => {});

init();
