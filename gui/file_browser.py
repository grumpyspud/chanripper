"""Full-window file browser with mark-for-deletion.

Mirrors the web UI on purpose: a full-page layout, a breadcrumb, the same
"Mark all" / "Delete marked files" pair and the same dark palette. Marking is
one click on a tile (double-click opens it); a marked tile gets a red corner
badge and a red border. The media ledger is untouched by a delete, so a removed
file is never re-downloaded on the next check.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImageReader,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
THUMB = QSize(140, 92)

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
QPushButton#close{background:none; border:none; color:#8b93a1; font-size:16px;
                  padding:2px 8px}
QPushButton#danger:hover{border-color:#f85149; color:#f85149}
QListWidget{background:#14161a; border:none; outline:none}
QListWidget::item{background:#22262e; border:1px solid #2e333d; border-radius:8px;
                  color:#e6e9ef; padding:6px}
QListWidget::item:selected{background:#22262e; border:1px solid #f85149; color:#e6e9ef}
QScrollBar:vertical{background:#14161a; width:10px}
QScrollBar::handle:vertical{background:#2e333d; border-radius:5px; min-height:30px}
QScrollBar::add-line,QScrollBar::sub-line{height:0}
"""


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class FileBrowser(QDialog):
    """One folder at a time; only files can be marked."""

    def __init__(self, root: str | Path, start: str = "", title: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.root = Path(root).resolve()
        self.title = title or "Files"
        self.path = ""                       # relative to root
        self.marked: set[str] = set()
        self._pixmaps: dict[str, QPixmap] = {}

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
        self.list.setIconSize(THUMB)
        self.list.setGridSize(QSize(170, 172))
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
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
        self.marked.clear()            # marks never outlive the folder
        self._fill(target)
        self._render_crumbs()
        self._refresh_buttons()

    def _fill(self, folder: Path) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._pixmaps.clear()
        try:
            children = sorted(folder.iterdir(),
                              key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            children = []
        for child in children:
            is_dir = child.is_dir()
            try:
                size = 0 if is_dir else child.stat().st_size
            except OSError:
                continue
            rel = str(child.relative_to(self.root)).replace("\\", "/")
            label = f"{child.name}\n{'folder' if is_dir else human(size)}"
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
                    "double-click to open")
                item.setIcon(self._file_icon(rel, child, marked=False))
            self.list.addItem(item)
        self.list.blockSignals(False)

    # -- artwork -------------------------------------------------------------
    def _dir_icon(self) -> QIcon:
        pm = QPixmap(THUMB)
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
            reader.setScaledSize(THUMB)
            img = reader.read()
            if not img.isNull():
                pm = QPixmap.fromImage(img)
                self._pixmaps[rel] = pm
                return pm
        pm = QPixmap(THUMB)
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
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.root / item.data(Qt.UserRole))))

    def _flush_click(self) -> None:
        item, self._click_item = self._click_item, None
        if item is not None:
            self.toggle_mark(item)

    def toggle_mark(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole + 1):
            return
        rel = item.data(Qt.UserRole)
        if rel in self.marked:
            self.marked.discard(rel)
        else:
            self.marked.add(rel)
        path = self.root / rel
        item.setIcon(self._file_icon(rel, path, marked=rel in self.marked))
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
