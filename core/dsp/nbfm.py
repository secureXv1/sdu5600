from __future__ import annotations
import numpy as np
from core.dsp.wbfm import WBFMStream, fir_lowpass


class NBFMStream(WBFMStream):
    """
    NFM para VHF/UHF (canal más angosto).
    """
    def __init__(self, freq_mhz: float):
        super().__init__(freq_mhz)

        # NFM: canal más angosto (aprox)
        # Ojo: esto es un buen baseline; luego lo afinamos con "deviation" y niveles.
        self.chan_fir = fir_lowpass(129, 20_000.0, self.fs)   # antes era 120k
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        self.aud_fir = fir_lowpass(129, 4_000.0, self.fs1)    # audio voz
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        # De-emphasis típico NFM (baseline)
        self.tau = 300e-6

        # Gains base
        self.lna = 24
        self.vga = 12
        self.amp = 0
