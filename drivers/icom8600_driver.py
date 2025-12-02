# drivers/icom8600_driver.py
from drivers.base import RadioDriver

class Icom8600Driver(RadioDriver):
    """
    Stub del driver. Solo evita errores de importación.
    Lo completaremos más adelante.
    """
    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def set_frequency(self, hz: float):
        pass

    def set_mode(self, mode: str):
        pass

    def get_smeter(self):
        return -120.0

    def get_spectrum(self):
        return [], []
