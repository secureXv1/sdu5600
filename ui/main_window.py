# ui/main_window.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStatusBar, QLabel, QSplitter, QFrame, QApplication,
    QComboBox, QPushButton, QMessageBox,
    QDockWidget, QGroupBox, QFormLayout, QCheckBox,
    QDoubleSpinBox, QSpinBox, QListWidget, QListWidgetItem, QAbstractItemView

)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont

import pyqtgraph as pg
import threading

from core.radio_manager import RadioManager
from .radio_card import RadioCard
from .waterfall_widget import WaterfallWidget
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


class SpectrumWidget(QWidget):
    """Display de espectro principal (FFT)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#020617")
        self.plot.showGrid(x=True, y=True, alpha=0.2)

        self.plot.setLabel("bottom", "Frecuencia", units="MHz")
        self.plot.setLabel("left", "Nivel", units="dB")

        self.plot.enableAutoRange(x=False, y=False)
        self.plot.setYRange(-140, 0, padding=0.0)

        self.curve = self.plot.plot([], [], pen=pg.mkPen("#f9fafb", width=1.2))

        self.tune_line = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#22c55e", width=2.4)
        )
        self.plot.addItem(self.tune_line)

        # Overlay texto (arriba del espectro)
        self._info = pg.TextItem(color="#e5e7eb", anchor=(0, 0))
        self._info.setPos(0, 0)  # se reajusta con viewRange
        self.plot.addItem(self._info)

        v.addWidget(self.plot)

    def update_spectrum(self, freqs_hz, levels_db, tuned_mhz: float | None = None):
        if freqs_hz is None or len(freqs_hz) == 0:
            return

        freqs_mhz = (freqs_hz / 1e6) if hasattr(freqs_hz, "__array__") else [f / 1e6 for f in freqs_hz]
        self.curve.setData(freqs_mhz, levels_db)

        # fija X al span recibido
        try:
            x0 = float(freqs_mhz[0]); x1 = float(freqs_mhz[-1])
            self.plot.setXRange(x0, x1, padding=0.0)
        except Exception:
            return

        # Reposicionar overlay arriba-izquierda del view
        try:
            (xr, yr) = self.plot.viewRange()
            self._info.setPos(xr[0], yr[1])  # esquina sup izq
        except Exception:
            pass

        # Texto de rango/centro/span
        try:
            start = float(freqs_mhz[0])
            end = float(freqs_mhz[-1])
            center = (start + end) / 2.0
            span = end - start
            tuned_txt = f" | Tuned: {tuned_mhz:.6f} MHz" if tuned_mhz is not None else ""
            self._info.setText(
                f"Rango: {start:.6f} – {end:.6f} MHz | Centro: {center:.6f} MHz | Span: {span:.3f} MHz{tuned_txt}"
            )
        except Exception:
            pass

    def set_tuned_freq_mhz(self, mhz: float):
        self.tune_line.setPos(float(mhz))

    def center_on_mhz(self, mhz: float):
        """
        Mueve la ventana visible del espectro (XRange) para que el centro sea 'mhz'.
        Mantiene el mismo ancho de span que el usuario esté viendo.
        """
        try:
            (x0, x1), (_y0, _y1) = self.plot.viewRange()
            width = max(0.001, float(x1) - float(x0))  # evita width 0
            c = float(mhz)
            self.plot.setXRange(c - width/2.0, c + width/2.0, padding=0.0)
        except Exception:
            pass


    def set_tuned_freq_mhz(self, mhz: float):
        try:
            self.tune_line.setPos(float(mhz))
            self.tune_line.show()
        except Exception:
            pass

    def center_on_mhz(self, mhz: float):
        try:
            (x0, x1), (_y0, _y1) = self.plot.viewRange()
            width = max(0.001, float(x1) - float(x0))
            c = float(mhz)
            self.plot.setXRange(c - width/2.0, c + width/2.0, padding=0.0)
        except Exception:
            pass





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
            mode = self.mode_combo.currentText().strip().upper()

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

        if freqs is None or len(freqs) == 0:
            return

        # Spectrum
        tuned_mhz = float(self.freq_spin.value())
        self.spectrum.update_spectrum(freqs, levels, tuned_mhz=tuned_mhz)
        self.spectrum.set_tuned_freq_mhz(tuned_mhz)


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
            f"{self.active_device_name} · Tuned {tuned_mhz:.6f} MHz · "
            f"Rango {freqs[0]/1e6:.6f}–{freqs[-1]/1e6:.6f} MHz · Span {span_mhz:.3f} MHz"
        )

        # Rango real del FFT para eje X del waterfall
        try:
            self.waterfall.set_freq_axis(freqs[0], freqs[-1])
            self.waterfall.set_tuned_freq(float(self.freq_spin.value()) * 1e6)
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

    def _scan_status_from_thread(self, st):
        # hilo del scanner -> hilo UI
        try:
            QTimer.singleShot(0, lambda s=st: self._apply_scan_status(s))
        except Exception:
            pass

    def _apply_scan_status(self, st):
        try:
            # Dial
            self.freq_spin.blockSignals(True)
            self.freq_spin.setValue(float(st.freq_mhz))
            self.freq_spin.blockSignals(False)

            # Línea verde + centrar espectro
            if hasattr(self, "spectrum"):
                self.spectrum.set_tuned_freq_mhz(float(st.freq_mhz))
                if hasattr(self.spectrum, "center_on_mhz"):
                    self.spectrum.center_on_mhz(float(st.freq_mhz))

            # Estado
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
