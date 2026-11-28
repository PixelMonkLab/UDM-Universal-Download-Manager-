"""
End-to-end test of a built UDM desktop app on a real Windows / Mac machine (used by GitHub Actions).

    python tools/ci_smoke.py <path to UDM.exe or UDM.app/Contents/MacOS/UDM>

Starts the app, downloads a real file with several connections, pauses and resumes it, tries a
YouTube video (only a warning if YouTube blocks the build machine), quits in the middle of a
download, starts the app again and checks the download continues by itself.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

L = "http://127.0.0.1:5757"
REPO = "https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-"
SMALL = REPO + "/releases/latest/download/UDM-addon-firefox.zip"
BIG = REPO + "/releases/latest/download/UDM-Windows.zip"
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"     # "Me at the zoo", 19 seconds
EXE = sys.argv[1]
fails: list[str] = []


def call(path, data=None, timeout=30):
    req = urllib.request.Request(L + path, data=None if data is None else json.dumps(data).encode(),
                                 headers={"X-VideoGrab": "1", "Content-Type": "application/json"},
                                 method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"error": f"HTTP {e.code}: {body[:200]}"}


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name, info, flush=True)
    if not ok:
        fails.append(name)


def start_app():
    p = subprocess.Popen([EXE, "--minimized"])
    for _ in range(90):
        try:
            if call("/api/ping", timeout=3).get("app") == "UDM":
                return p
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("UDM did not start")


def wait(job_id, until=("done", "error", "cancelled"), secs=180):
    j = {}
    for _ in range(secs * 2):
        j = call(f"/api/progress/{job_id}")
        if j.get("status") in until:
            break
        time.sleep(0.5)
    return j


def main():
    p = start_app()
    print("app:", call("/api/ping"))
    check("desktop app mode", call("/api/desktop").get("desktop") is True)

    # 1. a real file, 4 connections, pause + resume
    r = call("/api/fetch", {"url": SMALL, "connections": 4, "source": "app"})
    print("fetch:", r, flush=True)
    jid = r.get("job_id")
    time.sleep(0.4)
    call(f"/api/pause/{jid}", {})
    j = wait(jid, until=("paused", "done"), secs=30)
    call(f"/api/resume/{jid}", {})
    j = wait(jid)
    size_ok = j.get("file") and os.path.getsize(j["file"]) == j.get("size")
    check("file download (pause + resume)", j.get("status") == "done" and size_ok,
          f"{j.get('status')} {j.get('filename')} {j.get('size')} bytes {j.get('error') or ''}")

    # 2. YouTube video (warning only: YouTube often blocks build machines)
    v = call("/api/download", {"url": VIDEO, "mode": "video", "quality": "360", "source": "app"}).get("job_id")
    jv = wait(v, secs=180) if v else {}
    print(("PASS " if jv.get("status") == "done" else "WARN ") + "YouTube video:", jv.get("status"),
          jv.get("filename"), (jv.get("error") or "")[:300], flush=True)

    # 3. quit in the middle of a download, start again -> it continues by itself
    big = call("/api/fetch", {"url": BIG, "connections": 8, "source": "app"}).get("job_id")
    for _ in range(120):
        jb = call(f"/api/progress/{big}")
        if (jb.get("percent") or 0) > 3 or jb.get("status") in ("done", "error"):
            break
        time.sleep(0.5)
    before = jb.get("percent") or 0
    print("before quit:", {k: jb.get(k) for k in ("status", "percent", "size", "error")}, flush=True)
    r = call("/api/quit", {})
    check("quit request accepted", r.get("ok") is True, r)
    try:
        p.wait(timeout=40)
        check("app exits after quit", True)
    except subprocess.TimeoutExpired:
        check("app exits after quit", False)
        p.kill()
    hist = json.load(open(os.path.join(os.path.expanduser("~"), ".udm", "history.json"), encoding="utf-8"))
    saved = next((h for h in hist if h.get("id") == big), {})
    check("download paused on quit", saved.get("status") in ("paused", "done") and
          (saved.get("status") == "done" or saved.get("resume_next")), f"{saved.get('status')} {before:.0f}%")
    p = start_app()
    time.sleep(6)
    jb = call(f"/api/progress/{big}")
    check("download continues after restart", jb.get("status") in ("downloading", "connecting", "starting", "done"),
          f"{jb.get('status')} {jb.get('percent', 0):.0f}% (was {before:.0f}%)")
    jb = wait(big, secs=300)
    check("continued download completes", jb.get("status") == "done" and os.path.getsize(jb["file"]) == jb.get("size"),
          f"{jb.get('status')} {jb.get('size')} bytes")
    try:
        call("/api/quit", {})
        p.wait(timeout=40)
    except Exception as e:
        check("app quits cleanly", False, e)
        p.kill()

    # 4. screenshot of the window with these downloads, printed so it can be viewed from the log
    out = os.path.join(os.environ.get("RUNNER_TEMP", "."), "smoke.json")
    subprocess.run([EXE, "--selftest", "--selftest-wait=5000", f"--selftest-out={out}"], timeout=120)
    shot = out.rsplit(".", 1)[0] + ".png"
    if os.path.exists(shot):
        print("SCREENSHOT-BASE64-BEGIN")
        print(base64.b64encode(open(shot, "rb").read()).decode())
        print("SCREENSHOT-BASE64-END", flush=True)

    print("FAILED:" if fails else "ALL PASSED", fails)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
