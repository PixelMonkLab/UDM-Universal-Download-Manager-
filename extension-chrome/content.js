/* UDM extension – on-page Download button for YouTube, Instagram and Pinterest */
(() => {
  if (window.__videograbLoaded) return;
  window.__videograbLoaded = true;
  const api = VG.api;

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .wrap { position: fixed; right: 22px; bottom: 22px; z-index: 2147483646; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
    .pill { display: flex; align-items: center; gap: 8px; border: none; cursor: pointer; color: #fff;
      background: #1a8cff; padding: 11px 16px; border-radius: 999px; font-size: 14px; font-weight: 600;
      box-shadow: 0 6px 22px rgba(0,0,0,.35); position: relative; overflow: hidden; min-width: 128px; justify-content: center; }
    .pill:hover { background: #0f74d6; }
    .pill .fill { position: absolute; left: 0; top: 0; bottom: 0; width: 0; background: rgba(255,255,255,.22); transition: width .3s; }
    .pill span { position: relative; }
    .pill.ok { background: #1f9d55; } .pill.err { background: #d64545; } .pill.off { background: #555; }
    .menu { background: #181b22; color: #e9ecf1; border: 1px solid #2a2f3a; border-radius: 14px; padding: 8px; width: 270px;
      box-shadow: 0 10px 30px rgba(0,0,0,.45); font-size: 13px; display: none; }
    .menu.open { display: block; }
    .menu h4 { margin: 4px 8px 8px; font-size: 12px; color: #9aa3b2; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
    .item { display: flex; justify-content: space-between; width: 100%; text-align: left; background: transparent; color: #e9ecf1;
      border: none; padding: 9px 10px; border-radius: 9px; cursor: pointer; font-size: 14px; }
    .item:hover { background: #262a35; }
    .item small { color: #9aa3b2; }
    .sep { height: 1px; background: #2a2f3a; margin: 6px 4px; }
    .msg { padding: 8px 10px; color: #e9ecf1; line-height: 1.45; white-space: pre-wrap; max-height: 180px; overflow: auto; }
    .msg.err { color: #ff8a8a; }
    .link { color: #5fb8ff; cursor: pointer; text-decoration: underline; background: none; border: none; padding: 0; font-size: 13px; }
    .mini { position: absolute; top: 10px; right: 52px; z-index: 50; border: none; cursor: pointer; color: #fff; background: #1a8cff;
      padding: 6px 11px; border-radius: 999px; font-size: 12px; font-weight: 600; box-shadow: 0 3px 12px rgba(0,0,0,.3); }
    .mini.ok { background: #1f9d55; } .mini.err { background: #d64545; } .mini.off { background: #555; }
  `;

  // ---------------- floating button ----------------
  const host = document.createElement("div");
  host.id = "videograb-root";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${CSS}</style>
    <div class="wrap">
      <div class="menu" part="menu"></div>
      <button class="pill" title="Download with UDM"><div class="fill"></div><span class="label">⬇ Download</span></button>
    </div>`;
  const pill = root.querySelector(".pill");
  const fill = root.querySelector(".fill");
  const label = root.querySelector(".label");
  const menu = root.querySelector(".menu");

  let settings = Object.assign({}, VG.DEFAULTS);
  let currentUrl = null;
  const jobsByUrl = {};   // url -> {job_id, status, ...}
  let pollTimer = null;

  VG.getSettings().then(s => { settings = s; tick(); });
  api.storage.onChanged.addListener((ch) => { if (ch.settings) { settings = Object.assign({}, VG.DEFAULTS, ch.settings.newValue); tick(); } });

  function setPill(text, cls = "", pct = 0) {
    label.textContent = text;
    pill.className = "pill " + cls;
    fill.style.width = (cls ? 0 : pct) + "%";
  }

  function renderIdle() {
    const job = jobsByUrl[currentUrl];
    if (!job) return setPill("⬇ Download");
    renderJob(job);
  }

  function renderJob(job) {
    if (job.status === "done") setPill("✓ Saved", "ok");
    else if (job.status === "error") setPill(job.offline ? "⚠ App not running" : "⚠ Failed", job.offline ? "off" : "err");
    else if (job.status === "converting") setPill("Converting…", "", 100);
    else if (job.status === "processing") setPill("Finishing…", "", 100);
    else if (job.status === "downloading") setPill(`⬇ ${Math.round(job.percent || 0)}%`, "", job.percent || 0);
    else setPill("Starting…", "", 2);
  }

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function openMenu() {
    menu.textContent = "";
    const job = jobsByUrl[currentUrl];
    if (job && job.status === "error") {
      menu.append(el("h4", "", job.offline ? "UDM app isn't running" : "Download failed"));
      menu.append(el("div", "msg " + (job.offline ? "" : "err"),
        job.offline ? "Start UDM on your computer first:\n• Mac: double-click “Start UDM.command”\n• Windows: double-click “start-windows.bat”" : job.error));
      menu.append(el("div", "sep"));
    } else if (job && job.status === "done") {
      menu.append(el("h4", "", "Saved"));
      menu.append(el("div", "msg", job.filename || "Saved to Downloads/UDM"));
      const show = el("button", "item"); show.append(el("span", "", "📂 Show in folder"));
      show.onclick = () => { VG.send({ type: "reveal", job_id: job.job_id }).catch(() => {}); closeMenu(); };
      menu.append(show, el("div", "sep"));
    } else if (job && job.status !== "error") {
      menu.append(el("h4", "", "Downloading…"));
      menu.append(el("div", "msg", `${Math.round(job.percent || 0)}%  ${job.speed || ""}  ${job.eta ? "· ETA " + job.eta : ""}`));
      menu.append(el("div", "sep"));
    }

    menu.append(el("h4", "", job ? "Download again" : "Download"));
    const opts = [
      ["🎬 Video", "Best quality", { mode: "video", quality: "best" }],
      ["🎬 Video", "1080p", { mode: "video", quality: "1080" }],
      ["🎬 Video", "720p", { mode: "video", quality: "720" }],
      ["🎬 Video", "480p", { mode: "video", quality: "480" }],
      ["🎵 Audio", "MP3", { mode: "audio", audio_format: "mp3" }],
    ];
    for (const [a, b, o] of opts) {
      const it = el("button", "item"); it.append(el("span", "", a), el("small", "", b));
      it.onclick = () => { closeMenu(); start(currentUrl, o); };
      menu.append(it);
    }
    if (/instagram\.com$/.test(location.hostname)) {
      const it = el("button", "item"); it.append(el("span", "", "🖼 All items"), el("small", "", "carousel"));
      it.onclick = () => { closeMenu(); start(currentUrl, { mode: "video", all_items: true }); };
      menu.append(it);
    }
    menu.append(el("div", "sep"));
    const more = el("button", "item"); more.append(el("span", "", "⚙ More options…"), el("small", "", "opens app"));
    more.onclick = () => { closeMenu(); VG.send({ type: "openApp", url: currentUrl }).catch(() => {}); };
    menu.append(more);
    menu.classList.add("open");
  }
  function closeMenu() { menu.classList.remove("open"); }

  pill.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.classList.contains("open")) return closeMenu();
    openMenu();
  });
  pill.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); openMenu(); });
  document.addEventListener("click", (e) => { if (!e.composedPath().includes(host)) closeMenu(); }, true);

  async function start(url, opts) {
    const t = pageTitle();
    jobsByUrl[url] = { status: "starting", percent: 0 };
    if (url === currentUrl) renderIdle();
    try {
      const og = document.querySelector('meta[property="og:image"]');
      const job = await VG.send({ type: "download", url, opts: Object.assign({ title: t, thumbnail: og ? og.content : "" }, opts) });
      jobsByUrl[url] = job;
      follow(url);
    } catch (e) {
      jobsByUrl[url] = { status: "error", error: e.message, offline: e.offline };
    }
    if (url === currentUrl) renderIdle();
  }

  function follow(url) {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      const j = jobsByUrl[url];
      if (!j || !j.job_id) return clearInterval(pollTimer);
      try {
        const p = await VG.send({ type: "progress", job_id: j.job_id });
        jobsByUrl[url] = Object.assign(j, p);
      } catch (e) {
        jobsByUrl[url] = Object.assign(j, { status: "error", error: e.message, offline: e.offline });
      }
      if (url === currentUrl) renderIdle();
      if (["done", "error"].includes(jobsByUrl[url].status)) clearInterval(pollTimer);
    }, 1000);
  }

  function pageTitle() {
    const og = document.querySelector('meta[property="og:title"]');
    let t = (og && og.content) || document.title || "";
    return t.replace(/ - YouTube$/, "").replace(/^\(\d+\)\s*/, "").slice(0, 120);
  }

  // ---------------- per-post buttons in the Instagram feed ----------------
  function scanInstagramFeed() {
    if (!/instagram\.com$/.test(location.hostname) || !settings.show_button) return;
    document.querySelectorAll("article").forEach(art => {
      if (art.__vg) return;
      const link = Array.from(art.querySelectorAll("a[href]")).map(a => a.href).find(h => VG.siteOf(h) === "Instagram");
      if (!link) return;
      art.__vg = true;
      if (getComputedStyle(art).position === "static") art.style.position = "relative";
      const h = document.createElement("div");
      h.className = "videograb-mini";
      const r = h.attachShadow({ mode: "open" });
      r.innerHTML = `<style>${CSS}</style><button class="mini" title="Download with UDM">⬇ Download</button>`;
      const b = r.querySelector("button");
      let job = null, timer = null;
      const show = () => {
        if (!job) { b.className = "mini"; b.textContent = "⬇ Download"; return; }
        b.className = "mini " + (job.status === "done" ? "ok" : job.status === "error" ? (job.offline ? "off" : "err") : "");
        b.textContent = job.status === "done" ? "✓ Saved" : job.status === "error" ? (job.offline ? "⚠ App off" : "⚠ Failed")
          : job.status === "downloading" ? `⬇ ${Math.round(job.percent || 0)}%` : job.status === "converting" ? "Converting…" : "Starting…";
        b.title = job.status === "error" ? job.error : job.status === "done" ? "Click to show in folder" : "Download with UDM";
      };
      b.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        if (job && job.status === "done") return VG.send({ type: "reveal", job_id: job.job_id }).catch(() => {});
        if (job && !["error"].includes(job.status)) return;
        job = { status: "starting" }; show();
        try {
          job = await VG.send({ type: "download", url: link, opts: {} });
          timer = setInterval(async () => {
            try { job = Object.assign(job, await VG.send({ type: "progress", job_id: job.job_id })); }
            catch (err) { job = { status: "error", error: err.message, offline: err.offline }; }
            show();
            if (["done", "error"].includes(job.status)) clearInterval(timer);
          }, 1000);
        } catch (err) { job = { status: "error", error: err.message, offline: err.offline }; }
        show();
      };
      art.appendChild(h);
    });
  }

  // ---------------- keep in sync with single-page navigation ----------------
  function tick() {
    const target = VG.siteOf(location.href) ? location.href : null;
    const visible = !!target && settings.show_button && !document.fullscreenElement;
    if (visible && !host.isConnected) (document.body || document.documentElement).appendChild(host);
    host.style.display = visible ? "" : "none";
    if (target !== currentUrl) {
      currentUrl = target;
      closeMenu();
      renderIdle();
      const j = jobsByUrl[currentUrl];
      if (j && j.job_id && !["done", "error"].includes(j.status)) follow(currentUrl);
    }
    scanInstagramFeed();
  }
  setInterval(tick, 800);
  document.addEventListener("fullscreenchange", tick);
  tick();
})();
