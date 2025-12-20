# ui/band_bar.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt


class BandBar(QWidget):
    def __init__(self, text: str = "—", parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)

        self.lbl = QLabel(text)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("color:#e5e7eb; font-weight:900; letter-spacing:0.6px;")

        lay.addWidget(self.lbl, 1)

        self.setStyleSheet("""
            BandBar {
                background: #0f6b2f;
                border-top: 1px solid rgba(0,0,0,0.55);
                border-bottom: 1px solid rgba(0,0,0,0.75);
            }
        """)

    def set_text(self, text: str):
        self.lbl.setText(text or "—")
