# ui/waterfall_widget.py
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6 import QtCore
from PySide6.QtCore import Qt
import pyqtgraph as pg
import numpy as np


class WaterfallWidget(QWidget):
    """
    Waterfall estilo SDR Control:
    - Colormap tipo SDR (CET-L9)
    - Auto-contrast (AGC visual) con toggle "Auto"
    - Barra lateral de escala (HistogramLUTItem) como en SDR
    - Marcador verde 3 líneas
    - Click/drag para sintonizar
    """
    sig_tune = QtCore.Signal(float, bool)

    MODE_BW_HZ = {
        "NFM": 12_500,
        "FMN": 12_500,
        "AM": 10_000,
        "USB": 2_700,
        "LSB": 2_700,
        "FM": 200_000,
    }

    def __init__(self, parent=None, history_lines: int = 420, default_width: int = 512):
        super().__init__(parent)

        self.history_lines = int(history_lines)
        self.width = int(default_width)
        self.reverse = False

        # niveles SDR-like
        self.levels = (-130.0, -40.0)

        # Auto-contrast suave (visual AGC)
        self.auto_contrast = True
        self._contrast_alpha = 0.08
        self._levels_smooth = list(self.levels)

        self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)

        # Layout gráfico con barra lateral LUT
        self.view = pg.GraphicsLayoutWidget()

        self.plot = self.view.addPlot(row=0, col=0)

        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.showGrid(x=True, y=False, alpha=0.12)

        self.plot.showAxis("bottom")
        self.plot.setLabel("bottom", "", units=None)
        self.plot.getAxis("bottom").setTextPen(pg.mkPen("#cbd5e1"))
        self.plot.getAxis("bottom").setPen(pg.mkPen("#6b7280"))

        self.plot.hideAxis("left")

        # ImageItem
        self.img_item = pg.ImageItem()
        self.img_item.setOpts(axisOrder="row-major")
        self.plot.addItem(self.img_item)

        # Colormap SDR-like
        try:
            cmap = pg.colormap.get("CET-L9")
        except Exception:
            cmap = pg.colormap.get("viridis")

        self._lut = cmap.getLookupTable(0.0, 1.0, 256)
        self.img_item.setLookupTable(self._lut)
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

                

        layout.addWidget(self.view)

        # freq mapping
        self._f_start_hz = 0.0
        self._f_stop_hz = float(self.width)
        self._rect_dirty = True

        # Modo/ancho para marcador
        self._mode = "NFM"
        self._tuned_hz = None

        # Tune lines: izquierda/centro/derecha
        self.tune_left = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.1))
        self.tune_center = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=2.0))
        self.tune_right = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.1))
        self.plot.addItem(self.tune_left)
        self.plot.addItem(self.tune_center)
        self.plot.addItem(self.tune_right)

        # Badge inferior-centro (Freq/Span)
        self.center_box = pg.TextItem(color="#e5e7eb", anchor=(0.5, 0.5))
        self.center_box.setZValue(10)
        self.plot.addItem(self.center_box)
        self.center_box.setHtml(
            "<div style='background:rgba(2,6,23,0.80);"
            "border:1px solid rgba(34,197,94,0.55);"
            "border-radius:10px;padding:7px 12px;"
            "font-weight:900;letter-spacing:0.3px;'>—</div>"
        )

        self._drag_tuning = False
        self.plot.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)
        self.plot.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)

        self._apply_rect()

    # ----------------------------
    # Internos
    # ----------------------------
    def _bw_hz(self) -> float:
        return float(self.MODE_BW_HZ.get(self._mode, 12_500))

    def _apply_rect(self):
        self.img_item.setRect(
            QtCore.QRectF(
                self._f_start_hz / 1e6,
                0,
                (self._f_stop_hz - self._f_start_hz) / 1e6,
                self.history_lines
            )
        )
        self._rect_dirty = False

    def _update_marker(self):
        if self._tuned_hz is None:
            return

        c_hz = float(self._tuned_hz)
        bw = self._bw_hz()
        left = (c_hz - bw / 2.0) / 1e6
        center = c_hz / 1e6
        right = (c_hz + bw / 2.0) / 1e6

        self.tune_left.setPos(left)
        self.tune_center.setPos(center)
        self.tune_right.setPos(right)

        x_center_mhz = ((self._f_start_hz + self._f_stop_hz) / 2.0) / 1e6
        self.center_box.setPos(x_center_mhz, self.history_lines * 0.82)

    def _auto_levels_from_line(self, arr: np.ndarray):
        lo = float(np.percentile(arr, 8))
        hi = float(np.percentile(arr, 98))
        if hi - lo < 15:
            hi = lo + 15

        a = self._contrast_alpha
        self._levels_smooth[0] = (1 - a) * self._levels_smooth[0] + a * lo
        self._levels_smooth[1] = (1 - a) * self._levels_smooth[1] + a * hi

        self._levels_smooth[0] = max(-160.0, min(-20.0, self._levels_smooth[0]))
        self._levels_smooth[1] = max(-160.0, min(-10.0, self._levels_smooth[1]))

        self.levels = (self._levels_smooth[0], self._levels_smooth[1])

    # ----------------------------
    # Config
    # ----------------------------
    def set_reverse(self, on: bool):
        self.reverse = bool(on)

    def set_levels(self, min_db: float, max_db: float):
        self.auto_contrast = False
        self.levels = (float(min_db), float(max_db))
        self._levels_smooth = [self.levels[0], self.levels[1]]
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

    def set_mode(self, mode: str):
        self._mode = (mode or "").upper().strip() or "NFM"
        self._update_marker()

    def set_auto(self, on: bool):
        self.auto_contrast = bool(on)
    # ----------------------------
    # API pública
    # ----------------------------
    def append_line(self, levels_db):
        arr = np.asarray(levels_db, dtype=np.float32)
        if arr.size == 0:
            return

        if arr.size != self.width:
            self.width = int(arr.size)
            self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)
            self._rect_dirty = True

        if self.auto_contrast:
            try:
                self._auto_levels_from_line(arr)
            except Exception:
                pass

        if not self.reverse:
            self.img[:-1, :] = self.img[1:, :]
            self.img[-1, :] = arr
        else:
            self.img[1:, :] = self.img[:-1, :]
            self.img[0, :] = arr

        if self._rect_dirty:
            try:
                self._apply_rect()
            except Exception:
                pass

        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)
        self._update_marker()

    def clear(self):
        self.img[:] = self.levels[0]
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

    def set_freq_axis(self, start_hz: float, stop_hz: float):
        self._f_start_hz = float(start_hz)
        self._f_stop_hz = float(stop_hz)
        self._rect_dirty = True
        self.plot.setXRange(self._f_start_hz / 1e6, self._f_stop_hz / 1e6, padding=0.0)
        self._update_marker()

    def set_tuned_freq(self, hz: float):
        self._tuned_hz = float(hz)
        self._update_marker()

        mhz = self._tuned_hz / 1e6
        span_khz = abs(self._f_stop_hz - self._f_start_hz) / 1000.0
        self.center_box.setHtml(
            "<div style='background:rgba(2,6,23,0.80);"
            "border:1px solid rgba(34,197,94,0.55);"
            "border-radius:10px;padding:7px 12px;"
            "font-weight:900;letter-spacing:0.35px;'>"
            f"Freq: {mhz:.6f} MHz<br/>Span: ±{span_khz/2.0:.0f} kHz"
            "</div>"
        )

    # ----------------------------
    # Dial manual (mouse)
    # ----------------------------
    def _mhz_from_scene_pos(self, scene_pos) -> float | None:
        try:
            vb = self.plot.getViewBox()
            p = vb.mapSceneToView(scene_pos)
            x_mhz = float(p.x())
        except Exception:
            return None

        try:
            xmin = float(self._f_start_hz) / 1e6
            xmax = float(self._f_stop_hz) / 1e6
            if xmax < xmin:
                xmin, xmax = xmax, xmin
            x_mhz = max(xmin, min(xmax, x_mhz))
        except Exception:
            pass

        return x_mhz

    def _on_scene_mouse_clicked(self, ev):
        try:
            if ev.button() != Qt.LeftButton:
                return
            scene_pos = ev.scenePos()
        except Exception:
            return

        mhz = self._mhz_from_scene_pos(scene_pos)
        if mhz is None:
            return
        self.sig_tune.emit(float(mhz), True)

    def _on_scene_mouse_moved(self, scene_pos):
        try:
            buttons = QtCore.QCoreApplication.mouseButtons()
            if not (buttons & Qt.LeftButton):
                self._drag_tuning = False
                return
        except Exception:
            return

        mhz = self._mhz_from_scene_pos(scene_pos)
        if mhz is None:
            return

        self._drag_tuning = True
        self.sig_tune.emit(float(mhz), False)



