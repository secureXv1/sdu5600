# core/audio_engine.py
from __future__ import annotations

import threading
from typing import Callable, Optional

from core.dsp.wbfm import WBFMStream
from core.dsp.nbfm import NBFMStream


class AudioEngine:
    """
    AudioEngine NO abre HackRF.
    Consume IQ desde el driver (cola dedicada) para no pelear con el espectro.

    - Permite parámetros DSP en vivo (update_dsp_params)
    - Permite TAP de audio (on_audio) para grabación (scanner)
    - Arranque no bloqueante (start en hilo) para escaneo rápido
    """
    def __init__(self):
        self.stream = None
        self.pending_params: dict = {}
        self._on_audio: Optional[Callable] = None

        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # --- TAP para grabación ---
    def set_on_audio(self, cb: Optional[Callable]):
        """Callback opcional: recibe np.ndarray float32 mono (chunks)"""
        self._on_audio = cb

    def start(self, driver, freq_mhz: float, mode: str, blocking: bool = False):
        """
        Inicia el stream de audio.
        Por defecto NO bloquea (ideal para ScannerEngine).
        Si blocking=True, se comporta como antes (start() bloqueante).
        """
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
        if mode in ("FM", "WFM"):  # WFM = FM comercial
            self.stream = WBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz, on_audio=self._on_audio)
        elif mode == "NFM":
            self.stream = NBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz, on_audio=self._on_audio)
        else:
            raise ValueError(f"Modo no soportado: {mode}")

        # aplica parámetros pendientes (si el UI los envió antes de iniciar)
        if self.pending_params and hasattr(self.stream, "update_params"):
            try:
                self.stream.update_params(**self.pending_params)
            except Exception:
                pass

        if blocking:
            # modo antiguo: bloquea aquí (no recomendado para scan)
            self.stream.start()
            return

        # modo recomendado para scan: no bloquear
        def _run():
            try:
                self.stream.start()
            except Exception:
                # si falla, liberamos referencia
                with self._lock:
                    self.stream = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            st = self.stream

        if st:
            try:
                st.stop()
            except Exception:
                pass

        with self._lock:
            self.stream = None
            self._thread = None

    def update_dsp_params(self, **params):
        """Actualiza parámetros DSP del stream activo; si no hay stream, quedan pendientes."""
        if not params:
            return

        # guardar defaults para el próximo start (ICR30-like)
        self.pending_params.update(params)

        st = self.stream
        if st and hasattr(st, "update_params"):
            try:
                st.update_params(**params)
            except Exception:
                pass
