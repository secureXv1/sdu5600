# drivers/base.py
from abc import ABC, abstractmethod

class RadioDriver(ABC):
    def __init__(self, config):
        self.config = config
        self.connected = False

    @abstractmethod
    def connect(self): ...
    
    @abstractmethod
    def disconnect(self): ...
    
    @abstractmethod
    def set_frequency(self, hz: float): ...
    
    @abstractmethod
    def set_mode(self, mode: str): ...
    
    @abstractmethod
    def get_smeter(self) -> float:
        """Devuelve nivel de señal en dB o S-units."""
    
    @abstractmethod
    def get_spectrum(self):
        """Devuelve numpy array con FFT o niveles de espectro."""
    
    def start_scan(self, band_cfg):
        """Opcional: algunos drivers pueden tener scan nativo."""
