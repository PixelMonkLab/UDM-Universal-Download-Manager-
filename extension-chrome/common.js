/* VDM extension – shared helpers (used by content script, popup and background) */
var VG = (() => {
  const api = globalThis.browser ?? globalThis.chrome;
  const BASE = "http://127.0.0.1:5757";
  const VERSION_LABEL = "1.0 Beta";

  // Pages that get the on-page Download button
  const SITES = [
    { name: "YouTube",   host: /(^|\.)youtube\.com$/,          path: /^\/(watch|shorts\/[^/]+|live\/[^/]+)/ },
    { name: "YouTube",   host: /^youtu\.be$/,                  path: /^\/[^/]+/ },
    { name: "Instagram", host: /(^|\.)instagram\.com$/,        path: /^\/([^/]+\/)?(p|reel|reels|tv|stories)\/[^/]+/ },
    { name: "Pinterest", host: /(^|\.)pinterest\.[a-z.]+$/,    path: /^\/pin\/[^/]+/ },
    { name: "Pinterest", host: /^pin\.it$/,                    path: /^\/[^/]+/ },
  ];
  const TEST_SITES = []; // filled only in the automated test build
  // "store" = Chrome Web Store edition: its rules don't allow downloading from YouTube.
  const EDITION = "full";
  const STORE_BLOCKED = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|googlevideo\.com|ytimg\.com)$/i;
  function isBlocked(url) {
    if (EDITION !== "store") return false;
    try { return STORE_BLOCKED.test(new URL(url).hostname); } catch (e) { return false; }
  }
  const BLOCKED_MSG = "YouTube isn't supported in this store version of the VDM add-on. Paste the link into the VDM app instead.";

  function siteOf(url) {
    if (isBlocked(url)) return null;
    try {
      const u = new URL(url);
      for (const s of SITES.concat(TEST_SITES)) {
        if (s.host.test(u.hostname) && s.path.test(u.pathname)) {
          if (s.name === "YouTube" && u.pathname === "/watch" && !u.searchParams.get("v")) return null;
          return s.name;
        }
      }
    } catch (e) {}
    return null;
  }

  // Remove tracking / playlist noise so we download exactly what's on screen
  function cleanUrl(url) {
    try {
      const u = new URL(url);
      if (/youtube\.com$/.test(u.hostname) && u.pathname === "/watch") {
        return `https://www.youtube.com/watch?v=${u.searchParams.get("v")}`;
      }
      if (/instagram\.com$/.test(u.hostname) || /pinterest\./.test(u.hostname)) {
        u.search = ""; u.hash = "";
      }
      return u.toString();
    } catch (e) { return url; }
  }

  const DEFAULTS = {
    mode: "video", quality: "best", audio_format: "mp3",
    mac_compat: true, cookies: "none", all_items: false, show_button: true,
    dock: true, notify_start: true, notify_done: true,
    catch_downloads: true,
    catch_types: "zip rar 7z tar gz tgz bz2 xz iso dmg pkg exe msi apk deb rpm appimage mp4 mkv avi mov webm mp3 flac wav m4a pdf epub docx xlsx pptx",
  };
  async function getSettings() {
    const r = await api.storage.local.get("settings");
    return Object.assign({}, DEFAULTS, r.settings || {});
  }
  async function saveSettings(patch) {
    const s = Object.assign(await getSettings(), patch);
    await api.storage.local.set({ settings: s });
    return s;
  }

  class Offline extends Error { constructor() { super("VDM app isn't running"); this.offline = true; } }

  // Talk to the local VDM app (only background + popup call this; content scripts go via messages)
  async function call(path, body, timeoutMs = 120000) {
    if (body && typeof body.url === "string" && isBlocked(body.url)) throw new Error(BLOCKED_MSG);
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    let r;
    try {
      r = await fetch(BASE + path, body === undefined
        ? { signal: ctrl.signal }
        : { method: "POST", signal: ctrl.signal,
            headers: { "Content-Type": "application/json", "X-VideoGrab": "1" },
            body: JSON.stringify(body) });
    } catch (e) {
      throw new Offline();
    } finally { clearTimeout(t); }
    let j = {};
    try { j = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(j.error || `VDM error (${r.status})`);
    return j;
  }

  // Message helper for content script / popup -> background
  async function send(msg) {
    const res = await api.runtime.sendMessage(msg);
    if (res && res.error) {
      const e = new Error(res.error); e.offline = !!res.offline; throw e;
    }
    return res;
  }

  return { api, BASE, VERSION_LABEL, EDITION, isBlocked, BLOCKED_MSG, SITES, TEST_SITES, siteOf, cleanUrl, DEFAULTS, getSettings, saveSettings, call, send, Offline };
})();
