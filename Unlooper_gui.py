#!/usr/bin/env python3
"""GUI front-end for Unlooper.py — runs the existing CLI tool as a subprocess
and shows live progress, results, and the rendered toolpath image.

Unlooper.py is a self-contained script (all logic lives inside its own
`if __name__ == "__main__":` block) and is invoked exactly as documented in
the README: `python Unlooper.py "<file>" <0|1>`. This GUI does not modify or
import that script — it launches it as a child process and parses its
console output.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, Slot
from PySide6.QtGui import QPixmap, QFontMetrics, QPainter, QColor
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QCheckBox, QFileDialog, QProgressBar, QPlainTextEdit,
    QGroupBox, QSplitter, QDoubleSpinBox, QListWidget, QListWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
)

SCRIPT_DIR = Path(__file__).resolve().parent
UNLOOPER_SCRIPT = SCRIPT_DIR / "Unlooper.py"

TQDM_RE = re.compile(r"^\s*(\d+)%\|")
DISTANCE_RE = re.compile(r"Distance travelled:\s*([\d.]+)\s*m")
TIME_RE = re.compile(r"Total Time:\s*(.+)")
MATERIAL_RE = re.compile(r"Material Used:\s*([\d.]+)\s*mg")
SIZE_RE = re.compile(r"Total size used x:\s*([\-\d.]+)\s*y:\s*([\-\d.]+)")
PRECISE_SAVED_RE = re.compile(r"Precise image saved:\s*(.+)")
PREVIEW_SVG_SAVED_RE = re.compile(r"Preview vector saved:\s*(.+)")


class ZoomableView(QGraphicsView):
    """Pan/zoom viewer for either the vector preview (.svg) or the precise render
    (.png) - scroll to zoom, drag to pan, double-click to fit. Used for both so
    either one gets the same NCViewer-style interaction: an SVG here has no
    resolution ceiling at all, and even the raster PNG benefits from being able
    to zoom in past what a fixed-size thumbnail could show."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("background:#1e1e1e; border:none;")
        self.setMinimumHeight(300)
        self._item = None
        self._fitted = True  # re-fit on resize until the user zooms/pans by hand
        self.show_placeholder("No image yet — enable “Precise image” or “Fast preview”")

    def show_placeholder(self, text: str):
        self._scene.clear()
        self._item = None
        item = self._scene.addText(text)
        item.setDefaultTextColor(QColor("#888"))
        self._scene.setSceneRect(item.boundingRect())
        self._fitted = True
        self.fit()

    def show_svg(self, path) -> bool:
        item = QGraphicsSvgItem(str(path))
        if not item.renderer().isValid():
            return False
        self._scene.clear()
        self._scene.addItem(item)
        self._scene.setSceneRect(item.boundingRect())
        self._item = item
        self._fitted = True
        self.fit()
        return True

    def show_pixmap(self, path) -> bool:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._scene.clear()
        item = QGraphicsPixmapItem(pixmap)
        self._scene.addItem(item)
        self._scene.setSceneRect(item.boundingRect())
        self._item = item
        self._fitted = True
        self.fit()
        return True

    def fit(self):
        if self.viewport().width() > 0 and self.viewport().height() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep auto-fitting through layout/window resizes, but stop the instant the
        # user zooms or pans by hand - resizing shouldn't yank their view back.
        if self._fitted:
            self.fit()

    def wheelEvent(self, event):
        if self._item is None:
            return
        self._fitted = False
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if self._item is not None:
            self._fitted = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.fit()
        super().mouseDoubleClickEvent(event)


class UnlooperWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Unlooper")
        self.resize(1200, 800)

        self.input_files = []
        self._active_file = None
        self._queue_index = -1
        self._batch_cancelled = False
        self.output_base_dir = SCRIPT_DIR
        self.process = None
        self._out_buffer = ""
        self._run_completed = False
        self._displayed_image_path = None
        self._displayed_image_tag = None
        self._vector_preview_path = None

        self._build_ui()
        self._update_run_enabled()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top bar: file picker
        top = QHBoxLayout()
        self.open_btn = QPushButton("Open G-code Files…")
        self.open_btn.clicked.connect(self._open_files)
        top.addWidget(self.open_btn)
        self.clear_files_btn = QPushButton("Clear")
        self.clear_files_btn.setEnabled(False)
        self.clear_files_btn.clicked.connect(self._clear_files)
        top.addWidget(self.clear_files_btn)
        self.file_label = QLabel("No files selected")
        top.addWidget(self.file_label, stretch=1)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: options + results
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        queue_group = QGroupBox("Files Queue")
        queue_layout = QVBoxLayout(queue_group)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(120)
        self.queue_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        queue_layout.addWidget(self.queue_list)
        left_layout.addWidget(queue_group)

        opts_group = QGroupBox("Options")
        opts_layout = QVBoxLayout(opts_group)
        self.unloop_only_check = QCheckBox("Unloop only (skip analysis + all rendering)")
        self.unloop_only_check.setToolTip(
            "Matches the second command-line argument to Unlooper.py.\n"
            "Off = full run (unloop + analyze + render)."
        )
        self.unloop_only_check.toggled.connect(self._on_unloop_only_toggled)
        opts_layout.addWidget(self.unloop_only_check)

        self.precise_check = QCheckBox("Precise image (exact fibre width + pass overlap, slow)")
        self.precise_check.setChecked(True)
        self.precise_check.setToolTip(
            "The full-resolution raster render at 10 µm/px\n"
            "(…_cv2_Image_output.png). Scales with part area.\n"
            "Shown below in the same pan/zoom viewer as the preview."
        )
        opts_layout.addWidget(self.precise_check)

        self.preview_check = QCheckBox("Fast preview")
        self.preview_check.setToolTip(
            "An interactive vector render of the toolpath (…_preview.svg),\n"
            "shown below with pan/zoom. No line thickness or pass overlap,\n"
            "but no resolution ceiling either - arcs are exact and it zooms\n"
            "as far as the toolpath data resolves, NCViewer-style. Renders\n"
            "in a fraction of the time and appears before the precise image."
        )
        opts_layout.addWidget(self.preview_check)
        left_layout.addWidget(opts_group)

        overrides_group = QGroupBox("Overrides (leave at 0 to use the file's own values)")
        overrides_grid = QGridLayout(overrides_group)
        overrides_grid.addWidget(QLabel("Feed rate (mm/min):"), 0, 0)
        self.feedrate_spin = QDoubleSpinBox()
        self.feedrate_spin.setRange(0, 1_000_000)
        self.feedrate_spin.setDecimals(2)
        self.feedrate_spin.setSingleStep(10)
        self.feedrate_spin.setToolTip(
            "If > 0, dictates the estimated time directly:\n"
            "time = total distance / feed rate.\n"
            "Ignores the feed rates written in the G-code.\n"
            "Leave at 0 to use the G-code's own feed rates."
        )
        overrides_grid.addWidget(self.feedrate_spin, 0, 1)

        overrides_grid.addWidget(QLabel("Flow rate (mg/min):"), 1, 0)
        self.flowrate_spin = QDoubleSpinBox()
        self.flowrate_spin.setRange(0, 1_000_000)
        self.flowrate_spin.setDecimals(3)
        self.flowrate_spin.setSingleStep(0.1)
        self.flowrate_spin.setToolTip(
            "If > 0, dictates material used directly:\n"
            "material (mg) = flow rate x total time (minutes).\n"
            "Raw multiply, no unit conversion applied.\n"
            "Leave at 0 to use the file's fibre diameter / material density."
        )
        overrides_grid.addWidget(self.flowrate_spin, 1, 1)
        left_layout.addWidget(overrides_group)

        out_group = QGroupBox("Output Folder")
        out_layout = QVBoxLayout(out_group)
        self.output_dir_label = QLabel(str(SCRIPT_DIR))
        self.output_dir_label.setWordWrap(True)
        self.output_dir_label.setToolTip(
            "Unlooper.py creates its 'Output/<name>/' folder inside whichever\n"
            "directory it is run from. Choose that base directory here."
        )
        out_layout.addWidget(self.output_dir_label)
        browse_output_btn = QPushButton("Browse…")
        browse_output_btn.clicked.connect(self._browse_output_dir)
        out_layout.addWidget(browse_output_btn)
        left_layout.addWidget(out_group)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.setFixedHeight(36)
        f = self.run_btn.font()
        f.setBold(True)
        self.run_btn.setFont(f)
        self.run_btn.clicked.connect(self._start_run)
        run_row.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_run)
        run_row.addWidget(self.cancel_btn)
        left_layout.addLayout(run_row)

        results_group = QGroupBox("Results")
        results_grid = QGridLayout(results_group)
        self._result_labels = {}
        for row, (key, caption) in enumerate([
            ("distance", "Distance travelled"),
            ("time", "Estimated time"),
            ("material", "Material used"),
            ("size", "Build size used (x, y)"),
        ]):
            cap = QLabel(caption + ":")
            val = QLabel("—")
            val.setStyleSheet("font-weight:bold;")
            results_grid.addWidget(cap, row, 0)
            results_grid.addWidget(val, row, 1)
            self._result_labels[key] = val
        left_layout.addWidget(results_group)

        self.output_folder_btn = QPushButton("Open Output Folder")
        self.output_folder_btn.setEnabled(False)
        self.output_folder_btn.clicked.connect(self._open_output_folder)
        left_layout.addWidget(self.output_folder_btn)

        self.open_vector_btn = QPushButton("Open Preview in Browser")
        self.open_vector_btn.setEnabled(False)
        self.open_vector_btn.setToolTip(
            "Opens the same .svg toolpath shown below in your default browser -\n"
            "useful for a bigger window or for sharing the file with someone else."
        )
        self.open_vector_btn.clicked.connect(self._open_vector_preview)
        left_layout.addWidget(self.open_vector_btn)

        left_layout.addStretch()

        # Right: image preview + console log
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        viewer_container = QWidget()
        viewer_layout = QVBoxLayout(viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(2)
        viewer_toolbar = QHBoxLayout()
        viewer_hint = QLabel("Scroll to zoom · drag to pan · double-click to fit")
        viewer_hint.setStyleSheet("color:#888; font-size:10px;")
        viewer_toolbar.addWidget(viewer_hint)
        viewer_toolbar.addStretch()
        fit_btn = QPushButton("Fit")
        fit_btn.setFixedWidth(50)
        fit_btn.clicked.connect(lambda: self.viewer.fit())
        viewer_toolbar.addWidget(fit_btn)
        viewer_layout.addLayout(viewer_toolbar)
        self.viewer = ZoomableView()
        viewer_layout.addWidget(self.viewer, stretch=1)
        right_splitter.addWidget(viewer_container)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        right_splitter.addWidget(self.log)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)

        splitter.addWidget(left)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        root.addWidget(self.status_label)

    # ── File selection ───────────────────────────────────────────────────

    def _open_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open G-code File(s)", "",
            "G-code / Text Files (*.gcode *.txt *.nc *.tap);;All Files (*)",
        )
        if not paths:
            return
        self.input_files = paths
        self.queue_list.clear()
        for p in self.input_files:
            item = QListWidgetItem(Path(p).name)
            item.setToolTip(p)
            self.queue_list.addItem(item)
        self._elide(
            self.file_label,
            paths[0] if len(paths) == 1 else f"{len(paths)} files selected",
        )
        self._active_file = None
        self._queue_index = -1
        self._reset_results()
        self._update_run_enabled()

    def _clear_files(self):
        self.input_files = []
        self._active_file = None
        self._queue_index = -1
        self.queue_list.clear()
        self._elide(self.file_label, "No files selected")
        self._reset_results()
        self._update_run_enabled()

    def _set_queue_status(self, index: int, status: str):
        item = self.queue_list.item(index)
        if item is not None:
            item.setText(f"{Path(self.input_files[index]).name}  —  {status}")

    def _elide(self, label: QLabel, text: str):
        # Long g-code filenames/paths would otherwise force the label - and with it
        # the window - wider. Elide to the label's current width and keep the full
        # text as a tooltip so it's still available on hover.
        label.setProperty("_fullText", text)
        label.setToolTip(text)
        metrics = QFontMetrics(label.font())
        available = max(label.width(), 60)
        label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, available))

    def _set_status(self, text: str):
        self._elide(self.status_label, text)

    def _reelide_labels(self):
        for label in (self.file_label, self.status_label):
            full = label.property("_fullText")
            if full:
                self._elide(label, full)

    def _update_run_enabled(self):
        running = self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning
        self.run_btn.setEnabled(bool(self.input_files) and not running)
        self.cancel_btn.setEnabled(running)
        self.open_btn.setEnabled(not running)
        self.clear_files_btn.setEnabled(bool(self.input_files) and not running)

    def _on_unloop_only_toggled(self, checked: bool):
        # "Unloop only" means no analysis and no rendering at all
        self.precise_check.setEnabled(not checked)
        self.preview_check.setEnabled(not checked)

    def _render_mode(self) -> str:
        if self.unloop_only_check.isChecked():
            return "none"
        precise = self.precise_check.isChecked()
        preview = self.preview_check.isChecked()
        if precise and preview:
            return "both"
        if precise:
            return "precise"
        if preview:
            return "preview"
        return "none"

    def _reset_results(self):
        self._run_completed = False
        self._displayed_image_path = None
        self._displayed_image_tag = None
        self._vector_preview_path = None
        for lbl in self._result_labels.values():
            lbl.setText("—")
        self.viewer.show_placeholder("No image yet — enable “Precise image” or “Fast preview”")
        self.output_folder_btn.setEnabled(False)
        self.open_vector_btn.setEnabled(False)

    # ── Run / cancel ─────────────────────────────────────────────────────

    def _stem(self) -> str:
        return Path(self._active_file).stem

    def _output_dir(self) -> Path:
        return self.output_base_dir / "Output" / self._stem()

    @Slot()
    def _browse_output_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose Output Base Folder", str(self.output_base_dir),
        )
        if not chosen:
            return
        self.output_base_dir = Path(chosen)
        self.output_dir_label.setText(str(self.output_base_dir))
        self._reset_results()

    @Slot()
    def _start_run(self):
        if not self.input_files or not UNLOOPER_SCRIPT.exists():
            self._set_status(f"Error: cannot find {UNLOOPER_SCRIPT}")
            return

        self.log.clear()
        self._batch_cancelled = False
        self._queue_index = -1
        self._run_next_file()

    def _run_next_file(self):
        self._queue_index += 1
        if self._queue_index >= len(self.input_files):
            self._set_status(f"All {len(self.input_files)} file(s) complete")
            self._update_run_enabled()
            return

        self._active_file = self.input_files[self._queue_index]
        n = len(self.input_files)
        position = self._queue_index + 1
        name = Path(self._active_file).name

        self._reset_results()
        self._out_buffer = ""
        self.progress_bar.setValue(0)
        self._set_status(f"[{position}/{n}] Starting {name}…")
        self.log.appendPlainText(f"\n===== [{position}/{n}] {name} =====")
        self._set_queue_status(self._queue_index, "Running")

        unloop_only = "1" if self.unloop_only_check.isChecked() else "0"
        feedrate = str(self.feedrate_spin.value())
        flowrate = str(self.flowrate_spin.value())
        render_mode = self._render_mode()

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")

        self.process = QProcess(self)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.output_base_dir))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.setProgram(sys.executable)
        self.process.setArguments([str(UNLOOPER_SCRIPT), self._active_file, unloop_only, feedrate, flowrate, render_mode])
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_process_error)
        self.process.start()
        self._update_run_enabled()

    @Slot()
    def _cancel_run(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self._batch_cancelled = True
            self._set_status("Cancelling…")
            self.process.kill()

    # ── Output parsing ───────────────────────────────────────────────────

    @Slot()
    def _on_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._out_buffer += data
        parts = re.split(r"[\r\n]", self._out_buffer)
        self._out_buffer = parts[-1]
        for line in parts[:-1]:
            line = line.strip()
            if not line:
                continue
            m = TQDM_RE.match(line)
            if m:
                self.progress_bar.setValue(int(m.group(1)))
                self._set_status(f"Rendering image… {m.group(1)}%")
                continue
            self.log.appendPlainText(line)
            self._set_status(line)
            self._parse_metrics(line)
            m = PREVIEW_SVG_SAVED_RE.search(line)
            if m:
                self._show_image(m.group(1).strip(), "preview")
            m = PRECISE_SAVED_RE.search(line)
            if m:
                self._show_image(m.group(1).strip(), "precise")

    def _parse_metrics(self, line: str):
        m = DISTANCE_RE.search(line)
        if m:
            self._result_labels["distance"].setText(f"{m.group(1)} m")
        m = TIME_RE.search(line)
        if m:
            self._result_labels["time"].setText(m.group(1).strip())
        m = MATERIAL_RE.search(line)
        if m:
            self._result_labels["material"].setText(f"{m.group(1)} mg")
        m = SIZE_RE.search(line)
        if m:
            self._result_labels["size"].setText(f"{m.group(1)} mm, {m.group(2)} mm")

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, exit_code, exit_status):
        self._run_completed = True
        if exit_status == QProcess.ExitStatus.CrashExit:
            status = "Cancelled" if self._batch_cancelled else "Crashed"
            self._set_status(status)
            self.progress_bar.setValue(0)
            self._set_queue_status(self._queue_index, status)
        elif exit_code == 0:
            self._set_status("Done")
            self.progress_bar.setValue(100)
            if self._displayed_image_path is None:
                # Nothing was announced on stdout — fall back to whatever is on disk
                for name, tag in (
                    (f"{self._stem()}_cv2_Image_output.png", "precise"),
                    (f"{self._stem()}_preview.svg", "preview"),
                ):
                    cand = self._output_dir() / name
                    if cand.exists():
                        self._show_image(str(cand), tag)
                        break
                else:
                    self.viewer.show_placeholder("No image was generated for this run")
            if self._vector_preview_path is None:
                # Independent of what's shown above (precise supersedes preview there) -
                # the "open in browser" button should still light up if a preview exists.
                cand = self._output_dir() / f"{self._stem()}_preview.svg"
                if cand.exists():
                    self._vector_preview_path = cand
                    self.open_vector_btn.setEnabled(True)
            self._set_queue_status(self._queue_index, "Done")
        else:
            self._set_status(f"Failed (exit code {exit_code}) — see log")
            self._set_queue_status(self._queue_index, "Failed")
        self.output_folder_btn.setEnabled(self._output_dir().exists())

        if self._batch_cancelled:
            self._update_run_enabled()
            return
        self._run_next_file()

    @Slot(QProcess.ProcessError)
    def _on_process_error(self, error):
        self.log.appendPlainText(f"[process error] {error}")

    def _resolve_output_path(self, path) -> Path:
        # Unlooper.py prints paths relative to its working directory (output_base_dir)
        p = Path(path)
        return p if p.is_absolute() else self.output_base_dir / p

    def _show_image(self, path, tag):
        p = self._resolve_output_path(path)
        if not p.exists():
            return
        # The precise render supersedes the fast preview, never the other way round
        if tag == "preview" and self._displayed_image_tag == "precise":
            return
        ok = self.viewer.show_svg(p) if tag == "preview" else self.viewer.show_pixmap(p)
        if not ok:
            return
        self._displayed_image_path = p
        self._displayed_image_tag = tag
        if tag == "preview":
            # This is the same file the "open in browser" button opens
            self._vector_preview_path = p
            self.open_vector_btn.setEnabled(True)
            if self._render_mode() == "both":
                self._set_status("Showing fast preview — precise image still rendering…")
            else:
                self._set_status("Showing fast preview")
        else:
            self._set_status("Showing precise image")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reelide_labels()

    # ── Output folder ────────────────────────────────────────────────────

    def _open_path(self, path: Path):
        # Hand a file or folder to whatever the OS considers its default opener
        # (Explorer/an app on Windows, Finder/an app on macOS, xdg-open elsewhere -
        # for an .svg that's the system's default browser).
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])

    def _open_output_folder(self):
        folder = self._output_dir()
        if folder.exists():
            self._open_path(folder)

    def _open_vector_preview(self):
        if self._vector_preview_path and self._vector_preview_path.exists():
            self._open_path(self._vector_preview_path)

    def closeEvent(self, event):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Unlooper")
    win = UnlooperWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
