"""
SDU-5600 GUI — v0.1 (PySide6)
Autor: RadioDigital / ChatGPT
Objetivo: Interfaz gráfica simple e intuitiva para operar un AOR SDU-5600.

• Enfoque UX: botones claros, presets de span, campos en MHz, autoscale, marcadores.
• Arquitectura: GUI (Qt) + Driver (abstracción). El driver actual simula datos para que
  puedas validar la interfaz sin hardware; cambia USE_SIMULATOR=False para usar serial.
• Requisitos: PySide6, pyqtgraph, (opcional) pyserial para uso real, python>=3.9

pip install PySide6 pyqtgraph pyserial

Ejecución:
  python sdu5600_gui.py

Notas:
- Sustituye los comandos exactos por los reales del manual del SDU-5600.
- El driver simulado genera barridos tipo RF para pruebas.
- Exporta CSV/PNG desde el menú Archivo.
"""
from __future__ import annotations
import sys
import math
import time
import csv
import random
from dataclasses import dataclass
from typing import Optional, List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

USE_SIMULATOR = True
try:
    if not USE_SIMULATOR:
        import serial
        from serial.tools import list_ports
except Exception:
    USE_SIMULATOR = True

# ------------------------------
# Utilidades de dominio
# ------------------------------

def mhz_to_hz(mhz: float) -> int:
    return int(mhz * 1_000_000)

def hz_to_mhz(hz: int) -> float:
    return hz / 1_000_000.0

SPAN_PRESETS_HZ = [
    10_000, 25_000, 50_000, 100_000, 200_000,
    500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000
]

# ------------------------------
# Driver (abstracción)
# ------------------------------

class SDUDriverBase(QtCore.QObject):
    """Interfaz base para control del SDU-5600."""
    connectedChanged = QtCore.Signal(bool)
    sweepReady = QtCore.Signal(list)  # lista de (freq_Hz, power_dB)
    idChanged = QtCore.Signal(str)

    def connect_device(self, port: str, baud: int) -> None:
        raise NotImplementedError

    def disconnect_device(self) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    def set_center_span(self, center_hz: int, span_hz: int) -> None:
        raise NotImplementedError

    def request_sweep(self) -> None:
        """Solicitar un barrido (async); emitirá sweepReady cuando esté listo."""
        raise NotImplementedError

    def get_device_id(self) -> str:
        raise NotImplementedError


class SDUDriverSimulator(SDUDriverBase):
    def __init__(self):
        super().__init__()
        self._connected = False
        self.center = mhz_to_hz(145.0)
        self.span = 1_000_000
        self._device_id = "SDU5600-SIM v0.1"
        self.timer = QtCore.QTimer()
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._emit_sweep)

    def connect_device(self, port: str, baud: int) -> None:
        time.sleep(0.1)
        self._connected = True
        self.connectedChanged.emit(True)
        self.idChanged.emit(self._device_id)
        # El simulador no arranca hasta que el usuario pulse "Iniciar barrido".
        self.timer.stop()

    def disconnect_device(self) -> None:
        self.timer.stop()
        self._connected = False
        self.connectedChanged.emit(False)

    def is_connected(self) -> bool:
        return self._connected

    def set_center_span(self, center_hz: int, span_hz: int) -> None:
        self.center = center_hz
        self.span = max(10_000, span_hz)

    def request_sweep(self) -> None:
        # Simulador: emitimos en temporizador periódico
        pass

    def _emit_sweep(self):
        if not self._connected:
            return
        pts = 1024
        start = self.center - self.span // 2
        stop = self.center + self.span // 2
        df = (stop - start) / (pts - 1)
        data = []
        # Señales simuladas: ruido de -90 dB y dos picos positivos
        for i in range(pts):
            f = int(start + i * df)
            base_noise = -90 + (random.random() * 2 - 1)  # -91 a -89 dB aprox
            sig1 = 35 * math.exp(-((f - (self.center - self.span*0.15))**2) / (2*(self.span*0.02)**2))
            sig2 = 25 * math.exp(-((f - (self.center + self.span*0.18))**2) / (2*(self.span*0.01)**2))
            p = base_noise + sig1 + sig2
            data.append((f, p))
        self.sweepReady.emit(data)

    def get_device_id(self) -> str:
        return self._device_id


class SDUDriverSerial(SDUDriverBase):
    def __init__(self):
        super().__init__()
        self._ser: Optional['serial.Serial'] = None
        self._device_id = ""
        self.center = mhz_to_hz(145.0)
        self.span = 1_000_000
        self.worker = QtCore.QThread()
        self.moveToThread(self.worker)
        self.worker.start()

    def connect_device(self, port: str, baud: int) -> None:
        try:
            self._ser = serial.Serial(port, baudrate=baud, bytesize=8, parity='N', stopbits=1, timeout=1)
            self._device_id = self._send_recv("IDEN") or "SDU5600?"
            self.connectedChanged.emit(True)
            self.idChanged.emit(self._device_id)
        except Exception as e:
            self._ser = None
            QtWidgets.QMessageBox.critical(None, "Conexión", f"No se pudo conectar: {e}")
            self.connectedChanged.emit(False)

    def disconnect_device(self) -> None:
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        finally:
            self._ser = None
            self.connectedChanged.emit(False)

    def is_connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def _send(self, cmd: str) -> None:
        if not self.is_connected():
            return
        payload = (cmd + "\r\n").encode("ascii")
        self._ser.write(payload)

    def _recv_line(self) -> str:
        if not self.is_connected():
            return ""
        data = self._ser.read_until(b"\r\n")
        try:
            return data.decode("ascii").strip()
        except Exception:
            return ""

    def _send_recv(self, cmd: str) -> str:
        self._send(cmd)
        return self._recv_line()

    def set_center_span(self, center_hz: int, span_hz: int) -> None:
        self.center, self.span = center_hz, max(10_000, span_hz)
        # TODO: Reemplazar por comandos reales del SDU-5600
        self._send(f"FREQ {self.center}")
        self._send(f"SPAN {self.span}")

    def request_sweep(self) -> None:
        # TODO: Implementar solicitud de barrido real y parseo
        # Ejemplo genérico:
        # self._send("SCAN START")
        # raw = self._send_recv("READ SWEEP")
        # data = parsear_sw(raw)
        # self.sweepReady.emit(data)
        pass

    def get_device_id(self) -> str:
        return self._device_id or "SDU5600"

# ------------------------------
# Widgets de UI
# ------------------------------

class ConnectBar(QtWidgets.QWidget):
    connectRequested = QtCore.Signal(str, int)
    disconnectRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.portBox = QtWidgets.QComboBox()
        self.baudBox = QtWidgets.QComboBox()
        self.baudBox.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.btn = QtWidgets.QPushButton("Conectar")
        self.status = QtWidgets.QLabel("Desconectado")
        self.status.setStyleSheet("color:#64748b;")

        lay.addWidget(QtWidgets.QLabel("Puerto:"))
        lay.addWidget(self.portBox)
        lay.addSpacing(12)
        lay.addWidget(QtWidgets.QLabel("Baudios:"))
        lay.addWidget(self.baudBox)
        lay.addStretch(1)
        lay.addWidget(self.status)
        lay.addWidget(self.btn)

        self.btn.clicked.connect(self._toggle)
        self.reload_ports()

    def reload_ports(self):
        self.portBox.clear()
        if USE_SIMULATOR:
            self.portBox.addItems(["SIMULADOR"]) 
        else:
            ports = [p.device for p in list_ports.comports()]  # type: ignore
            self.portBox.addItems(ports or ["(sin puertos)"])

    def _toggle(self):
        if self.btn.text() == "Conectar":
            port = self.portBox.currentText()
            baud = int(self.baudBox.currentText())
            self.connectRequested.emit(port, baud)
        else:
            self.disconnectRequested.emit()

    def set_connected(self, ok: bool, dev_id: str = ""):
        if ok:
            self.btn.setText("Desconectar")
            self.status.setText(f"Conectado · {dev_id}")
            self.status.setStyleSheet("color:#059669;")
        else:
            self.btn.setText("Conectar")
            self.status.setText("Desconectado")
            self.status.setStyleSheet("color:#64748b;")


class NumberField(QtWidgets.QWidget):
    changed = QtCore.Signal(float)

    def __init__(self, label: str, unit: str, value: float, step: float, decimals: int = 3, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel(label)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(0.001, 6000.0)
        self.spin.setValue(value)
        self.spin.setSingleStep(step)
        self.unit = QtWidgets.QLabel(unit)
        lay.addWidget(self.label)
        lay.addWidget(self.spin)
        lay.addWidget(self.unit)
        self.spin.valueChanged.connect(self.changed)

    def value(self) -> float:
        return float(self.spin.value())

    def setValue(self, v: float):
        self.spin.setValue(v)


class SpanChips(QtWidgets.QWidget):
    spanSelected = QtCore.Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        flow = QtWidgets.QHBoxLayout(self)
        flow.setContentsMargins(0, 0, 0, 0)
        for hz in SPAN_PRESETS_HZ:
            btn = QtWidgets.QPushButton(_fmt_span(hz))
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, v=hz: self._emit(v))
            flow.addWidget(btn)
        flow.addStretch(1)
    def _emit(self, hz: int):
        self.spanSelected.emit(hz)


def _fmt_span(hz: int) -> str:
    if hz >= 1_000_000:
        return f"{hz/1_000_000:.0f} MHz"
    elif hz >= 1_000:
        return f"{hz/1_000:.0f} kHz"
    return f"{hz} Hz"


class MarkersModel(QtCore.QAbstractTableModel):
    headers = ["#", "Frecuencia (MHz)", "Nivel (dB)"]
    def __init__(self):
        super().__init__()
        self.rows: List[Tuple[float, float]] = []
    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self.rows)
    def columnCount(self, parent=QtCore.QModelIndex()):
        return 3
    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)
    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if role == QtCore.Qt.DisplayRole:
            if c == 0:
                return r + 1
            elif c == 1:
                return f"{self.rows[r][0]:.6f}"
            elif c == 2:
                return f"{self.rows[r][1]:.1f}"
        return None
    def add_marker(self, f_mhz: float, p_db: float):
        self.beginInsertRows(QtCore.QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append((f_mhz, p_db))
        self.endInsertRows()
    def clear(self):
        self.beginResetModel()
        self.rows.clear()
        self.endResetModel()


# ------------------------------
# Ventana principal
# ------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AOR SDU-5600 — Console")
        self.resize(1100, 720)
        self._create_actions()
        self._create_menu()

        # Driver
        self.driver: SDUDriverBase
        if USE_SIMULATOR:
            self.driver = SDUDriverSimulator()
        else:
            self.driver = SDUDriverSerial()

        # Top toolbar
        top = QtWidgets.QWidget()
        top_l = QtWidgets.QVBoxLayout(top)
        top_l.setContentsMargins(12, 12, 12, 6)

        self.connectBar = ConnectBar()
        top_l.addWidget(self.connectBar)

        # Controls row
        ctr = QtWidgets.QWidget()
        ctr_l = QtWidgets.QHBoxLayout(ctr)
        ctr_l.setContentsMargins(0, 8, 0, 0)

        self.centerField = NumberField("Centro:", "MHz", 145.000, 0.005, decimals=3)
        self.spanField = NumberField("Span:", "MHz", 1.000, 0.1, decimals=3)
        self.btnApply = QtWidgets.QPushButton("Aplicar")
        self.btnStart = QtWidgets.QPushButton("Iniciar barrido")
        self.btnStop = QtWidgets.QPushButton("Detener")
        self.chkPeak = QtWidgets.QCheckBox("Peak hold")
        self.btnAutoscale = QtWidgets.QPushButton("Autoescala")

        ctr_l.addWidget(self.centerField)
        ctr_l.addSpacing(8)
        ctr_l.addWidget(self.spanField)
        ctr_l.addSpacing(8)
        ctr_l.addWidget(self.btnApply)
        ctr_l.addSpacing(16)
        ctr_l.addWidget(self.btnStart)
        ctr_l.addWidget(self.btnStop)
        ctr_l.addSpacing(16)
        ctr_l.addWidget(self.chkPeak)
        ctr_l.addWidget(self.btnAutoscale)
        ctr_l.addStretch(1)

        top_l.addWidget(ctr)

        # Span chips
        chips = SpanChips()
        chips.setStyleSheet("QPushButton{padding:6px 10px;border:1px solid #cbd5e1;border-radius:8px;} QPushButton:checked{background:#eef2ff;border-color:#6366f1}")
        chips.spanSelected.connect(self._on_span_chip)
        top_l.addWidget(chips)

        # Central split: plot + markers
        split = QtWidgets.QSplitter()
        split.setOrientation(QtCore.Qt.Horizontal)

        # Plot area
        plotw = QtWidgets.QWidget()
        plot_l = QtWidgets.QVBoxLayout(plotw)
        plot_l.setContentsMargins(12, 6, 12, 12)
        self.plot = pg.PlotWidget()
        self.plot.setLabel('bottom', 'Frecuencia', units='Hz')
        self.plot.setLabel('left', 'Nivel', units='dB')
        self.curve = self.plot.plot([], [])
        self.peakCurve = self.plot.plot([], [], pen=pg.mkPen(style=QtCore.Qt.DashLine))
        plot_l.addWidget(self.plot)

        split.addWidget(plotw)

        # Markers panel
        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)
        right_l.setContentsMargins(12, 6, 12, 12)
        lab = QtWidgets.QLabel("Marcadores")
        lab.setStyleSheet("font-weight:600;")
        self.tblMarkers = QtWidgets.QTableView()
        self.modelMarkers = MarkersModel()
        self.tblMarkers.setModel(self.modelMarkers)
        self.tblMarkers.horizontalHeader().setStretchLastSection(True)
        self.tblMarkers.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tblMarkers.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        btns = QtWidgets.QHBoxLayout()
        self.btnMark = QtWidgets.QPushButton("Añadir marcador @ pico")
        self.btnClearMarks = QtWidgets.QPushButton("Limpiar")
        btns.addWidget(self.btnMark)
        btns.addWidget(self.btnClearMarks)
        btns.addStretch(1)

        right_l.addWidget(lab)
        right_l.addWidget(self.tblMarkers)
        right_l.addLayout(btns)

        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        # Status bar
        self.statusBar = QtWidgets.QStatusBar()
        self.setStatusBar(self.statusBar)
        self.lblInfo = QtWidgets.QLabel("Listo")
        self.statusBar.addPermanentWidget(self.lblInfo)

        # Central widget
        central = QtWidgets.QWidget()
        central_l = QtWidgets.QVBoxLayout(central)
        central_l.setContentsMargins(0,0,0,0)
        central_l.addWidget(top)
        central_l.addWidget(split)
        self.setCentralWidget(central)

        # Estado
        self.connected = False
        self.peak_buf: Optional[List[Tuple[int, float]]] = None
        self.last_sweep: List[Tuple[int, float]] = []

        # Wiring
        self.connectBar.connectRequested.connect(self._do_connect)
        self.connectBar.disconnectRequested.connect(self._do_disconnect)
        self.btnApply.clicked.connect(self._apply_center_span)
        self.btnStart.clicked.connect(lambda: self._set_sweep(True))
        self.btnStop.clicked.connect(lambda: self._set_sweep(False))
        self.btnAutoscale.clicked.connect(self._autoscale)
        self.btnMark.clicked.connect(self._mark_peak)
        self.btnClearMarks.clicked.connect(self.modelMarkers.clear)

        self.driver.connectedChanged.connect(self._on_connected)
        self.driver.sweepReady.connect(self._on_sweep)
        self.driver.idChanged.connect(lambda s: self.connectBar.set_connected(self.connected, s))

        # Shortcuts
        QtGui.QShortcut(QtGui.QKeySequence("Space"), self, activated=lambda: self._toggle_sweep())
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+E"), self, activated=self._export_csv)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+P"), self, activated=self._export_png)

    # ----- Menús -----
    def _create_actions(self):
        self.actExportCSV = QtGui.QAction("Exportar CSV", self)
        self.actExportCSV.triggered.connect(self._export_csv)
        self.actExportPNG = QtGui.QAction("Exportar PNG", self)
        self.actExportPNG.triggered.connect(self._export_png)
        self.actQuit = QtGui.QAction("Salir", self)
        self.actQuit.triggered.connect(self.close)
        self.actAbout = QtGui.QAction("Acerca de", self)
        self.actAbout.triggered.connect(self._about)

    def _create_menu(self):
        mfile = self.menuBar().addMenu("Archivo")
        mfile.addAction(self.actExportCSV)
        mfile.addAction(self.actExportPNG)
        mfile.addSeparator()
        mfile.addAction(self.actQuit)
        mhelp = self.menuBar().addMenu("Ayuda")
        mhelp.addAction(self.actAbout)

    # ----- Slots/UI -----
    def _do_connect(self, port: str, baud: int):
        self.driver.connect_device(port, baud)

    def _do_disconnect(self):
        self.driver.disconnect_device()

    def _on_connected(self, ok: bool):
        self.connected = ok
        dev_id = self.driver.get_device_id() if ok else ""
        self.connectBar.set_connected(ok, dev_id)
        self.lblInfo.setText("Conectado" if ok else "Desconectado")

    def _apply_center_span(self):
        c_mhz = self.centerField.value()
        s_mhz = self.spanField.value()
        self.driver.set_center_span(mhz_to_hz(c_mhz), mhz_to_hz(s_mhz))
        self.lblInfo.setText(f"Centro {c_mhz:.3f} MHz · Span {s_mhz:.3f} MHz")

    def _set_sweep(self, start: bool):
        if start:
            self.lblInfo.setText("Barrido en curso… (Space para pausar)")
            # En simulador: arrancar/parar timer explícitamente
            if USE_SIMULATOR and hasattr(self.driver, 'timer'):
                self.driver.timer.start()
            # En real: pedir barridos periódicos
            if not USE_SIMULATOR:
                self._start_poll_timer()
        else:
            self.lblInfo.setText("Barrido detenido")
            if USE_SIMULATOR and hasattr(self.driver, 'timer'):
                self.driver.timer.stop()
            if not USE_SIMULATOR:
                self._stop_poll_timer()
            if not USE_SIMULATOR:
                self._stop_poll_timer()

    def _toggle_sweep(self):
        self._set_sweep("en curso" not in self.lblInfo.text().lower())

    def _on_sweep(self, data: List[Tuple[int, float]]):
        first = not self.last_sweep
        self.last_sweep = data
        xs = [f for f, _ in data]
        ys = [p for _, p in data]
        self.curve.setData(xs, ys)
        if self.chkPeak.isChecked():
            if self.peak_buf is None:
                self.peak_buf = data.copy()
            else:
                self.peak_buf = [(f, max(p, self.peak_buf[i][1])) for i, (f, p) in enumerate(data)]
            self.peakCurve.setData([f for f, _ in self.peak_buf], [p for _, p in self.peak_buf])
        else:
            self.peak_buf = None
            self.peakCurve.setData([], [])
        # Autoescala automática al primer barrido para que siempre se vea algo
        if first:
            self._autoscale()

    def _autoscale(self):
        self.plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)

    def _mark_peak(self):
        if not self.last_sweep:
            return
        # índice del máximo
        idx = max(range(len(self.last_sweep)), key=lambda i: self.last_sweep[i][1])
        f_hz, p_db = self.last_sweep[idx]
        self.modelMarkers.add_marker(hz_to_mhz(f_hz), p_db)
        # marcador visual
        inf = pg.InfiniteLine(pos=f_hz, angle=90, movable=False, pen=pg.mkPen('#6366f1'))
        self.plot.addItem(inf)

    def _export_csv(self):
        if not self.last_sweep:
            QtWidgets.QMessageBox.information(self, "Exportar CSV", "No hay datos de barrido aún.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar CSV", "sdu_sweep.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["freq_hz", "level_db"])
            for fz, p in self.last_sweep:
                w.writerow([fz, f"{p:.2f}"])
        self.statusBar.showMessage(f"CSV guardado en {path}", 4000)

    def _export_png(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar PNG", "sdu_plot.png", "PNG (*.png)")
        if not path:
            return
        exporter = pg.exporters.ImageExporter(self.plot.plotItem)
        exporter.export(path)
        self.statusBar.showMessage(f"PNG guardado en {path}", 4000)

    def _start_poll_timer(self):
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self.driver.request_sweep)
        self._poll.start()

    def _stop_poll_timer(self):
        if hasattr(self, "_poll"):
            self._poll.stop()

    def _on_span_chip(self, hz: int):
        self.spanField.setValue(hz_to_mhz(hz))
        self._apply_center_span()

    def _about(self):
        QtWidgets.QMessageBox.information(self, "Acerca de", "SDU-5600 GUI — v0.1\nInterfaz de prueba con simulador.\n© RadioDigital")


def main():
    app = QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    # registrar exportador (evita import tardío)
    import pyqtgraph.exporters  # noqa: F401
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
