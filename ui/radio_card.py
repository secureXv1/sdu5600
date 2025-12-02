# ui/radio_card.py
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QComboBox,
    QSlider,
    QDoubleSpinBox
)
from PySide6.QtCore import Qt, QTimer

from core.scan_engine import ScanEngine, ScanConfig


class RadioCard(QWidget):
    def __init__(self, name, driver):
        super().__init__()
        self.driver = driver

        # --- Estado interno ---
        self.scan_engine: ScanEngine | None = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(250)  # ms → ~4 pasos/seg
        self._scan_timer.timeout.connect(self._on_scan_tick)

        # ---------------------------
        # Layout principal
        # ---------------------------
        layout = QVBoxLayout(self)

        # Nombre del equipo
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.lbl_name)

        # Frecuencia actual
        self.lbl_freq = QLabel("000.000.000 MHz")
        self.lbl_freq.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(self.lbl_freq)

        # ---------------------------
        # Banda (HF/VHF/UHF) — por ahora solo visual
        # ---------------------------
        band_layout = QHBoxLayout()
        self.btn_hf = QPushButton("HF")
        self.btn_vhf = QPushButton("VHF")
        self.btn_uhf = QPushButton("UHF")
        band_layout.addWidget(self.btn_hf)
        band_layout.addWidget(self.btn_vhf)
        band_layout.addWidget(self.btn_uhf)
        layout.addLayout(band_layout)

        # ---------------------------
        # Modo
        # ---------------------------
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo:"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["AM", "FM", "NFM", "USB", "LSB"])
        mode_layout.addWidget(self.cmb_mode)
        layout.addLayout(mode_layout)

        # Cambiar modo en el driver (lógico para HackRF)
        self.cmb_mode.currentTextChanged.connect(self._on_mode_changed)

        # ---------------------------
        # Rango de escaneo
        # ---------------------------
        scan_range_layout = QHBoxLayout()

        self.spn_start = QDoubleSpinBox()
        self.spn_start.setDecimals(3)
        self.spn_start.setRange(0.001, 6000.0)
        self.spn_start.setSingleStep(0.005)
        self.spn_start.setValue(120.000)  # ejemplo por defecto

        self.spn_end = QDoubleSpinBox()
        self.spn_end.setDecimals(3)
        self.spn_end.setRange(0.001, 6000.0)
        self.spn_end.setSingleStep(0.005)
        self.spn_end.setValue(122.000)

        self.spn_step = QDoubleSpinBox()
        self.spn_step.setDecimals(3)
        self.spn_step.setRange(0.001, 5000.0)
        self.spn_step.setSingleStep(0.5)
        self.spn_step.setValue(6.5)  # kHz por defecto

        scan_range_layout.addWidget(QLabel("Inicio (MHz):"))
        scan_range_layout.addWidget(self.spn_start)
        scan_range_layout.addWidget(QLabel("Fin (MHz):"))
        scan_range_layout.addWidget(self.spn_end)
        scan_range_layout.addWidget(QLabel("Paso (kHz):"))
        scan_range_layout.addWidget(self.spn_step)

        layout.addLayout(scan_range_layout)

        # ---------------------------
        # Squelch
        # ---------------------------
        sq_layout = QHBoxLayout()
        sq_layout.addWidget(QLabel("SQL:"))
        self.sld_sql = QSlider(Qt.Horizontal)
        self.sld_sql.setRange(0, 120)  # 0..120 → mapeamos a -120..0 dB
        self.sld_sql.setValue(60)      # ~ -60 dB
        sq_layout.addWidget(self.sld_sql)
        self.lbl_sql_db = QLabel("-60 dB")
        sq_layout.addWidget(self.lbl_sql_db)
        layout.addLayout(sq_layout)

        self.sld_sql.valueChanged.connect(self._on_sql_changed)
        self._on_sql_changed(self.sld_sql.value())

        # ---------------------------
        # Botones de control
        # ---------------------------
        btn_layout = QHBoxLayout()
        self.btn_scan = QPushButton("SCAN")
        self.btn_hold = QPushButton("HOLD")
        self.btn_rec = QPushButton("REC")
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_hold)
        btn_layout.addWidget(self.btn_rec)
        layout.addLayout(btn_layout)

        # Conexión de botones
        self.btn_scan.clicked.connect(self._toggle_scan)
        self.btn_hold.clicked.connect(self._hold_pressed)

        # TODO: conectar self.btn_rec a lógica de grabación cuando la tengamos
        # (por ahora, solo placeholder)

    # =========================================================
    #  Slots / lógica de control
    # =========================================================
    def _on_mode_changed(self, mode: str):
        try:
            if hasattr(self.driver, "set_mode"):
                self.driver.set_mode(mode)
        except Exception:
            # No rompemos la UI si el driver no lo soporta bien
            pass

    def _on_sql_changed(self, value: int):
        """
        Mapear slider [0..120] → squelch_db [-120..0].
        """
        sq_db = -120.0 + float(value)
        self.lbl_sql_db.setText(f"{sq_db:.0f} dB")

    def _toggle_scan(self):
        """
        Botón SCAN: inicia o detiene el escaneo con ScanEngine.
        """
        if self._scan_timer.isActive():
            # Detener scan
            self._scan_timer.stop()
            if self.scan_engine:
                self.scan_engine.stop()
            self.btn_scan.setText("SCAN")
            self.lbl_name.setStyleSheet("font-weight:600;")
            return

        # Asegurarnos de que el driver está conectado
        try:
            if hasattr(self.driver, "connected") and not self.driver.connected:
                self.driver.connect()
        except Exception:
            # Si algo falla, continuamos pero probablemente en simulador
            pass

        # Configurar ScanEngine desde los spinboxes
        start_mhz = self.spn_start.value()
        end_mhz = self.spn_end.value()
        step_khz = self.spn_step.value()

        # Convertimos a Hz
        start_hz = start_mhz * 1_000_000.0
        end_hz = end_mhz * 1_000_000.0
        step_hz = step_khz * 1_000.0

        # Squelch actual
        sq_db = -120.0 + float(self.sld_sql.value())

        cfg = ScanConfig(
            start_hz=start_hz,
            end_hz=end_hz,
            step_hz=step_hz,
            squelch_db=sq_db,
            hold_ms=2000,
        )

        self.scan_engine = ScanEngine(self.driver, cfg)
        self.scan_engine.start()

        # Arrancamos timer
        self._scan_timer.start()
        self.btn_scan.setText("STOP")
        self.lbl_name.setStyleSheet("font-weight:600; color:#16a34a;")

    def _hold_pressed(self):
        """
        Interpretamos HOLD como "pausar el scan inmediatamente"
        en la frecuencia actual (sin reanudar automáticamente).
        """
        if self.scan_engine and self._scan_timer.isActive():
            # Pausar completamente
            self._scan_timer.stop()
            self.scan_engine.stop()
            self.btn_scan.setText("SCAN")
            self.lbl_name.setStyleSheet("font-weight:600; color:#f97316;")

    def _on_scan_tick(self):
        """
        Llamado periódicamente mientras el scan está activo.
        """
        if not self.scan_engine:
            return

        state, freq_hz, level_db = self.scan_engine.step_once()

        # Actualizar frecuencia en la tarjeta
        self._update_freq_label(freq_hz)

        # Visual: si está en HOLD, pintamos el nombre en otro color
        if state == "HOLD":
            self.lbl_name.setStyleSheet("font-weight:600; color:#2563eb;")
        elif state == "SCAN":
            self.lbl_name.setStyleSheet("font-weight:600; color:#16a34a;")
        else:
            self.lbl_name.setStyleSheet("font-weight:600;")

    # =========================================================
    #  Utilidades
    # =========================================================
    def _update_freq_label(self, hz: float):
        mhz = hz / 1_000_000.0
        # Ej: 120.123.456 MHz
        self.lbl_freq.setText(f"{mhz:09.3f} MHz")
