# ui/waterfall_widget.py
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
import pyqtgraph as pg
import numpy as np
from PySide6 import QtCore



class WaterfallWidget(QWidget):
    """
    Waterfall tipo SDR Console.

    API:
      wf.append_line(levels_db)
      wf.set_reverse(True/False)
      wf.set_levels(min_db, max_db)
      wf.clear()
    """

    def __init__(self, parent=None, history_lines: int = 400, default_width: int = 512):
        super().__init__(parent)

        self.history_lines = int(history_lines)
        self.width = int(default_width)

        self.reverse = False  # False: nueva línea abajo (sube). True: nueva línea arriba (baja)
        self.levels = (-120.0, 0.0)

        self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)

        self.view = pg.GraphicsLayoutWidget()
        self.plot = self.view.addPlot()

        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.showGrid(x=True, y=False, alpha=0.2)

        # Mostrar eje X con MHz
        self.plot.showAxis("bottom")
        self.plot.setLabel("bottom", "Frecuencia", units="MHz")
        self.plot.getAxis("bottom").setTextPen(pg.mkPen("#9ca3af"))
        self.plot.getAxis("bottom").setPen(pg.mkPen("#374151"))

        # Eje Y oculto (tiempo)
        self.plot.hideAxis("left")




        self.img_item = pg.ImageItem()
        self.img_item = pg.ImageItem()
        self.img_item.setOpts(axisOrder='row-major')  # <<< CLAVE: filas hacia abajo (Y), columnas hacia la derecha (X)
        self.plot.addItem(self.img_item)

        self.plot.addItem(self.img_item)

        # Paleta tipo SDR
        try:
            cmap = pg.colormap.get("CET-L9")
        except Exception:
            cmap = pg.colormap.get("viridis")
        self.img_item.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))

        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)
        layout.addWidget(self.view)

        # Por defecto: eje X 0..(width-1) pero en MHz lo vamos a mapear con setRect
        self._f_start_hz = 0.0
        self._f_stop_hz = float(self.width)

        self.img_item.setRect(QtCore.QRectF(self._f_start_hz/1e6, 0, (self._f_stop_hz-self._f_start_hz)/1e6, self.history_lines))





    # ----------------------------
    # Config
    # ----------------------------
    def set_reverse(self, on: bool):
        self.reverse = bool(on)

    def set_levels(self, min_db: float, max_db: float):
        self.levels = (float(min_db), float(max_db))
        # refresca sin recalcular autoLevels
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

    # ----------------------------
    # API pública
    # ----------------------------
    def append_line(self, levels_db):
        """Agrega una nueva línea al waterfall (vertical)."""
        arr = np.asarray(levels_db, dtype=np.float32)
        if arr.size == 0:
            return

        # Si cambia tamaño, redimensiona
        if arr.size != self.width:
            self.width = int(arr.size)
            self.img = np.full((self.history_lines, self.width), self.levels[0], dtype=np.float32)

        if not self.reverse:
            # Nueva línea entra abajo; lo viejo sube
            self.img[:-1, :] = self.img[1:, :]
            self.img[-1, :] = arr
        else:
            # Nueva línea entra arriba; lo viejo baja
            self.img[1:, :] = self.img[:-1, :]
            self.img[0, :] = arr

        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)

        # Mantener mapeo X (MHz) al ancho actual
        try:
            self.img_item.setRect(QtCore.QRectF(self._f_start_hz/1e6, 0,
                                                (self._f_stop_hz - self._f_start_hz)/1e6,
                                                self.history_lines))
        except Exception:
            pass


        self.tune_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.2))
        self.plot.addItem(self.tune_line)


    def clear(self):
        self.img[:] = self.levels[0]
        self.img_item.setImage(self.img, autoLevels=False, levels=self.levels)



    def set_freq_axis(self, start_hz: float, stop_hz: float):
        """Define el rango de frecuencias visible en el eje X (en Hz)."""
        self._f_start_hz = float(start_hz)
        self._f_stop_hz = float(stop_hz)

        self.plot.setXRange(self._f_start_hz / 1e6, self._f_stop_hz / 1e6, padding=0.0)

    def set_tuned_freq(self, hz: float):
        """Mueve el marcador verde al tuned (Hz)."""
        self.tune_line.setPos(float(hz) / 1e6)
