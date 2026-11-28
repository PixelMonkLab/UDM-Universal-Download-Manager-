"""
UDM desktop app - start here.

Checks for an already-running UDM and shows a splash screen *before* loading the download
engine (which may spend a few seconds checking for a newer YouTube engine once a day).
"""
from __future__ import annotations

import os
import sys
import urllib.request

PORT = 5757


def _already_running() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1.5) as r:
            return b'"UDM"' in r.read()
    except Exception:
        return False


def _show_running_copy() -> None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/show-window", data=b"{}", method="POST",
                                     headers={"X-VideoGrab": "1", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def _is_desktop_app() -> bool:
    try:
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/desktop", timeout=2) as r:
            return bool(json.load(r).get("desktop"))
    except Exception:
        return False        # older UDM versions don't know /api/desktop: they are the browser version


def _bundle_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def run() -> int:
    args = sys.argv[1:]
    selftest = "--selftest" in args
    running = not selftest and _already_running()
    if running and _is_desktop_app():
        _show_running_copy()          # UDM is already open: just bring its window to the front
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    QApplication.setApplicationName("UDM")
    QApplication.setOrganizationName("PixelMonkLab")
    qapp = QApplication(sys.argv)
    if running:                       # the older browser version of UDM is still open
        QMessageBox.information(None, "UDM", "An older UDM (the browser version) is still running.\n\n"
                                "Close its black window (Windows) or Terminal window (Mac), then open UDM again.")
        return 1

    splash = None
    if "--minimized" not in args:
        pm = QPixmap(420, 200)
        pm.fill(QColor("#ffffff"))
        p = QPainter(pm)
        icon = QPixmap(os.path.join(_bundle_dir(), "extension-chrome", "icons", "128.png"))
        if not icon.isNull():
            p.drawPixmap(24, 36, icon)
        f = QFont()
        f.setPixelSize(34)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#1a8cff"))
        p.drawText(170, 92, "UDM")
        f.setPixelSize(15)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#333333"))
        p.drawText(170, 120, "Universal Download Manager")
        p.setPen(QColor("#888888"))
        p.drawText(170, 150, "Starting…")
        p.setPen(QColor("#dddddd"))
        p.drawRect(0, 0, 419, 199)
        p.end()
        splash = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
        splash.show()
        qapp.processEvents()

    import udm_qt                      # loads the download engine
    return udm_qt.main(qapp, splash)


if __name__ == "__main__":
    code = run()
    os._exit(code or 0)
