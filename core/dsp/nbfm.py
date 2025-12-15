from __future__ import annotations
import numpy as np
from core.dsp.wbfm import WBFMStream, fir_lowpass


class NBFMStream(WBFMStream):
    """
    NFM (voz) desde IQ del driver (NO abre hardware).
    """

    def __init__(self, driver):
        super().__init__(driver)

        # NFM: canal más angosto
        self.chan_fir = fir_lowpass(129, 20_000.0, self.fs)
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        # audio voz
        self.aud_fir = fir_lowpass(129, 4_000.0, self.fs1)
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        # De-emphasis típico NFM
        self.tau = 300e-6

        # Un poco más “rápido” para voz (menos latencia)
        self.block_iq = 131_072  # ~54ms @ 2.4Msps
