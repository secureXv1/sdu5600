# core/dsp/nbfm.py
from __future__ import annotations
import numpy as np
from core.dsp.wbfm import WBFMStream, fir_lowpass


class NBFMStream(WBFMStream):
    """
    NFM para VHF/UHF (canal angosto).
    """
    def __init__(self, iq_bytes_queue, freq_mhz: float):
        super().__init__(iq_bytes_queue=iq_bytes_queue, freq_mhz=freq_mhz)

        # NFM: canal más angosto
        self.chan_fir = fir_lowpass(129, 20_000.0, self.fs)
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        # voz
        self.aud_fir = fir_lowpass(129, 4_000.0, self.fs1)
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        # de-emphasis típico NFM
        self.tau = 300e-6
