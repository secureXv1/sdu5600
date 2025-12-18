# core/audio_engine.py
from __future__ import annotations

import threading
from typing import Callable, Optional

from core.dsp.wbfm import WBFMStream
from core.dsp.nbfm import NBFMStream
from core.dsp.am import AMStream
from core.dsp.ssb import SSBStream


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

        # Sample rate de entrada (importante para AM/SSB si resamplean)
        input_fs = int(getattr(driver, "sample_rate", 2_400_000))

        # ---- Presets "voz limpia" (tipo SDR Console) ----
        # OJO: esto se aplica primero, pero luego pending_params (UI) tiene prioridad.
        preset = {}
        if mode in ("NFM", "FMN"):
            preset = dict(
                chan_cutoff_hz=16_000.0,  # IF NFM
                aud_cutoff_hz=3_000.0,    # AF voz
                tau_us=530.0,             # de-emphasis NFM
                drive=1.0,
                hpf_hz=300.0              # limpia graves
            )
        elif mode in ("AM",):
            preset = dict(
                drive=1.2,
                hpf_hz=150.0,             # limpia graves
                # aud_cutoff_hz lo maneja AMStream internamente como LPF (3k por defecto)
            )
        elif mode in ("USB", "LSB"):
            preset = dict(
                drive=1.2,
                hpf_hz=150.0,             # limpia graves
                # LPF voz 3k por defecto en SSBStream
            )
        else:
            # FM broadcast (si lo usas para voz, no recomiendo; pero lo dejamos neutro)
            preset = dict(
                drive=1.1,
                hpf_hz=0.0
            )

        # ---- Selección de stream ----
        if mode in ("FM", "WFM"):
            self.stream = WBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz, on_audio=self._on_audio)

        elif mode in ("NFM", "FMN"):
            self.stream = NBFMStream(iq_bytes_queue=q, freq_mhz=freq_mhz, on_audio=self._on_audio)

        elif mode == "AM":
            # requiere core/dsp/am.py (AMStream)
            from core.dsp.am import AMStream
            self.stream = AMStream(iq_bytes_queue=q, freq_mhz=freq_mhz, on_audio=self._on_audio, input_fs=input_fs)

        elif mode in ("USB", "LSB"):
            # requiere core/dsp/ssb.py (SSBStream)
            from core.dsp.ssb import SSBStream
            self.stream = SSBStream(iq_bytes_queue=q, freq_mhz=freq_mhz, sideband=mode, on_audio=self._on_audio, input_fs=input_fs)

        else:
            raise ValueError(f"Modo no soportado: {mode}")

        # ---- Aplicación de params: preset primero, UI después (UI manda) ----
        if hasattr(self.stream, "update_params"):
            try:
                if preset:
                    self.stream.update_params(**preset)
            except Exception:
                pass

            # aplica parámetros pendientes (si el UI los envió antes de iniciar)
            if self.pending_params:
                try:
                    self.stream.update_params(**self.pending_params)
                except Exception:
                    pass

        # ---- Arranque ----
        if blocking:
            self.stream.start()
            return

        def _run():
            try:
                self.stream.start()
            except Exception:
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
