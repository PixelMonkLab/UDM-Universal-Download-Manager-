"""
Build the UDM desktop app (Windows UDM.exe / Mac UDM.app / Linux UDM) from source.

1. UDM's own code (app.py, downloader.py, udm_qt.py, udm_main.py + the web pages) is compiled to
   machine code with Nuitka, so no readable .py source ends up in the app.
2. PyInstaller then packs the compiled parts, Python, Qt and the other libraries into one app.

    pip install -r requirements.txt -r requirements-desktop.txt nuitka pyinstaller
    python tools/build_native.py            # result: dist/UDM (Windows/Linux) or dist/UDM.app (Mac)
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "native"
SRC = OUT / "src"            # copies of our modules (+ generated udm_embedded.py)
COMPILED = OUT / "compiled"  # the machine-code modules (.pyd / .so)
MODULES = ["downloader", "app", "udm_qt", "udm_main", "udm_embedded"]
PAGES = ["ui.html", "manager.html", "mobile.html"]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def imports_of(path: Path) -> set[str]:
    """Every module our code imports - PyInstaller can't look inside compiled modules, so we tell it."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def main():
    if "--package-only" in sys.argv:          # reuse the compiled modules from the last build
        return package()
    shutil.rmtree(OUT, ignore_errors=True)
    SRC.mkdir(parents=True)
    COMPILED.mkdir(parents=True)

    # the Firefox add-on folder is generated from the Chrome one
    run([sys.executable, str(ROOT / "tools" / "build_firefox.py")])

    # 1. the web pages (browser version, phone page, web manager) go inside a compiled module
    pages = {n: (ROOT / n).read_text(encoding="utf-8") for n in PAGES}
    (SRC / "udm_embedded.py").write_text("PAGES = " + repr(pages) + "\n", encoding="utf-8")
    for m in MODULES[:-1]:
        shutil.copy2(ROOT / f"{m}.py", SRC / f"{m}.py")

    # 2. compile each module to machine code
    for m in MODULES:
        run([sys.executable, "-m", "nuitka", "--module", f"{m}.py", "--nofollow-imports",
             f"--output-dir={COMPILED}", "--remove-output", "--assume-yes-for-downloads", "--quiet",
             "--python-flag=no_docstrings"], cwd=SRC)
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    built = sorted(p.name for p in COMPILED.iterdir())
    print("compiled:", built)
    for m in MODULES:
        if not (COMPILED / f"{m}{ext}").exists():
            sys.exit(f"Nuitka did not produce {m}{ext}")

    # 3. a tiny starter (the only plain-Python file in the app; it just calls the compiled code)
    (OUT / "UDM.py").write_text("import os, udm_main\nos._exit(udm_main.run() or 0)\n", encoding="utf-8")

    package()


def package():
    hidden = set()
    for m in MODULES[:-1]:
        hidden |= imports_of(ROOT / f"{m}.py")
    hidden -= set(MODULES)
    hidden = sorted(h for h in hidden if not h.startswith("__future__"))
    (OUT / "hiddenimports.txt").write_text("\n".join(hidden), encoding="utf-8")
    print(f"{len(hidden)} imports passed to PyInstaller")

    env = dict(os.environ, UDM_NATIVE_OUT=str(OUT))
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "dist"),
         "--workpath", str(OUT / "pyi"), str(ROOT / "UDM-desktop.spec")], cwd=ROOT, env=env)

    # make sure none of our source code slipped into the app
    app_dir = ROOT / "dist" / ("UDM.app" if sys.platform == "darwin" else "UDM")
    leaked = [p for p in app_dir.rglob("*") if p.stem in MODULES and p.suffix in (".py", ".pyc")]
    if leaked:
        sys.exit(f"Source code found in the app: {leaked}")
    print("OK:", app_dir)


if __name__ == "__main__":
    main()
