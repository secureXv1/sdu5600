from typing import Optional
from core.dsp.wbfm import WBFMStream
from core.dsp.nbfm import NBFMStream

class AudioEngine:
    def __init__(self):
        self.stream = None

    def start(self, freq_mhz: float, mode: str):
        self.stop()

        if mode == "FM":
            self.stream = WBFMStream(freq_mhz)
        elif mode == "NFM":
            self.stream = NBFMStream(freq_mhz)
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
