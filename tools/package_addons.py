"""Build ready-to-upload add-on packages into dist-addons/.

    python tools/package_addons.py

Creates:
  UDM-addon-firefox.zip        -> addons.mozilla.org (sign it, then it installs in one click)
  UDM-addon-edge.zip           -> Microsoft Edge Add-ons (Partner Center)
  UDM-addon-chrome-store.zip   -> Chrome Web Store (YouTube removed: the store doesn't allow it)
  UDM-addon-chrome-manual.zip  -> full version for "Load unpacked" (Chrome, Brave, Opera, Edge)
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

root = pathlib.Path(__file__).resolve().parent.parent
out = root / "dist-addons"
work = out / "_work"
chrome = root / "extension-chrome"


def zip_dir(src: pathlib.Path, dest: pathlib.Path):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and f.name != ".DS_Store":
                z.write(f, f.relative_to(src).as_posix())      # manifest.json at the top of the zip
    print(f"  {dest.name:32s} {dest.stat().st_size // 1024} KB")


def make_store_edition(dst: pathlib.Path):
    """Chrome Web Store edition: same add-on, minus YouTube."""
    shutil.copytree(chrome, dst)
    m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    for cs in m["content_scripts"]:
        cs["matches"] = [x for x in cs["matches"] if "youtube" not in x and "youtu.be" not in x]
    m["description"] = ("Download manager with a Download button on Instagram & Pinterest, catches streamed videos on "
                        "other sites and speeds up file downloads. Works with the free UDM app on your computer.")
    (dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    c = (dst / "common.js").read_text(encoding="utf-8")
    c = c.replace('const EDITION = "full";', 'const EDITION = "store";')
    (dst / "common.js").write_text(c, encoding="utf-8")
    texts = {
        "YouTube, Instagram or Pinterest": "Instagram or Pinterest",
        "YouTube/Instagram/Pinterest": "Instagram/Pinterest",
        "YouTube, Instagram &amp; Pinterest": "Instagram &amp; Pinterest",
        "YouTube, Instagram & Pinterest": "Instagram & Pinterest",
        "Open a YouTube video, Instagram reel or Pinterest pin": "Open an Instagram reel or Pinterest pin",
    }
    for f in dst.glob("*.*"):
        if f.suffix in (".html", ".js"):
            s = f.read_text(encoding="utf-8")
            for a, b in texts.items():
                s = s.replace(a, b)
            f.write_text(s, encoding="utf-8")
    left = [f.name for f in dst.glob("*.*") if f.suffix in (".html", ".json")
            and re.search(r"youtube", f.read_text(encoding="utf-8"), re.I)]
    if left:
        print("  note: 'YouTube' still mentioned in", left)


def main():
    shutil.rmtree(out, ignore_errors=True)
    work.mkdir(parents=True)
    subprocess.run([sys.executable, str(root / "tools" / "build_firefox.py")], check=True)
    print("Packages:")
    zip_dir(root / "extension-firefox", out / "UDM-addon-firefox.zip")
    zip_dir(chrome, out / "UDM-addon-edge.zip")
    zip_dir(chrome, out / "UDM-addon-chrome-manual.zip")
    make_store_edition(work / "chrome-store")
    zip_dir(work / "chrome-store", out / "UDM-addon-chrome-store.zip")
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
