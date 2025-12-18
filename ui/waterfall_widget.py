from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6 import QtCore
import pyqtgraph as pg
import numpy as np


class WaterfallWidget(QWidget):
    """
    Waterfall tipo SDR Console.

    API:
      wf.append_line(levels_db)
      wf.set_reverse(True/False)
      wf.set_levels(min_db, max_db)
      wf.clear()
      wf.set_freq_axis(start_hz, stop_hz)
      wf.set_tuned_freq(hz)
    """

    def __init__(self, parent=None, history_lines: int = 400, default_width: int = 512):
        super().__init__(parent)

        self.history_lines = int(history_lines)
        self.width = int(default_width)

        self.reverse = False
        self.levels = (-120.0, 0.0)

        self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)

        self.view = pg.GraphicsLayoutWidget()
        self.plot = self.view.addPlot()

        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.showGrid(x=True, y=False, alpha=0.15)

        # X axis MHz
        self.plot.showAxis("bottom")
        self.plot.setLabel("bottom", "Frecuencia", units="MHz")
        self.plot.getAxis("bottom").setTextPen(pg.mkPen("#9ca3af"))
        self.plot.getAxis("bottom").setPen(pg.mkPen("#374151"))

        # Y axis oculto (tiempo)
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
        self.img_item.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))

        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)
        layout.addWidget(self.view)

        # freq mapping
        self._f_start_hz = 0.0
        self._f_stop_hz = float(self.width)

        self.img_item.setRect(
            QtCore.QRectF(
                self._f_start_hz / 1e6,
                0,
                (self._f_stop_hz - self._f_start_hz) / 1e6,
                self.history_lines
            )
        )

        # Tune line (UNA sola vez)
        self.tune_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=2.0))
        self.plot.addItem(self.tune_line)

        # “Cuadro” centrado con frecuencia
        self.center_box = pg.TextItem(color="#e5e7eb", anchor=(0.5, 0.5))
        self.center_box.setZValue(10)
        self.plot.addItem(self.center_box)

        # Estilo “badge”
        self.center_box.setHtml(
            "<div style='background:rgba(2,6,23,0.75);"
            "border:1px solid rgba(34,197,94,0.55);"
            "border-radius:8px;padding:6px 10px;"
            "font-weight:700;'>—</div>"
        )

    # ----------------------------
    # Config
    # ----------------------------
    def set_reverse(self, on: bool):
        self.reverse = bool(on)

    def set_levels(self, min_db: float, max_db: float):
        self.levels = (float(min_db), float(max_db))
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

    # ----------------------------
    # API pública
    # ----------------------------
    def append_line(self, levels_db):
        arr = np.asarray(levels_db, dtype=np.float32)
        if arr.size == 0:
            return

        # resize si cambia FFT
        if arr.size != self.width:
            self.width = int(arr.size)
            self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)

        if not self.reverse:
            self.img[:-1, :] = self.img[1:, :]
            self.img[-1, :] = arr
        else:
            self.img[1:, :] = self.img[:-1, :]
            self.img[0, :] = arr

        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

        # mantener rect
        try:
            self.img_item.setRect(
                QtCore.QRectF(
                    self._f_start_hz / 1e6,
                    0,
                    (self._f_stop_hz - self._f_start_hz) / 1e6,
                    self.history_lines
                )
            )
        except Exception:
            pass

        # mover badge al centro del span visible
        try:
            x_center_mhz = ((self._f_start_hz + self._f_stop_hz) / 2.0) / 1e6
            self.center_box.setPos(x_center_mhz, self.history_lines * 0.5)
        except Exception:
            pass

    def clear(self):
        self.img[:] = self.levels[0]
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

    def set_freq_axis(self, start_hz: float, stop_hz: float):
        self._f_start_hz = float(start_hz)
        self._f_stop_hz = float(stop_hz)
        self.plot.setXRange(self._f_start_hz / 1e6, self._f_stop_hz / 1e6, padding=0.0)

    def set_tuned_freq(self, hz: float):
        mhz = float(hz) / 1e6
        self.tune_line.setPos(mhz)

        # actualizar texto del cuadro
        self.center_box.setHtml(
            "<div style='background:rgba(2,6,23,0.75);"
            "border:1px solid rgba(34,197,94,0.55);"
            "border-radius:8px;padding:6px 10px;"
            "font-weight:800;'>"
            f"{mhz:.6f} MHz"
            "</div>"
        )
