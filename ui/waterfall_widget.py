# ui/waterfall_widget.py
from PySide6.QtWidgets import QWidget, QVBoxLayout
import pyqtgraph as pg
import numpy as np


class WaterfallWidget(QWidget):
    """
    Waterfall tipo SDR Console.

    Uso:
      wf = WaterfallWidget()
      wf.append_line(levels_db)

    Donde levels_db es un array/list de niveles en dB
    (mismo tamaño que la FFT usada en el Spectrum).
    """

    def __init__(self, parent=None, history_lines: int = 400):
        super().__init__(parent)

        self.history_lines = history_lines
        self.width = 512  # se ajustará al primer vector real
        self.img = np.full((self.history_lines, self.width), -120.0, dtype=float)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)

        self.view = pg.GraphicsLayoutWidget()
        self.plot = self.view.addPlot()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideAxis("bottom")
        self.plot.hideAxis("left")
        self.plot.setLabel("right", "Nivel", units="dB")

        self.img_item = pg.ImageItem()
        self.plot.addItem(self.img_item)

        # paleta tipo “azul SDR”
        try:
            cmap = pg.colormap.get("CET-L9")  # pyqtgraph >=0.12
            self.img_item.setLookupTable(cmap.getLookupTable())
        except Exception:
            # fallback a “viridis”
            cmap = pg.colormap.get("viridis")
            self.img_item.setLookupTable(cmap.getLookupTable())

        self.img_item.setImage(self.img, autoLevels=False, levels=(-120, 0))

        layout.addWidget(self.view)

    # --------------------------------------------------
    # API pública
    # --------------------------------------------------
    def append_line(self, levels_db):
        """
        Agrega una nueva línea a la parte inferior del waterfall.
        levels_db: iterable de floats (niveles en dB).
        """
        arr = np.asarray(levels_db, dtype=float)
        if arr.size == 0:
            return

        # Si el tamaño de la FFT cambia, redimensionamos
        if arr.size != self.width:
            self.width = arr.size
            self.img = np.full((self.history_lines, self.width), -120.0, dtype=float)

        # Desplazamos hacia arriba y metemos la nueva línea abajo
        self.img = np.roll(self.img, -1, axis=0)
        self.img[-1, :] = arr

        self.img_item.setImage(self.img, autoLevels=False, levels=(-120, 0))

    def clear(self):
        self.img[:] = -120.0
        self.img_item.setImage(self.img, autoLevels=False, levels=(-120, 0))
