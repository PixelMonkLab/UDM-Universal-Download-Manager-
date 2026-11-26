/* Lets the VDM app page know this add-on is installed. */
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  document.documentElement.dataset.videograbExt = "1.0 Beta";
})();
