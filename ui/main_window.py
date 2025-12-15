# ui/main_window.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStatusBar, QLabel, QSplitter, QFrame, QApplication,
    QComboBox, QPushButton, QMessageBox,
    QDockWidget, QGroupBox, QFormLayout, QCheckBox,
    QDoubleSpinBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont

import pyqtgraph as pg
import threading

from core.radio_manager import RadioManager
from .radio_card import RadioCard
from .waterfall_widget import WaterfallWidget
from drivers.hackrf_driver import HackRFDriver


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


class SpectrumWidget(QWidget):
    """Display de espectro principal (FFT)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#020617")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Frecuencia", units="Hz")
        self.plot.setLabel("left", "Nivel", units="dB")

        self.curve = self.plot.plot([], [], pen=pg.mkPen("#f9fafb", width=1.2))

        self.tune_line = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#22c55e", width=1.3)
        )
        self.plot.addItem(self.tune_line)

        v.addWidget(self.plot)

    def update_spectrum(self, freqs, levels):
        if freqs is None or len(freqs) == 0:
            return
        self.curve.setData(freqs, levels)

        # Fija rango X siempre al span recibido (evita sensación de “deslizamiento”)
        try:
            self.plot.setXRange(float(freqs[0]), float(freqs[-1]), padding=0.0)
        except Exception:
            pass


    def set_tuned_freq(self, hz: float):
        self.tune_line.setPos(hz)


class WaveformWidget(QWidget):
    """Waveform (arriba), tipo SDR Console."""
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#020617")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Tiempo")
        self.plot.setLabel("left", "Amplitud")

        self.curve = self.plot.plot([], [], pen=pg.mkPen("#f9fafb", width=1.0))
        v.addWidget(self.plot)

    def update_waveform(self, x, y):
        if y is None:
            return
        self.curve.setData(x, y)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Remote Radios Station – SDR Style")
        self.resize(1500, 850)

        # =========================
        #  Manager y tarjetas
        # =========================
        self.manager = RadioManager()
        self.cards: list[RadioCard] = []
        self.device_map = {}  # name -> driver

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
        #  Central layout
        # =========================
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self.ribbon = RibbonBar()
        central_layout.addWidget(self.ribbon)

        self.waveform = WaveformWidget()
        self.spectrum = SpectrumWidget()
        self.waterfall = WaterfallWidget()

        bottom_split = QSplitter(Qt.Vertical)
        bottom_split.addWidget(self.spectrum)
        bottom_split.addWidget(self.waterfall)
        bottom_split.setStretchFactor(0, 2)
        bottom_split.setStretchFactor(1, 3)

        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(self.waveform)
        main_split.addWidget(bottom_split)
        main_split.setStretchFactor(0, 2)
        main_split.setStretchFactor(1, 5)

        central_layout.addWidget(main_split)
        self.setCentralWidget(central)

        # =========================
        #  Docks (Control a la IZQUIERDA / Radios oculto)
        # =========================
        self._build_controls_dock_left()
          

        
        self._build_radios_dock_hidden()

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

        self.freq_spin = QDoubleSpinBox()
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

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["FM", "NFM"])
        self.mode_combo.setMinimumHeight(38)

        self.btn_monitor = QPushButton("▶ MONITOR")
        self.btn_monitor.setMinimumHeight(44)
        self.btn_monitor.clicked.connect(self._toggle_monitor)

        f_rx.addRow("Frecuencia", self.freq_spin)
        f_rx.addRow("Modo", self.mode_combo)
        f_rx.addRow("", self.btn_monitor)

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



    def _build_radios_dock_hidden(self):
        self.left_panel = LeftPanel(self.cards)
        self.radios_dock = QDockWidget("Radios", self)
        self.radios_dock.setWidget(self.left_panel)
        self.radios_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.radios_dock)
        self.radios_dock.hide()  # oculto por defecto

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

        # sincroniza checkmarks si cierran con la X
        self.controls_dock.visibilityChanged.connect(self.act_controls.setChecked)
        self.radios_dock.visibilityChanged.connect(self.act_radios.setChecked)

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


    # ---------- Monitor ----------
    def _toggle_monitor(self):
        # START MONITOR
        if not self.is_monitoring:
            freq = float(self.freq_spin.value())
            mode = self.mode_combo.currentText().strip().upper()

            if mode == "FM" and not (88.0 <= freq <= 108.0):
                QMessageBox.warning(self, "FM", "FM (broadcast) normalmente es 88–108 MHz.")
                return

            self.is_monitoring = True
            self.spec_timer.stop()
            self.btn_monitor.setText("⏹ STOP")
            self.lbl_status.setText(f"Iniciando MONITOR {mode} · {freq:.6f} MHz ...")

            # soltar dispositivo activo si estaba en FFT
            try:
                if self.active_driver is not None:
                    if hasattr(self.active_driver, "disconnect"):
                        try:
                            self.active_driver.disconnect()
                        except Exception:
                            pass
                    if hasattr(self.active_driver, "connected"):
                        self.active_driver.connected = False
            except Exception:
                pass

            def _run_audio():
                try:
                    self.manager.start_audio(freq, mode)  # bloqueante
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
        self.spec_timer.start()

    def _monitor_failed(self, err: str):
        self.is_monitoring = False
        self.btn_monitor.setText("▶ MONITOR")
        self.lbl_status.setText(f"Monitor falló: {err}")
        self.spec_timer.start()

    # ---------- Update FFT + Waterfall ----------
    def _update_from_active_device(self):
        if self.is_monitoring:
            return

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

        if freqs is None or len(freqs) == 0:
            return

        # Spectrum
        self.spectrum.update_spectrum(freqs, levels)

        # marcador centro
        center_idx = len(freqs) // 2
        center_freq = freqs[center_idx]
        self.spectrum.set_tuned_freq(center_freq)

        # Waterfall (vertical: apila líneas, no “desplaza x”)
        try:
            if hasattr(self.waterfall, "append_line"):
                self.waterfall.append_line(levels)
        except Exception:
            pass

        # Waveform (placeholder)
        try:
            y = levels
            x = list(range(len(y)))
            self.waveform.update_waveform(x, y)
        except Exception:
            pass

        span_mhz = (freqs[-1] - freqs[0]) / 1e6
        self.lbl_status.setText(
            f"{self.active_device_name} · Centro {center_freq/1e6:,.3f} MHz · Span {span_mhz:,.3f} MHz"
        )


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
