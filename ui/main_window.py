# ui/main_window.py
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QApplication
)
from core.radio_manager import RadioManager
from ui.radio_card import RadioCard
from ui.waterfall_widget import WaterfallWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Remote Radios Station")
        self.resize(1400, 800)

        self.manager = RadioManager()

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # fila superior: tarjetas de radios
        cards_layout = QHBoxLayout()
        self.cards = []

        for radio_cfg in self.manager.radios:
            card = RadioCard(radio_cfg["name"], radio_cfg["driver"])
            cards_layout.addWidget(card)
            self.cards.append(card)

        main_layout.addLayout(cards_layout)

        # abajo: waterfall + log (aquí solo waterfall de ejemplo)
        self.waterfall = WaterfallWidget()
        main_layout.addWidget(self.waterfall)
