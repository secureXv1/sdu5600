# ui/main_window.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStatusBar, QLabel, QSplitter, QFrame, QApplication,
    QComboBox, QPushButton, QMessageBox,
    QDockWidget, QGroupBox, QFormLayout, QCheckBox,
    QDoubleSpinBox, QSpinBox, QListWidget, QListWidgetItem, QAbstractItemView

)
from PySide6 import QtCore
from PySide6.QtCore import Qt, QTimer

from PySide6.QtGui import QAction, QFont, QCursor, QLinearGradient, QColor, QBrush

import pyqtgraph as pg

import numpy as np

import threading
import re
import math
from PySide6.QtWidgets import QToolButton, QMenu, QSlider
from PySide6.QtCore import QEvent
from PySide6 import QtGui
from ui.band_bar import BandBar
from PySide6 import QtWidgets
from ui.waterfall_widget import WaterfallWidget




from core.radio_manager import RadioManager
from .radio_card import RadioCard
from drivers.hackrf_driver import HackRFDriver
from core.banks_store import BanksStore
from core.scanner_engine import ScannerEngine
from ui.banks_dialog import BanksDialog



class RibbonBar(QWidget):
    """Barra superior estilo SDR Console (simplificada)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)

        for title in ["Home", "View", "Receive", "Record", "Favourites", "Tools"]:
            lbl = QLabel(title)
            lbl.setStyleSheet(
                "font-weight:600; padding:4px 8px; "
                "border-radius:6px; background-color:rgba(148,163,184,0.15); "
                "color:#e5e7eb;"
            )
            layout.addWidget(lbl)

        layout.addStretch(1)
        self.setStyleSheet("""
            RibbonBar {
                background-color:#111827;
                border-bottom:1px solid #1f2937;
            }
        """)


class FrequencyNavigator(QWidget):
    sig_center_changed = QtCore.Signal(float)  # center_mhz

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_min = None
        self._full_max = None
        self._view_w = None
        self._block = False

        self.lbl = QLabel("Rango: --  |  Vista: --  |  Centro: --")
        self.lbl.setStyleSheet("color:#cbd5e1; font-size:11px;")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(25)
        self.slider.setMinimumHeight(18)
        self.slider.valueChanged.connect(self._on_change)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.lbl)
        lay.addWidget(self.slider)

    def set_limits(self, full_min_mhz: float, full_max_mhz: float):
        self._full_min = float(full_min_mhz)
        self._full_max = float(full_max_mhz)
        self._refresh_label()

    def set_view_width(self, view_width_mhz: float):
        self._view_w = max(0.000001, float(view_width_mhz))
        self._refresh_label()

    def set_center(self, center_mhz: float):
        if self._full_min is None or self._full_max is None:
            return
        span = max(0.000001, self._full_max - self._full_min)
        c = float(center_mhz)

        # clamp center dentro del rango
        c = max(self._full_min, min(self._full_max, c))

        # thumb representa el CENTRO (siempre “centrado” conceptualmente)
        t = (c - self._full_min) / span
        t = max(0.0, min(1.0, t))

        self._block = True
        self.slider.blockSignals(True)
        self.slider.setValue(int(t * 1000))
        self.slider.blockSignals(False)
        self._block = False

        self._refresh_label(center=c)

    def _refresh_label(self, center=None):
        if self._full_min is None or self._full_max is None or self._view_w is None:
            return
        span = self._full_max - self._full_min
        c = center
        if c is None:
            c = self._full_min + (self.slider.value() / 1000.0) * max(0.000001, span)

        v0 = c - self._view_w / 2.0
        v1 = c + self._view_w / 2.0

        # clamp vista dentro del rango total
        if v0 < self._full_min:
            v0 = self._full_min
            v1 = v0 + self._view_w
        if v1 > self._full_max:
            v1 = self._full_max
            v0 = v1 - self._view_w

        self.lbl.setText(
            f"Rango: {self._full_min:.6f} – {self._full_max:.6f} MHz   |   "
            f"Vista: {v0:.6f} – {v1:.6f} MHz   |   Centro: {((v0+v1)/2.0):.6f} MHz"
        )

    def _on_change(self, val: int):
        if self._block:
            return
        if self._full_min is None or self._full_max is None:
            return
        span = max(0.000001, self._full_max - self._full_min)
        t = float(val) / 1000.0
        c = self._full_min + t * span
        self._refresh_label(center=c)
        self.sig_center_changed.emit(float(c))

class SpectrumNavBar(pg.PlotWidget):
    sig_center_changed = QtCore.Signal(float)  # center_mhz

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.setFixedHeight(46)
        self.setBackground("#070b12")

        self._full_min = None
        self._full_max = None
        self._span = None
        self._view_w = None
        self._lock = False

        self.hideAxis("left")
        self.showAxis("bottom")
        self.showGrid(x=True, y=False, alpha=0.25)

        ax = self.getAxis("bottom")
        ax.setPen(pg.mkPen("#64748b", width=1))
        ax.setTextPen(pg.mkPen("#94a3b8"))
        try:
            ax.setStyle(tickFont=QtGui.QFont("Segoe UI", 8))
        except Exception:
            pass

        self.vb = self.getViewBox()
        self.vb.setMouseEnabled(x=False, y=False)
        self.vb.enableAutoRange(x=False, y=False)

        # baseline
        self._baseline = self.plot([], [])
        self._baseline.setPen(pg.mkPen("#111827", width=1.0))
        self.setYRange(-1, 1, padding=0.0)

        # viewport (visible range)
        self.view_region = pg.LinearRegionItem(
            values=[0.0, 0.0],
            movable=True,
            swapMode="push",
            brush=pg.mkBrush(34, 197, 94, 55),      # green-ish translucent
            pen=pg.mkPen(34, 197, 94, 210, width=1),
        )
        self.view_region.setZValue(10)
        self.addItem(self.view_region)

        # dotted edges
        self._l = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(34, 197, 94, 180, width=1, style=Qt.DotLine))
        self._r = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(34, 197, 94, 180, width=1, style=Qt.DotLine))
        self._l.setZValue(11)
        self._r.setZValue(11)
        self.addItem(self._l)
        self.addItem(self._r)

        self.view_region.sigRegionChanged.connect(self._on_region_live)
        self.view_region.sigRegionChangeFinished.connect(self._on_region_final)

    def set_limits(self, fmin_mhz: float, fmax_mhz: float):
        self._full_min = float(fmin_mhz)
        self._full_max = float(fmax_mhz)
        self._span = max(0.000001, self._full_max - self._full_min)

        self.vb.setLimits(
            xMin=self._full_min,
            xMax=self._full_max,
            minXRange=0.00005,
            maxXRange=self._span
        )
        self.setXRange(self._full_min, self._full_max, padding=0.0)

        try:
            xs = np.linspace(self._full_min, self._full_max, 64, dtype=float)
            ys = np.zeros_like(xs)
            self._baseline.setData(xs, ys)
        except Exception:
            pass

    def set_view_width(self, view_width_mhz: float):
        self._view_w = max(0.000001, float(view_width_mhz))

    def set_view_center(self, center_mhz: float):
        if self._full_min is None or self._full_max is None or self._view_w is None:
            return

        c = float(center_mhz)
        w = float(self._view_w)

        x0 = c - w / 2.0
        x1 = c + w / 2.0

        # clamp inside full
        if x0 < self._full_min:
            x0 = self._full_min
            x1 = x0 + w
        if x1 > self._full_max:
            x1 = self._full_max
            x0 = x1 - w

        self._lock = True
        try:
            self.view_region.blockSignals(True)
            self.view_region.setRegion((x0, x1))
            self.view_region.blockSignals(False)
            self._l.setPos(x0)
            self._r.setPos(x1)
        finally:
            self._lock = False

    def _enforce_fixed_width(self):
        if self._full_min is None or self._full_max is None or self._view_w is None:
            return None

        x0, x1 = self.view_region.getRegion()
        x0 = float(x0); x1 = float(x1)
        if x1 < x0:
            x0, x1 = x1, x0

        w = float(self._view_w)
        c = (x0 + x1) / 2.0

        nx0 = c - w / 2.0
        nx1 = c + w / 2.0

        # clamp inside full
        if nx0 < self._full_min:
            nx0 = self._full_min
            nx1 = nx0 + w
        if nx1 > self._full_max:
            nx1 = self._full_max
            nx0 = nx1 - w

        self._lock = True
        try:
            self.view_region.blockSignals(True)
            self.view_region.setRegion((nx0, nx1))
            self.view_region.blockSignals(False)
        finally:
            self._lock = False

        self._l.setPos(nx0)
        self._r.setPos(nx1)
        return (nx0 + nx1) / 2.0

    def _on_region_live(self):
        if self._lock:
            return
        c = self._enforce_fixed_width()
        if c is None:
            return
        self.sig_center_changed.emit(float(c))

    def _on_region_final(self):
        if self._lock:
            return
        c = self._enforce_fixed_width()
        if c is None:
            return
        self.sig_center_changed.emit(float(c))







class SpectrumWidget(QWidget):
    """
    Spectrum estilo SDR Control V3:
    - Trazo blanco + relleno azul
    - Promedio suave + Peak Hold (identificar señales fácil)
    - Eje dBm a izquierda y derecha
    - Grid horizontal marcado
    - S-Meter overlay (top-left)
    - Barra mini-map inferior (tu navbar) se mantiene
    """
    sig_tune = QtCore.Signal(float, bool)  # mhz, final

    MODE_BW_HZ = {
        "NFM": 12_500,
        "FMN": 12_500,
        "AM": 10_000,
        "USB": 2_700,
        "LSB": 2_700,
        "FM": 200_000,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # ======================
        # Plot principal
        # ======================
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#05070b")
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.getViewBox().enableAutoRange(x=False, y=False)

        # Grid estilo SDR (mucho horizontal, poco vertical)
        self.plot.showGrid(x=True, y=True, alpha=0.10)

        # Ejes
        axL = self.plot.getAxis("left")
        axB = self.plot.getAxis("bottom")
        axR = self.plot.getAxis("right")

        self.plot.showAxis("right")

        for ax in (axL, axR, axB):
            ax.setPen(pg.mkPen("#6b7280", width=1))
            ax.setTextPen(pg.mkPen("#cbd5e1"))

        # Etiquetas (como screenshot: dBm en vertical)
        axL.setLabel("dBm")
        axR.setLabel("dBm")

        # Ticks dB (cada 5 dB, -40 arriba a -130 abajo)
        self._ymin = -130.0
        self._ymax = -40.0
        self.plot.setYRange(self._ymin, self._ymax, padding=0.0)

        def _db_ticks():
            vals = list(range(-130, -39, 5))
            return [(v, f"{v} dBm") for v in vals]

        axL.setTicks([_db_ticks()])
        axR.setTicks([_db_ticks()])

        # Bottom: MHz (limpio)
        axB.setLabel("")  # SDR Control casi no pone label grande
        try:
            axB.setStyle(tickFont=QtGui.QFont("Segoe UI", 8))
            axL.setStyle(tickFont=QtGui.QFont("Segoe UI", 8))
            axR.setStyle(tickFont=QtGui.QFont("Segoe UI", 8))
        except Exception:
            pass

        self.vb = self.plot.getViewBox()
        self.vb.setLimits(minXRange=0.00005)

        # ======================
        # Curvas: AVG + PEAK
        # ======================
        self.avg_curve = self.plot.plot([], [])
        self.avg_curve.setPen(pg.mkPen("#e5e7eb", width=1.2))

        # relleno azul (como SDR Control)
        self.avg_curve.setFillLevel(self._ymin)
        grad = QLinearGradient(0, self._ymin, 0, self._ymax)
        grad.setColorAt(0.0, QColor(12, 25, 60, 210))
        grad.setColorAt(1.0, QColor(65, 105, 180, 80))
        self.avg_curve.setBrush(QBrush(grad))

        self.peak_curve = self.plot.plot([], [])
        self.peak_curve.setPen(pg.mkPen(QColor(210, 210, 210, 170), width=1.0))

        # ======================
        # Marcador verde (3 líneas)
        # ======================
        self._tuned_mhz = None
        self._mode = "NFM"
        self._sync_lock = False

        self.chan_region = pg.LinearRegionItem(
            values=[0.0, 0.0],
            movable=True,
            swapMode="push",
            brush=pg.mkBrush(34, 197, 94, 30),
            pen=pg.mkPen(34, 197, 94, 120, width=1),
        )
        self.chan_region.setZValue(8)
        self.plot.addItem(self.chan_region)

        self.tune_left = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.4))
        self.tune_center = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen("#22c55e", width=2.6),
            hoverPen=pg.mkPen("#86efac", width=3.4),
        )
        self.tune_center.setZValue(10)
        self.tune_right = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#22c55e", width=1.4))
        self.plot.addItem(self.tune_left)
        self.plot.addItem(self.tune_center)
        self.plot.addItem(self.tune_right)

        self.chan_region.sigRegionChanged.connect(self._on_region_changed_live)
        self.chan_region.sigRegionChangeFinished.connect(self._on_region_changed_final)
        self.tune_center.sigPositionChanged.connect(self._on_center_changed_live)
        self.tune_center.sigPositionChangeFinished.connect(self._on_center_changed_final)

        # ======================
        # Texto info discreto (opcional)
        # ======================
        self._info = pg.TextItem(color="#cbd5e1", anchor=(0, 0))
        self._info.setZValue(20)
        self.plot.addItem(self._info)

        # ======================
        # Suavizado + Peak Hold
        # ======================
        self._avg = None
        self._peak = None
        self._avg_alpha = 0.22          # “natural”
        self._peak_decay_db = 0.35      # decaimiento por frame (dB)

        # X range tracking
        self._full_xmin = None
        self._full_xmax = None
        self._span = None
        self._x_inited = False

        # Mouse tune
        self._drag_tuning = False
        sc = self.plot.scene()
        sc.sigMouseMoved.connect(self._on_scene_mouse_moved)
        sc.sigMouseClicked.connect(self._on_scene_mouse_clicked)


        v.addWidget(self.plot, 1)

        # ======================
        # Navbar (tu mini-map)
        # ======================
        self.navbar = SpectrumNavBar()
        self.navbar.sig_center_changed.connect(self._on_nav_center)
        v.addWidget(self.navbar, 0)

        self._syncing_navbar = False
        self.vb.sigRangeChanged.connect(self._on_view_range_changed)

        self.plot.setMinimumHeight(180)

    # ----------------------------
    # Helpers / Layout overlay
        # ----------------------------


    # navbar -> centra la vista
    def _on_nav_center(self, center_mhz: float):
        if self._full_xmin is None or self._full_xmax is None:
            return
        try:
            (xr, _yr) = self.plot.viewRange()
            w = max(0.000001, float(xr[1]) - float(xr[0]))
        except Exception:
            w = max(0.000001, float(self._full_xmax) - float(self._full_xmin))

        c = float(center_mhz)
        x0 = c - w / 2.0
        x1 = c + w / 2.0

        if x0 < self._full_xmin:
            x0 = self._full_xmin
            x1 = x0 + w
        if x1 > self._full_xmax:
            x1 = self._full_xmax
            x0 = x1 - w

        self._syncing_navbar = True
        try:
            self.plot.setXRange(x0, x1, padding=0.0)
        finally:
            self._syncing_navbar = False

    def _on_view_range_changed(self, *_):
        
        if getattr(self, "_syncing_navbar", False):
            return
        if self._full_xmin is None or self._full_xmax is None:
            return
        try:
            (xr, _yr) = self.plot.viewRange()
            x0 = float(xr[0])
            x1 = float(xr[1])
        except Exception:
            return

        c = (x0 + x1) / 2.0
        w = max(0.000001, x1 - x0)

        self.navbar.set_limits(self._full_xmin, self._full_xmax)
        self.navbar.set_view_width(w)
        self.navbar.set_view_center(c)

    # mouse tune
    def _mhz_from_scene_pos(self, scene_pos) -> float | None:
        try:
            p = self.vb.mapSceneToView(scene_pos)
            x_mhz = float(p.x())
        except Exception:
            return None
        try:
            xr = self.plot.viewRange()[0]
            xmin = float(xr[0]); xmax = float(xr[1])
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
            mhz = self._mhz_from_scene_pos(ev.scenePos())
            if mhz is None:
                return
        except Exception:
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

    # mode / marker
    def set_mode(self, mode: str):
        self._mode = (mode or "").upper().strip() or "NFM"
        self._refresh_channel_marker()

    def _bw_mhz(self) -> float:
        bw_hz = self.MODE_BW_HZ.get(self._mode, 12_500)
        return float(bw_hz) / 1e6

    def _refresh_channel_marker(self):
        if self._tuned_mhz is None:
            return
        bw = self._bw_mhz()
        c = float(self._tuned_mhz)
        left = c - (bw / 2.0)
        right = c + (bw / 2.0)
        try:
            self.chan_region.setRegion((left, right))
            self.tune_left.setPos(left)
            self.tune_center.setPos(c)
            self.tune_right.setPos(right)
        except Exception:
            pass

    def set_tuned_freq_mhz(self, mhz: float):
        try:
            self._tuned_mhz = float(mhz)
            self._refresh_channel_marker()
        except Exception:
            pass

    # API: si tu scanner lo llama
    def center_on_mhz(self, mhz: float):
        try:
            (xr, _yr) = self.plot.viewRange()
            w = float(xr[1]) - float(xr[0])
            c = float(mhz)
            self.plot.setXRange(c - w / 2.0, c + w / 2.0, padding=0.0)
        except Exception:
            pass

    # ----------------------------
    # Update spectrum (AVG + PEAK)
    # ----------------------------
    def update_spectrum(self, freqs_hz, levels_db, tuned_mhz: float | None = None):
        if freqs_hz is None or len(freqs_hz) == 0:
            return

        freqs_mhz = (freqs_hz / 1e6) if hasattr(freqs_hz, "__array__") else [f / 1e6 for f in freqs_hz]
        y = np.asarray(levels_db, dtype=np.float32)

        # init
        if self._avg is None or self._avg.shape != y.shape:
            self._avg = y.copy()
            self._peak = y.copy()
        else:
            a = float(self._avg_alpha)
            self._avg = a * y + (1.0 - a) * self._avg

            # peak-hold con decaimiento (dB)
            decay = float(self._peak_decay_db)
            self._peak = np.maximum(self._peak - decay, y)

            x = np.asarray(freqs_mhz, dtype=np.float32)
            y_avg = np.asarray(self._avg, dtype=np.float32)
            y_pk  = np.asarray(self._peak, dtype=np.float32)

            m = np.isfinite(x) & np.isfinite(y_avg) & np.isfinite(y_pk)
            x = x[m]; y_avg = y_avg[m]; y_pk = y_pk[m]

            self.avg_curve.setData(x, y_avg)
            self.peak_curve.setData(x, y_pk)

        # rango X completo
        try:
            xmin = float(freqs_mhz[0])
            xmax = float(freqs_mhz[-1])
            if xmax < xmin:
                xmin, xmax = xmax, xmin
        except Exception:
            return

        self._full_xmin = xmin
        self._full_xmax = xmax
        self._span = max(0.000001, self._full_xmax - self._full_xmin)

        self.vb.setLimits(
            xMin=self._full_xmin,
            xMax=self._full_xmax,
            minXRange=0.00005,
            maxXRange=self._span
        )

        if not self._x_inited:
            self.plot.setXRange(self._full_xmin, self._full_xmax, padding=0.0)
            self._x_inited = True

        # info discreto (como el badge “Freq/Span” del screenshot)
        try:
            start = float(freqs_mhz[0])
            end = float(freqs_mhz[-1])
            center = (start + end) / 2.0
            span = end - start
            tuned_txt = f" | Tuned: {tuned_mhz:.6f} MHz" if tuned_mhz is not None else ""
            self._info.setText(f"Freq: {center:.6f} MHz | Span: ±{(span/2.0)*1000.0:.0f} kHz{tuned_txt}")
            (xr, yr) = self.plot.viewRange()
            self._info.setPos(xr[0] + 0.01 * (xr[1] - xr[0]), yr[1] - 2)
        except Exception:
            pass

       

        if tuned_mhz is not None:
            self.set_tuned_freq_mhz(tuned_mhz)

        # sync navbar
        self._on_view_range_changed()

    # ----------------------------
    # verde interactivo
    # ----------------------------
    def _emit_tune(self, mhz: float, final: bool):
        try:
            self.sig_tune.emit(float(mhz), bool(final))
        except Exception:
            pass

    def _on_center_changed_live(self):
        if self._sync_lock:
            return
        self._sync_lock = True
        try:
            c = float(self.tune_center.value())
            left, right = self.chan_region.getRegion()
            bw = max(0.000001, float(right) - float(left))
            left = c - bw / 2.0
            right = c + bw / 2.0
            self.chan_region.setRegion((left, right))
            self.tune_left.setPos(left)
            self.tune_right.setPos(right)
            self._tuned_mhz = c
            self._emit_tune(c, False)
        except Exception:
            pass
        finally:
            self._sync_lock = False

    def _on_center_changed_final(self):
        try:
            c = float(self.tune_center.value())
            self._tuned_mhz = c
            self._emit_tune(c, True)
        except Exception:
            pass

    def _on_region_changed_live(self):
        if self._sync_lock:
            return
        self._sync_lock = True
        try:
            left, right = self.chan_region.getRegion()
            left = float(left); right = float(right)
            c = (left + right) / 2.0
            self.tune_center.setValue(c)
            self.tune_left.setPos(left)
            self.tune_right.setPos(right)
            self._tuned_mhz = c
            self._emit_tune(c, False)
        except Exception:
            pass
        finally:
            self._sync_lock = False

    def _on_region_changed_final(self):
        try:
            left, right = self.chan_region.getRegion()
            c = (float(left) + float(right)) / 2.0
            self._tuned_mhz = c
            self._emit_tune(c, True)
        except Exception:
            pass

    def set_fixed_dbm_range(self, ymin: float, ymax: float):
        self._ymin = float(ymin)
        self._ymax = float(ymax)
        try:
            self.plot.setYRange(self._ymin, self._ymax, padding=0.0)
        except Exception:
            pass
        try:
            axL = self.plot.getAxis("left")
            axR = self.plot.getAxis("right")

            def _db_ticks():
                vals = list(range(int(self._ymin), int(self._ymax) + 1, 5))
                return [(v, f"{v} dBm") for v in vals]

            axL.setTicks([_db_ticks()])
            axR.setTicks([_db_ticks()])
        except Exception:
            pass        




class LeftPanel(QWidget):
    """Panel con tarjetas de radio (RadioCard)."""
    def __init__(self, radio_cards, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        title = QLabel("Radios / Control")
        title.setStyleSheet("font-weight:700; color:#e5e7eb;")
        v.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#1f2937;")
        v.addWidget(sep)

        for card in radio_cards:
            card.setStyleSheet("""
                QWidget {
                    background-color:#020617;
                    border:1px solid #1f2937;
                    border-radius:10px;
                    color:#e5e7eb;
                }
                QPushButton {
                    background-color:#111827;
                    border:1px solid #1f2937;
                    border-radius:6px;
                    padding:4px 8px;
                    color:#e5e7eb;
                }
                QPushButton:hover { background-color:#1f2937; }
                QLabel { color:#e5e7eb; }
            """)
            v.addWidget(card)

        v.addStretch(1)

        self.setStyleSheet("""
            LeftPanel {
                background-color:#020617;
                border-right:1px solid #1f2937;
            }
        """)


class FrequencySpinBox(QDoubleSpinBox):
    """
    SpinBox estilo SDR:
    - Al pasar el mouse sobre un dígito, lo resalta (selección)
    - Wheel sin necesidad de “focus” (si estás encima)
    - El paso depende del dígito bajo el cursor (1, 0.1, 0.01, 0.001… MHz)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.lineEdit().setMouseTracking(True)
        self.lineEdit().installEventFilter(self)

    def _step_from_cursor(self) -> float:
        txt = self.lineEdit().text()
        # quitar sufijo si existe
        txt = txt.replace("MHz", "").strip()
        # cursor sobre el texto actual del lineEdit
        cur = self.lineEdit().cursorPosition()
        if cur < 0:
            return float(self.singleStep())

        dot = txt.find(".")
        if dot == -1:
            # sin decimales -> 1 MHz
            return 1.0

        # si cursor está a la derecha del punto: decimales
        if cur > dot:
            decimals_pos = cur - dot  # 1 => décimas, 2 => centésimas...
            step = 10 ** (-decimals_pos)
            return float(step)

        # si cursor está a la izquierda del punto: enteros
        # distancia al punto define 1,10,100...
        dist = dot - cur
        step = 10 ** (dist - 1) if dist >= 1 else 1.0
        return float(step)

    def eventFilter(self, obj, ev):
        if obj is self.lineEdit():
            if ev.type() == QEvent.MouseMove:
                # seleccionar “token” numérico bajo el cursor
                txt = self.lineEdit().text()
                pos = self.lineEdit().cursorPositionAt(ev.position().toPoint())
                self.lineEdit().setCursorPosition(pos)

                # intenta seleccionar alrededor del cursor (números y punto)
                # encuentra el rango continuo de [0-9.]
                start = pos
                end = pos
                while start > 0 and (txt[start-1].isdigit() or txt[start-1] == "."):
                    start -= 1
                while end < len(txt) and (txt[end].isdigit() or txt[end] == "."):
                    end += 1
                if end > start:
                    self.lineEdit().setSelection(start, end - start)
                return False
        return super().eventFilter(obj, ev)

    def wheelEvent(self, event):
        # permitir wheel si el mouse está encima, incluso sin focus
        if not self.underMouse():
            return super().wheelEvent(event)

        step = self._step_from_cursor()
        delta = event.angleDelta().y()
        if delta == 0:
            return

        v = float(self.value())
        if delta > 0:
            v += step
        else:
            v -= step

        # clamp
        v = max(self.minimum(), min(self.maximum(), v))
        self.setValue(v)
        event.accept()




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        
                # ✅ Definir estado ANTES de conectar señales/armar UI
        self.is_monitoring = False

        self.setWindowTitle("Remote Radios Station – SDR Style")
        self.resize(1500, 850)


        # =========================
        #  Manager y tarjetas
        # =========================
        self.manager = RadioManager()
        self.cards: list[RadioCard] = []
        self.device_map = {}  # name -> driver
        self.banks_store = BanksStore("config/banks.json")
        self.scanner = ScannerEngine(self.manager, self.banks_store, recordings_root="recordings")

        for radio_cfg in self.manager.radios:
            card = RadioCard(radio_cfg["name"], radio_cfg["driver"])
            self.cards.append(card)
            self.device_map[radio_cfg["name"]] = card.driver

        self.active_driver = None
        self.hackrf_driver: HackRFDriver | None = None

        for name, drv in self.device_map.items():
            if isinstance(drv, HackRFDriver):
                self.hackrf_driver = drv
                break

        if self.hackrf_driver is not None:
            self.active_driver = self.hackrf_driver
            self.active_device_name = self._find_device_name(self.active_driver)
        else:
            self.active_device_name = next(iter(self.device_map.keys()), "N/A")
            self.active_driver = self.device_map.get(self.active_device_name, None)

        # =========================
        #  Estado modo (FUENTE ÚNICA)
        # =========================
        self.current_mode = "NFM"

        # =========================
        #  Central layout
        # =========================
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self.ribbon = RibbonBar()
        central_layout.addWidget(self.ribbon)

       

        self.spectrum = SpectrumWidget()

                # =========================
        # Dial manual desde espectro
        # =========================
        self._spec_tune_timer = QTimer(self)
        self._spec_tune_timer.setSingleShot(True)
        self._spec_tune_timer.setInterval(60)  # throttle
        self._spec_tune_timer.timeout.connect(self._apply_tune)

        try:
            self.spectrum.sig_tune.connect(self._on_spectrum_tune)
        except Exception:
            pass

        self.spectrum.set_mode(self.current_mode)  # ✅ marcador 3 líneas inicial



                # =========================
        # Dial manual en waterfall (mouse -> tune)
        # =========================
        self._wf_tune_timer = QTimer(self)
        self._wf_tune_timer.setSingleShot(True)
        self._wf_tune_timer.setInterval(60)  # throttle (ms) para que no “ahogue” el driver
        self._wf_tune_timer.timeout.connect(self._apply_tune)

        try:
            self.waterfall.sig_tune.connect(self._on_waterfall_tune)
        except Exception:
            pass


        # =========================
        # Spectrum + BandBar + Waterfall (tipo SDR Control)
        # =========================
        self.bandbar = BandBar("MARINE VHF")  # puedes cambiar dinámicamente luego
        
        self.waterfall = WaterfallWidget()
        self.bandbar = BandBar("—")

        bottom_split = QSplitter(Qt.Vertical)
        bottom_split.addWidget(self.spectrum)
        bottom_split.addWidget(self.bandbar)
        bottom_split.addWidget(self.waterfall)

        bottom_split.setStretchFactor(0, 2)
        bottom_split.setStretchFactor(1, 0)
        bottom_split.setStretchFactor(2, 3)

        # fija la barra verde para que no se “estire”
        self.bandbar.setMinimumHeight(22)
        self.bandbar.setMaximumHeight(22)


        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(bottom_split)
        main_split.setStretchFactor(0, 1)
        central_layout.addWidget(main_split)
        self.setCentralWidget(central)

        # =========================
        #  Docks (Control a la IZQUIERDA / Radios oculto)
        # =========================
        self._build_controls_dock_left()

        # ✅ IMPORTANTE: al crear el dock se crean mode_btn y botones.
        # Ahora sincronizamos TODO (texto, checks, preset DSP y espectro).
        if hasattr(self, "_set_mode_from_button"):
            self._set_mode_from_button(self.current_mode)

        self._build_radios_dock_hidden()
        self._build_audio_dsp_dock()
        self._build_scanner_dock()

        # =========================
        #  Barra de estado
        # =========================
        status = QStatusBar()
        self.setStatusBar(status)
        self.lbl_status = QLabel("Listo · SDR Style")
        status.addPermanentWidget(self.lbl_status)

        # =========================
        #  Estado monitor/audio
        # =========================
        self.is_monitoring = False

        # =========================
        #  Menús (Devices + View con toggle docks)
        # =========================
        self._build_menus()

        # =========================
        #  Timer para actualizar espectro
        # =========================
        self.spec_timer = QTimer(self)
        self.spec_timer.setInterval(200)
        self.spec_timer.timeout.connect(self._update_from_active_device)
        self.spec_timer.start()

        self._apply_theme()

    # ---------- Docks ----------
    def _build_controls_dock_left(self):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)

        # ===== Device =====
        gb_dev = QGroupBox("Device")
        f_dev = QFormLayout(gb_dev)

        self.device_combo = QComboBox()
        self.device_combo.addItems(list(self.device_map.keys()))
        self.device_combo.setCurrentText(self.active_device_name)
        self.device_combo.currentTextChanged.connect(self.set_active_device)
        f_dev.addRow("Activo", self.device_combo)

        # ===== Receiver / Monitor =====
        gb_rx = QGroupBox("Receiver / Monitor")
        f_rx = QFormLayout(gb_rx)

        self.freq_spin = FrequencySpinBox()
        self.freq_spin.setDecimals(6)
        self.freq_spin.setRange(0.0, 6000.0)
        self.freq_spin.setSingleStep(0.0125)
        self.freq_spin.setValue(91.400000)
        self.freq_spin.setSuffix("  MHz")

        font_big = QFont("Segoe UI", 16)
        font_big.setBold(True)
        self.freq_spin.setFont(font_big)
        self.freq_spin.setMinimumHeight(46)

        # --- Auto-TUNE (debounce) ---
        self._auto_tune_timer = QTimer(self)
        self._auto_tune_timer.setSingleShot(True)
        self._auto_tune_timer.setInterval(250)
        self._auto_tune_timer.timeout.connect(self._apply_tune)

        # Con flechas/ruedita sí dispara valueChanged
        self.freq_spin.valueChanged.connect(lambda _v: self._auto_tune_timer.start())

        # Mientras escribes, valueChanged a veces NO dispara => usa textEdited
        le = self.freq_spin.lineEdit()
        le.textEdited.connect(lambda _t: self._auto_tune_timer.start())

        # Enter o perder foco => aplica inmediato
        le.editingFinished.connect(self._apply_tune)

        # =========================
        # MODO: botones + desplegable
        # =========================
        mode_row = QWidget()
        mode_lay = QHBoxLayout(mode_row)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.setSpacing(6)

        self.mode_btn = QToolButton()
        self.mode_btn.setText("NFM")
        self.mode_btn.setMinimumHeight(38)
        self.mode_btn.setPopupMode(QToolButton.MenuButtonPopup)

        menu = QMenu(self.mode_btn)
        for m in ["NFM", "AM", "USB", "LSB", "FM"]:
            act = menu.addAction(m)
            act.triggered.connect(lambda _=False, mm=m: self._set_mode_from_button(mm))
        self.mode_btn.setMenu(menu)

        # Botones rápidos (tipo SDR)
        self.btn_mode_nfm = QPushButton("NFM")
        self.btn_mode_am  = QPushButton("AM")
        self.btn_mode_usb = QPushButton("USB")
        self.btn_mode_lsb = QPushButton("LSB")
        self.btn_mode_fm  = QPushButton("FM")

        for b, m in [
            (self.btn_mode_nfm, "NFM"),
            (self.btn_mode_am,  "AM"),
            (self.btn_mode_usb, "USB"),
            (self.btn_mode_lsb, "LSB"),
            (self.btn_mode_fm,  "FM"),
        ]:
            b.setCheckable(True)
            b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, mm=m: self._set_mode_from_button(mm))

        # (Opcional) que se vean “tipo botón SDR”
        # Si ya tienes stylesheet global, puedes borrar esto.
        for b in [self.btn_mode_nfm, self.btn_mode_am, self.btn_mode_usb, self.btn_mode_lsb, self.btn_mode_fm]:
            b.setStyleSheet("""
                QPushButton {
                    padding: 6px 10px;
                    border-radius: 8px;
                    border: 1px solid #334155;
                    background: #0b1220;
                    color: #e5e7eb;
                    font-weight: 700;
                }
                QPushButton:checked {
                    border: 1px solid rgba(34,197,94,0.9);
                    background: rgba(34,197,94,0.12);
                }
                QPushButton:hover {
                    background: rgba(148,163,184,0.08);
                }
            """)

        self.mode_btn.setStyleSheet("""
            QToolButton {
                padding: 6px 10px;
                border-radius: 8px;
                border: 1px solid #334155;
                background: #0b1220;
                color: #e5e7eb;
                font-weight: 800;
            }
            QToolButton:hover {
                background: rgba(148,163,184,0.08);
            }
        """)

        mode_lay.addWidget(self.btn_mode_nfm)
        mode_lay.addWidget(self.btn_mode_am)
        mode_lay.addWidget(self.btn_mode_usb)
        mode_lay.addWidget(self.btn_mode_lsb)
        mode_lay.addWidget(self.btn_mode_fm)
        mode_lay.addWidget(self.mode_btn)

        # Botón Monitor
        self.btn_monitor = QPushButton("▶ MONITOR")
        self.btn_monitor.setMinimumHeight(44)
        self.btn_monitor.clicked.connect(self._toggle_monitor)

        f_rx.addRow("Frecuencia", self.freq_spin)
        f_rx.addRow("Modo", mode_row)     # <-- antes era self.mode_combo
        f_rx.addRow("", self.btn_monitor)

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setMinimumHeight(26)

        self.lbl_vol = QLabel("Vol: 70%")
        self.lbl_vol.setStyleSheet("color:#9ca3af; font-weight:700;")

        self.vol_slider.valueChanged.connect(self._on_volume_changed)

        f_rx.addRow("Volumen", self.vol_slider)
        f_rx.addRow("", self.lbl_vol)



        
        # ===== Waterfall =====
        gb_wf = QGroupBox("Waterfall")
        f_wf = QFormLayout(gb_wf)

        self.chk_reverse_wf = QCheckBox("Invertir dirección (arriba↔abajo)")
        self.chk_reverse_wf.setChecked(False)
        self.chk_reverse_wf.toggled.connect(self._set_waterfall_reverse)
        f_wf.addRow(self.chk_reverse_wf)

        v.addWidget(gb_dev)
        v.addWidget(gb_rx)
        v.addWidget(gb_wf)
        v.addStretch(1)

        self.controls_dock = QDockWidget("Controls", self)
        self.controls_dock.setWidget(panel)
        self.controls_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.controls_dock)
        self.controls_dock.show()


    def _build_scanner_dock(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # --- Barra superior: filtro + botones ---
        top = QHBoxLayout()

        self.scan_filter = QComboBox()
        self.scan_filter.addItems(["Todos", "Frecuencias", "Rangos"])
        self.scan_filter.currentIndexChanged.connect(self._refresh_scanner_panel)

        self.btn_scan = QPushButton("Iniciar escáner")
        self.btn_scan.clicked.connect(self._toggle_scan)

        self.btn_scan_edit = QPushButton("Editar")
        self.btn_scan_edit.clicked.connect(self._edit_selected_bank)

        self.btn_scan_del = QPushButton("Eliminar")
        self.btn_scan_del.clicked.connect(self._delete_selected_bank)

        top.addWidget(QLabel("Mostrar:"))
        top.addWidget(self.scan_filter, 1)
        top.addWidget(self.btn_scan)
        top.addWidget(self.btn_scan_edit)
        top.addWidget(self.btn_scan_del)

        lay.addLayout(top)

        # --- Lista de bancos (con checkbox Activo) ---
        self.banks_list = QListWidget()
        self.banks_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.banks_list.itemChanged.connect(self._on_bank_item_changed)
        self.banks_list.itemDoubleClicked.connect(lambda _it: self._edit_selected_bank())
        lay.addWidget(self.banks_list, 1)

        # --- Estado / ayuda ---
        self.lbl_scan_status = QLabel("Selecciona bancos activos y pulsa Iniciar.")
        self.lbl_scan_status.setWordWrap(True)
        lay.addWidget(self.lbl_scan_status)

        self.scanner_dock = QDockWidget("Escáner", self)
        self.scanner_dock.setWidget(w)
        self.scanner_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.scanner_dock)
        self.scanner_dock.hide()

        # Cargar lista inicial
        self._refresh_scanner_panel()

    def _apply_mode_preset(self, mode: str):
        mode = (mode or "").upper().strip()

        # Solo si existe el dock
        if not hasattr(self, "dsp_tau"):
            return

        # Defaults “voz limpia”
        if mode in ("NFM", "FMN"):
            # NFM: IF 16k, AF 3k, HPF 300, tau 530
            self.dsp_chan_cut.setValue(16_000.0)
            self.dsp_aud_cut.setValue(3_000.0)
            self.dsp_tau.setValue(530.0)     # us
            self.dsp_drive.setValue(1.0)
            if hasattr(self, "dsp_hpf"):
                self.dsp_hpf.setValue(300.0)

        elif mode == "AM":
            # AM: (no tau) AF 3k, HPF 150
            # OJO: tu dock etiqueta “FM Canal cutoff”, pero lo usamos como “canal/IF” genérico
            self.dsp_chan_cut.setValue(10_000.0)   # AM voz típica 6–10k
            self.dsp_aud_cut.setValue(3_000.0)
            self.dsp_tau.setValue(1.0)             # “sin tau” (mínimo) para no afectar
            self.dsp_drive.setValue(1.2)
            if hasattr(self, "dsp_hpf"):
                self.dsp_hpf.setValue(150.0)

        elif mode in ("USB", "LSB"):
            # SSB: (no tau) AF 3k, HPF 150
            self.dsp_chan_cut.setValue(3_000.0)    # ancho SSB típico 2.4–3k
            self.dsp_aud_cut.setValue(3_000.0)
            self.dsp_tau.setValue(1.0)             # “sin tau”
            self.dsp_drive.setValue(1.2)
            if hasattr(self, "dsp_hpf"):
                self.dsp_hpf.setValue(150.0)

        else:  # FM (broadcast)
            self.dsp_chan_cut.setValue(110_000.0)
            self.dsp_aud_cut.setValue(15_000.0)
            self.dsp_tau.setValue(50.0)            # Colombia: 50us
            self.dsp_drive.setValue(1.2)
            if hasattr(self, "dsp_hpf"):
                self.dsp_hpf.setValue(0.0)

        # Auto-aplicar al motor (igual que SDR Console)
        self._apply_audio_dsp_params()


    def _set_mode_from_button(self, mode: str):
        mode = (mode or "").upper().strip() or "NFM"

        # =========================
        # UI: marcar botones
        # =========================
        for b in [
            self.btn_mode_nfm,
            self.btn_mode_am,
            self.btn_mode_usb,
            self.btn_mode_lsb,
            self.btn_mode_fm,
        ]:
            b.blockSignals(True)
            b.setChecked(b.text().upper() == mode)
            b.blockSignals(False)

        self.mode_btn.setText(mode)

        # =========================
        # Preset DSP (IF / AF / etc.)
        # =========================
        self._apply_mode_preset(mode)

        # =========================
        # 🔥 CAMBIO DE MODO EN VIVO (AUDIO)
        # =========================
        try:
            if self.is_monitoring and hasattr(self.manager, "audio"):
                self.manager.audio.set_mode(mode)
        except Exception as e:
            print("Error cambiando modo de audio:", e)

        # =========================
        # Espectro: ancho de banda / marcador
        # =========================
        try:
            self.spectrum.set_mode(mode)
        except Exception:
            pass

        # =========================
        # Waterfall: ancho de banda
        # =========================
        try:
            self.waterfall.set_mode(mode)
        except Exception:
            pass



    def _on_spectrum_tune(self, mhz: float, final: bool):
        # si escáner corre, no pelear
        if getattr(getattr(self, "scanner", None), "is_running", False):
            return

        mhz = float(mhz)

        # solo actualiza UI
        try:
            self.freq_spin.blockSignals(True)
            self.freq_spin.setValue(mhz)
            self.freq_spin.blockSignals(False)
        except Exception:
            pass

        # SOLO al soltar aplica (evita que el espectro se mueva mientras arrastras)
        if final:
            self._apply_tune()





    def _build_radios_dock_hidden(self):
        self.left_panel = LeftPanel(self.cards)
        self.radios_dock = QDockWidget("Radios", self)
        self.radios_dock.setWidget(self.left_panel)
        self.radios_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.radios_dock)
        self.radios_dock.hide()  # oculto por defecto


    def _build_audio_dsp_dock(self):
        panel = QWidget()
        f = QFormLayout(panel)
        f.setContentsMargins(10, 10, 10, 10)

        self.dsp_chan_cut = QDoubleSpinBox()
        self.dsp_chan_cut.setRange(1_000.0, 500_000.0)
        self.dsp_chan_cut.setSingleStep(5_000.0)
        self.dsp_chan_cut.setValue(110_000.0)

        self.dsp_aud_cut = QDoubleSpinBox()
        self.dsp_aud_cut.setRange(1_000.0, 30_000.0)
        self.dsp_aud_cut.setSingleStep(500.0)
        self.dsp_aud_cut.setValue(14_000.0)

        self.dsp_tau = QDoubleSpinBox()
        self.dsp_tau.setRange(1.0, 2_000.0)
        self.dsp_tau.setSingleStep(5.0)
        self.dsp_tau.setValue(90.0)

        self.dsp_drive = QDoubleSpinBox()
        self.dsp_drive.setRange(0.1, 5.0)
        self.dsp_drive.setSingleStep(0.1)
        self.dsp_drive.setValue(1.2)

        self.dsp_chan_taps = QSpinBox()
        self.dsp_chan_taps.setRange(31, 401)
        self.dsp_chan_taps.setSingleStep(2)
        self.dsp_chan_taps.setValue(161)

        self.dsp_aud_taps = QSpinBox()
        self.dsp_aud_taps.setRange(31, 401)
        self.dsp_aud_taps.setSingleStep(2)
        self.dsp_aud_taps.setValue(161)

        self.btn_apply_dsp = QPushButton("Aplicar")
        self.btn_apply_dsp.clicked.connect(self._apply_audio_dsp_params)

        f.addRow("FM Canal cutoff (Hz)", self.dsp_chan_cut)
        f.addRow("Audio cutoff (Hz)", self.dsp_aud_cut)
        f.addRow("De-emphasis tau (µs)", self.dsp_tau)
        f.addRow("Drive (tanh)", self.dsp_drive)
        f.addRow("Canal taps", self.dsp_chan_taps)
        f.addRow("Audio taps", self.dsp_aud_taps)
        f.addRow("", self.btn_apply_dsp)

        self.audio_dsp_dock = QDockWidget("Audio DSP", self)
        self.audio_dsp_dock.setWidget(panel)
        self.audio_dsp_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.audio_dsp_dock)
        self.audio_dsp_dock.hide()
        self.dsp_hpf = QDoubleSpinBox()
        self.dsp_hpf.setRange(0.0, 2000.0)
        self.dsp_hpf.setSingleStep(50.0)
        self.dsp_hpf.setValue(0.0)

        f.addRow("Audio HPF (Hz)", self.dsp_hpf)



    def _apply_audio_dsp_params(self):
        params = self._collect_audio_dsp_params()
        if not params:
            return
        try:
            self.manager.update_audio_params(**params)
            self.lbl_status.setText(
                "DSP actualizado: "
                f"chan={params['chan_cutoff_hz']:.0f}Hz, aud={params['aud_cutoff_hz']:.0f}Hz, "
                f"tau={params['tau_us']:.0f}us, drive={params['drive']:.2f}"
            )
        except Exception as e:
            QMessageBox.warning(self, "Audio DSP", f"No se pudo aplicar: {e}")



    # ---------- Menús ----------
    def _build_menus(self):
        mb = self.menuBar()

        m_devices = mb.addMenu("Devices")
        for name in self.device_map.keys():
            act = QAction(name, self)
            act.triggered.connect(lambda _=False, n=name: self.set_active_device(n))
            m_devices.addAction(act)

        m_view = mb.addMenu("View")

        self.act_controls = QAction("Controls", self, checkable=True)
        self.act_controls.setChecked(True)
        self.act_controls.triggered.connect(self.controls_dock.setVisible)
        m_view.addAction(self.act_controls)

        self.act_radios = QAction("Radios", self, checkable=True)
        self.act_radios.setChecked(False)
        self.act_radios.triggered.connect(self.radios_dock.setVisible)
        m_view.addAction(self.act_radios)

        self.act_audio_dsp = QAction("Audio DSP", self, checkable=True)
        self.act_audio_dsp.setChecked(False)
        self.act_audio_dsp.triggered.connect(self._toggle_audio_dsp_dock)

        m_view.addAction(self.act_audio_dsp)

        self.audio_dsp_dock.visibilityChanged.connect(self.act_audio_dsp.setChecked)


        # sincroniza checkmarks si cierran con la X
        self.controls_dock.visibilityChanged.connect(self.act_controls.setChecked)
        self.radios_dock.visibilityChanged.connect(self.act_radios.setChecked)

        m_banks = mb.addMenu("Bancos / Memorias")

        self.act_manage_banks = QAction("Administrar bancos…", self)
        self.act_manage_banks.triggered.connect(self._open_banks_dialog)
        m_banks.addAction(self.act_manage_banks)

        self.act_scanner_panel = QAction("Panel Escáner", self, checkable=True)
        self.act_scanner_panel.triggered.connect(self._toggle_scanner_dock)
        m_banks.addAction(self.act_scanner_panel)
        self.scanner_dock.visibilityChanged.connect(self.act_scanner_panel.setChecked)
        self.act_scanner_panel.setChecked(False)




    # ---------- Helpers ----------
    def _find_device_name(self, driver_obj):
        for name, drv in self.device_map.items():
            if drv is driver_obj:
                return name
        return "N/A"

    def set_active_device(self, name: str):
        drv = self.device_map.get(name)
        if drv is None:
            return

        # si estabas monitoreando, para audio
        if self.is_monitoring:
            try:
                self.manager.stop_audio()
            except Exception:
                pass
            self.is_monitoring = False
            self.btn_monitor.setText("▶ MONITOR")

        self.active_device_name = name
        self.active_driver = drv

        # UI
        if self.device_combo.currentText() != name:
            self.device_combo.setCurrentText(name)
        self.lbl_status.setText(f"Dispositivo activo: {name}")

        # fuerza reconectar en FFT
        try:
            if hasattr(self.active_driver, "connected"):
                self.active_driver.connected = False
        except Exception:
            pass

    # ---------- Theme ----------
    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color:#020617; }
            QStatusBar {
                background-color:#020617;
                color:#9ca3af;
                border-top:1px solid #1f2937;
            }
            QDockWidget::title {
                background:#0b1220;
                color:#e5e7eb;
                padding:6px;
                font-weight:700;
                border-bottom:1px solid #1f2937;
            }
            QDockWidget {
                background:#020617;
                border:1px solid #1f2937;
            }
            QGroupBox {
                border:1px solid #1f2937;
                border-radius:10px;
                margin-top:8px;
                padding:10px;
                background:#020617;
                font-weight:700;
            }
            QComboBox, QDoubleSpinBox {
                padding:8px 10px;
                border:1px solid #1f2937;
                border-radius:10px;
                color:#e5e7eb;
                background:#0b1220;
                min-height:36px;
            }
            QPushButton {
                background:#111827;
                border:1px solid #1f2937;
                border-radius:10px;
                padding:8px 10px;
                color:#e5e7eb;
                font-weight:800;
            }
            QPushButton:hover { background:#1f2937; }
        """)

    # ---------- Waterfall direction ----------
    def _set_waterfall_reverse(self, on: bool):
        if hasattr(self.waterfall, "set_reverse"):
            try:
                self.waterfall.set_reverse(bool(on))
            except Exception:
                pass

    # ---------- TUNE ----------
    def _apply_tune(self):
        mhz = float(self.freq_spin.value())

        # Evita tunear si no cambió (muy importante)
        if getattr(self, "_last_tuned_mhz", None) is not None:
            if abs(mhz - self._last_tuned_mhz) < 0.000001:  # 1 Hz aprox en MHz
                return

        self._last_tuned_mhz = mhz
        self.lbl_status.setText(f"TUNE: {mhz:.6f} MHz")

        # Si tu driver soporta set_center_freq(hz)
        if self.active_driver and hasattr(self.active_driver, "set_center_freq"):
            try:
                self.active_driver.set_center_freq(mhz * 1e6)
            except Exception as e:
                QMessageBox.warning(self, "TUNE", f"No se pudo sintonizar: {e}")


    def _on_waterfall_tune(self, mhz: float, final: bool):
            # Si el escáner está corriendo, evitamos que el dial pelee con el scanner
                try:
                    if getattr(self, "scanner", None) is not None and getattr(self.scanner, "is_running", False):
                        return
                except Exception:
                    pass

                mhz = float(mhz)

                # Actualiza el dial sin disparar valueChanged
                try:
                    self.freq_spin.blockSignals(True)
                    self.freq_spin.setValue(mhz)
                    self.freq_spin.blockSignals(False)
                except Exception:
                    pass

                # Si es “final” (solté mouse), aplica ya
                if final:
                    self._wf_tune_timer.stop()
                    self._apply_tune()
                    return

                # Mientras arrastras: throttle + aplica tune suave
                self._wf_tune_timer.start()



    # ---------- Monitor ----------
    
    def _collect_audio_dsp_params(self) -> dict:
        """Lee valores del dock (si existe) y retorna params para el stream."""
        if not hasattr(self, "dsp_chan_cut"):
            return {}
        return dict(
            chan_cutoff_hz=float(self.dsp_chan_cut.value()),
            aud_cutoff_hz=float(self.dsp_aud_cut.value()),
            tau_us=float(self.dsp_tau.value()),
            drive=float(self.dsp_drive.value()),
            hpf_hz=float(self.dsp_hpf.value()) if hasattr(self, "dsp_hpf") else 0.0,
            chan_taps=int(self.dsp_chan_taps.value()) if hasattr(self, "dsp_chan_taps") else 161,
            aud_taps=int(self.dsp_aud_taps.value()) if hasattr(self, "dsp_aud_taps") else 161,
        )

    def _prime_audio_dsp_defaults(self):
        """Envía al motor los defaults del dock (se aplican al iniciar monitor)."""
        params = self._collect_audio_dsp_params()
        if not params:
            return
        try:
            self.manager.update_audio_params(**params)
        except Exception:
            pass

    def _toggle_monitor(self):
        # START MONITOR
        if not self.is_monitoring:
            freq = float(self.freq_spin.value())
            mode = self.mode_btn.text().strip().upper()


            if mode == "FM" and not (88.0 <= freq <= 108.0):
                QMessageBox.warning(self, "FM", "FM (broadcast) normalmente es 88–108 MHz.")
                return


            # Cargar parámetros del dock automáticamente (se aplican al iniciar el stream)
            self._prime_audio_dsp_defaults()

            self.is_monitoring = True
            self.btn_monitor.setText("⏹ STOP")
            self.lbl_status.setText(f"MONITOR {mode} · {freq:.6f} MHz (spectro sigue activo)")

            # Arranca audio SIN parar el espectro
            def _run_audio():
                try:
                    self.manager.start_audio(self.active_driver, freq, mode)
                except Exception as e:
                    QTimer.singleShot(0, lambda: self._monitor_failed(str(e)))

            threading.Thread(target=_run_audio, daemon=True).start()
            return

        # STOP MONITOR
        try:
            self.manager.stop_audio()
        except Exception as e:
            QMessageBox.warning(self, "STOP", f"Error al detener audio: {e}")

        self.is_monitoring = False
        self.btn_monitor.setText("▶ MONITOR")
        self.lbl_status.setText("Listo · SDR Style")


    def _monitor_failed(self, err: str):
        self.is_monitoring = False
        self.btn_monitor.setText("▶ MONITOR")
        self.lbl_status.setText(f"Monitor falló: {err}")
        self.spec_timer.start()


    def _toggle_audio_dsp_dock(self, checked=None):
        # checked puede venir como bool o None dependiendo de la señal
        if checked is None:
            checked = not self.audio_dsp_dock.isVisible()
        self.audio_dsp_dock.setVisible(bool(checked))


    # ---------- Update FFT + Waterfall ----------


    def _update_from_active_device(self):

        if self.active_driver is None:
            self.lbl_status.setText("No hay dispositivo activo.")
            return

        # conectar si aplica
        if hasattr(self.active_driver, "connected") and not getattr(self.active_driver, "connected", False):
            try:
                if hasattr(self.active_driver, "connect"):
                    self.active_driver.connect()
                self.lbl_status.setText(f"{self.active_device_name} conectado (modo FFT).")
            except Exception as e:
                self.lbl_status.setText(f"Error al conectar {self.active_device_name}: {e}")
                return

        try:
            if not hasattr(self.active_driver, "get_spectrum"):
                self.lbl_status.setText(f"{self.active_device_name}: no soporta get_spectrum().")
                return
            freqs, levels = self.active_driver.get_spectrum()
        except Exception as e:
            self.lbl_status.setText(f"Error get_spectrum ({self.active_device_name}): {e}")
            return

        if freqs is None or len(freqs) == 0 or levels is None or len(levels) == 0:
            return

        # --- Normaliza a numpy + asegura orden de freqs ---
        try:
            freqs = np.asarray(freqs, dtype=np.float64)
            levels_raw = np.asarray(levels, dtype=np.float64)
        except Exception:
            return

        if freqs.shape[0] != levels_raw.shape[0]:
            return

        # Si freqs viene invertido, ordena ambos
        if freqs[0] > freqs[-1]:
            freqs = freqs[::-1]
            levels_raw = levels_raw[::-1]

        # -------------------------------------------------
        # 1) PARA EL ESPECTRO: intenta convertir a dB
        #    (sin tocar el waterfall)
        # -------------------------------------------------
        levels_for_spec = levels_raw

        try:
            finite = np.isfinite(levels_raw)
            if finite.any():
                lv = levels_raw[finite]

                # 1) si viene en lineal (>=0) -> a dBFS
                frac_nonneg = float(np.mean(lv >= 0))
                if frac_nonneg > 0.90:
                    eps = 1e-12
                    lv = 20.0 * np.log10(np.maximum(lv, eps))

                # 2) si NO parece dBm real (picos > -20 dB), aplica offset automático para caer en -130..-40
                p = float(np.percentile(lv, 99.5))
                if p > -20.0:
                    # mapea el pico alto a ~ -45 dBm (look SDR Control)
                    lv = lv + (-45.0 - p)

                tmp = levels_raw.copy()
                tmp[finite] = lv
                levels_for_spec = tmp
        except Exception:
            levels_for_spec = levels_raw

        # -------------------------------------------------
        # 2) tuned_mhz (scanner vs manual)
        # -------------------------------------------------
        if getattr(self, "scanner", None) is not None and getattr(self.scanner, "is_running", False):
            try:
                center_hz = (float(freqs[0]) + float(freqs[-1])) / 2.0
                tuned_mhz = center_hz / 1e6
            except Exception:
                tuned_mhz = float(self.freq_spin.value())

            # Actualiza el dial SI el usuario no lo está editando
            try:
                if not self.freq_spin.hasFocus() and not self.freq_spin.lineEdit().hasFocus():
                    self.freq_spin.blockSignals(True)
                    self.freq_spin.setValue(float(tuned_mhz))
                    self.freq_spin.blockSignals(False)
            except Exception:
                pass
        else:
            tuned_mhz = float(self.freq_spin.value())

        # -------------------------------------------------
        # 2.5) BandBar por rango (MARINE/AIR/FM)
        # -------------------------------------------------
        try:
            f = float(tuned_mhz)
            if 156.0 <= f <= 174.0:
                self.bandbar.set_text("MARINE VHF")
            elif 118.0 <= f <= 137.0:
                self.bandbar.set_text("AIR BAND")
            elif 88.0 <= f <= 108.0:
                self.bandbar.set_text("FM BROADCAST")
            else:
                self.bandbar.set_text("—")
        except Exception:
            pass

        # -------------------------------------------------
        # 3) Spectrum: usa levels_for_spec (ya en dB si aplica)
        # -------------------------------------------------
        try:
            self.spectrum.update_spectrum(freqs, levels_for_spec, tuned_mhz=tuned_mhz)
            self.spectrum.set_tuned_freq_mhz(tuned_mhz)
        except Exception:
            pass

        
        try:
            self.spectrum.set_fixed_dbm_range(-130.0, -40.0)
        except Exception:
            pass

        # -------------------------------------------------
        # 4) Waterfall: usa levels_raw (como ya te funciona)
        # -------------------------------------------------
        try:
            if hasattr(self.waterfall, "append_line"):
                self.waterfall.append_line(levels_raw)
        except Exception:
            pass

        # -------------------------------------------------
        # 5) Status + axis waterfall
        # -------------------------------------------------
        try:
            span_mhz = (freqs[-1] - freqs[0]) / 1e6
            # debug útil: min/max del espectro
            try:
                lv = levels_for_spec[np.isfinite(levels_for_spec)]
                mn = float(np.min(lv)) if lv.size else 0.0
                mx = float(np.max(lv)) if lv.size else 0.0
                mm = f" | Lvl[{mn:.1f},{mx:.1f}]"
            except Exception:
                mm = ""

            self.lbl_status.setText(
                f"{self.active_device_name} · Tuned {tuned_mhz:.6f} MHz · "
                f"Rango {freqs[0]/1e6:.6f}–{freqs[-1]/1e6:.6f} MHz · Span {span_mhz:.3f} MHz{mm}"
            )
        except Exception:
            pass

        try:
            self.waterfall.set_freq_axis(freqs[0], freqs[-1])
            self.waterfall.set_tuned_freq(float(tuned_mhz) * 1e6)
        except Exception:
            pass




    def _open_banks_dialog(self):
        QMessageBox.information(self, "Bancos", "Aquí irá el CRUD de bancos (siguiente paso).")

    def _toggle_scanner_dock(self, checked=None):
        if not hasattr(self, "scanner_dock"):
            self._build_scanner_dock()
        if checked is None:
            checked = not self.scanner_dock.isVisible()
        self.scanner_dock.setVisible(bool(checked))


    def _scan_status_from_thread(self, st):
        """Recibe ScanStatus desde el hilo del scanner; lo pasamos al hilo UI."""
        try:
            QTimer.singleShot(0, lambda s=st: self._apply_scan_status(s))
        except Exception:
            pass

    def _apply_scan_status(self, st):
        try:
            # 1) Dial (panel izquierdo)
            self.freq_spin.blockSignals(True)
            self.freq_spin.setValue(float(st.freq_mhz))
            self.freq_spin.blockSignals(False)

            # 2) Línea de "tuned / scan" en el espectro
            self.spectrum.set_tuned_freq_mhz(float(st.freq_mhz))

            # 3) Centrar la vista del espectro en la frecuencia actual del escaneo
            self.spectrum.center_on_mhz(float(st.freq_mhz))

            # 4) Waterfall (si existe)
            try:
                self.waterfall.set_tuned_freq(float(st.freq_mhz) * 1e6)
            except Exception:
                pass

            # 5) Texto estado
            if hasattr(self, "lbl_scan_status"):
                self.lbl_scan_status.setText(
                    f"{st.state} · [{st.bank_kind.upper()}] {st.bank_name} · {st.freq_mhz:.6f} MHz · {st.level_db:.1f} dB"
                )
        except Exception:
            pass


  


    def _toggle_scan(self):
        # 1) Validaciones base
        driver = getattr(self, "active_driver", None)
        if driver is None:
            QMessageBox.warning(self, "Escáner", "No hay radio/driver activo.")
            return

        if not hasattr(self, "scanner"):
            QMessageBox.warning(self, "Escáner", "ScannerEngine no inicializado.")
            return

        if not hasattr(self, "banks_store"):
            QMessageBox.warning(self, "Escáner", "BanksStore no inicializado.")
            return

        # 2) Si ya está corriendo -> detener
        if getattr(self.scanner, "is_running", False):
            try:
                self.scanner.stop()
            except Exception:
                pass

            self.btn_scan.setText("Iniciar escáner")
            if hasattr(self, "lbl_scan_status"):
                self.lbl_scan_status.setText("Escáner detenido.")
            if hasattr(self, "btn_scan_edit"):
                self.btn_scan_edit.setEnabled(True)
            if hasattr(self, "btn_scan_del"):
                self.btn_scan_del.setEnabled(True)

            # refresca lista (por si se activó/desactivó algo)
            if hasattr(self, "_refresh_scanner_panel"):
                self._refresh_scanner_panel()
            return

        # 3) Antes de iniciar: validar bancos activos (según filtro)
        flt = self.scan_filter.currentText() if hasattr(self, "scan_filter") else "Todos"

        freq_active = any(bool(b.get("active")) for b in self.banks_store.list_banks("freq"))
        range_active = any(bool(b.get("active")) for b in self.banks_store.list_banks("range"))

        if flt == "Frecuencias" and not freq_active:
            QMessageBox.warning(self, "Escáner", "No hay bancos de FRECUENCIAS activos.")
            return
        if flt == "Rangos" and not range_active:
            QMessageBox.warning(self, "Escáner", "No hay bancos de RANGOS activos.")
            return
        if flt == "Todos" and not (freq_active or range_active):
            QMessageBox.warning(self, "Escáner", "No hay bancos activos para escanear.")
            return

        # 4) Asegurar driver conectado (si aplica)
        try:
            if hasattr(driver, "connect"):
                driver.connect()
        except Exception as e:
            QMessageBox.warning(self, "Escáner", f"No se pudo conectar el driver:\n{e}")
            return

        # 5) Iniciar escáner
        try:
            # Opcional: pasar el filtro al scanner si tu ScannerEngine lo soporta
            # Por ahora, el scanner puede leer bancos activos (freq+range)
            kind = {
                "Todos": "ALL",
                "Frecuencias": "FREQ",
                "Rangos": "RANGE",
            }.get(flt, "ALL")

          
            flt = self.scan_filter.currentText() if hasattr(self, "scan_filter") else "Todos"
            kind = {"Todos": "ALL", "Frecuencias": "FREQ", "Rangos": "RANGE"}.get(flt, "ALL")

            self.scanner.start(driver, kind_filter=kind, on_status=self._scan_status_from_thread)




            self.btn_scan.setText("Detener escáner")
            if hasattr(self, "lbl_scan_status"):
                self.lbl_scan_status.setText("Escáner en ejecución… (Stop para finalizar)")
            if hasattr(self, "btn_scan_edit"):
                self.btn_scan_edit.setEnabled(False)
            if hasattr(self, "btn_scan_del"):
                self.btn_scan_del.setEnabled(False)

        except Exception as e:
            QMessageBox.warning(self, "Escáner", f"No se pudo iniciar:\n{e}")
            self.btn_scan.setText("Iniciar escáner")
            if hasattr(self, "lbl_scan_status"):
                self.lbl_scan_status.setText("Error iniciando escáner.")
            if hasattr(self, "btn_scan_edit"):
                self.btn_scan_edit.setEnabled(True)
            if hasattr(self, "btn_scan_del"):
                self.btn_scan_del.setEnabled(True)

    
    def _open_banks_dialog(self):
        try:
            dlg = BanksDialog(self, self.banks_store)
            dlg.exec()
            # opcional: refrescar panel del escáner si ya lo tienes
            if hasattr(self, "_refresh_scanner_panel"):
                self._refresh_scanner_panel()
        except Exception as e:
            QMessageBox.warning(self, "Bancos", f"No se pudo abrir el administrador:\n{e}")

    def _refresh_scanner_panel(self):
        if not hasattr(self, "banks_store"):
            return

        # Evitar disparar itemChanged mientras reconstruimos
        self.banks_list.blockSignals(True)
        self.banks_list.clear()

        flt = self.scan_filter.currentText()

        def add_bank(kind: str, b: dict):
            name = b.get("name", "")
            active = bool(b.get("active", False))

            if kind == "freq":
                items = b.get("items") or []
                modes = sorted({(it.get("mode") or "").upper().strip() for it in items if it.get("mode")})
                meta = f"{len(items)} frec | {', '.join(modes) if modes else '-'}"
                tag = "FREQ"
            else:
                r = b.get("range") or {}
                meta = f"{r.get('start_mhz','?')}–{r.get('stop_mhz','?')} MHz | TS {r.get('ts_khz','?')} kHz | {str(b.get('mode','')).upper()}"
                tag = "RANGE"

            it = QListWidgetItem(f"[{tag}] {name}  —  {meta}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if active else Qt.Unchecked)

            # guardamos kind y id en UserRole
            it.setData(Qt.UserRole, (kind, b.get("id")))
            self.banks_list.addItem(it)

        if flt in ("Todos", "Frecuencias"):
            for b in self.banks_store.list_banks("freq"):
                add_bank("freq", b)

        if flt in ("Todos", "Rangos"):
            for b in self.banks_store.list_banks("range"):
                add_bank("range", b)

        self.banks_list.blockSignals(False)



    def _on_bank_item_changed(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole)
        if not data:
            return
        kind, bank_id = data
        active = (item.checkState() == Qt.Checked)
        try:
            self.banks_store.set_active(kind, bank_id, active)
        except Exception as e:
            QMessageBox.warning(self, "Bancos", f"No se pudo cambiar activo:\n{e}")
        # no recargamos toda la lista para no perder selección


    def _selected_bank(self):
        it = self.banks_list.currentItem()
        if not it:
            return None, None
        data = it.data(Qt.UserRole)
        if not data:
            return None, None
        return data  # (kind, id)


    def _edit_selected_bank(self):
        kind, bank_id = self._selected_bank()
        if not bank_id:
            QMessageBox.information(self, "Editar", "Selecciona un banco.")
            return
        # Abre el administrador general
        self._open_banks_dialog()
        self._refresh_scanner_panel()

    def _on_volume_changed(self, v: int):
        self.lbl_vol.setText(f"Vol: {int(v)}%")
        # Si tu AudioEngine tiene set_volume, se activa aquí
        try:
            if hasattr(self.manager, "audio") and hasattr(self.manager.audio, "set_volume"):
                self.manager.audio.set_volume(float(v) / 100.0)
        except Exception:
            pass



    def _delete_selected_bank(self):
        kind, bank_id = self._selected_bank()
        if not bank_id:
            QMessageBox.information(self, "Eliminar", "Selecciona un banco.")
            return

        if QMessageBox.question(self, "Confirmar", "¿Eliminar este banco?") != QMessageBox.Yes:
            return

        try:
            self.banks_store.delete_bank(kind, bank_id)
            self._refresh_scanner_panel()
        except Exception as e:
            QMessageBox.warning(self, "Eliminar", str(e))







if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
