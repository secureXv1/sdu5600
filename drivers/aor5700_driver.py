# drivers/aor5700_driver.py
from drivers.base import RadioDriver

class Aor5700Driver(RadioDriver):
    """
    Stub del driver del AOR 5700.
    Lo completaremos cuando integremos el AR-DV / protocolo AOR.
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
