"""
UDM – Universal Download Manager: the native desktop app (Windows, Mac, Linux).

Real native windows (Qt) - no browser and no web pages inside. Closing the window keeps UDM
downloading in the tray / menu bar; quitting pauses every download and they continue by
themselves the next time UDM starts.

Run from source:   python udm_qt.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def _log_path() -> Path:
    d = Path.home() / ".udm"
    d.mkdir(parents=True, exist_ok=True)
    return d / "udm-desktop.log"


def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# A packaged windowed app has no console: send stray output to the log file
if sys.stdout is None or sys.stderr is None:
    _f = open(_log_path(), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _f
    sys.stderr = sys.stderr or _f

import app as core            # noqa: E402  - the UDM download engine
import downloader as dl       # noqa: E402

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QRectF, QSettings,  # noqa: E402
                            QSize, QSortFilterProxyModel, Qt, QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QGuiApplication, QIcon,  # noqa: E402
                           QKeySequence, QPainter, QPalette, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,  # noqa: E402
                               QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QRadioButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionProgressBar, QSystemTrayIcon,
                               QTableView, QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

APP_NAME = "UDM – Universal Download Manager"
BRAND = QColor("#1a8cff")
OK = QColor("#21a366")
WARN = QColor("#e0a100")
ERR = QColor("#e5484d")
BASE = f"http://127.0.0.1:{core.APP_PORT}"
HEADERS = {"X-VideoGrab": "1"}
ACTIVE = ("queued", "starting", "connecting", "downloading", "processing", "converting", "pausing", "cancelling")
CATS = ["Video", "Music", "Documents", "Compressed", "Programs", "Images", "Other"]
CAT_ICON = {"Video": "🎬", "Music": "🎵", "Documents": "📄", "Compressed": "🗜️", "Programs": "💿",
            "Images": "🖼️", "Other": "📦"}
SOURCE = {"app": "UDM", "extension": "Browser", "popup": "Browser", "page": "Browser", "menu": "Browser",
          "shortcut": "Browser", "catch": "Browser", "phone": "Phone"}
VIDEO_SITES = re.compile(r"(youtube\.com|youtu\.be|instagram\.com|pinterest\.|pin\.it|tiktok\.com|facebook\.com|"
                         r"fb\.watch|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|reddit\.com)", re.I)


class ApiError(Exception):
    pass


class Api:
    """Talks to the UDM engine inside this same app (no network involved)."""

    @staticmethod
    def get(path: str):
        r = core.app.test_client().get(path, base_url=BASE)
        return r.get_json(silent=True) or {}

    @staticmethod
    def post(path: str, data: dict | None = None):
        r = core.app.test_client().post(path, json=data or {}, headers=HEADERS, base_url=BASE)
        j = r.get_json(silent=True) or {}
        if r.status_code >= 400:
            raise ApiError(j.get("error") or f"Something went wrong ({r.status_code})")
        return j


API = Api()


# ---------------- helpers ----------------
class Bridge(QObject):
    """Run code on the window's thread from background threads."""
    call = Signal(object)


BRIDGE: Bridge | None = None


def on_ui(fn):
    if BRIDGE is not None:
        BRIDGE.call.emit(fn)


def in_background(fn, done=None, fail=None):
    def run():
        try:
            res = fn()
            if done:
                on_ui(lambda: done(res))
        except Exception as e:
            if fail:
                on_ui(lambda: fail(e))
            else:
                log("background error:", e)
    threading.Thread(target=run, daemon=True).start()


def human_size(b) -> str:
    if not b:
        return ""
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return f"{b:.0f} {u}" if u in ("B", "KB") else f"{b:.2f} {u}"
        b /= 1024
    return ""


def when(ts) -> str:
    if not ts:
        return ""
    t = time.localtime(ts)
    return time.strftime("%H:%M", t) if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d") else time.strftime("%d %b %Y", t)


def fmt_secs(s: float) -> str:
    s = max(0, int(round(s)))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def parse_secs(v: str):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return core.parse_time(v)
    except ValueError:
        return float("nan")


_ICON_CACHE: dict = {}


def emoji_icon(ch: str, size: int = 32) -> QIcon:
    key = (ch, size)
    if key not in _ICON_CACHE:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        f = QFont("Segoe UI Emoji" if IS_WIN else "Apple Color Emoji" if IS_MAC else "Noto Color Emoji")
        f.setPixelSize(int(size * 0.78))
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, ch)
        p.end()
        _ICON_CACHE[key] = QIcon(pm)
    return _ICON_CACHE[key]


def app_icon() -> QIcon:
    icon = QIcon()
    for n in (16, 32, 48, 128):
        f = core.HERE / "extension-chrome" / "icons" / f"{n}.png"
        if f.exists():
            icon.addFile(str(f), QSize(n, n))
    return icon


def name_of(j: dict) -> str:
    return j.get("filename") or j.get("title") or j.get("url") or ""


def status_text(j: dict) -> str:
    st = j.get("status")
    if st == "done":
        return "Complete"
    if j.get("needs_refresh"):
        return "Link expired"
    return {"error": "Error", "cancelled": "Cancelled", "queued": "Queued", "pausing": "Stopping…",
            "connecting": "Connecting…", "starting": "Connecting…", "converting": "Converting…",
            "processing": "Finishing…", "cancelling": "Cancelling…"}.get(st) or (
        f"Paused {round(j.get('percent') or 0)}%" if st == "paused" else
        "Downloading…" if j.get("indeterminate") else f"{(j.get('percent') or 0):.1f}%")


def message(parent, title, text, kind="info"):
    box = {"info": QMessageBox.information, "warn": QMessageBox.warning, "error": QMessageBox.critical}[kind]
    box(parent, title, text)


# ---------------- the downloads table ----------------
COLS = ["File name", "Size", "Status", "Time left", "Transfer rate", "Last try", "From"]
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
JOB_ROLE = Qt.ItemDataRole.UserRole


class JobsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.jobs: list[dict] = []
        self.index_of: dict[str, int] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.jobs)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        j = self.jobs[index.row()]
        c = index.column()
        if role == JOB_ROLE:
            return j
        downloading = j.get("status") == "downloading"
        if role == Qt.ItemDataRole.DisplayRole:
            return [name_of(j), human_size(j.get("size")), status_text(j),
                    j.get("eta") if downloading else "", j.get("speed") if downloading else "",
                    when(j.get("done_ts") or j.get("ts")), SOURCE.get(j.get("source"), j.get("source") or "")][c]
        if role == SORT_ROLE:
            return [name_of(j).lower(), j.get("size") or 0,
                    (0 if j.get("status") in ACTIVE else 1) * 1000 + (j.get("percent") or 0),
                    j.get("eta_s") or 0, j.get("speed_bps") or 0, j.get("done_ts") or j.get("ts") or 0,
                    j.get("source") or ""][c]
        if role == Qt.ItemDataRole.DecorationRole and c == 0:
            return emoji_icon(CAT_ICON.get(j.get("category") or "Other", "📦"), 20)
        if role == Qt.ItemDataRole.ToolTipRole and c == 0:
            return f"{name_of(j)}\n{j.get('url', '')}"
        if role == Qt.ItemDataRole.ForegroundRole and c == 2:
            if j.get("needs_refresh"):
                return WARN
        return None

    def update(self, jobs: list[dict]):
        new_ids = [j["id"] for j in jobs]
        if new_ids != [j["id"] for j in self.jobs]:
            self.beginResetModel()
            self.jobs = jobs
            self.index_of = {j["id"]: i for i, j in enumerate(jobs)}
            self.endResetModel()
        else:
            self.jobs = jobs
            if jobs:
                self.dataChanged.emit(self.index(0, 0), self.index(len(jobs) - 1, len(COLS) - 1))


class JobsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.view = "all"
        self.search = ""
        self.setSortRole(SORT_ROLE)

    def set_view(self, v):
        self.view = v
        self.invalidateFilter()

    def set_search(self, s):
        self.search = s.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        j = self.sourceModel().jobs[row]
        v, st = self.view, j.get("status")
        ok = (v == "all" or (v in CATS and (j.get("category") or "Other") == v)
              or (v == "unfinished" and st != "done") or (v == "finished" and st == "done")
              or (v == "queued" and st in ("queued", "paused")) or (v == "failed" and st in ("error", "cancelled")))
        if ok and self.search:
            ok = self.search in (name_of(j) + " " + j.get("url", "")).lower()
        return ok


class StatusDelegate(QStyledItemDelegate):
    """Progress bar in the Status column, like IDM."""

    def paint(self, painter, option, index):
        j = index.data(JOB_ROLE)
        if not j:
            return super().paint(painter, option, index)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(3, 4, -3, -4)
        bar.minimum, bar.maximum = 0, 100
        bar.progress = 100 if j.get("status") == "done" else int(j.get("percent") or 0)
        bar.text = status_text(j)
        bar.textVisible = True
        bar.textAlignment = Qt.AlignmentFlag.AlignCenter
        bar.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Horizontal
        pal = QPalette(option.palette)
        color = OK if j.get("status") == "done" else WARN if (j.get("needs_refresh") or j.get("status") == "paused") \
            else ERR if j.get("status") in ("error", "cancelled") else BRAND
        pal.setColor(QPalette.ColorRole.Highlight, color)
        bar.palette = pal
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter)


class SegmentMap(QWidget):
    """IDM-style map of the parts of a file being downloaded at the same time."""

    def __init__(self):
        super().__init__()
        self.job = None
        self.setMinimumHeight(22)
        self.setMaximumHeight(22)

    def set_job(self, j):
        self.job = j
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, self.palette().color(QPalette.ColorRole.Base))
        j = self.job
        if not j:
            return
        total, w = j.get("size") or 0, r.width()
        if j.get("status") == "done":
            p.fillRect(r, OK)
        elif total and j.get("segments"):
            for s0, e0, d in j["segments"]:
                x = s0 / total * w
                p.fillRect(QRectF(x, 0, max(d / total * w, 0), r.height()), BRAND)
                p.fillRect(QRectF(x, 0, 1, r.height()), QColor("#9fd2ff"))
        elif j.get("percent"):
            p.fillRect(QRectF(0, 0, w * j["percent"] / 100, r.height()), BRAND)
        p.setPen(self.palette().color(QPalette.ColorRole.Mid))
        p.drawRect(r.adjusted(0, 0, -1, -1))


# ---------------- dialogs ----------------
class AddUrlDialog(QDialog):
    def __init__(self, parent, prefill=""):
        super().__init__(parent)
        self.setWindowTitle("Add download")
        self.setMinimumWidth(560)
        s = API.get("/api/settings")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Address(es) – one per line:"))
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://example.com/file.zip")
        self.urls.setFixedHeight(90)
        if not prefill:
            clip = QGuiApplication.clipboard().text().strip()
            if re.match(r"^(https?|ftp)://", clip):
                prefill = clip
        self.urls.setPlainText(prefill)
        lay.addWidget(self.urls)
        self.hint = QLabel()
        self.hint.setStyleSheet(f"color:{BRAND.name()}")
        lay.addWidget(self.hint)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Use the file's own name")
        self.conn = QComboBox()
        for n in (1, 2, 4, 8, 16):
            self.conn.addItem(str(n), n)
        self.conn.setCurrentText(str(s.get("connections", 8)))
        self.folder = QLineEdit(s.get("download_dir", ""))
        pick = QPushButton("Browse…")
        pick.clicked.connect(self.browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder)
        frow.addWidget(pick)
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("Page the link came from (optional)")
        form.addRow("Save as:", self.name)
        form.addRow("Connections:", self.conn)
        form.addRow("Save to:", frow)
        form.addRow("From page:", self.ref)
        lay.addLayout(form)
        self.err = QLabel()
        self.err.setStyleSheet(f"color:{ERR.name()}")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        btns = QHBoxLayout()
        btns.addStretch()
        later = QPushButton("Download later")
        start = QPushButton("Start download")
        start.setDefault(True)
        cancel = QPushButton("Cancel")
        later.clicked.connect(lambda: self.go(True))
        start.clicked.connect(lambda: self.go(False))
        cancel.clicked.connect(self.reject)
        for b in (cancel, later, start):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.urls.textChanged.connect(self.check)
        self.check()

    def links(self):
        return [u for u in self.urls.toPlainText().split() if re.match(r"^(https?|ftp)://", u)]

    def check(self):
        v = [u for u in self.links() if VIDEO_SITES.search(u)]
        self.hint.setText("🎬 Video page – UDM downloads the video itself (best quality)." if v else "")

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save downloads to", self.folder.text())
        if d:
            self.folder.setText(d)

    def go(self, later):
        urls = self.links()
        if not urls:
            self.err.setText("Enter a link that starts with http:// or https://")
            return
        try:
            vids = [u for u in urls if VIDEO_SITES.search(u)]
            files = [u for u in urls if not VIDEO_SITES.search(u)]
            for v in vids:
                API.post("/api/download", {"url": v, "source": "app", "filename": self.name.text().strip()})
            if files:
                API.post("/api/fetch", {"url": "\n".join(files), "filename": self.name.text().strip(),
                                        "connections": self.conn.currentData(), "referer": self.ref.text().strip(),
                                        "folder": self.folder.text().strip(), "start_paused": later, "source": "app"})
            self.accept()
        except Exception as e:
            self.err.setText(str(e))


class VideoDialog(QDialog):
    """Download a video (or its audio) from YouTube, Instagram, Pinterest, streams … with all options."""

    def __init__(self, parent, url="", referer="", name=""):
        super().__init__(parent)
        self.setWindowTitle("Video downloader")
        self.setMinimumWidth(720)
        self.info = None
        self.prefs = QSettings("PixelMonkLab", "UDM")
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("Paste a YouTube, Instagram, Pinterest or video link…")
        paste = QPushButton("Paste")
        fetch = QPushButton("Fetch")
        paste.clicked.connect(lambda: (self.url.setText(QGuiApplication.clipboard().text().strip()), self.fetch()))
        fetch.clicked.connect(self.fetch)
        self.url.returnPressed.connect(self.fetch)
        top.addWidget(self.url)
        top.addWidget(paste)
        top.addWidget(fetch)
        lay.addLayout(top)

        prev = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(176, 99)
        self.thumb.setFrameShape(QFrame.Shape.StyledPanel)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta = QVBoxLayout()
        self.title = QLabel("Paste a link and click Fetch to see the video.")
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        meta.addWidget(self.title)
        meta.addWidget(self.meta)
        meta.addStretch()
        prev.addWidget(self.thumb)
        prev.addLayout(meta, 1)
        lay.addLayout(prev)

        grid = QGridLayout()
        self.r_video = QRadioButton("🎬 Video (MP4)")
        self.r_audio = QRadioButton("🎵 Audio only")
        grp = QButtonGroup(self)
        grp.addButton(self.r_video)
        grp.addButton(self.r_audio)
        self.quality = QComboBox()
        for label, v in (("Best available", "best"), ("2160p (4K)", "2160"), ("1440p", "1440"), ("1080p", "1080"),
                         ("720p", "720"), ("480p", "480"), ("360p", "360")):
            self.quality.addItem(label, v)
        self.afmt = QComboBox()
        for v in ("mp3", "m4a", "wav"):
            self.afmt.addItem(v.upper(), v)
        self.login = QComboBox()
        for label, v in (("Off", "none"), ("Chrome", "chrome"), ("Safari", "safari"), ("Firefox", "firefox"),
                         ("Edge", "edge"), ("Brave", "brave")):
            self.login.addItem(label, v)
        grid.addWidget(self.r_video, 0, 0)
        grid.addWidget(self.r_audio, 0, 1)
        grid.addWidget(QLabel("Quality:"), 1, 0)
        grid.addWidget(self.quality, 1, 1)
        grid.addWidget(QLabel("Audio format:"), 1, 2)
        grid.addWidget(self.afmt, 1, 3)
        grid.addWidget(QLabel("Login from:"), 2, 0)
        grid.addWidget(self.login, 2, 1)
        self.login.setToolTip("For private / login-only posts: use your login from this browser")
        lay.addLayout(grid)

        self.trim = QGroupBox("✂️ Trim (only part of the video)")
        self.trim.setCheckable(True)
        self.trim.setChecked(False)
        tg = QGridLayout(self.trim)
        self.s_start = QSlider(Qt.Orientation.Horizontal)
        self.s_end = QSlider(Qt.Orientation.Horizontal)
        self.t_start = QLineEdit()
        self.t_start.setPlaceholderText("0:00")
        self.t_end = QLineEdit()
        self.t_end.setPlaceholderText("end of video")
        self.t_len = QLabel()
        self.precise = QCheckBox("Exact cut (a bit slower)")
        self.precise.setChecked(True)
        tg.addWidget(QLabel("Start:"), 0, 0)
        tg.addWidget(self.s_start, 0, 1)
        tg.addWidget(self.t_start, 0, 2)
        tg.addWidget(QLabel("End:"), 1, 0)
        tg.addWidget(self.s_end, 1, 1)
        tg.addWidget(self.t_end, 1, 2)
        tg.addWidget(self.t_len, 2, 1)
        tg.addWidget(self.precise, 2, 2)
        self.s_start.valueChanged.connect(self.slid)
        self.s_end.valueChanged.connect(self.slid)
        for w in (self.s_start, self.s_end):
            w.setEnabled(False)
        lay.addWidget(self.trim)

        ex = QGroupBox("Options")
        eg = QGridLayout(ex)
        self.compat = QCheckBox("Plays in QuickTime / Windows player (H.264)")
        self.all_items = QCheckBox("All items (carousel / playlist)")
        self.cover = QCheckBox("Save cover image (JPG)")
        self.embed = QCheckBox("Add cover art && title to audio")
        self.subs = QComboBox()
        for label, v in (("No subtitles", "off"), ("Subtitles: English (.srt)", "en"), ("Subtitles: all languages", "all")):
            self.subs.addItem(label, v)
        self.fname = QLineEdit(name)
        self.fname.setPlaceholderText("File name (optional)")
        self.ref = QLineEdit(referer)
        self.ref.setPlaceholderText("Page the stream came from (optional, for .m3u8/.mpd links)")
        eg.addWidget(self.compat, 0, 0)
        eg.addWidget(self.all_items, 0, 1)
        eg.addWidget(self.cover, 1, 0)
        eg.addWidget(self.embed, 1, 1)
        eg.addWidget(self.subs, 2, 0)
        eg.addWidget(self.fname, 2, 1)
        eg.addWidget(self.ref, 3, 0, 1, 2)
        lay.addWidget(ex)

        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color:{ERR.name()}")
        lay.addWidget(self.err)
        b = QHBoxLayout()
        b.addStretch()
        cancel = QPushButton("Close")
        go = QPushButton("⬇ Download")
        go.setDefault(True)
        cancel.clicked.connect(self.reject)
        go.clicked.connect(self.download)
        b.addWidget(cancel)
        b.addWidget(go)
        lay.addLayout(b)

        # remembered choices
        self.r_audio.setChecked(self.prefs.value("mode", "video") == "audio")
        self.r_video.setChecked(not self.r_audio.isChecked())
        self.compat.setChecked(self.prefs.value("compat", True, type=bool))
        self.embed.setChecked(self.prefs.value("embed", True, type=bool))
        self.afmt.setCurrentIndex(max(0, self.afmt.findData(self.prefs.value("afmt", "mp3"))))
        self.login.setCurrentIndex(max(0, self.login.findData(self.prefs.value("login", "none"))))
        self.r_video.toggled.connect(self.mode_changed)
        self.mode_changed()
        if url:
            QTimer.singleShot(50, self.fetch)

    def mode_changed(self):
        v = self.r_video.isChecked()
        self.quality.setEnabled(v)
        self.afmt.setEnabled(not v)
        self.compat.setEnabled(v)
        self.subs.setEnabled(v)
        self.embed.setEnabled(not v)

    def fetch(self):
        url = self.url.text().strip()
        if not url:
            return
        self.err.setText("")
        self.title.setText("Fetching…")
        self.meta.setText("")
        body = {"url": url, "cookies": self.login.currentData(), "referer": self.ref.text().strip()}
        in_background(lambda: API.post("/api/info", body), self.got_info, self.info_failed)

    def info_failed(self, e):
        self.title.setText("Couldn't read this link")
        self.err.setText(str(e))

    def got_info(self, info):
        self.info = info
        title = info.get("title") or "Untitled"
        if re.match(r"^(master|index|playlist|manifest)\b", title, re.I) and self.fname.text().strip():
            title = self.fname.text().strip()
        self.title.setText(title)
        bits = [info.get("platform"), info.get("uploader"),
                ("Length " + fmt_secs(info["duration"])) if info.get("duration") else "",
                f"{info['items']} items" if (info.get("items") or 1) > 1 else ""]
        self.meta.setText(" · ".join(b for b in bits if b))
        if info.get("heights"):
            keep = self.quality.currentData()
            self.quality.clear()
            self.quality.addItem(f"Best available ({info['heights'][0]}p)", "best")
            for h in info["heights"]:
                self.quality.addItem(f"{h}p", str(h))
            self.quality.setCurrentIndex(max(0, self.quality.findData(keep)))
        d = int(info.get("duration") or 0)
        for w in (self.s_start, self.s_end):
            w.setEnabled(d > 0)
            w.setRange(0, max(d, 1))
        self.s_start.setValue(0)
        self.s_end.setValue(d)
        self.t_start.clear()
        self.t_end.clear()
        if info.get("thumbnail"):
            in_background(lambda: urllib.request.urlopen(info["thumbnail"], timeout=10).read(), self.got_thumb)

    def got_thumb(self, data):
        pm = QPixmap()
        if pm.loadFromData(data):
            self.thumb.setPixmap(pm.scaled(self.thumb.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))

    def slid(self):
        a, b = self.s_start.value(), self.s_end.value()
        if a >= b:
            if self.sender() is self.s_start:
                self.s_start.setValue(max(0, b - 1))
            else:
                self.s_end.setValue(a + 1)
            return
        self.t_start.setText(fmt_secs(a) if a else "")
        self.t_end.setText("" if b >= self.s_end.maximum() else fmt_secs(b))
        self.t_len.setText(f"Selected: {fmt_secs(b - a)}")
        self.trim.setChecked(True)

    def download(self):
        url = self.url.text().strip()
        if not url:
            self.err.setText("Paste a link first.")
            return
        start = end = ""
        if self.trim.isChecked():
            start, end = self.t_start.text().strip(), self.t_end.text().strip()
            a, b = parse_secs(start), parse_secs(end)
            if (start and a != a) or (end and b != b):
                self.err.setText("Check the trim times – use e.g. 1:30")
                return
            if a is not None and b is not None and b <= a:
                self.err.setText("The trim end must be after the start.")
                return
        mode = "video" if self.r_video.isChecked() else "audio"
        body = {"url": url, "mode": mode, "quality": self.quality.currentData(), "audio_format": self.afmt.currentData(),
                "cookies": self.login.currentData(), "mac_compat": self.compat.isChecked(),
                "all_items": self.all_items.isChecked(), "save_thumbnail": self.cover.isChecked(),
                "embed_cover": self.embed.isChecked(), "subtitles": self.subs.currentData(),
                "start": start, "end": end, "precise": self.precise.isChecked(),
                "filename": self.fname.text().strip(), "referer": self.ref.text().strip(), "source": "app",
                "title": (self.info or {}).get("title") or "", "thumbnail": (self.info or {}).get("thumbnail") or ""}
        try:
            API.post("/api/download", body)
        except Exception as e:
            self.err.setText(str(e))
            return
        for k, v in (("mode", mode), ("compat", self.compat.isChecked()), ("embed", self.embed.isChecked()),
                     ("afmt", self.afmt.currentData()), ("login", self.login.currentData())):
            self.prefs.setValue(k, v)
        self.accept()


class RefreshDialog(QDialog):
    """IDM-style 'Refresh download address' for a link that expired."""

    def __init__(self, parent, job):
        super().__init__(parent)
        self.job_id = job["id"]
        self.setWindowTitle("Refresh download address")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        got = job.get("downloaded") or ((job.get("size") or 0) * (job.get("percent") or 0) / 100)
        t = QLabel(f"<b>The link for “{name_of(job)}” stopped working.</b><br>"
                   + (f"{human_size(got)} of {human_size(job.get('size')) or '?'} is already downloaded and will be kept. "
                      if got else "")
                   + "UDM continues with a fresh link from where it stopped.")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.page = (job.get("request") or {}).get("referer") or ""
        if self.page:
            g1 = QGroupBox("1 · Easiest")
            v = QVBoxLayout(g1)
            b = QPushButton("🌐 Open the download page")
            b.clicked.connect(self.open_page)
            v.addWidget(b)
            v.addWidget(QLabel("Then click the download link there again. The UDM browser add-on hands it over "
                               "and the download continues by itself."))
            lay.addWidget(g1)
        g2 = QGroupBox("2 · Or paste the new download link" if self.page else "Paste the new download link")
        h = QHBoxLayout(g2)
        self.new = QLineEdit()
        self.new.setPlaceholderText("https://…")
        use = QPushButton("Use this link")
        use.clicked.connect(self.use)
        h.addWidget(self.new)
        h.addWidget(use)
        lay.addWidget(g2)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(later)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

    def open_page(self):
        try:
            API.post(f"/api/refresh-wait/{self.job_id}")
            QDesktopServices.openUrl(self.page)
            self.status.setText("⏳ Waiting… click the download link on that page.")
            self.timer.start(800)
        except Exception as e:
            self.status.setText(f"<span style='color:{ERR.name()}'>{e}</span>")

    def use(self):
        self.status.setText("Checking the new link…")
        url = self.new.text().strip()
        in_background(lambda: API.post(f"/api/refresh/{self.job_id}", {"url": url}),
                      lambda r: (self.timer.start(800)),
                      lambda e: self.status.setText(f"<span style='color:{ERR.name()}'>{e}</span>"))

    def poll(self):
        j = API.get(f"/api/progress/{self.job_id}")
        if j and not j.get("needs_refresh") and j.get("status") != "error":
            self.timer.stop()
            self.status.setText("✓ New link found – continuing from where it stopped!")
            QTimer.singleShot(1500, self.accept)


class OptionsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(560)
        s = API.get("/api/settings")
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        gen = QWidget()
        f = QFormLayout(gen)
        self.folder = QLineEdit(s.get("download_dir", ""))
        pick = QPushButton("Browse…")
        pick.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.folder)
        row.addWidget(pick)
        f.addRow("Save downloads to:", row)
        self.maxc = QSpinBox()
        self.maxc.setRange(1, 10)
        self.maxc.setValue(int(s.get("max_concurrent", 3)))
        self.conn = QComboBox()
        for n in (1, 2, 4, 8, 16):
            self.conn.addItem(str(n), n)
        self.conn.setCurrentText(str(s.get("connections", 8)))
        self.limit = QSpinBox()
        self.limit.setRange(0, 10_000_000)
        self.limit.setSingleStep(100)
        self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("No limit")
        self.limit.setValue(int(s.get("speed_limit_kbps", 0)))
        f.addRow("Downloads at the same time:", self.maxc)
        f.addRow("Connections per file:", self.conn)
        f.addRow("Speed limit:", self.limit)
        tabs.addTab(gen, "Downloads")
        app_tab = QWidget()
        v = QVBoxLayout(app_tab)
        self.resume = QCheckBox("Continue unfinished downloads when UDM starts")
        self.tray = QCheckBox("Keep downloading when I close the window (UDM stays in the tray / menu bar)")
        self.login = QCheckBox("Start UDM when I log in (in the tray)")
        self.notify = QCheckBox("Show notifications when downloads start and finish")
        for w, k, d in ((self.resume, "resume_on_start", True), (self.tray, "close_to_tray", True),
                        (self.login, "start_at_login", False), (self.notify, "notify", True)):
            w.setChecked(bool(s.get(k, d)))
            v.addWidget(w)
        v.addStretch()
        tabs.addTab(app_tab, "App")
        lay.addWidget(tabs)
        self.err = QLabel()
        self.err.setStyleSheet(f"color:{ERR.name()}")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch()
        c = QPushButton("Cancel")
        ok = QPushButton("Save")
        ok.setDefault(True)
        c.clicked.connect(self.reject)
        ok.clicked.connect(self.save)
        row.addWidget(c)
        row.addWidget(ok)
        lay.addLayout(row)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save downloads to", self.folder.text())
        if d:
            self.folder.setText(d)

    def save(self):
        try:
            API.post("/api/settings", {"download_dir": self.folder.text().strip(), "max_concurrent": self.maxc.value(),
                                       "connections": self.conn.currentData(), "speed_limit_kbps": self.limit.value(),
                                       "resume_on_start": self.resume.isChecked(), "close_to_tray": self.tray.isChecked(),
                                       "start_at_login": self.login.isChecked(), "notify": self.notify.isChecked()})
            self.accept()
        except Exception as e:
            self.err.setText(str(e))


class PhoneDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Download on your phone")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        t = QLabel("Your phone uses this computer to download, then saves the file to the phone. "
                   "Both must be on the <b>same Wi-Fi</b>, and UDM must be running here.")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.on = QCheckBox("Allow my phone to use UDM")
        self.on.toggled.connect(self.toggle)
        lay.addWidget(self.on)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setFixedSize(250, 250)
        lay.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.url = QLabel()
        self.url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url.setWordWrap(True)
        self.url.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.url)
        row = QHBoxLayout()
        self.copy = QPushButton("Copy link")
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.url.text()))
        self.reset = QPushButton("Make a new link")
        self.reset.setToolTip("Old QR codes stop working")
        self.reset.clicked.connect(lambda: self.paint(API.post("/api/phone", {"reset": True})))
        row.addWidget(self.copy)
        row.addWidget(self.reset)
        lay.addLayout(row)
        note = QLabel("🔒 Only phones with this secret link can connect, and they can't change your settings or "
                      "delete files. If the computer asks to allow incoming connections, click Allow.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.paint(API.get("/api/phone"))

    def toggle(self, on):
        self.paint(API.post("/api/phone", {"enabled": on}))

    def paint(self, info):
        self.on.blockSignals(True)
        self.on.setChecked(bool(info.get("enabled")))
        self.on.blockSignals(False)
        show = bool(info.get("enabled") and info.get("url"))
        for w in (self.qr, self.url, self.copy, self.reset):
            w.setVisible(show)
        if show:
            self.url.setText(info["url"])
            svg = info.get("qr_svg")
            if svg:
                try:
                    from PySide6.QtSvg import QSvgRenderer
                    from PySide6.QtCore import QByteArray
                    r = QSvgRenderer(QByteArray(svg.encode()))
                    pm = QPixmap(240, 240)
                    pm.fill(Qt.GlobalColor.white)
                    p = QPainter(pm)
                    r.render(p)
                    p.end()
                    self.qr.setPixmap(pm)
                except Exception as e:
                    self.qr.setText(f"(QR code unavailable: {e})")
        elif info.get("error"):
            self.url.setVisible(True)
            self.url.setText(info["error"])


class AddonDialog(QDialog):
    """Guided install of the browser add-on (or one click, when a store link exists)."""
    BROWSERS = [("Chrome", "chrome://extensions"), ("Microsoft Edge", "edge://extensions"),
                ("Brave", "brave://extensions"), ("Opera", "opera://extensions"),
                ("Vivaldi", "vivaldi://extensions"), ("Firefox", "about:debugging#/runtime/this-firefox")]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Install the UDM browser add-on")
        self.setMinimumWidth(600)
        self.info = API.get("/api/extension")
        lay = QVBoxLayout(self)
        intro = QLabel("The add-on puts a Download button on YouTube, Instagram and Pinterest, finds videos on other "
                       "sites, and hands your browser downloads to UDM – like IDM.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Your browser:"))
        self.br = QComboBox()
        for n, _ in self.BROWSERS:
            self.br.addItem(n)
        self.br.currentIndexChanged.connect(self.paint)
        row.addWidget(self.br)
        row.addStretch()
        lay.addLayout(row)
        self.box = QVBoxLayout()
        lay.addLayout(self.box)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(2000)
        self.paint()
        self.check()

    def clear(self):
        while self.box.count():
            it = self.box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()

    def paint(self):
        self.clear()
        name, page = self.BROWSERS[self.br.currentIndex()]
        ff = name == "Firefox"
        store = self.info.get("store") or {}
        link = store.get("firefox") if ff else (store.get("edge") or store.get("chrome")) if name == "Microsoft Edge" \
            else store.get("chrome")
        if link:
            b = QPushButton(f"⬇ Add to {name} – one click")
            b.setMinimumHeight(44)
            b.clicked.connect(lambda: QDesktopServices.openUrl(link))
            self.box.addWidget(b)
            self.box.addWidget(QLabel("Or install it manually (full version, includes YouTube):"))
        folder = self.info.get("firefox" if ff else "chrome", "")
        path = str(Path(folder) / "manifest.json") if ff else folder
        paste = "press ⌘ Cmd+⇧ Shift+G, paste with ⌘ Cmd+V, press Enter" if IS_MAC else \
            "click the address bar at the top, paste with Ctrl+V, press Enter"
        steps = [
            (f"1. Open {name}'s add-ons page", f"Copy the address, open a new tab in {name}, paste it and press Enter.",
             [("📋 Copy “" + page + "”", lambda: self.copy(page))]),
            ("2. Click “Load Temporary Add-on…”" if ff else "2. Turn on “Developer mode”",
             "It's on that page." if ff else ("Bottom-left of the page." if name == "Microsoft Edge" else "Top-right corner of the page."), []),
            ("3. Choose the UDM " + ("file" if ff else "folder"),
             ("Click “Load unpacked”. " if not ff else "") + f"In the window that opens, {paste}, then click "
             + ("Open." if ff else "Select."),
             [("📋 Copy the location", lambda: self.copy(path)), ("📂 Show it", self.reveal)]),
        ]
        for title, text, buttons in steps:
            g = QGroupBox(title)
            v = QVBoxLayout(g)
            lab = QLabel(text)
            lab.setWordWrap(True)
            v.addWidget(lab)
            if buttons:
                h = QHBoxLayout()
                for label, fn in buttons:
                    b = QPushButton(label)
                    b.clicked.connect(fn)
                    h.addWidget(b)
                h.addStretch()
                v.addLayout(h)
            self.box.addWidget(g)
        if ff:
            self.box.addWidget(QLabel("Note: Firefox removes test add-ons when it closes – repeat after restarting it."))

    def copy(self, text):
        QGuiApplication.clipboard().setText(text)
        self.status.setText("📋 Copied: " + text)

    def reveal(self):
        name = self.BROWSERS[self.br.currentIndex()][0]
        try:
            API.post("/api/reveal-extension", {"browser": "firefox" if name == "Firefox" else "chrome"})
        except Exception as e:
            self.status.setText(str(e))

    def check(self):
        seen = (API.get("/api/extension") or {}).get("seen")
        if seen and seen.get("recent"):
            self.status.setText(f"<b style='color:{OK.name()}'>✓ Add-on installed (v{seen.get('version')} in "
                                f"{seen.get('browser') or 'your browser'})!</b> Look for the UDM icon in your browser's toolbar.")


class AboutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("About UDM")
        lay = QVBoxLayout(self)
        logo = QLabel()
        f = core.HERE / "extension-chrome" / "icons" / "128.png"
        if f.exists():
            logo.setPixmap(QPixmap(str(f)))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)
        p = API.get("/api/ping")
        t = QLabel(f"<h2>UDM</h2><p>Universal <b>Download</b> Manager</p><p>Version {core.VERSION_LABEL}<br>"
                   f"Download engine {p.get('engine', '?')}</p>"
                   f"<p><a href='https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-'>Website &amp; updates</a> · "
                   f"<a href='https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-/blob/main/PRIVACY.md'>Privacy</a></p>"
                   "<p style='color:gray'>Only download content you own or have permission to save.</p>")
        t.setOpenExternalLinks(True)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)


# ---------------- main window ----------------
class MainWindow(QMainWindow):
    def __init__(self, tray_ok: bool):
        super().__init__()
        self.tray = None
        self.tray_ok = tray_ok
        self.quitting = False
        self.told_hidden = False
        self.asked_refresh: set = set()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1240, 780)
        self.setMinimumSize(900, 560)
        self.setAcceptDrops(True)

        self.model = JobsModel()
        self.proxy = JobsFilter()
        self.proxy.setSourceModel(self.model)

        # toolbar
        tb = QToolBar("Main")
        tb.setIconSize(QSize(30, 30))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, icon, fn, key=None, tip=None):
            a = QAction(emoji_icon(icon, 30), text, self)
            a.triggered.connect(fn)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                a.setToolTip(tip)
            return a

        self.a_add = act("Add URL", "➕", lambda: self.add_url(), "Ctrl+N", "Download any link (Ctrl+N)")
        self.a_video = act("Video", "🎬", lambda: self.video(), "Ctrl+Shift+V", "Video downloader – YouTube, Instagram, Pinterest…")
        self.a_resume = act("Resume", "▶️", lambda: self.do("resume"))
        self.a_stop = act("Stop", "⏸️", lambda: self.do("stop"))
        self.a_stopall = act("Stop all", "⏹️", self.stop_all)
        self.a_delete = act("Delete", "🗑️", lambda: self.do("delete"), "Delete")
        self.a_deldone = act("Delete completed", "🧹", self.delete_completed)
        self.a_startq = act("Start queue", "⏯️", lambda: API.post("/api/resume-all"))
        self.a_folder = act("Open folder", "📂", lambda: API.post("/api/open-folder"))
        self.a_options = act("Options", "⚙️", lambda: OptionsDialog(self).exec(), "Ctrl+,")
        self.a_phone = act("Phone", "📱", lambda: PhoneDialog(self).exec())
        self.a_addon = act("Browser add-on", "🧩", lambda: AddonDialog(self).exec())
        for a in (self.a_add, self.a_video, None, self.a_resume, self.a_stop, self.a_stopall, None, self.a_delete,
                  self.a_deldone, None, self.a_startq, self.a_folder, self.a_options, None, self.a_phone, self.a_addon):
            tb.addSeparator() if a is None else tb.addAction(a)

        # menus
        mb = self.menuBar()
        m = mb.addMenu("&Tasks")
        m.addAction(self.a_add)
        m.addAction(self.a_video)
        m.addSeparator()
        quit_a = QAction("Quit UDM (downloads pause)", self)
        quit_a.setMenuRole(QAction.MenuRole.QuitRole)       # Mac: UDM > Quit (Cmd+Q)
        quit_a.setShortcut(QKeySequence.StandardKey.Quit)
        quit_a.triggered.connect(self.quit_app)
        m.addAction(quit_a)
        m = mb.addMenu("&Downloads")
        for a in (self.a_resume, self.a_stop, self.a_stopall, self.a_startq, self.a_delete, self.a_deldone):
            m.addAction(a)
        m = mb.addMenu("&Options")
        opt = QAction("Options…", self)
        opt.setMenuRole(QAction.MenuRole.PreferencesRole)
        opt.triggered.connect(lambda: OptionsDialog(self).exec())
        m.addAction(opt)
        m.addAction(self.a_phone)
        m.addAction(self.a_addon)
        m = mb.addMenu("&Help")
        about = QAction("About UDM", self)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(lambda: AboutDialog(self).exec())
        m.addAction(about)
        guide = QAction("How to use UDM", self)
        guide.triggered.connect(lambda: QDesktopServices.openUrl(
            "https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-#readme"))
        m.addAction(guide)

        # left: categories
        self.side = QTreeWidget()
        self.side.setHeaderHidden(True)
        self.side.setMinimumWidth(190)
        self.side.setMaximumWidth(260)
        self.side_items = {}
        root = QTreeWidgetItem(["All downloads"])
        root.setData(0, JOB_ROLE, "all")
        root.setIcon(0, emoji_icon("📥", 20))
        self.side.addTopLevelItem(root)
        self.side_items["all"] = root
        for c in CATS:
            it = QTreeWidgetItem([c])
            it.setData(0, JOB_ROLE, c)
            it.setIcon(0, emoji_icon(CAT_ICON[c], 20))
            root.addChild(it)
            self.side_items[c] = it
        for key, label, ic in (("unfinished", "Unfinished", "⏳"), ("finished", "Finished", "✅"),
                               ("queued", "Queued / stopped", "🕒"), ("failed", "Failed", "⚠️")):
            it = QTreeWidgetItem([label])
            it.setData(0, JOB_ROLE, key)
            it.setIcon(0, emoji_icon(ic, 20))
            self.side.addTopLevelItem(it)
            self.side_items[key] = it
        root.setExpanded(True)
        self.side.setCurrentItem(root)
        self.side.currentItemChanged.connect(lambda cur, prev: cur and self.proxy.set_view(cur.data(0, JOB_ROLE)))

        # center: search + table + details
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Search downloads…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.set_search)
        cv.addWidget(self.search)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(5, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(2, StatusDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 90), (2, 190), (3, 80), (4, 100), (5, 110), (6, 80)):
            self.table.setColumnWidth(c, w)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.doubleClicked.connect(self.double_click)
        self.table.selectionModel().selectionChanged.connect(lambda *a: self.refresh_details())

        self.details = QWidget()
        dv = QVBoxLayout(self.details)
        hd = QHBoxLayout()
        self.d_title = QLabel("Select a download to see its details.")
        f = self.d_title.font()
        f.setBold(True)
        self.d_title.setFont(f)
        hd.addWidget(self.d_title, 1)
        self.d_btns = QHBoxLayout()
        hd.addLayout(self.d_btns)
        dv.addLayout(hd)
        self.segmap = SegmentMap()
        dv.addWidget(self.segmap)
        self.d_grid = QGridLayout()
        self.d_vals = {}
        for i, k in enumerate(("Status", "Size", "Downloaded", "Transfer rate", "Time left", "Connections",
                               "Resume capable", "Category", "Address", "Saved to")):
            lab = QLabel(k + ":")
            lab.setStyleSheet("color: gray")
            val = QLabel("–")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.d_grid.addWidget(lab, i // 2, (i % 2) * 2)
            self.d_grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self.d_vals[k] = val
        self.d_grid.setColumnStretch(1, 1)
        self.d_grid.setColumnStretch(3, 1)
        dv.addLayout(self.d_grid)

        split_v = QSplitter(Qt.Orientation.Vertical)
        split_v.addWidget(self.table)
        split_v.addWidget(self.details)
        split_v.setStretchFactor(0, 4)
        split_v.setStretchFactor(1, 1)
        cv.addWidget(split_v)
        split = QSplitter()
        split.addWidget(self.side)
        split.addWidget(center)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        # status bar
        self.sb_run = QLabel()
        self.sb_speed = QLabel()
        self.sb_folder = QLabel()
        self.statusBar().addWidget(self.sb_run)
        self.statusBar().addWidget(self.sb_folder, 1)
        self.statusBar().addPermanentWidget(self.sb_speed)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(800)
        self.refresh()

    # ----- data -----
    def refresh(self):
        try:
            r = API.get("/api/jobs?all=1")
        except Exception as e:
            log("refresh:", e)
            return
        jobs = r.get("jobs") or []
        keep = [j["id"] for j in self.selected()]
        reset = [j["id"] for j in jobs] != [j["id"] for j in self.model.jobs]
        self.model.update(jobs)
        if reset and keep:
            sm = self.table.selectionModel()
            for jid in keep:
                row = self.model.index_of.get(jid)
                if row is not None:
                    idx = self.proxy.mapFromSource(self.model.index(row, 0))
                    if idx.isValid():
                        sm.select(idx, sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
        run = sum(1 for j in jobs if j.get("status") in ("starting", "connecting", "downloading", "processing", "converting"))
        q = sum(1 for j in jobs if j.get("status") == "queued")
        sp = (r.get("stats") or {}).get("speed_bps") or 0
        self.sb_run.setText(f"  {run} downloading · {q} queued")
        self.sb_speed.setText(f"Total speed: {human_size(sp)}/s  " if sp else "")
        self.sb_folder.setText("Saving to " + str(core.download_dir()))
        self.setWindowTitle(f"({run}) {APP_NAME}" if run else APP_NAME)
        counts = {"all": len(jobs), "unfinished": sum(j.get("status") != "done" for j in jobs),
                  "finished": sum(j.get("status") == "done" for j in jobs),
                  "queued": sum(j.get("status") in ("queued", "paused") for j in jobs),
                  "failed": sum(j.get("status") in ("error", "cancelled") for j in jobs)}
        for c in CATS:
            counts[c] = sum((j.get("category") or "Other") == c for j in jobs)
        labels = {"all": "All downloads", "unfinished": "Unfinished", "finished": "Finished",
                  "queued": "Queued / stopped", "failed": "Failed"}
        for k, it in self.side_items.items():
            base = labels.get(k, k)
            it.setText(0, f"{base} ({counts.get(k, 0)})" if counts.get(k) else base)
        self.refresh_details()
        self.ask_refresh(jobs)
        if self.tray is not None:
            self.tray.setToolTip(f"UDM – {run} downloading" + (f", {human_size(sp)}/s" if sp else ""))

    def selected(self) -> list[dict]:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        return [self.proxy.index(r, 0).data(JOB_ROLE) for r in sorted(rows)]

    def refresh_details(self):
        sel = self.selected()
        j0 = sel[0] if len(sel) == 1 else None
        key = (j0["id"], j0.get("status"), bool(j0.get("needs_refresh"))) if j0 else None
        rebuild = key != getattr(self, "_btn_key", "x")
        self._btn_key = key
        if rebuild:
            while self.d_btns.count():
                w = self.d_btns.takeAt(0).widget()
                if w:
                    w.deleteLater()
        if len(sel) != 1:
            self.d_title.setText(f"{len(sel)} downloads selected." if sel else "Select a download to see its details.")
            self.segmap.set_job(None)
            for v in self.d_vals.values():
                v.setText("–")
            return
        j = sel[0]
        self.d_title.setText(CAT_ICON.get(j.get("category") or "Other", "") + "  " + name_of(j))
        self.segmap.set_job(j)
        got = j.get("downloaded") or ((j.get("size") or 0) * (j.get("percent") or 0) / 100)
        vals = {"Status": status_text(j) + (" – " + (j.get("error") or "").split("\n")[0] if j.get("error") else ""),
                "Size": human_size(j.get("size")) or "Unknown",
                "Downloaded": human_size(j.get("size") if j.get("status") == "done" else got) or "–",
                "Transfer rate": j.get("speed") or "", "Time left": j.get("eta") or "",
                "Connections": str(j.get("connections") or 0) if j.get("kind") == "file" else "auto (video)",
                "Resume capable": ("Yes" if j.get("resumable", True) else "No – restarts from the beginning")
                if j.get("kind") == "file" else "Yes",
                "Category": j.get("category") or "Other", "Address": j.get("url", ""),
                "Saved to": j.get("file") or j.get("folder") or ""}
        for k, v in vals.items():
            self.d_vals[k].setText(v or "–")
            self.d_vals[k].setToolTip(v or "")

        if not rebuild:
            return

        def btn(text, fn):
            b = QPushButton(text)
            b.clicked.connect(fn)
            self.d_btns.addWidget(b)
        st = j.get("status")
        if st == "done":
            btn("▶ Open", lambda: self.do("open", [j]))
            btn("📂 Show in folder", lambda: self.do("folder", [j]))
        elif j.get("needs_refresh"):
            btn("🔄 Refresh download address", lambda: self.do("refresh", [self.fresh(j)]))
        elif st in ("paused", "error", "cancelled"):
            btn("▶ Resume", lambda: self.do("resume", [j]))
        elif st in ACTIVE:
            btn("⏸ Stop", lambda: self.do("stop", [j]))
        btn("🔗 Copy address", lambda: self.do("copy", [j]))

    def fresh(self, j):
        row = self.model.index_of.get(j["id"])
        return self.model.jobs[row] if row is not None else j

    def ask_refresh(self, jobs):
        """Like IDM: ask for a new address as soon as a link expires."""
        if QApplication.activeModalWidget() is not None:
            return
        for j in jobs:
            if j.get("needs_refresh") and j["id"] not in self.asked_refresh:
                self.asked_refresh.add(j["id"])
                if self.isVisible():
                    RefreshDialog(self, j).exec()
                else:
                    notify("Download link expired", f"{name_of(j)} – open UDM to refresh the download address.")
                return

    # ----- actions -----
    def do(self, action, jobs=None):
        jobs = jobs if jobs is not None else self.selected()
        try:
            for j in jobs:
                jid = j["id"]
                if action == "open":
                    API.post("/api/open-file", {"job_id": jid})
                elif action == "folder":
                    API.post("/api/open-folder", {"job_id": jid})
                elif action == "resume":
                    API.post(f"/api/resume/{jid}")
                elif action == "stop":
                    API.post(f"/api/pause/{jid}")
                elif action == "redownload":
                    API.post(f"/api/retry/{jid}")
                elif action in ("delete", "deletefile"):
                    API.post(f"/api/delete/{jid}", {"with_file": action == "deletefile"})
                elif action == "copy":
                    QGuiApplication.clipboard().setText("\n".join(x.get("url", "") for x in jobs))
                    self.statusBar().showMessage("Address copied", 2500)
                    break
                elif action == "refresh":
                    RefreshDialog(self, j).exec()
                    break
        except Exception as e:
            message(self, "UDM", str(e), "warn")
        self.refresh()

    def context_menu(self, pos):
        sel = self.selected()
        if not sel:
            return
        m = QMenu(self)
        st = {j.get("status") for j in sel}

        def add(text, action, enabled=True):
            a = m.addAction(text)
            a.setEnabled(enabled)
            a.triggered.connect(lambda: self.confirm_then(action))
        add("▶ Open", "open", "done" in st)
        add("📂 Open folder", "folder")
        m.addSeparator()
        add("▶️ Resume", "resume", bool(st & {"paused", "error", "cancelled", "queued"}))
        add("⏸️ Stop", "stop", bool(st & set(ACTIVE)))
        add("🔁 Redownload", "redownload", bool(st - set(ACTIVE)))
        add("🔄 Refresh download address…", "refresh",
            len(sel) == 1 and sel[0].get("kind") == "file" and sel[0].get("status") in ("error", "paused", "cancelled"))
        m.addSeparator()
        add("🔗 Copy address", "copy")
        add("🗑️ Delete from list", "delete")
        add("❌ Delete with file", "deletefile", "done" in st)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def confirm_then(self, action):
        if action == "deletefile" and QMessageBox.question(
                self, "Delete files", "Delete the selected file(s) from your computer too?") != QMessageBox.StandardButton.Yes:
            return
        self.do(action)

    def double_click(self, index):
        j = index.data(JOB_ROLE)
        if not j:
            return
        self.do("open" if j.get("status") == "done" else "resume" if j.get("status") in ("paused", "error", "cancelled")
                else "folder", [j])

    def stop_all(self):
        API.post("/api/pause-all")
        self.refresh()

    def delete_completed(self):
        done = [j for j in self.model.jobs if j.get("status") == "done"]
        if done and QMessageBox.question(self, "Delete completed",
                                         f"Remove {len(done)} completed download(s) from the list? (Files are kept.)") \
                == QMessageBox.StandardButton.Yes:
            self.do("delete", done)

    def add_url(self, prefill=""):
        AddUrlDialog(self, prefill).exec()
        self.refresh()

    def video(self, url="", referer="", name=""):
        VideoDialog(self, url, referer, name).exec()
        self.refresh()

    # drag & drop a link onto the window
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        text = "\n".join(u.toString() for u in md.urls()) if md.hasUrls() else md.text()
        links = [u for u in text.split() if re.match(r"^(https?|ftp)://", u)]
        if links:
            self.add_url("\n".join(links))

    # ----- window / quit -----
    def bring_to_front(self, payload=None):
        payload = payload or {}
        self.showNormal()
        self.raise_()
        self.activateWindow()
        view = payload.get("view")
        if view == "video":
            QTimer.singleShot(100, lambda: self.video(payload.get("url", ""), payload.get("referer", ""), payload.get("name", "")))
        elif view == "phone":
            QTimer.singleShot(100, lambda: PhoneDialog(self).exec())
        elif view == "refresh" and payload.get("job_id"):
            j = API.get(f"/api/progress/{payload['job_id']}")
            if j and j.get("id"):
                QTimer.singleShot(100, lambda: RefreshDialog(self, j).exec())

    def closeEvent(self, e):
        if self.quitting:
            e.accept()
            return
        if self.tray is not None and core.get_settings().get("close_to_tray", True):
            e.ignore()
            self.hide()
            if not self.told_hidden:
                self.told_hidden = True
                where = "in the menu bar (top right)" if IS_MAC else "in the tray, next to the clock"
                notify("UDM is still downloading", f"UDM is {where}. To quit (downloads pause), use its menu.")
            return
        e.ignore()
        self.quit_app()

    def quit_app(self):
        if self.quitting:
            return
        self.quitting = True
        busy = [j for j in self.model.jobs if j.get("status") in ACTIVE]
        dlg = None
        if busy and self.isVisible():
            dlg = QProgressDialog("Pausing downloads – they continue next time you open UDM…", None, 0, 0, self)
            dlg.setWindowTitle("Quitting UDM")
            dlg.setMinimumDuration(0)
            dlg.show()
        done = threading.Event()
        threading.Thread(target=lambda: (core.pause_all_for_quit(8), done.set()), daemon=True).start()

        def wait():
            if not done.is_set():
                QTimer.singleShot(100, wait)
                return
            if dlg:
                dlg.close()
            if self.tray is not None:
                self.tray.hide()
            QApplication.quit()
        wait()


# ---------------- tray / notifications / start at login ----------------
TRAY: QSystemTrayIcon | None = None


def notify(title: str, body: str):
    def show():
        if TRAY is not None and TRAY.supportsMessages():
            TRAY.showMessage(title, body or " ", app_icon(), 6000)
        elif IS_MAC:
            esc = lambda t: (t or "").replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'])
    on_ui(show)


def set_start_at_login(on: bool):
    exe = sys.executable
    if not (getattr(sys, "frozen", False) or "__compiled__" in globals()):
        raise RuntimeError("Start at login works in the installed UDM app.")
    if IS_WIN:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                             winreg.KEY_SET_VALUE)
        if on:
            winreg.SetValueEx(key, "UDM", 0, winreg.REG_SZ, f'"{exe}" --minimized')
        else:
            try:
                winreg.DeleteValue(key, "UDM")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    elif IS_MAC:
        plist = Path.home() / "Library" / "LaunchAgents" / "com.pixelmonklab.udm.plist"
        if on:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.pixelmonklab.udm</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>--minimized</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""")
        elif plist.exists():
            plist.unlink()
    else:
        f = Path.home() / ".config" / "autostart" / "udm.desktop"
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f'[Desktop Entry]\nType=Application\nName=UDM\nExec="{exe}" --minimized\n')
        elif f.exists():
            f.unlink()


def make_tray(win: MainWindow) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(app_icon(), win)
    tray.setToolTip("UDM – Universal Download Manager")
    m = QMenu()
    m.addAction("Open UDM", lambda: win.bring_to_front())
    m.addAction("Add URL…", lambda: (win.bring_to_front(), win.add_url()))
    m.addAction("Video downloader…", lambda: (win.bring_to_front(), win.video()))
    m.addSeparator()
    m.addAction("Pause all downloads", win.stop_all)
    m.addAction("Resume all downloads", lambda: API.post("/api/resume-all"))
    m.addSeparator()
    login = m.addAction("Start UDM when I log in")
    login.setCheckable(True)
    login.setChecked(bool(core.get_settings().get("start_at_login")))

    def toggle(on):
        try:
            API.post("/api/settings", {"start_at_login": on})
        except Exception as e:
            login.setChecked(not on)
            message(win, "UDM", str(e), "warn")
    login.toggled.connect(toggle)
    m.addAction("Quit UDM (downloads pause)", win.quit_app)
    tray.setContextMenu(m)
    tray.activated.connect(lambda reason: win.bring_to_front()
                           if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                         QSystemTrayIcon.ActivationReason.DoubleClick) and not IS_MAC else None)
    tray.show()
    win._tray_menu = m
    return tray


# ---------------- start ----------------
def other_copy_running() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/ping", timeout=1.5) as r:
            return b'"UDM"' in r.read()
    except Exception:
        return False


def show_other_copy():
    try:
        req = urllib.request.Request(BASE + "/api/show-window", data=b"{}", method="POST",
                                     headers={"X-VideoGrab": "1", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def start_server():
    import logging
    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    srv = make_server("127.0.0.1", core.APP_PORT, core.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True, name="udm-server").start()
    return srv


def main(qapp: QApplication | None = None, splash=None):
    global BRIDGE, TRAY
    args = sys.argv[1:]
    test_out = next((a.split("=", 1)[1] for a in args if a.startswith("--selftest-out=")), None)
    selftest = "--selftest" in args
    if qapp is None:
        if other_copy_running() and not selftest:
            show_other_copy()
            return 0
        QApplication.setApplicationName("UDM")
        QApplication.setOrganizationName("PixelMonkLab")
        qapp = QApplication(sys.argv)
    QApplication.setApplicationDisplayName("UDM")
    qapp.setWindowIcon(app_icon())
    qapp.setQuitOnLastWindowClosed(False)
    BRIDGE = Bridge()
    BRIDGE.call.connect(lambda fn: fn())

    start_server()
    win = MainWindow(QSystemTrayIcon.isSystemTrayAvailable())
    TRAY = win.tray = make_tray(win)
    core.DESKTOP.update(active=True, tray=TRAY is not None, notify=notify, set_login=set_start_at_login,
                        show=lambda payload=None: on_ui(lambda: win.bring_to_front(payload)),
                        quit=lambda: on_ui(win.quit_app))
    if IS_MAC:   # clicking UDM in the Dock brings the hidden window back
        qapp.applicationStateChanged.connect(
            lambda st: win.bring_to_front() if st == Qt.ApplicationState.ApplicationActive and not win.isVisible()
            and not win.quitting else None)
    threading.Thread(target=core.startup, daemon=True).start()
    qapp.aboutToQuit.connect(lambda: core.pause_all_for_quit(5) if not win.quitting else None)
    log(f"UDM {core.VERSION_LABEL} started ({sys.platform}, tray={TRAY is not None})")

    if not ("--minimized" in args and TRAY is not None):
        win.show()
    if splash is not None:
        splash.finish(win)

    if selftest:
        def finish():
            res = {"server": False, "platform": sys.platform, "tray": TRAY is not None,
                   "compiled": "__compiled__" in globals(), "frozen": bool(getattr(sys, "frozen", False))}
            try:
                with urllib.request.urlopen(BASE + "/api/ping", timeout=5) as r:
                    p = json.load(r)
                res.update(server=True, app=p.get("app"), version=p.get("version_label"), engine=p.get("engine"),
                           youtube_js=p.get("youtube_js"), ffmpeg=p.get("ffmpeg"))
                res["window_title"] = win.windowTitle()
                res["rows"] = win.model.rowCount()
                shot = test_out.rsplit(".", 1)[0] + ".png" if test_out else None
                if shot:
                    win.grab().save(shot)
                    res["screenshot"] = shot
            except Exception as e:
                res["error"] = str(e)
            if test_out:
                Path(test_out).write_text(json.dumps(res, indent=1))
            print(json.dumps(res), flush=True)
            win.quitting = True
            qapp.quit()
        QTimer.singleShot(int(next((a.split("=", 1)[1] for a in args if a.startswith("--selftest-wait=")), "4000")), finish)

    return qapp.exec()


if __name__ == "__main__":
    import udm_main
    os._exit(udm_main.run() or 0)
