# core/dsp/nbfm.py
from __future__ import annotations
from core.dsp.wbfm import WBFMStream

class NBFMStream(WBFMStream):
    """
    NFM para VHF/UHF (canal angosto).
    """
    def __init__(self, iq_bytes_queue, freq_mhz: float, on_audio=None):
        super().__init__(iq_bytes_queue=iq_bytes_queue, freq_mhz=freq_mhz, on_audio=on_audio)

        # ===== Defaults NFM (voz) tipo SDR Console =====
        # IF para NFM: 12.5k–16k (si escaneas 25k, puedes subir a ~20k)
        self.chan_taps = 161
        self.chan_cutoff_hz = 16_000.0

        # Audio voz: 3k (más limpio). Si lo quieres más “abierto”, 3.5k–4k
        self.aud_taps = 161
        self.aud_cutoff_hz = 3_000.0

        # De-emphasis NFM típico: 530 µs
        self.tau = 530e-6

        # Menos drive para no distorsionar
        self.drive = 1.0

        self._rebuild_filters()

