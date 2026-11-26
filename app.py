"""
VDM - download YouTube, Instagram and Pinterest videos (and much more) in one place.

Run:  python app.py      -> opens http://127.0.0.1:5757 in your browser
      python app.py --fix-old   -> convert old downloads so QuickTime / Preview can play them
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

# Use certifi's certificates (fixes CERTIFICATE_VERIFY_FAILED on macOS python.org installs)
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass


FROZEN = getattr(sys, "frozen", False)          # True when running as VDM.exe
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ENGINE_DIR = Path.home() / ".vdm" / "engine"


def use_latest_engine():
    """VDM.exe only: keep the download engine (yt-dlp + yt-dlp-ejs) up to date.
    Sites like YouTube change often, so a frozen copy would soon stop working.
    Checks PyPI at most once a day, verifies each download's SHA-256, and falls back
    to the built-in engine if anything goes wrong."""
    if not FROZEN or os.environ.get("VIDEOGRAB_NO_UPDATE"):
        return
    import hashlib, io, json as _json, ssl, urllib.request, zipfile
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    current, stamp = ENGINE_DIR / "current.txt", ENGINE_DIR / "checked"

    def activate():
        try:
            d = ENGINE_DIR / current.read_text().strip()
            if (d / "yt_dlp" / "__init__.py").exists():
                sys.path.insert(0, str(d))
                return True
        except Exception:
            pass
        return False

    if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400 and activate():
        return
    try:
        ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))

        def pypi(name, version=None):
            url = f"https://pypi.org/pypi/{name}/{version + '/' if version else ''}json"
            with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
                return _json.load(r)

        def fetch_wheel(meta, into, top):
            w = next(u for u in meta["urls"] if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("-none-any.whl"))
            with urllib.request.urlopen(w["url"], timeout=90, context=ctx) as r:
                data = r.read()
            if hashlib.sha256(data).hexdigest() != w["digests"]["sha256"]:
                raise ValueError(f"checksum mismatch for {w['filename']}")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if n.startswith(top + "/"):
                        z.extract(n, into)

        meta = pypi("yt-dlp")
        ver = meta["info"]["version"]
        target = ENGINE_DIR / ver
        if not (target / "yt_dlp" / "__init__.py").exists():
            print(f"  Updating the download engine to {ver} ...", flush=True)
            tmp = ENGINE_DIR / (ver + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            fetch_wheel(meta, tmp, "yt_dlp")
            # the YouTube helper scripts must match this yt-dlp version
            req = [r for r in (meta["info"].get("requires_dist") or []) if r.startswith("yt-dlp-ejs")]
            m = re.search(r"==\s*([\w.]+)", req[0]) if req else None
            if m:
                fetch_wheel(pypi("yt-dlp-ejs", m.group(1)), tmp, "yt_dlp_ejs")
            shutil.rmtree(target, ignore_errors=True)
            tmp.rename(target)
            for old in ENGINE_DIR.iterdir():
                if old.is_dir() and old.name != ver:
                    shutil.rmtree(old, ignore_errors=True)
        current.write_text(ver)
        stamp.write_text(ver)
    except Exception as e:
        print(f"  (Couldn't check for engine updates: {e}. Using the built-in engine.)", flush=True)
    activate()


def import_engine():
    """Import yt-dlp; if an updated copy can't load, fall back to the built-in one."""
    use_latest_engine()
    try:
        import yt_dlp as y
        from yt_dlp.utils import download_range_func as d, DownloadCancelled as c
        return y, d, c
    except Exception as e:
        print(f"  (Updated engine failed to load: {e}. Using the built-in engine.)", flush=True)
        sys.path[:] = [p for p in sys.path if not p.startswith(str(ENGINE_DIR))]
        for k in [k for k in sys.modules if k.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
            del sys.modules[k]
        import yt_dlp as y
        from yt_dlp.utils import download_range_func as d, DownloadCancelled as c
        return y, d, c


def deno_path():
    """The JavaScript engine yt-dlp needs for YouTube (bundled in the .exe, or from pip)."""
    for cand in (BUNDLE / "deno.exe", BUNDLE / "deno"):
        if FROZEN and cand.exists():
            return str(cand)
    try:
        import deno
        return deno.find_deno_bin()
    except Exception:
        return shutil.which("deno")


from flask import Flask, jsonify, request, send_file, Response
import downloader as dl
yt_dlp, download_range_func, DownloadCancelled = import_engine()

APP_PORT = 5757  # fixed port the browser extension talks to (5000 clashes with macOS AirPlay)
APP_VERSION = "1.0.0-beta"
VERSION_LABEL = "1.0 Beta"
PHONE_PORT = 5758  # used only when "Phone access" is switched on
HERE = BUNDLE
DATA_DIR = Path.home() / ".vdm"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.json"
DEFAULT_DIR = Path.home() / "Downloads" / "VDM"
# VDM used to be called VideoGrab: bring over its settings and download history once
_OLD = Path.home() / ".videograb"
if not DATA_DIR.exists() and _OLD.exists():
    try:
        DATA_DIR.mkdir(parents=True)
        for _n in ("settings.json", "history.json"):
            if (_OLD / _n).exists():
                shutil.copy2(_OLD / _n, DATA_DIR / _n)
    except Exception:
        pass
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


# ---------------- settings & history ----------------
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get_settings() -> dict:
    s = load_json(SETTINGS_FILE, {})
    s.setdefault("download_dir", str(DEFAULT_DIR))
    s.setdefault("max_concurrent", 3)          # downloads running at the same time (the rest wait in the queue)
    s.setdefault("connections", 8)             # connections per file (IDM-style acceleration)
    s.setdefault("speed_limit_kbps", 0)        # 0 = no limit
    return s


def download_dir() -> Path:
    d = Path(get_settings()["download_dir"]).expanduser()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = DEFAULT_DIR
        d.mkdir(parents=True, exist_ok=True)
    return d


HISTORY: list[dict] = load_json(HISTORY_FILE, [])
for _h in HISTORY:   # downloads that were running when VDM was closed can be resumed
    if _h.get("status") not in ("done", "error", "cancelled", "paused"):
        _h["status"] = "paused"


def remember(job: dict):
    """Store a finished job in the persistent history (newest first, max 200)."""
    keep = {k: job.get(k) for k in ("id", "url", "title", "platform", "mode", "status", "file", "files",
                                    "error", "warning", "ts", "trim", "folder", "thumbnail", "size",
                                    "source", "request", "done_ts", "kind", "category", "part",
                                    "resumable", "downloaded", "custom_name", "queued_ts", "percent")}
    keep["part"] = job.get("_part") or job.get("part")
    if keep.get("request"):     # never store browser cookies on disk
        keep["request"] = {k: v for k, v in keep["request"].items() if k not in ("headers",)}
    with LOCK:
        HISTORY[:] = [h for h in HISTORY if h.get("id") != job["id"]]
        HISTORY.insert(0, keep)
        del HISTORY[200:]
        save_json(HISTORY_FILE, HISTORY)


# ---------------- helpers ----------------
def ffmpeg_path():
    """Use system ffmpeg if installed, otherwise the bundled one from imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if "pinterest." in u or "pin.it" in u:
        return "Pinterest"
    return "Other"


def parse_time(value) -> float | None:
    """'90', '1:30', '01:02:03', '1m30s', '1h2m', '75.5' -> seconds. Empty -> None."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return float(v)
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}(\.\d+)?", v):
        parts = [float(p) for p in v.split(":")]
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
        return secs
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", v)
    if m and any(m.groups()):
        h, mi, s = (float(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + s
    raise ValueError(f"Can't read the time “{value}”. Use e.g. 1:30 or 0:01:30")


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m{x:02d}s" if h else f"{m}m{x:02d}s"


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:120]


def base_opts(cookies_browser: str | None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_path(),
        "windowsfilenames": True,
        "noprogress": True,
    }
    dp = deno_path()
    if dp:
        opts["js_runtimes"] = {"deno": {"path": dp}}
    if cookies_browser and cookies_browser != "none":
        # lets Instagram / age-restricted videos work using your browser login
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def format_selector(mode: str, quality: str) -> str:
    if mode == "audio":
        return "bestaudio[acodec^=mp4a]/bestaudio/best"
    if quality in (None, "", "best"):
        return "bv*+ba/b"
    h = int(quality)
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"


def format_sort(quality):
    """Prefer H.264 video + AAC audio (plays in QuickTime/Preview).
    For 1440p/4K, YouTube has no H.264, so prefer resolution and convert afterwards."""
    q = str(quality or "best").rstrip("p")
    if q.isdigit() and int(q) > 1080:
        return [f"res:{q}", "vcodec:h264", "acodec:aac"]
    return ["vcodec:h264", f"res:{q}" if q.isdigit() else "res", "acodec:aac"]


def probe_codecs(path, ffmpeg):
    """Return (video_codec, audio_codec) using 'ffmpeg -i' (no ffprobe needed)."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="ignore")
    v = re.search(r"Stream #.*?Video: (\w+)", r.stderr)
    a = re.search(r"Stream #.*?Audio: (\w+)", r.stderr)
    return (v.group(1).lower() if v else None, a.group(1).lower() if a else None)


def media_duration(path, ffmpeg):
    """Length in seconds of an audio/video file (None if it has no playable media)."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="ignore")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def make_mac_compatible(path, ffmpeg):
    """Re-encode a downloaded video so QuickTime / Preview can play it.
    Returns the final path (same name, .mp4). Does nothing if already compatible."""
    path = Path(path)
    if not ffmpeg or not path.exists() or path.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
        return path
    vcodec, acodec = probe_codecs(path, ffmpeg)
    if vcodec is None:
        return path
    v_ok = vcodec in ("h264", "hevc")
    a_ok = acodec in (None, "aac", "mp3", "alac")
    if v_ok and a_ok and path.suffix.lower() == ".mp4" and vcodec == "h264":
        return path
    out = path.with_name(path.stem + ".mac-tmp.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?"]
    if vcodec == "h264":
        cmd += ["-c:v", "copy"]
    elif vcodec == "hevc":
        cmd += ["-c:v", "copy", "-tag:v", "hvc1"]  # QuickTime needs the hvc1 tag
    else:  # vp9 / av1 / others -> H.264
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy"] if a_ok else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise RuntimeError("Converting for QuickTime failed: " + r.stderr[-500:])
    final = path.with_suffix(".mp4")
    path.unlink(missing_ok=True)
    out.replace(final)
    return final


def clean_error(e) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    low = msg.lower()
    if any(k in low for k in ("login", "cookies", "private", "rate-limit", "sign in", "confirm you")):
        msg += ("\n\nTip: choose your browser under 'Login from' "
                "(and make sure you're logged in to the site in that browser).")
    elif "drm" in low:
        msg = ("This video is protected with DRM (copy protection), so it can't be downloaded. "
               "This is common on Netflix, Prime Video, Hotstar, Disney+ and similar paid services.")
    elif "403" in low and "forbidden" in low:
        msg += ("\n\nTip: the site refused the download. If this came from a stream found on a web page, "
                "download it from the extension while that page is open, or set 'Login from' to your browser.")
    elif "unsupported url" in low:
        msg += "\n\nTip: check the link is a direct link to a video, reel, post or pin."
    return msg


# ---------------- the download job ----------------
def new_job(data: dict) -> dict:
    url = data["url"]
    job = {
        "id": uuid.uuid4().hex[:10], "url": url, "platform": detect_platform(url),
        "title": data.get("title") or "", "mode": data.get("mode", "video"),
        "status": "queued", "percent": 0, "speed": "", "eta": "", "file": None, "files": [],
        "error": None, "warning": None, "ts": time.time(), "cancel": False, "trim": None,
        "folder": str(download_dir()), "thumbnail": data.get("thumbnail") or None, "size": None,
        "source": data.get("source") or "app",
        "request": {k: v for k, v in data.items() if k not in ("source",)},
        "kind": data.get("kind") or "video",
        "category": data.get("category") or ("Music" if data.get("mode") == "audio" else "Video"),
        "queued_ts": time.time(),
    }
    JOBS[job["id"]] = job
    return job


def build_opts(job: dict, data: dict, finals: list, extras: bool = True) -> dict:
    mode = data.get("mode", "video")
    quality = str(data.get("quality", "best")).rstrip("p") or "best"
    audio_fmt = data.get("audio_format", "mp3")
    mac_compat = data.get("mac_compat", True)
    folder = Path(job["folder"])

    def hook(d):
        if job["cancel"] or job.get("_pause"):
            raise DownloadCancelled("Cancelled")
        for k in ("tmpfilename", "filename"):
            if d.get(k):
                job.setdefault("_tmp", set()).add(d[k])
        inf = d.get("info_dict") or {}
        if inf:
            if not job["title"] and inf.get("title") and not re.fullmatch(r"(master|index|playlist|manifest)", inf["title"]):
                job["title"] = inf["title"]
            if not job.get("thumbnail") and inf.get("thumbnail"):
                job["thumbnail"] = inf["thumbnail"]
        if d.get("total_bytes") or d.get("total_bytes_estimate"):
            job["size"] = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            pct = round(done * 100 / total, 1) if total else None
            job.update(status="downloading", percent=pct if pct is not None else job["percent"],
                       speed=(d.get("_speed_str") or "").strip(), eta=(d.get("_eta_str") or "").strip(),
                       indeterminate=pct is None)
        elif d["status"] == "finished":
            job.update(status="processing", percent=100)

    # ---- file name ----
    custom = safe_name(data.get("filename") or "")
    stem = custom or "%(title).80s [%(id)s]"
    if data.get("all_items"):
        stem += " %(playlist_index|)s"
    if mode == "video" and quality != "best":
        stem += f" [{quality}p]"
    start, end = parse_time(data.get("start")), parse_time(data.get("end"))
    if start is not None or end is not None:
        start = start or 0.0
        if end is not None and end <= start:
            raise ValueError("The end time must be after the start time.")
        job["trim"] = f"{fmt_time(start)}–{fmt_time(end) if end is not None else 'end'}"
        job["_cut"] = f" (cut {fmt_time(start)}-{fmt_time(end) if end is not None else 'end'})"
        stem += job["_cut"]

    if mode == "audio":
        # Separate working name, so making an MP3 never overwrites or deletes a video with the same name
        stem += AUDIO_TAG
    opts = base_opts(data.get("cookies"))
    lim = int(get_settings().get("speed_limit_kbps") or 0)
    if lim:
        opts["ratelimit"] = lim * 1024
    referer = (data.get("referer") or "").strip()
    if referer.startswith("http"):
        # streams found on a web page usually only play when they're asked for "from" that page
        from urllib.parse import urlsplit
        o = urlsplit(referer)
        opts["http_headers"] = {"Referer": referer, "Origin": f"{o.scheme}://{o.netloc}"}
    opts.update({
        "format": format_selector(mode, quality),
        "format_sort": format_sort(quality) if (mac_compat and mode == "video") else [],
        "outtmpl": {"default": str(folder / f"{stem}.%(ext)s"),
                    "thumbnail": str(folder / f"{stem}.%(ext)s"),
                    "subtitle": str(folder / f"{stem}.%(ext)s")},
        "progress_hooks": [hook],
        "post_hooks": [lambda fp: finals.append(fp)],
        "noplaylist": not data.get("all_items", False),
        "postprocessors": [],
    })

    # ---- trim: only download the chosen part ----
    if start is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end if end is not None else float("inf"))])
        opts["force_keyframes_at_cuts"] = bool(data.get("precise", True))

    pps = opts["postprocessors"]
    if mode == "audio":
        pps.append({"key": "FFmpegExtractAudio", "preferredcodec": audio_fmt, "preferredquality": "192"})
        pps.append({"key": "FFmpegMetadata", "add_metadata": True})   # title / artist tags
    else:
        opts["merge_output_format"] = "mp4"

    if extras:
        # ---- subtitles as .srt files next to the video ----
        subs = data.get("subtitles", "off")
        if subs and subs != "off" and mode == "video":
            opts.update({"writesubtitles": True, "writeautomaticsub": True,
                         "subtitleslangs": ["en.*", "en"] if subs == "en" else ["all", "-live_chat"],
                         "subtitlesformat": "srt/vtt/best"})
            if subs != "en":
                opts["writeautomaticsub"] = False   # 'all' auto-translations would be 100+ files
            pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
        # ---- cover image ----
        cover = bool(data.get("save_thumbnail"))
        embed = mode == "audio" and audio_fmt in ("mp3", "m4a") and data.get("embed_cover", True)
        if cover or embed:
            opts["writethumbnail"] = True
            pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        if embed:
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": cover})
    return opts


AUDIO_TAG = ".vg-audio"


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
    return p


def finish_audio_names(files: list[Path]) -> list[Path]:
    """'Song.vg-audio.mp3' -> 'Song.mp3' (and its cover image), without overwriting anything."""
    out = []
    for f in files:
        if AUDIO_TAG in f.name:
            for sib in f.parent.glob(f.name.split(AUDIO_TAG)[0].replace("[", "[[]") + AUDIO_TAG + ".*"):
                if sib != f and sib.suffix.lower() in (".jpg", ".png", ".webp"):
                    sib.replace(unique_path(sib.with_name(sib.name.replace(AUDIO_TAG, ""))))
            target = unique_path(f.with_name(f.name.replace(AUDIO_TAG, "")))
            f.replace(target)
            f = target
        out.append(f)
    return out


def cleanup_leftovers(job: dict):
    """Remove half-finished and temporary files after a cancelled or failed download."""
    for t in job.get("_tmp", ()):
        tp = Path(t)
        base = Path(t[:-5]) if t.endswith(".part") else tp
        for p in (tp, Path(t + ".ytdl"), Path(str(base) + ".part"), Path(str(base) + ".ytdl"), base):
            try:
                # .part/.ytdl are always scraps; a finished file is only removed if it's our audio working copy
                if p.exists() and (p.suffix in (".part", ".ytdl") or AUDIO_TAG in p.name):
                    p.unlink()
            except Exception:
                pass
    # a failed trim can leave an empty piece that never reported its name
    cut = job.get("_cut")
    if cut:
        try:
            for p in Path(job["folder"]).glob("*.part"):
                if cut in p.name and p.stat().st_size == 0 and p.stat().st_mtime >= job["ts"] - 1:
                    p.unlink()
        except Exception:
            pass


def run_job(job: dict, data: dict):
    mode = job["mode"]
    mac_compat = data.get("mac_compat", True)
    attempt_extras = True
    while True:
        finals: list[str] = []
        try:
            opts = build_opts(job, data, finals, extras=attempt_extras)
            job["status"] = "starting"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job["url"], download=True)
            entries = [e for e in (info.get("entries") or []) if e] if info.get("entries") is not None else [info]
            if not job["title"]:
                job["title"] = info.get("title") or (entries[0].get("title") if entries else "") or ""
            files = [Path(f) for f in dict.fromkeys(finals) if Path(f).exists()]
            if not files:  # e.g. "already downloaded" – find it on disk
                for e in entries:
                    for key in ("filepath", "_filename"):
                        p = e.get("requested_downloads", [{}])[0].get(key) if e.get("requested_downloads") else e.get(key)
                        if p and Path(p).exists():
                            files.append(Path(p))
                            break
            # make sure we really got something playable (e.g. a trim past the end gives an empty file)
            ff = ffmpeg_path()
            media_ext = (".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".wav", ".opus", ".ogg")
            empty = [f for f in files if f.suffix.lower() in media_ext and ff and not (media_duration(f, ff) or 0) > 0]
            if empty:
                for f in empty:
                    f.unlink(missing_ok=True)
                files = [f for f in files if f not in empty]
                if not files:
                    raise RuntimeError("ffmpeg exited: empty result" if job.get("trim") else
                                       "The download finished but the file was empty. Please try again.")
            if mode == "audio":
                files = finish_audio_names(files)
            if mode == "video" and mac_compat:
                job["status"] = "converting"
                files = [make_mac_compatible(f, ffmpeg_path()) if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov") else f
                         for f in files]
            job.update(status="done", percent=100, files=[str(f) for f in files],
                       file=str(files[0]) if files else None, done_ts=time.time(),
                       size=sum(f.stat().st_size for f in files if f.exists()) or job.get("size"))
            break
        except DownloadCancelled:
            if job.get("_pause") and not job["cancel"]:
                job.update(status="paused", speed="", eta="")     # keep the .part file: yt-dlp continues it
                job["_pause"] = False
                remember(job)
                return
            cleanup_leftovers(job)
            job.update(status="cancelled", error="Cancelled")
            break
        except Exception as e:
            msg = str(e).lower()
            # subtitle / cover image problems shouldn't cost you the video: retry without them
            if attempt_extras and any(k in msg for k in ("subtitle", "thumbnail", "embedthumbnail")):
                attempt_extras = False
                job["warning"] = "Subtitles or the cover image weren't available, so the video was saved without them."
                continue
            cleanup_leftovers(job)
            if job.get("trim") and ("ffmpeg exited" in msg or "postprocessing" in msg):
                job.update(status="error", error=f"Couldn't cut {job['trim']}. Check the trim times - "
                                                  "the start may be after the end of the video.")
            else:
                job.update(status="error", error=clean_error(e))
            break
    job["cancel"] = False
    remember(job)


def human_size(b) -> str:
    b = float(b or 0)
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return f"{b:.0f} {u}" if u in ("B", "KB") else f"{b:.1f} {u}"
        b /= 1024
    return ""


def fmt_eta(s) -> str:
    if s is None:
        return ""
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------------- queue (like IDM: only N downloads run at once, the rest wait) ----------------
RUNNING_STATES = ("starting", "connecting", "downloading", "processing", "converting", "cancelling")
QLOCK = threading.Lock()


def pump():
    """Start waiting downloads while there are free slots."""
    with QLOCK:
        limit = max(1, int(get_settings().get("max_concurrent") or 3))
        running = sum(1 for j in JOBS.values() if j["status"] in RUNNING_STATES)
        waiting = sorted((j for j in JOBS.values() if j["status"] == "queued"), key=lambda j: j.get("queued_ts", 0))
        for j in waiting[:max(0, limit - running)]:
            j["status"] = "starting"
            target = run_file_job if j.get("kind") == "file" else run_job_then_pump
            threading.Thread(target=target, args=(j, j["_data"]), daemon=True).start()


def enqueue(job: dict, data: dict):
    job["_data"] = data
    job["status"] = "queued"
    job["queued_ts"] = time.time()
    job["cancel"] = False
    job["_pause"] = False
    pump()


def run_job_then_pump(job, data):
    try:
        run_job(job, data)
    finally:
        pump()


def run_file_job(job: dict, data: dict):
    """Any file (zip, pdf, exe, ...) with several connections at once."""
    s = get_settings()
    dl.LIMITER.rate = int(s.get("speed_limit_kbps") or 0) * 1024
    headers = dict(data.get("headers") or {})
    ua = headers.pop("user_agent", None)
    if ua:
        headers["User-Agent"] = ua
    if data.get("referer"):
        headers["Referer"] = data["referer"]
    try:
        remember(job)
        dl.run_file_download(job, headers, Path(job["folder"]), int(data.get("connections") or s.get("connections") or 8),
                             unique_path)
        job.update(status="done", done_ts=time.time(), error=None)
    except InterruptedError as e:
        if str(e) == "paused":
            job.update(status="paused")
            job["_pause"] = False
        else:
            job.update(status="cancelled", error="Cancelled")
    except Exception as e:
        msg = str(e)
        if "403" in msg or "401" in msg:
            msg += ("\nThe site refused the download. Start it from the browser with the VDM add-on, "
                    "so your login is passed along.")
        job.update(status="error", error=msg, resumable=bool(job.get("_part")))
    finally:
        job["cancel"] = False
        remember(job)
        pump()


def public(job: dict) -> dict:
    out = {k: v for k, v in job.items() if k != "cancel" and not k.startswith("_")}
    out["filename"] = Path(job["file"]).name if job.get("file") else (job.get("filename_planned") or None)
    if job.get("speed_bps") is not None and job.get("status") == "downloading":
        out["speed"] = human_size(job["speed_bps"]) + "/s" if job["speed_bps"] else ""
        out["eta"] = fmt_eta(job.get("eta_s"))
    out["exists"] = bool(job.get("file") and Path(job["file"]).exists())
    return out


# ---------------- security ----------------
# The computer itself (127.0.0.1) can use everything. Browser extensions may call the API.
# Phones on your Wi-Fi only get in when "Phone access" is on AND they know the secret key from the QR code.
ALLOWED_ORIGIN_PREFIXES = ("chrome-extension://", "moz-extension://", "safari-web-extension://",
                           "extension://")
LOCAL_HOSTS = {f"127.0.0.1:{APP_PORT}", f"localhost:{APP_PORT}", f"127.0.0.1:{PHONE_PORT}", f"localhost:{PHONE_PORT}"}
# things a phone must never do on your computer
LOCAL_ONLY = ("/api/settings", "/api/choose-folder", "/api/open-folder", "/api/open-file", "/api/clear-history",
              "/api/phone", "/api/extension", "/api/reveal-extension", "/api/remove", "/api/delete",
              "/api/pause-all", "/api/resume-all")


def is_local_request():
    return request.remote_addr in ("127.0.0.1", "::1")


def host_is_ip(host: str) -> bool:
    h = host.rsplit(":", 1)[0].strip("[]")
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def phone_key_ok() -> bool:
    s = get_settings()
    key = request.args.get("k") or request.cookies.get("vgk") or request.headers.get("X-VideoGrab-Key") or ""
    return bool(s.get("phone_enabled") and s.get("phone_key") and key and secrets.compare_digest(key, s["phone_key"]))


@app.before_request
def guard():
    p = request.path
    local = is_local_request()
    if local:
        # blocks "DNS rebinding" tricks where a website pretends to be 127.0.0.1
        if request.host not in LOCAL_HOSTS and not host_is_ip(request.host):
            return jsonify(error="Forbidden host"), 403
    else:
        if not host_is_ip(request.host) or not phone_key_ok():
            return Response("VDM: phone access is off or this link is old. "
                            "Scan the QR code in VDM on your computer again.", 403, mimetype="text/plain")
        if p.startswith(LOCAL_ONLY):
            return jsonify(error="Not allowed from a phone"), 403
        if p == "/":
            return redirect_to_mobile()
    if not p.startswith("/api/"):
        return None
    origin = request.headers.get("Origin")
    same_origin = origin == f"{request.scheme}://{request.host}"
    if origin and not (same_origin or origin.startswith(ALLOWED_ORIGIN_PREFIXES)):
        return jsonify(error="Forbidden origin"), 403
    if request.method == "OPTIONS":
        return ("", 204)
    # A custom header can't be sent cross-site without a CORS preflight, which websites fail.
    if request.method == "POST" and request.headers.get("X-VideoGrab") != "1":
        return jsonify(error="Missing X-VideoGrab header"), 403
    return None


def redirect_to_mobile():
    from flask import redirect
    return redirect("/m")


@app.after_request
def cors(resp):
    origin = request.headers.get("Origin")
    if origin and origin.startswith(ALLOWED_ORIGIN_PREFIXES):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-VideoGrab"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    # remember the phone key in a cookie so the phone page's links keep working
    if request.args.get("k") and not is_local_request() and phone_key_ok():
        resp.set_cookie("vgk", request.args["k"], max_age=31536000, httponly=True, samesite="Strict")
    return resp


# ---------------- phone access (same Wi-Fi) ----------------
PHONE = {"server": None, "thread": None, "error": None}


def lan_ips() -> list[str]:
    ips = []
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))          # no data is sent; just picks the Wi-Fi/LAN address
        ips.append(sk.getsockname()[0])
        sk.close()
    except Exception:
        pass
    try:
        for inf in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if inf[4][0] not in ips:
                ips.append(inf[4][0])
    except Exception:
        pass
    return [i for i in ips if not i.startswith(("127.", "169.254."))]


def start_phone_server() -> str | None:
    if PHONE["server"]:
        return None
    try:
        from werkzeug.serving import make_server
        srv = make_server("0.0.0.0", PHONE_PORT, app, threaded=True)
    except Exception as e:
        PHONE["error"] = f"Couldn't open port {PHONE_PORT}: {e}"
        return PHONE["error"]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    PHONE.update(server=srv, thread=t, error=None)
    return None


def stop_phone_server():
    srv = PHONE.get("server")
    if srv:
        threading.Thread(target=srv.shutdown, daemon=True).start()
    PHONE.update(server=None, thread=None)


def phone_info() -> dict:
    s = get_settings()
    ips = lan_ips()
    url = f"http://{ips[0]}:{PHONE_PORT}/m?k={s.get('phone_key', '')}" if ips and s.get("phone_key") else None
    svg = None
    if url and s.get("phone_enabled"):
        try:
            import qrcode
            import qrcode.image.svg
            img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
            svg = img.to_string(encoding="unicode")
        except Exception as e:
            print(f"  (QR code couldn't be made: {e})", flush=True)
            svg = None
    return {"enabled": bool(s.get("phone_enabled")), "running": bool(PHONE["server"]), "url": url,
            "other_urls": [f"http://{i}:{PHONE_PORT}/m?k={s.get('phone_key', '')}" for i in ips[1:]],
            "qr_svg": svg, "error": PHONE["error"]}


# ---------------- browser add-on helper ----------------
def extension_dir(browser: str) -> Path:
    name = "extension-firefox" if browser == "firefox" else "extension-chrome"
    src = HERE / name
    if not FROZEN:
        return src
    # In VDM.exe the add-on lives inside the app; copy it somewhere permanent and easy to find,
    # because the browser keeps loading it from that folder.
    dst = Path.home() / "VDM Add-on" / ("Firefox" if browser == "firefox" else "Chrome, Edge, Brave, Opera")
    try:
        if not (dst / "manifest.json").exists() or (dst / "manifest.json").read_bytes() != (src / "manifest.json").read_bytes() \
                or any(not (dst / f.relative_to(src)).exists() for f in src.rglob("*") if f.is_file()):
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        return dst
    except Exception:
        return src


# ---------------- API ----------------
@app.get("/api/ping")
def api_ping():
    return jsonify(app="VDM", version=APP_VERSION, version_label=VERSION_LABEL, folder=str(download_dir()),
                   engine=yt_dlp.version.__version__, engine_path=str(Path(yt_dlp.__file__).parent),
                   youtube_js=bool(deno_path()), ffmpeg=bool(ffmpeg_path()), packaged=bool(FROZEN))


@app.get("/api/settings")
def api_get_settings():
    return jsonify(get_settings() | {"default_dir": str(DEFAULT_DIR)})


@app.post("/api/settings")
def api_set_settings():
    data = request.get_json(force=True)
    s = get_settings()
    if "download_dir" in data:
        raw = (data["download_dir"] or "").strip() or str(DEFAULT_DIR)
        d = Path(raw).expanduser()
        if not d.is_absolute():
            return jsonify(error="Please use a full folder path, e.g. /Users/you/Movies"), 400
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".videograb-write-test"
            probe.write_text("ok")
            probe.unlink()
        except Exception as e:
            return jsonify(error=f"Can't save to that folder: {e}"), 400
        s["download_dir"] = str(d)
    for k, lo, hi in (("max_concurrent", 1, 10), ("connections", 1, 16), ("speed_limit_kbps", 0, 10_000_000)):
        if k in data:
            try:
                s[k] = max(lo, min(hi, int(data[k])))
            except (TypeError, ValueError):
                return jsonify(error=f"{k} must be a number"), 400
    save_json(SETTINGS_FILE, s)
    dl.LIMITER.rate = int(s.get("speed_limit_kbps") or 0) * 1024
    pump()
    return jsonify(s)


def pick_folder_dialog(start: str) -> str | None:
    """Show the computer's own 'choose folder' window. Returns a path, or None if cancelled."""
    if sys.platform == "darwin":
        start_as = start.replace("\\", "\\\\").replace('"', '\\"')
        script = (f'set p to POSIX path of (choose folder with prompt "Where should VDM save downloads?" '
                  f'default location (POSIX file "{start_as}"))\nreturn p')
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    if os.name == "nt":
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$f.Description='Where should VDM save downloads?';$f.ShowNewFolderButton=$true;"
              "$f.SelectedPath='" + start.replace("'", "''") + "';"
              "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
              "if($f.ShowDialog($o) -eq 'OK'){[Console]::Out.Write($f.SelectedPath)}")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.strip() or None
    if shutil.which("zenity"):
        r = subprocess.run(["zenity", "--file-selection", "--directory", f"--filename={start}/"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None
    code = ("import tkinter as t,tkinter.filedialog as f;r=t.Tk();r.withdraw();r.attributes('-topmost',1);"
            f"print(f.askdirectory(initialdir={start!r}) or '')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip() or None


@app.post("/api/choose-folder")
def api_choose_folder():
    try:
        path = pick_folder_dialog(str(download_dir()))
    except Exception as e:
        return jsonify(error=f"Couldn't open the folder picker ({e}). Type the folder path instead."), 500
    if not path:
        return jsonify(cancelled=True)
    s = get_settings()
    s["download_dir"] = str(Path(path))
    save_json(SETTINGS_FILE, s)
    return jsonify(s)


@app.post("/api/info")
def api_info():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="Please paste a link."), 400
    try:
        o = base_opts(data.get("cookies")) | {"extract_flat": "in_playlist"}
        ref = (data.get("referer") or "").strip()
        if ref.startswith("http"):
            o["http_headers"] = {"Referer": ref}
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error=clean_error(e)), 400
    entries = info.get("entries")
    entries = list(entries) if entries is not None else None
    first = (entries[0] if entries else info) or info
    heights = sorted({f.get("height") for f in (first.get("formats") or [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    return jsonify(
        platform=detect_platform(url),
        title=info.get("title") or first.get("title") or "Untitled",
        uploader=info.get("uploader") or first.get("uploader") or "",
        thumbnail=info.get("thumbnail") or first.get("thumbnail") or
                  ((first.get("thumbnails") or [{}])[-1].get("url")),
        duration=first.get("duration"),
        heights=heights,
        items=len(entries) if entries else 1,
        has_subtitles=bool(first.get("subtitles")) or bool(first.get("automatic_captions")),
    )


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="Please paste a link."), 400
    try:   # validate times now so mistakes show immediately
        s, e = parse_time(data.get("start")), parse_time(data.get("end"))
        if s is not None and e is not None and e <= s:
            raise ValueError("The end time must be after the start time.")
    except ValueError as err:
        return jsonify(error=str(err)), 400
    data["url"] = url
    job = new_job(data)
    enqueue(job, data)
    return jsonify(job_id=job["id"], folder=job["folder"])


@app.get("/api/progress/<job_id>")
def api_progress(job_id):
    job = JOBS.get(job_id) or next((h for h in HISTORY if h.get("id") == job_id), None)
    if not job:
        return jsonify(error="Unknown download"), 404
    return jsonify(public(job))


@app.post("/api/cancel/<job_id>")
def api_cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown download"), 404
    if job["status"] in ("queued", "paused", "error"):
        drop_partial(job)
        job.update(status="cancelled", error="Cancelled")
        remember(job)
    elif job["status"] not in ("done", "cancelled"):
        job["cancel"] = True
        job["status"] = "cancelling"
    return jsonify(ok=True)


def drop_partial(job):
    part = job.get("_part") or job.get("part")
    if part and job.get("kind") == "file":
        for p in (Path(part), Path(part + ".vgstate")):
            try:
                p.unlink()
            except Exception:
                pass
    else:
        cleanup_leftovers(job)


def load_job(job_id):
    """Get a job, bringing it back from history (e.g. a paused download after a restart)."""
    if job_id in JOBS:
        return JOBS[job_id]
    h = next((h for h in HISTORY if h.get("id") == job_id), None)
    if not h:
        return None
    job = dict(h)
    job.update(cancel=False, _pause=False, _part=h.get("part"), speed="", eta="")
    JOBS[job_id] = job
    return job


@app.post("/api/pause/<job_id>")
def api_pause(job_id):
    job = load_job(job_id)
    if not job:
        return jsonify(error="Unknown download"), 404
    if job["status"] == "queued":
        job["status"] = "paused"
    elif job["status"] in RUNNING_STATES:
        job["_pause"] = True
        job["status"] = "pausing"
    remember(job)
    return jsonify(ok=True)


@app.post("/api/resume/<job_id>")
def api_resume(job_id):
    job = load_job(job_id)
    if not job:
        return jsonify(error="Unknown download"), 404
    if job["status"] in ("paused", "error", "cancelled", "pausing"):
        data = job.get("_data") or dict(job.get("request") or {})
        data.setdefault("url", job["url"])
        job.update(error=None, warning=None)
        if job["status"] == "cancelled":
            job["_part"] = None
        enqueue(job, data)
    return jsonify(ok=True)


@app.post("/api/pause-all")
def api_pause_all():
    for j in list(JOBS.values()):
        if j["status"] == "queued":
            j["status"] = "paused"
        elif j["status"] in RUNNING_STATES and j["status"] != "cancelling":
            j["_pause"] = True
            j["status"] = "pausing"
    return jsonify(ok=True)


@app.post("/api/resume-all")
def api_resume_all():
    for h in list(HISTORY):
        if h.get("status") == "paused":
            load_job(h["id"])
    for j in list(JOBS.values()):
        if j["status"] == "paused":
            data = j.get("_data") or dict(j.get("request") or {})
            data.setdefault("url", j["url"])
            enqueue(j, data)
    return jsonify(ok=True)


@app.post("/api/fetch")
def api_fetch():
    """Download ANY file (zip, pdf, exe, music, images ...) with several connections - like IDM."""
    data = request.get_json(force=True)
    urls = [u.strip() for u in re.split(r"[\s]+", data.get("url") or "") if u.strip().startswith(("http://", "https://", "ftp://"))]
    if not urls:
        return jsonify(error="Please enter a link that starts with http:// or https://"), 400
    ids = []
    for i, u in enumerate(urls):
        d = dict(data, url=u, kind="file", source=data.get("source") or "app")
        name = (data.get("filename") or "").strip()
        if name and len(urls) > 1:
            name = f"{name} {i + 1}"
        guess = dl.safe_filename(name) if name else u.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        d["category"] = dl.category_for(guess)
        job = new_job(d)
        job.update(custom_name=name or None, title=data.get("title") or "", filename_planned=name or None)
        if data.get("folder"):
            job["folder"] = str(Path(data["folder"]).expanduser())
        if data.get("start_paused"):
            job["_data"] = d
            job["status"] = "paused"
            remember(job)
        else:
            enqueue(job, d)
        ids.append(job["id"])
    return jsonify(job_ids=ids, job_id=ids[0])


@app.post("/api/delete/<job_id>")
def api_delete(job_id):
    """Remove a download from the list; with {"with_file": true} also delete the file from the computer."""
    job = load_job(job_id)
    if not job:
        return jsonify(error="Unknown download"), 404
    if job["status"] in RUNNING_STATES:
        job["cancel"] = True
        time.sleep(1.0)
    drop_partial(job)
    if (request.get_json(silent=True) or {}).get("with_file"):
        for f in job.get("files") or ([job["file"]] if job.get("file") else []):
            try:
                Path(f).unlink()
            except Exception:
                pass
    JOBS.pop(job_id, None)
    with LOCK:
        HISTORY[:] = [h for h in HISTORY if h.get("id") != job_id]
        save_json(HISTORY_FILE, HISTORY)
    return jsonify(ok=True)


@app.get("/api/jobs")
def api_jobs():
    active = [public(j) for j in JOBS.values() if j["status"] not in ("done", "error", "cancelled")]
    ids = {j["id"] for j in active}
    mem = {j["id"]: j for j in JOBS.values()}
    hist = [public(mem.get(h["id"], h) | {"cancel": False}) for h in HISTORY if h.get("id") not in ids]
    n = 500 if request.args.get("all") else 60
    stats = {"speed_bps": sum(j.get("speed_bps") or 0 for j in JOBS.values() if j["status"] == "downloading"),
             "running": len(active)}
    return jsonify(jobs=sorted(active, key=lambda j: -j["ts"]) + hist[:n], stats=stats,
                   categories=list(dl.CATEGORIES) + ["Other"])


@app.post("/api/clear-history")
def api_clear_history():
    with LOCK:
        HISTORY.clear()
        save_json(HISTORY_FILE, HISTORY)
    return jsonify(ok=True)


def find_job_file(job_id):
    job = JOBS.get(job_id or "") or next((h for h in HISTORY if h.get("id") == job_id), None)
    if job and job.get("file") and Path(job["file"]).exists():
        return job["file"]
    return None


@app.get("/api/file/<job_id>")
def api_file(job_id):
    f = find_job_file(job_id)
    if not f:
        return "File not found", 404
    return send_file(f, as_attachment=request.args.get("inline") != "1", conditional=True)


@app.post("/api/open-folder")
def api_open_folder():
    """Open the downloads folder, or reveal one finished download (job_id) in Finder/Explorer."""
    data = request.get_json(silent=True) or {}
    target = find_job_file(data.get("job_id"))
    folder = str(download_dir())
    try:
        if sys.platform.startswith("win"):
            if target:
                subprocess.Popen(["explorer", "/select,", target])
            else:
                os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target] if target else ["open", folder])
        else:
            subprocess.Popen(["xdg-open", str(Path(target).parent) if target else folder])
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


@app.post("/api/open-file")
def api_open_file():
    """Play / open a finished download with the computer's default app."""
    target = find_job_file((request.get_json(silent=True) or {}).get("job_id"))
    if not target:
        return jsonify(error="That file isn't there anymore."), 404
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


@app.post("/api/retry/<job_id>")
def api_retry(job_id):
    old = JOBS.get(job_id) or next((h for h in HISTORY if h.get("id") == job_id), None)
    if not old or not old.get("request"):
        return jsonify(error="Can't retry this one - please start it again."), 404
    data = dict(old["request"])
    data["source"] = old.get("source") or "app"
    data.setdefault("title", old.get("title") or "")
    job = new_job(data)
    enqueue(job, data)
    return jsonify(job_id=job["id"])


@app.post("/api/remove/<job_id>")
def api_remove(job_id):
    """Remove an entry from the list (the file itself is kept)."""
    job = JOBS.get(job_id)
    if job and job["status"] not in ("done", "error", "cancelled"):
        return jsonify(error="Cancel it first."), 400
    JOBS.pop(job_id, None)
    with LOCK:
        HISTORY[:] = [h for h in HISTORY if h.get("id") != job_id]
        save_json(HISTORY_FILE, HISTORY)
    return jsonify(ok=True)


EXT_SEEN: dict = {}


@app.post("/api/extension-hello")
def api_extension_hello():
    """The browser add-on says hello when it's installed/started, so the app can show 'installed ✓'."""
    d = request.get_json(silent=True) or {}
    EXT_SEEN.update(version=str(d.get("version", ""))[:20], browser=str(d.get("browser", ""))[:40], ts=time.time())
    return jsonify(ok=True, app_version=VERSION_LABEL)


REPO = "PixelMonkLab/VDM-Video-Downloads-Manager-"
STORE_LINKS_URL = f"https://raw.githubusercontent.com/{REPO}/main/store/links.json"
_LINKS = {"t": 0, "data": None}


def store_links() -> dict:
    """One-click install links (add-on stores). Read from GitHub once a day, so new store
    approvals reach every VDM without an update. Falls back to the copy that came with VDM."""
    if _LINKS["data"] is not None and time.time() - _LINKS["t"] < 86400:
        return _LINKS["data"]
    data = {}
    try:
        import ssl, urllib.request
        ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
        with urllib.request.urlopen(STORE_LINKS_URL, timeout=4, context=ctx) as r:
            data = json.load(r)
    except Exception:
        data = load_json(HERE / "store" / "links.json", {}) or load_json(Path(__file__).resolve().parent / "store" / "links.json", {})
    clean = {k: v for k, v in data.items() if k in ("firefox", "edge", "chrome") and isinstance(v, str)
             and v.startswith("https://")}
    _LINKS.update(t=time.time(), data=clean)
    return clean


@app.get("/api/extension")
def api_extension():
    seen = dict(EXT_SEEN, recent=bool(EXT_SEEN) and time.time() - EXT_SEEN.get("ts", 0) < 600) if EXT_SEEN else None
    return jsonify(chrome=str(extension_dir("chrome")), firefox=str(extension_dir("firefox")),
                   version=VERSION_LABEL, seen=seen, store=store_links())


@app.get("/favicon.png")
def favicon():
    f = HERE / "extension-chrome" / "icons" / "128.png"
    return send_file(f) if f.exists() else ("", 404)


@app.post("/api/reveal-extension")
def api_reveal_extension():
    browser = (request.get_json(silent=True) or {}).get("browser", "chrome")
    d = extension_dir(browser)
    target = d / "manifest.json" if browser == "firefox" else d
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(d.parent)])
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True, path=str(d))


@app.get("/api/phone")
def api_phone_get():
    return jsonify(phone_info())


@app.post("/api/phone")
def api_phone_set():
    data = request.get_json(silent=True) or {}
    s = get_settings()
    if data.get("reset") or not s.get("phone_key"):
        s["phone_key"] = secrets.token_urlsafe(9)
    if "enabled" in data:
        s["phone_enabled"] = bool(data["enabled"])
    save_json(SETTINGS_FILE, s)
    if s.get("phone_enabled"):
        err = start_phone_server()
        if err:
            s["phone_enabled"] = False
            save_json(SETTINGS_FILE, s)
    else:
        stop_phone_server()
    return jsonify(phone_info())


@app.get("/manager")
def manager_page():
    html = (HERE / "manager.html").read_text(encoding="utf-8")
    return Response(html.replace("{{VERSION}}", VERSION_LABEL), mimetype="text/html")


@app.get("/m")
def mobile_page():
    html = (HERE / "mobile.html").read_text(encoding="utf-8")
    key = get_settings().get("phone_key", "") if (is_local_request() or phone_key_ok()) else ""
    return Response(html.replace("{{VERSION}}", VERSION_LABEL).replace("{{KEY}}", key), mimetype="text/html")


@app.get("/")
def index():
    html = (HERE / "ui.html").read_text(encoding="utf-8")
    return Response(html.replace("{{PORT}}", str(APP_PORT)).replace("{{VERSION}}", VERSION_LABEL), mimetype="text/html")


def fix_old():
    """Convert videos already in the download folder so QuickTime / Preview can play them."""
    ff = ffmpeg_path()
    folder = download_dir()
    print(f"Checking {folder}\n")
    vids = [p for p in folder.iterdir() if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    for p in vids:
        v, a = probe_codecs(p, ff)
        if v == "h264" and a in (None, "aac", "mp3") and p.suffix.lower() == ".mp4":
            print("  OK        ", p.name)
            continue
        print("  Converting", p.name, f"({v}/{a}) ...", flush=True)
        try:
            print("  Done ->   ", make_mac_compatible(p, ff).name)
        except Exception as e:
            print("  Failed:   ", e)
    print("\nAll done.")


if __name__ == "__main__":
    if "--fix-old" in sys.argv:
        fix_old()
        sys.exit(0)
    url = f"http://127.0.0.1:{APP_PORT}"
    if get_settings().get("phone_enabled"):
        err = start_phone_server()
        print(f"  Phone access: {'on - ' + phone_info()['url'] if not err else err}", flush=True)
    print(f"\n  VDM {VERSION_LABEL} (engine {yt_dlp.version.__version__}) is running at {url}\n  Saving to: {download_dir()}\n  Keep this window open. Close it to stop.\n", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # keep the window tidy
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None
    try:
        app.run(host="127.0.0.1", port=APP_PORT, debug=False, threaded=True)
    except OSError as e:
        print(f"\n  Couldn't start: {e}\n  VDM may already be running - open http://127.0.0.1:{APP_PORT}")
        if FROZEN:
            webbrowser.open(url)
            input("  Press Enter to close...")
