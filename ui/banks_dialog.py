# ui/banks_dialog.py
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QTableWidget,
    QTableWidgetItem, QPushButton, QMessageBox, QAbstractItemView,
    QLineEdit, QFormLayout, QDoubleSpinBox, QComboBox, QCheckBox, QSpinBox,
    QDialogButtonBox, QHeaderView, QTextEdit, QLabel
)

ALLOWED_MODES = ["FM", "NFM", "AM", "LSB", "USB", "WFM"]


# -------------------------
# Editors
# -------------------------

class RangeBankEditor(QDialog):
    def __init__(self, parent=None, bank: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Banco de Rangos (ICR-30 style)")
        self.setModal(True)
        self.bank_in = bank or {}
        self.bank_out = None

        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit((self.bank_in.get("name") or "").strip())

        self.start_mhz = QDoubleSpinBox(); self.start_mhz.setDecimals(6)
        self.start_mhz.setRange(0.001, 6000.0); self.start_mhz.setSingleStep(0.005)

        self.stop_mhz = QDoubleSpinBox(); self.stop_mhz.setDecimals(6)
        self.stop_mhz.setRange(0.001, 6000.0); self.stop_mhz.setSingleStep(0.005)

        r = self.bank_in.get("range") or {}
        self.start_mhz.setValue(float(r.get("start_mhz", 118.000)))
        self.stop_mhz.setValue(float(r.get("stop_mhz", 136.975)))

        self.ts_khz = QDoubleSpinBox(); self.ts_khz.setDecimals(3)
        self.ts_khz.setRange(0.001, 5000.0); self.ts_khz.setSingleStep(0.5)
        self.ts_khz.setValue(float(r.get("ts_khz", 25.0)))

        self.mode = QComboBox(); self.mode.addItems(ALLOWED_MODES)
        m = (self.bank_in.get("mode") or "AM").upper().strip()
        if m in ALLOWED_MODES:
            self.mode.setCurrentText(m)

        # RF Gain (HackRF) -> ICR-30 feel
        rg = self.bank_in.get("rf_gain") or {}
        self.lna = QSpinBox(); self.lna.setRange(0, 40); self.lna.setSingleStep(8)
        self.vga = QSpinBox(); self.vga.setRange(0, 62); self.vga.setSingleStep(2)
        self.amp = QCheckBox("AMP (RF Amplifier)")

        self.lna.setValue(int(rg.get("lna_db", 32)))
        self.vga.setValue(int(rg.get("vga_db", 20)))
        self.amp.setChecked(bool(rg.get("amp", False)))

        self.active = QCheckBox("Activo")
        self.active.setChecked(bool(self.bank_in.get("active", False)))

        form.addRow("Nombre", self.name)
        form.addRow("Frecuencia inicio (MHz)", self.start_mhz)
        form.addRow("Frecuencia fin (MHz)", self.stop_mhz)
        form.addRow("TS (kHz)", self.ts_khz)
        form.addRow("MODE", self.mode)

        rg_row = QWidget()
        rg_lay = QHBoxLayout(rg_row); rg_lay.setContentsMargins(0, 0, 0, 0)
        rg_lay.addWidget(QLabel("LNA"))
        rg_lay.addWidget(self.lna)
        rg_lay.addSpacing(10)
        rg_lay.addWidget(QLabel("VGA"))
        rg_lay.addWidget(self.vga)
        rg_lay.addSpacing(10)
        rg_lay.addWidget(self.amp)
        form.addRow("RF GAIN", rg_row)

        form.addRow("", self.active)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _save(self):
        name = (self.name.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Validación", "Debes ingresar un nombre.")
            return

        start = float(self.start_mhz.value())
        stop = float(self.stop_mhz.value())
        if stop <= start:
            QMessageBox.warning(self, "Validación", "Frecuencia fin debe ser mayor que inicio.")
            return

        ts = float(self.ts_khz.value())
        if ts <= 0:
            QMessageBox.warning(self, "Validación", "TS debe ser mayor que 0.")
            return

        out = dict(self.bank_in)  # conserva id si existe
        out["name"] = name
        out["active"] = bool(self.active.isChecked())
        out["mode"] = self.mode.currentText().strip().upper()
        out["range"] = {"start_mhz": start, "stop_mhz": stop, "ts_khz": ts}
        out["rf_gain"] = {"lna_db": int(self.lna.value()), "vga_db": int(self.vga.value()), "amp": bool(self.amp.isChecked())}
        self.bank_out = out
        self.accept()


class FreqBankEditor(QDialog):
    """
    Banco de frecuencias: nombre + lista (freq, mode).
    Para que sea simple y rápido, usamos un textarea donde cada línea es:
      91.4 FM
      99.5 WFM
    """
    def __init__(self, parent=None, bank: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Banco de Frecuencias")
        self.setModal(True)
        self.bank_in = bank or {}
        self.bank_out = None

        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit((self.bank_in.get("name") or "").strip())
        self.active = QCheckBox("Activo")
        self.active.setChecked(bool(self.bank_in.get("active", False)))

        self.lines = QTextEdit()
        self.lines.setPlaceholderText("Una por línea: 91.4 FM  |  170.3125 NFM  |  99.5 WFM")

        # cargar items existentes
        items = self.bank_in.get("items") or self.bank_in.get("channels") or []
        text_lines = []
        for it in items:
            f = it.get("freq_mhz")
            m = (it.get("mode") or "FM").upper().strip()
            if f is not None:
                text_lines.append(f"{float(f)} {m}")
        self.lines.setPlainText("\n".join(text_lines))

        form.addRow("Nombre", self.name)
        form.addRow("", self.active)
        lay.addLayout(form)

        lay.addWidget(QLabel("Frecuencias (una por línea: <MHz> <MODE>)"))
        lay.addWidget(self.lines)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _save(self):
        name = (self.name.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Validación", "Debes ingresar un nombre.")
            return

        raw = self.lines.toPlainText().strip()
        items = []
        if raw:
            for ln in raw.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                parts = ln.replace(",", ".").split()
                if len(parts) < 1:
                    continue
                try:
                    f = float(parts[0])
                except Exception:
                    QMessageBox.warning(self, "Validación", f"Línea inválida: {ln}")
                    return
                m = (parts[1] if len(parts) >= 2 else "FM").upper().strip()
                if m not in ALLOWED_MODES:
                    QMessageBox.warning(self, "Validación", f"MODE inválido en línea: {ln}")
                    return
                items.append({"freq_mhz": f, "mode": m})

        if not items:
            QMessageBox.warning(self, "Validación", "Debes ingresar al menos 1 frecuencia.")
            return

        out = dict(self.bank_in)  # conserva id si existe
        out["name"] = name
        out["active"] = bool(self.active.isChecked())
        out["items"] = items
        self.bank_out = out
        self.accept()


# -------------------------
# Main CRUD Dialog
# -------------------------

class BanksDialog(QDialog):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.setWindowTitle("Bancos / Memorias")
        self.setMinimumSize(820, 480)
        self.setModal(True)

        self.store = store

        root = QVBoxLayout(self)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # --- Tab Frecuencias ---
        tab_freq = QWidget()
        self.tabs.addTab(tab_freq, "Bancos de Frecuencias")
        v1 = QVBoxLayout(tab_freq)

        self.tbl_freq = QTableWidget(0, 4)
        self.tbl_freq.setHorizontalHeaderLabels(["Activo", "Nombre", "#", "Modos"])
        self.tbl_freq.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_freq.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_freq.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        v1.addWidget(self.tbl_freq)

        b1 = QHBoxLayout()
        self.btn_f_new = QPushButton("Crear")
        self.btn_f_edit = QPushButton("Editar")
        self.btn_f_del = QPushButton("Eliminar")
        b1.addWidget(self.btn_f_new); b1.addWidget(self.btn_f_edit); b1.addWidget(self.btn_f_del)
        b1.addStretch(1)
        v1.addLayout(b1)

        # --- Tab Rangos ---
        tab_range = QWidget()
        self.tabs.addTab(tab_range, "Bancos de Rangos")
        v2 = QVBoxLayout(tab_range)

        self.tbl_range = QTableWidget(0, 6)
        self.tbl_range.setHorizontalHeaderLabels(["Activo", "Nombre", "Inicio", "Fin", "TS(kHz)", "MODE"])
        self.tbl_range.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_range.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_range.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        v2.addWidget(self.tbl_range)

        b2 = QHBoxLayout()
        self.btn_r_new = QPushButton("Crear")
        self.btn_r_edit = QPushButton("Editar")
        self.btn_r_del = QPushButton("Eliminar")
        b2.addWidget(self.btn_r_new); b2.addWidget(self.btn_r_edit); b2.addWidget(self.btn_r_del)
        b2.addStretch(1)
        v2.addLayout(b2)

        # actions
        self.btn_f_new.clicked.connect(self._new_freq)
        self.btn_f_edit.clicked.connect(self._edit_freq)
        self.btn_f_del.clicked.connect(self._del_freq)

        self.btn_r_new.clicked.connect(self._new_range)
        self.btn_r_edit.clicked.connect(self._edit_range)
        self.btn_r_del.clicked.connect(self._del_range)

        self.tbl_freq.cellClicked.connect(self._freq_cell_clicked)
        self.tbl_range.cellClicked.connect(self._range_cell_clicked)

        # close
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        root.addWidget(close)

        self.reload()

    # ----- Reload tables -----
    def reload(self):
        self._load_freq()
        self._load_range()

    def _load_freq(self):
        banks = self.store.list_banks("freq")
        self.tbl_freq.setRowCount(0)
        for b in banks:
            row = self.tbl_freq.rowCount()
            self.tbl_freq.insertRow(row)

            active = QTableWidgetItem("✓" if b.get("active") else "")
            active.setTextAlignment(Qt.AlignCenter)
            self.tbl_freq.setItem(row, 0, active)

            name = QTableWidgetItem(b.get("name", ""))
            self.tbl_freq.setItem(row, 1, name)

            items = b.get("items") or []
            self.tbl_freq.setItem(row, 2, QTableWidgetItem(str(len(items))))

            modes = sorted({(it.get("mode") or "").upper().strip() for it in items if it.get("mode")})
            self.tbl_freq.setItem(row, 3, QTableWidgetItem(", ".join(modes)))

            # guarda id en UserRole
            name.setData(Qt.UserRole, b.get("id"))

    def _load_range(self):
        banks = self.store.list_banks("range")
        self.tbl_range.setRowCount(0)
        for b in banks:
            row = self.tbl_range.rowCount()
            self.tbl_range.insertRow(row)

            active = QTableWidgetItem("✓" if b.get("active") else "")
            active.setTextAlignment(Qt.AlignCenter)
            self.tbl_range.setItem(row, 0, active)

            name = QTableWidgetItem(b.get("name", ""))
            self.tbl_range.setItem(row, 1, name)

            r = b.get("range") or {}
            self.tbl_range.setItem(row, 2, QTableWidgetItem(str(r.get("start_mhz", ""))))
            self.tbl_range.setItem(row, 3, QTableWidgetItem(str(r.get("stop_mhz", ""))))
            self.tbl_range.setItem(row, 4, QTableWidgetItem(str(r.get("ts_khz", ""))))
            self.tbl_range.setItem(row, 5, QTableWidgetItem((b.get("mode") or "").upper()))

            name.setData(Qt.UserRole, b.get("id"))

    # ----- Helpers -----
    def _selected_id(self, tbl: QTableWidget) -> str | None:
        r = tbl.currentRow()
        if r < 0:
            return None
        item = tbl.item(r, 1)  # columna Nombre
        if not item:
            return None
        return item.data(Qt.UserRole)

    def _get_bank(self, kind: str, bank_id: str) -> dict | None:
        for b in self.store.list_banks(kind):
            if b.get("id") == bank_id:
                return b
        return None

    # ----- Click to toggle Active -----
    def _freq_cell_clicked(self, row: int, col: int):
        if col != 0:
            return
        bank_id = self.tbl_freq.item(row, 1).data(Qt.UserRole)
        b = self._get_bank("freq", bank_id)
        if not b:
            return
        new_active = not bool(b.get("active"))
        self.store.set_active("freq", bank_id, new_active)
        self.reload()

    def _range_cell_clicked(self, row: int, col: int):
        if col != 0:
            return
        bank_id = self.tbl_range.item(row, 1).data(Qt.UserRole)
        b = self._get_bank("range", bank_id)
        if not b:
            return
        new_active = not bool(b.get("active"))
        self.store.set_active("range", bank_id, new_active)
        self.reload()

    # ----- CRUD freq -----
    def _new_freq(self):
        try:
            if len(self.store.list_banks("freq")) >= 20:
                QMessageBox.warning(self, "Límite", "Máximo 20 bancos de frecuencias.")
                return
            dlg = FreqBankEditor(self)
            if dlg.exec() == QDialog.Accepted and dlg.bank_out:
                self.store.upsert_bank("freq", dlg.bank_out)
                self.reload()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _edit_freq(self):
        bank_id = self._selected_id(self.tbl_freq)
        if not bank_id:
            QMessageBox.information(self, "Editar", "Selecciona un banco.")
            return
        b = self._get_bank("freq", bank_id)
        if not b:
            return
        try:
            dlg = FreqBankEditor(self, bank=b)
            if dlg.exec() == QDialog.Accepted and dlg.bank_out:
                self.store.upsert_bank("freq", dlg.bank_out)
                self.reload()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _del_freq(self):
        bank_id = self._selected_id(self.tbl_freq)
        if not bank_id:
            QMessageBox.information(self, "Eliminar", "Selecciona un banco.")
            return
        if QMessageBox.question(self, "Confirmar", "¿Eliminar este banco?") != QMessageBox.Yes:
            return
        self.store.delete_bank("freq", bank_id)
        self.reload()

    # ----- CRUD range -----
    def _new_range(self):
        try:
            if len(self.store.list_banks("range")) >= 20:
                QMessageBox.warning(self, "Límite", "Máximo 20 bancos de rangos.")
                return
            dlg = RangeBankEditor(self)
            if dlg.exec() == QDialog.Accepted and dlg.bank_out:
                self.store.upsert_bank("range", dlg.bank_out)
                self.reload()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _edit_range(self):
        bank_id = self._selected_id(self.tbl_range)
        if not bank_id:
            QMessageBox.information(self, "Editar", "Selecciona un banco.")
            return
        b = self._get_bank("range", bank_id)
        if not b:
            return
        try:
            dlg = RangeBankEditor(self, bank=b)
            if dlg.exec() == QDialog.Accepted and dlg.bank_out:
                self.store.upsert_bank("range", dlg.bank_out)
                self.reload()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _del_range(self):
        bank_id = self._selected_id(self.tbl_range)
        if not bank_id:
            QMessageBox.information(self, "Eliminar", "Selecciona un banco.")
            return
        if QMessageBox.question(self, "Confirmar", "¿Eliminar este banco?") != QMessageBox.Yes:
            return
        self.store.delete_bank("range", bank_id)
        self.reload()
