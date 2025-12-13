# ui/main_window.py
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStatusBar, QLabel, QSplitter, QFrame, QApplication,
    QLineEdit, QComboBox, QPushButton, QMessageBox
)

from PySide6.QtCore import Qt, QTimer
import pyqtgraph as pg
import threading


from core.radio_manager import RadioManager
from .radio_card import RadioCard
from .waterfall_widget import WaterfallWidget
from drivers.hackrf_driver import HackRFDriver



class RibbonBar(QWidget):
    """
    Barra superior estilo SDR Console (simplificada).
    """
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

        self.setAutoFillBackground(True)
        self.setStyleSheet("""
            RibbonBar {
                background-color:#111827;
                border-bottom:1px solid #1f2937;
            }
        """)


class SpectrumWidget(QWidget):
    """
    Display de espectro principal (FFT), estilo SDR.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#020617")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Frecuencia", units="Hz")
        self.plot.setLabel("left", "Nivel", units="dB")

        # curva FFT
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#f9fafb", width=1.2))

        # marcador vertical de frecuencia sintonizada
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

    def set_tuned_freq(self, hz: float):
        self.tune_line.setPos(hz)


class LeftPanel(QWidget):
    """
    Panel izquierdo con las tarjetas de radio (RadioCard).
    """
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
                QPushButton:hover {
                    background-color:#1f2937;
                }
                QLabel {
                    color:#e5e7eb;
                }
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
        self.resize(1400, 800)

        # =========================
        #  Manager y tarjetas
        # =========================
        self.manager = RadioManager()
        self.cards = []

        for radio_cfg in self.manager.radios:
            card = RadioCard(radio_cfg["name"], radio_cfg["driver"])
            self.cards.append(card)

        # Identificamos el HackRF (si existe)
        self.hackrf_driver: HackRFDriver | None = None
        for card in self.cards:
            if isinstance(card.driver, HackRFDriver):
                self.hackrf_driver = card.driver
                break

        # =========================
        #  Widgets principales
        # =========================
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Ribbon
        self.ribbon = RibbonBar()
        central_layout.addWidget(self.ribbon)

        # Splitter vertical: zona principal + waterfall
        main_split = QSplitter(Qt.Vertical)

        # Splitter horizontal superior: panel izq + spectrum
        top_split = QSplitter(Qt.Horizontal)

        self.left_panel = LeftPanel(self.cards)
        self.spectrum = SpectrumWidget()

        top_split.addWidget(self.left_panel)
        top_split.addWidget(self.spectrum)
        top_split.setStretchFactor(0, 0)
        top_split.setStretchFactor(1, 1)

        # Waterfall abajo
        self.waterfall = WaterfallWidget()

        main_split.addWidget(top_split)
        main_split.addWidget(self.waterfall)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)

        central_layout.addWidget(main_split)
        self.setCentralWidget(central)

        # =========================
        #  Barra de estado
        # =========================
        status = QStatusBar()
        self.setStatusBar(status)
        self.lbl_status = QLabel("Listo · Estilo SDR")
        status.addPermanentWidget(self.lbl_status)


        # =========================
        #  Control MONITOR (Audio)
        # =========================
        self.is_monitoring = False

        self.freq_edit = QLineEdit("91.4")
        self.freq_edit.setFixedWidth(90)
        self.freq_edit.setStyleSheet("padding:4px 6px; border:1px solid #1f2937; border-radius:6px; color:#e5e7eb; background:#0b1220;")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["FM", "NFM"])
        self.mode_combo.setStyleSheet("padding:4px 6px; border:1px solid #1f2937; border-radius:6px; color:#e5e7eb; background:#0b1220;")

        self.btn_monitor = QPushButton("▶ MONITOR")
        self.btn_monitor.setStyleSheet("""
            QPushButton { background:#111827; border:1px solid #1f2937; border-radius:6px; padding:4px 10px; color:#e5e7eb; }
            QPushButton:hover { background:#1f2937; }
        """)
        self.btn_monitor.clicked.connect(self._toggle_monitor)

        status.addPermanentWidget(QLabel("  Freq (MHz):"))
        status.addPermanentWidget(self.freq_edit)
        status.addPermanentWidget(QLabel("  Mode:"))
        status.addPermanentWidget(self.mode_combo)
        status.addPermanentWidget(self.btn_monitor)


        # Tema oscuro general
        self._apply_theme()

        # =========================
        #  Timer para actualizar HackRF
        # =========================
        self.spec_timer = QTimer(self)
        self.spec_timer.setInterval(200)  # ms
        self.spec_timer.timeout.connect(self._update_from_hackrf)
        self.spec_timer.start()

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color:#020617;
            }
            QStatusBar {
                background-color:#020617;
                color:#9ca3af;
                border-top:1px solid #1f2937;
            }
        """)

    def _parse_freq_mhz(self) -> float | None:
        txt = (self.freq_edit.text() or "").strip().replace(",", ".")
        try:
            return float(txt)
        except Exception:
            return None

    def _toggle_monitor(self):
    # START MONITOR
        if not self.is_monitoring:
            freq = self._parse_freq_mhz()
            if freq is None:
                QMessageBox.warning(self, "Frecuencia", "Frecuencia inválida. Ej: 91.4")
                return

            mode = self.mode_combo.currentText().strip().upper()

            # Validaciones básicas
            if mode == "FM" and not (88.0 <= freq <= 108.0):
                QMessageBox.warning(self, "FM", "FM (broadcast) normalmente es 88–108 MHz.")
                return

            # 1) Marcar estado + parar FFT
            self.is_monitoring = True
            self.spec_timer.stop()
            self.btn_monitor.setText("⏹ STOP")
            self.lbl_status.setText(f"Iniciando MONITOR {mode} · {freq:.3f} MHz ...")

            # 2) IMPORTANTE: “soltar” el HackRFDriver de FFT (evita -5)
            # (si tu driver tiene disconnect(), úsalo; si no, al menos marca no conectado)
            try:
                if self.hackrf_driver is not None:
                    if hasattr(self.hackrf_driver, "disconnect"):
                        try:
                            self.hackrf_driver.disconnect()
                        except Exception:
                            pass
                    self.hackrf_driver.connected = False
            except Exception:
                pass

            # 3) Correr audio en hilo y capturar fallo
            def _run_audio():
                try:
                    self.manager.start_audio(freq, mode)  # bloqueante
                except Exception as e:
                    # regresar a UI thread
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
        self.lbl_status.setText("Listo · Estilo SDR")

        # Reanudar FFT
        self.spec_timer.start()

    def _monitor_failed(self, err: str):
        self.is_monitoring = False
        self.btn_monitor.setText("▶ MONITOR")
        self.lbl_status.setText(f"Monitor falló: {err}")
        # volver a FFT
        self.spec_timer.start()





    # --------------------------------------------------
    # Actualización de Spectrum + Waterfall desde HackRF
    # --------------------------------------------------
    def _update_from_hackrf(self):

        if getattr(self, "is_monitoring", False):
            return
        
        if self.hackrf_driver is None:
            self.lbl_status.setText("HackRF no encontrado (revisa radios.json y drivers).")
            return

        # conectamos si aún no está
        if not self.hackrf_driver.connected:
            try:
                self.hackrf_driver.connect()
                self.lbl_status.setText("HackRF conectado (modo FFT).")
            except Exception as e:
                self.lbl_status.setText(f"Error al conectar HackRF: {e}")
                return

        try:
            freqs, levels = self.hackrf_driver.get_spectrum()
        except Exception as e:
            self.lbl_status.setText(f"Error get_spectrum: {e}")
            return

        if freqs is None or len(freqs) == 0:
            return

        # Spectrum
        self.spectrum.update_spectrum(freqs, levels)

        # marcador en centro de la banda
        center_idx = len(freqs) // 2
        center_freq = freqs[center_idx]
        self.spectrum.set_tuned_freq(center_freq)

        # Waterfall
        self.waterfall.append_line(levels)

        # texto barra de estado
        span_mhz = (freqs[-1] - freqs[0]) / 1e6
        self.lbl_status.setText(
            f"HackRF · Centro {center_freq/1e6:,.3f} MHz · Span {span_mhz:,.3f} MHz"
        )


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
