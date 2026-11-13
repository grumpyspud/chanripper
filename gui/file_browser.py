"""Full-window file browser with mark-for-deletion and an item preview.

Mirrors the web UI on purpose: a full-page layout, a breadcrumb, the same
"Mark all" / "Delete marked files" pair, the same dark palette, a preview-size
selector (S/M/L/XL) and a zoomable preview overlay. Marking is one click on a
tile (double-click opens the preview); a marked tile gets a red corner badge
and a red border. The media ledger is untouched by a delete, so a removed file
is never re-downloaded on the next check.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImageReader,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QVBoxLayout,
                               QWidget)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

#: Preview-size presets (height, with a 3:2-ish width), matching web/static/app.js.
THUMB_SIZES = {
    "S": QSize(96, 64),
    "M": QSize(144, 96),
    "L": QSize(216, 144),
    "XL": QSize(300, 200),
}

#: Same colours as web/static/style.css, so the two front ends feel alike.
STYLE = """
QDialog{background:#14161a}
QLabel{color:#e6e9ef; font-size:13px}
QLabel#title{font-size:16px; font-weight:600}
QLabel#muted{color:#8b93a1; font-size:12px}
QLabel#crumbs{color:#8b93a1; font-size:12px}
QPushButton{background:#22262e; border:1px solid #2e333d; border-radius:7px;
            color:#e6e9ef; padding:7px 12px; font-size:13px}
QPushButton:hover{background:#272c35; border-color:#3d4450}
QPushButton:disabled{color:#5a616d; background:#1b1e24}
QPushButton:checked{background:#4a9eff; border-color:#4a9eff; color:#04121f;
                   font-weight:600}
QPushButton#close{background:none; border:none; color:#8b93a1; font-size:16px;
                  padding:2px 8px}
QPushButton#danger:hover{border-color:#f85149; color:#f85149}
QListWidget{background:#14161a; border:none; outline:none}
QListWidget::item{background:#22262e; border:1px solid #2e333d; border-radius:8px;
                  color:#e6e9ef; padding:6px}
QListWidget::item:selected{background:#22262e; border:1px solid #f85149; color:#e6e9ef}
QScrollBar:vertical{background:#14161a; width:10px}
QScrollBar:horizontal{background:#14161a; height:10px}
QScrollBar::handle:vertical{background:#2e333d; border-radius:5px; min-height:30px}
QScrollBar::handle:horizontal{background:#2e333d; border-radius:5px; min-width:30px}
QScrollBar::add-line,QScrollBar::sub-line{height:0}
QScrollArea{background:#0e1013; border:none}
QCheckBox{color:#e6e9ef; font-size:13px}
QSlider::groove:horizontal{height:4px; background:#2e333d; border-radius:2px}
QSlider::handle:horizontal{width:14px; margin:-5px 0; background:#4a9eff;
                           border-radius:7px}
"""


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def format_ts(ts: float, fmt: str) -> str:
    """Format an epoch timestamp for the file-browser meta line."""
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts)
    if fmt == "relative":
        s = max(0, int(time.time() - ts))
        if s < 60:
            return "just now"
        m = s // 60
        if m < 60:
            return f"{m}m ago"
        h = m // 60
        if h < 24:
            return f"{h}h ago"
        d = h // 24
        if d < 30:
            return f"{d}d ago"
        mo = d // 30
        if mo < 12:
            return f"{mo}mo ago"
        return f"{mo // 12}y ago"
    if fmt == "us":
        return dt.strftime("%m/%d/%Y %I:%M %p")
    if fmt == "euro":
        return dt.strftime("%d/%m/%Y %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")   # iso


class FileBrowser(QDialog):
    """One folder at a time; only files can be marked."""

    def __init__(self, root: str | Path, start: str = "", title: str = "",
                 date_format: str = "iso", parent: QWidget | None = None):
        super().__init__(parent)
        self.root = Path(root).resolve()
        self.title = title or "Files"
        self.date_format = date_format
        self.path = ""                       # relative to root
        self.marked: set[str] = set()
        self._pixmaps: dict[str, QPixmap] = {}
        self.file_rels: list[str] = []       # ordered non-dir entries (for preview)
        self.thumb_size = THUMB_SIZES["M"]
        self._current: Path = self.root

        self.setWindowTitle(f"Files — {self.title}")
        self.setStyleSheet(STYLE)
        if parent is not None:
            # Cover the app window, the way the web overlay covers the page.
            self.resize(parent.size())
            self.move(parent.mapToGlobal(parent.rect().topLeft()))

        # A double-click fires a single click first. Defer the mark toggle so
        # opening a file doesn't leave it marked on the way past.
        self._click_item: QListWidgetItem | None = None
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self._flush_click)

        self._build()
        self.browse(start)

    # -- ui ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 12, 16, 12)
        bar_lay.setSpacing(12)

        self.title_label = QLabel(self.title)
        self.title_label.setObjectName("title")
        bar_lay.addWidget(self.title_label)

        sep = QLabel("·")
        sep.setObjectName("muted")
        bar_lay.addWidget(sep)

        self.crumbs = QLabel()
        self.crumbs.setObjectName("crumbs")
        self.crumbs.setTextFormat(Qt.RichText)
        self.crumbs.linkActivated.connect(self._crumb_clicked)
        bar_lay.addWidget(self.crumbs)
        bar_lay.addStretch(1)

        size_label = QLabel("Size")
        size_label.setObjectName("muted")
        bar_lay.addWidget(size_label)

        self._size_btns: dict[str, QPushButton] = {}
        for lvl in ("S", "M", "L", "XL"):
            btn = QPushButton(lvl)
            btn.setCheckable(True)
            btn.setToolTip(f"Preview size {lvl}")
            btn.clicked.connect(lambda _c, l=lvl: self._set_size(l))
            bar_lay.addWidget(btn)
            self._size_btns[lvl] = btn
        self._size_btns["M"].setChecked(True)

        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        bar_lay.addWidget(self.count_label)

        self.mark_all_btn = QPushButton("Mark all")
        self.mark_all_btn.clicked.connect(self._toggle_all)
        bar_lay.addWidget(self.mark_all_btn)

        self.delete_btn = QPushButton("Delete marked files")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_marked)
        bar_lay.addWidget(self.delete_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("close")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.reject)
        bar_lay.addWidget(close_btn)
        lay.addWidget(bar)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWrapping(True)
        self.list.setSpacing(6)
        self.list.setIconSize(self.thumb_size)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.itemClicked.connect(self._on_click)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.list, 1)

    # -- navigation ----------------------------------------------------------
    def browse(self, rel: str) -> None:
        rel = (rel or "").strip()
        # Accept both a path relative to the download root and an absolute path
        # that lives inside it (thread folders come from the store as absolute).
        target = Path(rel) if Path(rel).is_absolute() else (self.root / rel)
        target = target.resolve() if rel else self.root
        if target != self.root and self.root not in target.parents:
            return                                    # never leave the root
        if not target.is_dir():
            return
        self.path = "" if target == self.root else str(
            target.relative_to(self.root)).replace("\\", "/")
        self._current = target
        self.marked.clear()            # marks never outlive the folder
        self._fill(target)
        self._render_crumbs()
        self._refresh_buttons()

    def _grid_size(self, names: list[str], metas: list[str]) -> QSize:
        fm = self.list.fontMetrics()
        max_w = self.thumb_size.width()
        for n in names:
            max_w = max(max_w, fm.horizontalAdvance(n))
        for m in metas:
            max_w = max(max_w, fm.horizontalAdvance(m))
        w = min(max_w + 24, 320)
        h = self.thumb_size.height() + 2 * fm.lineSpacing() + 16
        return QSize(w, h)

    def _fill(self, folder: Path) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._pixmaps.clear()
        self.file_rels = []
        names: list[str] = []
        metas: list[str] = []
        try:
            children = sorted(folder.iterdir(),
                              key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            children = []
        for child in children:
            is_dir = child.is_dir()
            try:
                st = child.stat()
                size = 0 if is_dir else st.st_size
                mtime = st.st_mtime
            except OSError:
                continue
            rel = str(child.relative_to(self.root)).replace("\\", "/")
            names.append(child.name)
            if not is_dir:
                self.file_rels.append(rel)
            date = format_ts(mtime, self.date_format)
            meta = (f"folder · {date}" if is_dir else f"{human(size)} · {date}") if date \
                else ("folder" if is_dir else human(size))
            metas.append(meta)
            label = f"{child.name}\n{meta}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, rel)
            item.setData(Qt.UserRole + 1, is_dir)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            if is_dir:
                item.setToolTip(f"{child.name} — click to open")
                item.setIcon(self._dir_icon())
            else:
                item.setToolTip(
                    f"{child.name} — click to mark for deletion, "
                    "double-click to preview")
                item.setIcon(self._file_icon(rel, child, marked=False))
            self.list.addItem(item)
        self.list.setGridSize(self._grid_size(names, metas))
        self.list.blockSignals(False)

    def _set_size(self, lvl: str) -> None:
        if lvl not in THUMB_SIZES:
            return
        self.thumb_size = THUMB_SIZES[lvl]
        for l, btn in self._size_btns.items():
            btn.setChecked(l == lvl)
        self.list.setIconSize(self.thumb_size)
        self._pixmaps.clear()
        self._fill(self._current)

    # -- artwork -------------------------------------------------------------
    def _dir_icon(self) -> QIcon:
        pm = QPixmap(self.thumb_size)
        pm.fill(Qt.transparent)
        return QIcon(pm)

    def _base_pixmap(self, rel: str, path: Path) -> QPixmap:
        """Thumbnail (images) or a typed placeholder, decoded once per folder."""
        cached = self._pixmaps.get(rel)
        if cached is not None:
            return cached
        if path.suffix.lower() in IMAGE_EXT:
            reader = QImageReader(str(path))
            # Decode straight to thumbnail size: 150 full-resolution images
            # would cost hundreds of MB for icons nobody sees that big.
            reader.setScaledSize(self.thumb_size)
            img = reader.read()
            if not img.isNull():
                pm = QPixmap.fromImage(img)
                self._pixmaps[rel] = pm
                return pm
        pm = QPixmap(self.thumb_size)
        pm.fill(QColor("#1b1e24"))
        painter = QPainter(pm)
        painter.setPen(QColor("#8b93a1"))
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(pm.rect(), Qt.AlignCenter,
                         (path.suffix.lstrip(".") or "file").upper())
        painter.end()
        self._pixmaps[rel] = pm
        return pm

    def _file_icon(self, rel: str, path: Path, marked: bool) -> QIcon:
        pm = QPixmap(self._base_pixmap(rel, path))
        if marked:
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#f85149"))
            painter.drawRoundedRect(4, 4, 24, 24, 5, 5)
            painter.setPen(QPen(QColor("#14161a"), 3, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(9, 16, 13, 21)
            painter.drawLine(13, 21, 22, 10)
            painter.end()
        return QIcon(pm)

    # -- breadcrumb ----------------------------------------------------------
    def _render_crumbs(self) -> None:
        parts = [p for p in self.path.split("/") if p]
        html = ['<a href="">root</a>']
        acc = ""
        for p in parts:
            acc = f"{acc}/{p}" if acc else p
            html.append(f'/ <a href="{acc}">{p}</a>')
        if self.path:
            parent = "/".join(parts[:-1])
            html.insert(0, f'<a href="{parent}">↑ up</a> &nbsp;')
        self.crumbs.setText(" ".join(html))

    def _crumb_clicked(self, href: str) -> None:
        self.browse(href)

    # -- marking -------------------------------------------------------------
    def _on_click(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole + 1):
            self._click_timer.stop()
            self._click_item = None
            self.browse(item.data(Qt.UserRole))
            return
        self._click_item = item
        self._click_timer.start()

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self._click_timer.stop()
        self._click_item = None
        if item.data(Qt.UserRole + 1):
            return                       # already handled by the single click
        rel = item.data(Qt.UserRole)
        idx = self.file_rels.index(rel) if rel in self.file_rels else 0
        FilePreview(self, idx, parent=self).exec()

    def _flush_click(self) -> None:
        item, self._click_item = self._click_item, None
        if item is not None:
            self.toggle_mark(item)

    def toggle_mark(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole + 1):
            return
        rel = item.data(Qt.UserRole)
        self.mark_path(rel, rel not in self.marked)

    def _item_for(self, rel: str) -> QListWidgetItem | None:
        for n in range(self.list.count()):
            it = self.list.item(n)
            if it.data(Qt.UserRole) == rel:
                return it
        return None

    def mark_path(self, rel: str, on: bool) -> None:
        """Mark/unmark a file by its relative path (used by the preview)."""
        if on:
            self.marked.add(rel)
        else:
            self.marked.discard(rel)
        item = self._item_for(rel)
        if item is not None:
            item.setIcon(self._file_icon(rel, self.root / rel, marked=on))
        self._refresh_buttons()

    def _file_items(self) -> list[QListWidgetItem]:
        return [self.list.item(n) for n in range(self.list.count())
                if not self.list.item(n).data(Qt.UserRole + 1)]

    def _toggle_all(self) -> None:
        items = self._file_items()
        if not items:
            return
        check = any(i.data(Qt.UserRole) not in self.marked for i in items)
        for i in items:
            rel = i.data(Qt.UserRole)
            if check:
                self.marked.add(rel)
            else:
                self.marked.discard(rel)
            i.setIcon(self._file_icon(rel, self.root / rel, marked=check))
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        n = len(self.marked)
        self.delete_btn.setEnabled(n > 0)
        self.delete_btn.setText(
            f"Delete marked files ({n})" if n else "Delete marked files")
        self.count_label.setText(f"{n} marked" if n else "")

    # -- deletion ------------------------------------------------------------
    def _delete_marked(self) -> None:
        paths = sorted(self.marked)
        if not paths:
            return
        noun = "file" if len(paths) == 1 else "files"
        if QMessageBox.question(
                self, "Delete files",
                f"Delete {len(paths)} marked {noun} from disk?\n\n"
                "This cannot be undone.") != QMessageBox.Yes:
            return

        deleted, failed = 0, []
        for rel in paths:
            target = (self.root / rel).resolve()
            if self.root not in target.parents or target.is_dir():
                failed.append(f"{rel}: refused")
                continue
            try:
                target.unlink()
                deleted += 1
            except OSError as exc:
                failed.append(f"{rel}: {exc}")

        self.browse(self.path)         # clears marks and re-reads the folder
        if failed:
            QMessageBox.warning(
                self, "Some files were not deleted",
                f"Deleted {deleted} {noun}.\n\n" + "\n".join(failed[:20]))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class _ZoomScroll(QScrollArea):
    """A scroll area whose wheel zooms instead of scrolling."""

    def __init__(self, on_wheel, parent=None):
        super().__init__(parent)
        self._on_wheel = on_wheel

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() != 0:
            self._on_wheel(event.angleDelta().y() > 0)
            event.accept()
            return
        super().wheelEvent(event)


class _PanLabel(QLabel):
    """A label you can drag to pan a zoomed image, double-click to fit."""

    def __init__(self, scroll: QScrollArea, on_double, parent=None):
        super().__init__(parent)
        self._scroll = scroll
        self._on_double = on_double
        self._start = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._start = (event.position().toPoint(),
                           self._scroll.horizontalScrollBar().value(),
                           self._scroll.verticalScrollBar().value())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._start is not None:
            start, hx, hy = self._start
            delta = event.position().toPoint() - start
            self._scroll.horizontalScrollBar().setValue(hx - delta.x())
            self._scroll.verticalScrollBar().setValue(hy - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._on_double()
        super().mouseDoubleClickEvent(event)


class FilePreview(QDialog):
    """Single-item preview: images zoom (slider + wheel + drag), everything
    else shows a placeholder with an "Open in player" action. Prev/next walk
    the folder's files; a checkbox mirrors the parent's mark-for-deletion set.
    """

    def __init__(self, browser: FileBrowser, index: int, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.files = list(browser.file_rels)
        self.index = index
        self.scale = 1.0
        self._full: QPixmap | None = None

        self.setWindowTitle("Preview")
        self.setStyleSheet(STYLE)
        self.resize(920, 720)
        if parent is not None:
            self.move(parent.mapToGlobal(parent.rect().topLeft()) + QPoint(24, 24))

        self._build()
        self._show_index(index)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QWidget()
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(16, 12, 16, 12)
        top_lay.setSpacing(12)

        self.name_label = QLabel("")
        self.name_label.setObjectName("title")
        top_lay.addWidget(self.name_label)

        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        top_lay.addWidget(self.count_label)
        top_lay.addStretch(1)

        self.mark_box = QCheckBox("Mark for deletion")
        self.mark_box.toggled.connect(self._on_mark)
        top_lay.addWidget(self.mark_box)

        self.open_btn = QPushButton("Open in player")
        self.open_btn.clicked.connect(self._open_current)
        top_lay.addWidget(self.open_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("close")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.reject)
        top_lay.addWidget(close_btn)
        lay.addWidget(top)

        self.scroll = _ZoomScroll(self._on_wheel)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.image_label = _PanLabel(self.scroll, lambda: self._set_zoom(1.0))
        self.image_label.setStyleSheet("background:#0e1013; border:none")
        self.scroll.setWidget(self.image_label)
        lay.addWidget(self.scroll, 1)

        nav = QWidget()
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(16, 10, 16, 10)
        nav_lay.setSpacing(12)

        self.prev_btn = QPushButton("‹")
        self.prev_btn.setToolTip("Previous (←)")
        self.prev_btn.clicked.connect(lambda: self._nav(-1))
        nav_lay.addWidget(self.prev_btn)

        self.next_btn = QPushButton("›")
        self.next_btn.setToolTip("Next (→)")
        self.next_btn.clicked.connect(lambda: self._nav(1))
        nav_lay.addWidget(self.next_btn)

        nav_lay.addWidget(QLabel("Zoom"))

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(25, 600)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(self._on_slider)
        nav_lay.addWidget(self.slider, 1)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("muted")
        self.zoom_label.setMinimumWidth(44)
        nav_lay.addWidget(self.zoom_label)
        lay.addWidget(nav)

    # -- current item --------------------------------------------------------
    def _show_index(self, i: int) -> None:
        self.index = i
        rel = self.files[i]
        path = self.browser.root / rel
        name = Path(rel).name

        self.name_label.setText(name)
        self.count_label.setText(f"{i + 1} / {len(self.files)}")

        self.mark_box.blockSignals(True)
        self.mark_box.setChecked(rel in self.browser.marked)
        self.mark_box.blockSignals(False)

        self._full = None
        self.scale = 1.0
        self.slider.blockSignals(True)
        self.slider.setValue(100)
        self.slider.blockSignals(False)
        self.zoom_label.setText("100%")

        self.prev_btn.setEnabled(i > 0)
        self.next_btn.setEnabled(i < len(self.files) - 1)

        if path.suffix.lower() in IMAGE_EXT:
            self.slider.setEnabled(True)
            self.open_btn.setVisible(False)
            self._load_image(path)
        else:
            self.slider.setEnabled(False)
            self.open_btn.setVisible(True)
            self._show_placeholder(name)

    def _load_image(self, path: Path) -> None:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            self._show_placeholder(Path(path).name)
            return
        self._full = QPixmap.fromImage(img)
        self._apply_zoom()

    def _show_placeholder(self, name: str) -> None:
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(
            f"{name}\n\nNo preview for this file type.\n"
            "Use 'Open in player' to view it.")
        self.image_label.setAlignment(Qt.AlignCenter)
        vp = self.scroll.viewport().size()
        self.image_label.resize(max(1, vp.width() - 8), max(1, vp.height() - 8))

    def _apply_zoom(self) -> None:
        if self._full is None:
            return
        self.image_label.setAlignment(Qt.AlignCenter)
        vp = self.scroll.viewport().size()
        iw, ih = self._full.width(), self._full.height()
        fit = min(vp.width() / iw, vp.height() / ih) if iw and ih else 1.0
        w = max(1, int(iw * fit * self.scale))
        h = max(1, int(ih * fit * self.scale))
        pm = self._full.scaled(QSize(w, h), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        self.image_label.setPixmap(pm)
        self.image_label.resize(pm.size())
        pan = (self.scroll.horizontalScrollBar().maximum() > 0 or
               self.scroll.verticalScrollBar().maximum() > 0)
        self.image_label.setCursor(
            Qt.ClosedHandCursor if pan else Qt.ArrowCursor)

    # -- controls ------------------------------------------------------------
    def _on_slider(self, val: int) -> None:
        self.scale = val / 100.0
        self.zoom_label.setText(f"{val}%")
        self._apply_zoom()

    def _set_zoom(self, scale: float) -> None:
        self.scale = max(0.25, min(6.0, scale))
        val = round(self.scale * 100)
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.slider.blockSignals(False)
        self.zoom_label.setText(f"{val}%")
        self._apply_zoom()

    def _on_wheel(self, up: bool) -> None:
        if self._full is None:
            return
        self._set_zoom(self.scale * (1.15 if up else 1 / 1.15))

    def _nav(self, d: int) -> None:
        ni = self.index + d
        if 0 <= ni < len(self.files):
            self._show_index(ni)

    def _on_mark(self, checked: bool) -> None:
        self.browser.mark_path(self.files[self.index], checked)

    def _open_current(self) -> None:
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.browser.root / self.files[self.index])))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key_Left:
            self._nav(-1)
        elif event.key() == Qt.Key_Right:
            self._nav(1)
        else:
            super().keyPressEvent(event)
