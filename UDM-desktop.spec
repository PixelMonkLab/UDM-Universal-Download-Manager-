# PyInstaller recipe for the UDM desktop app. Don't run this directly - use:
#     python tools/build_native.py
# (it first compiles UDM's own code to machine code, then calls this file)
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

OUT = Path(os.environ["UDM_NATIVE_OUT"])
IS_MAC = sys.platform == "darwin"

datas = [("store/links.json", "store"), ("extension-chrome", "extension-chrome"),
         ("extension-firefox", "extension-firefox")]
binaries = []
hiddenimports = [h for h in (OUT / "hiddenimports.txt").read_text().split() if h]
hiddenimports += collect_submodules("yt_dlp") + collect_submodules("qrcode") + collect_submodules("flask") \
    + collect_submodules("werkzeug")
hiddenimports += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtSvg"]

# ffmpeg (video joining / converting), certificates, and YouTube helper scripts
for pkg in ("imageio_ffmpeg", "certifi", "yt_dlp_ejs"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Deno: the JavaScript engine yt-dlp needs for YouTube
try:
    import deno
    binaries.append((deno.find_deno_bin(), "."))
except Exception as e:
    print("WARNING: Deno not bundled - YouTube may not fully work:", e)

# Standard-library modules that newer (auto-updated) yt-dlp versions may use
hiddenimports += ["sqlite3", "xml.etree.ElementTree", "http.cookiejar", "ctypes", "hmac", "secrets",
                  "lzma", "bz2", "gzip", "asyncio", "concurrent.futures", "html.parser", "unicodedata",
                  "email.utils", "netrc", "zipimport", "importlib.metadata", "urllib.request"]
if sys.platform == "win32":
    hiddenimports.append("winreg")

a = Analysis([str(OUT / "UDM.py")], pathex=[str(OUT / "compiled")], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports,
             excludes=["pkg_resources", "setuptools", "tkinter", "matplotlib", "numpy", "PIL", "pytest", "PySide6.QtWebEngineCore",
                       "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="UDM",
          icon="assets/udm-icon.png" if IS_MAC else "icon.ico",
          console=False, upx=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="UDM", upx=False)
if IS_MAC:
    app = BUNDLE(coll, name="UDM.app", icon="assets/udm-icon.png", bundle_identifier="com.pixelmonklab.udm",
                 version="1.1.0",
                 info_plist={"CFBundleName": "UDM", "CFBundleDisplayName": "UDM",
                             "CFBundleShortVersionString": "1.1.0", "NSHighResolutionCapable": True,
                             "LSMinimumSystemVersion": "11.0",
                             "NSHumanReadableCopyright": "UDM – Universal Download Manager"})
