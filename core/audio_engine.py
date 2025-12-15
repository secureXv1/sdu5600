# core/audio_engine.py
from __future__ import annotations
from typing import Optional

from core.dsp.wbfm import WBFMStream
from core.dsp.nbfm import NBFMStream


class AudioEngine:
    """
    AudioEngine ahora NO abre HackRF.
    Consume IQ desde el driver (cola dedicada) para no pelear con el espectro.
    """
    def __init__(self):
        self.stream = None

    def start(self, driver, freq_mhz: float, mode: str):
        self.stop()

        if driver is None:
            raise ValueError("No hay driver activo")

        # Asegurar que el driver esté vivo y en esa frecuencia
        if hasattr(driver, "connect"):
            driver.connect()

        if hasattr(driver, "set_center_freq"):
            driver.set_center_freq(freq_mhz * 1e6)

        # Debe existir una cola de audio en el driver
        if not hasattr(driver, "get_audio_queue"):
            raise RuntimeError("El driver no expone get_audio_queue()")

        q = driver.get_audio_queue()

        mode = (mode or "").upper().strip()
        if mode == "FM":
            self.stream = WBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz)
        elif mode == "NFM":
            self.stream = NBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz)
        else:
            raise ValueError(f"Modo no soportado: {mode}")

        self.stream.start()

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
            except Exception:
                pass
            self.stream = None
