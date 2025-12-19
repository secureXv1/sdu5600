# core/dsp/nbfm.py
from __future__ import annotations
from core.dsp.wbfm import WBFMStream


class NBFMStream(WBFMStream):
    """
    NFM para VHF/UHF (canal angosto).
    Estilo SDR Console: voz limpia, estable y sin distorsión.
    """

    def __init__(self, iq_bytes_queue, freq_mhz: float, on_audio=None):
        super().__init__(
            iq_bytes_queue=iq_bytes_queue,
            freq_mhz=freq_mhz,
            on_audio=on_audio
        )

        # ==============================
        # Defaults NFM (voz)
        # ==============================
        self.chan_taps = 161
        self.chan_cutoff_hz = 16_000.0     # IF NFM

        self.aud_taps = 161
        self.aud_cutoff_hz = 3_000.0       # AF voz

        self.tau = 530e-6                  # de-emphasis NFM
        self.drive = 1.0

        # 🔊 volumen real (AudioEngine)
        self.volume = 1.0

        # cache para evitar rebuild innecesario
        self._last_params = {}

        self._rebuild_filters()

    # -------------------------------------------------
    # API usada por AudioEngine
    # -------------------------------------------------
    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, float(v)))

    def update_params(self, **params):
        """
        Permite cambiar parámetros DSP EN VIVO.
        Solo reconstruye filtros si hace falta.
        """
        rebuild = False

        for k, v in params.items():
            if not hasattr(self, k):
                continue

            if getattr(self, k) != v:
                setattr(self, k, v)
                rebuild = True

        if rebuild:
            self._rebuild_filters()

    # -------------------------------------------------
    # Hook final de audio (sobrescribe salida)
    # -------------------------------------------------
    def _post_audio(self, audio):
        """
        Última etapa antes de enviar audio al output:
        - aplica volumen
        """
        if audio is None:
            return audio

        return audio * self.volume
