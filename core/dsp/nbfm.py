# core/dsp/nbfm.py
from __future__ import annotations

from core.dsp.wbfm import WBFMStream


class NBFMStream(WBFMStream):
    """
    NFM para VHF/UHF (canal angosto).
    Hereda pipeline WBFM pero con parámetros (cutoffs/taps/tau) típicos de NFM.
    """
    def __init__(self, iq_bytes_queue, freq_mhz: float, on_audio=None):
        super().__init__(iq_bytes_queue=iq_bytes_queue, freq_mhz=freq_mhz, on_audio=on_audio)

        # --- Defaults NFM (para escaneo y panel DSP) ---
        # Canal angosto: ~20 kHz (a veces 12.5 kHz real; puedes bajarlo si quieres más selectividad)
        self.chan_taps = 129
        self.chan_cutoff_hz = 20_000.0

        # Voz: 3–4 kHz (4k suena más “abierto”, 3k más limpio)
        self.aud_taps = 129
        self.aud_cutoff_hz = 4_000.0

        # De-emphasis típico NFM
        self.tau = 300e-6

        # Drive un poco más bajo para evitar distorsión en voz
        self.drive = 1.0

        # Reconstruye filtros y estados con los defaults NFM
        self._rebuild_filters()
