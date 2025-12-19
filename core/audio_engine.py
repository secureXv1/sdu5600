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

    ✔ Cambio de modo EN VIVO (sin stop)
    ✔ Volumen real
    ✔ Parámetros DSP dinámicos
    ✔ TAP de audio para grabación / scanner
    """

    def __init__(self):
        self.stream = None
        self.pending_params: dict = {}
        self._on_audio: Optional[Callable] = None

        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Estado
        self.current_mode: Optional[str] = None
        self.iq_queue = None
        self.volume = 0.8

    # -------------------------------------------------
    # TAP para grabación
    # -------------------------------------------------
    def set_on_audio(self, cb: Optional[Callable]):
        """Callback opcional: recibe np.ndarray float32 mono"""
        self._on_audio = cb

    # -------------------------------------------------
    # Interno: crear stream DSP
    # -------------------------------------------------
    def _create_stream(self, mode: str, q, freq_mhz: float, input_fs: int):
        mode = mode.upper()

        if mode in ("FM", "WFM"):
            return WBFMStream(
                iq_bytes_queue=q,
                freq_mhz=freq_mhz,
                on_audio=self._on_audio
            )

        elif mode in ("NFM", "FMN"):
            return NBFMStream(
                iq_bytes_queue=q,
                freq_mhz=freq_mhz,
                on_audio=self._on_audio
            )

        elif mode == "AM":
            return AMStream(
                iq_bytes_queue=q,
                freq_mhz=freq_mhz,
                on_audio=self._on_audio,
                input_fs=input_fs
            )

        elif mode in ("USB", "LSB"):
            return SSBStream(
                iq_bytes_queue=q,
                freq_mhz=freq_mhz,
                sideband=mode,
                on_audio=self._on_audio,
                input_fs=input_fs
            )

        raise ValueError(f"Modo no soportado: {mode}")

    # -------------------------------------------------
    # Start
    # -------------------------------------------------
    def start(self, driver, freq_mhz: float, mode: str, blocking: bool = False):
        self.stop()

        if driver is None:
            raise ValueError("No hay driver activo")

        if hasattr(driver, "connect"):
            driver.connect()

        if hasattr(driver, "set_center_freq"):
            driver.set_center_freq(freq_mhz * 1e6)

        if not hasattr(driver, "get_audio_queue"):
            raise RuntimeError("El driver no expone get_audio_queue()")

        q = driver.get_audio_queue()
        self.iq_queue = q

        mode = (mode or "").upper().strip()
        self.current_mode = mode

        input_fs = int(getattr(driver, "sample_rate", 2_400_000))

        # ---------------------------
        # Presets tipo SDR Console
        # ---------------------------
        preset = {}
        if mode in ("NFM", "FMN"):
            preset = dict(
                chan_cutoff_hz=16_000.0,
                aud_cutoff_hz=3_000.0,
                tau_us=530.0,
                drive=1.0,
                hpf_hz=300.0
            )
        elif mode == "AM":
            preset = dict(
                drive=1.2,
                hpf_hz=150.0
            )
        elif mode in ("USB", "LSB"):
            preset = dict(
                drive=1.2,
                hpf_hz=150.0
            )
        else:  # FM broadcast
            preset = dict(
                drive=1.1,
                hpf_hz=0.0
            )

        self.stream = self._create_stream(mode, q, freq_mhz, input_fs)

        # aplicar preset + params UI
        if hasattr(self.stream, "update_params"):
            try:
                if preset:
                    self.stream.update_params(**preset)
                if self.pending_params:
                    self.stream.update_params(**self.pending_params)
            except Exception:
                pass

        # volumen inicial
        if hasattr(self.stream, "set_volume"):
            self.stream.set_volume(self.volume)

        # arranque
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

    # -------------------------------------------------
    # Stop
    # -------------------------------------------------
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

    # -------------------------------------------------
    # Cambio de modo EN VIVO
    # -------------------------------------------------
    def set_mode(self, mode: str):
        """
        Cambia el demodulador SIN detener el driver.
        Similar a SDR Console / ICR-30.
        """
        mode = (mode or "").upper().strip()
        if not mode or mode == self.current_mode:
            return

        with self._lock:
            old = self.stream

        if not old or self.iq_queue is None:
            self.current_mode = mode
            return

        try:
            old.stop()
        except Exception:
            pass

        input_fs = int(getattr(old, "input_fs", 2_400_000))
        freq_mhz = float(getattr(old, "freq_mhz", 0.0))

        try:
            new_stream = self._create_stream(
                mode=mode,
                q=self.iq_queue,
                freq_mhz=freq_mhz,
                input_fs=input_fs
            )

            if hasattr(new_stream, "update_params") and self.pending_params:
                new_stream.update_params(**self.pending_params)

            if hasattr(new_stream, "set_volume"):
                new_stream.set_volume(self.volume)

            self.stream = new_stream
            self.current_mode = mode

            threading.Thread(target=new_stream.start, daemon=True).start()

        except Exception as e:
            print("Error cambiando modo en vivo:", e)
            self.stream = old

    # -------------------------------------------------
    # Volumen REAL
    # -------------------------------------------------
    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, float(v)))
        st = self.stream
        if st and hasattr(st, "set_volume"):
            try:
                st.set_volume(self.volume)
            except Exception:
                pass

    # -------------------------------------------------
    # DSP params dinámicos
    # -------------------------------------------------
    def update_dsp_params(self, **params):
        if not params:
            return

        self.pending_params.update(params)

        st = self.stream
        if st and hasattr(st, "update_params"):
            try:
                st.update_params(**params)
            except Exception:
                pass
