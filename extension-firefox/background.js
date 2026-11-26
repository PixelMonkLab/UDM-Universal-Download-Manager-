/* VDM extension – background (service worker in Chrome/Edge/Brave/Opera, event page in Firefox) */
if (typeof VG === "undefined" && typeof importScripts === "function") importScripts("common.js");

const api = VG.api;
const MAX_JOBS = 25;

// ---------- job list (kept in storage so the popup can show it) ----------
async function getJobs() {
  const r = await api.storage.local.get("jobs");
  return r.jobs || [];
}
async function saveJob(job) {
  const jobs = await getJobs();
  const i = jobs.findIndex(j => j.job_id === job.job_id);
  if (i >= 0) jobs[i] = Object.assign(jobs[i], job); else jobs.unshift(job);
  await api.storage.local.set({ jobs: jobs.slice(0, MAX_JOBS) });
  return i >= 0 ? jobs[i] : job;
}

function summarize(p) {
  return {
    status: p.status, percent: p.percent || 0, speed: p.speed || "", eta: p.eta || "",
    filename: p.filename || null, error: p.error || null,
    title: p.title || undefined,
  };
}

// ---------- actions ----------
async function startDownload(url, opts = {}, source = "page") {
  const s = await VG.getSettings();
  const o = Object.assign({}, s, opts);
  const clean = VG.cleanUrl(url);
  const body = {
    url: clean, mode: o.mode, quality: o.quality, audio_format: o.audio_format,
    mac_compat: o.mac_compat, cookies: o.cookies, all_items: o.all_items, title: o.title || "",
    referer: o.referer || "", filename: o.filename || "", thumbnail: o.thumbnail || "",
    source: "extension",
  };
  const r = await VG.call("/api/download", body, 15000);
  const job = {
    job_id: r.job_id, url: clean, site: VG.siteOf(clean) || hostOf(clean),
    title: o.title || o.filename || "", mode: o.mode, quality: o.quality, source,
    status: "starting", percent: 0, ts: Date.now(), notified: false,
  };
  await saveJob(job);
  watch(job.job_id);
  if (s.notify_start) {
    notifyBasic("vg-start-" + job.job_id, "⬇️ Download started", o.title || o.filename || hostOf(clean) || clean);
  }
  updateBadge();
  return job;
}

function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }

async function progress(jobId) {
  try {
    const p = await VG.call("/api/progress/" + jobId, undefined, 8000);
    const job = await saveJob(Object.assign({ job_id: jobId }, summarize(p)));
    await maybeNotify(job);
    return job;
  } catch (e) {
    if (e.offline) throw e;
    // app restarted and forgot this job
    const job = await saveJob({ job_id: jobId, status: "error", error: "VDM was restarted during this download. Please try again." });
    return job;
  }
}

// background polling so notifications fire even if the tab/popup is closed
const watching = new Set();
async function watch(jobId) {
  if (watching.has(jobId)) return;
  watching.add(jobId);
  try {
    for (let i = 0; i < 3600; i++) {          // up to ~1.5 h
      await new Promise(r => setTimeout(r, 1500));
      let job;
      try { job = await progress(jobId); } catch (e) { break; }
      if (job.status === "done" || job.status === "error" || job.status === "cancelled") break;
    }
  } finally { watching.delete(jobId); updateBadge(); }
}

async function notifyBasic(id, title, message, buttons) {
  if (!api.notifications) return;
  const opts = { type: "basic", iconUrl: api.runtime.getURL("icons/128.png"), title, message: (message || "").slice(0, 200) };
  try {
    if (buttons) { try { await api.notifications.create(id, Object.assign({ buttons }, opts)); return; } catch (e) {} }
    await api.notifications.create(id, opts);       // Firefox has no notification buttons
  } catch (e) {}
}

async function maybeNotify(job) {
  if (job.status === "cancelled") {
    try { api.notifications && api.notifications.clear("vg-start-" + job.job_id); } catch (e) {}
    updateBadge();
    return;
  }
  if (job.notified || (job.status !== "done" && job.status !== "error")) return;
  await saveJob({ job_id: job.job_id, notified: true });
  updateBadge();
  if (!(await VG.getSettings()).notify_done) return;
  const done = job.status === "done";
  try { api.notifications && api.notifications.clear("vg-start-" + job.job_id); } catch (e) {}
  await notifyBasic("vg-" + job.job_id,
    done ? "✅ Download finished" : "⚠️ Download failed",
    done ? (job.filename || job.title || "Saved") : (job.error || "Something went wrong").split("\n")[0],
    done ? [{ title: "▶ Open" }, { title: "📂 Show in folder" }] : undefined);
}

async function updateBadge() {
  try {
    const n = (await getJobs()).filter(j => j.status !== "done" && j.status !== "error" && j.status !== "cancelled").length;
    await api.action.setBadgeText({ text: n ? "↓" + n : "" });
    await api.action.setBadgeBackgroundColor({ color: "#1f9d55" });
  } catch (e) {}
}

// Re-check unfinished jobs (e.g. after the service worker was restarted)
async function refreshUnfinished() {
  const jobs = await getJobs();
  for (const j of jobs) {
    if (!["done", "error", "cancelled"].includes(j.status)) watch(j.job_id);
  }
  return getJobs();
}

// ---------- find streamed ("blob:") and direct videos on any page ----------
// Players that show a blob: link really load an HLS (.m3u8) or DASH (.mpd) playlist, or a video file.
// We watch the page's network requests for those, so VDM can download every piece and join them.
const MANIFEST_TYPES = /(mpegurl|dash\+xml)/i;
const SEGMENT_RE = /\.(ts|m4s|m4a|m4v\?.*range|aac|vtt|webvtt|srt|key|jpg|png|gif|webp)(\?|#|$)/i;
const SKIP_HOSTS = /(googlevideo\.com|doubleclick\.net|googlesyndication|google-analytics|facebook\.com\/tr)/i;
const media = {};            // tabId -> [{url, kind, size, ts, page}]

async function loadMedia(tabId) {
  if (media[tabId]) return media[tabId];
  try {
    const k = "media_" + tabId;
    const r = api.storage.session ? await api.storage.session.get(k) : {};
    media[tabId] = r[k] || [];
  } catch (e) { media[tabId] = []; }
  return media[tabId];
}
function storeMedia(tabId) {
  try { api.storage.session && api.storage.session.set({ ["media_" + tabId]: media[tabId] || [] }); } catch (e) {}
  const n = (media[tabId] || []).length;
  try {
    api.action.setBadgeText({ tabId, text: n ? String(n) : "" });
    api.action.setBadgeBackgroundColor({ tabId, color: "#1a8cff" });
  } catch (e) {}
}
function classify(url, headers) {
  const h = {};
  for (const x of headers || []) h[x.name.toLowerCase()] = x.value || "";
  const ct = (h["content-type"] || "").toLowerCase();
  const path = url.split("?")[0].toLowerCase();
  if (/\.m3u8$/.test(path) || /mpegurl/.test(ct)) return { kind: "HLS stream" };
  if (/\.mpd$/.test(path) || /dash\+xml/.test(ct)) return { kind: "DASH stream" };
  if (/video\/mp2t|iso\.segment/.test(ct) || SEGMENT_RE.test(path)) return null;      // a piece, not the whole thing
  const isVideo = /^video\/(mp4|webm|quicktime|x-matroska|x-m4v)/.test(ct) || /\.(mp4|webm|mov|mkv)$/.test(path);
  if (!isVideo) return null;
  if (/[?&](bytestart|byteend|range)=/.test(url)) return null;                         // partial chunk
  let size = 0;
  const cr = h["content-range"]; const m = cr && cr.match(/\/(\d+)$/);
  size = m ? +m[1] : +(h["content-length"] || 0);
  if (size && size < 300 * 1024) return null;                                         // tiny ads / previews
  return { kind: "Video file", size };
}

api.webRequest && api.webRequest.onHeadersReceived.addListener(async (d) => {
  if (d.tabId < 0 || d.url.startsWith(VG.BASE) || SKIP_HOSTS.test(d.url) || d.statusCode >= 400) return;
  if (!/^https?:/.test(d.url)) return;
  const c = classify(d.url, d.responseHeaders);
  if (!c) return;
  let tab = null;
  try { tab = await api.tabs.get(d.tabId); } catch (e) { return; }
  if (VG.siteOf(tab.url || "")) return;          // YouTube/Instagram/Pinterest: the page link works better
  const list = await loadMedia(d.tabId);
  const key = d.url.split("?")[0];
  const i = list.findIndex(x => x.url.split("?")[0] === key);
  const item = { url: d.url, kind: c.kind, size: c.size || 0, ts: Date.now(), page: tab.url, title: tab.title || "" };
  if (i >= 0) list[i] = Object.assign(list[i], item); else list.unshift(item);
  list.splice(30);
  storeMedia(d.tabId);
}, { urls: ["<all_urls>"], types: ["xmlhttprequest", "media", "other", "object"] }, ["responseHeaders"]);

api.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) { media[tabId] = []; storeMedia(tabId); }
});
api.tabs.onRemoved.addListener((tabId) => {
  delete media[tabId];
  try { api.storage.session && api.storage.session.remove("media_" + tabId); } catch (e) {}
});

// Best guess for "the video on this page": a master playlist first, then the biggest file
function bestMedia(list) {
  const streams = list.filter(x => x.kind !== "Video file");
  const master = streams.find(x => /master|playlist|manifest/i.test(x.url)) || streams[streams.length - 1];
  if (master) return master;
  return list.slice().sort((a, b) => (b.size || 0) - (a.size || 0))[0];
}
async function downloadMedia(item, opts = {}, source = "popup") {
  const name = (opts.filename || item.title || "").replace(/[\\/:*?"<>|]/g, "_").slice(0, 100);
  return startDownload(item.url, Object.assign({ referer: item.page, filename: name, title: item.title }, opts), source);
}

// ---------- IDM-style: any file link / take over browser downloads ----------
async function cookieHeader(url) {
  try {
    if (!api.cookies) return "";
    const list = await api.cookies.getAll({ url });
    return list.map(c => `${c.name}=${c.value}`).join("; ");
  } catch (e) { return ""; }
}
async function fetchFile(url, opts = {}, source = "extension") {
  const s = await VG.getSettings();
  const body = {
    url, referer: opts.referer || "", filename: opts.filename || "", source,
    headers: { Cookie: await cookieHeader(url), user_agent: navigator.userAgent },
  };
  const r = await VG.call("/api/fetch", body, 15000);
  const job = { job_id: r.job_id, url, site: hostOf(url), title: opts.filename || url.split("?")[0].split("/").pop(),
                mode: "file", source, status: "starting", percent: 0, ts: Date.now(), notified: false };
  await saveJob(job);
  watch(job.job_id);
  if (s.notify_start) notifyBasic("vg-start-" + job.job_id, "⬇️ Download started", job.title || url);
  updateBadge();
  return job;
}

let appOnline = false, appCheckedAt = 0;
async function appIsOnline() {
  if (Date.now() - appCheckedAt < 15000) return appOnline;
  try { await VG.call("/api/ping", undefined, 1500); appOnline = true; } catch (e) { appOnline = false; }
  appCheckedAt = Date.now();
  return appOnline;
}
const handedOver = new Set();
api.downloads && api.downloads.onCreated.addListener(async (item) => {
  try {
    const s = await VG.getSettings();
    const url = item.finalUrl || item.url || "";
    if (!s.catch_downloads || !/^https?:/i.test(url) || handedOver.has(url)) return;
    if (item.byExtensionId && item.byExtensionId === api.runtime.id) return;
    const name = (item.filename || "").split(/[\\/]/).pop() || decodeURIComponent(url.split("?")[0].split("/").pop() || "");
    const ext = (name.match(/\.([a-z0-9]{1,6})$/i) || [])[1];
    const types = (s.catch_types || "").toLowerCase().split(/[\s,]+/).filter(Boolean);
    if (!(types.includes("*") || (ext && types.includes(ext.toLowerCase())) || (!ext && item.fileSize > 20 * 1024 * 1024))) return;
    if (!(await appIsOnline())) return;                   // VDM not running: leave it to the browser
    await api.downloads.cancel(item.id).catch(() => {});
    api.downloads.erase({ id: item.id }).catch(() => {});
    handedOver.add(url); setTimeout(() => handedOver.delete(url), 30000);
    await fetchFile(url, { referer: item.referrer || "", filename: "" }, "catch");
  } catch (e) {
    notifyBasic("vg-err-" + Date.now(), "VDM couldn't take over the download", e.message || String(e));
  }
});

// ---------- messages from content scripts and popup ----------
async function handle(msg, sender) {
  switch (msg.type) {
    case "ping":       { const p = await VG.call("/api/ping", undefined, 3000); hello(); return p; }
    case "appJobs":    return VG.call("/api/jobs", undefined, 5000);
    case "jobAction": {
      const id = encodeURIComponent(msg.job_id);
      const path = { cancel: "/api/cancel/" + id, retry: "/api/retry/" + id, remove: "/api/remove/" + id,
                     pause: "/api/pause/" + id, resume: "/api/resume/" + id,
                     open: "/api/open-file", reveal: "/api/open-folder" }[msg.action];
      if (!path) throw new Error("Unknown action");
      const r = await VG.call(path, { job_id: msg.job_id }, 8000);
      if (msg.action === "retry" && r.job_id) { await saveJob({ job_id: r.job_id, status: "starting", ts: Date.now(), notified: false }); watch(r.job_id); }
      return r;
    }
    case "fetchFile":  return fetchFile(msg.url, msg.opts || {}, msg.source || "extension");
    case "openManager": {
      const online = await appIsOnline().catch(() => false);
      await api.tabs.create({ url: online || await VG.call("/api/ping", undefined, 1500).then(() => true, () => false)
        ? VG.BASE + "/manager" : api.runtime.getURL("popup.html?full=1") });
      return { ok: true };
    }
    case "applyDock":  await applyDock(); return { ok: true };
    case "testNotify": await notifyBasic("vg-test-" + Date.now(), "🔔 VDM notifications work", "You'll see one when downloads start and finish."); return { ok: true };
    case "info":       return VG.call("/api/info", { url: VG.cleanUrl(msg.url), cookies: (await VG.getSettings()).cookies });
    case "download":   return startDownload(msg.url, msg.opts || {}, msg.source || "page");
    case "progress":   return progress(msg.job_id);
    case "jobs":       return { jobs: await refreshUnfinished() };
    case "clearJobs": {
      const jobs = (await getJobs()).filter(j => j.status !== "done" && j.status !== "error");
      await api.storage.local.set({ jobs });
      try { await VG.call("/api/clear-history", {}, 5000); } catch (e) {}
      return { jobs };
    }
    case "media": {
      const list = await loadMedia(msg.tabId);
      return { media: list, best: list.length ? bestMedia(list).url : null };
    }
    case "downloadMedia": {
      const list = await loadMedia(msg.tabId);
      const item = Object.assign({ url: msg.url, page: msg.page || "", title: msg.title || "" },
                                 list.find(x => x.url === msg.url) || {});
      return downloadMedia(item, msg.opts || {}, "popup");
    }
    case "reveal":     return VG.call("/api/open-folder", { job_id: msg.job_id || "" }, 8000);
    case "openApp": {
      const q = new URLSearchParams();
      if (msg.url) q.set("url", msg.url);
      if (msg.referer) q.set("referer", msg.referer);
      if (msg.name) q.set("name", msg.name);
      await api.tabs.create({ url: VG.BASE + "/" + (q.toString() ? "?" + q : "") + (msg.hash ? "#" + msg.hash : "") });
      return { ok: true };
    }
    default:           throw new Error("Unknown request");
  }
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(
    r => sendResponse(r || { ok: true }),
    e => sendResponse({ error: e.message || String(e), offline: !!e.offline })
  );
  return true; // async response
});

// ---------- right-click menu ----------
function setupMenus() {
  if (!api.contextMenus) return;
  api.contextMenus.removeAll(() => {
    const ctx = ["link", "page", "video", "image"];
    api.contextMenus.create({ id: "vg-video", title: "Download video with VDM", contexts: ctx });
    api.contextMenus.create({ id: "vg-audio", title: "Download audio (MP3) with VDM", contexts: ctx });
    api.contextMenus.create({ id: "vg-open", title: "Open in VDM (choose quality)…", contexts: ctx });
    api.contextMenus.create({ id: "vg-link", title: "Download link with VDM (fast)", contexts: ["link"] });
    api.contextMenus.create({ id: "vg-mgr", title: "VDM Download Manager", contexts: ["action"] });
  });
}
api.runtime.onInstalled.addListener(setupMenus);
if (api.runtime.onStartup) api.runtime.onStartup.addListener(setupMenus);

api.contextMenus && api.contextMenus.onClicked.addListener(async (info, tab) => {
  // Prefer a link to a post/video; otherwise the page itself
  if (info.menuItemId === "vg-mgr") { handle({ type: "openManager" }); return; }
  if (info.menuItemId === "vg-link") {
    try { await fetchFile(info.linkUrl, { referer: info.pageUrl || "" }, "menu"); }
    catch (e) { notifyBasic("vg-err-" + Date.now(), e.offline ? "VDM isn't running" : "VDM", e.offline ? "Start VDM on your computer, then try again." : e.message); }
    return;
  }
  let url = info.linkUrl && (VG.siteOf(info.linkUrl) || !VG.siteOf(info.pageUrl || "")) ? info.linkUrl : (info.pageUrl || info.linkUrl);
  if (!url) return;
  try {
    if (info.menuItemId === "vg-open") {
      const found = tab && !VG.siteOf(url) ? await loadMedia(tab.id) : [];
      const q = new URLSearchParams();
      if (found.length && (!info.linkUrl || url === info.pageUrl)) {
        const b = bestMedia(found); q.set("url", b.url); q.set("referer", b.page); q.set("name", b.title || "");
      } else q.set("url", VG.cleanUrl(url));
      await api.tabs.create({ url: VG.BASE + "/?" + q });
      return;
    }
    const opts = info.menuItemId === "vg-audio" ? { mode: "audio", audio_format: "mp3" } : { mode: "video" };
    const found = tab && !VG.siteOf(url) ? await loadMedia(tab.id) : [];
    if (found.length && (!info.linkUrl || url === info.pageUrl)) {
      await downloadMedia(bestMedia(found), opts, "menu");      // streamed / blob video on this page
    } else {
      await startDownload(url, opts, "menu");
    }

  } catch (e) {
    if (api.notifications) {
      api.notifications.create("vg-err-" + Date.now(), {
        type: "basic", iconUrl: api.runtime.getURL("icons/128.png"),
        title: e.offline ? "VDM app isn't running" : "VDM",
        message: e.offline ? "Start VDM on your computer, then try again." : (e.message || String(e)).slice(0, 180),
      });
    }
  }
});

// Clicking a "Download finished" notification shows the file; its buttons open / reveal it
const jobOf = id => /^vg-[0-9a-f]{6,}$/.test(id) ? id.slice(3) : null;
api.notifications && api.notifications.onClicked.addListener(id => {
  const j = jobOf(id);
  if (j) VG.call("/api/open-folder", { job_id: j }, 8000).catch(() => {});
  api.notifications.clear(id);
});
api.notifications && api.notifications.onButtonClicked && api.notifications.onButtonClicked.addListener((id, i) => {
  const j = jobOf(id);
  if (j) VG.call(i === 0 ? "/api/open-file" : "/api/open-folder", { job_id: j }, 8000).catch(() => {});
  api.notifications.clear(id);
});

// ---------- tell the app the add-on is here (so the app can show "Add-on installed ✓") ----------
function browserName() {
  const u = navigator.userAgent;
  return /Firefox\//.test(u) ? "Firefox" : /Edg\//.test(u) ? "Edge" : /OPR\//.test(u) ? "Opera" : "Chrome/Brave";
}
function hello() {
  const m = api.runtime.getManifest();
  VG.call("/api/extension-hello", { version: VG.VERSION_LABEL, browser: browserName() }, 3000).catch(() => {});
}

// ---------- docking: open in the side panel (Chrome/Edge) or sidebar (Firefox) ----------
async function applyDock() {
  const dock = !!(await VG.getSettings()).dock;
  try {
    if (api.sidePanel && api.sidePanel.setPanelBehavior) {
      await api.sidePanel.setPanelBehavior({ openPanelOnActionClick: dock });
      await api.action.setPopup({ popup: dock ? "" : "popup.html" });
    } else if (api.sidebarAction) {                       // Firefox
      await api.action.setPopup({ popup: dock ? "" : "popup.html" });
    }
  } catch (e) {}
}
api.action.onClicked && api.action.onClicked.addListener(() => {
  // only fires when there's no popup, i.e. "dock" is on (Firefox needs this to toggle its sidebar)
  if (api.sidebarAction && api.sidebarAction.toggle) api.sidebarAction.toggle();
});

// ---------- first install: say hello, show the welcome page, open VDM ----------
api.runtime.onInstalled.addListener(async (d) => {
  hello();
  await applyDock();
  updateBadge();
  if (d.reason === "install") {
    await api.tabs.create({ url: api.runtime.getURL("welcome.html") });
  }
});
api.runtime.onStartup && api.runtime.onStartup.addListener(() => { hello(); applyDock(); updateBadge(); });

// ---------- keyboard shortcut: download the video on this page ----------
api.commands && api.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== "download-current") return;
  const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) return;
  try {
    const found = !VG.siteOf(tab.url) ? await loadMedia(tab.id) : [];
    if (found.length) await downloadMedia(bestMedia(found), {}, "shortcut");
    else await startDownload(tab.url, { title: tab.title }, "shortcut");
  } catch (e) {
    notifyBasic("vg-err-" + Date.now(), e.offline ? "VDM app isn't running" : "VDM",
      e.offline ? "Start VDM on your computer, then try again." : e.message);
  }
});
