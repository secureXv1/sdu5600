# ui/radio_card.py
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QComboBox, QSlider
)
from PySide6.QtCore import Qt

class RadioCard(QWidget):
    def __init__(self, name, driver):
        super().__init__()
        self.driver = driver

        layout = QVBoxLayout(self)
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.lbl_name)

        self.lbl_freq = QLabel("000.000.000 MHz")
        self.lbl_freq.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(self.lbl_freq)

        # banda
        band_layout = QHBoxLayout()
        self.btn_hf = QPushButton("HF")
        self.btn_vhf = QPushButton("VHF")
        self.btn_uhf = QPushButton("UHF")
        band_layout.addWidget(self.btn_hf)
        band_layout.addWidget(self.btn_vhf)
        band_layout.addWidget(self.btn_uhf)
        layout.addLayout(band_layout)

        # modo
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo:"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["AM", "FM", "NFM", "USB", "LSB"])
        mode_layout.addWidget(self.cmb_mode)
        layout.addLayout(mode_layout)

        # squelch
        sq_layout = QHBoxLayout()
        sq_layout.addWidget(QLabel("SQL:"))
        self.sld_sql = QSlider(Qt.Horizontal)
        sq_layout.addWidget(self.sld_sql)
        layout.addLayout(sq_layout)

        # botones scan
        btn_layout = QHBoxLayout()
        self.btn_scan = QPushButton("SCAN")
        self.btn_hold = QPushButton("HOLD")
        self.btn_rec  = QPushButton("REC")
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_hold)
        btn_layout.addWidget(self.btn_rec)
        layout.addLayout(btn_layout)

        # TODO: conectar señales / slots a driver y scan_engine
