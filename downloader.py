"""
VDM download engine for ordinary files (zip, pdf, exe, mp3, images ...) - IDM style.

* Splits a file into parts and downloads them over several connections at once.
* When a connection finishes early it takes over half of the biggest remaining part ("work stealing"),
  so the download keeps all connections busy until the very end.
* Pause / resume, even after VDM was closed (progress is saved next to the .part file).
* Falls back to one connection when the server doesn't support partial downloads.
"""
from __future__ import annotations

import email.message
import json
import mimetypes
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 64 * 1024
MIN_PART = 1024 * 1024            # never split into parts smaller than 1 MB
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

CATEGORIES = {
    "Video": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts m2ts vob ogv",
    "Music": "mp3 m4a aac wav flac ogg opus wma aiff alac mid midi",
    "Documents": "pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf csv epub mobi md pages numbers key",
    "Compressed": "zip rar 7z tar gz tgz bz2 xz zst iso dmg cab lz lzma",
    "Programs": "exe msi apk dmg pkg deb rpm appimage msix jar bat sh xapk aab",
    "Images": "jpg jpeg png gif webp bmp svg tif tiff heic heif raw cr2 nef psd ico avif",
}
EXT_TO_CAT = {}
for _cat, _exts in CATEGORIES.items():
    for _e in _exts.split():
        EXT_TO_CAT.setdefault(_e, _cat)


def category_for(name: str) -> str:
    n = (name or "").lower().split("?")[0]
    if n.endswith((".tar.gz", ".tar.xz", ".tar.bz2")):
        return "Compressed"
    ext = n.rsplit(".", 1)[-1] if "." in n else ""
    return EXT_TO_CAT.get(ext, "Other")


class Limiter:
    """Shared speed limit for all file downloads (bytes per second, 0 = unlimited)."""

    def __init__(self):
        self.rate = 0
        self._lock = threading.Lock()
        self._allow = 0.0
        self._t = time.monotonic()

    def take(self, n: int):
        if not self.rate:
            return
        with self._lock:
            now = time.monotonic()
            self._allow = min(float(self.rate), self._allow + (now - self._t) * self.rate)
            self._t = now
            self._allow -= n
            wait = -self._allow / self.rate if self._allow < 0 else 0
        if wait > 0:
            time.sleep(min(wait, 2.0))


LIMITER = Limiter()


def _ctx():
    return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)


def _open(url: str, headers: dict, rng=None, timeout=25):
    h = {"User-Agent": DEFAULT_UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    h.update({k: v for k, v in (headers or {}).items() if v})
    if rng is not None:
        h["Range"] = f"bytes={rng[0]}-{'' if rng[1] is None else rng[1]}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout, context=_ctx())


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return name[:180] or "download"


def probe(url: str, headers: dict) -> dict:
    """Ask the server about the file: size, real name, and whether it can be downloaded in parts."""
    info = {"final_url": url, "size": None, "ranges": False, "filename": None, "ctype": "",
            "etag": None, "last_modified": None}
    try:
        r = _open(url, headers, rng=(0, 0))
    except urllib.error.HTTPError as e:
        if e.code == 416:                      # empty file
            info["size"] = 0
            return info
        raise
    with r:
        info["final_url"] = r.geturl()
        cr = r.headers.get("Content-Range") or ""
        if r.status == 206 and "/" in cr and cr.split("/")[-1].strip().isdigit():
            info["size"] = int(cr.split("/")[-1])
            info["ranges"] = True
        else:
            cl = r.headers.get("Content-Length")
            info["size"] = int(cl) if cl and cl.isdigit() else None
        cd = r.headers.get("Content-Disposition")
        if cd:
            m = email.message.Message()
            m["content-disposition"] = cd
            info["filename"] = m.get_filename()
        info["ctype"] = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        info["etag"] = r.headers.get("ETag")
        info["last_modified"] = r.headers.get("Last-Modified")
    if not info["filename"]:
        path = urllib.parse.urlparse(info["final_url"]).path
        info["filename"] = urllib.parse.unquote(path.rsplit("/", 1)[-1]) or "download"
    fn = safe_filename(info["filename"])
    if "." not in fn and info["ctype"]:
        ext = mimetypes.guess_extension(info["ctype"]) or ""
        fn += {".jpe": ".jpg"}.get(ext, ext)
    info["filename"] = fn
    return info


def _plan(size: int | None, ranges: bool, connections: int) -> list[dict]:
    if not ranges or not size or size < 2 * MIN_PART or connections <= 1:
        return [{"s": 0, "e": (size - 1) if size else None, "d": 0}]
    n = max(1, min(connections, size // MIN_PART))
    step = size // n
    segs = []
    for i in range(n):
        s = i * step
        e = size - 1 if i == n - 1 else (i + 1) * step - 1
        segs.append({"s": s, "e": e, "d": 0})
    return segs


def run_file_download(job: dict, headers: dict, folder: Path, connections: int, unique_path) -> None:
    """Download job["url"] into folder. Updates job in place. Raises on failure."""
    lock = threading.Lock()
    state_file = Path(job["_part"] + ".vgstate") if job.get("_part") else None
    state = None
    if state_file and state_file.exists() and Path(job["_part"]).exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = None

    if state:
        url, size, ranges, segs = state["url"], state["size"], state["ranges"], state["segments"]
        filename, part = state["filename"], Path(job["_part"])
        if not ranges:                          # can't continue a one-connection download: start over
            segs = _plan(size, False, 1)
    else:
        job["status"] = "connecting"
        info = probe(job["url"], headers)
        url, size, ranges = info["final_url"], info["size"], info["ranges"]
        filename = safe_filename(job.get("custom_name") or info["filename"])
        if job.get("custom_name") and "." not in job["custom_name"] and "." in info["filename"]:
            filename += "." + info["filename"].rsplit(".", 1)[-1]
        folder.mkdir(parents=True, exist_ok=True)
        final_guess = unique_path(folder / filename)
        part = Path(str(final_guess) + ".part")
        segs = _plan(size, ranges, connections)
        job["_part"] = str(part)
        state_file = Path(str(part) + ".vgstate")

    job.update(filename_planned=filename, size=size, resumable=bool(ranges),
               category=category_for(filename), title=job.get("title") or filename)
    job.setdefault("title", filename)
    if not part.exists() or not ranges:
        with open(part, "wb") as f:
            if size and ranges:
                f.truncate(size)                # reserve the space

    def save_state():
        with lock:
            data = {"url": url, "size": size, "ranges": ranges, "filename": filename,
                    "segments": [dict(s) for s in segs]}
        try:
            state_file.write_text(json.dumps(data))
        except Exception:
            pass

    errors: list[Exception] = []
    stop = threading.Event()

    def steal_work() -> dict | None:
        """A connection that finished early takes half of the largest remaining part."""
        with lock:
            best, rem = None, 0
            for sg in segs:
                if sg["e"] is None:
                    continue
                r = sg["e"] - (sg["s"] + sg["d"]) + 1
                if r > rem:
                    best, rem = sg, r
            if not best or rem < 2 * MIN_PART:
                return None
            pos = best["s"] + best["d"]
            mid = pos + rem // 2
            new = {"s": mid, "e": best["e"], "d": 0}
            best["e"] = mid - 1
            segs.append(new)
            return new

    def worker(seg: dict):
        tries = 0
        while not stop.is_set():
            with lock:
                pos, end = seg["s"] + seg["d"], seg["e"]
            if end is not None and pos > end:
                nxt = steal_work() if ranges else None
                if nxt is None:
                    return
                seg, tries = nxt, 0
                continue
            try:
                r = _open(url, headers, rng=(pos, end) if ranges else None)
                if ranges and r.status != 206:
                    raise IOError("The server stopped allowing partial downloads")
                with r, open(part, "r+b") as f:
                    f.seek(pos)
                    while not stop.is_set():
                        if job.get("cancel") or job.get("_pause"):
                            stop.set()
                            return
                        with lock:
                            end = seg["e"]
                            cur = seg["s"] + seg["d"]
                        want = CHUNK if end is None else min(CHUNK, end - cur + 1)
                        if want <= 0:
                            break
                        data = r.read(want)
                        if not data:
                            if end is None:             # unknown size: finished
                                with lock:
                                    seg["e"] = seg["s"] + seg["d"] - 1
                            break
                        LIMITER.take(len(data))
                        f.write(data)
                        with lock:
                            seg["d"] += len(data)
                tries = 0
            except Exception as e:
                if stop.is_set():
                    return
                tries += 1
                if tries > 8 or (isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404, 410)):
                    errors.append(e)
                    stop.set()
                    return
                time.sleep(min(2 ** tries, 20))

    threads = []
    with lock:
        todo = [s for s in segs if s["e"] is None or s["s"] + s["d"] <= s["e"]]
    if not todo and ranges:
        todo = []
    for sg in todo:
        t = threading.Thread(target=worker, args=(sg,), daemon=True)
        t.start()
        threads.append(t)

    job["status"] = "downloading"
    last_t, last_b, speed, last_save = time.monotonic(), None, 0.0, 0.0
    while any(t.is_alive() for t in threads):
        time.sleep(0.5)
        with lock:
            done = sum(s["d"] for s in segs)
            seg_map = [[s["s"], s["e"], s["d"]] for s in sorted(segs, key=lambda x: x["s"])]
            active = sum(1 for s in segs if s["e"] is None or s["s"] + s["d"] <= s["e"])
        now = time.monotonic()
        if last_b is not None:
            inst = (done - last_b) / max(now - last_t, 1e-6)
            speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
        last_t, last_b = now, done
        job.update(downloaded=done, percent=round(done * 100 / size, 1) if size else 0,
                   indeterminate=not size, speed_bps=int(speed), connections=min(active, len(threads) or 1),
                   segments=seg_map[:64],
                   eta_s=int((size - done) / speed) if size and speed > 1 else None)
        if now - last_save > 2:
            save_state()
            last_save = now

    with lock:
        done = sum(s["d"] for s in segs)
    job.update(downloaded=done, connections=0, speed_bps=0)
    if job.get("cancel"):
        for p in (part, state_file):
            try:
                p.unlink()
            except Exception:
                pass
        raise InterruptedError("cancelled")
    if job.get("_pause"):
        save_state()
        raise InterruptedError("paused")
    if errors:
        save_state()
        raise errors[0]
    if size is not None and done < size:
        save_state()
        raise IOError(f"Download stopped early ({done} of {size} bytes). Press Resume to continue.")
    if size is None:
        with open(part, "r+b") as f:
            f.truncate(done)
    final = unique_path(folder / filename)
    part.replace(final)
    try:
        state_file.unlink()
    except Exception:
        pass
    job.update(file=str(final), files=[str(final)], size=done, percent=100, segments=[[0, max(done - 1, 0), done]])
