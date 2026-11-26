# Build recipe for VDM.exe (PyInstaller).
# Windows: just double-click build-windows-exe.bat. Manually: pyinstaller --noconfirm --clean VDM.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("ui.html", "."), ("mobile.html", "."), ("manager.html", "."),
         ("extension-chrome", "extension-chrome"), ("extension-firefox", "extension-firefox")]
binaries = []
hiddenimports = collect_submodules("yt_dlp") + collect_submodules("qrcode")

# ffmpeg (video joining / converting), certificates, and YouTube helper scripts
for pkg in ("imageio_ffmpeg", "certifi", "yt_dlp_ejs"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

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

a = Analysis(["app.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VDM", icon="icon.ico",
          console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="VDM", upx=False)
