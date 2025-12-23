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
    # Selector de vista (rectángulo) para navegar el rango SIN cambiar frecuencia sintonizada
    sig_view_center_changed = QtCore.Signal(float, bool)  # center_mhz, final
    MODE_BW_HZ = {
        "NFM": 12_500,
        "FMN": 12_500,
        "AM": 10_000,
        "USB": 2_700,
        "LSB": 2_700,
        "FM": 200_000,
    }

    sig_view_window_changed = QtCore.Signal(float, float, bool)  # x0_mhz, x1_mhz, final
    sig_nav_edge_recenter = QtCore.Signal(float)



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

                # ----------------------------
        # NAV BAR (regla inferior para navegar rangos)
        # ----------------------------
        self.nav = pg.PlotWidget()
        self.nav.setFixedHeight(38)
        self.nav.setMenuEnabled(False)
        self.nav.hideButtons()
        self.nav.setMouseEnabled(x=False, y=False)

                # ==========================================================
        # Rueda del mouse: NO hace zoom. Desplaza la frecuencia (línea verde)
        # Paso: 0.5 kHz = 0.0005 MHz (ajústalo si quieres 0.5 MHz)
        # ==========================================================
        self._wheel_step_mhz = 0.0005
        try:
            self._orig_view_wheel = self.view.wheelEvent
            self.view.wheelEvent = self._on_view_wheel_tune
        except Exception:
            pass
        try:
            self._orig_nav_wheel = self.nav.wheelEvent
            self.nav.wheelEvent = self._on_view_wheel_tune
        except Exception:
            pass


        nav_pi = self.nav.getPlotItem()
        nav_pi.showAxis("bottom")
        nav_pi.getAxis("bottom").setTextPen(pg.mkPen("#cbd5e1"))
        nav_pi.getAxis("bottom").setPen(pg.mkPen("#6b7280"))
        nav_pi.hideAxis("left")
        nav_pi.setContentsMargins(0, 0, 0, 0)
        nav_pi.vb.setDefaultPadding(0)

        # Y fijo (solo para dibujar el selector)
        self.nav.setYRange(0, 1, padding=0.0)
        self.nav.enableAutoRange(x=False, y=False)

        layout.addWidget(self.nav)

        self._sync_nav_region = False

        #RECTANGULO DEL DIAL
        self.nav_region = pg.LinearRegionItem(
            values=[0.0, 0.0],
            movable=True,
            swapMode="push",
            # Más visible (menos transparente) + borde más grueso
            # (verde suave para que destaque sin tapar demasiado)
            brush=pg.mkBrush(34, 197, 94, 70),          # <-- sube/baja este alpha (0-255)
            pen=pg.mkPen(34, 197, 94, 230, width=2),    # <-- borde más fuerte
        )
        self.nav_region.setZValue(5)
        nav_pi.addItem(self.nav_region)

        # Línea tuned en la regla (opcional, ayuda visual)
        self.nav_tuned = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#22c55e", width=1.3)
        )
        self.nav_tuned.setZValue(6)
        nav_pi.addItem(self.nav_tuned)

        self.nav_region.sigRegionChanged.connect(self._on_nav_region_live)
        self.nav_region.sigRegionChangeFinished.connect(self._on_nav_region_final)


        # freq mapping
        self._f_start_hz = 0.0
        self._f_stop_hz = float(self.width)
        self._rect_dirty = True

        # Modo/ancho para marcador
        self._mode = "NFM"
        self._tuned_hz = None

        # Tune lines: izquierda/centro/derecha
        self._sync_tune_line = False
        self.tune_left = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.1))
        self.tune_center = pg.InfiniteLine(
            angle=90,
            movable=True,  # ✅ ahora se puede arrastrar
            pen=pg.mkPen("#22c55e", width=2.2),
            hoverPen=pg.mkPen("#86efac", width=3.0),
        )
        self.tune_right = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.1))
        self.plot.addItem(self.tune_left)
        self.plot.addItem(self.tune_center)
        self.tune_center.sigPositionChanged.connect(self._on_tune_line_live)
        self.tune_center.sigPositionChangeFinished.connect(self._on_tune_line_final)
        self.plot.addItem(self.tune_right)

        # Selector de vista (rectángulo) para mover el rango visible del espectro superior
        self._sync_view_region = False
        self._view_w_mhz = None




        # Drag de la línea verde -> retune
        self.tune_center.sigPositionChanged.connect(self._on_tune_line_live)
        self.tune_center.sigPositionChangeFinished.connect(self._on_tune_line_final)


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

        # región por defecto (si aún no existe)
        if self._view_w_mhz is None:
            try:
                span_mhz = abs(self._f_stop_hz - self._f_start_hz) / 1e6
                self._view_w_mhz = max(0.0002, span_mhz * 0.25)
            except Exception:
                self._view_w_mhz = 0.002
        try:
            c = ((self._f_start_hz + self._f_stop_hz) / 2.0) / 1e6
            self.set_view_center(c, self._view_w_mhz)
        except Exception:
            pass


    def _update_marker(self):
        if self._tuned_hz is None:
            return

        c_hz = float(self._tuned_hz)
        bw = self._bw_hz()
        left = (c_hz - bw / 2.0) / 1e6
        center = c_hz / 1e6
        right = (c_hz + bw / 2.0) / 1e6

        # al mover programáticamente la línea verde, evitamos re-emisiones
        self._sync_tune_line = True
        try:
            self.tune_left.setPos(left)
            self.tune_center.setPos(center)
            self.tune_right.setPos(right)
        finally:
            self._sync_tune_line = False


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

        x0 = self._f_start_hz / 1e6
        x1 = self._f_stop_hz / 1e6

        self.plot.setXRange(x0, x1, padding=0.0)

        # regla abajo
        try:
            # Preserva la ventana del selector (no la resetees en cada frame)
            prev = None
            try:
                prev = self.nav_region.getRegion()
            except Exception:
                prev = None

            self.nav.setXRange(x0, x1, padding=0.0)

            span = max(0.00005, x1 - x0)
            if prev is not None:
                p0, p1 = prev
                p0 = float(p0); p1 = float(p1)
                if p1 < p0:
                    p0, p1 = p1, p0
                w = max(0.00005, p1 - p0)
                c = (p0 + p1) / 2.0
                w = min(w, span)
                self.set_nav_window(c - w/2.0, c + w/2.0)
            else:
                # selector inicial: 25% del span centrado
                w = span * 0.25
                c = (x0 + x1) / 2.0
                self.set_nav_window(c - w/2.0, c + w/2.0)
        except Exception:
            pass

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

        try:
            self.nav_tuned.setPos(self._tuned_hz / 1e6)
        except Exception:
            pass


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


    # ----------------------------
    # Selector de vista (rectángulo)
    # ----------------------------
    def _clamp_mhz(self, x_mhz: float) -> float:
        xmin = float(self._f_start_hz) / 1e6
        xmax = float(self._f_stop_hz) / 1e6
        if xmax < xmin:
            xmin, xmax = xmax, xmin
        return max(xmin, min(xmax, float(x_mhz)))

    def set_view_center(self, center_mhz: float, width_mhz: float | None = None):
        if width_mhz is not None:
            self._view_w_mhz = max(0.00005, float(width_mhz))
        if self._view_w_mhz is None:
            return

        w = float(self._view_w_mhz)
        c = self._clamp_mhz(float(center_mhz))
        left = c - w / 2.0
        right = c + w / 2.0

        xmin = float(self._f_start_hz) / 1e6
        xmax = float(self._f_stop_hz) / 1e6
        if xmax < xmin:
            xmin, xmax = xmax, xmin
        if left < xmin:
            left = xmin
            right = left + w
        if right > xmax:
            right = xmax
            left = right - w

        self._sync_view_region = True
        try:
            self.view_region.setRegion((left, right))
        finally:
            self._sync_view_region = False

    def set_view_window(self, x0_mhz: float, x1_mhz: float):
        x0 = float(x0_mhz)
        x1 = float(x1_mhz)
        if x1 < x0:
            x0, x1 = x1, x0
        w = max(0.00005, x1 - x0)
        c = (x0 + x1) / 2.0
        self.set_view_center(c, w)

    def _emit_view_center(self, final: bool):
        try:
            left, right = self.view_region.getRegion()
            c = (float(left) + float(right)) / 2.0
            self.sig_view_center_changed.emit(float(c), bool(final))
        except Exception:
            pass

    def _on_view_region_live(self):
        if self._sync_view_region:
            return
        self._emit_view_center(False)

    def _on_view_region_final(self):
        if self._sync_view_region:
            return
        self._emit_view_center(True)

    # ----------------------------
    # Drag de la línea verde (retune)
    # ----------------------------
    def _on_tune_line_live(self):
        if self._sync_tune_line:
            return
        try:
            mhz = self._clamp_mhz(float(self.tune_center.value()))
        except Exception:
            return
        # feedback inmediato
        self._tuned_hz = mhz * 1e6
        self._update_marker()
        self.sig_tune.emit(float(mhz), False)

    def _on_tune_line_final(self):
        if self._sync_tune_line:
            return
        try:
            mhz = self._clamp_mhz(float(self.tune_center.value()))
        except Exception:
            return
        self._tuned_hz = mhz * 1e6
        self._update_marker()
        self.sig_tune.emit(float(mhz), True)


    def _clamp_mhz(self, x_mhz: float) -> float:
        xmin = float(self._f_start_hz) / 1e6
        xmax = float(self._f_stop_hz) / 1e6
        if xmax < xmin:
            xmin, xmax = xmax, xmin
        return max(xmin, min(xmax, float(x_mhz)))

    def set_nav_window(self, x0_mhz: float, x1_mhz: float):
        x0 = float(x0_mhz)
        x1 = float(x1_mhz)
        if x1 < x0:
            x0, x1 = x1, x0
        x0 = self._clamp_mhz(x0)
        x1 = self._clamp_mhz(x1)
        if x1 <= x0:
            x1 = x0 + 0.00005

        self._sync_nav_region = True
        try:
            self.nav_region.setRegion((x0, x1))
        finally:
            self._sync_nav_region = False

    def _emit_nav_window(self, final: bool):
        try:
            x0, x1 = self.nav_region.getRegion()
            self.sig_view_window_changed.emit(float(x0), float(x1), bool(final))
        except Exception:
            pass

    def _on_nav_region_live(self):
        if self._sync_nav_region:
            return
        self._emit_nav_window(False)

    def _on_nav_region_final(self):
        if self._sync_nav_region:
            return
        self._emit_nav_window(True)

        # Si el selector llega a un tope y sueltas, pide recenter del span (para poder seguir avanzando)
        try:
            xmin = float(self._f_start_hz) / 1e6
            xmax = float(self._f_stop_hz) / 1e6
            if xmax < xmin:
                xmin, xmax = xmax, xmin

            x0, x1 = self.nav_region.getRegion()
            x0 = float(x0); x1 = float(x1)
            if x1 < x0:
                x0, x1 = x1, x0

            w = max(0.00005, x1 - x0)
            margin = max(w * 0.08, 0.00010)  # 8% del ancho o 100 Hz
            hit_left = (x0 - xmin) <= margin
            hit_right = (xmax - x1) <= margin

            if hit_left or hit_right:
                c = (x0 + x1) / 2.0
                self.sig_nav_edge_recenter.emit(float(c))
        except Exception:
            pass


    # ---- Drag de la línea verde (retune) ----
    def _on_tune_line_live(self):
        if self._sync_tune_line:
            return
        try:
            mhz = self._clamp_mhz(float(self.tune_center.value()))
        except Exception:
            return
        self._tuned_hz = mhz * 1e6
        self._update_marker()
        self.sig_tune.emit(float(mhz), False)

    def _on_tune_line_final(self):
        if self._sync_tune_line:
            return
        try:
            mhz = self._clamp_mhz(float(self.tune_center.value()))
        except Exception:
            return
        self._tuned_hz = mhz * 1e6
        self._update_marker()
        self.sig_tune.emit(float(mhz), True)


    def _on_view_wheel_tune(self, ev):
        """Intercepta la rueda para mover la frecuencia (sin zoom)."""
        try:
            dy = ev.angleDelta().y()
        except Exception:
            dy = 0

        if dy == 0:
            try:
                ev.ignore()
            except Exception:
                pass
            return

        steps = float(dy) / 120.0
        step_mhz = float(getattr(self, "_wheel_step_mhz", 0.0005))

        # frecuencia actual (tuned)
        try:
            cur_mhz = float(getattr(self, "_tuned_hz", 0.0)) / 1e6
        except Exception:
            cur_mhz = 0.0

        new_mhz = cur_mhz + steps * step_mhz

        # clamp al rango visible del waterfall
        try:
            xmin = float(self._f_start_hz) / 1e6
            xmax = float(self._f_stop_hz) / 1e6
            if xmax < xmin:
                xmin, xmax = xmax, xmin
            new_mhz = max(xmin, min(xmax, new_mhz))
        except Exception:
            pass

        try:
            self.sig_tune.emit(float(new_mhz), False)
        except Exception:
            pass

        try:
            ev.accept()
        except Exception:
            pass




