/* VDM add-on – welcome page shown right after installing */
const api = VG.api;
const $ = (id) => document.getElementById(id);
const m = api.runtime.getManifest();
$("ver").textContent = "v" + VG.VERSION_LABEL;
const hasPanel = !!(api.sidePanel && api.sidePanel.open);
const hasSidebar = !!api.sidebarAction;
if (!hasPanel && !hasSidebar) $("openNote").textContent = "It opens from the VDM icon in your toolbar.";

async function openVDM(auto) {
  const s = await VG.getSettings();
  try {
    if (s.dock && hasPanel) {                       // Chrome / Edge: docked side panel
      const w = await api.windows.getCurrent();
      await api.sidePanel.open({ windowId: w.id });
      return true;
    }
    if (s.dock && hasSidebar) { await api.sidebarAction.open(); return true; }
  } catch (e) { /* needs a click – fall through */ }
  try {
    if (api.action.openPopup) {
      await api.action.setPopup({ popup: "popup.html" });
      await api.action.openPopup();
      if (s.dock) setTimeout(() => api.action.setPopup({ popup: "" }).catch(() => {}), 500);
      return true;
    }
  } catch (e) {}
  if (!auto) api.tabs.create({ url: api.runtime.getURL("popup.html?full=1") });
  return false;
}
$("openBtn").onclick = () => openVDM(false);
// open VDM automatically right after installing (browsers may require the click instead)
setTimeout(() => openVDM(true), 600);

async function check() {
  try {
    const p = await VG.send({ type: "ping" });
    $("app").textContent = `✓ Connected to the VDM app (${p.version_label || p.version}). Saving to ${p.folder}`;
    $("app").className = "st ok"; $("appHelp").classList.add("hidden");
  } catch (e) {
    $("app").textContent = "✗ The VDM app isn't running yet.";
    $("app").className = "st bad"; $("appHelp").classList.remove("hidden");
  }
  try {
    if (api.action.getUserSettings) {
      const us = await api.action.getUserSettings();
      $("pin").textContent = us.isOnToolbar ? "📌 Pinned. You're all set!" : "Not pinned yet:";
      $("pin").className = "st " + (us.isOnToolbar ? "ok" : "");
      $("pinHelp").classList.toggle("hidden", us.isOnToolbar);
    } else { $("pin").textContent = ""; }
  } catch (e) {}
}
check(); setInterval(check, 2000);
